"""Actual FFmpeg audition and preparation navigation in desktop and phone browsers."""
import argparse
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
for path in [ROOT/'_98_database',ROOT/'_03_review/_generation_review',ROOT/'_03_review/_generation_review/_tests']:sys.path.insert(0,str(path))
from _gbr_database.initialize import initialize
from _review.app import create_app
from conftest import complete,archive_all


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--browser',required=True);args=parser.parse_args()
    errors=[];failures=[];checks=[]
    with tempfile.TemporaryDirectory() as temporary:
        root=Path(temporary);source=root/'input';source.mkdir()
        database=root/'shared.sqlite3';initialize(database)
        config=root/'config.json';config.write_text(json.dumps({'input_directories':[str(source)],'database_path':str(database)}))
        app=create_app(config,start_worker=False,database_path=database);service=app.state.service;prep=app.state.preparation
        prep.tracks=root/'_tracks';prep.references=root/'_references';service.archiver.preparation_root=prep.tracks
        audio=source/'song.flac';t=np.arange(16000*4)/16000;sf.write(audio,.2*np.sin(2*np.pi*330*t),16000,subtype='PCM_24')
        raw=b'title: Browser song\ncluster: Rock\nstyle: Punk\nlyrics: |\n  test the room\n  keep the song\n';audio.with_suffix('.yaml').write_bytes(raw)
        aid=service.indexer.index(audio);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
        service.set_flags(gid,service.default_reviewer_id,distribution=True);complete(service,gid);archive_all(service)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            deadline=time.monotonic()+20
            while not server.started and time.monotonic()<deadline:time.sleep(.05)
            assert server.started
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
                for width in [1440,390,320]:
                    page=browser.new_page(viewport={'width':width,'height':950},is_mobile=width<700,has_touch=width<700)
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.on('response',lambda response:failures.append((response.status,response.url)) if response.status>=500 else None)
                    page.goto(f'http://127.0.0.1:{port}')
                    page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click()
                    page.locator('#preparationTab').click()
                    page.wait_for_selector('#prepWorkspace:not([hidden])')
                    assert 'Browser song' in page.locator('#prepProject').inner_text()
                    page.locator('[data-mix=gain_db]').fill('-6')
                    page.locator('#prepPreview').click()
                    page.wait_for_function("()=>document.querySelector('#prepHistory').textContent.includes('mix preview · done')",timeout=25000)
                    page.wait_for_function("()=>document.getElementById('prepProcessed').src.includes('/artifacts/')")
                    page.locator('#prepAB').click()
                    page.wait_for_function("()=>!document.getElementById('prepProcessed').paused")
                    page.locator('#prepAB').click()
                    page.wait_for_function("()=>!document.getElementById('prepRaw').paused")
                    page.locator('#prepRaw').evaluate('(a)=>a.pause()')
                    page.locator('#prepDirection').fill('Ink engraving, emerald and copper')
                    page.locator('#prepBrief').click()
                    page.wait_for_function("()=>document.getElementById('prepPrompt').value.includes('Ink engraving')")
                    assert 'test the room' in page.locator('#prepPrompt').input_value()
                    assert 'Sample peak' in page.locator('#prepHistory').inner_text()
                    page.reload();page.wait_for_selector('#preparationTab');page.locator('#preparationTab').click()
                    page.wait_for_function("()=>document.getElementById('prepPrompt').value.includes('Ink engraving')")
                    assert page.locator('#prepDirection').input_value()=='Ink engraving, emerald and copper'
                    page.locator('#prepMaster').click()
                    page.wait_for_function("()=>document.getElementById('prepHistory').textContent.includes('mix master · done')",timeout=25000)
                    page.locator('#prepVisuals').click()
                    page.wait_for_function("()=>document.getElementById('prepHistory').textContent.includes('visuals · done')",timeout=25000)
                    assert page.locator('#prepHistory a',has_text='visual_features').count()
                    page.locator('#prepClean').click()
                    page.wait_for_function("()=>!document.getElementById('prepProcessed').getAttribute('src')")
                    assert page.locator('#prepHistory a',has_text='master_candidate').count()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    page.locator('#reviewTab').click()
                    assert page.locator('#preparationView').is_hidden()
                    assert next(prep.source(gid)[1].parent.glob('*.yaml')).read_bytes()==raw
                    checks.append({'width':width,'passed':True});print(json.dumps(checks[-1]),flush=True);page.close()
                browser.close()
            assert not errors,errors
            assert not failures,failures
            print(json.dumps({'workflows':checks,'js_errors':errors,'server_errors':failures,'media':'real FFmpeg output'}))
        finally:
            server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':main()
