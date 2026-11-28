"""Exercise actual import, edit, audition, save and render APIs in a browser."""
import argparse
import io
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
from conftest import complete,archive_all
from _gbr_preparation.service import digest


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True)
    parser.add_argument('--width',type=int,default=1440);parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    fixture=test_app_generation.GenerationAppTests('runTest');fixture.setUp()
    server=None;errors=[];failures=[]
    try:
        review=fixture.app.state.service;prep=fixture.app.state.preparation
        prep.root=fixture.root;prep.tracks=fixture.root/'_tracks';review.archiver.preparation_root=prep.tracks
        prep.sounds.root=fixture.root/'_04_preparation/_references/_sounds'
        audio=fixture.root/'song.flac';t=np.arange(16000*8)/16000
        sf.write(audio,.16*np.sin(2*np.pi*330*t),16000,subtype='PCM_24')
        audio.with_suffix('.yaml').write_text('song_id: 13\nvariant_id: 1\ntitle: One timeline\ncluster: Rock\nstyle: Punk\nlyrics: |\n  Keep the lyric timing\n  Listen to this section\n')
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
            page=browser.new_page(viewport={'width':args.width,'height':1050})
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.on('response',lambda response:failures.append((response.status,response.url)) if response.status>=500 else None)
            page.goto(f'http://127.0.0.1:{port}')
            page.locator('#knownReviewers button',has_text='Simon').click()
            def open_tab():
                if args.width<1080:page.locator('#workspaceMenu').click()
                page.locator('#preparationTab').click()
                try:page.wait_for_function("()=>['Original audio','Saved','Unsaved'].some(text=>document.getElementById('songSaveState')?.textContent.startsWith(text))")
                except Exception:
                    page.screenshot(path='/tmp/gbr-workbench-failure.png',full_page=True)
                    print(json.dumps({'errors':errors,'toast':page.locator('#toast').all_text_contents(),'body':page.locator('#preparationView').inner_text()[:1000]}),flush=True)
                    raise
                page.wait_for_function("()=>!document.getElementById('prepWorkspace').inert&&Boolean(document.querySelector('#songSoundList [data-clip]'))")
                page.wait_for_selector('#workspaceLoading',state='hidden')
            open_tab()
            # Tools share one dock. Changing tools never replaces or moves the
            # timeline, starts playback, or exposes another competing canvas.
            initial_bounds=page.locator('#songTimeline').bounding_box()
            for pane in ['effects','lyrics','release','queue','versions','details','sounds']:
                page.locator('#workbenchTab-'+pane).click()
                assert page.locator('#workbenchTab-'+pane).get_attribute('aria-selected')=='true'
                assert page.locator('#preparationView [role=tabpanel]:visible').count()==1
                assert page.locator('#preparationView canvas:visible').count()==1
                assert page.locator('#songTimeline').bounding_box()==initial_bounds
            page.locator('#workbenchTab-sounds').focus();page.keyboard.press('ArrowRight')
            assert page.locator('#workbenchTab-effects').get_attribute('aria-selected')=='true'
            page.keyboard.press('Home')
            assert page.locator('#workbenchTab-sounds').get_attribute('aria-selected')=='true'
            # Start/stop change selected music, rather than inserting a stock
            # noise clip. Both operations remain undoable on the same timeline.
            page.locator('#songRangeStart').fill('.25');page.locator('#songRangeStart').press('Tab')
            page.locator('#songRangeEnd').fill('1.25');page.locator('#songRangeEnd').press('Tab')
            page.get_by_role('button',name='Apply record start to selected music',exact=True).click()
            draft=page.evaluate("id=>JSON.parse(localStorage.getItem('gbr-song-draft:'+id)).project",gid)
            assert any(section.get('mode')=='start' for section in draft['segments']) and not draft['sounds']
            page.locator('#songUndo').click()
            page.get_by_role('button',name='Apply record stop to selected music',exact=True).click()
            draft=page.evaluate("id=>JSON.parse(localStorage.getItem('gbr-song-draft:'+id)).project",gid)
            assert any(section.get('mode')=='stop' for section in draft['segments']) and not draft['sounds']
            page.locator('#songUndo').click()
            page.locator('#songSoundList [data-clip]').first.wait_for()
            assert page.locator('#songSoundList [data-clip]').count()==6
            assert not page.locator('#mixWave').is_visible()
            assert not page.locator('#prepRaw').is_visible()
            # One playhead for all editing; insert a needle break at exactly two seconds.
            stage=page.locator('#songTimeline');stage.scroll_into_view_if_needed();rect=stage.bounding_box()
            page.mouse.click(rect['x']+90+(rect['width']-100)*.25,rect['y']+12)
            page.locator('#songNeedle').click()
            page.locator('#songSave').click()
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            saved=prep.song_projects.get(gid);doc=saved['project']
            assert len(doc['segments'])==3 and len(doc['sounds'])==2
            assert saved['duration']==10
            # Upload a user sound and add it without replacing the previous clips.
            raw=io.BytesIO();sf.write(raw,np.full((8000,2),.04),16000,format='WAV',subtype='PCM_24')
            page.locator('#songImport').set_input_files({'name':'Personal hit.wav','mimeType':'audio/wav','buffer':raw.getvalue()})
            page.wait_for_function("()=>document.getElementById('songImportStatus').textContent.includes('added')")
            page.locator('#songSoundSearch').fill('Personal hit')
            page.get_by_role('button',name='Add Personal hit',exact=True).click()
            page.locator('#songInspector [data-key=duck_db]').fill('-9')
            page.locator('#songInspector [data-key=duck_db]').press('Tab')
            page.locator('#songInspector [data-key=position]').fill('2.5')
            page.locator('#songInspector [data-key=position]').press('Tab')
            page.locator('#songUndo').click();page.locator('#songRedo').click()
            # Reloading an unsaved song recovers the arrangement without losing clips.
            page.reload();open_tab()
            assert page.locator('#songSaveState').text_content().startswith('Unsaved')
            assert page.locator('#songSoundList [data-clip]').count()==7
            # The source lyric editor keeps drag corrections and omitted-word recovery.
            page.locator('#workbenchTab-lyrics').click()
            page.locator('#songLyricText button').first.click()
            initial=float(page.locator('#songInspector [data-key=start]').input_value())
            page.locator('#songTimeline').focus();page.keyboard.press('ArrowRight')
            assert float(page.locator('#songInspector [data-key=start]').input_value())>initial
            page.locator('#songInspector [data-key=omitted]').check()
            page.locator('#songLyricText button').first.click()
            assert page.locator('#songInspector [data-key=omitted]').is_checked()
            page.locator('#songInspector [data-key=omitted]').uncheck()
            page.locator('#songSpeed').select_option('.75');page.locator('#songSpeed').select_option('1')
            # Existing preset controls now take their range from this timeline.
            page.locator('#songRangeStart').fill('1');page.locator('#songRangeStart').press('Tab')
            page.locator('#songRangeEnd').fill('5');page.locator('#songRangeEnd').press('Tab')
            page.locator('#workbenchTab-effects').click()
            page.locator('[data-preset=parametric_eq]').click()
            effect=page.locator('#prepEffectChain li').first
            effect.locator('[data-parameter=frequency]').fill('330');effect.locator('[data-parameter=frequency]').press('Tab')
            effect.locator('[data-parameter=gain_db]').fill('4');effect.locator('[data-parameter=gain_db]').press('Tab')
            effect.locator('[data-scope=selection]').click()
            effect.locator('.studio-automation summary').click();effect.locator('[data-curve=rise]').click()
            page.locator('#workbenchTab-lyrics').click()
            page.locator('#songSensitivity').fill('1.5');page.locator('#songSensitivity').press('Tab')
            page.locator('#songApprove').click()
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            page.locator('#songPreview').click()
            page.wait_for_function("()=>!document.getElementById('songListen').options[1].disabled",timeout=30000)
            page.locator('#songListen').select_option('processed');page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').textContent.includes('Pause')")
            page.locator('#songPlay').click()
            page.locator('#songListen').select_option('draft');page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').textContent.includes('Pause')")
            page.locator('#songPlay').click()
            page.locator('#songMaster').click()
            page.wait_for_function("()=>document.getElementById('songRenderState').textContent.includes('selected for release')",timeout=30000)
            saved=prep.song_projects.get(gid);assert len(saved['project']['sounds'])==3
            assert saved['project']['sounds'][-1]['duck_db']==-9
            assert abs(saved['project']['sounds'][-1]['offset']-.5)<.001
            master=page.locator('#songMasterChoice').input_value();assert master
            editorial=prep.editorial(gid,master)
            assert editorial['duration']==10 and editorial['approved']
            assert editorial['sensitivity']==1.5
            assert editorial['words'][0]['start']>initial
            assert editorial['words'][3]['start']>doc['words'][3]['start']
            assert digest(source)==original
            old_audio=prep.artifact(master)
            # A later correction reuses that master and updates its lyric sidecar.
            page.locator('#songLyricText button').first.click()
            page.locator('#songInspector [data-key=text]').fill('Kept')
            page.locator('#songInspector [data-key=text]').press('Tab')
            page.locator('#songApprove').click()
            page.wait_for_function("old=>document.getElementById('songMasterChoice').value&&document.getElementById('songMasterChoice').value!==old",arg=master)
            master=page.locator('#songMasterChoice').input_value()
            assert prep.artifact(master)==old_audio
            assert prep.editorial(gid,master)['words'][0]['text']=='Kept'
            page.reload();open_tab()
            assert page.locator('#prepEffectChain [data-parameter=gain_db]').input_value()=='4'
            assert page.locator('#songLyricState').text_content()=='approved'
            assert len(prep.song_projects.get(gid)['project']['sounds'])==3
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            if args.screenshots:
                page.wait_for_selector('#songTimeline[data-waveform=ready]')
                page.wait_for_selector('#workspaceLoading',state='hidden')
                args.screenshots.mkdir(parents=True,exist_ok=True);stage=page.locator('#songTimeline');stage.scroll_into_view_if_needed()
                page.screenshot(path=str(args.screenshots/f'song-studio-{args.width}.png'),full_page=True)
            assert not errors,errors
            assert not failures,failures
            print(json.dumps({'passed':True,'width':args.width,'checks':['shared timeline','needle break','persistent sound import','clip ducking','undo redo','effect range','automation','draft and processed playback','approved remapped lyrics','master render','reload','original preserved']}))
            browser.close()
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        fixture.doCleanups()


if __name__=='__main__':main()
