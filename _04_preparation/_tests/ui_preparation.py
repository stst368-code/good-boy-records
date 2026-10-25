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
                for width in [1440,3840,390,320]:
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
                    page.locator('#prepTimelinePlay').click()
                    page.wait_for_function("()=>document.getElementById('prepRaw').paused&&document.getElementById('prepProcessed').paused")
                    page.locator('#prepTimelinePlay').click()
                    page.wait_for_function("()=>!document.getElementById('prepRaw').paused")
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
                    page.locator('[data-mix=gain_db]').fill('-6')
                    page.locator('#prepMaster').click()
                    page.wait_for_function("()=>document.getElementById('prepHistory').textContent.includes('mix master · done')",timeout=25000)
                    page.locator('#prepVisuals').click()
                    page.wait_for_function("()=>document.getElementById('prepHistory').textContent.includes('visuals · done')",timeout=25000)
                    assert page.locator('#prepHistory a',has_text='visual_features').count()
                    page.wait_for_function("()=>document.getElementById('prepTimelineStatus').textContent.includes('Visual features ready')")
                    page.locator('#prepSensitivity').fill('2')
                    page.locator('#prepAddCue').click()
                    cue=page.locator('.prep-cue').first
                    cue.locator('[data-field=start]').fill('0.1');cue.locator('[data-field=start]').dispatch_event('change')
                    cue.locator('[data-field=end]').fill('2');cue.locator('[data-field=end]').dispatch_event('change')
                    page.locator('#prepSaveLights').click()
                    page.wait_for_function("()=>document.getElementById('prepTimelineStatus').textContent.includes('Lighting saved')")
                    assert prep.editorial(gid)['sensitivity']==2
                    page.locator('#prepRaw').evaluate('async a=>{a.currentTime=.5;await a.play()}')
                    page.wait_for_function("()=>Number(document.getElementById('prepStar').style.opacity)>.15",timeout=6000)
                    page.locator('#prepRaw').evaluate('a=>a.pause()')
                    page.locator('#prepTimelinePlay').click()
                    page.wait_for_function("()=>!document.getElementById('prepRaw').paused")
                    page.wait_for_function("()=>document.getElementById('prepTimelinePlay').textContent==='Pause'")
                    page.locator('#prepTimelinePlay').click()
                    page.wait_for_function("()=>document.getElementById('prepRaw').paused")
                    page.locator('#prepTimelineSpeed').select_option('0.75')
                    assert page.locator('#prepRaw').evaluate('a=>a.playbackRate')==.75
                    page.locator('#prepTimelineScrub').fill('1')
                    assert page.locator('#prepRaw').evaluate('a=>a.currentTime')==1
                    page.locator('#prepTimelineBack').click()
                    assert page.locator('#prepRaw').evaluate('a=>a.currentTime')==0
                    assert page.locator('.prep-word-tag').count()==6
                    page.locator('#prepLyricWords [data-word]').first.click()
                    assert not page.locator('#prepWordStart').is_visible()
                    initial=float(page.locator('#prepWordStart').input_value())
                    stage=page.locator('#prepWordTimeline').bounding_box();scale=stage['width']/4
                    def drag(locator,delta):
                        locator.evaluate("el=>el.scrollIntoView({block:'center',inline:'nearest'})")
                        box=locator.bounding_box();x=box['x']+box['width']/2;y=box['y']+box['height']/2
                        hit=page.evaluate('p=>{const e=document.elementFromPoint(p.x,p.y);return {className:e?.className,word:e?.closest("[data-word]")?.dataset.word}}',{'x':x,'y':y})
                        page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+delta,y,steps=5);page.mouse.up()
                        return {'box':box,'hit':hit,'delta':delta}
                    drag_info=drag(page.locator('.prep-word-tag').first,.2*scale)
                    moved=float(page.locator('#prepWordStart').input_value())
                    if moved<=initial:page.screenshot(path='/tmp/gbr-lyric-drag-failure.png')
                    assert moved>initial,{'initial':initial,'moved':moved,'drag':drag_info,'errors':errors}
                    page.locator('#prepUndoLyrics').click()
                    assert float(page.locator('#prepWordStart').input_value())==initial
                    page.locator('#prepRedoLyrics').click()
                    assert float(page.locator('#prepWordStart').input_value())==moved
                    drag(page.locator('.prep-word-tag').first.locator('[data-edge=start]'),-.05*scale)
                    assert float(page.locator('#prepWordStart').input_value())<moved
                    old_end=float(page.locator('#prepWordEnd').input_value())
                    drag(page.locator('.prep-word-tag').first.locator('[data-edge=end]'),.1*scale)
                    assert float(page.locator('#prepWordEnd').input_value())>old_end
                    assert not page.locator('#prepWordConfirmed').is_checked()
                    page.locator('.prep-word-tag').first.focus();page.keyboard.press('ArrowRight');page.keyboard.press('ArrowRight')
                    assert page.locator('.prep-word-tag').first.evaluate('el=>el===document.activeElement')
                    # Cancelling a pointer gesture must not commit its draft.
                    before_cancel=page.locator('#prepWordStart').input_value()
                    tag=page.locator('.prep-word-tag').first;tag.scroll_into_view_if_needed();box=tag.bounding_box();x=box['x']+box['width']/2;y=box['y']+box['height']/2
                    page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+20,y,steps=3)
                    tag.dispatch_event('pointercancel',{'pointerId':1,'bubbles':True});page.mouse.up()
                    assert page.locator('#prepWordStart').input_value()==before_cancel
                    page.locator('#prepApproveLyrics').click()
                    page.wait_for_function("()=>document.getElementById('prepTimelineStatus').textContent.includes('Lyric revision saved')")
                    saved=prep.editorial(gid)
                    assert saved['approved'] and saved['words'][0]['start']>initial
                    assert all(not word['confirmed'] for word in saved['words'])
                    assert any(word['estimated'] for word in saved['words'])
                    with page.expect_response(lambda response:'/editorial' in response.url and response.request.method=='GET'):
                        page.locator('#prepReloadTimeline').click()
                    page.wait_for_function("()=>document.getElementById('prepApproveLyrics').textContent==='Approved'")
                    assert page.locator('.prep-word-tag.estimated').count()>0
                    page.locator('#prepLyricWords').scroll_into_view_if_needed()
                    transport=page.locator('#prepTimelinePlay').bounding_box()
                    assert transport['y']>=0 and transport['y']+transport['height']<=950
                    page.locator('.prep-timeline-toolbar').scroll_into_view_if_needed()
                    page.screenshot(path=f'/tmp/gbr-lyric-timeline-{width}.png')
                    page.locator('#prepAudioSource').select_option(master_id:=(next(a['id'] for t in prep.tasks(gid) for a in t['artifacts'] if a['kind']=='master_candidate')))
                    page.wait_for_function("()=>document.getElementById('prepTimelineStatus').textContent.includes('Calculate visual features')")
                    assert page.locator('.prep-word-tag.estimated').count()==6
                    assert page.locator('#prepApproveLyrics').get_attribute('aria-pressed')=='false'
                    page.locator('#prepAudioSource').select_option('')
                    page.wait_for_function("()=>document.getElementById('prepApproveLyrics').getAttribute('aria-pressed')==='true'")

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
