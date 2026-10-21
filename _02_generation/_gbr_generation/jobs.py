"""Batch dashboard and run controls over the existing generation ledger."""
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import re

from .planner import open_database,verify_plan
from .yaml_document import YamlDocument

LIVE={'running','publishing','cancel_requested'}
TERMINAL={'succeeded','failed','cancelled'}


def identity(target):return target['backend']+'|'+(target.get('address') or '')


class Jobs:
    def __init__(self,generation):self.g=generation

    def controllers(self):
        latest={}
        for task in self.g.tasks(include_logs=False):
            key=(task.get('batch_id') or task['id'],task.get('worker_id') or identity(task['connection']))
            # A live controller takes precedence over an old completed/check snapshot.
            if key not in latest or task['active'] and not latest[key]['active']:latest[key]=task
        return list(latest.values())

    def listing(self):
        tasks=self.controllers();by_batch={}
        for task in tasks:
            if task.get('batch_id'):by_batch.setdefault(task['batch_id'],[]).append(task)
        batches=self.g.review.db.all("SELECT id,created_at,status,selection_json,policy_json FROM generation_batches WHERE status NOT IN ('succeeded','failed','cancelled') OR id IN (SELECT id FROM generation_batches ORDER BY created_at DESC LIMIT 100) ORDER BY created_at DESC")
        result=[]
        for batch in batches:
            policy=json.loads(batch.pop('policy_json'));selection=json.loads(batch.pop('selection_json'))
            counts=Counter(r['status'] for r in self.g.review.db.all('SELECT status FROM generation_runs WHERE batch_id=?',(batch['id'],)))
            workers=by_batch.get(batch['id'],[]);active=any(t['active'] for t in workers)
            if active and (counts['prepared'] or any(counts[s] for s in LIVE)):section='active';status='running' if any(counts[s] for s in LIVE) else 'queued'
            elif counts and counts['succeeded']==sum(counts.values()):section='completed';status='completed'
            elif counts['failed'] or any(t.get('error') and t['status']=='failed' for t in workers) or any(counts[s] for s in LIVE):section='failed';status='interrupted' if any(counts[s] for s in LIVE) else 'failed'
            elif counts['prepared']:section='active';status='paused' if any(t['status']=='paused' for t in workers) else 'queued'
            elif counts['cancelled']:section='failed';status='cancelled'
            else:section='completed';status='completed'
            known={t.get('worker_id') or identity(t['connection']) for t in workers}
            available=[{'id':e['id'],'connection':e['connection']} for e in self.g.workers.entries() if e['enabled'] and e['id'] not in known] if counts['prepared'] else []
            automatic=policy.get('automatic_workers',selection.get('worker_id') in {'auto','all'} or selection.get('worker_id') is None and any(t.get('worker_id') for t in workers))
            result.append({**batch,'status':status,'section':section,'counts':dict(counts),'total':sum(counts.values()),'available_workers':available,'automatic_workers':automatic,
                'worker_notices':[{'worker_id':wid,'message':message} for (bid,wid),message in list(self.g.dispatcher.notices.items()) if bid==batch['id']],
                'workers':[{**{k:t.get(k) for k in ('id','worker_id','connection','status','active','run_id','stage','step','steps','message','updated_at','error','report','cleanup_pending')},'current_title':next((r['title'] for r in t.get('runs',[]) if r['id']==t.get('run_id')),None)} for t in workers]})
        return {'jobs':result,'history_limit':100}

    def batch(self,batch_id):
        if not re.fullmatch('[0-9a-f]{32}',batch_id):raise ValueError('Invalid batch ID')
        path=self.g.runtime/'batches'/batch_id/'manifest.json'
        if not path.is_file():
            path=next((Path(t['manifest']) for t in self.g.tasks(False) if t.get('batch_id')==batch_id),path)
        manifest=verify_plan(path)
        if manifest['batch_id']!=batch_id:raise ValueError('Batch identity changed')
        return path,manifest

    def details(self,batch_id,offset=0,limit=100):
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid page')
        path,manifest=self.batch(batch_id);frozen={r['id']:r for r in manifest['runs']}
        controllers=[t for t in self.controllers() if t.get('batch_id')==batch_id]
        rows=self.g.review.db.all('SELECT id,title,song_id,variant_id,status,effective_json,started_at,completed_at FROM generation_runs WHERE batch_id=? ORDER BY rowid LIMIT ? OFFSET ?',(batch_id,limit,offset))
        result=[]
        for row in rows:
            parameters=json.loads(row.pop('effective_json'));parameters.pop('caption',None)
            attempts=self.g.review.db.all('SELECT id,status,error,started_at FROM generation_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(row['id'],))
            attempt=attempts[0] if attempts else None;worker=None;stage=row['status'];extra={}
            if attempt:
                request=path.parent/'attempts'/attempt['id']/'request.json'
                try:worker=identity(json.loads(request.read_text())['execution']['diffusion'])
                except (OSError,ValueError,KeyError):pass
                current=next((t for t in controllers if (t.get('worker_id') or identity(t['connection']))==worker),None)
                if current and row['status'] in LIVE:
                    stage=current.get('stage') if current.get('run_id')==row['id'] else 'publishing' if row['status']=='publishing' else 'reconnect' if not current['active'] else 'generating'
                    extra={k:current.get(k) for k in ('step','steps','message') if current.get('run_id')==row['id']}
            elif row['status']=='prepared' and controllers and all(t['status']=='waiting_worker' for t in controllers):stage='waiting_worker'
            options=frozen.get(row['id'],{}).get('worker_options') or []
            eligible=set(o['worker_id'] for o in options)
            eligible.update(c.get('worker_id') or identity(c['connection']) for c in controllers if any(r['id']==row['id'] for r in c.get('runs',[])))
            if not worker and not attempt:
                if len(eligible)==1:worker=next(iter(eligible))
            result.append({**row,'parameters':parameters,'attempt':attempt,'worker':worker,'stage':stage or row['status'],**extra,
                'eligible_workers':sorted(eligible), 'can_cancel':row['status'] in {'prepared','running','failed'},
                'error':attempt.get('error') if attempt and row['status']=='failed' else None})
        return {'runs':result,'offset':offset,'total':len(frozen),'has_more':offset+len(rows)<len(frozen)}

    def text(self,batch_id,run_id):
        path,manifest=self.batch(batch_id)
        if run_id not in {r['id'] for r in manifest['runs']}:raise ValueError('Run is not in this batch')
        row=self.g.review.db.one('SELECT effective_json,source_sha256 FROM generation_runs WHERE id=?',(run_id,))
        source=self.g.review.db.one('SELECT raw_bytes FROM source_documents WHERE sha256=?',(row['source_sha256'],))
        return {'caption':json.loads(row['effective_json']).get('caption',''),'lyrics':YamlDocument(source['raw_bytes']).values.get('lyrics','')}

    def cancel(self,batch_id,run_id):
        path,manifest=self.batch(batch_id)
        if run_id not in {r['id'] for r in manifest['runs']}:raise ValueError('Run is not in this batch')
        with closing(open_database(self.g.review.config.database)) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status FROM generation_runs WHERE id=?',(run_id,)).fetchone()
            if row['status'] in {'succeeded','publishing'}:raise ValueError('This recording is already saved or finalising; it cannot be cancelled')
            status='cancel_requested' if row['status'] in {'running','cancel_requested'} else 'cancelled'
            now=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            conn.execute('UPDATE generation_runs SET status=?,completed_at=? WHERE id=?',(status,None if status=='cancel_requested' else now,run_id))
            remaining=conn.execute("SELECT 1 FROM generation_runs WHERE batch_id=? AND status NOT IN ('succeeded','failed','cancelled')",(batch_id,)).fetchone()
            if not remaining:conn.execute("UPDATE generation_batches SET status='cancelled' WHERE id=?",(batch_id,))
        if row['status']=='failed':
            from .cancellation import discard
            attempt=self.g.review.db.one('SELECT id FROM generation_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,))
            if attempt:
                directory=path.parent/'attempts'/attempt['id']
                if (directory/'request.json').is_file() and discard(directory,json.loads((directory/'request.json').read_text())):
                    (directory/'cleanup-pending.json').write_text(json.dumps({'cancelled':True,'error':'Remote cancellation output cleanup may be retried'}))
        return {'run_id':run_id,'status':status}
