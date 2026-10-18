"""Optional real-browser integration check. Uses temporary synthetic audio only.
Run: python tests/ui_smoke.py [--browser /path/to/chromium]
Requires requirements-dev.txt and `python -m playwright install chromium`.
"""
import argparse
import importlib
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
if not (Path(__file__).resolve().parents[1] / 'gbr_review').is_dir():
    sys.modules['gbr_review'] = importlib.import_module('review')
from gbr_review.app import create_app


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser');parser.add_argument('--screenshots',default='');args=parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);inp=root/'input';inp.mkdir();sr=16000;t=np.arange(sr*12)/sr
        for i in range(3):
            p=inp/f'version-{i}.flac';sf.write(p,.15*np.sin(2*np.pi*(220+i*110)*t),sr,subtype='PCM_24')
            p.with_suffix('.yaml').write_text(f'title: The Endless Game\ngenre: Chiptune\nsampler_steps: {30+i}\n',encoding='utf-8')
        config=root/'config.json';config.write_text(json.dumps({'input_directories':[str(inp)],'port':18765}))
        app=create_app(config,start_worker=True);service=app.state.service
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18765,log_level='error',loop='gbr_review.server_loop:create_loop'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.time()+25
        while time.time()<deadline:
            if server.started and service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n']==3:break
            time.sleep(.1)
        screenshots=Path(args.screenshots) if args.screenshots else None
        if screenshots:screenshots.mkdir(parents=True,exist_ok=True)
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
            page=browser.new_page(viewport={'width':1280,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:18765');page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click();page.wait_for_selector('#reviewCard:not([hidden])')
            page.wait_for_function("()=>document.querySelectorAll('.snippet').length>0")
            assert page.locator('#trackGenre').inner_text()=='Chiptune'
            assert page.locator('.rating-values button').count()==24
            page.locator('#play').click();page.wait_for_function("()=>!document.getElementById('audio').paused")
            page.locator('#selectionTab').click();assert page.evaluate("!document.getElementById('audio').paused")
            page.locator('#reviewTab').click();page.locator('#play').click()
            assert not page.locator('#reviewCard').inner_text().lower().__contains__('sampler')
            if screenshots:page.screenshot(path=str(screenshots/'desktop-review.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            sizes=page.locator('.rating-values button').evaluate_all('(buttons)=>buttons.map(b=>({width:b.getBoundingClientRect().width,height:b.getBoundingClientRect().height}))')
            assert all(x['height']>=48 and x['width']>=48 for x in sizes)
            assert page.evaluate('document.documentElement.scrollWidth<=window.innerWidth')
            if screenshots:page.screenshot(path=str(screenshots/'mobile-review.png'),full_page=True)
            assert page.locator('.rating-guidance').count()==4
            assert page.locator('.rating-anchor[data-score-min="0"]').count()==4
            assert page.locator('.rating-bracket').count()==8
            assert page.locator('#ratingReminder').is_visible()
            assert page.locator('#keyboardHelp').count()==0
            assert page.locator('#speed,#zoom,#pan,.rating-intro').count()==0
            assert page.evaluate('document.getElementById("audio").playbackRate')==1
            page.locator('#spectralToggle').click();page.wait_for_selector('#spectralWrap:not([hidden])')
            page.wait_for_function("()=>{const im=document.getElementById('spectrogram');return im.complete&&im.naturalWidth>0;}")
            page.locator('#spectralToggle').click()
            page.locator('#selectRange').click();box=page.locator('#waveform').bounding_box()
            page.mouse.move(box['x']+40,box['y']+25);page.mouse.down();page.mouse.move(box['x']+160,box['y']+25);page.mouse.up()
            page.wait_for_selector('#playRange:not([hidden])');page.locator('#playRange').click();page.wait_for_function("()=>!document.getElementById('audio').paused")
            page.locator('#selectRange').click();page.locator('[data-mode="quick"]').click()
            for dimension in ('audio_quality','composition','consistency','realism'):
                page.locator(f'[data-dimension="{dimension}"] [data-score="5"]').click()
            page.wait_for_function("()=>document.querySelectorAll('[aria-pressed=true]').length===0")
            assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==1
            page.locator('#back').click();page.wait_for_selector('#correctionBadge:not([hidden])')
            page.locator('[data-dimension="audio_quality"] [data-score="4"]').click()
            page.wait_for_function("()=>document.querySelectorAll('[aria-pressed=true]').length===0")
            assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==2
            page.locator('#skip').click()
            page.wait_for_timeout(300)
            for dimension in ('audio_quality','composition','consistency','realism'):
                page.locator(f'[data-dimension="{dimension}"] [data-score="5"]').click()
            page.wait_for_function("()=>document.querySelectorAll('[aria-pressed=true]').length===0")
            page.locator('#selectionTab').click();page.wait_for_selector('.candidate')
            page.locator('.candidate select').first.select_option('selected')
            page.wait_for_timeout(300)
            assert service.db.one("SELECT status FROM publishing_selection")['status']=='selected'
            page.locator('.candidate input[type=checkbox]').nth(0).check();page.locator('.candidate input[type=checkbox]').nth(1).check()
            page.locator('#compare').click();page.wait_for_selector('#compareDialog[open]')
            page.locator('#playA').click();page.wait_for_function("()=>!document.getElementById('audio').paused")
            page.locator('#playB').click();page.wait_for_function("()=>document.getElementById('audio').src.includes('/b/audio')&&!document.getElementById('audio').paused")
            page.locator('[data-prefer="a"]').click();page.wait_for_function("()=>document.getElementById('compareResult').textContent.includes('saved')")
            assert service.db.one('SELECT COUNT(*) AS n FROM preferences')['n']==1
            page.locator('[data-close="compareDialog"]').click()
            page.locator('#reviewTab').click();page.locator('#settingsButton').click();page.wait_for_selector('#settingsDialog[open]')
            if screenshots:page.screenshot(path=str(screenshots/'mobile-settings.png'),full_page=True)
            assert not errors,errors
            print(json.dumps({'desktop_and_mobile':'passed','js_errors':errors,'screenshots':str(screenshots) if screenshots else None}))
            browser.close()
        server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':main()
