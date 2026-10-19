"""HTTP Comfy boundary with synthetic FLAC, not a GPU/model quality test."""
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
import numpy as np
import pytest
import soundfile as sf
import test_execution

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'_99_tools/_workers_comfyui'))
from client import Client
from _gbr_generation.executor import execute
from _gbr_generation.connections import normalize
from _gbr_worker.contract import MODEL


def inventory():
    graph=json.loads((ROOT/'_99_tools/_workers_comfyui/_workflows/music-full.json').read_text())
    nodes={n['class_type']:{'input':{'required':{}}} for n in graph.values()}
    for n in graph.values():
        for key,value in n['inputs'].items():
            kind='INT' if type(value) is int else 'FLOAT' if type(value) is float else 'STRING'
            nodes[n['class_type']]['input']['required'][key]=[kind,{'min':0,'max':2**64-1}] if kind!='STRING' else ['STRING',{}]
    for node,key,values in [('CLIPLoader','clip_name',['minimax_music3_text_encoder_bf16.safetensors']),('UNETLoader','unet_name',['minimax_music3_dit_fp32.safetensors']),('VAELoader','vae_name',['minimax_music3_dav.safetensors']),('KSampler','sampler_name',['euler','dpmpp_2m_sde_gpu']),('KSampler','scheduler',['simple','ddim_uniform'])]:nodes[node]['input']['required'][key]=[values]
    nodes['MiniMaxMusic3TextEncode']['input']['required']['max_duration']=['FLOAT',{'min':.04,'max':360}]
    nodes['MiniMaxMusic3TextEncode']['input']['required']['cfg_scale']=['FLOAT',{'min':0,'max':100}]
    nodes['KSampler']['input']['required']['cfg']=['FLOAT',{'min':0,'max':100}]
    return nodes


@contextmanager
def server(drop_ack=False,extensions=True,unknown_code=404,prompt_code=None,busy=False,nodes_override=None):
    output=io.BytesIO();wave=np.sin(np.arange(4410)*.05)*.1;sf.write(output,np.stack([wave,wave],axis=1),44100,format='FLAC',subtype='PCM_24');audio=output.getvalue();state={'submissions':0,'acked':False,'requests':[]}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            state['requests'].append(('POST',self.path))
            value=json.loads(self.rfile.read(int(self.headers.get('Content-Length',0))))
            if self.path=='/prompt':
                if prompt_code:self.send_response(prompt_code);self.end_headers();return
                state['submissions']+=1;state['prompt']=value['prompt'];state['attempt']=value['extra_data']['gbr_attempt_id']
                if drop_ack:self.send_response(503);self.end_headers();return
                self.respond({'prompt_id':'owned-prompt'})
            elif self.path=='/gbr/ack' and extensions:
                assert value['sha256']==hashlib.sha256(audio).hexdigest();state['acked']=True;self.respond({'acknowledged':True})
            else:self.send_response(unknown_code);self.end_headers()
        def respond(self,data):
            raw=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):
            state['requests'].append(('GET',self.path))
            if self.path=='/object_info':self.respond(nodes_override or inventory())
            elif self.path=='/gbr/capabilities' and extensions:self.respond({'version':2,'verified_output_cleanup':True,'checkpoint_sha256':False})
            elif self.path=='/system_stats':self.respond({'system':{'comfyui_version':'test-boundary'},'devices':[{'name':'test GPU','vram_total':123456789,'vram_free':100000000}]})
            elif self.path=='/queue':self.respond({'queue_running':[[0,'another-job',{},{}]] if busy else [],'queue_pending':[]})
            elif self.path.startswith('/history'):
                history={'prompt':[0,'owned-prompt',{}, {'gbr_attempt_id':state['attempt']}],'status':{'status_str':'success','completed':True,'messages':[['execution_start',{'timestamp':1792400000000}],['execution_cached',{'nodes':['1','2']}],['execution_success',{'timestamp':1792400001500}]]},'outputs':{'9':{'audio':[{'filename':state['attempt']+'_00001_.flac','subfolder':'gbr','type':'output'}]}}}
                self.respond({'owned-prompt':history})
            elif self.path.startswith('/view?'):
                self.send_response(200);self.send_header('Content-Length',str(len(audio)));self.end_headers();self.wfile.write(audio)
            else:self.send_response(unknown_code);self.end_headers()
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(http.server_port),state
    finally:http.shutdown();http.server_close();thread.join()


@pytest.mark.parametrize('drop_ack',[False,True])
def test_comfy_full_publication_and_reconnect_without_duplicate_submission(drop_ack):
    fixture=test_execution.ExecutionTests('runTest');fixture.setUp()
    try:
        with server(drop_ack) as (address,state):
            fixture.spec.setdefault('batch',{}).update(sampler='dpmpp_2m_sde_gpu',scheduler='ddim_uniform',encoder_cfg=3.2,text_encoder='bf16',dit='fp32')
            fixture.spec['execution']={'conditioning':{'backend':'comfyui','address':address},'diffusion':{'backend':'comfyui','address':address},'encoding_cache':'prefer'}
            fixture.spec.pop('model_revision',None)
            path=fixture.prepare();result=execute(path)
            if drop_ack:
                assert result['status']=='running',json.dumps(result)
                result=execute(path,resume_remote=True)
            assert result['status']=='succeeded',json.dumps(result)
            assert state['submissions']==1 and state['acked']
            assert state['prompt']['2']['inputs']['cfg_scale']==3.2
            assert state['prompt']['6']['inputs']['denoise']==1.0
            assert state['prompt']['6']['inputs']['sampler_name']=='dpmpp_2m_sde_gpu'
            assert fixture.source.read_bytes()==fixture.original
            assert len(list((path.parent/'outputs').rglob('*.flac')))==1
            assert not list((path.parent/'attempts').rglob('audio.flac'))
            result=execute(path);assert not result['attempts'] and state['submissions']==1
            assert fixture.rows('SELECT gpu_name FROM worker_sessions')[0][0]=='test GPU'
            assert fixture.rows("SELECT duration_seconds FROM generation_timing_spans WHERE stage='comfy_execution'")[0][0]==1.5
            models=json.loads(fixture.rows('SELECT actual_models_json FROM generation_attempts')[0][0]);assert models['checkpoint_sha256'] is None
    finally:fixture.doCleanups()


def test_comfy_url_rejects_credentials_and_unsupported_cache_does_not_reach_server():
    for address in ['file:///tmp/comfy','https://user:secret@pod/','https://pod/?token=secret','https://pod/path']:
        with pytest.raises(ValueError):normalize({'backend':'comfyui','address':address})


def test_stock_comfy_405_optional_routes_publish_once_and_retain_cleanup_receipt():
    fixture=test_execution.ExecutionTests('runTest');fixture.setUp()
    try:
        with server(extensions=False,unknown_code=405) as (address,state):
            fixture.spec.setdefault('batch',{}).update(sampler='euler',scheduler='simple',text_encoder='bf16',dit='fp32')
            fixture.spec['execution']={'conditioning':{'backend':'comfyui','address':address},'diffusion':{'backend':'comfyui','address':address},'encoding_cache':'prefer'}
            fixture.spec.pop('model_revision',None)
            manifest=fixture.prepare();result=execute(manifest)
            assert result['status']=='succeeded',result
            assert state['submissions']==1
            assert ('POST','/gbr/model-info') not in state['requests']
            assert ('POST','/gbr/ack') not in state['requests']
            assert len(list((manifest.parent/'outputs').rglob('*.flac')))==1
            assert not list((manifest.parent/'attempts').rglob('audio.flac'))
            assert len(list((manifest.parent/'attempts').rglob('cleanup-pending.json')))==1
            assert fixture.source.read_bytes()==fixture.original
            assert not execute(manifest)['attempts']
    finally:fixture.doCleanups()
