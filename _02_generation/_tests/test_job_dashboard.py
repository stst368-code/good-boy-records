"""Real shared DB/HTTP run controls, including Windows rename contention."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest
import test_app_generation
import test_execution
from _gbr_generation.jobs import Jobs
from _gbr_generation.executor import execute
from _gbr_generation.planner import open_database, verify_plan
from _gbr_generation.job_state import atomic_state,read_state
from _gbr_generation import app_jobs
from test_comfy_execution import server


@pytest.fixture
def setup():
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    try:yield f,Jobs(f.generation)
    finally:f.doCleanups()


def batch(f,repetitions=3):
    p=f.preview(experiments={'repetitions':repetitions});f.generation.start(p['token'])
    path=Path(f.launched[-1]['manifest']);return path,verify_plan(path)


def boundary():
    b=test_execution.ExecutionTests();b.calls=[];return b


def test_cancel_specific_configurations_never_dispatches_them_and_separates_history(setup):
    f,jobs=setup;path,m=batch(f,10);cancelled={r['id'] for r in m['runs'][::2]}
    for run in cancelled:
        response=f.client.post(f'/api/generation/jobs/{m["batch_id"]}/runs/{run}/cancel',headers=f.headers,json={})
        assert response.status_code==200 and response.json()['status']=='cancelled'
    worker=boundary();result=execute(path,worker_python=sys.executable,launcher=worker.worker,limit=20)
    assert len(worker.calls)==5 and not cancelled & {r['run_id'] for r in worker.calls}
    listed=jobs.listing()['jobs'][0];assert listed['section']=='failed' and listed['counts']=={'succeeded':5,'cancelled':5}
    assert f.source.read_bytes()==f.original
    assert len(list(f.generation.generated.rglob('*.flac')))==5
    second,other=batch(f,1);execute(second,worker_python=sys.executable,launcher=worker.worker)
    third,pending=batch(f,1)
    groups={r['id']:r['section'] for r in jobs.listing()['jobs']}
    assert groups[other['batch_id']]=='completed' and groups[pending['batch_id']]=='active'
    assert not execute(path,worker_python=sys.executable,launcher=worker.worker,retry_failed=True)['attempts']


def test_expanded_rows_have_configurations_and_snapshot_text_loaded_separately(setup):
    f,jobs=setup;path,m=batch(f,2);run=m['runs'][0]
    details=jobs.details(m['batch_id'],limit=1);assert details['has_more'] and len(details['runs'])==1
    assert 'caption' not in details['runs'][0]['parameters'] and details['runs'][0]['can_cancel']
    original=jobs.text(m['batch_id'],run['id']);assert original['caption'] and '[chorus]' in original['lyrics']
    f.source.write_text('lyrics: changed\n')
    assert jobs.text(m['batch_id'],run['id'])==original
    assert f.client.post(f'/api/generation/jobs/{m["batch_id"]}/runs/{run["id"]}/cancel',json={}).status_code==403
    with pytest.raises(ValueError):jobs.cancel(m['batch_id'],'f'*32)


def test_cancellation_between_worker_finish_and_publication_discards_only_that_output(setup):
    f,jobs=setup;path,m=batch(f,2);worker=boundary()
    def cancel_one(*args):
        worker.worker(*args)
        if len(worker.calls)==1:assert jobs.cancel(m['batch_id'],m['runs'][0]['id'])['status']=='cancel_requested'
    result=execute(path,worker_python=sys.executable,launcher=cancel_one,limit=5)
    assert [r['status'] for r in result['attempts']]==['cancelled','succeeded']
    assert len(list(f.generation.generated.rglob('*.flac')))==1
    assert not list(path.parent.glob('attempts/*/audio.flac'))
    with pytest.raises(ValueError,match='already saved'):jobs.cancel(m['batch_id'],m['runs'][1]['id'])


def test_running_native_cancellation_stops_only_its_own_child(setup):
    f,jobs=setup;path,m=batch(f,1);children=[];original=subprocess.Popen;results=[]
    unrelated=original([sys.executable,'-c','import time;time.sleep(30)'])
    def child(*args,**kwargs):
        process=original([sys.executable,'-u','-c','import time;print("[now] stage_start diffusion",flush=True);time.sleep(30)'],**kwargs);children.append(process);return process
    def work():results.append(execute(path,worker_python=sys.executable,progress=lambda **p:None))
    try:
        with patch('_gbr_generation.executor.subprocess.Popen',side_effect=child):
            thread=threading.Thread(target=work);thread.start()
            deadline=time.monotonic()+5
            while not children and time.monotonic()<deadline:time.sleep(.02)
            assert children
            jobs.cancel(m['batch_id'],m['runs'][0]['id']);thread.join(timeout=8)
            assert not thread.is_alive() and results[0]['attempts'][0]['status']=='cancelled'
            assert children[0].poll() is not None and unrelated.poll() is None
    finally:
        unrelated.terminate();unrelated.wait(timeout=5)
        for child in children:
            if child.poll() is None:child.kill();child.wait(timeout=5)


def test_windows_state_replace_retries_and_persistent_lock_never_fails_generation(tmp_path):
    target=tmp_path/'state.json';target.write_text('{"status":"running"}');replace=Path.replace;attempts=[]
    def locked(source,destination):
        attempts.append(source.name)
        if len(attempts)<4:raise PermissionError(5,'Access is denied')
        return replace(source,destination)
    with patch.object(Path,'replace',locked):assert atomic_state(target,{'status':'waiting_worker'})
    assert len(set(attempts))==1 and attempts[0]!='state.tmp' and read_state(target)['status']=='waiting_worker'
    with patch.object(Path,'replace',side_effect=PermissionError(5,'Access is denied')),patch('time.sleep'):
        assert app_jobs.write_state(tmp_path,'running') is False
    assert read_state(target)['status']=='waiting_worker' and not list(tmp_path.glob('*.tmp'))
    with ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(lambda i:atomic_state(target,{'status':str(i)}),range(20)))
    assert read_state(target)['status'].isdigit() and not list(tmp_path.glob('*.tmp'))


def test_busy_local_controller_survives_locked_status_file_and_runs_after_release(setup):
    f,jobs=setup;path,m=batch(f,1);worker=boundary();folder=f.generation.runtime/'app_jobs'/('e'*32);folder.mkdir(parents=True)
    task={**f.launched[-1],'connection':{**f.launched[-1]['connection'],'worker_python':sys.executable}}
    (folder/'task.json').write_text(json.dumps(task));(folder/'state.json').write_text('{"status":"waiting_worker"}')
    replace=Path.replace;waiting=threading.Event();results=[]
    def locked(source,destination):
        if Path(destination).name=='state.json':waiting.set();raise PermissionError(5,'Access is denied')
        return replace(source,destination)
    def dispatch(manifest,**options):return execute(manifest,launcher=worker.worker,**options)
    with patch.object(Path,'replace',locked),patch.object(app_jobs,'prepare_worker',return_value={}),patch.object(app_jobs,'execute',side_effect=dispatch):
        with app_jobs.gpu_lock('gbr-controller:local:'):
            thread=threading.Thread(target=lambda:results.append(app_jobs.run_task(folder/'task.json')));thread.start()
            assert waiting.wait(timeout=5) and not worker.calls
        thread.join(timeout=15)
    assert not thread.is_alive() and results==[0] and len(worker.calls)==1
    assert not list(folder.glob('*.tmp')) and f.source.read_bytes()==f.original


@pytest.mark.parametrize('targeted,pending,completed_output',[(True,False,False),(False,True,False),(False,False,False),(True,False,True)])
def test_comfy_cancellation_is_id_scoped_and_old_servers_discard_safely(setup,targeted,pending,completed_output):
    f,jobs=setup;gate=threading.Event();events=[];results=[]
    with server(holding=gate,targeted_cancel=targeted,pending=pending,busy=True,cancel_output=completed_output) as (address,state):
        p=f.preview(connection={'backend':'comfyui','address':address},parameters={'dit':'fp32','text_encoder':'bf16','sampler':'euler','scheduler':'simple'},parameter_priority='form')
        assert p['ready']==1,p
        f.generation.start(p['token']);path=Path(f.launched[-1]['manifest']);m=verify_plan(path)
        thread=threading.Thread(target=lambda:results.append(execute(path,progress=lambda **p:events.append(p))));thread.start()
        deadline=time.monotonic()+6
        while not state['submissions'] and time.monotonic()<deadline:time.sleep(.02)
        assert state['submissions']==1;jobs.cancel(m['batch_id'],m['runs'][0]['id'])
        try:
            if not targeted and not pending:
                deadline=time.monotonic()+5
                while not any(e['stage']=='cancel_after_generation' for e in events) and time.monotonic()<deadline:time.sleep(.02)
                assert any(e['stage']=='cancel_after_generation' for e in events) and thread.is_alive()
                gate.set()
            thread.join(timeout=8)
            assert not thread.is_alive() and results[0]['attempts'][0]['status']=='cancelled',results
            assert state['submissions']==1 and not list(f.generation.generated.rglob('*.flac'))
            assert not list(path.parent.glob('attempts/*/audio.flac'))
            assert all(url!='/interrupt' for method,url in state['requests'])
            if completed_output or not targeted and not pending:assert state['acked']
        finally:gate.set();thread.join(timeout=8)


def test_flat_queue_promotes_next_worker_claim_and_separates_cancelled_runs(setup):
    f, jobs = setup
    path, manifest = batch(f)
    first, middle, last = [r['id'] for r in manifest['runs']]
    jobs.cancel(manifest['batch_id'], middle)
    response = f.client.post('/api/generation/queue/order', headers=f.headers,
                             json={'action': 'promote', 'run_ids': [last]})
    assert response.status_code == 200
    queued = f.client.get('/api/generation/queue', headers=f.headers).json()
    assert [r['id'] for r in queued['runs']] == [last, first]
    assert queued['counts']['cancelled'] == 1
    worker = boundary()
    execute(path, worker_python=sys.executable, launcher=worker.worker)
    assert [call['run_id'] for call in worker.calls] == [last]
    assert jobs.queue('completed', 0)['runs'][0]['id'] == last
    assert jobs.queue('cancelled', 0)['runs'][0]['id'] == middle


def test_new_batch_is_visible_with_a_thousand_old_runs_and_abandoned_cancellations(setup):
    f,jobs=setup
    _,old=batch(f,1)
    _,recent=batch(f,2)
    recent_ids=[run['id'] for run in recent['runs']]
    with closing(open_database(f.database)) as db, db:
        db.execute("UPDATE generation_batches SET created_at='2000-01-01T00:00:00Z' WHERE id=?",(old['batch_id'],))
        db.execute("UPDATE generation_runs SET title='CofetĂrie Nouă' WHERE id=?",(recent_ids[0],))
        db.execute("""
            WITH RECURSIVE backlog(i) AS (VALUES (1) UNION ALL SELECT i+1 FROM backlog WHERE i<999)
            INSERT INTO generation_runs(id,batch_id,source_sha256,title,status,requested_json,effective_json,created_at)
            SELECT printf('%032x',i),batch_id,source_sha256,'Older track',
                CASE WHEN i<=500 THEN 'cancel_requested' ELSE 'prepared' END, '{}','{}',created_at
            FROM backlog CROSS JOIN generation_runs WHERE id=?
            """,(old['runs'][0]['id'],))
    url='/api/generation/queue'
    queue=f.client.get(url,headers=f.headers).json()
    assert [r['id'] for r in queue['runs'][:2]]==recent_ids
    assert queue['waiting']==queue['counts']['queued']==502 and queue['counts']['attention']==500
    oldest=f.client.get(url,params={'order':'dispatch'},headers=f.headers).json()
    assert oldest['runs'][0]['batch_id']==old['batch_id']
    scoped=f.client.get(url,params={'batch':recent['batch_id']},headers=f.headers).json()
    assert scoped['counts']['attention']==0 and [r['id'] for r in scoped['runs']]==recent_ids
    matched=f.client.get(url,params={'q':'COFETĂRIE'},headers=f.headers).json()
    assert [r['id'] for r in matched['runs']]==recent_ids[:1]
    f.generation.dispatcher.tick()
    queue=jobs.queue('queued',0)
    assert queue['counts']['attention']==0 and queue['counts']['cancelled']==500 and queue['waiting']==502
    cancelled=jobs.queue('cancelled',0)['runs']
    assert len(cancelled)==50 and all(r['completed_at'] for r in cancelled)
    assert not list(f.generation.generated.rglob('*.flac'))


def test_bulk_cancel_is_idempotent_and_preserves_publishing_and_finished_runs(setup):
    f,jobs=setup
    _,manifest=batch(f,5)
    prepared,running,publishing,completed,failed=[run['id'] for run in manifest['runs']]
    with closing(open_database(f.database)) as db, db:
        db.executemany('UPDATE generation_runs SET status=? WHERE id=?',
                       [('running',running),('publishing',publishing),('succeeded',completed),('failed',failed)])
    url='/api/generation/queue/cancel'
    selection={'run_ids':[prepared,prepared,running,publishing,completed,failed,'f'*32]}
    result=f.client.post(url,headers=f.headers,json=selection)
    assert result.status_code==200 and result.json()=={'cancelled':1,'requested':1,'unavailable':4}
    cancelled=jobs.queue('cancelled',0)['runs'][0]
    assert cancelled['id']==prepared and cancelled['completed_at']
    assert f.client.post(url,headers=f.headers,json=selection).json()==result.json()
    assert jobs.queue('cancelled',0)['runs'][0]['completed_at']==cancelled['completed_at']
    with closing(open_database(f.database)) as db:
        assert dict(db.execute('SELECT id,status FROM generation_runs'))=={
            prepared:'cancelled',running:'cancel_requested',publishing:'publishing',completed:'succeeded',failed:'failed',
        }


def test_detached_comfy_cancellations_are_confirmed_only_for_owned_or_ended_prompts(setup):
    f,jobs=setup
    path,manifest=batch(f,5)
    gone,pending,foreign,terminated,preserved=[run['id'] for run in manifest['runs']]
    recovery=f.generation.dispatcher.cancellations
    now=datetime.now(timezone.utc)
    started=(now-timedelta(minutes=10)).isoformat()
    hold=threading.Event()
    with server(holding=hold,pending=True,busy=True) as (address,state):
        stopped='https://stopped-pod-8188.proxy.runpod.net'
        with closing(open_database(f.database)) as db, db:
            for number,(run_id,prompt,worker) in enumerate([
                (gone,'already-gone',address),(pending,'owned-prompt',address),(foreign,'another-job',address),
                (terminated,None,stopped),(preserved,'owned-output',address),
            ],1):
                attempt=f'{number:032x}'
                db.execute("UPDATE generation_runs SET status='cancel_requested',started_at=? WHERE id=?",(started,run_id))
                db.execute('INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
                           (attempt,run_id,None,'full_generation','running','comfyui','{}',started,None,None))
                directory=path.parent/'attempts'/attempt
                directory.mkdir(parents=True)
                target={'backend':'comfyui','address':worker}
                (directory/'request.json').write_text(json.dumps({'run_id':run_id,'attempt_id':attempt,
                    'execution':{'conditioning':target,'diffusion':target}}),encoding='utf-8')
                if prompt:
                    (directory/'comfy.json').write_text(json.dumps({'attempt_id':attempt,'prompt_id':prompt}),encoding='utf-8')
                if run_id==pending:
                    state['attempt']=attempt
                if run_id==preserved:
                    output=f.root/'already-saved'
                    output.mkdir()
                    audio=output/'audio.flac'
                    audio.write_bytes(b'existing recording')
                    db.execute('INSERT INTO generation_execution_state VALUES(?,?,?,?)',(run_id,None,'saved',str(output)))
        worker={'runpod':{'pod_id':'stopped-pod','state':'TERMINATED','last_discovery':now.timestamp()}}
        with patch.object(f.generation.workers,'entries',return_value=[worker]):
            recovery.reconcile([])
        assert {r['id'] for r in jobs.queue('cancelled',0)['runs']}=={gone,terminated}
        assert [request for request in state['requests'] if request[0]=='POST']==[('POST','/queue')]
        assert 'ownership' in recovery.notices[foreign]
        assert 'saved output' in recovery.notices[preserved]
        recovery.reconcile([])
        assert {r['id'] for r in jobs.queue('cancelled',0)['runs']}=={gone,terminated,pending}
        assert audio.read_bytes()==b'existing recording'
        assert {r['id'] for r in jobs.queue('attention',0)['runs']}=={foreign,preserved}
        from _gbr_generation.executor import ComfyClient
        with patch.object(ComfyClient,'prompt_queue',side_effect=OSError('worker offline')):
            recovery.reconcile([])
        assert 'Could not confirm' in recovery.notices[foreign]
        assert {r['id'] for r in jobs.queue('attention',0)['runs']}=={foreign,preserved}


def test_flat_queue_reports_only_fresh_owned_comfy_activity_and_real_times(setup):
    f,jobs=setup
    _,manifest=batch(f,7)
    running,pending,stale,wrong_attempt,finished,waiting,unknown=[run['id'] for run in manifest['runs']]
    batch_id=manifest['batch_id']
    now=datetime.now(timezone.utc)
    dispatched=(now-timedelta(minutes=3)).isoformat()
    started=(now-timedelta(minutes=1)).isoformat()
    attempt_ids={}
    with closing(open_database(f.database)) as db, db:
        for index,run_id in enumerate((running,pending,stale,wrong_attempt,finished,unknown),1):
            attempt_id=f'{index:032x}'
            attempt_ids[run_id]=attempt_id
            db.execute("UPDATE generation_runs SET status='running',started_at=? WHERE id=?",(dispatched,run_id))
            db.execute('INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (attempt_id,run_id,None,'full_generation','running','comfyui','{}',dispatched,None,None))
        db.execute("UPDATE generation_runs SET status='succeeded',completed_at=? WHERE id=?",(now.isoformat(),finished))
        db.execute('INSERT INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)',
                   (attempt_ids[finished]+'-comfy',attempt_ids[finished],None,'comfy_execution',started,
                    now.isoformat(),60,None,None,None,0))
    def state(run_id, stage, **extra):
        return {'run_id':run_id,'attempt_id':attempt_ids[run_id],'stage':stage,'active':True,'batch_id':batch_id,
                'prompt_id':run_id,'updated_at':now.isoformat(),'connection':{'backend':'comfyui','address':'http://127.0.0.1:8190'},**extra}
    snapshots=[
        state(running,'comfy_generating',step=12,steps=30,progress_unit='steps',execution_started_at=started),
        state(pending,'comfy_queued'),
        {**state(stale,'comfy_generating',step=11,steps=30),'updated_at':(now-timedelta(minutes=1)).isoformat()},
        {**state(wrong_attempt,'comfy_generating',step=11,steps=30),'attempt_id':'e'*32},
        state(unknown,'comfy_waiting'),
    ]
    with patch.object(f.generation,'tasks',return_value=snapshots):
        payload=f.client.get('/api/generation/queue',headers=f.headers).json()
        data={row['id']:row for row in payload['runs']}
        assert {run_id:row['phase'] for run_id,row in data.items()}=={
            running:'running',pending:'worker_queue',stale:'awaiting_status',
            waiting:'prepared',unknown:'awaiting_status',
        }
        assert payload['counts']['attention']==1
        attention=jobs.queue('attention',0)['runs']
        assert [(row['id'],row['phase']) for row in attention]==[(wrong_attempt,'reconnect')]
        assert data[running]['step']==12 and data[running]['started_at']==started
        assert data[pending]['started_at'] is None and data[pending]['dispatched_at']==dispatched
        assert all(row.get('step') is None for run_id,row in data.items() if run_id!=running)
        completed=jobs.queue('completed',0)['runs'][0]
        assert completed['started_at']==started and completed['completed_at']==now.isoformat()
