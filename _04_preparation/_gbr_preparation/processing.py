"""Live local processing status and bounded alignment progress on the shared ledger."""
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import time


class ProcessingQueue:
    def __init__(self,prep):self.prep=prep;self.db=prep.db

    @staticmethod
    def alive(identity):
        import psutil
        if not identity.get('pid'):return False
        try:
            process=psutil.Process(identity['pid'])
            return identity.get('created_at') is None or abs(process.create_time()-identity['created_at'])<.01
        except psutil.NoSuchProcess:return False
        except psutil.AccessDenied:return True

    def generation_busy(self):
        # Status rows are historical evidence, not proof that a GPU is occupied.
        # Probe the same OS locks as the native worker and Comfy controller.
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_workers_infdit'))
        from _gbr_worker.hardware import gpu_lock
        rows=self.db.all("""SELECT DISTINCT w.address,w.gpu_uuid,a.execution_mode FROM generation_attempts a
            LEFT JOIN worker_sessions w ON w.id=a.worker_session_id WHERE a.status IN ('running','queued')
            AND (a.execution_mode='local' OR w.provider='local' OR w.address LIKE '%127.0.0.1%' OR w.address LIKE '%localhost%')""")
        keys=set()
        for row in rows:
            if row['gpu_uuid']:keys.add(row['gpu_uuid'])
            address=row['address'] or ''
            if address.startswith(('http://127.0.0.1','http://localhost','https://127.0.0.1','https://localhost')):
                keys.add('gbr-controller:comfyui:'+address.rstrip('/'))
            if row['execution_mode']=='local':keys.add('gbr-controller:local:')
        for key in keys:
            try:
                with gpu_lock(key):pass
            except RuntimeError:return True
        return False

    def reason(self,task):
        if not self.prep.alignment_python().is_file():return 'Processing environment is missing. Run py -3.12 _97_app/run_gbr.py setup-processing on this PC.'
        if self.prep.local_generation_busy():return 'A live local generation worker is busy; alignment will start after it releases the worker.'
        active=self.db.one("SELECT id FROM processing_tasks WHERE id<>? AND kind IN ('lyric_alignment','mix_master','mix_preview','visuals') AND status IN ('queued','running') ORDER BY rowid LIMIT 1",(task['id'],))
        if active:return 'Queued behind another local processing task; songs run one at a time.'
        earlier=self.db.one("SELECT 1 FROM processing_tasks WHERE kind='lyric_alignment' AND status='waiting' AND rowid<(SELECT rowid FROM processing_tasks WHERE id=?) LIMIT 1",(task['id'],))
        if earlier:return 'Queued behind an earlier alignment; songs run one at a time.'
        telemetry=json.loads(task.get('telemetry_json') or '{}')
        if telemetry.get('retry_after',0)>time.time():return 'The worker reported a GPU lock in use; automatically retrying shortly.'
        return None

    def recover(self):
        import psutil
        from .service import now
        rows=self.db.all("SELECT id,telemetry_json FROM processing_tasks WHERE kind='lyric_alignment' AND status IN ('queued','running')")
        for row in rows:
            data=json.loads(row['telemetry_json']);owner=data.get('owner',{})
            if owner.get('host')!=socket.gethostname() or not owner.get('created_at'):continue
            if not self.alive(owner) and not self.alive(data.get('child',{})):
                self.db.execute("UPDATE processing_tasks SET status='interrupted',completed_at=?,error='Previous alignment controller stopped. Retry this task from the processing queue.' WHERE id=? AND status IN ('queued','running')",(now(),row['id']))

    def status(self):
        if not self.prep.enabled:return {'enabled':False,'items':[]}
        self.recover()
        rows=self.db.all("""SELECT t.id,t.generation_id,t.kind,t.status,t.error,t.created_at,t.started_at,t.completed_at,t.telemetry_json,s.title
            FROM processing_tasks t JOIN generations g ON g.id=t.generation_id JOIN songs s ON s.id=g.song_id
            WHERE t.kind IN ('lyric_alignment','mix_master','mix_preview','visuals')
            AND (t.status IN ('waiting','queued','running','failed','interrupted') OR t.rowid IN
                (SELECT rowid FROM processing_tasks WHERE kind='lyric_alignment' AND status='done' ORDER BY rowid DESC LIMIT 5))
            AND (t.status NOT IN ('failed','interrupted') OR NOT EXISTS (SELECT 1 FROM processing_tasks newer
                WHERE newer.generation_id=t.generation_id AND newer.kind=t.kind AND newer.rowid>t.rowid))
            ORDER BY CASE t.status WHEN 'running' THEN 0 WHEN 'queued' THEN 1 WHEN 'waiting' THEN 2 ELSE 3 END,t.rowid""")
        waiting=0
        for row in rows:
            row['progress']=json.loads(row.pop('telemetry_json'))
            if row['status'] in {'waiting','queued'}:
                waiting+=1;row['position']=waiting
            if row['status']=='waiting':row['reason']=self.reason({'id':row['id'],'telemetry_json':json.dumps(row['progress'])}) or 'Ready for the next automatic queue check.'
        return {'enabled':True,'environment_ready':self.prep.alignment_python().is_file(),'python':str(self.prep.alignment_python()),
                'items':rows,'running':sum(r['status']=='running' for r in rows),'pending':waiting,
                'failed':sum(r['status'] in {'failed','interrupted'} for r in rows)}

    def resume(self):
        self.recover()
        for row in self.db.all("SELECT * FROM processing_tasks WHERE kind='lyric_alignment' AND status='waiting' ORDER BY rowid"):
            try:
                data=self.prep.metadata.read(row['generation_id']);recipe=json.loads(row['recipe_json'])
                if recipe.get('lyrics_sha256',data['original_lyrics_sha256'])!=data['lyrics_sha256']:
                    self.db.execute("UPDATE processing_tasks SET status='cancelled',error='Superseded by changed lyrics or language' WHERE id=?",(row['id'],));continue
            except (ValueError,OSError) as exc:
                from .service import now
                self.db.execute("UPDATE processing_tasks SET status='failed',completed_at=?,error=? WHERE id=?",(now(),str(exc),row['id']));continue
            result=self.prep.launch_waiting(row)
            if result.get('status')=='queued':break
        return self.status()

    def execute(self,identifier,command,request,environment,temporary,timeout):
        """Never buffer hours of worker output invisibly in subprocess.run."""
        import psutil
        from .service import canonical,now
        folder=Path(temporary);progress=folder/'progress.json';request['progress_file']=str(progress)
        started=time.monotonic();process=None
        with (folder/'stdout.log').open('w+b') as stdout,(folder/'worker.log').open('w+b') as stderr:
            try:
                process=subprocess.Popen(command,stdin=subprocess.PIPE,stdout=stdout,stderr=stderr,env=environment)
                try:created=psutil.Process(process.pid).create_time()
                except psutil.Error:created=None
                child={'pid':process.pid,'created_at':created}
                process.stdin.write(canonical(request).encode('utf-8'));process.stdin.close()
                details={'phase':'starting','message':'Starting isolated processing environment'}
                while True:
                    try:
                        if progress.is_file():details=json.loads(progress.read_text(encoding='utf-8'))
                    except (OSError,ValueError):pass
                    with (folder/'worker.log').open('rb') as log:
                        log.seek(0,2);size=log.tell();log.seek(max(0,size-6000));tail=log.read().decode('utf-8',errors='replace')
                    tail=re.sub(r'\x1b\[[0-9;]*[A-Za-z]','',tail).replace('\r','\n')
                    previous=json.loads(self.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(identifier,))['telemetry_json'])
                    data={**previous,**details,'child':child,'wall_seconds':time.monotonic()-started,'last_update':now(),'log_tail':tail}
                    self.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical(data),identifier))
                    code=process.poll()
                    if code is not None:break
                    if self.prep.closed:raise RuntimeError('Application closed during alignment; retry the interrupted task')
                    if time.monotonic()-started>timeout:raise TimeoutError('Alignment exceeded its execution timeout; inspect the worker log and retry')
                    time.sleep(.5)
                stdout.seek(0)
                with (folder/'worker.log').open('rb') as log:
                    log.seek(max(0,size-6000));error=log.read()
                return subprocess.CompletedProcess(command,code,stdout.read(),error)
            finally:
                if process and process.poll() is None:process.kill();process.wait()
