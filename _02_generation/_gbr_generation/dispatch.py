"""Admit additional workers to frozen batches; never restart or steal attempts."""
from contextlib import closing
from copy import deepcopy
import json
import logging
import threading
import uuid

from .assignments import admission,policy
from .connections import execution,connection_key
from .planner import open_database,sha,json_text,write_new
from _gbr_worker.contract import SMOKE_MODEL_REVISION
from _gbr_worker.hardware import gpu_lock

LOG=logging.getLogger('gbr_dispatch')


class Dispatcher:
    def __init__(self,generation):
        self.g=generation;self.stop=threading.Event();self.wake=threading.Event();self.thread=None;self.notices={};self.incompatible={}

    def start(self):
        if self.thread and self.thread.is_alive():return
        self.stop.clear();self.thread=threading.Thread(target=self.work,name='gbr-worker-dispatch',daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.wake.set()
        if self.thread:self.thread.join(timeout=1)

    def work(self):
        while not self.stop.is_set():
            try:self.tick()
            except Exception:LOG.exception('Worker admission check failed')
            self.wake.wait(10);self.wake.clear()

    def automatic(self,manifest):
        override=policy(manifest).get('automatic_workers')
        if override is not None:return bool(override)
        choice=manifest.get('selection',{}).get('worker_id')
        return choice in {'auto','all'} or choice is None and any(r.get('worker_options') for r in manifest['runs'])

    def set_automatic(self,batch_id,enabled):
        from .jobs import Jobs
        path,manifest=Jobs(self.g).batch(batch_id)
        if not self.g.review.db.one("SELECT 1 FROM generation_runs WHERE batch_id=? AND status='prepared'",(batch_id,)):
            raise ValueError('No queued configurations remain in this batch')
        with closing(open_database(manifest['database'])) as db,db:
            db.execute('BEGIN IMMEDIATE')
            saved=json.loads(db.execute('SELECT policy_json FROM generation_batches WHERE id=?',(batch_id,)).fetchone()[0])
            saved['automatic_workers']=enabled
            db.execute('UPDATE generation_batches SET policy_json=? WHERE id=?',(json_text(saved),batch_id))
        self.wake.set()
        return {'automatic_workers':enabled}

    def add(self,batch_id,worker_id,report=None):
        from .jobs import Jobs
        from .executor import make_job
        from client import Client
        path,manifest=Jobs(self.g).batch(batch_id)
        entry=self.g.workers.get(worker_id)
        if not entry['enabled']:raise ValueError('Enable this worker in Worker settings first')
        report=report or self.g.workers.query(entry)
        if not report.get('online') or not report.get('compatible',True):raise ValueError(report.get('error') or 'Worker is offline or has no compatible MiniMax models')
        with self.g.lock,gpu_lock('gbr-admit:'+str(self.g.review.config.database)):
            tasks=[t for t in self.g.tasks(False) if t.get('batch_id')==batch_id]
            if any(t['active'] and not t.get('worker_id') for t in tasks):
                raise ValueError('This batch has a controller from before worker joining was supported. Pause it after the current song, then Continue with the updated app before adding a worker.')
            if any((t.get('worker_id') or connection_key(t['connection']))==worker_id for t in tasks):
                return {'status':'already_assigned','worker_id':worker_id,'batch_id':batch_id}
            frozen=admission(path,manifest,worker_id)
            options={};errors=[]
            if frozen is not None:options=frozen
            else:
                pending={r['id'] for r in self.g.review.db.all("SELECT id FROM generation_runs r WHERE batch_id=? AND status='prepared' AND NOT EXISTS(SELECT 1 FROM generation_attempts a WHERE a.run_id=r.id)",(batch_id,))}
                defaults=self.g.worker_parameters({'connection':entry['connection']},entry,report.get('_inventory'))
                for run in manifest['runs']:
                    if run['id'] not in pending:continue
                    existing=next((o for o in run.get('worker_options',[]) if o['worker_id']==worker_id),None)
                    if existing:options[run['id']]=existing;continue
                    values=deepcopy(run['effective']);provenance=dict(run['provenance'])
                    flexible=run.get('worker_default_fields')
                    if flexible is None:flexible=[k for k,v in provenance.items() if v=='defaults' and k in self.g.workers.default_fields]
                    for field in flexible:
                        if field in defaults:values[field]=defaults[field];provenance[field]='worker_default:admitted'
                    target=execution(entry['connection'],run['execution'].get('encoding_cache','prefer'))
                    revision=None if entry['connection']['backend']=='comfyui' else run.get('model_revision') or SMOKE_MODEL_REVISION
                    option={'effective':values,'provenance':provenance,'execution':target,'model_revision':revision}
                    try:
                        job=make_job({**run,**option},path.parent,'preflight',path.parent)
                        if entry['connection']['backend']=='comfyui':
                            if target['encoding_cache']!='prefer':raise ValueError('This batch requires native encoding-cache controls')
                            Client(entry['connection']['address']).prepare(job,report['_inventory'])
                        options[run['id']]=option
                    except (ValueError,KeyError) as error:
                        if len(errors)<3:errors.append(str(error))
                if not options:raise ValueError('No compatible queued configurations for this worker'+(': '+'; '.join(errors) if errors else ''))
                with closing(open_database(manifest['database'])) as db,db:
                    db.execute('BEGIN IMMEDIATE')
                    saved=json.loads(db.execute('SELECT policy_json FROM generation_batches WHERE id=?',(batch_id,)).fetchone()[0])
                    registry=saved.setdefault('worker_assignments',{})
                    if worker_id in registry:raise ValueError('Worker was assigned concurrently; refresh the queue')
                    pending={r[0] for r in db.execute("SELECT id FROM generation_runs r WHERE batch_id=? AND status='prepared' AND NOT EXISTS(SELECT 1 FROM generation_attempts a WHERE a.run_id=r.id)",(batch_id,))}
                    options={id:option for id,option in options.items() if id in pending}
                    if not options:raise ValueError('No queued configurations remain for this worker')
                    folder=path.parent/'worker_assignments';folder.mkdir(exist_ok=True)
                    file=folder/(uuid.uuid4().hex+'.json')
                    base={r['id']:r for r in manifest['runs']}
                    deltas={id:{'changes':{k:v for k,v in o['effective'].items() if v!=base[id]['effective'].get(k)},
                        'provenance':{k:v for k,v in o['provenance'].items() if v!=base[id]['provenance'].get(k)},
                        'execution':o['execution'],'model_revision':o['model_revision']} for id,o in options.items()}
                    raw=json_text({'batch_id':batch_id,'worker_id':worker_id,'manifest_sha256':saved['manifest_sha256'],'options':deltas}).encode()
                    try:
                        write_new(file,raw)
                        registry[worker_id]={'file':str(file.relative_to(path.parent)).replace('\\','/'),'sha256':sha(raw)}
                        db.execute('UPDATE generation_batches SET policy_json=? WHERE id=?',(json_text(saved),batch_id))
                    except BaseException:file.unlink(missing_ok=True);raise
            if self.stop.is_set():raise ValueError('GBR is closing; no worker was started')
            launched=self.g.launch({'manifest':str(path),'batch_id':batch_id,'connection':entry['connection'],'launcher':entry.get('launcher'),
                'install':entry['connection']['auto_setup'],'worker_id':worker_id})
            self.notices.pop((batch_id,worker_id),None)
            return {**launched,'worker_id':worker_id,'status':'assigned','eligible':len(options),'excluded_errors':errors}

    def tick(self):
        from .jobs import Jobs
        if not self.g.shared_ready():return
        tasks=self.g.tasks(False);active={t.get('batch_id') for t in tasks if t['active'] and t.get('batch_id')}
        if not active:return
        entries=[e for e in self.g.workers.entries() if e['enabled']]
        for batch_id in active:
            if self.stop.is_set():return
            if not self.g.review.db.one("SELECT 1 FROM generation_runs WHERE batch_id=? AND status='prepared'",(batch_id,)):continue
            path,manifest=Jobs(self.g).batch(batch_id)
            if not self.automatic(manifest):continue
            known={t.get('worker_id') or connection_key(t['connection']) for t in tasks if t.get('batch_id')==batch_id}
            for entry in entries:
                if self.stop.is_set():return
                if entry['id'] in known:continue
                signature=None
                try:
                    report=self.g.workers.query(entry)
                    if self.stop.is_set():return
                    if report.get('online') and report.get('compatible',True):
                        nodes=(report.get('_inventory') or {}).get('nodes',{})
                        signature=json.dumps([entry,report.get('models'),{k:nodes.get(k) for k in ('CLIPLoader','UNETLoader','KSampler','MiniMaxMusic3TextEncode','VAELoader','EmptyMiniMaxMusic3LatentAudio')}],sort_keys=True)
                        if self.incompatible.get((batch_id,entry['id']))==signature:continue
                        self.add(batch_id,entry['id'],report)
                except (OSError,ValueError,RuntimeError) as error:
                    self.notices[(batch_id,entry['id'])]=str(error)
                    if signature and str(error).startswith('No compatible queued'):self.incompatible[(batch_id,entry['id'])]=signature
