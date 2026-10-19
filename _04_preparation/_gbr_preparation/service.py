"""Stage 4 orchestration on the existing database, move journal and artifact registry."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from .artwork import brief

DEFAULT_MIX = {'gain_db':0., 'bass_db':0., 'mid_db':0., 'treble_db':0.,
               'reverb':0., 'room_seconds':1.2, 'fade_in':0., 'fade_out':0., 'limiter_db':None}


def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':'))


def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as stream:
        for data in iter(lambda:stream.read(1024**2),b''):h.update(data)
    return h.hexdigest()


def now():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def mix_recipe(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT_MIX):
        raise ValueError('Unknown mixing control')
    result={**DEFAULT_MIX,**value}
    limits={'gain_db':(-24,12),'bass_db':(-12,12),'mid_db':(-12,12),'treble_db':(-12,12),
            'reverb':(0,1),'room_seconds':(.1,4),'fade_in':(0,30),'fade_out':(0,60),'limiter_db':(-12,0)}
    for key,(low,high) in limits.items():
        v=result[key]
        if key=='limiter_db' and v is None:continue
        if isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v) or not low<=v<=high:
            raise ValueError('Invalid '+key)
    return {'version':1,**result}


class Preparation:
    def __init__(self,root,review):
        self.root,self.review,self.db=Path(root),review,review.db
        self.tracks=self.root/'_04_preparation/_tracks'
        self.references=self.root/'_04_preparation/_references/_artwork'
        self.worker=self.root/'_99_tools/_workers_processing/worker.py'
        self.lock=threading.RLock()
        self.submitted=set()
        self.owner_nonce=uuid.uuid4().hex
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='gbr-preparation')
        self.enabled=self.db.one("SELECT COUNT(*) n FROM sqlite_master WHERE type='table' AND name IN ('generation_artifacts','worker_sessions','platform_migration_report')")['n']==3
        if self.enabled:
            # Additive and idempotent: no copied metadata, history reset or second DB.
            with self.db.transaction() as c:
                sql=(self.root/'_98_database/_gbr_database/processing.sql').read_text()
                for statement in sql.split(';'):
                    if statement.strip():c.execute(statement)
            self.review.archiver.preparation_root=self.tracks
        self.review.preparation=self

    def close(self):
        self.pool.shutdown(wait=False,cancel_futures=True)
        for identifier in self.submitted:
            self.db.execute("UPDATE processing_tasks SET status='interrupted',error='Application closed before execution' WHERE id=? AND status='queued'",(identifier,))

    def require(self):
        if not self.enabled:raise ValueError('Stage 4 requires the existing shared platform database')

    def owner(self):
        import psutil
        try:created=psutil.Process().create_time()
        except (psutil.Error,OSError):created=None
        return {'pid':os.getpid(),'created_at':created,'host':socket.gethostname(),'nonce':self.owner_nonce}

    def queue_promotion(self,c,gid):
        if not self.enabled:return
        row=c.execute('''SELECT g.audio_id,a.archive_path,r.id AS review_id,r.bucket
            FROM generations g JOIN audio a ON a.id=g.audio_id JOIN latest_reviews r ON r.audio_id=g.audio_id
            JOIN publishing_selection p ON p.generation_id=g.id WHERE g.id=? AND p.status IN ('selected','published')''',(gid,)).fetchone()
        if not row:return
        if row['archive_path'] and Path(row['archive_path']).resolve().is_relative_to(self.tracks.resolve()):return
        if c.execute("SELECT 1 FROM archive_operations WHERE audio_id=? AND operation_type='prepare' AND status IN ('pending','running','failed') AND review_id=?",(row['audio_id'],row['review_id'])).fetchone():return
        c.execute('''INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,operation_type,created_at,updated_at)
            VALUES(?,?,?,?,?,'prepare',?,?)''',(uuid.uuid4().hex,row['audio_id'],gid,row['review_id'],row['bucket'],now(),now()))

    def sync_selection(self):
        if not self.enabled:return
        with self.db.transaction() as c:
            for row in c.execute("SELECT generation_id FROM publishing_selection WHERE status IN ('selected','published')").fetchall():
                self.queue_promotion(c,row[0])

    def projects(self):
        if not self.enabled:return {'enabled':False,'projects':[]}
        rows=self.db.all('''SELECT g.id AS generation_id,g.audio_id,s.title,m.parsed_json,a.duration,a.archive_path,p.status
            FROM publishing_selection p JOIN generations g ON g.id=p.generation_id JOIN songs s ON s.id=g.song_id
            JOIN generation_metadata m ON m.generation_id=g.id JOIN audio a ON a.id=g.audio_id
            WHERE (p.status IN ('selected','published') AND EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id))
               OR a.archive_path LIKE ? ORDER BY p.selected_at,g.id''',(str(self.tracks)+'%',))
        for row in rows:
            meta=json.loads(row.pop('parsed_json'))
            row.update(cluster=meta.get('cluster'),style=meta.get('style') or meta.get('style_primary'),
                       song_id=meta.get('song_id'),variant_id=meta.get('variant_id'))
            row['ready']=bool(row['archive_path'] and Path(row['archive_path']).is_file() and Path(row['archive_path']).resolve().is_relative_to(self.tracks.resolve()))
            row['tasks']=self.tasks(row['generation_id'])
            row['promotion']=self.db.one("SELECT id,status,error FROM archive_operations WHERE audio_id=? AND operation_type='prepare' ORDER BY rowid DESC LIMIT 1",(row['audio_id'],))
        return {'enabled':True,'projects':rows,'ffmpeg':bool(shutil.which('ffmpeg')),
                'reference_root':str(self.references),'worker_root':str(self.worker.parent)}

    def source(self,gid,ready=True):
        row=self.db.one('''SELECT g.audio_id,a.archive_path,a.duration,m.parsed_json,m.yaml_sha256
                          FROM generations g JOIN audio a ON a.id=g.audio_id
                          JOIN generation_metadata m ON m.generation_id=g.id WHERE g.id=?''',(gid,))
        if not row:raise KeyError('Recording not found')
        source=self.review.archiver.available(row['audio_id'])
        if not source:raise FileNotFoundError('Recording unavailable')
        if ready and not source.resolve().is_relative_to(self.tracks.resolve()):
            raise ValueError('Wait for verified promotion into Stage 4 before processing')
        return row,source

    def tasks(self,gid):
        rows=self.db.all('SELECT * FROM processing_tasks WHERE generation_id=? ORDER BY created_at DESC,id DESC LIMIT 100',(gid,))
        for row in rows:
            row['recipe']=json.loads(row.pop('recipe_json'));row['telemetry']=json.loads(row.pop('telemetry_json'))
            row['artifacts']=self.db.all("SELECT id,kind,size_bytes,metadata_json,uri FROM generation_artifacts WHERE json_extract(metadata_json,'$.processing_task_id')=?",(row['id'],))
            for artifact in row['artifacts']:
                artifact['available']=Path(artifact.pop('uri')).is_file()
                artifact.pop('metadata_json')
        return rows

    def artifact(self,identifier):
        row=self.db.one('SELECT * FROM generation_artifacts WHERE id=?',(identifier,))
        if not row:raise KeyError('Artifact not found')
        path=Path(row['uri']).resolve()
        if not path.is_relative_to(self.tracks.resolve()) or not path.is_file():raise FileNotFoundError('Preparation artifact unavailable')
        return path

    def reference_list(self):
        self.require()
        for group in ['_global','_songs','_clusters']:(self.references/group).mkdir(parents=True,exist_ok=True)
        found={}
        for path in sorted(self.references.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:continue
            if not path.resolve().is_relative_to(self.references.resolve()):raise ValueError('Reference symlinks must stay inside the reference folder')
            sha=digest(path);identifier='ref-'+sha
            found.setdefault(identifier,{'id':identifier,'sha256':sha,'name':path.relative_to(self.references).as_posix()})
            with self.db.transaction() as c:
                c.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                    (identifier,'artwork_reference',sha,str(path),path.stat().st_size,canonical({'source_relative':str(path.relative_to(self.references))})))
            if len(found)>1000:raise ValueError('Reference collection exceeds 1000 images; narrow the collection')
        return list(found.values())

    def artwork(self,gid,data):
        self.require();row,_=self.source(gid)
        references={r['id'] for r in self.reference_list()}
        if set(data.get('references',[]))-references:raise ValueError('A selected reference is missing or changed')
        recipe=brief(json.loads(row['parsed_json']),data.get('family','qwen-2.1'),data.get('direction',''),data.get('references',[]),data.get('seed',0))
        recipe['source_yaml_sha256']=row['yaml_sha256']
        identifier=self._insert(gid,'artwork_brief',recipe,row['yaml_sha256'] or row['audio_id'],'done')
        return {'id':identifier,**recipe}

    def _insert(self,gid,kind,recipe,source_hash,status='queued'):
        identifier=uuid.uuid4().hex
        fingerprint=hashlib.sha256(canonical([gid,kind,recipe,source_hash]).encode()).hexdigest()
        if kind=='artwork_brief':
            previous=self.db.one("SELECT id FROM processing_tasks WHERE fingerprint=? AND status='done'",(fingerprint,))
            if previous:return previous['id']
        try:
            self.db.execute('''INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at)
                VALUES(?,?,?,?,?,?,?,?,?)''',(identifier,gid,kind,status,fingerprint,canonical(recipe),source_hash,now(),now() if status=='done' else None))
        except sqlite3.IntegrityError:
            raise ValueError('A matching task is already queued; refresh processing history') from None
        if status=='queued':
            import psutil
            self.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical({'owner':self.owner()}),identifier))
        return identifier

    def submit(self,gid,kind,data):
        self.require();row,source=self.source(gid)
        if kind not in {'mix_preview','mix_master','visuals'}:raise ValueError('This processing adapter is not installed')
        recipe={'mix':mix_recipe(data.get('mix',{}))} if kind.startswith('mix_') else {'version':1,'fps':10}
        if kind=='mix_preview':
            start=data.get('start',0);seconds=data.get('seconds',30)
            if isinstance(start,bool) or not isinstance(start,(float,int)) or not math.isfinite(start) or not 0<=start<row['duration']:raise ValueError('Preview start is outside the recording')
            if type(seconds) is not int or not 1<=seconds<=60:raise ValueError('Preview length must be 1–60 seconds')
            recipe.update(start=start,seconds=min(seconds,row['duration']-start))
        source_hash=digest(source)
        fingerprint=hashlib.sha256(canonical([gid,kind,recipe,source_hash]).encode()).hexdigest()
        with self.lock:
            cached=self.db.one("SELECT id,status FROM processing_tasks WHERE fingerprint=? AND status IN ('queued','running','done') ORDER BY created_at DESC LIMIT 1",(fingerprint,))
            if cached:
                artifacts=self.db.all("SELECT uri FROM generation_artifacts WHERE json_extract(metadata_json,'$.processing_task_id')=?",(cached['id'],))
                if cached['status']!='done' or artifacts and all(Path(a['uri']).is_file() for a in artifacts):return cached
            identifier=self._insert(gid,kind,recipe,source_hash)
            self.submitted.add(identifier)
            self.pool.submit(self._run,identifier)
            return {'id':identifier,'status':'queued'}

    def _run(self,identifier):
        task=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
        clock=time.perf_counter()
        try:
            import psutil
            owner=self.owner()
            with self.db.transaction() as c:
                claimed=c.execute("UPDATE processing_tasks SET status='running',started_at=?,telemetry_json=? WHERE id=? AND status='queued'",(now(),canonical({'owner':owner}),identifier)).rowcount
                if not claimed:return
            row,source=self.source(task['generation_id'])
            if digest(source)!=task['input_sha256']:raise ValueError('Source bytes changed after job creation')
            recipe=json.loads(task['recipe_json']);destination=self.tracks/row['audio_id']/'_derived'
            destination.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.gbr-processing-',dir=destination) as temporary:
                request={'kind':task['kind'],'source':str(source),'source_sha256':task['input_sha256'],'duration':row['duration'],'recipe':recipe,'output':str(Path(temporary)/'result')}
                command=[sys.executable,str(self.worker)]
                process=subprocess.run(command,input=canonical(request),capture_output=True,text=True,timeout=600)
                if process.returncode:raise RuntimeError(process.stderr[-5000:] or 'Processing worker failed')
                result=json.loads(process.stdout);output=Path(result['output'])
                if not output.resolve().is_relative_to(Path(temporary).resolve()) or not output.is_file():raise ValueError('Invalid worker output')
                if digest(source)!=task['input_sha256']:raise ValueError('Source changed while processing; result was not published')
                sha=digest(output);target=destination/(sha+output.suffix)
                if task['kind']=='mix_master':
                    from _review.indexer import audio_identity
                    if audio_identity(output)[0]==row['audio_id']:
                        # An unchanged PCM master reuses the canonical source, even if
                        # FFmpeg would have produced a different FLAC container.
                        target=source;sha=task['input_sha256']
                if target.exists():
                    if digest(target)!=sha:raise ValueError('Derived artifact collision')
                else:output.replace(target)
                software=result['software'];environment=canonical({'python':platform.python_version(),**software})
                address='local://'+socket.gethostname()+'/processing'
                hardware=canonical({'cpu':platform.processor() or None,'ram_total_bytes':psutil.virtual_memory().total})
                session='processing-'+hashlib.sha256((address+hardware+environment).encode()).hexdigest()
                with self.db.transaction() as c:
                    c.execute('INSERT OR IGNORE INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (session,address,'local',None,now(),None,None,None,hardware,environment))
                    c.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                        (identifier+'-output',result['artifact_kind'],sha,str(target),target.stat().st_size,canonical({'processing_task_id':identifier,'source_generation_id':task['generation_id']})))
                    c.execute("UPDATE processing_tasks SET status='done',completed_at=?,worker_session_id=?,telemetry_json=? WHERE id=?",(now(),session,canonical({'wall_seconds':time.perf_counter()-clock,'measurements':result.get('measurements',{})}),identifier))
            if task['kind']=='mix_preview':
                try:self.clean_previews(task['generation_id'],keep=identifier)
                except Exception:pass  # Publication succeeded. Cleanup can be retried independently.
        except Exception as error:
            self.db.execute("UPDATE processing_tasks SET status='failed',completed_at=?,error=?,telemetry_json=? WHERE id=?",(now(),str(error),canonical({'wall_seconds':time.perf_counter()-clock}),identifier))

    def retry(self,identifier):
        self.require()
        task=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
        if not task:raise KeyError('Processing task not found')
        if task['status'] in {'queued','running'}:
            owner=json.loads(task['telemetry_json']).get('owner')
            if not owner or owner.get('host')!=socket.gethostname():
                raise ValueError('Previous controller state is unknown; verify that it stopped before recovering this task')
            if owner.get('nonce')==self.owner_nonce:
                raise ValueError('This task still belongs to a live controller')
            import psutil
            try:
                created=psutil.Process(owner['pid']).create_time()
                if owner.get('created_at') is None:
                    raise ValueError('Previous controller identity is unknown; recovery was not attempted')
                if abs(created-owner['created_at'])<.01:
                    raise ValueError('This task still belongs to a live controller')
            except psutil.NoSuchProcess:pass
            except psutil.AccessDenied:raise ValueError('Cannot verify that the previous controller stopped') from None
        elif task['status'] not in {'failed','interrupted'}:
            raise ValueError('Only a failed or stopped processing task can be retried')
        row,source=self.source(task['generation_id'])
        if digest(source)!=task['input_sha256']:raise ValueError('Source changed; start a new processing recipe')
        with self.lock:
            self.db.execute("UPDATE processing_tasks SET status='interrupted' WHERE id=? AND status IN ('queued','running')",(identifier,))
            new_id=self._insert(task['generation_id'],task['kind'],json.loads(task['recipe_json']),task['input_sha256'])
            self.submitted.add(new_id);self.pool.submit(self._run,new_id)
        return {'id':new_id,'status':'queued','previous_task_id':identifier}

    def clean_previews(self,gid,keep=None):
        removed,locked=0,0
        for artifact in self.db.all("SELECT a.* FROM generation_artifacts a JOIN processing_tasks t ON t.id=json_extract(a.metadata_json,'$.processing_task_id') WHERE t.generation_id=? AND t.kind='mix_preview' AND t.status='done' AND t.id<>?",(gid,keep or '')):
            path=Path(artifact['uri']).resolve()
            if not path.is_relative_to(self.tracks.resolve()):raise ValueError('Preview cache path escaped preparation storage')
            if path.parent.name!='_derived' or path.suffix.lower()!='.wav':continue
            if self.db.one("SELECT 1 FROM generation_artifacts WHERE uri=? AND (kind<>'preview_audio' OR json_extract(metadata_json,'$.processing_task_id')=?)",(str(path),keep or '')):
                continue
            try:
                if path.exists() and digest(path)!=artifact['sha256']:continue
                path.unlink(missing_ok=True);removed+=1
            except OSError:locked+=1
        return {'removed':removed,'in_use':locked}
