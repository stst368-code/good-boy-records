"""Publish in the app, close the studio, then audition the public R2-backed page.

Uses synthetic tones and an in-memory R2 boundary. No real bucket is written.
"""
import argparse
from copy import deepcopy
from functools import partial
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import sys
import threading
import time
from urllib.parse import urlsplit

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'_02_generation/_tests'),str(ROOT/'_02_generation'),
               str(ROOT/'_03_review/_generation_review'),str(ROOT/'_96_website/_tests')]
import test_app_generation
import test_experiment_publishing as fixture
from _gbr_generation.experiment_outputs import ExperimentOutputs
from ui_website import Routes


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True)
    parser.add_argument('--site',type=Path,required=True);parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    if args.screenshots:args.screenshots.mkdir(parents=True,exist_ok=True)
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    server=None
    try:
        store=fixture.Store();fixture.configure(f,store)
        manifest=fixture.generate(f,seconds=8)
        f.generation.workers.inspect=lambda e:{'busy':False,'models':f.generation.native_choices()}
        f.generation.workers.save({'connection':{'backend':'local'}})
        server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18786,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=args.browser,headless=True,
                args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu','--autoplay-policy=no-user-gesture-required'])
            page=browser.new_page(viewport={'width':1920,'height':1080});errors=[]
            page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:18786')
            page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#generationTab').click()
            page.wait_for_selector('.generation-file');page.locator('#genExperimentsTab').click()
            card=page.locator(f'[data-batch="{manifest["batch_id"]}"]');card.wait_for()
            card.locator('summary',has_text='Publish comparison to website').click()
            publish=card.get_by_role('button',name='Publish comparison to R2',exact=True)
            publish.wait_for();page.wait_for_function('()=>[...document.querySelectorAll(".comparison-form button")].some(b=>b.textContent==="Publish comparison to R2"&&!b.disabled)')
            assert card.get_by_label('Varied parameter').input_value()=='sampler_steps'
            assert card.get_by_label('Website position').input_value()=='sampling-steps'
            assert card.get_by_label('Public audio format').input_value()=='flac'
            card.get_by_label('Public comparison title').fill('Three controlled step settings')
            publish.click();link=card.get_by_role('link',name='Open website comparison');link.wait_for(timeout=20000)
            public_url=link.get_attribute('href');assert '?experiment=' in public_url
            if args.screenshots:page.screenshot(path=str(args.screenshots/'comparison-publish-app.png'),full_page=True)
            page.close()
            # Remove every local result and stop both studio lifespans before
            # making any public playback request. Only the R2 objects remain.
            for run in manifest['runs']:ExperimentOutputs(f.generation).action(run['id'],'delete')
            assert not list(f.generation.experimental.rglob('*.flac'))
            assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==0
            server.should_exit=True;thread.join(timeout=10);server=None
            f.doCleanups()
            objects=deepcopy(store.objects)
            with ThreadingHTTPServer(('127.0.0.1',0),partial(Routes,directory=str(args.site.resolve()))) as website:
                threading.Thread(target=website.serve_forever,daemon=True).start()
                base=f'http://127.0.0.1:{website.server_port}/good-boy-records/'
                requested=[]
                def media(route):
                    key=urlsplit(route.request.url).path.lstrip('/');requested.append(key)
                    raw=objects.get(key)
                    if raw is None:route.fulfill(status=404,body='Missing fixture');return
                    content_type='application/json' if key.endswith('.json') else 'audio/flac'
                    headers={'Access-Control-Allow-Origin':'*','Accept-Ranges':'bytes'}
                    value=route.request.headers.get('range','')
                    if value.startswith('bytes='):
                        first,last=value[6:].split('-',1);start=int(first or 0);end=min(int(last) if last else len(raw)-1,len(raw)-1)
                        headers['Content-Range']=f'bytes {start}-{end}/{len(raw)}'
                        route.fulfill(status=206,content_type=content_type,body=raw[start:end+1],headers=headers)
                    else:route.fulfill(content_type=content_type,body=raw,headers=headers)
                for width,height in [(1920,1080),(3840,2160),(390,844)]:
                    page=browser.new_page(viewport={'width':width,'height':height});page.on('pageerror',lambda e:errors.append(str(e)))
                    page.route('**/gbr/v2/experiments/**',media)
                    public=urlsplit(public_url)
                    page.goto(base+'how-its-made/?'+public.query+'#'+public.fragment)
                    lab=page.locator('[data-experiment=sampling-steps]')
                    page.wait_for_function('()=>document.querySelector(".experiment-count")?.textContent.includes("3 / 3")')
                    assert lab.locator('input[type=range]').get_attribute('max')=='3'
                    assert lab.locator('h4').inner_text()=='Three controlled step settings'
                    assert lab.locator('audio').is_visible() and lab.locator('.experiment-measurements').is_visible()
                    audio=lab.locator('audio');audio.evaluate('a=>a.play()')
                    page.wait_for_function('()=>document.querySelector(".experiment-audio").currentTime>2')
                    audio.evaluate('a=>a.pause()');position=audio.evaluate('a=>a.currentTime')
                    lab.get_by_role('button',name='B · 2',exact=True).click()
                    page.wait_for_function('()=>document.querySelector(".experiment-audio").readyState>=1&&document.querySelector(".experiment-audio").currentTime>1.8')
                    assert audio.evaluate('a=>a.paused') and abs(audio.evaluate('a=>a.currentTime')-position)<.2
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    if args.screenshots:page.screenshot(path=str(args.screenshots/f'comparison-public-{width}.png'),full_page=True)
                    page.close()
                assert any(k.endswith('.flac') for k in requested)
                # A corrupted descriptor must never populate the player.
                catalogue=json.loads(objects[fixture.POINTER]);key=urlsplit(catalogue['sets'][0]['url']).path.lstrip('/')
                saved=objects[key];objects[key]=saved.replace(b'Three controlled',b'TAMPERED controlled')
                page=browser.new_page();page.on('pageerror',lambda e:errors.append(str(e)));page.route('**/gbr/v2/experiments/**',media)
                page.goto(base+'how-its-made/?'+urlsplit(public_url).query+'#generation/sampling')
                page.wait_for_function('()=>document.querySelector(".published-set-state")?.textContent.includes("could not be loaded")')
                assert page.locator('.experiment-audio').count()==0
                objects[key]=saved
                page.get_by_role('button',name='Reload comparisons',exact=True).click()
                page.wait_for_function('()=>document.querySelector(".experiment-count")?.textContent.includes("3 / 3")')
                page.close()
            assert not errors,errors
            browser.close()
            print('PASS: app publication, no review rows, local deletion/studio shutdown, R2-only playback, A/B seek, desktop/4K/mobile, integrity rejection and reload; no JS errors')
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        f.doCleanups()


if __name__=='__main__':main()
