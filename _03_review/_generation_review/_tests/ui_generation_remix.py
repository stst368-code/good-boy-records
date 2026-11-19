"""Mouse/keyboard multiselection and review-to-remix through the real browser/API."""
import argparse
from pathlib import Path
import sys
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT/'_02_generation/_tests'),str(ROOT/'_02_generation'),
               str(ROOT/'_98_database'),str(ROOT/'_03_review/_generation_review')]
import test_app_generation
import test_execution
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan
from _gbr_generation.yaml_document import YamlDocument


def drag(page,first,last):
    first.scroll_into_view_if_needed()
    start,end = first.bounding_box(),last.bounding_box()
    page.mouse.move(start['x']+80,start['y']+start['height']/2)
    page.mouse.down()
    page.mouse.move(end['x']+80,end['y']+end['height']/2,steps=8)
    page.mouse.up()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser',required=True)
    parser.add_argument('--screenshots',type=Path)
    args = parser.parse_args()
    fixture = test_app_generation.GenerationAppTests('runTest')
    fixture.setUp()
    server = None
    try:
        generation = fixture.generation
        generation.workers.inspect = lambda entry:{'busy':False,'models':generation.native_choices()}
        generation.workers.save({'connection':{'backend':'local'}})
        preview = fixture.preview(parameters={'sampler_seed':'18446744073709551614'},
                                  parameter_sources={'sampler_seed':'control'})
        generation.start(preview['token'])
        worker = test_execution.ExecutionTests('runTest')
        worker.calls = []
        execute(fixture.launched[-1]['manifest'],worker_python=sys.executable,launcher=worker.worker)
        fixture.app.state.service.indexer.scan()
        for number in range(2,7):
            title = f'gbr-selection-{number}'
            raw = fixture.original.replace(b'900000001',str(900000000+number).encode()).replace(b'gbr-local-smoke',title.encode())
            (fixture.built/'_yaml'/f'{title}.yaml').write_bytes(raw)
            with (fixture.built/'track_list.csv').open('a') as output:
                output.write(f'{900000000+number},_yaml/{title}.yaml,{title},,\n')
        server = uvicorn.Server(uvicorn.Config(fixture.app,host='127.0.0.1',port=18786,
                               log_level='error',loop='_review.server_loop:create_loop'))
        thread = threading.Thread(target=server.run,daemon=True)
        thread.start()
        deadline = time.monotonic()+10
        while not server.started and time.monotonic()<deadline:
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser,headless=True,
                args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            page = browser.new_page(viewport={'width':1440,'height':1000})
            errors = []
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto('http://127.0.0.1:18786')
            page.locator('#knownReviewers button',has_text='Simon').click()
            page.locator('#generationTab').click()
            files = page.locator('.generation-file')
            page.wait_for_function("()=>document.querySelectorAll('.generation-file').length===6")
            drag(page,files.nth(0),files.nth(3))
            assert page.locator('#genFiles input:checked').count()==4
            files.nth(5).click(modifiers=['Shift'])
            assert page.locator('#genFiles input:checked').count()==6
            page.locator('#genClear').click()
            files.nth(1).get_by_role('checkbox').click()
            files.nth(3).get_by_role('checkbox').click(modifiers=['Shift'])
            assert page.locator('#genFiles input:checked').count()==3
            files.nth(4).get_by_role('checkbox').focus()
            page.keyboard.press('Shift+Space')
            assert page.locator('#genFiles input:checked').count()==4
            drag(page,files.nth(1),files.nth(4))
            assert page.locator('#genFiles input:checked').count()==0
            page.locator('#listenTab').click()
            page.locator('#remixTrack').click()
            page.locator('#genRemix:not([hidden])').wait_for()
            assert page.locator('#genSamplerSeed').input_value()=='18446744073709551614'
            assert '[chorus]' in page.locator('#genRemixLyrics').input_value()
            lyrics = '[verse]\nA brand new lyric version\n'
            caption = 'A warm, intimate recording with tape colour and a live room.'
            page.locator('#genRemixLyrics').fill(lyrics)
            page.locator('#genRemixCaption').fill(caption)
            page.locator('#genStepsMode').select_option('list')
            page.locator('#genStepsList').fill('30,35')
            page.locator('#genPreview').click()
            page.locator('#genPlan:not([hidden])').wait_for()
            assert '2 generations' in page.locator('#genPlanSummary').inner_text()
            if args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                page.locator('#genRemix').scroll_into_view_if_needed()
                page.screenshot(path=str(args.screenshots/'remix-desktop.png'),full_page=True)
            page.locator('#genStart').click()
            page.wait_for_function("()=>document.querySelectorAll('.queue-row').length===2")
            rows = page.locator('.queue-row')
            rows.nth(0).get_by_role('checkbox').click()
            rows.nth(1).get_by_role('checkbox').click(modifiers=['Shift'])
            assert page.locator('.queue-row input:checked').count()==2
            manifest = verify_plan(fixture.launched[-1]['manifest'])
            assert [run['effective']['sampler_steps'] for run in manifest['runs']]==[30,35]
            assert all(run['effective']['sampler_seed']=='18446744073709551614' for run in manifest['runs'])
            assert all(run['effective']['caption']==caption for run in manifest['runs'])
            source = Path(fixture.launched[-1]['manifest']).parent/manifest['runs'][0]['snapshot']
            assert YamlDocument(source.read_bytes()).values['lyrics']==lyrics
            page.locator('#genSetupTab').click()
            page.locator(f'#genRemixVersion option[value="{manifest["batch_id"]}"]').wait_for(state='attached')
            page.locator('#genRemixVersion').select_option(manifest['batch_id'])
            page.wait_for_function("()=>document.getElementById('genStepsMode').value==='fixed'")
            assert page.locator('#genRemixLyrics').input_value()==lyrics
            assert page.locator('#genRemixCaption').input_value()==caption
            assert page.locator('#genSamplerSeed').input_value()=='18446744073709551614'
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if args.screenshots:
                page.locator('#genRemix').scroll_into_view_if_needed()
                page.screenshot(path=str(args.screenshots/'remix-mobile.png'),full_page=True)
            page.locator('#genRemixExit').click()
            page.locator('#genCatalogue:not([hidden])').wait_for()
            assert not errors,errors
            browser.close()
            print('PASS: drag select, Shift-click, Shift-Space, drag deselect, review remix, exact 64-bit seeds, lyric/caption edits, step lists, queue multiselection and mobile layout')
    finally:
        if server:
            server.should_exit = True
            thread.join(timeout=10)
        fixture.doCleanups()


if __name__=='__main__':
    main()
