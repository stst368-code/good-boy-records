"""Real shared DB/HTTP run controls, including Windows rename contention."""
from concurrent.futures import ThreadPoolExecutor
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
from _gbr_generation.planner import verify_plan
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
