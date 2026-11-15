"""ComfyUI HTTP transport. Frozen prompt IDs survive controller disconnects."""
import hashlib
import json
from pathlib import Path
import re
import time
import uuid
from urllib.error import HTTPError,URLError
from urllib.parse import urlencode
from urllib.request import Request,urlopen
from workflow import discover,compile_workflow,canonical
from live_progress import PromptProgress, comfy_timestamp

ROOT=Path(__file__).parent
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36',
         'Accept':'application/json, text/plain, */*','Cache-Control':'no-cache','Pragma':'no-cache'}

class ComfyConnectionError(ValueError):pass


def base_url(address):
    # Reuse the preflight's credential/path validation without making a request.
    discover(address,fetch=lambda _: {})
    return address.rstrip('/')


def resolve(options,value,label,preferred=None):
    if value in options:return value
    relevant=[name for name in options if 'minimax' in ''.join(c for c in name.casefold() if c.isalnum())]
    if value in (None,'','auto'):
        if preferred in relevant:return preferred
        if relevant:return relevant[0]
        raise ValueError(label+': no MiniMax checkpoint is available on this worker')
    aliases={'bf16':['bf16','bfloat16'],'fp32':['fp32','float32'],'fp16':['fp16','float16'],'int8':['int8','q8'],'fp8':['fp8']}
    candidates=[name for name in relevant if any(alias in name.casefold() for alias in aliases.get(value,[value]))]
    if label=='VAE' and not candidates:candidates=[name for name in options if 'minimax' in name.casefold() and any(part in name.casefold() for part in ('dav','vae'))]
    if len(candidates)!=1:raise ValueError(f'{label}: choose an exact checkpoint filename; matches={candidates}')
    return candidates[0]

class Client:
    def __init__(self,address):self.address=base_url(address);self.prefix=None
    def _open(self,path,data=None,timeout=45):
        body=None if data is None else canonical(data).encode()
        return urlopen(Request(self.address+path,data=body,headers={**HEADERS,**({'Content-Type':'application/json'} if body is not None else {})}),timeout=timeout)
    def _prefix(self,timeout):
        if self.prefix is not None:return
        last=None
        for prefix in ('','/api'):
            try:
                with self._open(prefix+'/system_stats',timeout=timeout) as response:stats=json.load(response)
                if not isinstance(stats,dict) or 'system' not in stats:raise ValueError('Response is not ComfyUI system statistics')
                self.prefix=prefix;return
            except HTTPError as error:
                if error.code==403:
                    detail=error.read(4096).decode('utf-8',errors='replace')
                    kind='RunPod/Cloudflare rejected the request' if '1010' in detail or 'cloudflare' in detail.casefold() else 'The worker or proxy denied access'
                    raise ComfyConnectionError(f'{kind} (HTTP 403 at {error.url}). Use the full HTTP service URL from RunPod Connect, including -8188 (or the exposed ComfyUI port), and verify it opens in your browser. Authentication/access rules must be resolved on the worker.') from None
                if error.code not in (404,405):raise
                last=error
            except (ValueError,UnicodeDecodeError) as error:last=error
        raise ComfyConnectionError('No ComfyUI API at '+self.address+' or its /api route: '+str(last)) from last
    def request(self,path,data=None,timeout=45):
        self._prefix(timeout)
        with self._open(self.prefix+path,data,timeout) as response:return json.load(response)
    def prompt_queue(self, timeout=45):
        queue = self.request('/queue',timeout=timeout)
        if not isinstance(queue,dict) or any(
            not isinstance(queue.get(key),list) or any(
                not isinstance(row,list) or len(row)<2 or not isinstance(row[1],str) for row in queue[key]
            ) for key in ('queue_running','queue_pending')
        ):
            raise ValueError('ComfyUI did not return a valid prompt queue')
        return queue
    def cancel_prompt(self, prompt_id, *, timeout=15):
        if not re.fullmatch('[A-Za-z0-9_-]+',prompt_id):
            raise ValueError('Invalid ComfyUI prompt ID')
        try:
            self._prefix(timeout)
            with self._open('/api/jobs/'+prompt_id+'/cancel',{},timeout=timeout) as response:
                reply=json.load(response)
            return bool(isinstance(reply,dict) and reply.get('cancelled')), True
        except HTTPError as error:
            if error.code not in (404,405,501):raise
        except (ValueError,UnicodeDecodeError):
            pass
        return False, False
    def delete_pending_prompts(self, prompt_ids, *, timeout=15):
        self._prefix(timeout)
        with self._open(self.prefix+'/queue',{'delete':prompt_ids},timeout=timeout) as response:
            response.read()
    def capabilities(self,timeout=5):
        # Stock ComfyUI may route absent extension URLs to a static handler.
        try:
            result=self.request('/gbr/capabilities',timeout=timeout)
            return result if isinstance(result,dict) else {}
        except HTTPError as error:
            if error.code not in (404,405,501):raise
        except (json.JSONDecodeError,UnicodeDecodeError):pass
        return {}
    def inventory(self,timeout=45):
        return {'system':self.request('/system_stats',timeout=timeout),'nodes':self.request('/object_info',timeout=timeout),
                'capabilities':self.capabilities(timeout=min(timeout,5))}
    def prepare(self,job,inventory=None):
        inventory=inventory or self.inventory();nodes=inventory['nodes'];graph=json.loads((ROOT/'_workflows/music-full.json').read_text(encoding="utf-8"));bindings=json.loads((ROOT/'_workflows/music-bindings.json').read_text(encoding="utf-8"))
        def choices(node,key):
            try:return nodes[node]['input']['required'][key][0]
            except KeyError:raise ValueError('Missing Comfy node/input: '+node+'/'+key) from None
        vae_options=nodes.get('VAELoader',{}).get('input',{}).get('required',{}).get('vae_name',[[]])
        vae_default=(vae_options[1].get('default') if len(vae_options)>1 and isinstance(vae_options[1],dict) else None) or graph['7']['inputs']['vae_name']
        p=job['parameters'];values={k:p[k] for k in ['caption','encoder_seed','duration','encoder_cfg','top_k','sampler_seed','sampler_steps','sampler_cfg','sampler','scheduler']}
        values.update(lyrics=job['lyrics'],text_encoder=resolve(choices('CLIPLoader','clip_name'),p['text_encoder'],'text encoder'),dit=resolve(choices('UNETLoader','unet_name'),p['dit'],'DiT'),
            vae=resolve(choices('VAELoader','vae_name'),None,'VAE',vae_default),output_prefix='gbr/'+job['attempt_id'])
        compiled=compile_workflow(graph,bindings,values,nodes)
        identity={k:values[k] for k in ['text_encoder','dit','vae']};identity.update(identity_kind='workflow-and-checkpoint-names',checkpoint_sha256=None,node_classes=compiled['node_classes'])
        identity['source_workflow_sha256']=compiled['source_workflow_sha256']
        if inventory.get('capabilities',{}).get('checkpoint_sha256'):
            names={k:values[k] for k in ['text_encoder','dit','vae']}
            cache=inventory.setdefault('_checkpoint_hashes',{})
            try:
                key=canonical(names)
                if key not in cache:cache[key]=self.request('/gbr/model-info',names,timeout=300)
                identity['checkpoint_sha256']=cache[key];identity['identity_kind']='workflow-and-checkpoint-sha256'
            except HTTPError as error:
                if error.code not in (404,405,501):raise
        identity['revision']=hashlib.sha1(canonical(identity).encode()).hexdigest()
        if job.get('model_revision') and job['model_revision']!=identity['revision']:raise ValueError('Comfy model inventory changed from the reserved run')
        devices=inventory['system'].get('devices',[]);device=devices[0] if devices else {};system=inventory['system'].get('system',{})
        hardware={'address':self.address,'provider':'comfyui_url','gpu_name':device.get('name'),'usable_vram_bytes':device.get('vram_total'),'free_vram_bytes_at_start':device.get('vram_free'),'gpu_uuid':None,'gpu_peak_vram_bytes':None,'cuda_device':device.get('index'),'memory_profile':'server_managed'}
        return {'compiled':compiled,'identity':identity,'hardware':hardware,'system':system}
    def acknowledge(self,job,directory):
        ref=json.loads((Path(directory)/'comfy.json').read_text(encoding="utf-8"))
        if not self.capabilities().get('verified_output_cleanup'):
            raise RuntimeError('ComfyUI output cleanup is pending: install _99_tools/_workers_comfyui/gbr_bridge.py in ComfyUI/custom_nodes, restart ComfyUI, then retry Clean temporary files. The verified local result is already saved.')
        self.request('/gbr/ack',{'attempt_id':job['attempt_id'],'file':ref['file'],'sha256':ref['download_sha256']})
    def run(self,job,directory,resume=False):
        from datetime import datetime,timezone
        from _gbr_worker.runpod import RemotePending
        from _gbr_worker.control import Cancelled,check_cancel
        cancelled=getattr(self,'cancelled',None);progress=getattr(self,'progress',lambda *a,**k:None)
        directory=Path(directory);journal=directory/'comfy.json'
        def save(data):
            temporary=journal.with_suffix('.tmp');temporary.write_text(canonical(data),encoding='utf-8');temporary.replace(journal)
        def event(**data):
            with (directory/'events.ndjson').open('a',encoding='utf-8') as f:f.write(canonical({'at':datetime.now(timezone.utc).isoformat(),**data})+'\n')
        def execution_stamps(history):
            return {kind:comfy_timestamp(payload.get('timestamp'))
                    for kind,payload in (history or {}).get('status',{}).get('messages',[])
                    if kind in ('execution_start','execution_success') and isinstance(payload,dict)}
        def subscribe(reference):
            self._prefix(45)
            subscription = PromptProgress(self.address, self.prefix, reference['client_id'], reference['prompt'], HEADERS['User-Agent'])
            subscription.open()
            return subscription
        live = None
        try:
            if journal.is_file():
                ref=json.loads(journal.read_text(encoding="utf-8"))
                if ref.get('attempt_id')!=job['attempt_id']:
                    raise RemotePending('The saved ComfyUI prompt belongs to a different attempt')
            else:
                check_cancel(cancelled)
                prepared=self.prepare(job);compiled=prepared['compiled'];identity=prepared['identity'];hardware=prepared['hardware'];system=prepared['system']
                ref={'state':'submitting','attempt_id':job['attempt_id'],'prompt':compiled['prompt'],'workflow_sha256':compiled['workflow_sha256'],'models':identity,'hardware':hardware,'versions':system,'client_id':uuid.uuid4().hex}
                save(ref)
                event(event='worker',hardware=hardware,versions=system);event(event='model_resolved',models=identity)
                live = subscribe(ref)
                submitted=self.request('/prompt',{'prompt':ref['prompt'],'client_id':ref['client_id'],'extra_data':{'gbr_attempt_id':job['attempt_id']}})
                if submitted.get('node_errors') or not submitted.get('prompt_id'):raise ValueError('Comfy rejected workflow: '+canonical(submitted))
                ref.update(state='queued',prompt_id=submitted['prompt_id']);save(ref)
            if not (directory/'events.ndjson').is_file():
                event(event='worker',hardware=ref['hardware'],versions=ref['versions']);event(event='model_resolved',models=ref['models'])
            if not ref.get('prompt_id'):
                # A timed-out POST may have been accepted. Never resubmit an uncertain attempt.
                candidates=[]
                queue=self.request('/queue')
                for row in [*queue.get('queue_running',[]),*queue.get('queue_pending',[])]:
                    if len(row)>3 and row[3].get('gbr_attempt_id')==job['attempt_id']:candidates.append(row[1])
                for identifier,history in self.request('/history?max_items=100').items():
                    prompt=history.get('prompt',[])
                    if len(prompt)>3 and prompt[3].get('gbr_attempt_id')==job['attempt_id']:candidates.append(identifier)
                if len(set(candidates))!=1:raise RemotePending('Submission acknowledgement missing; inspect Comfy queue/history. This attempt was not submitted again.')
                ref['prompt_id']=candidates[0];save(ref)
            if live is None:
                live = subscribe(ref)
            prompt_id = ref['prompt_id']
            recorded_start = False

            def record_start(started_at):
                nonlocal recorded_start
                if started_at and not recorded_start:
                    event(event='stage_start', id=job['attempt_id']+'-comfy', stage='comfy_execution', started_at=started_at)
                    recorded_start = True

            def report():
                record_start(live.started_at)
                if cancel_requested:
                    return
                if running and not live.finished:
                    progress('comfy_generating', prompt_id=prompt_id, heartbeat=True, **live.snapshot())
                else:
                    stage = 'comfy_queued' if queued else 'comfy_waiting'
                    progress(stage, prompt_id=prompt_id, heartbeat=True, execution_started_at=live.started_at)

            deadline=time.monotonic()+7200
            cancel_sent=False;targeted_available=True
            def owns(item):
                return len(item)>3 and isinstance(item[3],dict) and item[3].get('gbr_attempt_id')==job['attempt_id']
            while time.monotonic()<deadline:
                cancel_requested = bool(cancelled and cancelled())
                if cancel_requested and not cancel_sent:
                    queue=self.prompt_queue()
                    visible=[*queue['queue_running'],*queue['queue_pending']]
                    owned=[row for row in visible if row[1]==prompt_id and owns(row)]
                    if not owned:
                        if any(row[1]==prompt_id or owns(row) for row in visible):
                            raise RemotePending('Cannot verify cancellation ownership; reconnect to the saved attempt')
                        cancel_sent=True  # A saved prompt ID can already have left the queue.
                    else:
                        if targeted_available:
                            cancel_sent,targeted_available=self.cancel_prompt(prompt_id)
                        if not targeted_available and any(row[1]==prompt_id and owns(row) for row in queue['queue_pending']):
                            self.delete_pending_prompts([prompt_id])
                            cancel_sent=True
                        elif not targeted_available:
                            progress('cancel_after_generation',message='This server cannot safely interrupt one prompt; its output will be discarded when it finishes.')
                    if cancel_sent:progress('cancelling')
                history=self.request('/history/'+ref['prompt_id']).get(ref['prompt_id'])
                if cancel_sent:
                    queue=self.prompt_queue()
                    if not any(row[1]==prompt_id for row in [*queue['queue_running'],*queue['queue_pending']]):
                        # Completion can win the cancellation race. Fetch the
                        # owned FLAC so normal verified discard/ack removes it.
                        outputs=(history or {}).get('outputs',{}) if owns((history or {}).get('prompt',[])) else {}
                        files=[f for output in outputs.values() for f in output.get('audio',[]) if f.get('filename','').endswith('.flac')]
                        if len(files)==1:ref['file']=files[0];break
                        record_start(execution_stamps(history).get('execution_start'))
                        raise Cancelled('ComfyUI confirmed this prompt is no longer running or queued')
                if history:
                    if history.get('status',{}).get('status_str')=='error':
                        record_start(execution_stamps(history).get('execution_start'))
                        check_cancel(cancelled)
                        raise RuntimeError('Comfy execution failed: '+canonical(history.get('status')))
                    if history.get('status',{}).get('completed'):
                        files=[f for output in history.get('outputs',{}).values() for f in output.get('audio',[]) if f.get('filename','').endswith('.flac')]
                        if len(files)!=1:raise ValueError('Expected one generated FLAC in Comfy history')
                        ref['file']=files[0];break
                queue=self.request('/queue')
                running=any(row[1]==prompt_id for row in queue.get('queue_running',[]))
                queued=any(row[1]==prompt_id for row in queue.get('queue_pending',[]))
                report()
                live.wait(prompt_id, 1, report)
            else:raise RemotePending('Comfy prompt is still pending; reconnect to the saved prompt ID')
            live.close()
            messages=(history or {}).get('status',{}).get('messages',[])
            stamps=execution_stamps(history)
            started_at = stamps.get('execution_start') or live.started_at
            finished_at = stamps.get('execution_success')
            if started_at and finished_at:
                seconds = (datetime.fromisoformat(finished_at) - datetime.fromisoformat(started_at)).total_seconds()
                if seconds >= 0:
                    event(event='stage_end',id=job['attempt_id']+'-comfy',stage='comfy_execution',started_at=started_at,
                          completed_at=finished_at,duration_seconds=seconds,resource={'device_id':ref['hardware']['gpu_uuid'] or self.address,
                          'sampling_interval_ms':None,'coverage':'unavailable','offload':{},
                          'details':{'timing_source':'comfy_server_timestamps','queue_time_excluded':True}})
            else:
                record_start(started_at)
            file=ref['file']
            progress('downloading', execution_started_at=started_at)
            if file.get('type','output')!='output' or Path(file['filename']).name!=file['filename'] or '..' in Path(file.get('subfolder','')).parts or '\\' in file.get('subfolder','') or not file.get('subfolder','').startswith('gbr') or not file['filename'].startswith(job['attempt_id']):raise ValueError('Comfy returned a file outside this attempt namespace')
            output=directory/'audio.flac';temporary=directory/'download.part'
            self._prefix(45)
            with self._open(self.prefix+'/view?'+urlencode(file),timeout=120) as response,temporary.open('wb') as stream:
                total=0
                while chunk:=response.read(1024*1024):
                    total+=len(chunk)
                    if total>2*1024**3:raise ValueError('Comfy audio exceeds transfer limit')
                    stream.write(chunk)
            import soundfile as sf
            info=sf.info(temporary)
            if info.format!='FLAC' or not info.frames:raise ValueError('Comfy output is not valid FLAC')
            temporary.replace(output);ref['download_sha256']=hashlib.sha256(output.read_bytes()).hexdigest();ref['state']='downloaded';save(ref)
            cached=[node for kind,payload in messages if kind=='execution_cached' and isinstance(payload,dict) for node in payload.get('nodes',[])]
            result={'protocol':1,'run_id':job['run_id'],'attempt_id':job['attempt_id'],'status':'succeeded','completed_at':datetime.now(timezone.utc).isoformat(),
                'actual_parameters':job['parameters'],'models':ref['models'],'hardware':ref['hardware'],'workflow_sha256':ref['workflow_sha256'],
                'remote':{'prompt_id':ref['prompt_id'],'file':ref['file'],'download_sha256':ref['download_sha256']},'encoding':{'mode':'comfy_graph','cache_hit':('2' in cached) if any(kind=='execution_cached' for kind,_ in messages) else None,'key':None},'telemetry_limitations':['Per-prompt peak VRAM is unavailable from stock ComfyUI; checkpoint hashes require the GBR bridge.']}
            (directory/'result.json').write_text(canonical(result),encoding='utf-8')
            return result
        except HTTPError as error:
            if error.code in (400,422):raise ValueError('Comfy rejected request: '+error.read().decode('utf-8',errors='replace')[-4000:]) from error
            raise RemotePending('Comfy HTTP connection failed; reconnect to the saved attempt: '+str(error)) from error
        except ComfyConnectionError as error:raise RemotePending('Comfy connection failed; the saved attempt was not resubmitted: '+str(error)) from error
        except (OSError,URLError,TimeoutError) as error:raise RemotePending('Comfy connection lost; reconnect to this attempt without resubmitting: '+str(error)) from error
        finally:
            if live is not None:
                live.close()
            (directory/'download.part').unlink(missing_ok=True)
