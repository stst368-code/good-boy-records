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
from .editorial import lyric_words,validate_lyrics,lighting

DEFAULT_MIX = {'gain_db':0., 'bass_db':0., 'mid_db':0., 'treble_db':0.,
               'vocal_db':0., 'accompaniment_db':0., 'reverb':0., 'room_seconds':1.2, 'fade_in':0., 'fade_out':0., 'limiter_db':None}


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
            'vocal_db':(-24,12),'accompaniment_db':(-24,12),'reverb':(0,1),'room_seconds':(.1,4),'fade_in':(0,30),'fade_out':(0,60),'limiter_db':(-12,0)}
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
        self.showcase=self.root/'_05_showcase'
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
                sql=(self.root/'_98_database/_gbr_database/processing.sql').read_text(encoding="utf-8")
                for statement in sql.split(';'):
                    if statement.strip():c.execute(statement)
            self.review.archiver.preparation_root=self.tracks
            self.review.archiver.showcase_root=self.showcase
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
        if row['archive_path'] and any(Path(row['archive_path']).resolve().is_relative_to(p.resolve()) for p in (self.tracks,self.showcase)):return
        if c.execute("SELECT 1 FROM archive_operations WHERE audio_id=? AND operation_type='prepare' AND status IN ('pending','running','failed') AND review_id=?",(row['audio_id'],row['review_id'])).fetchone():return
        c.execute('''INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,operation_type,created_at,updated_at)
            VALUES(?,?,?,?,?,'prepare',?,?)''',(uuid.uuid4().hex,row['audio_id'],gid,row['review_id'],row['bucket'],now(),now()))

    def sync_selection(self):
        if not self.enabled:return
        with self.db.transaction() as c:
            for row in c.execute("SELECT generation_id FROM publishing_selection WHERE status IN ('selected','published')").fetchall():
                self.queue_promotion(c,row[0])

    def projects(self,include_completed=False):
        if not self.enabled:return {'enabled':False,'projects':[]}
        rows=self.db.all('''SELECT g.id AS generation_id,g.audio_id,s.title,m.parsed_json,a.duration,a.archive_path,p.status
            FROM publishing_selection p JOIN generations g ON g.id=p.generation_id JOIN songs s ON s.id=g.song_id
            JOIN generation_metadata m ON m.generation_id=g.id JOIN audio a ON a.id=g.audio_id
            WHERE (p.status IN ('selected','published') AND EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id))
               OR a.archive_path LIKE ? ORDER BY p.selected_at,g.id''',(str(self.tracks)+'%',))
        rows=[r for r in rows if include_completed or not (r['archive_path'] and Path(r['archive_path']).resolve().is_relative_to(self.showcase.resolve()))]
        for row in rows:
            meta=json.loads(row.pop('parsed_json'))
            row.update(cluster=meta.get('cluster'),style=meta.get('style') or meta.get('style_primary'),
                       song_id=meta.get('song_id'),variant_id=meta.get('variant_id'))
            row['completed']=bool(row['archive_path'] and Path(row['archive_path']).resolve().is_relative_to(self.showcase.resolve()))
            row['ready']=bool(row['archive_path'] and Path(row['archive_path']).is_file() and any(Path(row['archive_path']).resolve().is_relative_to(p.resolve()) for p in (self.tracks,self.showcase)))
            row['tasks']=self.tasks(row['generation_id'])
            row['masters']=[{'id':item['id'],'created_at':item['created_at']} for item in self.db.all("SELECT a.id,a.uri,t.created_at FROM generation_artifacts a JOIN processing_tasks t ON t.id=json_extract(a.metadata_json,'$.processing_task_id') WHERE t.generation_id=? AND t.status='done' AND a.kind='master_candidate' ORDER BY t.rowid DESC",(row['generation_id'],)) if Path(item['uri']).is_file()]
            row['promotion']=self.db.one("SELECT id,status,error FROM archive_operations WHERE audio_id=? AND operation_type='prepare' ORDER BY rowid DESC LIMIT 1",(row['audio_id'],))
        return {'enabled':True,'projects':rows,'ffmpeg':bool(shutil.which('ffmpeg')),
                'reference_root':str(self.references),'worker_root':str(self.worker.parent)}

    def source(self,gid,ready=True,artifact_id=None):
        row=self.db.one('''SELECT g.audio_id,a.archive_path,a.duration,m.parsed_json,m.yaml_sha256
                          FROM generations g JOIN audio a ON a.id=g.audio_id
                          JOIN generation_metadata m ON m.generation_id=g.id WHERE g.id=?''',(gid,))
        if not row:raise KeyError('Recording not found')
        source=self.review.archiver.available(row['audio_id'])
        if not source:raise FileNotFoundError('Recording unavailable')
        if ready and not any(source.resolve().is_relative_to(p.resolve()) for p in (self.tracks,self.showcase)):
            raise ValueError('Wait for verified promotion into Stage 4 before processing')
        if artifact_id:
            artifact=self.db.one("SELECT a.* FROM generation_artifacts a JOIN processing_tasks t ON t.id=json_extract(a.metadata_json,'$.processing_task_id') WHERE a.id=? AND t.generation_id=? AND a.kind='master_candidate' AND t.status='done'",(artifact_id,gid))
            if not artifact:raise ValueError('Choose a master candidate belonging to this recording')
            source=self.artifact(artifact_id)
            if digest(source)!=artifact['sha256']:raise ValueError('Master candidate bytes changed')
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
        if not any(path.is_relative_to(p.resolve()) for p in (self.tracks,self.showcase)) or not path.is_file():raise FileNotFoundError('Preparation artifact unavailable')
        return path

    def editable(self,gid):
        if self.db.one("SELECT 1 FROM processing_tasks t JOIN generations g ON g.id=t.generation_id WHERE g.audio_id=(SELECT audio_id FROM generations WHERE id=?) AND t.kind='showcase_move' AND t.status<>'cancelled'",(gid,)):
            raise ValueError('This recording is being completed or lives in Showcase; retry its publication from Publishing')

    def import_artwork(self,gid,raw):
        import io
        from PIL import Image, ImageOps
        self.require()
        if not raw or len(raw)>10*1024**2:raise ValueError('Choose an image smaller than 10 MiB')
        with self.lock:
            self.editable(gid);row,_=self.source(gid)
            try:
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in {'PNG','JPEG','WEBP'} or image.width*image.height>25_000_000:
                        raise ValueError('Choose a PNG, JPEG or WebP image under 25 megapixels')
                    clean=ImageOps.exif_transpose(image).convert('RGB');output=io.BytesIO();clean.save(output,format='PNG')
            except (OSError,Image.DecompressionBombError) as exc:
                raise ValueError('Cannot read this PNG, JPEG or WebP image') from exc
            content=output.getvalue();sha=hashlib.sha256(content).hexdigest()
            destination=self.tracks/row['audio_id']/'_derived';destination.mkdir(parents=True,exist_ok=True)
            path=destination/(sha+'.png')
            if path.exists():
                if digest(path)!=sha:raise ValueError('Artwork collision; retained both files')
            else:
                with path.open('xb') as stream:stream.write(content);stream.flush();os.fsync(stream.fileno())
            identifier=self._insert(gid,'artwork_import',{'sha256':sha},row['yaml_sha256'] or row['audio_id'],'done')
            aid=identifier+'-artwork'
            self.db.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(aid,'album_artwork',sha,str(path),len(content),canonical({'processing_task_id':identifier,'source_generation_id':gid})))
            return {'id':aid}

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
        self.require();self.editable(gid);row,_=self.source(gid)
        references={r['id'] for r in self.reference_list()}
        if set(data.get('references',[]))-references:raise ValueError('A selected reference is missing or changed')
        recipe=brief(json.loads(row['parsed_json']),data.get('family','comfyui'),data.get('direction',''),data.get('references',[]),data.get('seed',0))
        recipe['source_yaml_sha256']=row['yaml_sha256']
        with self.lock:
            self.editable(gid)
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
        self.require();self.editable(gid);row,source=self.source(gid,artifact_id=data.get('input_artifact_id'))
        if kind not in {'mix_preview','mix_master','visuals','lyric_alignment'}:raise ValueError('This processing adapter is not installed')
        recipe={'mix':mix_recipe(data.get('mix',{}))} if kind.startswith('mix_') else {'version':2,'fps':10,'energy':'channel_rms'}
        if kind=='lyric_alignment':
            language=data.get('language') or json.loads(row['parsed_json']).get('language') or 'en'
            if not isinstance(language,str) or not __import__('re').fullmatch('[a-z]{2,3}',language):raise ValueError('Use an ISO language code, e.g. en or de')
            recipe={'version':1,'language':language}
            if not self.alignment_python().is_file():raise ValueError('Install the isolated processing environment with run_gbr.py setup-processing first')
        if data.get('input_artifact_id'):recipe['input_artifact_id']=data['input_artifact_id']
        if kind=='mix_preview':
            start=data.get('start',0);seconds=data.get('seconds',30)
            if isinstance(start,bool) or not isinstance(start,(float,int)) or not math.isfinite(start) or not 0<=start<row['duration']:raise ValueError('Preview start is outside the recording')
            if type(seconds) is not int or not 1<=seconds<=60:raise ValueError('Preview length must be 1–60 seconds')
            recipe.update(start=start,seconds=min(seconds,row['duration']-start))
        source_hash=digest(source)
        if kind.startswith('mix_') and data.get('stem_task_id'):
            stems=self.stems(gid,data['stem_task_id'],source_hash)
            recipe.update(stem_task_id=data['stem_task_id'],stem_hashes={key:value['sha256'] for key,value in stems.items()})
        elif kind.startswith('mix_') and (recipe['mix']['vocal_db'] or recipe['mix']['accompaniment_db']):raise ValueError('Enable the separated stems to use vocal/accompaniment faders')
        fingerprint=hashlib.sha256(canonical([gid,kind,recipe,source_hash]).encode()).hexdigest()
        with self.lock:
            self.editable(gid)
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
        clock=time.perf_counter();failure_telemetry={}
        try:
            import psutil
            owner=self.owner()
            with self.db.transaction() as c:
                claimed=c.execute("UPDATE processing_tasks SET status='running',started_at=?,telemetry_json=? WHERE id=? AND status='queued'",(now(),canonical({'owner':owner}),identifier)).rowcount
                if not claimed:return
            recipe=json.loads(task['recipe_json'])
            row,source=self.source(task['generation_id'],artifact_id=recipe.get('input_artifact_id'))
            if digest(source)!=task['input_sha256']:raise ValueError('Source bytes changed after job creation')
            recipe=json.loads(task['recipe_json']);destination=self.tracks/row['audio_id']/'_derived'
            destination.mkdir(parents=True,exist_ok=True)
            with tempfile.TemporaryDirectory(prefix='.gbr-processing-',dir=destination) as temporary:
                request={'kind':task['kind'],'source':str(source),'source_sha256':task['input_sha256'],'duration':row['duration'],'recipe':recipe,'output':str(Path(temporary)/'result')}
                command=[sys.executable,str(self.worker)]
                stems={}
                if recipe.get('stem_task_id'):
                    stems=self.stems(task['generation_id'],recipe['stem_task_id'],task['input_sha256'])
                    if {key:value['sha256'] for key,value in stems.items()}!=recipe['stem_hashes']:raise ValueError('Stem identities changed')
                    request['stems']={key:value['path'] for key,value in stems.items()}
                timeout=600
                if task['kind']=='lyric_alignment':
                    request['recipe']['words']=lyric_words(json.loads(row['parsed_json']).get('lyrics',''))
                    command=[str(self.alignment_python()),str(self.worker.with_name('alignment.py'))];timeout=7200
                environment=os.environ.copy()
                environment.update(PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
                environment.setdefault('HF_HOME',str(self.worker.parent/'_runtime/huggingface'))
                environment.setdefault('TORCH_HOME',str(self.worker.parent/'_runtime/torch'))
                process=subprocess.run(command,input=canonical(request).encode('utf-8'),capture_output=True,timeout=timeout,env=environment)
                if process.returncode:
                    resource_file=Path(temporary)/'resources.ndjson'
                    if resource_file.is_file():
                        observations=[json.loads(line) for line in resource_file.read_text(encoding="utf-8").splitlines()]
                        ended=[row for row in observations if row.get('event')=='stage_end']
                        if ended:failure_telemetry={'resources':ended[-1]['resource']}
                    raise RuntimeError(process.stderr.decode('utf-8',errors='replace')[-5000:] or 'Processing worker failed')
                result=json.loads(process.stdout.decode('utf-8'));output=Path(result['output'])
                if not output.resolve().is_relative_to(Path(temporary).resolve()) or not output.is_file():raise ValueError('Invalid worker output')
                if digest(source)!=task['input_sha256']:raise ValueError('Source changed while processing; result was not published')
                if any(digest(value['path'])!=value['sha256'] for value in stems.values()):raise ValueError('Stem bytes changed while processing')
                published=[]
                for item in [{'output':str(output),'artifact_kind':result['artifact_kind']},*result.get('additional_outputs',[])]:
                    output=Path(item['output'])
                    if not output.resolve().is_relative_to(Path(temporary).resolve()) or not output.is_file():raise ValueError('Invalid additional worker output')
                    sha=digest(output);target=destination/(sha+output.suffix)
                    if task['kind']=='mix_master':
                        from _review.indexer import audio_identity
                        if audio_identity(output)[0]==row['audio_id']:target=source;sha=task['input_sha256']
                    if target.exists():
                        if digest(target)!=sha:raise ValueError('Derived artifact collision')
                    else:output.replace(target)
                    published.append((item['artifact_kind'],sha,target))
                software=result['software'];environment=canonical({'python':platform.python_version(),**software})
                address=result.get('hardware',{}).get('address') or 'local://'+socket.gethostname()+'/processing'
                hardware=canonical({'cpu':platform.processor() or None,'ram_total_bytes':psutil.virtual_memory().total,**result.get('hardware',{})})
                session='processing-'+hashlib.sha256((address+hardware+environment).encode()).hexdigest()
                with self.db.transaction() as c:
                    c.execute('INSERT OR IGNORE INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (session,address,'local',None,now(),result.get('hardware',{}).get('gpu_uuid'),result.get('hardware',{}).get('gpu_name'),result.get('hardware',{}).get('usable_vram_bytes'),hardware,environment))
                    for index,(kind,sha,target) in enumerate(published):
                        c.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                            (identifier+('-output' if index==0 else '-'+str(index)),kind,sha,str(target),target.stat().st_size,canonical({'processing_task_id':identifier,'source_generation_id':task['generation_id']})))
                    c.execute("UPDATE processing_tasks SET worker_session_id=?,telemetry_json=? WHERE id=?",(session,canonical({'wall_seconds':time.perf_counter()-clock,'measurements':result.get('measurements',{})}),identifier))
            cleanup={}
            if task['kind']=='mix_preview':
                try:cleanup=self.clean_previews(task['generation_id'],keep=identifier)
                except Exception as error:cleanup={'error':str(error)}
            self.db.execute("UPDATE processing_tasks SET status='done',completed_at=?,telemetry_json=? WHERE id=?",(now(),canonical({'wall_seconds':time.perf_counter()-clock,'measurements':result.get('measurements',{}),'cleanup':cleanup}),identifier))
        except Exception as error:
            self.db.execute("UPDATE processing_tasks SET status='failed',completed_at=?,error=?,telemetry_json=? WHERE id=?",(now(),str(error),canonical({'wall_seconds':time.perf_counter()-clock,**failure_telemetry}),identifier))

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
        row,source=self.source(task['generation_id'],artifact_id=json.loads(task['recipe_json']).get('input_artifact_id'))
        if digest(source)!=task['input_sha256']:raise ValueError('Source changed; start a new processing recipe')
        with self.lock:
            self.editable(task['generation_id'])
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


    def alignment_python(self):
        return self.worker.parent/'.venv'/('Scripts/python.exe' if os.name=='nt' else 'bin/python')

    def editorial(self,gid,artifact_id=None):
        self.require();row,source=self.source(gid,artifact_id=artifact_id);sha=digest(source)
        result={'source_sha256':sha,'duration':row['duration'],'language':json.loads(row['parsed_json']).get('language') or 'en',
                'words':lyric_words(json.loads(row['parsed_json']).get('lyrics','')),'approved':False,'lyric_task_id':None,
                'features':None,'feature_artifact_id':None,'lighting_task_id':None,'sensitivity':1,'sections':[]}
        for kinds,key in [(('lyric_edit','lyric_alignment'),'lyric'),(('visuals',),'visual'),(('visual_edit',),'light')]:
            placeholders=','.join('?' for _ in kinds)
            task=self.db.one(f"SELECT * FROM processing_tasks WHERE generation_id=? AND input_sha256=? AND kind IN ({placeholders}) AND status='done' ORDER BY rowid DESC LIMIT 1",(gid,sha,*kinds))
            if not task:continue
            artifact=self.db.one("SELECT id,sha256 FROM generation_artifacts WHERE json_extract(metadata_json,'$.processing_task_id')=? AND kind IN ('lyric_alignment','lyric_edit','visual_features','visual_edit')",(task['id'],))
            if not artifact:continue
            path=self.artifact(artifact['id'])
            if digest(path)!=artifact['sha256']:raise ValueError('Editorial sidecar bytes changed; restore the registered version')
            data=json.loads(path.read_text(encoding="utf-8"))
            if data.get('source_sha256')!=sha:raise ValueError('Sidecar refers to different audio')
            if key=='lyric':result.update(language=data.get('language',result['language']),words=data['words'],approved=data.get('approved',False),lyric_task_id=task['id'],unmatched_recognition=data.get('unmatched_recognition',[]))
            elif key=='visual':result.update(features=data,feature_artifact_id=artifact['id'])
            else:result.update(sensitivity=data['sensitivity'],sections=data['sections'],lighting_task_id=task['id'])
        return result

    def save_editorial(self,gid,kind,data):
        self.require()
        with self.lock:
            self.editable(gid)
            current=self.editorial(gid,data.get('input_artifact_id'))
            key='lyric_task_id' if kind=='lyric_edit' else 'lighting_task_id'
            if data.get('parent_task_id')!=current[key]:raise ValueError('This draft is stale; reload the latest saved revision before editing')
            if data.get('source_sha256')!=current['source_sha256']:raise ValueError('Audio changed; reload the timeline')
            if kind=='lyric_edit':
                words=validate_lyrics(data,current['words'],current['duration'])
                approved=data.get('approved',False)
                if type(approved) is not bool:raise ValueError('Invalid lyric approval')
                if approved and any(not w['omitted'] and (w['start'] is None or not w['confirmed'] and (w.get('edited') or w['confidence'] is None or w['confidence']<.7)) for w in words):raise ValueError('Check and confirm every low-confidence or untimed lyric before approval')
                payload={'format':'gbr-lyrics-v1','source_sha256':current['source_sha256'],'language':current['language'],
                         'words':words,'approved':approved,'unmatched_recognition':current.get('unmatched_recognition',[])}
            elif kind=='visual_edit':
                if not current['features']:raise ValueError('Calculate visual features before saving light settings')
                payload=lighting(data,current['features'])
            else:raise ValueError('Unknown editorial kind')
            recipe={'parent_task_id':current[key],'input_artifact_id':data.get('input_artifact_id'),'feature_artifact_id':current['feature_artifact_id'] if kind=='visual_edit' else None}
            raw=canonical(payload).encode('utf-8');sha=hashlib.sha256(raw).hexdigest();recipe['output_sha256']=sha
            _,source=self.source(gid,artifact_id=data.get('input_artifact_id'));destination=source.parent.parent/'_derived';destination.mkdir(parents=True,exist_ok=True)
            path=destination/(sha+'.json')
            if path.exists():
                if digest(path)!=sha:raise ValueError('Editorial artifact collision')
            else:
                with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
            identifier=uuid.uuid4().hex
            fingerprint=hashlib.sha256(canonical([gid,kind,recipe,current['source_sha256']]).encode()).hexdigest()
            with self.db.transaction() as c:
                c.execute('INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (identifier,gid,kind,'done',fingerprint,canonical(recipe),current['source_sha256'],now(),now()))
                c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                    (identifier+'-output',kind,sha,str(path),len(raw),canonical({'processing_task_id':identifier,'source_generation_id':gid})))
            return {'id':identifier,**self.editorial(gid,data.get('input_artifact_id'))}


    def stems(self,gid,task_id,source_hash):
        task=self.db.one("SELECT id FROM processing_tasks WHERE id=? AND generation_id=? AND kind='lyric_alignment' AND status='done' AND input_sha256=?",(task_id,gid,source_hash))
        if not task:raise ValueError('Choose completed separation for this working audio')
        result={}
        for kind in ['vocals','accompaniment']:
            item=self.db.one("SELECT id,sha256 FROM generation_artifacts WHERE kind=? AND json_extract(metadata_json,'$.processing_task_id')=?",('stem_'+kind,task_id))
            if not item:raise ValueError('Required separated stem is unavailable')
            path=self.artifact(item['id'])
            if digest(path)!=item['sha256']:raise ValueError('Separated stem bytes changed')
            result[kind]={'path':str(path),'sha256':item['sha256']}
        return result
