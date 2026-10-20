"""Browser-compatible proxy transport, linked experiments and competing worker claims."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
import io
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch

import pytest
import test_app_generation
from test_comfy_execution import server,inventory
from _gbr_generation.experiments import expand
from _gbr_generation.executor import execute,cleanup_outputs
from _gbr_generation.planner import verify_plan
from _gbr_generation.app_jobs import TimestampStream
from _gbr_generation.yaml_document import YamlDocument
from client import Client


@contextmanager
def app():
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    raw=f.original
    for key,value in [(b'dit',b'bf16'),(b'text_encoder',b'bf16'),(b'sampler',b'euler'),(b'scheduler',b'flow_match_euler')]:raw=raw.replace(key+b': '+value,key+b':')
    f.source.write_bytes(raw);f.original=raw
    try:yield f
    finally:f.doCleanups()


def configured(f,address):return f.generation.workers.save({'connection':{'backend':'comfyui','address':address}})


@pytest.mark.parametrize('prefix',['','/api'])
def test_legacy_headers_and_api_prefix_include_flac_download(prefix):
    with app() as f,server(api_prefix=prefix,browser_headers=True) as (address,state):
        entry=configured(f,address)
        plan=f.preview(connection=None,worker_id=entry['id'],parameters={'dit':'','text_encoder':''},caption_mode='yaml')
        assert plan['ready']==1,plan
        assert plan['inputs'][0]['effective']['dit']=='minimax_music3_dit_fp32.safetensors'
        f.generation.start(plan['token']);task=f.launched[0]
        result=execute(task['manifest'])
        assert result['status']=='succeeded',result
        assert state['submissions']==1 and state['acked']
        assert f.source.read_bytes()==f.original
        assert not list(Path(task['manifest']).parent.rglob('download.part'))


def test_linked_combinations_are_not_cartesian_and_reject_overlap():
    pairs=[{'sampler':'euler','scheduler':'simple'},{'sampler':'dpmpp_2m_sde_gpu','scheduler':'ddim_uniform'}]
    models=[{'text_encoder':'bf16','dit':'fp32'},{'text_encoder':'int8','dit':'bf16'}]
    values=expand({'groups':[pairs,models],'sweeps':{'sampler_cfg':'1:2:1'},'repetitions':2})
    assert len(values)==16
    assert all({'sampler':v['sampler'],'scheduler':v['scheduler']} in pairs for v,_,_ in values)
    assert all({'text_encoder':v['text_encoder'],'dit':v['dit']} in models for v,_,_ in values)
    with pytest.raises(ValueError,match='one range or linked group'):expand({'groups':[pairs],'sweeps':{'sampler':['euler']}})
    with pytest.raises(ValueError,match='same fields'):expand({'groups':[[pairs[0],{'sampler':'heun'}]]})


def test_two_workers_claim_shared_jobs_once_with_their_own_models():
    second_nodes=inventory();second_nodes['CLIPLoader']['input']['required']['clip_name'][0]=['minimax_music3_text_encoder_int8.safetensors'];second_nodes['UNETLoader']['input']['required']['unet_name'][0]=['minimax_music3_dit_bf16.safetensors']
    with app() as f,server(browser_headers=True) as (a,sa),server(nodes_override=second_nodes,browser_headers=True) as (b,sb):
        wa,wb=configured(f,a),configured(f,b)
        plan=f.preview(connection=None,worker_id='auto',caption_mode='yaml',experiments={'repetitions':5,'seed_policy':'randomize'})
        assert plan['ready']==1 and plan['generation_count']==5 and len(plan['workers'])==2,plan
        f.generation.start(plan['token']);assert len(f.launched)==2
        path=Path(f.launched[0]['manifest']);manifest=verify_plan(path)
        assert all(len(r['worker_options'])==2 for r in manifest['runs'])
        gate=threading.Barrier(2);calls=[];lock=threading.Lock()
        # Exercise real Comfy HTTP/FLAC publication; hold only the first jobs so both endpoints claim concurrently.
        original=Client.run
        def held(client,job,directory,resume=False):
            with lock:count=sum(endpoint==client.address for endpoint,_ in calls);calls.append((client.address,job['run_id']))
            if count==0:gate.wait(timeout=10)
            if client.address==b:time.sleep(.2)
            return original(client,job,directory,resume)
        def consume(entry):
            while True:
                result=execute(path,worker_id=entry['id'])
                assert all(a['status']=='succeeded' for a in result['attempts']),result
                if not result['attempts']:break
        with patch.object(Client,'run',held),ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(consume,[wa,wb]))
        assert len(calls)==len({r for _,r in calls})==5
        assert {e for e,_ in calls}=={a,b}
        assert sa['submissions']+sb['submissions']==5
        assert sa['prompt']['1']['inputs']['clip_name'].endswith('bf16.safetensors')
        assert sb['prompt']['1']['inputs']['clip_name'].endswith('int8.safetensors')
        assert len(list(f.generation.generated.rglob('*.flac')))==5
        assert not list(path.parent.glob('attempts/*/audio.flac'))
        rows=f.app.state.service.db.all('SELECT effective_json FROM generation_runs')
        assert {json.loads(r['effective_json'])['text_encoder'] for r in rows}=={'minimax_music3_text_encoder_bf16.safetensors','minimax_music3_text_encoder_int8.safetensors'}
        cleanup_outputs(path)
        assert f.source.read_bytes()==f.original


def test_fixed_pair_excludes_only_incompatible_workers_and_no_default_errors():
    with app() as f,server() as (a,_),server(nodes_override={**inventory(),'KSampler':{'input':{'required':{**inventory()['KSampler']['input']['required'],'sampler_name':[['heun']]}}}}) as (b,_):
        wa,wb=configured(f,a),configured(f,b)
        p=f.preview(connection=None,worker_id='auto',caption_mode='yaml',experiments={'groups':[[{'sampler':'euler','scheduler':'simple'}]]})
        assert p['ready']==1,p
        cases=f.generation.previews[p['token']]['checked'][0].planned_runs
        assert [o['worker_id'] for o in cases[0]['worker_options']]==[wa['id']]
        f.generation.start(p['token']);assert len(f.launched)==1 and f.launched[0]['worker_id']==wa['id']


def test_per_field_sources_and_timestamped_log_view_metadata():
    with app() as f:
        p=f.preview(parameters={'duration':45,'sampler_cfg':2.2},parameter_sources={'duration':'control','sampler_cfg':'worker'})
        assert p['inputs'][0]['effective']['duration']==45
        assert p['inputs'][0]['effective']['sampler_cfg']==1.7
        # A default duration still inherits the YAML when no explicit control is selected.
        assert f.preview()['inputs'][0]['effective']['duration']==30
    target=io.StringIO();stream=TimestampStream(target);stream.write('first\npart');stream.write(' two\n');stream.flush()
    lines=target.getvalue().splitlines();assert len(lines)==2 and all(line.startswith('[20') for line in lines)
    assert lines[1].endswith('part two')


def test_uncertain_shared_prompt_stays_with_its_worker_and_reconnects_once():
    with app() as f,server(drop_ack=True) as (a,sa),server() as (b,sb):
        wa,wb=configured(f,a),configured(f,b)
        p=f.preview(connection=None,worker_id='auto',caption_mode='yaml');f.generation.start(p['token']);path=f.launched[0]['manifest']
        first=execute(path,worker_id=wa['id']);assert first['status']=='running'
        assert not execute(path,worker_id=wb['id'])['attempts']
        for index,task in enumerate(f.launched):
            folder=f.generation.runtime/'app_jobs'/str(index).zfill(32);folder.mkdir(parents=True)
            (folder/'task.json').write_text(json.dumps(task));(folder/'state.json').write_text('{"status":"running"}')
        listed={item['worker_id']:item for item in f.generation.tasks()}
        assert len(listed[wa['id']]['runs'])==1 and not listed[wb['id']]['runs']
        assert not listed[wb['id']]['reconnect']
        result=execute(path,worker_id=wa['id'],resume_remote=True)
        assert result['status']=='succeeded',result
        assert sa['submissions']==1 and sb['submissions']==0


def test_available_models_and_advertised_defaults_are_used_but_explicit_missing_models_fail():
    nodes=inventory();names=['minimax_music3_text_encoder_int8.safetensors','minimax_music3_text_encoder_fp8.safetensors']
    nodes['CLIPLoader']['input']['required']['clip_name']=[names,{'default':names[1]}]
    with app() as f,server(nodes_override=nodes) as (address,state):
        entry=configured(f,address)
        p=f.preview(connection=None,worker_id=entry['id'],caption_mode='yaml')
        assert p['ready']==1 and p['inputs'][0]['effective']['text_encoder']==names[1],p
        bad=f.preview(connection=None,worker_id=entry['id'],caption_mode='yaml',parameters={'text_encoder':'missing.safetensors'},parameter_sources={'text_encoder':'control'})
        assert bad['invalid']==1 and not state['submissions']


def test_actual_403_returns_actionable_proxy_error_without_posting():
    # This server deliberately rejects a request whose User-Agent is not browser-like.
    from client import HEADERS
    with server(browser_headers=True) as (address,state),patch.dict(HEADERS,{'User-Agent':'Python-urllib/test'}):
        with pytest.raises(ValueError,match='RunPod/Cloudflare.*403.*8188'):Client(address).inventory()
        assert not state['submissions']


def test_shared_local_worker_task_handles_null_execution_address():
    from test_execution import ExecutionTests
    with app() as f,patch.object(f.generation.workers,'inspect',return_value={'busy':False,'models':f.generation.native_choices()}):
        f.generation.workers.save({'connection':{'backend':'local'}})
        p=f.preview(connection=None,worker_id='auto',caption_mode='yaml');f.generation.start(p['token']);task=f.launched[0]
        boundary=ExecutionTests();boundary.calls=[]
        result=execute(task['manifest'],worker_python=sys.executable,launcher=boundary.worker,worker_id=task['worker_id'])
        assert result['status']=='succeeded'
        folder=f.generation.runtime/'app_jobs'/('d'*32);folder.mkdir(parents=True)
        (folder/'task.json').write_text(json.dumps(task));(folder/'state.json').write_text('{"status":"queue_complete"}')
        item=f.generation.tasks()[0]
        assert item['batch_progress']=={'complete':1,'total':1} and len(item['runs'])==1
