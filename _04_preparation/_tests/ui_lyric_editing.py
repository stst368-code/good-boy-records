"""Exercise the lyric correction workflow using real mouse and keyboard input."""
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


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--width',type=int,default=1440);parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args();fixture=test_app_generation.GenerationAppTests('runTest');fixture.setUp();server=None;errors=[]
    try:
        review=fixture.app.state.service;prep=fixture.app.state.preparation;prep.root=fixture.root;prep.tracks=fixture.root/'_tracks'
        prep.sounds.root=fixture.root/'_04_preparation/_references/_sounds';review.archiver.preparation_root=prep.tracks
        source=fixture.root/'song.flac';rate=16000;t=np.arange(rate*8)/rate;sf.write(source,.1*np.sin(2*np.pi*330*t),rate,subtype='PCM_24')
        source.with_suffix('.yaml').write_text('song_id: 13\nvariant_id: 1\ntitle: Built this house\nlanguage: en\nlyrics: built this house with tiny little words today\n')
        aid=review.indexer.index(source);gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
        review.set_flags(gid,review.default_reviewer_id,distribution=True);complete(review,gid);archive_all(review)
        loaded=prep.song_projects.get(gid)
        times=[(.5,1),(1.3,1.6),(2,2.5),(2.6,2.9),(3.2,3.23),(3.4,3.7),(4,4.3),(4.6,7.1)]
        for word,(start,end) in zip(loaded['project']['words'],times):word.update(start=start,end=end,estimated=False)
        prep.song_projects.save(gid,{'parent_revision_id':None,'source_sha256':loaded['source_sha256'],'project':loaded['project']})
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        deadline=time.monotonic()+15
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--autoplay-policy=no-user-gesture-required'])
            page=browser.new_page(viewport={'width':args.width,'height':1050});page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto(f'http://127.0.0.1:{port}');page.locator('#knownReviewers button',has_text='Simon').click()
            def open_tab():
                if args.width<1080:page.locator('#workspaceMenu').click()
                page.locator('#preparationTab').click()
                page.wait_for_function("()=>document.getElementById('songSaveState')?.textContent.startsWith('Saved')&&!document.getElementById('prepWorkspace').inert")
                page.locator('#workbenchTab-lyrics').click();page.wait_for_selector('#workspaceLoading',state='hidden')
            open_tab()
            def draft():
                return page.evaluate("id=>JSON.parse(localStorage.getItem('gbr-song-draft:'+id)).project",gid)
            def point(seconds,y=233):
                stage=page.locator('#songTimeline');stage.scroll_into_view_if_needed();rect=stage.bounding_box()
                return {'x':rect['x']+90+(rect['width']-100)*seconds/8,'y':rect['y']+y*rect['height']/390}
            def word(text):return page.locator('#songLyricText [data-source-word]',has_text=text).filter(has_text=text)
            page.mouse.dblclick(**point(.75))
            page.locator('#songInlineWords').wait_for(state='visible');assert page.locator('#songInlineWords').input_value()=='built'
            assert abs(draft()['words'][0]['end']-1.3)<.001
            page.locator('#songInlineWords').press('Escape')
            page.mouse.dblclick(**point(6,278));page.locator('#songInlineWords').fill('brand');page.locator('#songInlineWords').press('Space');page.locator('#songInlineWords').press_sequentially('new')
            assert page.locator('#songPlay').get_attribute('aria-label')=='Play song'
            page.locator('#songInlineWords').press('Enter')
            assert len(draft()['words'])==10 and [w['text'] for w in draft()['words'][-2:]]==['brand','new']
            buttons=page.locator('#songLyricText [data-source-word]')
            buttons.nth(0).click();buttons.nth(2).click(modifiers=['Control'])
            assert page.locator('#songInspectorTitle').inner_text().startswith('2 words')
            page.locator('#songTimeline').focus();page.keyboard.press('ArrowRight')
            assert abs(draft()['words'][0]['start']-.55)<.001 and abs(draft()['words'][2]['start']-2.05)<.001
            assert draft()['words'][1]['start']==1.3
            buttons.nth(0).click();buttons.nth(3).click(modifiers=['Shift'])
            assert page.locator('#songInspectorTitle').inner_text().startswith('4 words')
            page.locator('#songInspector [data-word-length]').fill('.2');page.locator('[data-object=sameWordLength]').click()
            page.locator('[data-object=packWords]').click()
            assert all(abs(w['end']-w['start']-.2)<.001 for w in draft()['words'][:4])
            assert all(abs(a['end']-b['start'])<.001 for a,b in zip(draft()['words'][:3],draft()['words'][1:4]))
            # Resize one selected word: the other selected words get its length.
            original=draft()['words'][0];start=point(original['end']-.015)
            page.mouse.move(**start);page.mouse.down();page.mouse.move(start['x']+(page.locator('#songTimeline').bounding_box()['width']-100)*.35/8,start['y'],steps=8);page.mouse.up()
            lengths=[w['end']-w['start'] for w in draft()['words'][:4]]
            assert max(lengths)-min(lengths)<.001 and lengths[0]>.4
            buttons.nth(0).click();page.locator('.song-emphasis summary').click()
            page.locator('[data-word-parts]').fill('b|uil|t');page.locator('[data-object=splitParts]').click()
            page.locator('.song-emphasis summary').click()
            for field,value in zip(page.locator('[data-part-weight]').all(),['1','8','1']):field.fill(value)
            page.locator('[data-object=weightParts]').click()
            assert abs(draft()['words'][0]['parts'][1]['end']-.9)<.0001
            # A narrow word remains available in the readable phrase ribbon.
            buttons.nth(4).click();assert page.locator('#songWordRibbon').get_by_role('button',name='tiny',exact=True).is_visible()
            assert page.locator('#songWordRibbon button').first.evaluate("e=>parseFloat(getComputedStyle(e).fontSize)")>=14
            # Pause near the beginning of 'today': select the previous word.
            page.mouse.click(**point(4.65,12));page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').getAttribute('aria-label')==='Pause song'")
            page.locator('#songTimeline').focus();page.keyboard.press('Space')
            page.wait_for_function("()=>document.getElementById('songPlay').getAttribute('aria-label')==='Play song'")
            assert page.locator('#songInspector [data-key=text]').input_value()=='words'
            page.mouse.click(**point(5.5,12));page.locator('#songPlay').click()
            page.wait_for_function("()=>document.getElementById('songPlay').getAttribute('aria-label')==='Pause song'")
            page.locator('#songTimeline').focus();page.keyboard.press('Space')
            page.wait_for_function("()=>document.getElementById('songPlay').getAttribute('aria-label')==='Play song'")
            assert page.locator('#songInspector [data-key=text]').input_value()=='today'
            # Follow is restored after zooming; it can also be switched off.
            page.mouse.click(**point(6.9,12))
            for _ in range(3):page.locator('#songZoomIn').click()
            page.locator('#songPlay').click();page.wait_for_function("()=>Number(document.getElementById('songScroll').value)>6",timeout=5000)
            page.locator('#songPlay').click()
            page.locator('#songFollow').click();page.locator('#songScroll').focus();page.keyboard.press('Home')
            page.locator('#songPlay').click();page.wait_for_timeout(200)
            assert float(page.locator('#songScroll').input_value())==0
            page.locator('#songPlay').click();page.locator('#songFit').click()
            page.locator('#songSave').click();page.wait_for_function("()=>document.getElementById('songSaveState').textContent.startsWith('Saved')")
            saved=prep.song_projects.get(gid)['project'];assert len(saved['words'])==10 and saved['words'][0]['parts'][1]['text']=='uil'
            page.reload();open_tab();assert len(prep.song_projects.get(gid)['project']['words'])==10
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
            if args.screenshots:
                page.locator('#songLyricText [data-source-word]').first.click()
                args.screenshots.mkdir(parents=True,exist_ok=True);page.screenshot(path=str(args.screenshots/f'lyric-editing-{args.width}.png'),full_page=True)
            assert not errors,errors
            print(json.dumps({'passed':True,'width':args.width,'checks':['double click extends','direct lyric entry','Ctrl and Shift groups','group nudge','pack end to end','group edge resize','emphasis parts','readable short words','Space reaction selection','playhead follow','saved versions']}))
            browser.close()
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        fixture.doCleanups()


if __name__=='__main__':main()
