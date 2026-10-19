"""Browser writing/settings/publication and fluid workflow layout with a synthetic HTTP LLM."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
for folder in ('_01_writing/_tests','_01_writing','_02_generation/_tests','_03_review/_generation_review'):
    sys.path.insert(0,str(ROOT/folder))
import test_writing
from _gbr_generation.yaml_document import YamlDocument


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser');args=parser.parse_args()
    fixture=test_writing.app.__wrapped__();f,w=next(fixture)
    server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18772,log_level='error',loop='_review.server_loop:create_loop'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with test_writing.worker() as (endpoint,calls),sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            page=browser.new_page(viewport={'width':1920,'height':1080});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:18772');page.locator('#knownReviewers button',has_text='Simon').click()
            assert page.locator('nav .tab').all_text_contents()==['Writing','YAML editor','Generation','Review','Selection','Preparation']
            assert page.locator('main').bounding_box()['width']>1800
            page.locator('#generationTab').click();page.wait_for_selector('.generation-file');assert page.locator('.generation-file').count()==1
            page.locator('#writingTab').click();page.locator('#writeSettings').click();page.locator('#writeEndpoint').fill(endpoint)
            page.locator('#writeQuery').click();page.wait_for_function("()=>document.getElementById('writeSettingsStatus').textContent.includes('Connected')")
            page.locator('#writeModel').select_option('writing-fixture');page.locator('#writeSettingsSave').click();page.locator('#writeSettingsClose').click()
            page.locator('#writeCluster').fill('Rock');page.locator('#writeStyle').fill('Punk')
            page.locator('#writeBrief').fill('A punk story about Bam Bam and a ball that becomes a star.')
            page.locator('#writeGenerate').click();page.wait_for_selector('#writeDraft:not([hidden])')
            assert page.locator('#writeTitle').input_value()=='ball-supernova'
            page.locator('#writeLyrics').fill('[verse]\nAn edited star-bound ball\n');page.locator('#writeSave').click()
            page.wait_for_function("()=>!document.getElementById('writeStatus').textContent.includes('Unsaved')")
            page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/writing-desktop.png',full_page=True)
            page.locator('#writePublish').click();page.wait_for_function("()=>document.getElementById('writePublish').textContent==='Added to Built'")
            published=f.built/'_yaml/ball-supernova.yaml';assert published.is_file()
            assert YamlDocument(published.read_bytes()).values['lyrics']=='[verse]\nAn edited star-bound ball\n'
            assert f.source.read_bytes()==f.original
            page.locator('summary',has_text='Save caption as an LLM style template').click();page.locator('#writeTemplateName').fill('punk-llm-browser');page.locator('#writeTemplateSave').click()
            page.wait_for_function("()=>document.getElementById('toast').textContent.includes('LLM template saved')")
            page.locator('#generationTab').click();page.wait_for_function("()=>document.querySelectorAll('.generation-file').length===2")
            page.wait_for_function("()=>[...document.getElementById('genTemplateList').options].some(o=>o.value==='punk-llm-browser')")
            page.locator('#writingTab').click()
            page.locator('#writeKind').select_option('caption')
            page.get_by_label('Select writing _yaml/gbr-local-smoke.yaml',exact=True).check()
            page.locator('#writeGenerate').click();page.wait_for_function("()=>document.getElementById('writeStatus').textContent.startsWith('ready')")
            page.locator('#writePublish').click();page.wait_for_function("()=>document.getElementById('writePublish').textContent==='Added to Built'")
            assert (f.built/'_yaml/gbr-local-smoke__v2.yaml').is_file() and f.source.read_bytes()==f.original
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/writing-mobile.png',full_page=True)
            page.locator('#settingsButton').click();page.locator('#openWritingSettings').click();page.wait_for_selector('#writingSettingsDialog[open]')
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert not errors,errors
            assert len(calls)==2
            browser.close();print(json.dumps({'writing_draft_edit_publish_variant':'passed','model_settings':'passed','workflow_order_and_1920_390_layout':'passed','js_errors':errors}))
    finally:
        server.should_exit=True;thread.join(timeout=10)
        try:next(fixture)
        except StopIteration:pass

if __name__=='__main__':main()
