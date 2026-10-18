"""Review flags/reasons/manual submit/directory editing in desktop and phone Chromium."""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from _review.app import create_app


def ready(page):
    page.wait_for_function("()=>document.getElementById('reviewCard').hidden===false&&document.getElementById('audio').src.includes('/api/playback/')")
    page.wait_for_function("()=>document.querySelectorAll('.rating-values button:disabled').length===0")


def score(page,values='5434'):
    page.evaluate('document.activeElement.blur()')
    page.keyboard.type(values,delay=60)


def current(s):return s.db.one('SELECT current_generation_id FROM sessions WHERE reviewer_id=?',(s.default_reviewer_id,))['current_generation_id']


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser');parser.add_argument('--screenshots');args=parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root=Path(tmp);inp=root/'input';inp.mkdir();extra=root/'extra music';extra.mkdir()
        sr=16000;t=np.arange(sr*12)/sr
        for i in range(8):
            path=inp/f'track-{i}.flac';sf.write(path,.1*np.sin(2*np.pi*(210+i*37)*t),sr,subtype='PCM_24');path.with_suffix('.yaml').write_text(f'title: Track {i}\ngenre: Chiptune\ncluster: Electronic\n')
        config=root/'config.json';config.write_text(json.dumps({'input_directories':[str(inp)],'port':18768}))
        app=create_app(config,start_worker=True);s=app.state.service
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=18768,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        try:
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                if server.started and s.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n']==8:break
                time.sleep(.1)
            assert server.started
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--use-gl=angle','--use-angle=swiftshader','--enable-unsafe-swiftshader'])
                page=browser.new_page(viewport={'width':1280,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto('http://127.0.0.1:18768');page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click();ready(page)
                assert page.locator('#autoSubmit').is_checked() and not page.locator('#submitReview').is_visible()
                first=current(s);reason='A strong chorus.\nWorth returning to. 012345 N B F'
                page.locator('#reason').fill(reason);page.locator('#reason').press('End');page.keyboard.type(' 5432');assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==0
                assert page.locator('.rating-values [aria-pressed=true]').count()==0
                page.locator('#distributionFlag').click();page.wait_for_function("()=>!document.getElementById('bookmarkFlag').disabled")
                page.locator('#bookmarkFlag').click();page.wait_for_function("()=>!document.getElementById('bookmarkFlag').disabled")
                assert s.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(first,))['status']=='selected'
                assert s.db.one('SELECT COUNT(*) AS n FROM bookmarks')['n']==1
                page.locator('#selectionTab').click();page.locator('#selectionStatus').select_option('selected');page.wait_for_selector('.candidate');assert page.locator('#selectionBucket').input_value()=='';page.locator('#selectionBookmarks').select_option('only');page.wait_for_selector('.candidate')
                card=page.locator(f'.candidate[data-generation-id="{first}"]');assert card.locator('select').input_value()=='selected'
                card.locator('select').select_option('not_selected');page.wait_for_timeout(150);page.locator('#reviewTab').click()
                page.wait_for_function("()=>document.getElementById('distributionFlag').getAttribute('aria-pressed')==='false'");assert (page.locator('#bookmarkFlag').get_attribute('aria-pressed')=='true');
                assert page.locator('#reason').input_value()==reason+' 5432'
                page.locator('#autoplayNext').check();page.locator('#play').click();page.wait_for_function("()=>!document.getElementById('audio').paused")
                score(page);page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0");ready(page)
                page.wait_for_function("()=>!document.getElementById('audio').paused")
                review=s.db.one('SELECT * FROM reviews');assert review['generation_id']==first and review['reason']==reason+' 5432'
                assert json.loads(review['flags_json'])['bookmarked'] and not json.loads(review['flags_json'])['distribution']
                second=current(s);assert second!=first and page.locator('#reason').input_value()==''
                assert not (page.locator('#distributionFlag').get_attribute('aria-pressed')=='true') and not (page.locator('#bookmarkFlag').get_attribute('aria-pressed')=='true')
                # Manual mode works at phone width and persists on reload.
                page.set_viewport_size({'width':390,'height':844});page.locator('#autoSubmit').uncheck();assert page.locator('#submitReview').is_visible() and page.locator('#submitReview').is_disabled()
                page.locator('#reason').fill('Manual review\nReady to save');page.locator('#distributionFlag').click();page.wait_for_function("()=>!document.getElementById('bookmarkFlag').disabled");page.locator('#bookmarkFlag').click();page.wait_for_function("()=>!document.getElementById('bookmarkFlag').disabled");score(page,'4444');assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==1
                assert page.locator('#submitReview').is_enabled()
                if args.screenshots:
                    out=Path(args.screenshots);out.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(out/'phone-manual-review.png'),full_page=True)
                # Fail the submission itself; all fields must survive and retry.
                failed={'once':True}
                def fail_review(route):
                    if failed['once']:
                        failed['once']=False;route.fulfill(status=503,content_type='application/json',body='{"detail":"Temporary save failure"}')
                    else:route.continue_()
                page.route('**/api/tracks/*/review',fail_review);page.locator('#submitReview').click();page.wait_for_function("()=>document.getElementById('reviewMessage').textContent.includes('entries are retained')")
                assert page.locator('#reason').input_value()=='Manual review\nReady to save' and page.locator('.rating-values [aria-pressed=true]').count()==4
                assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==1
                assert (page.locator('#distributionFlag').get_attribute('aria-pressed')=='true') and (page.locator('#bookmarkFlag').get_attribute('aria-pressed')=='true')
                page.locator('#submitReview').click();page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0");ready(page)
                assert s.db.one('SELECT reason FROM reviews WHERE generation_id=?',(second,))['reason']=='Manual review\nReady to save'
                assert page.locator('#reason').input_value()==''
                assert not (page.locator('#distributionFlag').get_attribute('aria-pressed')=='true') and not (page.locator('#bookmarkFlag').get_attribute('aria-pressed')=='true')
                page.unroute('**/api/tracks/*/review');page.reload();ready(page);assert not page.locator('#autoSubmit').is_checked()
                # Saved reason is loaded with Back, and blank corrections are valid.
                page.locator('#back').click();page.wait_for_selector('#correctionBadge:not([hidden])');assert page.locator('#reason').input_value()=='Manual review\nReady to save'
                page.locator('#reason').fill('');page.locator('#submitReview').click();page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0");ready(page)
                assert s.db.one('SELECT reason FROM latest_reviews WHERE audio_id=(SELECT audio_id FROM generations WHERE id=?)',(second,))['reason']==''
                # Bookmarks reopen even when they do not match the default bucket.
                page.locator('#selectionTab').click();page.locator('#selectionBookmarks').select_option('only');page.wait_for_selector('.candidate')
                card=page.locator(f'.candidate[data-generation-id="{first}"]');card.get_by_role('button',name='Reviews / Reason').click();page.wait_for_selector('#reviewsDialog[open]');assert reason in page.locator('#reviewDetails').inner_text();page.locator('[data-close=reviewsDialog]').click()
                card.get_by_role('button',name='Reassess',exact=True).click();page.wait_for_selector('#correctionBadge:not([hidden])');assert reason in page.locator('#reason').input_value();page.locator('#skip').click();ready(page)
                # Diagnostics failure must not prevent adding/editing a server folder.
                page.route('**/api/diagnostics',lambda route:route.fulfill(status=503,content_type='application/json',body='{"detail":"Diagnostics unavailable"}'))
                page.locator('#settingsButton').click();page.wait_for_selector('#settingsDialog[open]');page.wait_for_function("()=>!document.getElementById('saveSettings').disabled")
                page.locator('#browseInput').click();page.wait_for_selector('#directoryDialog[open]');page.locator('#directoryPath').fill(str(extra));page.locator('#directoryPathForm button').click();page.wait_for_function("()=>!document.getElementById('useDirectory').disabled");page.locator('#useDirectory').click()
                assert str(extra) in page.locator('#inputFolders').input_value();page.locator('#saveSettings').click();page.wait_for_selector('#settingsDialog[open]',state='hidden')
                assert str(extra) in json.loads(config.read_text())['input_directories']
                assert not errors,errors
                print(json.dumps({'auto_and_manual_submit':'passed','flags_both_views_bookmarks':'passed','reason_keyboard_failure_reset_history':'passed','directory_picker_with_failed_diagnostics':'passed','desktop_mobile':'passed','js_errors':errors}))
                browser.close()
        finally:server.should_exit=True;thread.join(timeout=10)


if __name__=='__main__':main()
