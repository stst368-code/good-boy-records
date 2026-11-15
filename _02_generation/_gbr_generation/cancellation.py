"""Verify explicitly cancelled temporary output before deleting or acknowledging it."""
import json
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import re
import time
from urllib.parse import urlsplit
from _gbr_worker.contract import file_sha
from .planner import write_new,json_text,open_database


def discard(directory,job):
    directory=Path(directory);audio=directory/'audio.flac';result=directory/'result.json'
    if not audio.is_file():return False
    if directory.name!=job['attempt_id']:raise ValueError('Cancelled attempt directory changed')
    if result.is_file():
        saved=json.loads(result.read_text(encoding="utf-8"))
        if saved.get('run_id')!=job['run_id'] or saved.get('attempt_id')!=job['attempt_id'] or saved.get('status') not in {'succeeded','failed'}:
            raise ValueError('Cancelled worker output does not match its attempt; retained for inspection')
    receipt=directory/'cancelled-output.json'
    evidence={k:file_sha(directory/k) if (directory/k).is_file() else None for k in ('request.json','result.json','audio.flac')}
    if receipt.is_file():
        if json.loads(receipt.read_text(encoding="utf-8"))!=evidence:raise ValueError('Cancelled output checksums changed')
    else:write_new(receipt,json_text(evidence).encode())
    audio.unlink()
    return True


def verify(directory):
    directory=Path(directory);receipt=json.loads((directory/'cancelled-output.json').read_text(encoding="utf-8"))
    for name in ('request.json','result.json'):
        if receipt[name] and file_sha(directory/name)!=receipt[name]:raise ValueError('Cancelled output receipt changed; retained for inspection')
    audio=directory/'audio.flac'
    if audio.is_file():
        if file_sha(audio)!=receipt['audio.flac']:raise ValueError('Cancelled temporary audio changed')
        audio.unlink()


def update_batch_status(conn, batch_id):
    return conn.execute("""
        UPDATE generation_batches SET status=(
            SELECT CASE
                WHEN MIN(status='succeeded')=1 THEN 'succeeded'
                WHEN MAX(status IN ('running','publishing','cancel_requested'))=1 THEN 'running'
                WHEN MAX(status='failed')=1 THEN 'failed'
                WHEN MAX(status='prepared')=1 THEN 'prepared'
                ELSE 'cancelled'
            END FROM generation_runs WHERE batch_id=?
        ) WHERE id=? RETURNING status
        """, (batch_id,batch_id)).fetchone()[0]


def saved_json(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


class CancellationRecovery:
    """Close confirmed cancellations when a detached controller can no longer do it."""
    def __init__(self, generation):
        self.generation = generation
        self.notices = {}
        self.retry_at = {}

    def reconcile(self, controllers=None):
        database = self.generation.review.config.database
        with closing(open_database(database)) as conn:
            if not conn.execute("SELECT 1 FROM generation_runs WHERE status='cancel_requested' LIMIT 1").fetchone():
                self.notices.clear()
                self.retry_at.clear()
                return
            rows = [dict(row) for row in conn.execute("""
                WITH published AS (SELECT DISTINCT run_id FROM generation_artifacts WHERE run_id IS NOT NULL)
                SELECT r.id,r.batch_id,a.id AS attempt_id,a.status AS attempt_status,a.execution_mode,a.started_at,
                       e.output_directory,
                       (SELECT COUNT(*) FROM generation_attempts older
                        WHERE older.run_id=r.id AND older.status='running' AND older.id<>a.id) AS other_running
                FROM generation_runs r
                LEFT JOIN generation_attempts a ON a.id=(
                    SELECT id FROM generation_attempts WHERE run_id=r.id ORDER BY rowid DESC LIMIT 1
                )
                LEFT JOIN generation_execution_state e ON e.run_id=r.id
                LEFT JOIN published p ON p.run_id=r.id
                WHERE r.status='cancel_requested' AND p.run_id IS NULL AND r.batch_id IS NOT NULL
                """)]
        from _gbr_worker.contract import execution_target
        import psutil

        if controllers is None:
            controllers = self.generation.tasks(False,live_only=True)
        live = {(t['batch_id'], t['connection']['backend'], t['connection'].get('address') or '')
                for t in controllers if t.get('batch_id') and t['active']}
        live_attempts = {t.get('attempt_id') for t in controllers if t['active']}
        worker_queries = {}
        resolved = []
        notices = {}
        terminated_pods = None
        for row in rows:
            attempt_id, run_id = row['attempt_id'], row['id']
            if row['other_running'] or row['attempt_status']=='succeeded':
                notices[run_id] = 'Check the attempt history in Job controls.'
                continue
            if row['output_directory'] and Path(row['output_directory']).exists():
                notices[run_id] = 'A saved output is present. Check the job before retrying.'
                continue
            if not attempt_id or row['attempt_status'] in {'cancelled','failed','interrupted'}:
                resolved.append(row)
                continue
            if attempt_id in live_attempts:
                continue
            directory = self.generation.runtime/'batches'/row['batch_id']/'attempts'/attempt_id
            job = saved_json(directory/'request.json')
            try:
                started = datetime.fromisoformat(row['started_at']).timestamp()
            except (TypeError,ValueError,OSError,OverflowError):
                started = None
            if row['execution_mode']=='local':
                try:
                    rebooted = started is not None and started < psutil.boot_time()
                except OSError:
                    rebooted = False
                result = saved_json(directory/'result.json')
                ended = result.get('attempt_id')==attempt_id and result.get('run_id')==run_id and result.get('status') in {'failed','succeeded'}
                if (rebooted or ended) and (row['batch_id'],'local','') not in live:
                    resolved.append(row)
                else:
                    notices[run_id] = 'The local worker stopped reporting before cancellation was confirmed.'
                continue
            if row['execution_mode']!='comfyui':
                notices[run_id] = 'Reconnect to the original worker in Job controls to confirm cancellation.'
                continue
            if job.get('run_id')!=run_id or job.get('attempt_id')!=attempt_id:
                notices[run_id] = 'The saved worker request is unavailable.'
                continue
            try:
                backend,address = execution_target(job['execution'])
            except (ValueError,KeyError,TypeError):
                notices[run_id] = 'The original worker address is unavailable.'
                continue
            if backend!='comfyui' or (row['batch_id'],backend,address) in live:
                continue
            pod = re.fullmatch(r'([a-z0-9_-]+)-[0-9]+\.proxy\.runpod\.net',urlsplit(address).hostname or '')
            if pod:
                if terminated_pods is None:
                    terminated_pods = {meta['pod_id']:meta['last_discovery']
                                       for entry in self.generation.workers.entries()
                                       if (meta := entry.get('runpod')) and meta.get('state')=='TERMINATED'
                                       and isinstance(meta.get('last_discovery'),(int,float))}
                observed = terminated_pods.get(pod[1])
                if observed and started is not None and observed >= started:
                    resolved.append(row)
                    continue
            saved = saved_json(directory/'comfy.json')
            prompt_id = saved.get('prompt_id')
            if saved.get('attempt_id')!=attempt_id or not isinstance(prompt_id,str) or not prompt_id:
                notices[run_id] = 'The saved ComfyUI prompt ID is unavailable.'
                continue
            worker_queries.setdefault(address,[]).append((row,prompt_id))

        self.finish(database,resolved)
        self.retry_at = {address:deadline for address,deadline in self.retry_at.items() if address in worker_queries}
        if worker_queries:
            for requests in worker_queries.values():
                notices.update((row['id'],self.notices[row['id']]) for row,_ in requests if row['id'] in self.notices)
            # Bound the work per dispatcher tick even when many pods are offline.
            address = min(worker_queries,key=lambda item:self.retry_at.get(item,0))
            if time.monotonic() >= self.retry_at.get(address,0):
                resolved = []
                requests = worker_queries[address]
                try:
                    self.check_comfy(address,requests,notices,resolved)
                    self.retry_at[address] = time.monotonic()
                except (OSError,ValueError,RuntimeError):
                    self.retry_at[address] = time.monotonic()+45
                    for row,_ in requests:
                        notices[row['id']] = 'Could not confirm cancellation with the original ComfyUI worker.'
                self.finish(database,resolved)
        self.notices = notices

    @staticmethod
    def finish(database, resolved):
        if resolved:
            confirmed = datetime.now(timezone.utc).isoformat(timespec='milliseconds')
            with closing(open_database(database)) as conn, conn:
                conn.execute('BEGIN IMMEDIATE')
                batches = set()
                published = {row[0] for row in conn.execute("""
                    SELECT DISTINCT a.run_id FROM generation_artifacts a
                    JOIN generation_runs r ON r.id=a.run_id WHERE r.status='cancel_requested'
                    """)}
                for row in resolved:
                    if row['id'] in published or row['output_directory'] and Path(row['output_directory']).exists():
                        continue
                    updated = conn.execute("""
                        UPDATE generation_runs SET status='cancelled',completed_at=?
                        WHERE id=? AND status='cancel_requested' AND
                          (SELECT id FROM generation_attempts WHERE run_id=? ORDER BY rowid DESC LIMIT 1) IS ?
                        """, (confirmed,row['id'],row['id'],row['attempt_id']))
                    if updated.rowcount:
                        batches.add(row['batch_id'])
                        conn.execute("""UPDATE generation_attempts SET status='cancelled',completed_at=?,error=?
                                        WHERE id=? AND status='running'""",
                                     (confirmed,'Cancellation confirmed after controller stopped',row['attempt_id']))
                for batch_id in batches:
                    update_batch_status(conn,batch_id)

    @staticmethod
    def check_comfy(address, requests, notices, resolved):
        from .executor import ComfyClient
        client = ComfyClient(address)
        queue = client.prompt_queue(timeout=4)
        running = {row[1]:row for row in queue['queue_running']}
        pending = {row[1]:row for row in queue['queue_pending']}
        owners = {item[3]['gbr_attempt_id'] for item in [*running.values(),*pending.values()]
                  if len(item)>3 and isinstance(item[3],dict) and isinstance(item[3].get('gbr_attempt_id'),str)}
        remove = []
        for row,prompt_id in requests:
            item = running.get(prompt_id) or pending.get(prompt_id)
            run_id,attempt_id = row['id'],row['attempt_id']
            if not item and attempt_id not in owners:
                resolved.append(row)
            elif not item or len(item)<4 or not isinstance(item[3],dict) or item[3].get('gbr_attempt_id')!=attempt_id:
                notices[run_id] = 'ComfyUI has a prompt whose ownership could not be confirmed.'
            elif prompt_id in pending:
                remove.append(prompt_id)
                notices[run_id] = 'Cancellation sent to the ComfyUI queue.'
            else:
                sent,supported = client.cancel_prompt(prompt_id,timeout=4)
                notices[run_id] = ('Cancellation sent to ComfyUI.' if sent else
                                   'Waiting for the prompt to finish.' if not supported else
                                   'Waiting for ComfyUI to confirm cancellation.')
        if remove:
            client.delete_pending_prompts(remove,timeout=4)
