"""Base registration, section mixing and responsive Writing tabs in a real browser."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/'_01_writing/_tests'))
import test_authoring
import test_writing
from _gbr_writing.authoring import compose


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    fixture=test_writing.app.__wrapped__();f,w=next(fixture);test_authoring.setup(f)
    (f.root/'_01_writing/_references/_minimax_templates/base.txt').write_text(compose({'vocal_style':'A gentle reference delivery','basic':'Driving guitars'}))
    server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18773,log_level='error',loop='_review.server_loop:create_loop'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            page=browser.new_page(viewport={'width':1920,'height':1080});errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:18773');page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#writingTab').click()
            page.wait_for_selector('#baseTitle');assert page.locator('#writingBase').is_visible() and not page.locator('#writingAssistant').is_visible()
            page.locator('#baseTitle').fill('MY ÉCHO Song');page.locator('#baseLyrics').fill('[VERSE]\n\nÉCHO SING\n')
            page.locator('#baseAlbum').fill('First ALBUM');page.locator('#basePosition').fill('3');page.locator('#baseSave').click()
            page.wait_for_function("()=>document.getElementById('baseStatus').textContent.startsWith('Saved #')")
            assert page.locator('#baseTitle').input_value()=='my-écho-song'
            assert page.locator('#baseLyrics').input_value()=='[verse]\nécho sing'
            page.locator('#baseSave').click();page.wait_for_function("()=>!document.getElementById('baseSave').disabled")
            assert len(w.review.db.all('SELECT * FROM catalogue_songs'))==1
            page.locator('#baseSource').select_option('_yaml/my-écho-song.yaml')
            page.wait_for_function("()=>document.getElementById('baseSave').textContent==='Save as new variant'")
            page.locator('#baseLyrics').fill('[VERSE]\nA NEW VERSION');page.locator('#baseSave').click()
            page.wait_for_function("()=>document.getElementById('baseStatus').textContent.includes('variant 2')")
            assert (f.built/'_yaml/my-écho-song__v2.yaml').is_file()
            page.locator('#writingMode1').click();page.locator('#captionCluster').fill('Rock');page.locator('#captionVersion').fill('Punk')
            pick=page.get_by_label('Vocal Style reference',exact=True);pick.focus()
            page.wait_for_function("()=>document.querySelector('[aria-label=\"Vocal Style reference\"]').options.length>1")
            pick.select_option('0');assert page.locator('#caption-vocal_style').input_value()=='A gentle reference delivery'
            page.locator('#caption-vocal_style').fill('A gentle reference delivery, edited');page.locator('#captionSong').select_option('900000002');page.locator('#captionSave').click()
            page.wait_for_function("()=>document.getElementById('captionStatus').textContent.startsWith('Saved custom caption:')")
            assert 'rock-punk-2' in page.locator('#captionStatus').text_content()
            custom=f.root/'_01_writing/_references/_custom_templates/rock-punk-2.txt'
            assert 'edited' in custom.read_text() and f.source.read_bytes()==f.original
            for width,height in [(1440,900),(3840,2160),(390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),width
                for index in (0,1,2):
                    page.locator('#writingMode'+str(index)).click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth'),(width,index)
            assert not errors,errors
            browser.close();print(json.dumps({'base_and_variant':'passed','editable_caption_reference_and_link':'passed','1440_3840_390_layout':'passed','js_errors':errors}))
    finally:
        server.should_exit=True;thread.join(timeout=10)
        try:next(fixture)
        except StopIteration:pass


if __name__=='__main__':main()
