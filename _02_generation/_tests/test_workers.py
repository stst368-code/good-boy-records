"""Worker settings, live selection and local launch at the real app/HTTP boundary."""
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError

import psutil
import pytest
import test_app_generation
from test_comfy_execution import server, inventory, Client
from _gbr_generation.connections import normalize
from _gbr_generation.workers import ensure_comfy, launcher_settings, launch_command


@pytest.fixture
def app():
    fixture = test_app_generation.GenerationAppTests('runTest')
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def save(app, connection, **kwargs):
    response = app.client.post('/api/generation/workers', headers=app.headers,
        json={'connection': connection, **kwargs})
    assert response.status_code == 200, response.text
    return response.json()


def test_settings_migrate_old_connections_once_keep_secrets_in_memory_and_use_endpoint_identity(app):
    g = app.generation
    g.remember({'connection': {'backend': 'comfyui', 'address': 'http://127.0.0.1:8188/'},
        'parameters': {'top_k': 51}, 'storage_bucket': 'existing'})
    listing = app.client.get('/api/generation/workers').json()['workers']
    assert listing[0]['id'] == 'comfyui|http://127.0.0.1:8188'
    assert 'workers' not in g.review.config.values['generation']
    entry = save(app, listing[0]['connection'], defaults={'top_k': 55},
                 secrets={'api_key':'secret-fixture'}, storage_bucket='existing')
    save(app, entry['connection'], defaults=entry['defaults'])
    saved = g.review.config.values['generation']
    assert 'connections' not in saved and len(saved['workers']) == 1
    assert saved['parameters'] == {'top_k': 51} and saved['storage_bucket'] == 'existing'
    assert 'secret-fixture' not in g.review.config.path.read_text()
    assert g.secrets['api_key'] == 'secret-fixture'
    app.client.post('/api/generation/workers/remove', headers=app.headers, json={'worker_id':entry['id']}).raise_for_status()
    assert app.client.get('/api/generation/workers').json()['workers'] == []
    assert g.preferences()['connections'] == []  # Deleting the final entry stays deleted.
    g.remember({'parameters': {'top_k':42}, 'worker_id':'auto'})
    assert g.preferences()['connections'] == []


def test_relevant_comfy_models_gpu_queue_and_default_settings(app):
    nodes = inventory()
    for node, field, irrelevant in [('CLIPLoader','clip_name','qwen_image_encoder.safetensors'),
                                  ('UNETLoader','unet_name','flux2.safetensors'),('VAELoader','vae_name','image_vae.safetensors')]:
        nodes[node]['input']['required'][field][0].append(irrelevant)
    with server(busy=True, nodes_override=nodes) as (url, _):
        entry = save(app, {'backend':'comfyui','address':url}, defaults={'sampler':'euler','scheduler':'simple'})
        response = app.client.post('/api/generation/workers/query', headers=app.headers, json={'worker_id':entry['id']})
        assert response.status_code == 200, response.text
        status = response.json()['status']
        assert status['online'] and status['busy'] and status['compatible']
        assert status['hardware']['gpu_name'] == 'test GPU'
        assert status['hardware']['usable_vram_bytes'] == 123456789
        assert status['queue_running'] == 1
        assert status['models']['diffusion_models'] == ['minimax_music3_dit_fp32.safetensors']
        assert status['models']['text_encoders'] == ['minimax_music3_text_encoder_bf16.safetensors']
        assert status['models']['vaes'] == ['minimax_music3_dav.safetensors']
        assert '_inventory' not in status


def test_automatic_prefers_online_idle_then_busy_skips_offline_disabled_and_incompatible(app):
    g = app.generation
    with server(busy=True) as (busy_url, _), server() as (idle_url, _):
        first = save(app, {'backend':'comfyui','address':busy_url})
        idle = save(app, {'backend':'comfyui','address':idle_url})
        disabled = save(app, {'backend':'comfyui','address':'http://127.0.0.1:1'}, enabled=False)
        selected, report = g.workers.select('auto', automatic=True)
        assert selected['id'] == idle['id'] and not report['busy']
        save(app, idle['connection'], enabled=False)
        selected, report = g.workers.select('auto', automatic=True)
        assert selected['id'] == first['id'] and report['busy']
        save(app, first['connection'], enabled=False)
        with pytest.raises(ValueError, match='No enabled worker'):
            g.workers.select('auto', automatic=True)
        # Unrelated image-only ComfyUI installations must not win Automatic.
        save(app, first['connection'], enabled=True)
        with patch.object(g.workers, 'inspect', return_value={'models':{}, 'missing_nodes':['MiniMaxMusic3TextEncode']}):
            with pytest.raises(ValueError, match='No enabled worker'):
                g.workers.select('auto', automatic=True)
        assert disabled['id'] not in {selected['id']}


def test_preview_freezes_selected_worker_defaults_and_never_mutates_yaml(app):
    with server() as (url, state):
        entry = save(app, {'backend':'comfyui','address':url}, defaults={'text_encoder':'bf16','dit':'fp32',
                     'sampler':'euler','scheduler':'simple','encoder_cfg':2.2})
        response = app.client.post('/api/generation/preview', headers=app.headers, json={
            'input_root': str(app.built), 'all_inputs':True, 'worker_id':'auto', 'parameter_priority':'form',
            'parameters':{'top_k':60}, 'experiments':{'sweeps':{'sampler_cfg':[1.4,2.4]}}})
        assert response.status_code == 200, response.text
        preview = response.json()
        assert preview['ready'] == 1 and preview['generation_count'] == 2
        assert preview['worker_id'] == entry['id']
        assert preview['inputs'][0]['effective']['encoder_cfg'] == 2.2
        assert preview['inputs'][0]['effective']['top_k'] == 60
        save(app, {'backend':'comfyui','address':'http://127.0.0.1:1'}, previous_id=entry['id'])
        app.client.post('/api/generation/start', headers=app.headers, json={'token':preview['token']}).raise_for_status()
        task = app.launched[-1]
        assert task['connection']['address'] == url  # Later settings never redirect a frozen run.
        assert app.source.read_bytes() == app.original
        assert state['submissions'] == 0


@pytest.mark.parametrize('unknown_code',[404,405,501])
def test_stock_comfy_missing_optional_routes_does_not_post_hash_request_or_hide_real_prompt_405(app, unknown_code):
    with server(extensions=False, unknown_code=unknown_code, prompt_code=405) as (url, state):
        entry = save(app, {'backend':'comfyui','address':url}, defaults={'dit':'fp32','scheduler':'simple'})
        result = app.client.post('/api/generation/preview', headers=app.headers, json={
            'input_root':str(app.built),'all_inputs':True,'worker_id':entry['id'],'parameter_priority':'form'})
        assert result.status_code == 200 and result.json()['ready'] == 1, result.text
        assert ('POST','/gbr/model-info') not in state['requests']
        # A required /prompt returning 405 still fails; only optional extension
        # discovery is allowed to fall back. No second prompt is submitted.
        frozen = app.generation.previews[result.json()['token']]
        from _gbr_generation.planner import prepare_batch
        manifest = prepare_batch(frozen['checked'],frozen['spec'],app.root/'batch',app.database,input_root=app.built)
        # make_job is exercised through execute in the existing reconnect tests.
        from _gbr_generation.executor import execute
        outcome = execute(manifest)
        assert outcome['status'] != 'succeeded'
        assert state['requests'].count(('POST','/prompt')) == 1
        assert ('POST','/gbr/model-info') not in state['requests']


def test_network_failures_identify_the_comfy_endpoint_not_the_writing_catalogue(app):
    with patch('client.Client.inventory', side_effect=HTTPError('http://localhost:8188/object_info',405,'Method Not Allowed',{},None)):
        response = app.client.post('/api/generation/preview', headers=app.headers, json={
            'input_root':str(app.built),'all_inputs':True,'connection':{'backend':'comfyui','address':'http://localhost:8188'}})
    assert response.status_code == 400
    assert 'ComfyUI' in response.text and 'writing input' not in response.text


def test_launcher_rejects_remote_urls_relative_paths_commands_and_invalid_arguments(tmp_path):
    local = normalize({'backend':'comfyui','address':'http://127.0.0.1:8188'})
    remote = normalize({'backend':'comfyui','address':'https://pod.example'})
    for value, connection in [({'path':str(tmp_path/'start.py'),'auto_start':True}, remote),
                              ({'path':'relative/start.py'},local), ({'path':str(tmp_path/'start.py'),'args':'--port 8188'},local),
                              ({'path':str(tmp_path/'start.py'),'command':'anything'},local)]:
        with pytest.raises(ValueError):launcher_settings(value,connection)
    start = tmp_path/'Comfy with spaces.bat';start.write_text('@echo off')
    with patch('_gbr_generation.workers.os', SimpleNamespace(name='nt',environ={})):
        command = launch_command({'path':str(start),'args':['--port','8188']})
        assert command[:4] == ['cmd.exe','/d','/s','/c']
        assert command[4] == f'""{start}" "--port" "8188""'
        with pytest.raises(ValueError,match='metacharacters'):
            launch_command({'path':str(start),'args':['&whoami']})


def test_local_autostart_waits_for_api_and_reuses_one_process(tmp_path):
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); port = s.getsockname()[1]
    start = tmp_path/'Comfy with spaces.py'
    start.write_text('''from http.server import BaseHTTPRequestHandler,HTTPServer
import json,sys
class Handler(BaseHTTPRequestHandler):
 def do_GET(self):
  raw=json.dumps({'system':{},'devices':[]}).encode();self.send_response(200);self.end_headers();self.wfile.write(raw)
 def log_message(self,*args):pass
HTTPServer(('127.0.0.1',int(sys.argv[1])),Handler).serve_forever()
''')
    connection = normalize({'backend':'comfyui','address':f'http://127.0.0.1:{port}'})
    launcher = {'path':str(start),'args':[str(port)],'python':sys.executable,'auto_start':True}
    runtime = tmp_path/'runtime'
    children = []
    popen = subprocess.Popen
    def capture(*args, **kwargs):
        child = popen(*args, **kwargs);children.append(child);return child
    try:
        with patch('_gbr_generation.workers.subprocess.Popen', side_effect=capture):
            ensure_comfy(connection,launcher,runtime,timeout=10)
            record = next(runtime.rglob('process.json')); before = record.read_bytes()
            ensure_comfy(connection,launcher,runtime,timeout=10)
            assert record.read_bytes() == before
            assert len(children) == 1 and children[0].poll() is None
            assert len(list(runtime.rglob('launcher.log'))) == 1
    finally:
        for child in children:
            if child.poll() is None:child.terminate();child.wait(timeout=5)


def test_new_worker_endpoints_keep_host_auth_csrf_and_no_extra_database(app):
    assert app.client.post('/api/generation/workers',json={'connection':{'backend':'local'}}).status_code == 403
    assert app.client.get('/tools/generation/workers.js').status_code == 200
    before = sorted(p.relative_to(app.root).as_posix() for p in app.root.rglob('*.sqlite3'))
    save(app, {'backend':'local'})
    assert sorted(p.relative_to(app.root).as_posix() for p in app.root.rglob('*.sqlite3')) == before
