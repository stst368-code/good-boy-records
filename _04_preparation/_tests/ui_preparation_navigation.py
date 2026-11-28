"""New arrivals, draft-safe recording switches and recovery from failed loads."""
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
sys.path[:0]=[str(ROOT/'_02_generation/_tests'),str(ROOT/'_03_review/_generation_review/_tests')]
import test_app_generation
from conftest import complete,archive_all


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True)
    parser.add_argument('--width',type=int,default=1440);args=parser.parse_args()
    fixture=test_app_generation.GenerationAppTests('runTest');fixture.setUp()
    server=None;errors=[]
    try:
        review=fixture.app.state.service;prep=fixture.app.state.preparation
        prep.root=fixture.root;prep.tracks=fixture.root/'_tracks'
        prep.sounds.root=fixture.root/'_04_preparation/_references/_sounds'
        review.archiver.preparation_root=prep.tracks
        def recording(number,ready=True):
            path=fixture.root/f'song-{number}.flac';rate=16000;t=np.arange(rate*8)/rate
            sf.write(path,.1*np.sin(2*np.pi*(220+number*10)*t),rate,subtype='PCM_24')
            lyrics='First recording words' if number==13 else 'Second recording words'
            path.with_suffix('.yaml').write_text(f'song_id: {number}\nvariant_id: 1\ntitle: Recording {number}\nlanguage: en\nlyrics: |\n  {lyrics}\n')
            aid=review.indexer.index(path)
            gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
            review.set_flags(gid,review.default_reviewer_id,distribution=True)
            complete(review,gid)
            if ready:archive_all(review)
            return gid
        first=recording(13)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        deadline=time.monotonic()+15
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox'])
            page=browser.new_page(viewport={'width':args.width,'height':1000})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('#knownReviewers button',has_text='Simon').click()
            legacy=prep.song_projects.get(first)
            legacy['project']['segments'][0]['id']='old-generated-section'
            page.evaluate("value=>localStorage.setItem('gbr-song-draft:'+value.id,JSON.stringify(value.draft))",
                {'id':first,'draft':{'revision_id':None,'source_sha256':legacy['source_sha256'],'project':legacy['project']}})
            def tab(name):
                if args.width<1080:page.locator('#workspaceMenu').click()
                page.locator('#'+name+'Tab').click()
            def loaded(title):
                page.wait_for_function("title=>document.getElementById('songTitle')?.textContent===title&&/Original audio|Saved|Unsaved/.test(document.getElementById('songSaveState')?.textContent)",arg=title)
            tab('preparation');loaded('Recording 13')
            # Closing a clean, never-saved song must not manufacture a dirty
            # draft just because its next default clip has a fresh UUID.
            tab('listen');page.reload();tab('preparation');loaded('Recording 13')
            assert 'Unsaved' not in page.locator('#songSaveState').text_content()
            # Reopening while an earlier open is pending needs a fresh load.
            # Polling must not enable controls before the settings are ready.
            tab('listen');settings=[]
            settings_pattern='**/api/releases/library/settings'
            page.route(settings_pattern,lambda route:settings.append(route))
            tab('preparation')
            deadline=time.monotonic()+5
            while len(settings)<1 and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert len(settings)==1
            page.wait_for_timeout(2200)
            assert page.locator('#prepProject').is_disabled()
            assert page.locator('#prepWorkspace').evaluate('(element)=>element.inert')
            tab('listen');tab('preparation')
            deadline=time.monotonic()+5
            while len(settings)<2 and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert len(settings)==2
            settings[1].fulfill(response=settings[1].fetch())
            page.wait_for_function("()=>!document.getElementById('prepWorkspace').inert&&!document.getElementById('prepProject').disabled")
            settings[0].fulfill(response=settings[0].fetch())
            page.unroute(settings_pattern)
            loaded('Recording 13')
            page.locator('#songPreview').click()
            page.wait_for_function("()=>!document.getElementById('songListen').options[1].disabled",timeout=30000)
            page.locator('#songMaster').click()
            page.wait_for_function("()=>Boolean(document.getElementById('songMasterChoice').value)",timeout=30000)
            first_master=page.locator('#songMasterChoice').input_value()
            page.locator('#songListen').select_option('processed');page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').textContent.includes('Pause')")
            second=recording(14,ready=False)
            page.wait_for_selector(f'#prepProject option[value="{second}"]',state='attached',timeout=10000)
            page.locator('#prepProject').select_option(second)
            page.wait_for_function("()=>document.getElementById('prepWorkspace').hidden")
            archive_all(review);loaded('Recording 14')
            assert page.locator('#songListen').input_value()=='draft'
            with page.expect_request(lambda request:'/api/preparation/audio/'+second in request.url):
                page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').textContent.includes('Pause')")
            page.locator('#songPlay').click()
            # Switching must keep a draft on its own recording without forcing
            # a database version save or accidentally publishing old audio.
            page.locator('#workbenchTab-lyrics').click()
            page.locator('#songLanguage').fill('de');page.locator('#songLanguage').press('Tab')
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Unsaved')")
            page.locator('#prepProject').select_option(first);loaded('Recording 13')
            page.locator('#prepProject').select_option(second);loaded('Recording 14')
            assert page.locator('#songLanguage').input_value()=='de'
            assert page.locator('#songSaveState').text_content().startswith('Unsaved')
            assert not prep.db.one("SELECT 1 FROM processing_tasks WHERE generation_id=? AND kind='song_project'",(second,))
            page.locator('#prepProject').select_option(first);loaded('Recording 13')
            editorial_pattern=f'**/api/preparation/projects/{first}/editorial*';alignment=[]
            page.route(editorial_pattern,lambda route:alignment.append(route))
            page.locator('#songLoadAlignment').click()
            deadline=time.monotonic()+5
            while not alignment and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert alignment
            page.locator('#prepProject').select_option(second);loaded('Recording 14')
            for route in alignment:route.fulfill(response=route.fetch())
            page.unroute(editorial_pattern);page.wait_for_timeout(100)
            assert 'Second' in page.locator('#songLyricText').text_content()
            assert 'First' not in page.locator('#songLyricText').text_content()
            # A stale response for the previous recording cannot replace the
            # selected song, its controls, source or unsaved draft.
            held=[]
            pattern=f'**/api/preparation/projects/{first}/song-project'
            page.route(pattern,lambda route:held.append(route))
            page.locator('#prepProject').select_option(first)
            page.wait_for_function("()=>document.getElementById('songTitle').textContent==='Recording 13'")
            deadline=time.monotonic()+5
            while not held and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert held
            page.locator('#prepProject').select_option(second);loaded('Recording 14')
            for route in held:route.fulfill(response=route.fetch())
            page.unroute(pattern)
            page.wait_for_timeout(200)
            assert page.locator('#songTitle').text_content()=='Recording 14'
            assert page.locator('#songLanguage').input_value()=='de'
            tab('listen')
            assert page.locator('#preparationView').is_hidden()
            # A failed initial list request must not prevent background polling
            # from recovering, and a different page must not lose the draft.
            page.reload();failed=[False];failed_song=[False]
            def fail_once(route):
                if not failed[0]:
                    failed[0]=True;route.fulfill(status=503,content_type='application/json',body='{"detail":"Temporary list failure"}')
                else:route.continue_()
            page.route('**/api/preparation/projects?*',fail_once)
            def fail_song_once(route):
                if not failed_song[0]:
                    failed_song[0]=True;route.fulfill(status=503,content_type='application/json',body='{"detail":"Temporary recording failure"}')
                else:route.continue_()
            page.route(f'**/api/preparation/projects/{first}/song-project',fail_song_once)
            tab('preparation');loaded('Recording 13')
            assert failed[0]
            assert failed_song[0]
            page.locator('#prepProject').select_option(second);loaded('Recording 14')
            assert page.locator('#songLanguage').input_value()=='de'
            page.locator('#prepProject').select_option(first);loaded('Recording 13')
            page.wait_for_function("()=>!document.getElementById('songListen').options[1].disabled",timeout=10000)
            page.locator('#songListen').select_option('processed')
            tab('listen');tab('preparation');loaded('Recording 13')
            assert page.locator('#songListen').input_value()=='draft'
            # Finish stays bound to the clicked recording across selection.
            page.locator('#workbenchTab-release').click()
            page.locator('#prepReleaseLyrics').uncheck()
            finishing=[];requests=[]
            page.route(editorial_pattern,lambda route:finishing.append(route))
            def finish(route):
                requests.append({'url':route.request.url,'body':route.request.post_data_json})
                route.fulfill(content_type='application/json',body='{"id":"queued"}')
            page.route('**/api/releases/projects/*/finish',finish)
            page.locator('#prepComplete').click()
            deadline=time.monotonic()+5
            while not finishing and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert finishing
            page.locator('#prepProject').select_option(second);loaded('Recording 14')
            for route in finishing:route.fulfill(response=route.fetch())
            deadline=time.monotonic()+5
            while not requests and time.monotonic()<deadline:page.wait_for_timeout(50)
            assert len(requests)==1 and f'/projects/{first}/finish' in requests[0]['url'],{
                'requests':requests,'first':first,'second':second,'selection':page.locator('#prepProject').input_value(),
                'notice':page.locator('#prepNotice').text_content(),'song':page.locator('#songSaveState').text_content(),
                'toasts':page.locator('#toast').all_text_contents(),'errors':errors}
            assert requests[0]['body']['master_id']==first_master
            assert page.locator('#prepProject').input_value()==second
            assert page.locator('#preparationView').is_visible()
            assert page.locator('#songLanguage').input_value()=='de'
            page.unroute(editorial_pattern)
            assert not errors,errors
            print(json.dumps({'passed':True,'width':args.width,'checks':['clean reopen','rapid reopen','settings loading','new arrivals','promotion progress','draft-safe switching','stale responses and lyrics','correct playback source','finish target','leave preparation','poll recovery']}))
            browser.close()
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        fixture.doCleanups()


if __name__=='__main__':main()
