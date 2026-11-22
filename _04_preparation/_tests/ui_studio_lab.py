"""Real API/browser regression for short trials and the unified song studio."""
import argparse
import json
from pathlib import Path
import socket
import sys
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'_02_generation/_tests'),str(ROOT/'_03_review/_generation_review/_tests'),str(ROOT/'_04_preparation')]
import test_app_generation
import test_execution
from conftest import complete,archive_all
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan
from _gbr_preparation.service import digest


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',required=True)
    parser.add_argument('--width',type=int,default=1440)
    parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    fixture=test_app_generation.GenerationAppTests('runTest');fixture.setUp()
    server=None;errors=[];failures=[]
    try:
        generation=fixture.generation
        generation.workers.inspect=lambda entry:{'busy':False,'models':generation.native_choices()}
        generation.workers.save({'connection':{'backend':'local'}})
        review=fixture.app.state.service;prep=fixture.app.state.preparation
        prep.tracks=fixture.root/'_tracks';review.archiver.preparation_root=prep.tracks
        audio=fixture.root/'studio.flac';t=np.arange(16000*8)/16000
        sf.write(audio,.16*np.sin(2*np.pi*330*t),16000,subtype='PCM_24')
        audio.with_suffix('.yaml').write_text('song_id: 13\nvariant_id: 1\ntitle: Studio browser test\ncluster: Rock\nstyle: Punk\nlyrics: |\n  Keep the lyric timing\n  Listen to this section\n')
        aid=review.indexer.index(audio);gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
        review.set_flags(gid,review.default_reviewer_id,distribution=True);complete(review,gid);archive_all(review)
        source=prep.source(gid)[1];original=digest(source)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        until=time.monotonic()+15
        while not server.started and time.monotonic()<until:time.sleep(.05)
        assert server.started
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
            page=browser.new_page(viewport={'width':args.width,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('response',lambda r:failures.append((r.status,r.url)) if r.status>=500 else None)
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('#knownReviewers button',has_text='Simon').click()
            def tab(identifier):
                if args.width<1080:page.locator('#workspaceMenu').click()
                page.locator('#'+identifier).click()
            tab('generationTab')
            page.locator('#genLabTab').click()
            page.locator('#labSource option').nth(1).wait_for(state='attached')
            page.locator('#labSource').select_option(str(fixture.source))
            page.wait_for_function("()=>document.getElementById('labLyrics').value.includes('[chorus]')")
            page.locator('#labTitle').fill('Browser short trial')
            page.locator('#labCaption').fill('An intimate, dry vocal over syncopated drums')
            page.locator('#labLyrics').fill('[verse]\nTry this little phrase\n')
            page.locator('#labSamplerSeed').fill('18446744073709551614')
            page.locator('#labSteps').fill('30,35')
            page.locator('#labCheck').click()
            page.wait_for_function("()=>!document.getElementById('labRun').disabled")
            page.locator('#labRun').click()
            page.wait_for_function("()=>document.getElementById('labStatus').textContent.includes('Trial queued')")
            manifest_path=Path(fixture.launched[-1]['manifest']);manifest=verify_plan(manifest_path)
            assert manifest['purpose']=='experiment' and len(manifest['runs'])==2
            assert all(run['effective']['sampler_seed']=='18446744073709551614' for run in manifest['runs'])
            worker=test_execution.ExecutionTests('runTest');worker.calls=[]
            execute(manifest_path,worker_python=sys.executable,launcher=worker.worker,limit=2,
                    worker_id=fixture.launched[-1].get('worker_id'))
            page.locator('#labRefresh').click()
            page.locator('#labResults button[data-deck=A]').first.wait_for()
            page.locator('#labResults button[data-deck=A]').first.click()
            assert '/experiments/' in page.locator('#labA').get_attribute('src')
            page.locator('#labResults button[data-reuse]').first.click()
            page.wait_for_function("()=>document.getElementById('labCaption').value==='An intimate, dry vocal over syncopated drums'")
            assert page.locator('#labSamplerSeed').input_value()=='18446744073709551614'
            assert not fixture.client.get('/api/generation/jobs').json()['jobs']
            assert fixture.source.read_bytes()==fixture.original
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            if args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(args.screenshots/f'lab-{args.width}.png'),full_page=True)
            assert not errors,errors
            assert not failures,failures
            print(json.dumps({'width':args.width,'passed':True,'checks':['short trial queue','exact seeds','production isolation','A/B results','source preserved']}))
            browser.close()
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        fixture.doCleanups()
    from ui_song_studio import main as studio_main
    studio_main()


if __name__=='__main__':main()
