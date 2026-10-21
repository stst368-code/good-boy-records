"""Late worker admissions use immutable recipes and atomic shared claims."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import threading
import time
from unittest.mock import patch
import pytest
import test_generation_queue as fixtures
from test_comfy_execution import server,inventory
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan
from _gbr_generation.assignments import admission,policy
from client import Client


def different_models():
    nodes=inventory()
    nodes['CLIPLoader']['input']['required']['clip_name'][0]=['minimax_music3_text_encoder_int8.safetensors']
    nodes['UNETLoader']['input']['required']['unet_name'][0]=['minimax_music3_dit_bf16.safetensors']
    return nodes


def test_late_worker_joins_single_worker_batch_without_regeneration_or_manifest_changes():
    with fixtures.app() as f,server() as (a,sa),server(nodes_override=different_models()) as (b,sb):
        wa=fixtures.configured(f,a)
        preview=f.preview(connection=None,worker_id=wa['id'],caption_mode='yaml',parameters={'sampler_cfg':2.1},parameter_sources={'sampler_cfg':'control'},experiments={'repetitions':4,'seed_policy':'randomize'})
        f.generation.start(preview['token']);path=Path(f.launched[0]['manifest']);raw=path.read_bytes();manifest=verify_plan(path)
        assert execute(path)['attempts'][0]['status']=='succeeded'
        wb=fixtures.configured(f,b)
        f.source.write_text('lyrics: changed after launch\n');changed=f.source.read_bytes()
        response=f.client.post(f'/api/generation/jobs/{manifest["batch_id"]}/workers',headers=f.headers,json={'worker_id':wb['id']})
        assert response.status_code==200,response.text
        assert response.json()['eligible']==3 and len(f.launched)==2
        joined=admission(path,manifest,wb['id']);assert manifest['runs'][0]['id'] not in joined
        for run in manifest['runs'][1:]:
            values=joined[run['id']]['effective']
            assert values['text_encoder'].endswith('int8.safetensors') and values['sampler_cfg']==2.1
            assert all(values[k]==run['effective'][k] for k in ('caption','encoder_seed','sampler_seed','cluster','style'))
        gate=threading.Barrier(2);calls=[];lock=threading.Lock();original=Client.run
        def held(client,job,directory,resume=False):
            with lock:first=not any(endpoint==client.address for endpoint,_ in calls);calls.append((client.address,job['run_id']))
            if first:gate.wait(timeout=15)
            return original(client,job,directory,resume)
        def consume(worker):
            while True:
                result=execute(path,worker_id=worker)
                assert all(r['status']=='succeeded' for r in result['attempts']),result
                if not result['attempts']:break
        with patch.object(Client,'run',held),ThreadPoolExecutor(max_workers=2) as pool:list(pool.map(consume,[None,wb['id']]))
        assert len(calls)==len({run for _,run in calls})==3
        assert sa['submissions']+sb['submissions']==4 and {endpoint for endpoint,_ in calls}=={a,b}
        assert path.read_bytes()==raw and f.source.read_bytes()==changed
        assert len(list(f.generation.generated.rglob('*.flac')))==4
        assert not list(path.parent.glob('attempts/*/audio.flac'))
        saved=policy(manifest)['worker_assignments'][wb['id']];assignment=path.parent/saved['file'];original_bytes=assignment.read_bytes();assignment.write_bytes(original_bytes+b' ')
        with pytest.raises(ValueError,match='assignment changed'):execute(path,worker_id=wb['id'])
        assignment.write_bytes(original_bytes)


def test_new_worker_cannot_replace_fixed_model_pair_or_bypass_request_protection():
    with fixtures.app() as f,server() as (a,_),server(nodes_override=different_models()) as (b,_):
        wa=fixtures.configured(f,a);wb=fixtures.configured(f,b)
        preview=f.preview(connection=None,worker_id=wa['id'],caption_mode='yaml',experiments={'groups':[[{'text_encoder':'minimax_music3_text_encoder_bf16.safetensors','dit':'minimax_music3_dit_fp32.safetensors'}]]})
        f.generation.start(preview['token']);path=Path(f.launched[0]['manifest']);manifest=verify_plan(path);url=f'/api/generation/jobs/{manifest["batch_id"]}/workers'
        assert f.client.post(url,json={'worker_id':wb['id']}).status_code==403
        rejected=f.client.post(url,headers=f.headers,json={'worker_id':wb['id']})
        assert rejected.status_code==400 and 'No compatible queued' in rejected.text
        assert not policy(manifest).get('worker_assignments') and len(f.launched)==1
        assert f.app.state.service.db.one('SELECT status FROM generation_runs')['status']=='prepared'


def test_all_available_discovers_new_workers_once_and_respects_manual_disable():
    with fixtures.app() as f,server() as (a,_),server(nodes_override=different_models()) as (b,_),server() as (c,_):
        wa=fixtures.configured(f,a)
        preview=f.preview(connection=None,worker_id='auto',caption_mode='yaml',experiments={'repetitions':3})
        f.generation.start(preview['token']);manifest=verify_plan(f.launched[0]['manifest'])
        def tasks(include_logs=True):
            return [{**task,'id':str(i),'active':True,'status':'running','runs':[]} for i,task in enumerate(f.launched)]
        with patch.object(f.generation,'tasks',side_effect=tasks):
            f.generation.dispatcher.start()
            wb=fixtures.configured(f,b)
            deadline=time.monotonic()+10
            while len(f.launched)<2 and time.monotonic()<deadline:time.sleep(.05)
            f.generation.dispatcher.close();f.generation.dispatcher.stop.clear()
            f.generation.dispatcher.tick()
            assert len(f.launched)==2 and f.launched[-1]['worker_id']==wb['id']
            f.generation.dispatcher.set_automatic(manifest['batch_id'],False)
            wc=fixtures.configured(f,c);f.generation.dispatcher.tick();assert len(f.launched)==2
            f.generation.dispatcher.set_automatic(manifest['batch_id'],True);f.generation.dispatcher.tick()
            assert len(f.launched)==3 and f.launched[-1]['worker_id']==wc['id']


def test_caption_pool_restricts_clusters_and_template_versions():
    with fixtures.app() as f:
        refs=f.root/'_01_writing/_references'
        (refs/'styles.csv').write_text('ID,cluster,style_primary,template\na1,Rock,Punk,a1.txt\na2,Rock,Punk,a2.txt\nb,Jazz,Choral,b.txt\n')
        for id in ('a1','a2','b'):(refs/(id+'.txt')).write_text('Global Metadata\n'+id+'\nVocal Details\nClear singing\nArrangement\nGuitar\n')
        p=f.preview(caption_mode='random',caption_count=2,caption_templates=['a2','b'])
        cases=f.generation.previews[p['token']]['checked'][0].planned_runs
        assert {r['template']['id'] for r in cases}=={'a2','b'}
        for ids in [[],['missing']]:
            response=f.client.post('/api/generation/preview',headers=f.headers,json={'input_root':str(f.built),'all_inputs':True,'connection':{'backend':'local'},'caption_mode':'random','caption_templates':ids})
            assert response.status_code==400
        assert f.source.read_bytes()==f.original and not f.launched


def test_legacy_active_controller_is_left_running_until_explicit_continue():
    with fixtures.app() as f,server() as (a,_),server(nodes_override=different_models()) as (b,_):
        wa=fixtures.configured(f,a);wb=fixtures.configured(f,b)
        p=f.preview(connection=None,worker_id=wa['id'],caption_mode='yaml');f.generation.start(p['token'])
        task=dict(f.launched[0]);task.pop('worker_id');manifest=verify_plan(task['manifest'])
        folder=f.generation.runtime/'app_jobs'/('a'*32);folder.mkdir(parents=True);(folder/'task.json').write_text(json.dumps(task));(folder/'state.json').write_text('{"status":"running"}')
        with patch.object(f.generation,'active',return_value=True):
            with pytest.raises(ValueError,match='Pause it after the current song'):f.generation.dispatcher.add(manifest['batch_id'],wb['id'])
        assert not policy(manifest).get('worker_assignments') and len(f.launched)==1
        with patch.object(f.generation,'active',return_value=False):f.generation.action('a'*32,'continue')
        assert f.launched[-1]['worker_id']==wa['id']
