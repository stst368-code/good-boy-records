"""Paired LAN workers pull source audio; the shared ledger owns every result."""
from base64 import b64encode
import hashlib
import hmac
import json
from pathlib import Path
import re
import secrets
import socket
import tempfile
import time
import zipfile
import zlib

MAX_BUNDLE=1024**3
LEASE_SECONDS=120
ONLINE_SECONDS=20


class RemoteProcessing:
    def __init__(self,prep):
        self.prep=prep;self.db=prep.db;self.workers={}

    def pair(self):
        from .service import canonical,now
        self.prep.require()
        token=secrets.token_urlsafe(32)
        with self.prep.lock:
            self.db.execute("INSERT INTO platform_settings VALUES('processing_worker_pair',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",
                (canonical({'sha256':hashlib.sha256(token.encode()).hexdigest()}),now()))
        return token

    def configured(self):
        if not self.prep.enabled:return False
        return bool(self.db.one("SELECT 1 FROM platform_settings WHERE key='processing_worker_pair'"))

    def authorized(self,header):
        if not self.prep.enabled:return False
        row=self.db.one("SELECT value_json FROM platform_settings WHERE key='processing_worker_pair'")
        if not row or not header.startswith('Bearer '):return False
        return hmac.compare_digest(json.loads(row['value_json'])['sha256'],hashlib.sha256(header[7:].encode()).hexdigest())
    def revoke(self):
        with self.prep.lock:
            self.db.execute("DELETE FROM platform_settings WHERE key='processing_worker_pair'")
            self.workers.clear()

    def online(self):
        with self.prep.lock:
            return [{k:v for k,v in worker.items() if k!='seen'} for worker in self.workers.values()
                    if time.time()-worker['seen']<ONLINE_SECONDS]

    def heartbeat(self,worker):
        identifier=worker.get('id','')
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Invalid worker identity')
        label=str(worker.get('name') or 'Preparation PC').strip()[:64]
        available=worker.get('available') is True
        with self.prep.lock:
            self.workers={key:value for key,value in self.workers.items() if time.time()-value['seen']<300}
            if identifier not in self.workers and len(self.workers)>=20:raise ValueError('Too many preparation workers')
            self.workers[identifier]={'id':identifier,'name':label,'available':available,
                'device':str(worker.get('device') or 'auto')[:40],'seen':time.time()}
        return {'accepted':True}

    def claim(self,worker):
        from .service import canonical,digest,now
        self.heartbeat(worker)
        if not worker.get('available'):return {'task':None}
        with self.prep.lock:
            # Remote workers have their own GPU/CPU. A local diffusion or render
            # must not prevent an independent machine from taking waiting work.
            if self.db.one("SELECT 1 FROM processing_tasks WHERE status='running' AND json_extract(telemetry_json,'$.remote.id')=?",(worker['id'],)):
                self.workers[worker['id']]['available']=False
                return {'task':None}
            for task in self.db.all("SELECT * FROM processing_tasks WHERE kind='lyric_alignment' AND status='waiting' ORDER BY rowid"):
                recipe=json.loads(task['recipe_json'])
                if recipe.get('execution_device')=='cpu':continue
                selection=self.db.one("SELECT status,COALESCE(published_at,updated_at) published_at FROM publishing_selection WHERE generation_id=?",(task['generation_id'],))
                if not selection or selection['status'] not in {'selected','published'}:continue
                if selection['status']=='published' and selection['published_at'] and task['created_at']<=selection['published_at']:continue
                try:
                    metadata=self.prep.metadata.read(task['generation_id'])
                    if recipe.get('lyrics_sha256',metadata['original_lyrics_sha256'])!=metadata['lyrics_sha256']:
                        self.db.execute("UPDATE processing_tasks SET status='cancelled',completed_at=?,error='Superseded by changed lyrics' WHERE id=?",(now(),task['id']));continue
                    row,source=self.prep.source(task['generation_id'],artifact_id=recipe.get('input_artifact_id'))
                    if digest(source)!=task['input_sha256']:raise ValueError('Source bytes changed after this alignment was queued')
                except (OSError,ValueError,KeyError) as error:
                    self.db.execute("UPDATE processing_tasks SET status='failed',completed_at=?,error=? WHERE id=?",(now(),str(error),task['id']));continue
                lease=secrets.token_hex(24)
                remote={'id':worker['id'],'name':self.workers[worker['id']]['name'],'lease':lease,'seen':time.time()}
                telemetry=canonical({'remote':remote,'phase':'transferring','message':'Sending audio to '+remote['name']})
                with self.db.transaction() as connection:
                    claimed=connection.execute("UPDATE processing_tasks SET status='running',started_at=?,error=NULL,telemetry_json=? WHERE id=? AND status='waiting'",
                        (now(),telemetry,task['id'])).rowcount
                if claimed:
                    self.workers[worker['id']]['available']=False
                    return {'task':{'id':task['id'],'lease':lease,'recipe':recipe,'source_sha256':task['input_sha256'],
                        'duration':row['duration'],'source_bytes':source.stat().st_size}}
        return {'task':None}

    def task(self,identifier,lease):
        task=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='lyric_alignment' AND status='running'",(identifier,))
        if not task:raise ValueError('This worker lease is no longer active')
        remote=json.loads(task['telemetry_json']).get('remote',{})
        if not lease or not hmac.compare_digest(remote.get('lease',''),lease):raise ValueError('Invalid worker lease')
        return task

    def source(self,identifier,lease):
        from .service import digest
        task=self.task(identifier,lease);recipe=json.loads(task['recipe_json'])
        source=self.prep.source(task['generation_id'],artifact_id=recipe.get('input_artifact_id'))[1]
        if digest(source)!=task['input_sha256']:raise ValueError('Source audio changed after the worker claimed it')
        return source

    def progress(self,identifier,lease,values):
        from .service import canonical
        with self.prep.lock:
            task=self.task(identifier,lease);telemetry=json.loads(task['telemetry_json'])
            remote=telemetry['remote'];remote['seen']=time.time()
            allowed={key:values[key] for key in ['phase','message','device','gpu_name','vram_bytes','wall_seconds','stage_started_at','log_tail','measurements'] if key in values}
            if len(canonical(allowed))>20000:raise ValueError('Progress message is too large')
            telemetry.update(allowed);telemetry['remote']=remote
            self.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical(telemetry),identifier))
            if remote['id'] in self.workers:self.workers[remote['id']].update(seen=time.time(),available=False)
        return {'accepted':True}

    def fail(self,identifier,lease,message):
        from .service import canonical,now
        with self.prep.lock:
            task=self.task(identifier,lease);telemetry=json.loads(task['telemetry_json'])
            busy='Another GBR worker is already using this GPU' in message
            telemetry.update(phase='waiting' if busy else 'failed',retry_after=time.time()+15)
            telemetry.pop('remote',None)
            self.db.execute('UPDATE processing_tasks SET status=?,error=?,completed_at=?,telemetry_json=? WHERE id=?',
                ('waiting' if busy else 'failed',str(message)[:6000],None if busy else now(),canonical(telemetry),identifier))
        return {'accepted':True}

    def recover(self):
        from .service import now
        for task in self.db.all("SELECT id,telemetry_json FROM processing_tasks WHERE status='running' AND json_extract(telemetry_json,'$.remote.id') IS NOT NULL"):
            remote=json.loads(task['telemetry_json'])['remote']
            if time.time()-remote.get('seen',0)>LEASE_SECONDS:
                self.db.execute("UPDATE processing_tasks SET status='interrupted',completed_at=?,error='Preparation worker disconnected. Retry when it is available, or run this alignment locally.' WHERE id=? AND status='running'",
                    (now(),task['id']))

    def complete(self,identifier,lease,bundle):
        """Validate an allowlisted bundle; never accept a remote path or command."""
        from .editorial import validate_lyrics,number
        from .service import canonical,digest,now
        import soundfile as sf
        with self.prep.lock:
            task=self.task(identifier,lease);recipe=json.loads(task['recipe_json'])
            source=self.source(identifier,lease);duration=self.prep.source(task['generation_id'],artifact_id=recipe.get('input_artifact_id'))[0]['duration']
            destination=self.prep.bundle(task['generation_id'])/'_derived';destination.mkdir(parents=True,exist_ok=True)
            with zipfile.ZipFile(bundle) as archive,tempfile.TemporaryDirectory(prefix='.remote-result-',dir=destination) as temp:
                entries=archive.infolist()
                if len(entries)>5 or sum(info.file_size for info in entries)>MAX_BUNDLE:raise ValueError('Worker result exceeds its limit')
                if len({entry.filename for entry in entries})!=len(entries):raise ValueError('Duplicate worker result filename')
                manifest=archive.getinfo('result.json')
                if manifest.file_size>100000:raise ValueError('Worker manifest is too large')
                result=json.loads(archive.read(manifest))
                if not isinstance(result,dict):raise ValueError('Invalid worker manifest')
                outputs=result.get('outputs')
                if not isinstance(outputs,list) or not 1<=len(outputs)<=3 or not all(isinstance(item,dict) for item in outputs):raise ValueError('Invalid worker outputs')
                if any(not isinstance(result.get(key,{}),dict) for key in ['hardware','software','measurements']):raise ValueError('Invalid worker metadata')
                if {entry.filename for entry in entries}!={'result.json',*(item.get('file','') for item in outputs)}:raise ValueError('Unexpected worker result files')
                kinds=[entry.get('kind') for entry in outputs]
                if kinds[0]!='lyric_alignment' or len(set(kinds))!=len(kinds) or set(kinds)-{'lyric_alignment','stem_vocals','stem_accompaniment'}:raise ValueError('Unexpected worker output kind')
                published=[]
                for index,item in enumerate(outputs):
                    filename=item.get('file','')
                    if filename!='output-'+str(index)+('.json' if item['kind']=='lyric_alignment' else '.wav'):raise ValueError('Invalid worker output filename')
                    path=Path(temp)/filename
                    with archive.open(filename) as incoming,path.open('xb') as target:
                        import shutil
                        shutil.copyfileobj(incoming,target,1024*1024)
                    sha=digest(path)
                    if sha!=item.get('sha256'):raise ValueError('Worker result checksum mismatch')
                    if item['kind']=='lyric_alignment':
                        if path.stat().st_size>8*1024**2:raise ValueError('Lyric result is too large')
                        lyrics=json.loads(path.read_text(encoding='utf-8'))
                        if lyrics.get('source_sha256')!=task['input_sha256'] or lyrics.get('language')!=recipe['language']:raise ValueError('Worker lyrics belong to different audio or language')
                        validate_lyrics(lyrics,recipe['words'],duration)
                        for word,original in zip(lyrics['words'],recipe['words']):
                            if any(word.get(key)!=original.get(key) for key in ['id','text','source_text','section','line']):
                                raise ValueError('Worker changed the supplied lyric text')
                            if word.get('confidence') is not None:number(word['confidence'],0,1,'word confidence')
                        if recipe['words'] and not any(word.get('start') is not None for word in lyrics['words']):raise ValueError('Alignment matched no supplied lyric words')
                    else:
                        info=sf.info(path)
                        if abs(info.duration-duration)>.25 or info.channels not in (1,2):raise ValueError('Separated stem does not match source duration')
                    published.append((item['kind'],sha,path,destination/(sha+path.suffix)))
                remote=json.loads(task['telemetry_json'])['remote']
                hardware={key:value for key,value in result.get('hardware',{}).items() if key in {'gpu_name','gpu_uuid','usable_vram_bytes','cpu','ram_total_bytes','compute_capability'}}
                software={key:str(value)[:100] for key,value in result.get('software',{}).items() if key in {'torch','demucs','whisperx','numpy','soundfile'}}
                session='processing-remote-'+hashlib.sha256(canonical([remote['id'],hardware,software]).encode()).hexdigest()
                for kind,sha,path,target in published:
                    if target.exists():
                        if digest(target)!=sha:raise ValueError('Derived artifact collision')
                    else:path.replace(target)
                # A late result cannot take over a retried/cancelled task.
                self.task(identifier,lease)
                telemetry=json.loads(self.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(identifier,))['telemetry_json'])
                telemetry.update(phase='complete',message='Aligned on '+remote['name'],measurements=result.get('measurements',{}))
                telemetry['remote'].pop('lease',None)
                with self.db.transaction() as connection:
                    connection.execute('INSERT OR IGNORE INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (session,'processing-remote://'+remote['id'],'network',None,now(),hardware.get('gpu_uuid'),hardware.get('gpu_name'),hardware.get('usable_vram_bytes'),canonical(hardware),canonical(software)))
                    for index,(kind,sha,path,target) in enumerate(published):
                        metadata={'processing_task_id':identifier,'source_generation_id':task['generation_id']}
                        if kind=='lyric_alignment':metadata['lyrics_backup_zlib']=b64encode(zlib.compress(target.read_bytes())).decode('ascii')
                        connection.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                            (identifier+('-output' if index==0 else '-'+str(index)),kind,sha,str(target),target.stat().st_size,canonical(metadata)))
                    connection.execute("UPDATE processing_tasks SET status='done',completed_at=?,worker_session_id=?,telemetry_json=? WHERE id=?",
                        (now(),session,canonical(telemetry),identifier))
            return {'accepted':True}
