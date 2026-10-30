"""Real GBR HTTP + bridge + browser; Comfy's shell is a small API harness."""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time

import pytest
import uvicorn
from aiohttp import web
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
for folder in ['_01_writing/_tests','_02_generation/_tests']:
    sys.path.insert(0,str(ROOT/folder))
from test_writing import app
from test_authoring import setup as setup_catalogue
from test_execution import ExecutionTests
from _gbr_generation.executor import execute

PACK=ROOT/'_99_tools/_comfyui_gbr'
node_spec=importlib.util.spec_from_file_location('gbr_ui_nodes',PACK/'nodes.py');node_module=importlib.util.module_from_spec(node_spec);node_spec.loader.exec_module(node_module)
spec=importlib.util.spec_from_file_location('gbr_bridge_test',PACK/'bridge.py');bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)


def until(fn):
    end=time.monotonic()+20
    while time.monotonic()<end:
        if fn():return
        time.sleep(.05)
    raise AssertionError('Background operation timed out')


@pytest.mark.skipif(not os.environ.get('GBR_TEST_CHROMIUM'),reason='Set GBR_TEST_CHROMIUM for the browser integration test')
def test_writing_graph_dispatch_experiment_and_fourth_rating(app,monkeypatch):
    f,w=app;setup_catalogue(f);review=f.app.state.service
    # Pure CPU test worker: the existing dispatcher/manifest/executor and media
    # handoff run normally, with no model download, GPU or paid pod allocation.
    entry=f.generation.workers.save({'connection':{'backend':'local'}})
    report={'busy':False}
    monkeypatch.setattr(f.generation.workers,'pool',lambda:[(entry,report)])
    monkeypatch.setattr(f.generation.workers,'select',lambda *args,**kwargs:(entry,report))
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=port,lifespan='off',log_level='error'))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start();until(lambda:server.started)
    loop=asyncio.new_event_loop();ready=threading.Event();state={}
    async def start_bridge():
        application=web.Application();routes=web.RouteTableDef();bridge.register(routes,f'http://127.0.0.1:{port}');application.add_routes(routes)
        defaults={name:{key:(value[1].get('default','') if len(value)>1 and isinstance(value[1],dict) else value[0][0] if isinstance(value[0],list) else '') for key,value in cls.INPUT_TYPES()['required'].items() if value[0] not in ('GBR_SONG','GBR_CAPTION')} for name,cls in node_module.NODE_CLASS_MAPPINGS.items()}
        shell="<html><body><script type='module' src='/test-extension.js'></script></body></html>"
        stub="""const defaults=DEFAULTS;const types={};
for(const [name,values] of Object.entries(defaults)){types[name]=class{constructor(){this.type=name;this.widgets=Object.entries(values).map(([name,value])=>({name,value}));this.size=[400,300];this.pos=[0,0];this.properties={};this.onNodeCreated?.();}addWidget(type,name,value,callback){this.widgets.push({type,name,value,callback});}connect(slot,node,target){app.graph.links.push({from:this.type,to:node.type,slot,target});}};}
window.LiteGraph={createNode:name=>new types[name]()};
export const app={graph:{_nodes:[],links:[],add(node){this._nodes.push(node)},setDirtyCanvas(){}},registerExtension(extension){for(const [name,type] of Object.entries(types))extension.beforeRegisterNodeDef(type,{name});extension.setup();extension.bottomPanelTabs[0].render(document.body);}};window.fixtureApp=app;""".replace('DEFAULTS',json.dumps(defaults))
        async def harness(request):return web.Response(text=shell,content_type='text/html')
        async def extension(request):return web.Response(text=(PACK/'_web/extension.js').read_text(),content_type='text/javascript')
        async def app_script(request):return web.Response(text=stub,content_type='text/javascript')
        application.router.add_get('/test-harness',harness);application.router.add_get('/test-extension.js',extension);application.router.add_get('/scripts/app.js',app_script)
        runner=web.AppRunner(application);await runner.setup();site=web.TCPSite(runner,'127.0.0.1',0);await site.start();state.update(runner=runner,port=site._server.sockets[0].getsockname()[1]);ready.set()
    def run_bridge():loop.run_until_complete(start_bridge());loop.run_forever()
    bridge_thread=threading.Thread(target=run_bridge,daemon=True);bridge_thread.start();assert ready.wait(10)
    errors=[]
    try:
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=os.environ['GBR_TEST_CHROMIUM'],headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
            page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{state["port"]}/test-harness');page.wait_for_selector('iframe');until(lambda:len(page.frames)>1);ui=page.frames[1];ui.wait_for_function("() => document.getElementById('connection').textContent==='Connected to GBR'")
            ui.locator('#reviewer').select_option(label='Simon');ui.wait_for_function("() => localStorage.getItem('gbr-comfy-reviewer')")
            ui.locator('#title').fill('New Browser Song');ui.locator('#lyrics').fill('[Intro: quiet]\nHELLO\n\n[Refrain]\nWORLD');ui.locator('#saveSong').click()
            ui.wait_for_function("() => document.getElementById('notice').textContent.includes('Saved and registered')")
            assert ui.locator('#lyrics').input_value()=='[intro]\nhello\n[chorus]\nworld'
            ui.locator('[data-tab=caption]').click();ui.locator('#cluster').fill('Rock');ui.locator('#style').fill('Punk');ui.locator('#field_basic').fill('Bright guitars');ui.locator('#field_vocal_style').fill('Close, dry singing');ui.locator('#saveCaption').click()
            ui.wait_for_function("() => document.getElementById('notice').textContent.includes('Custom caption saved')")
            # Window messaging uses the same payload as a Comfy node execution.
            # Assert a graph request without launching or writing another song.
            ui.locator('[data-tab=generate]').click();ui.locator('#createGraph').click();page.wait_for_function('() => fixtureApp.graph._nodes.length===3')
            assert page.evaluate('fixtureApp.graph.links.length')==2
            assert page.evaluate("fixtureApp.graph._nodes[0].widgets.find(w=>w.name==='lyrics').value")=='[intro]\nhello\n[chorus]\nworld'
            ui.locator('#preview').click();ui.wait_for_function("() => !document.getElementById('launch').disabled")
            assert not f.launched
            ui.locator('#launch').click();until(lambda:len(f.launched)==1)
            manifest=Path(f.launched[-1]['manifest']);assert json.loads(manifest.read_text())['purpose']=='experiment'
            worker=ExecutionTests();worker.calls=[];result=execute(manifest,worker_python=sys.executable,launcher=worker.worker,worker_id=entry['id'])
            assert result['status']=='succeeded',result
            assert not review.db.all('SELECT * FROM generations')
            ui.locator('#connect').click();ui.wait_for_function("() => document.querySelector('#jobs button')")
            ui.locator('#jobs button').first.click();ui.wait_for_selector('#jobs audio')
            audio=ui.locator('#jobs audio').first;audio.evaluate('(el)=>el.play()');ui.wait_for_function("() => document.querySelector('#jobs audio').readyState>=2")
            assert audio.evaluate('(el)=>!el.paused');audio.evaluate('(el)=>el.pause()')
            ui.locator('#purpose').select_option('production');ui.locator('#preview').click();ui.wait_for_function("() => !document.getElementById('launch').disabled");ui.locator('#launch').click();until(lambda:len(f.launched)==2)
            manifest=Path(f.launched[-1]['manifest']);worker.calls=[];result=execute(manifest,worker_python=sys.executable,launcher=worker.worker,worker_id=entry['id']);assert result['status']=='succeeded'
            audio=next(f.generation.generated.rglob('*.flac'));review.indexer.index(audio)
            gid=review.db.one('SELECT id FROM generations')['id']
            details=f.client.get('/api/generation/jobs/'+json.loads(manifest.read_text())['batch_id']).json()
            assert details['runs'][0]['review_generation_id']==gid
            ui.locator('[data-tab=review]').click();ui.locator('#nextReview').click();ui.wait_for_function("() => document.getElementById('reviewTitle').textContent.includes('new-browser-song')")
            ui.locator('[data-tag=vocal_tone][data-polarity=positive]').click()
            ui.locator('[data-tag=vocal_tone][data-polarity=negative]').click();assert ui.locator('[data-tag=vocal_tone][data-polarity=negative]').get_attribute('aria-pressed')=='true'
            ui.locator('[data-tag=vocal_tone][data-polarity=positive]').click();ui.locator('#reason').fill('Browser review');ui.locator('[data-flag=bookmarked]').click()
            for metric in ['audio_quality','composition','consistency']:
                ui.locator(f'[data-metric={metric}][data-score="4"]').click()
            assert not review.db.all('SELECT * FROM reviews')
            ui.locator('[data-metric=realism][data-score="4"]').click();until(lambda:bool(review.db.all('SELECT * FROM reviews')))
            saved=review.db.one('SELECT * FROM reviews');assert saved['reason']=='Browser review' and saved['reviewer_id']==ui.evaluate("localStorage.getItem('gbr-comfy-reviewer')")
            assert review.db.one('SELECT polarity FROM review_tags')['polarity']=='positive'
            ui.wait_for_function("() => document.getElementById('reviewTitle').textContent.includes('No unreviewed')")
            assert ui.locator('#reason').input_value()==''
            assert all(b.get_attribute('aria-pressed')=='false' for b in ui.locator('[data-tag]').all())
            for width in [3840,390]:
                page.set_viewport_size({'width':width,'height':1000});assert ui.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            assert f.source.read_bytes()==f.original
            assert not review.db.all('PRAGMA foreign_key_check')
            assert not errors,errors
            browser.close()
    finally:
        asyncio.run_coroutine_threadsafe(state['runner'].cleanup(),loop).result(10);loop.call_soon_threadsafe(loop.stop);bridge_thread.join(10);server.should_exit=True;thread.join(10)
