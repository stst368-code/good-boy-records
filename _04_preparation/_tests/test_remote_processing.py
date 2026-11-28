"""A second machine can align while diffusion owns the main PC's GPU."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import sys
import threading
import time
import zipfile
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from test_preparation import prepared,promote,ROOT
from _gbr_preparation.service import canonical,digest


def worker(number=1):
    return {'id':str(number)*32,'name':'Other PC','device':'auto','available':True}


def bundle(prep,gid,task,folder):
    recipe=task['recipe'];words=recipe['words']
    for index,word in enumerate(words):word.update(start=index*.5+.1,end=index*.5+.4,confidence=.9)
    lyrics=folder/'lyrics.json';lyrics.write_text(canonical({'source_sha256':task['source_sha256'],'language':recipe['language'],'words':words,'approved':False}))
    outputs=[{'kind':'lyric_alignment','file':'output-0.json','sha256':digest(lyrics)}]
    files={'output-0.json':lyrics.read_bytes()}
    for index,kind in enumerate(['stem_vocals','stem_accompaniment'],1):
        path=folder/(kind+'.wav');sf.write(path,np.zeros((16000*4,2)),16000,subtype='FLOAT')
        outputs.append({'kind':kind,'file':f'output-{index}.wav','sha256':digest(path)});files[f'output-{index}.wav']=path.read_bytes()
    path=folder/'result.zip'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('result.json',canonical({'outputs':outputs,'software':{'whisperx':'test'},'hardware':{'gpu_name':'GTX 1060'},'measurements':{'matched_words':len(words),'total_words':len(words)}}))
        for name,raw in files.items():archive.writestr(name,raw)
    return path


def test_remote_claim_and_verified_stems_complete_while_local_gpu_is_busy(prepared,monkeypatch,tmp_path):
    _,prep,_,_,gid,_=prepared;source=promote(prepared)
    monkeypatch.setattr(prep,'local_generation_busy',lambda:True)
    token=prep.remote.pair()
    assert prep.remote.authorized('Bearer '+token)
    assert not prep.remote.authorized('Bearer incorrect')
    assert token not in prep.db.one("SELECT value_json FROM platform_settings WHERE key='processing_worker_pair'")['value_json']
    task=prep.remote.claim(worker())['task'];assert task
    assert prep.remote.claim(worker(2))['task'] is None
    assert prep.remote.source(task['id'],task['lease'])==source
    prep.remote.progress(task['id'],task['lease'],{'phase':'alignment','message':'Matching words','device':'cuda'})
    assert prep.processing.status()['running']==1
    prep.remote.complete(task['id'],task['lease'],bundle(prep,gid,task,tmp_path))
    editorial=prep.editorial(gid)
    assert editorial['timed_words']==4 and editorial['words'][0]['start']==.1
    assert set(prep.stems(gid,task['id'],digest(source)))=={'vocals','accompaniment'}
    assert prep.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='done'
    with pytest.raises(ValueError,match='no longer active'):prep.remote.progress(task['id'],task['lease'],{})


def test_remote_leases_expire_without_an_old_result_overwriting_recovery(prepared,tmp_path):
    _,prep,_,_,gid,_=prepared;promote(prepared);task=prep.remote.claim(worker())['task']
    row=prep.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(task['id'],))
    progress=json.loads(row['telemetry_json']);progress['remote']['seen']=time.time()-121
    prep.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical(progress),task['id']))
    prep.processing.recover()
    assert prep.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='interrupted'
    with pytest.raises(ValueError,match='no longer active'):prep.remote.complete(task['id'],task['lease'],bundle(prep,gid,task,tmp_path))


def test_offline_workers_disappear_and_pair_revocation_rejects_the_key(prepared):
    _,prep,_,_,_,_=prepared;token=prep.remote.pair();prep.remote.heartbeat(worker())
    assert len(prep.remote.online())==1
    prep.remote.workers[worker()['id']]['seen']=time.time()-21
    assert not prep.remote.online()
    prep.remote.revoke();assert not prep.remote.authorized('Bearer '+token)


def test_worker_routes_require_pairing_and_do_not_unlock_browser_apis(prepared):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    from _review.app import create_app
    app=create_app(review.config.path,start_worker=False,database_path=review.db.path)
    app.state.preparation.tracks=prep.tracks
    app.state.service.config.values['password_hash']='browser-sign-in-required'
    pair=app.state.preparation.remote.pair()
    headers={'X-GBR-Request':'1','Authorization':'Bearer '+pair}
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/processing-worker/claim',json=worker(),headers={'X-GBR-Request':'1'}).status_code==401
        response=client.post('/api/processing-worker/claim',json=worker(),headers=headers)
        assert response.status_code==200,response.text
        task=response.json()['task'];assert task
        assert client.get('/api/processing-worker/tasks/'+task['id']+'/source',headers=headers).status_code==400
        assert client.get('/api/processing-worker/tasks/'+task['id']+'/source',headers={**headers,'X-GBR-Lease':task['lease']}).status_code==200
        assert client.get('/api/preparation/projects',headers=headers).status_code==401
        assert client.post('/api/preparation/processing/pair',json={},headers=headers).status_code==401


def test_queue_scheduler_is_independent_of_a_blocked_review_scan(prepared,monkeypatch):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    entered=threading.Event();release=threading.Event();admitted=threading.Event()
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:False)
    monkeypatch.setattr(prep.pool,'submit',lambda *args:admitted.set())
    def scan():entered.set();release.wait(5)
    monkeypatch.setattr(review.indexer,'scan',scan)
    try:
        review.start();assert entered.wait(2)
        assert admitted.wait(2),'A review scan prevented the independent alignment scheduler from running'
    finally:release.set();review.close()


def test_cpu_alignment_does_not_wait_for_the_main_gpu(prepared,monkeypatch):
    _,prep,_,_,gid,_=prepared;promote(prepared)
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:True)
    submitted=[];monkeypatch.setattr(prep.pool,'submit',lambda fn,identifier:submitted.append(identifier))
    task=prep.submit(gid,'lyric_alignment',{'language':'en','device':'cpu'})
    assert task['status']=='queued' and submitted==[task['id']]


def test_legacy_render_without_a_controller_cannot_block_alignment_forever(prepared,monkeypatch):
    _,prep,_,_,gid,_=prepared;source=promote(prepared)
    legacy=prep._insert(gid,'mix_preview',{'version':1},digest(source),'done')
    prep.db.execute("UPDATE processing_tasks SET status='running',telemetry_json='{}' WHERE id=?",(legacy,))
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:False)
    submitted=[];monkeypatch.setattr(prep.pool,'submit',lambda fn,identifier:submitted.append(identifier))
    prep.processing.resume()
    assert submitted
    assert prep.db.one('SELECT status FROM processing_tasks WHERE id=?',(legacy,))['status']=='interrupted'


@pytest.mark.parametrize('reuse',[False,True])
def test_network_client_streams_audio_and_returns_an_actual_subprocess_result(prepared,monkeypatch,tmp_path,reuse):
    import socket
    import uvicorn
    from fastapi import FastAPI
    from _gbr_preparation.worker_api import worker_router
    _,prep,_,_,gid,_=prepared;promote(prepared)
    spec=importlib.util.spec_from_file_location('network_preparation_client',ROOT/'_99_tools/_workers_processing/network_worker.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    remote=tmp_path/'worker';remote.mkdir()
    (remote/'alignment.py').write_text('''import json,sys,time,os\nfrom pathlib import Path\nr=json.loads(sys.stdin.buffer.read())\nif os.environ.get('GBR_PROCESSING_DEVICE')=='auto':\n print('RuntimeError: CUDA out of memory',file=sys.stderr);sys.exit(1)\nPath(r['progress_file']).write_text(json.dumps({'phase':'alignment','message':'Aligning fixture words','device':'cpu'}))\nprint('Processing Unicode lyrics — песня',file=sys.stderr,flush=True)\ntime.sleep(.4)\nwords=r['recipe']['words']\nfor i,w in enumerate(words):w.update(start=i*.5+.1,end=i*.5+.4,confidence=.9)\np=Path(r['output']).with_suffix('.json')\np.write_text(json.dumps({'source_sha256':r['source_sha256'],'language':r['recipe']['language'],'words':words,'approved':False}))\nprint(json.dumps({'output':str(p),'artifact_kind':'lyric_alignment','software':{'whisperx':'fixture'},'measurements':{'matched_words':len(words),'total_words':len(words)}}))\n''',encoding='utf-8')
    monkeypatch.setattr(module,'ROOT',remote)
    script=(remote/'alignment.py').read_text()
    script=script.replace("Path(r['progress_file'])","if r['recipe'].get('refine'):\n assert Path(r['vocal_source']).is_file()\nPath(r['progress_file'])")
    script=script.replace("'matched_words':len(words)","'reused_vocals':bool(r.get('vocal_source')),'matched_words':len(words)")
    (remote/'alignment.py').write_text(script,encoding='utf-8')
    app=FastAPI();app.include_router(worker_router(prep))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    deadline=time.monotonic()+8
    while not server.started and time.monotonic()<deadline:time.sleep(.02)
    assert server.started
    try:
        client=module.Client('http://127.0.0.1:'+str(port),prep.remote.pair())
        if reuse:
            original=prep.remote.claim(worker(2))['task']
            prep.remote.complete(original['id'],original['lease'],bundle(prep,gid,original,tmp_path))
            saved=prep.song_projects.get(gid);saved['project']['words'][0]['confirmed']=True
            saved=prep.song_projects.save(gid,{'parent_revision_id':None,'source_sha256':saved['source_sha256'],'project':saved['project']})
            prep.submit(gid,'lyric_alignment',{'song_project_id':saved['revision_id'],'refine':True,'force':True})
        task=client.json('/claim',worker())['task'];assert task
        if reuse:assert task['vocal_sha256']==prep.stems(gid,original['id'],task['source_sha256'])['vocals']['sha256']
        module.execute(client,task,'auto',python=sys.executable)
        assert prep.editorial(gid)['timed_words']==4
        progress=json.loads(prep.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(task['id'],))['telemetry_json'])
        assert progress['measurements']['fallback']=='CPU after GPU runtime error'
        assert progress['measurements']['reused_vocals']==reuse
        if reuse:assert prep.stems(gid,task['id'],task['source_sha256'])==prep.stems(gid,original['id'],task['source_sha256'])
        assert prep.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='done'
        assert not list(prep.tracks.rglob('.remote-result-*'))
    finally:server.should_exit=True;thread.join(timeout=5)


def test_worker_does_not_claim_two_tasks_and_rejects_changed_result_bytes(prepared,tmp_path):
    _,prep,_,_,gid,_=prepared;promote(prepared);task=prep.remote.claim(worker())['task']
    prep._insert(gid,'lyric_alignment',prep.lyric_recipe(gid,{'version':1,'language':'de'}),task['source_sha256'],'waiting')
    assert prep.remote.claim(worker())['task'] is None
    valid=bundle(prep,gid,task,tmp_path);bad=tmp_path/'invalid.zip'
    with zipfile.ZipFile(valid) as incoming,zipfile.ZipFile(bad,'w') as outgoing:
        for name in incoming.namelist():outgoing.writestr(name,incoming.read(name)+(b'changed' if name=='output-0.json' else b''))
    with pytest.raises(ValueError,match='checksum'):prep.remote.complete(task['id'],task['lease'],bad)
    assert not prep.db.one("SELECT 1 FROM generation_artifacts WHERE json_extract(metadata_json,'$.processing_task_id')=?",(task['id'],))


def test_missing_source_row_does_not_keep_waiting_forever(prepared):
    _,prep,_,_,gid,_=prepared;source=promote(prepared);source.unlink()
    assert prep.remote.claim(worker())['task'] is None
    row=prep.db.one("SELECT status,error FROM processing_tasks WHERE kind='lyric_alignment'")
    assert row['status']=='failed' and row['error']


def test_pascal_compute_and_unsupported_cuda_fallback():
    from types import SimpleNamespace
    spec=importlib.util.spec_from_file_location('alignment_devices',ROOT/'_99_tools/_workers_processing/alignment.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    torch=SimpleNamespace(cuda=SimpleNamespace(get_device_capability=lambda:(6,1),get_device_properties=lambda index:SimpleNamespace(total_memory=6*1024**3),is_available=lambda:True))
    ct2=SimpleNamespace(get_supported_compute_types=lambda device:{'float32','int8_float32'})
    assert module.asr_compute(torch,'cuda',ct2)=='int8_float32'
    def missing_kernel(*args,**kwargs):raise RuntimeError('no kernel image is available')
    torch.zeros=missing_kernel
    assert module.processing_device(torch,'auto')=='cpu'
    with pytest.raises(RuntimeError,match='cu126'):module.processing_device(torch,'cuda')
