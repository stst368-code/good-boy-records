"""RunPod account controls and caption auditions in the actual browser application."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
from unittest.mock import patch
import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
for relative in ('_01_writing/_tests','_02_generation/_tests'):
    sys.path.insert(0,str(ROOT/relative))
import test_writing
from test_runpod_discovery import pod
from test_comfy_execution import server
from _gbr_generation.credentials import AccountCredential
from _gbr_generation.runpod_account import AccountAPI
from _gbr_writing.authoring import compose


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    fixture=test_writing.app.__wrapped__();f,w=next(fixture);g=f.generation;vault={}
    class Vault:
        def get_password(self,service,name):return vault.get(name)
        def set_password(self,service,name,key):vault[name]=key
    caption=compose({'vocal_style':'Gentle lead vocals'});(f.root/'_01_writing/_references/_minimax_templates/base.txt').write_text(caption)
    audio=f.root/'example.flac';sf.write(audio,np.sin(np.arange(16000)*.03),16000,subtype='PCM_24');audio.with_suffix('.yaml').write_text('title: Listening example\ncaption: '+json.dumps(caption)+'\nlyrics: words\n')
    service=w.review;aid=service.indexer.index(audio);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id'];rid=service.default_reviewer_id
    with service.db.transaction() as db:
        session=service.queues.session(db,rid);db.execute('INSERT INTO queue_reservations VALUES(?,?,?,?,?,?)',(aid,rid,gid,0,service.queues.clock(),service.queues.clock()+180));db.execute('UPDATE sessions SET current_generation_id=? WHERE id=?',(gid,session['id']))
    service.review(gid,dict.fromkeys(['audio_quality','composition','consistency','realism'],5),'quick')
    http=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18774,log_level='error',loop='_review.server_loop:create_loop'))
    thread=threading.Thread(target=http.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not http.started and time.monotonic()<deadline:time.sleep(.05)
        assert http.started
        with server() as (url,state),patch.object(AccountCredential,'vault',lambda self:Vault()),sync_playwright() as playwright:
            state['attempt']='inspection-placeholder';rows=[pod('music'),pod('stopped',status='EXITED'),pod('unknown')]
            original=g.workers.query
            def query(entry,**kwargs):
                if entry['id']=='runpod|unknown':return {'online':True,'compatible':False,'error':'No MiniMax model loaders','checked_at':time.time()}
                return original(entry,**kwargs)
            with patch.object(AccountAPI,'pods',lambda *args:rows),patch.object(AccountAPI,'request',lambda self,path:next(p for p in rows if p['id']==path.rsplit('/',1)[-1])),patch('_gbr_generation.runpod_account.endpoints',lambda *args:[{'backend':'comfyui','address':url}]),patch.object(g.workers,'query',query):
                browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
                page=browser.new_page(viewport={'width':1440,'height':1000});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                page.goto('http://127.0.0.1:18774');page.locator('#knownReviewers button',has_text='Simon').click()
                page.locator('#settingsButton').click();page.locator('#openWorkerSettings').click()
                page.locator('#runpodAccount').evaluate('(node)=>node.open=true');page.wait_for_function("()=>!document.getElementById('runpodKey').disabled");page.locator('#runpodEnabled').uncheck();page.locator('#runpodKey').fill('account-secret-fixture');page.locator('#runpodTest').click()
                page.wait_for_function("()=>document.getElementById('runpodMessage').textContent.includes('Discovery complete')")
                assert page.locator('#runpodRows>tr').count()==3
                assert g.runpod.settings()['enabled'] is False
                assert 'account-secret-fixture' not in page.locator('#runpodProgress').text_content()
                assert page.locator('#runpodKey').input_value()==''
                assert page.get_by_label('Auto allocate pod music',exact=True).is_checked()
                page.get_by_label('Auto allocate pod music',exact=True).uncheck()
                page.wait_for_function("()=>!document.querySelector('[aria-label=\"Auto allocate pod music\"]').checked")
                for width,height in [(1440,1000),(3840,2160),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                page.set_viewport_size({'width':1440,'height':1000});page.screenshot(path='/tmp/gbr-runpod-account.png',full_page=True)
                page.locator('#workersClose').click();page.locator('#writingTab').click();page.locator('#writingMode1').click()
                pick=page.get_by_label('Vocal Style reference',exact=True);pick.focus();page.wait_for_function("()=>document.querySelector('[aria-label=\"Vocal Style reference\"]').options.length>1");pick.select_option('0')
                page.wait_for_selector('.caption-audition-row audio');assert '5.0/5' in page.locator('.caption-audition-row').text_content()
                player=page.locator('.caption-audition-row audio');player.evaluate('(audio)=>audio.play()');page.wait_for_function("()=>document.querySelector('.caption-audition-row audio').readyState>=2")
                for width,height in [(1440,1000),(3840,2160),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                page.locator('#caption-vocal_style').fill('Edited delivery');assert page.locator('.caption-audition-row').count()==0
                assert not errors,errors;browser.close()
                print(json.dumps({'account_configuration_and_discovery':'passed','allocation_controls':'passed','rated_caption_audio_playback':'passed','1440_3840_390_layout':'passed','js_errors':errors}))
    finally:
        http.should_exit=True;thread.join(timeout=10)
        try:next(fixture)
        except StopIteration:pass


if __name__=='__main__':main()
