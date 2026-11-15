"""Batch dashboard and run controls over the existing generation ledger."""
from collections import Counter
from contextlib import closing
from datetime import datetime,timezone
import json
from pathlib import Path
import re
from random import shuffle

from .cancellation import update_batch_status
from .planner import json_text,open_database,verify_plan
from .workers import LIVE_POD_STATES
from .yaml_document import YamlDocument

LIVE={'running','publishing','cancel_requested'}
TERMINAL={'succeeded','failed','cancelled'}
REMOTE={'comfyui','runpod_dedicated','runpod_serverless'}
COMFY_STATUS_MAX_AGE=8


def identity(target):return target['backend']+'|'+(target.get('address') or '')


def run_activity(status, backend, controller, *, now):
    """Translate a ledger state and its matching live attempt into a display phase."""
    if status not in LIVE:
        return {'phase':status}
    if not controller:
        if status == 'cancel_requested':
            return {'phase':'cancel_unconfirmed'}
        return {'phase':'reconnect' if backend in REMOTE else 'interrupted'}

    stage = controller.get('stage')
    if status == 'cancel_requested':
        if stage == 'cancel_after_generation':
            phase = 'finishing_to_cancel'
        else:
            phase = 'cancelling' if stage == 'cancelling' else 'cancel_requested'
        return {'phase':phase,'stage':stage,'message':controller.get('message')}
    if status == 'publishing' or stage in {'downloading','publishing'}:
        return {'phase':'finalising','stage':stage if stage == 'downloading' else 'publishing'}
    if stage == 'starting_worker':
        return {'phase':'preparing','stage':stage}

    if backend == 'comfyui':
        try:
            age = (now - datetime.fromisoformat(controller.get('updated_at') or '')).total_seconds()
        except (ValueError, TypeError):
            age = float('inf')
        if age > COMFY_STATUS_MAX_AGE or not controller.get('prompt_id'):
            return {'phase':'awaiting_status','stage':'comfy_waiting'}
        if stage == 'comfy_queued':
            return {'phase':'worker_queue','stage':stage}
        if stage != 'comfy_generating':
            return {'phase':'awaiting_status','stage':'comfy_waiting'}
    elif stage == 'serverless_queued':
        return {'phase':'worker_queue','stage':stage}
    elif not stage or stage == 'running':
        return {'phase':'awaiting_status'}

    return {'phase':'running', **{key:controller.get(key) for key in
            ('stage','node_label','step','steps','progress_unit','message')}}


class Jobs:
    def __init__(self,generation):self.g=generation

    def controllers(self):
        latest={}
        for task in self.g.tasks(include_logs=False):
            key=(task.get('batch_id') or task['id'],task.get('worker_id') or identity(task['connection']))
            # A live controller takes precedence over an old completed/check snapshot.
            if key not in latest or task['active'] and not latest[key]['active']:latest[key]=task
        return list(latest.values())

    def queue(self, status, offset, *, batch_id=None, search='', order='newest'):
        snapshots = {task['run_id']:task for task in self.g.tasks(include_logs=False,live_only=True)
                     if task.get('attempt_id') and task.get('run_id')}
        search = search.strip().casefold()
        filters = ["COALESCE(json_extract(b.policy_json,'$.purpose'),'production')='production'"]
        if batch_id:
            filters.append('r.batch_id=:batch')
        if search:
            filters.append('(instr(casefold(r.title),:search)>0 OR instr(r.batch_id,:search)=1)')
        source = 'FROM generation_runs r JOIN generation_batches b ON b.id=r.batch_id WHERE ' + ' AND '.join(filters)
        scope = f"""
            SELECT r.id,r.batch_id,r.title,r.style,r.variant_id,r.status,r.created_at AS queued_at,
                   r.started_at AS dispatched_at,r.completed_at,r.rowid AS sequence,b.created_at AS batch_created,
                   CASE
                       WHEN r.status='prepared' THEN 'queued'
                       WHEN r.status IN ('running','publishing','cancel_requested') THEN
                           CASE WHEN r.id IN (SELECT value FROM json_each(:live)) THEN 'queued' ELSE 'attention' END
                       WHEN r.status='succeeded' THEN 'completed'
                       ELSE r.status
                   END AS tab
            {source}
        """
        params = {'batch':batch_id,'search':search,'tab':status,'offset':offset}
        with closing(open_database(self.g.review.config.database)) as conn:
            from .planner import ensure_queue_order
            ensure_queue_order(conn)
            if search:
                conn.create_function('casefold',1,str.casefold,deterministic=True)
            conn.execute('BEGIN')
            # An old snapshot must not keep an abandoned attempt in the live queue.
            live = {}
            if snapshots:
                for row in conn.execute("""
                    SELECT a.run_id,a.id FROM generation_attempts a WHERE a.id IN (SELECT value FROM json_each(?))
                      AND a.id=(SELECT id FROM generation_attempts WHERE run_id=a.run_id ORDER BY rowid DESC LIMIT 1)
                    """, (json_text([t['attempt_id'] for t in snapshots.values()]),)):
                    task = snapshots.get(row['run_id'])
                    if task and task['attempt_id']==row['id']:
                        live[row['run_id']]=task
            params['live']=json_text(list(live))
            totals = dict(conn.execute(f'SELECT r.status,COUNT(*) {source} GROUP BY r.status',params))
            waiting = totals.get('prepared',0)
            active = sum(totals.get(state,0) for state in LIVE)
            with_workers = conn.execute(f"""
                SELECT COUNT(*) {source} AND r.status IN ('running','publishing','cancel_requested')
                AND r.id IN (SELECT value FROM json_each(:live))
                """,params).fetchone()[0] if live else 0
            counts = {'queued':waiting+with_workers,'attention':active-with_workers,'completed':totals.get('succeeded',0),
                      'cancelled':totals.get('cancelled',0),'failed':totals.get('failed',0)}
            if status == 'queued':
                direction = 'ASC' if order=='dispatch' else 'DESC'
                ordering = f"(p.status='prepared'),p.batch_created {direction},p.queue_position,p.sequence"
            else:
                ordering = 'COALESCE(p.completed_at,p.dispatched_at,p.queued_at) DESC,p.sequence DESC'
            statuses = {
                'queued': "'prepared','running','publishing','cancel_requested'",
                'attention': "'running','publishing','cancel_requested'",
                'completed': "'succeeded'",
                'cancelled': "'cancelled'",
                'failed': "'failed'",
            }[status]
            rows = [dict(row) for row in conn.execute(f"""
                WITH scoped AS ({scope} AND r.status IN ({statuses})), sorted AS (
                    SELECT s.*,COALESCE(q.position,s.sequence) AS queue_position
                    FROM scoped s LEFT JOIN generation_queue_order q ON q.run_id=s.id
                    WHERE s.tab=:tab
                ), page AS (
                    SELECT * FROM sorted p ORDER BY {ordering} LIMIT 50 OFFSET :offset
                )
                SELECT p.id,p.batch_id,p.title,p.style,p.variant_id,p.status,p.queued_at,p.dispatched_at,p.completed_at,
                       a.error,a.execution_mode AS backend,s.started_at AS execution_started_at
                FROM page p
                LEFT JOIN generation_attempts a ON a.id=(
                    SELECT id FROM generation_attempts WHERE run_id=p.id ORDER BY rowid DESC LIMIT 1
                )
                LEFT JOIN generation_timing_spans s ON s.id=a.id||'-comfy'
                ORDER BY {ordering}
                """,params)]
        now = datetime.now(timezone.utc)
        for row in rows:
            controller = live.get(row['id'])
            row.update(run_activity(row['status'], row['backend'], controller, now=now))
            if row['phase']=='cancel_unconfirmed':
                row['message']=self.g.dispatcher.cancellations.notices.get(row['id']) or 'Waiting for confirmation from the worker.'
            recorded_start = row.pop('execution_started_at')
            row['started_at'] = ((controller or {}).get('execution_started_at') or recorded_start
                                 if row['backend'] == 'comfyui' else row['dispatched_at'])
            row['worker_id'] = ((controller.get('worker_id') or identity(controller['connection']))
                                if controller else None)
            if row['status'] in LIVE:
                row['completed_at'] = None
        return {'runs':rows,'counts':counts,'waiting':waiting,'can_shuffle':bool(waiting)}

    def reorder(self, action, run_ids, batch_id=None):
        selected = set(run_ids)
        with closing(open_database(self.g.review.config.database)) as conn, conn:
            from .planner import ensure_queue_order
            ensure_queue_order(conn)
            conn.execute('BEGIN IMMEDIATE')
            groups = {}
            for row in conn.execute("""SELECT r.batch_id,r.id FROM generation_runs r
                    JOIN generation_batches b ON b.id=r.batch_id LEFT JOIN generation_queue_order q ON q.run_id=r.id
                    WHERE r.status='prepared' AND COALESCE(json_extract(b.policy_json,'$.purpose'),'production')='production'
                      AND (? IS NULL OR r.batch_id=?)
                    ORDER BY r.batch_id,COALESCE(q.position,r.rowid),r.rowid""", (batch_id,batch_id)):
                groups.setdefault(row['batch_id'],[]).append(row['id'])
            for queue in groups.values():
                if action == 'shuffle':
                    shuffle(queue)
                elif selected.intersection(queue):
                    queue.sort(key=lambda run_id: run_id not in selected)
                else:
                    continue
                conn.executemany('INSERT OR REPLACE INTO generation_queue_order(run_id,position) VALUES (?,?)',
                                 [(run_id,position) for position,run_id in enumerate(queue)])
        return {'ok':True}

    def listing(self,purpose='production'):
        if purpose not in {'production','experiment','all'}:raise ValueError('Unknown run purpose')
        tasks=self.controllers();by_batch={}
        for task in tasks:
            if task.get('batch_id'):by_batch.setdefault(task['batch_id'],[]).append(task)
        scope="(?='all' OR COALESCE(json_extract(policy_json,'$.purpose'),'production')=?)"
        batches=self.g.review.db.all("SELECT id,created_at,status,selection_json,policy_json FROM generation_batches WHERE "+scope+" AND (status NOT IN ('succeeded','failed','cancelled') OR id IN (SELECT id FROM generation_batches WHERE "+scope+" ORDER BY created_at DESC LIMIT 100)) ORDER BY created_at DESC",(purpose,purpose,purpose,purpose))
        result=[]
        now = datetime.now(timezone.utc)
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
            available=[{'id':e['id'],'connection':e['connection']} for e in self.g.workers.entries() if e['enabled'] and e['id'] not in known and (not e.get('runpod') or e['runpod'].get('state') in LIVE_POD_STATES)] if counts['prepared'] else []
            automatic=policy.get('automatic_workers',selection.get('worker_id') in {'auto','all'} or selection.get('worker_id') is None and any(t.get('worker_id') for t in workers))
            result.append({**batch,'purpose':policy.get('purpose','production'),'experiment_label':policy.get('experiment_label',''),
                'status':status,'section':section,'counts':dict(counts),'total':sum(counts.values()),'available_workers':available,'automatic_workers':automatic,
                'worker_notices':[{'worker_id':wid,'message':message} for (bid,wid),message in list(self.g.dispatcher.notices.items()) if bid==batch['id']],
                'workers':[{**{k:t.get(k) for k in ('id','worker_id','connection','status','active','run_id','stage','step','steps','message','updated_at','error','report','cleanup_pending')},
                    'activity':run_activity('running',t['connection']['backend'],t,now=now) if t['active'] and t.get('attempt_id') else None,
                    'current_title':next((r['title'] for r in t.get('runs',[]) if r['id']==t.get('run_id')),None)} for t in workers]})
            if policy.get('purpose')=='experiment' and getattr(self.g,'experiment_publisher',None):
                result[-1]['publications']=self.g.experiment_publisher.listing(batch['id'])
        return {'jobs':result,'history_limit':100}

    def batch(self,batch_id):
        if not re.fullmatch('[0-9a-f]{32}',batch_id):raise ValueError('Invalid batch ID')
        path=self.g.runtime/'batches'/batch_id/'manifest.json'
        if not path.is_file():
            path=next((Path(t['manifest']) for t in self.g.tasks(False) if t.get('batch_id')==batch_id),path)
        manifest=verify_plan(path)
        if manifest['batch_id']!=batch_id:raise ValueError('Batch identity changed')
        return path,manifest

    def details(self,batch_id,offset=0,limit=100,sort='created'):
        if offset<0 or not 1<=limit<=200:raise ValueError('Invalid page')
        ordering={'created':'rowid','steps':"CAST(json_extract(effective_json,'$.sampler_steps') AS INTEGER),rowid",
            'time':"(SELECT s.duration_seconds FROM generation_attempts a JOIN generation_timing_spans s ON s.attempt_id=a.id WHERE a.run_id=generation_runs.id AND s.stage='total' AND s.parent_id IS NULL ORDER BY a.rowid DESC LIMIT 1) IS NULL,(SELECT s.duration_seconds FROM generation_attempts a JOIN generation_timing_spans s ON s.attempt_id=a.id WHERE a.run_id=generation_runs.id AND s.stage='total' AND s.parent_id IS NULL ORDER BY a.rowid DESC LIMIT 1),rowid"}
        if sort not in ordering:raise ValueError('Unknown configuration order')
        path,manifest=self.batch(batch_id);frozen={r['id']:r for r in manifest['runs']}
        controllers=[t for t in self.controllers() if t.get('batch_id')==batch_id]
        live={t.get('attempt_id'):t for t in controllers if t['active'] and t.get('attempt_id')}
        rows=self.g.review.db.all('SELECT id,title,song_id,variant_id,status,effective_json,started_at,completed_at FROM generation_runs WHERE batch_id=? ORDER BY '+ordering[sort]+' LIMIT ? OFFSET ?',(batch_id,limit,offset))
        result=[]
        now = datetime.now(timezone.utc)
        for row in rows:
            parameters=json.loads(row.pop('effective_json'));parameters.pop('caption',None)
            attempts=self.g.review.db.all('SELECT id,status,error,started_at,execution_mode FROM generation_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(row['id'],))
            attempt=attempts[0] if attempts else None;worker=None;current=None
            if attempt:
                request=path.parent/'attempts'/attempt['id']/'request.json'
                try:worker=identity(json.loads(request.read_text(encoding="utf-8"))['execution']['diffusion'])
                except (OSError,ValueError,KeyError):pass
                current=live.get(attempt['id'])
                if current and current.get('run_id')!=row['id']:current=None
            activity=run_activity(row['status'],attempt['execution_mode'] if attempt else None,current,now=now)
            if row['status']=='prepared' and controllers and all(t['status']=='waiting_worker' for t in controllers):
                activity['stage']='waiting_worker'
            options=frozen.get(row['id'],{}).get('worker_options') or []
            eligible=set(o['worker_id'] for o in options)
            eligible.update(c.get('worker_id') or identity(c['connection']) for c in controllers if any(r['id']==row['id'] for r in c.get('runs',[])))
            if not worker and not attempt:
                if len(eligible)==1:worker=next(iter(eligible))
            result.append({**row,'parameters':parameters,'attempt':attempt,'worker':worker,**activity,
                'eligible_workers':sorted(eligible), 'can_cancel':row['status'] in {'prepared','running','failed'},
                'error':attempt.get('error') if attempt and row['status']=='failed' else None})
            if manifest.get('purpose')=='experiment':
                from .experiment_outputs import ExperimentOutputs,observations
                result[-1].update(observations=observations(self.g.review.db,row['id']),output=ExperimentOutputs(self.g).output(row['id']))
        return {'runs':result,'offset':offset,'total':len(frozen),'has_more':offset+len(rows)<len(frozen)}

    def performance(self,batch_id):
        """One row per attempt, including failures/deleted outputs and complete span data."""
        from .experiment_outputs import ExperimentOutputs,observations
        path,manifest=self.batch(batch_id)
        result=[]
        for run in self.g.review.db.all('SELECT * FROM generation_runs WHERE batch_id=? ORDER BY rowid',(batch_id,)):
            parameters=json.loads(run['effective_json']);parameters.pop('caption',None)
            output=ExperimentOutputs(self.g).output(run['id']) if manifest.get('purpose')=='experiment' else {}
            for attempt in observations(self.g.review.db,run['id']):
                result.append({'batch_id':batch_id,'run_id':run['id'],'purpose':manifest.get('purpose','production'),
                    'experiment_label':manifest.get('experiment_label',''),'title':run['title'],'song_id':run['song_id'],
                    'variant_id':run['variant_id'],'cluster':run['cluster'],'style':run['style'],
                    'output_state':output.get('state'),**parameters,**attempt})
        return result

    def text(self,batch_id,run_id):
        path,manifest=self.batch(batch_id)
        if run_id not in {r['id'] for r in manifest['runs']}:raise ValueError('Run is not in this batch')
        row=self.g.review.db.one('SELECT effective_json,source_sha256 FROM generation_runs WHERE id=?',(run_id,))
        source=self.g.review.db.one('SELECT raw_bytes FROM source_documents WHERE sha256=?',(row['source_sha256'],))
        return {'caption':json.loads(row['effective_json']).get('caption',''),'lyrics':YamlDocument(source['raw_bytes']).values.get('lyrics','')}

    def cancel_selected(self, run_ids):
        selected = set(run_ids)
        result = {'cancelled':0,'requested':0,'unavailable':len(selected)}
        with closing(open_database(self.g.review.config.database)) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            rows = conn.execute("""
                SELECT r.id,r.batch_id,r.status FROM generation_runs r
                JOIN generation_batches b ON b.id=r.batch_id
                WHERE r.id IN (SELECT value FROM json_each(?))
                  AND r.status IN ('prepared','running','cancel_requested','cancelled')
                  AND COALESCE(json_extract(b.policy_json,'$.purpose'),'production')='production'
                """, (json_text(list(selected)),)).fetchall()
            finished = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            batches = set()
            for row in rows:
                result['unavailable']-=1
                if row['status'] in {'prepared','cancelled'}:
                    result['cancelled']+=1
                    status,completed_at='cancelled',finished
                else:
                    result['requested']+=1
                    status,completed_at='cancel_requested',None
                if status!=row['status']:
                    conn.execute('UPDATE generation_runs SET status=?,completed_at=? WHERE id=?',
                                 (status,completed_at,row['id']))
                    batches.add(row['batch_id'])
            for batch_id in batches:
                update_batch_status(conn,batch_id)
        if result['requested']:
            self.g.dispatcher.wake.set()
        return result

    def cancel(self,batch_id,run_id):
        failed_path=None
        with closing(open_database(self.g.review.config.database)) as conn,conn:
            conn.execute('BEGIN IMMEDIATE')
            row=conn.execute('SELECT status FROM generation_runs WHERE batch_id=? AND id=?',(batch_id,run_id)).fetchone()
            if not row:raise ValueError('Run is not in this batch')
            if row['status'] in {'succeeded','publishing'}:raise ValueError('This recording is already saved or finalising; it cannot be cancelled')
            status='cancel_requested' if row['status'] in {'running','cancel_requested'} else 'cancelled'
            if status!=row['status']:
                if row['status']=='failed':
                    failed_path,manifest=self.batch(batch_id)
                    if not any(run['id']==run_id for run in manifest['runs']):
                        raise ValueError('Run is not in this batch')
                now=datetime.now(timezone.utc).isoformat(timespec='milliseconds')
                conn.execute('UPDATE generation_runs SET status=?,completed_at=? WHERE id=?',(status,None if status=='cancel_requested' else now,run_id))
                update_batch_status(conn,batch_id)
        if status=='cancel_requested':
            self.g.dispatcher.wake.set()
        if failed_path:
            from .cancellation import discard
            attempt=self.g.review.db.one('SELECT id FROM generation_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1',(run_id,))
            if attempt:
                directory=failed_path.parent/'attempts'/attempt['id']
                if (directory/'request.json').is_file() and discard(directory,json.loads((directory/'request.json').read_text(encoding="utf-8"))):
                    (directory/'cleanup-pending.json').write_text(json.dumps({'cancelled':True,'error':'Remote cancellation output cleanup may be retried'}), encoding="utf-8")
        return {'run_id':run_id,'status':status}
