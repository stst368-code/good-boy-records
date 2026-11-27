"""Completed worker timings, protected corrections and word audition in the app."""
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
from conftest import complete,archive_all
from test_song_alignment import alignment_result


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
        audio=fixture.root/'song.flac';rate=16000;t=np.arange(rate*8)/rate
        sf.write(audio,.1*np.sin(2*np.pi*330*t),rate,subtype='PCM_24')
        audio.with_suffix('.yaml').write_text('song_id: 13\nvariant_id: 1\ntitle: Alignment test\nlanguage: en\nlyrics: Keep these lyric words\n')
        aid=review.indexer.index(audio);gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
        review.set_flags(gid,review.default_reviewer_id,distribution=True);complete(review,gid);archive_all(review)
        loaded=prep.song_projects.get(gid)
        prep.song_projects.save(gid,{'parent_revision_id':None,'source_sha256':loaded['source_sha256'],'project':loaded['project']})
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
            def open_tab():
                if args.width<1080:page.locator('#workspaceMenu').click()
                page.locator('#preparationTab').click()
                page.wait_for_function("()=>document.getElementById('songTitle')?.textContent==='Alignment test'&&!document.getElementById('prepWorkspace').inert")
                if not page.locator('.song-lyrics').evaluate('(element)=>element.open'):page.locator('.song-lyrics summary').click()
            open_tab()
            # The alignment can finish after this song version was saved.
            page.locator('#songSensitivity').fill('1.5');page.locator('#songSensitivity').press('Tab')
            alignment_result(prep,gid,[.75,2.25,4.25,6.25])
            page.wait_for_function("()=>document.getElementById('songAlignmentStatus').textContent.includes('loaded')",timeout=10000)
            page.locator('#songLyricText button').first.click()
            assert float(page.locator('#songInspector [data-key=start]').input_value())==.75
            assert page.locator('#songSensitivity').input_value()=='1.5'
            page.locator('#songSave').click()
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            # An edited token keeps its spelling and a manually omitted word.
            page.locator('#songInspector [data-key=text]').fill('Kept');page.locator('#songInspector [data-key=text]').press('Tab')
            page.locator('#songInspector [data-key=start]').fill('.8');page.locator('#songInspector [data-key=start]').press('Tab')
            page.locator('#songInspector [data-key=end]').fill('1.5');page.locator('#songInspector [data-key=end]').press('Tab')
            page.locator('#songInspector [data-object=listenWord]').click()
            page.wait_for_function("()=>document.getElementById('songPlay').textContent.includes('Pause')")
            page.locator('#songPlay').click()
            page.locator('#songLyricText button').last.click();page.locator('#songInspector [data-key=omitted]').check()
            page.locator('#songSave').click()
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            alignment_result(prep,gid,[1.25,2.5,4.5,6.5])
            page.wait_for_function("()=>document.getElementById('songAlignmentStatus').textContent.includes('newer alignment available')",timeout=10000)
            page.locator('#songLyricText button').first.click()
            assert float(page.locator('#songInspector [data-key=start]').input_value())==.8
            page.locator('#songLoadAlignment').click()
            page.wait_for_function("()=>document.querySelector('#songInspector [data-key=start]')?.value==='1.25'")
            assert page.locator('#songInspector [data-key=text]').input_value()=='Kept'
            page.locator('#songLyricText button').last.click()
            assert page.locator('#songInspector [data-key=omitted]').is_checked()
            page.locator('#songApprove').click()
            page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            page.reload();open_tab()
            assert page.locator('#songLyricState').text_content()=='approved'
            doc=prep.song_projects.get(gid)['project']
            assert doc['words'][0]['text']=='Kept' and doc['words'][0]['start']==1.25
            assert doc['words'][-1]['omitted'] and doc['lighting']['sensitivity']==1.5
            # An explicit rerun can replace already approved timings, provided
            # the user has not edited the words while the worker is running.
            def rerun(route):
                body=route.request.post_data_json
                assert body['kind']=='lyric_alignment' and body['language']=='en' and body['force']
                identifier=alignment_result(prep,gid,[1.5,2.75,4.75,6.75])
                route.fulfill(content_type='application/json',body=json.dumps({'id':identifier,'status':'queued'}))
            page.route(f'**/api/preparation/projects/{gid}/jobs',rerun)
            page.locator('#songAlign').click()
            page.wait_for_function("()=>document.getElementById('songAlignmentStatus').textContent.includes('loaded')",timeout=10000)
            page.locator('#songLyricText button').first.click()
            assert float(page.locator('#songInspector [data-key=start]').input_value())==1.5
            assert page.locator('#songInspector [data-key=text]').input_value()=='Kept'
            assert page.locator('#songLyricState').text_content()=='not approved'
            failed=alignment_result(prep,gid,[1.6,2.8,4.8,6.8])
            prep.db.execute("UPDATE processing_tasks SET status='failed',error='Worker test failure' WHERE id=?",(failed,))
            page.wait_for_function("()=>document.getElementById('songAlignmentStatus').textContent.includes('Open Processing queue')",timeout=10000)
            assert not errors,errors
            print(json.dumps({'passed':True,'width':args.width,'checks':['completed alignment loads','saved song updates','draft mix retained','saved manual corrections protected','word audition after editing','approval survives reload','explicit rerun','failed task guidance']}))
            browser.close()
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        fixture.doCleanups()


if __name__=='__main__':main()
