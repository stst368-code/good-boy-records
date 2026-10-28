"""Read-only account discovery feeding the existing Workers registry and dispatcher."""
from concurrent.futures import ThreadPoolExecutor,as_completed
from copy import deepcopy
import ipaddress
import json
import logging
import re
import socket
import threading
import time
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode,urlsplit
from urllib.request import Request,build_opener,HTTPRedirectHandler

from .connections import normalize,connection_key,key_candidates
from .credentials import AccountCredential

LOG=logging.getLogger('gbr_runpod')
API='https://api.runpod.io/v2'
DEFAULTS={'enabled':True,'interval':30,'candidate_ports':[8188],'identity_file':'','native':True}


class AccountError(ValueError):
    def __init__(self,message,retry_after=0):super().__init__(message);self.retry_after=retry_after


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


def safe_error(error,key=None):
    if isinstance(error,HTTPError):return f'HTTP {error.code}'
    if isinstance(error,(TimeoutError,socket.timeout)):return 'Connection timed out'
    if isinstance(error,URLError):return 'Connection failed: '+type(error.reason).__name__
    text=str(error)
    if key:text=text.replace(key,'[redacted]')
    text=re.sub(r'(?i)(bearer\s+|api_key[=:]\s*)[^\s&"\']+',r'\1[redacted]',text)
    return text[:1500]


class AccountAPI:
    def __init__(self,key):self.key=key;self.retry_at=0

    def request(self,path):
        if not self.key:raise AccountError('RunPod API key is missing. Configure it in Worker settings.')
        if time.monotonic()<self.retry_at:raise AccountError('RunPod account API rate limit; waiting before retry',self.retry_at-time.monotonic())
        request=Request(API+path,headers={'Authorization':'Bearer '+self.key,'Accept':'application/json'})
        try:
            with build_opener(NoRedirect()).open(request,timeout=6) as response:
                raw=response.read(8*1024*1024+1)
                if len(raw)>8*1024*1024:raise AccountError('RunPod account response exceeds 8 MiB')
                return json.loads(raw)
        except HTTPError as error:
            try:delay=min(300,max(30,float(error.headers.get('Retry-After','30'))))
            except (TypeError,ValueError):delay=30
            reason={401:'Invalid or expired API key',403:'API key lacks permission to read these pods',429:'Account API rate limit; waiting before retry'}.get(error.code,'Account API request failed')
            if error.code==429:self.retry_at=time.monotonic()+delay
            raise AccountError(f'{reason} (HTTP {error.code})',delay if error.code==429 else 0) from None
        except (OSError,ValueError) as error:
            if isinstance(error,AccountError):raise
            raise AccountError('RunPod account API: '+safe_error(error,self.key)) from None

    def pods(self,stop=None):
        pods=[];cursor=None;seen=set()
        for _ in range(100):
            if stop and stop.is_set():raise AccountError('Discovery stopped')
            query={'includeClusterPods':'true','limit':100}
            if cursor:query['cursor']=cursor
            data=self.request('/pods?'+urlencode(query))
            if not isinstance(data,dict) or not isinstance(data.get('pods'),list):raise AccountError('Unexpected RunPod pods response; previous workers retained')
            pods.extend(data['pods']);page=data.get('pagination') or {}
            if not page.get('hasNextPage'):return pods
            cursor=page.get('nextCursor')
            if not cursor or cursor in seen:raise AccountError('RunPod returned an invalid pagination cursor; previous workers retained')
            seen.add(cursor)
        raise AccountError('RunPod account exceeds the 100-page discovery limit; previous workers retained')


def pod_summary(pod):
    # Deliberate allowlist: env, registry auth, command strings and API responses
    # can contain secrets and must never enter config, logs or browser responses.
    runtime=pod.get('runtime') or {};gpu=pod.get('gpu') or {};cpu=pod.get('cpu') or {}
    ssh={kind:{key:value for key,value in (endpoint or {}).items() if key in {'host','port','username'}}
         for kind,endpoint in (pod.get('ssh') or {}).items() if kind in {'direct','proxy'}}
    return {'pod_id':pod['id'],'name':str(pod.get('name') or pod['id'])[:300],'ssh':ssh,
        'advertised_ports':pod.get('ports'),'public_ip':pod.get('publicIp'),'port_mappings':runtime.get('ports') or pod.get('portMappings'),
        'desired_status':pod.get('desiredStatus'), 'state':str(pod.get('status') or pod.get('desiredStatus') or 'UNKNOWN').upper(),
        'runtime_status':pod.get('runtimeStatus'),'gpu_name':gpu.get('id') or pod.get('gpuTypeId'),
        'gpu_count':gpu.get('count',pod.get('gpuCount')),'vcpu_count':gpu.get('vcpuCount',cpu.get('count',pod.get('vcpuCount'))),
        'ram_gb':gpu.get('memory',cpu.get('memory',pod.get('memoryInGb'))),'hourly_cost':pod.get('cost',pod.get('costPerHr')),
        'uptime_seconds':runtime.get('uptime',runtime.get('uptimeInSeconds')),
        'gpu_utilisation':runtime.get('gpus'),'cpu_utilisation':runtime.get('cpu',runtime.get('container')),
        'ram_utilisation':runtime.get('memory'),'last_discovery':time.time()}


def endpoints(pod,settings,entries):
    pod_id=pod['id'];runtime=pod.get('runtime') or {};ip=pod.get('publicIp')
    http_ports=set();tcp={};addresses=[]
    for port in pod.get('ports') or []:
        if isinstance(port,str) and re.fullmatch(r'\d+/(http|tcp)',port):
            number,kind=port.split('/')
            if kind=='http':http_ports.add(int(number))
    for row in runtime.get('ports') or []:
        private=row.get('private',row.get('privatePort'));public=row.get('public',row.get('publicPort'))
        if not isinstance(private,int):continue
        if row.get('type')=='http':http_ports.add(private)
        host=row.get('ip') or ip
        try:
            if host and public and ipaddress.ip_address(host).is_global and row.get('isIpPublic',True):tcp[private]=(host,int(public))
        except ValueError:pass
    for private,public in (pod.get('portMappings') or {}).items():
        try:
            if ip and ipaddress.ip_address(ip).is_global:tcp[int(str(private).split('/')[0])]=(ip,int(public))
        except (TypeError,ValueError):pass
    ports=set(settings['candidate_ports'])|http_ports
    for entry in entries:
        c=entry.get('connection') or {};host=urlsplit(c.get('address','')).hostname or ''
        match=re.fullmatch(re.escape(pod_id)+r'-(\d+)\.proxy\.runpod\.net',host)
        if match:ports.add(int(match[1]));addresses.append(c)
    for port in sorted(ports):
        if 1<=port<=65535:addresses.append({'backend':'comfyui','address':f'https://{pod_id}-{port}.proxy.runpod.net'})
    for private,(host,public) in sorted(tcp.items()):
        if private!=22:addresses.append({'backend':'comfyui','address':f'http://{host}:{public}'})
    direct=(pod.get('ssh') or {}).get('direct')
    if isinstance(direct,dict):
        host,port,user=direct.get('host'),direct.get('port'),direct.get('username','root')
    elif 22 in tcp:host,port=tcp[22];user='root'
    else:host=port=user=None
    if settings['native'] and host and port:
        addresses.append({'backend':'runpod_dedicated','address':f'ssh://{user}@{host}:{port}',
            'identity_file':settings['identity_file'] or next(iter(key_candidates()),''),'auto_setup':False})
    result=[];seen=set()
    for value in addresses:
        try:connection=normalize(value)
        except (ValueError,TypeError):continue
        key=connection_key(connection)
        if key not in seen:result.append(connection);seen.add(key)
    return result[:16]


class RunpodDiscovery:
    def __init__(self,generation):
        self.g=generation;self.credential=AccountCredential(generation)
        self.stop=threading.Event();self.wake=threading.Event();self.lock=threading.RLock();self.cycle_lock=threading.Lock()
        self.thread=None;self.manual=False;self.target=None;self.failures=0;self.retry_delay=0
        self.progress={'running':False,'operation':'Idle','started_at':None,'completed_at':None,'next_at':None,'pods':0,'running_pods':0,'connected':0,'unavailable':0,'current_pods':[],'errors':[],'endpoints':[]}

    def settings(self):return {**DEFAULTS,**deepcopy(self.g.review.config.values.get('generation',{}).get('runpod_account',{}))}

    def save(self,values):
        key=values.pop('api_key','');settings={**self.settings(),**values}
        if key:self.credential.save(key)
        if not 10<=settings['interval']<=3600:raise ValueError('Discovery interval must be 10–3600 seconds')
        if len(settings['candidate_ports'])>8 or any(type(p) is not int or not 1<=p<=65535 for p in settings['candidate_ports']):raise ValueError('Choose up to eight valid candidate HTTP ports')
        with self.g.review.config_lock:
            saved=deepcopy(self.g.review.config.values.get('generation',{}));saved['runpod_account']=settings
            self.g.review.config.values['generation']=saved;self.g.review.config.save()
        self.wake.set();return self.status()

    def status(self):
        with self.lock:progress=deepcopy(self.progress)
        rows=[{**entry,'status':{k:v for k,v in self.g.workers.reports.get(entry['id'],{}).items() if not k.startswith('_')}} for entry in self.g.workers.entries() if entry.get('runpod')]
        return {'settings':self.settings(),'has_api_key':bool(self.credential.get()),'progress':progress,'workers':rows}

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear();self.thread=threading.Thread(target=self.work,name='gbr-runpod-discovery',daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=30)

    def refresh(self,worker_id=None):
        with self.lock:
            if self.progress['running'] or self.manual:return self.status()
            self.manual=True;self.target=worker_id.split('|',1)[1] if worker_id else None
            self.progress['operation']='Refresh queued';self.progress['next_at']=time.time()
        self.start();self.wake.set();return self.status()

    def work(self):
        while not self.stop.is_set():
            settings=self.settings()
            if self.manual or settings['enabled'] and self.credential.get() and (self.progress['next_at'] is None or time.time()>=self.progress['next_at']):
                with self.lock:self.manual=False;target=self.target;self.target=None
                self.cycle(target)
            self.wake.wait(1);self.wake.clear()

    def note(self,**values):
        with self.lock:self.progress.update(values)

    def cycle(self,target=None):
        if not self.cycle_lock.acquire(blocking=False):return False
        key=self.credential.get();settings=self.settings();self.retry_delay=0
        self.note(running=True,operation='Reading RunPod account',started_at=time.time(),completed_at=None,errors=[],endpoints=[],current_pods=[],pods=0,running_pods=0,connected=0,unavailable=0)
        try:
            api=AccountAPI(key);pods=api.pods(self.stop);seen=set()
            for pod in pods:
                if not isinstance(pod,dict) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',str(pod.get('id',''))):raise AccountError('Account returned an invalid pod identity; previous workers retained')
                if pod['id'] in seen:continue
                seen.add(pod['id'])
            pods=list({p['id']:p for p in pods}.values())
            self.note(account_ok=True,pods=len(pods),running_pods=sum(str(p.get('status') or p.get('desiredStatus')).upper() in {'RUNNING','STARTING','PROVISIONING','RESTARTING'} for p in pods),operation='Inspecting generation services')
            with ThreadPoolExecutor(max_workers=4,thread_name_prefix='gbr-runpod-probe') as pool:
                jobs=[pool.submit(self.inspect_pod,api,pod,settings,key) for pod in pods if not target or pod['id']==target]
                for job in as_completed(jobs):
                    if self.stop.is_set():break
                    try:job.result()
                    except Exception as error:
                        with self.lock:self.progress['errors'].append(safe_error(error,key))
            if self.stop.is_set():return False
            for entry in self.g.workers.entries():
                if entry.get('runpod') and (not target or entry['runpod']['pod_id']==target) and entry['runpod']['pod_id'] not in seen:
                    meta={**entry['runpod'],'state':'NOT_RETURNED','eligible':False,'last_error':'Pod is no longer returned by this account','last_discovery':time.time()}
                    self.g.workers.discovered(meta,entry.get('connection'),None)
            self.failures=0;self.note(operation='Discovery complete');LOG.info('RunPod discovery complete: %s pods',len(pods))
        except Exception as error:
            self.failures+=1;message=safe_error(error,key)
            self.note(account_ok=False,operation='Account discovery failed; previous workers retained',errors=[message]);LOG.warning('RunPod discovery: %s',message)
            settings['interval']=max(settings['interval'],getattr(error,'retry_after',0),min(300,30*2**min(self.failures-1,4)))
        finally:
            self.note(running=False,completed_at=time.time(),next_at=time.time()+max(settings['interval'],self.retry_delay),current_pods=[])
            self.cycle_lock.release()
        return True

    def inspect_pod(self,api,pod,settings,key):
        if self.stop.is_set():return
        pod_id=pod['id'];meta=pod_summary(pod);entries=self.g.workers.entries()
        previous=next((e for e in entries if (e.get('runpod') or {}).get('pod_id')==pod_id),None)
        with self.lock:self.progress['current_pods'].append(pod_id)
        report=None;incompatible_report=None;connection=previous.get('connection') if previous else None;attempts=[]
        try:
            if meta['state'] in {'RUNNING','STARTING','PROVISIONING','RESTARTING'}:
                try:
                    detail=api.request('/pods/'+pod_id)
                    if isinstance(detail,dict) and detail.get('id')==pod_id:pod={**pod,**detail};meta=pod_summary(pod)
                    else:raise AccountError('Pod detail response has a different identity')
                except AccountError as error:
                    # A detail-read failure is not proof that a previously healthy
                    # worker stopped. Preserve it and retry on the next cycle.
                    with self.lock:
                        self.progress['errors']=(self.progress['errors']+[pod_id+': '+safe_error(error,key)])[-100:]
                        self.retry_delay=max(self.retry_delay,error.retry_after)
                    if previous is None:
                        self.g.workers.discovered({**meta,'eligible':False,'last_error':'Cannot inspect pod: '+safe_error(error,key)},None,None)
                    return
                if meta['state'] not in {'RUNNING','STARTING','PROVISIONING','RESTARTING'}:
                    self.g.workers.discovered({**meta,'eligible':False,'last_error':'Pod became '+meta['state'].lower()+' during discovery'},connection,None)
                    with self.lock:self.progress['unavailable']+=1
                    return
                candidates=endpoints(pod,settings,entries);meta['endpoints']=candidates
                if previous and previous.get('connection'):
                    preferred=previous['connection']['backend']
                    candidates.sort(key=lambda c:(c['backend']!=preferred,connection_key(c)!=connection_key(previous['connection'])))
                deadline=time.monotonic()+40
                for candidate in candidates:
                    if self.stop.is_set() or time.monotonic()>deadline:break
                    trial=self.g.workers.query({'id':'runpod|'+pod_id,'connection':candidate,'launcher':{},'defaults':{},'enabled':True,'_probe_timeout':2,'runpod':{'pod_id':pod_id,'eligible':True}},cache=False)
                    # Discovery never bootstraps or mutates a pod.
                    ok=bool(trial.get('online') and trial.get('compatible',False))
                    attempts.append({'endpoint':candidate['address'],'type':candidate['backend'],'online':trial.get('online',False),'compatible':ok,'error':safe_error(trial.get('error') or ('Service does not support MiniMax Music' if not ok else ''),key),'at':time.time()})
                    with self.lock:self.progress['endpoints']=(self.progress['endpoints']+[{'pod_id':pod_id,**attempts[-1]}])[-100:]
                    if ok:connection=candidate;report=trial;break
                    if trial.get('online') and incompatible_report is None:
                        incompatible_report=trial;connection=candidate
                meta.update(eligible=bool(report),last_error='' if report else (attempts[-1]['error'] if attempts else 'No advertised generation endpoint; expose the service HTTP port or native SSH port'))
                if report:meta.update(last_health=report['checked_at'],worker_type=connection['backend'])
                else:
                    meta['worker_type']=connection['backend'] if incompatible_report else 'unknown'
                    meta['service_state']='incompatible' if incompatible_report else 'unreachable'
                    if incompatible_report:meta['last_health']=incompatible_report['checked_at']
            else:meta.update(eligible=False,last_error='Pod is '+meta['state'].lower())
            meta['recent_checks']=attempts[-16:]
            if not self.stop.is_set():self.g.workers.discovered(meta,connection,report or incompatible_report)
            with self.lock:
                self.progress['connected' if report else 'unavailable']+=1
        finally:
            with self.lock:
                if pod_id in self.progress['current_pods']:self.progress['current_pods'].remove(pod_id)
