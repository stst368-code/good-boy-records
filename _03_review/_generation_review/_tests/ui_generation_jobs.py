"""Browser fixture for live queue activity, accessible tabs, and job controls.

No inference is performed; run states are explicit UI fixtures in a real shared DB.
"""
import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import threading
import time
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'_02_generation/_tests'),str(ROOT/'_02_generation'),str(ROOT/'_98_database'),str(ROOT/'_03_review/_generation_review')]
import test_app_generation
from _gbr_generation.executor import make_job
from _gbr_generation.planner import open_database,verify_plan
from _gbr_generation.job_state import atomic_state
from _gbr_generation.connections import normalize


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--browser',required=True)
    parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    server=None
    try:
        generation=f.generation
        generation.workers.inspect=lambda e:{'busy':False,'models':generation.native_choices()}
        generation.workers.save({'connection':{'backend':'local'}})
        preview=f.preview(experiments={'sweeps':{'sampler_cfg':[1.2,1.5,1.8]}});generation.start(preview['token'])
        task=f.launched[-1];path=Path(task['manifest']);manifest=verify_plan(path);run=manifest['runs'][0]
        pending=manifest['runs'][1]
        waiting=manifest['runs'][2]
        attempt='b'*32
        pending_attempt='e'*32
        started='2026-10-20T18:00:00+00:00'
        with closing(open_database(f.database)) as db,db:
            for item,identifier in ((run,attempt),(pending,pending_attempt)):
                directory=path.parent/'attempts'/identifier
                directory.mkdir(parents=True)
                (directory/'request.json').write_text(json.dumps(make_job(item,path.parent,identifier,directory)))
                db.execute("UPDATE generation_runs SET status='running',started_at=? WHERE id=?",(started,item['id']))
                db.execute('INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',(identifier,item['id'],None,'full_generation','running','comfyui','{}',started,None,None))

        active_folder=generation.runtime/'app_jobs'/('a'*32)
        def live_state(**extra):
            atomic_state(active_folder/'state.json', {
                'status':'running','run_id':run['id'],'attempt_id':attempt,'stage':'comfy_generating',
                'prompt_id':'playing-prompt','updated_at':datetime.now(timezone.utc).isoformat(),
                'execution_started_at':started,**extra,
            })

        for identifier,address in [('a'*32,'http://127.0.0.1:8190'),('d'*32,'https://pod-8188.proxy.runpod.net')]:
            folder=generation.runtime/'app_jobs'/identifier
            folder.mkdir(parents=True)
            connection=normalize({'backend':'comfyui','address':address})
            (folder/'task.json').write_text(json.dumps({**task,'connection':connection,'worker_id':'comfyui|'+address}))
        live_state(node_label='Sampling',step=12,steps=30,progress_unit='steps')
        atomic_state(generation.runtime/'app_jobs'/('d'*32)/'state.json',{
            'status':'running','run_id':pending['id'],'attempt_id':pending_attempt,'stage':'comfy_queued',
            'prompt_id':'waiting-prompt','updated_at':datetime.now(timezone.utc).isoformat(),
        })
        generation.active=lambda identifier:identifier in {'a'*32,'d'*32}
        for status in ['succeeded','failed']:
            p=f.preview()
            generation.start(p['token'])
            other=verify_plan(f.launched[-1]['manifest'])
            with closing(open_database(f.database)) as db,db:
                db.execute('UPDATE generation_runs SET status=?,completed_at=? WHERE batch_id=?',(status,started,other['batch_id']))
        server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18775,log_level='error',loop='_review.server_loop:create_loop'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+8
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']);page=browser.new_page(viewport={'width':1920,'height':1080});errors=[];requests=[]
            page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
            page.goto('http://127.0.0.1:18775')
            page.locator('#knownReviewers button',has_text='Simon').click()
            page.locator('#generationTab').click()
            page.wait_for_selector('.generation-file')

            workspace=page.get_by_role('tablist',name='Generation workspace')
            workspace.get_by_role('tab',name='New batch').focus()
            page.keyboard.press('ArrowRight')
            page.keyboard.press('Enter')
            assert workspace.get_by_role('tab',name='Production queue').get_attribute('aria-selected')=='true'
            queue=page.get_by_role('tabpanel',name='Production queue')
            active=queue.locator(f'tr[data-run="{run["id"]}"]')
            active.wait_for()
            assert queue.locator('tr[data-phase=running]').count()==1
            assert 'Sampling · 12 / 30 steps' in active.inner_text()
            assert active.get_by_role('progressbar').get_attribute('value')=='12'
            assert active.locator('time').get_attribute('datetime')==started
            if args.screenshots:
                args.screenshots.mkdir(parents=True,exist_ok=True)
                page.screenshot(path=str(args.screenshots/'generation-queue-desktop.png'),full_page=True)
            queued=queue.locator(f'tr[data-run="{pending["id"]}"]')
            assert 'Queued in ComfyUI' in queued.inner_text()
            assert queued.get_by_role('progressbar').count()==0
            queued.get_by_role('checkbox').focus()
            live_state(node_label='Decoding audio')
            page.wait_for_function("()=>document.querySelector('tr[data-phase=running]').textContent.includes('Decoding audio')")
            assert active.get_by_role('progressbar').count()==0
            assert queued.get_by_role('checkbox').evaluate('el=>document.activeElement===el')
            statuses=queue.get_by_role('tablist',name='Run status')
            statuses.get_by_role('tab',name='Queue').focus()
            page.keyboard.press('ArrowRight')
            page.keyboard.press('Enter')
            completed=queue.get_by_role('tabpanel',name='Completed')
            finished=completed.locator('tr[data-phase=succeeded] time')
            finished.wait_for()
            assert finished.get_attribute('datetime')==started
            statuses.get_by_role('tab',name='Failed').click()
            queue.get_by_role('tabpanel',name='Failed').locator('tr[data-phase=failed]').wait_for()
            statuses.get_by_role('tab',name='Queue').click()
            selected=queue.locator(f'tr[data-run="{waiting["id"]}"]')
            selected.get_by_role('checkbox').check()
            page.once('dialog',lambda dialog:dialog.accept())
            queue.get_by_role('button',name='Cancel selected (1)').click()
            statuses.get_by_role('tab',name='Cancelled').click()
            queue.get_by_role('tabpanel',name='Cancelled').locator('tr[data-phase=cancelled] time').wait_for()

            statuses.get_by_role('tab',name='Queue').click()
            active.wait_for()
            assert not any(url.endswith('/generation/jobs?purpose=production') for url in requests)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            attention=statuses.get_by_role('tab',name='Needs attention')
            assert attention.evaluate('el=>el.getBoundingClientRect().right<=innerWidth')
            if args.screenshots:
                page.screenshot(path=str(args.screenshots/'generation-queue-mobile.png'),full_page=True)
            page.set_viewport_size({'width':1920,'height':1080})
            queue.locator('.job-controls > summary').click()
            card=queue.locator(f'[data-batch="{manifest["batch_id"]}"]')
            card.locator('summary',has_text='Tracks & configurations').click()
            card.locator('.job-run').first.wait_for()
            assert card.locator('.job-run').count()==3
            assert not any('/text' in url for url in requests)
            last=card.locator('.job-run').nth(2)
            last.locator('summary',has_text='Caption').click()
            last.locator('summary',has_text='Lyrics').click()
            page.wait_for_function("()=>[...document.querySelectorAll('.job-song-text')].some(e=>e.textContent.includes('[chorus]'))")
            assert last.locator('details[open]').count()==2
            assert f.source.read_bytes()==f.original

            submitted_runs=set()
            expected=2  # The two worker-owned runs above remain in the queue.
            for count in (100,25):
                page.locator('#genSetupTab').click()
                page.locator('#genSelectVisible').click()
                page.locator('#genCaptionMode').select_option('yaml')
                page.locator('#genRepetitions').fill(str(count))
                page.locator('#genPreview').click()
                page.locator('#genPlan:not([hidden])').wait_for()
                assert f'{count} generations' in page.locator('#genPlanSummary').inner_text()
                page.locator('#genQueueTab').click()
                if count==100:
                    statuses.get_by_role('tab',name='Completed').click()
                    page.get_by_role('searchbox',name='Find generation runs').fill('an old search')
                else:
                    queue.get_by_role('button',name='Next',exact=True).click()
                    page.wait_for_function("()=>document.querySelector('.queue-range').textContent.startsWith('51')")
                page.locator('#genSetupTab').click()
                with page.expect_response('**/api/generation/start') as added:
                    page.locator('#genStart').click()
                batch_id=added.value.json()['batch_id']
                expected+=count
                page.wait_for_function('(count)=>document.querySelector("#genRunTab-queued .generation-tab-count").textContent===String(count)',arg=expected)
                assert statuses.get_by_role('tab',name='Queue').get_attribute('aria-selected')=='true'
                assert page.get_by_role('searchbox',name='Find generation runs').input_value()==''
                assert page.locator('.queue-range').inner_text()==f'1–50 of {expected}'
                assert page.locator('.queue-job-filter').count()==0
                assert 'batch=' not in requests[-1]
                added_manifest=verify_plan(f.launched[-1]['manifest'])
                assert added_manifest['batch_id']==batch_id and len(added_manifest['runs'])==count
                submitted_runs.update(run['id'] for run in added_manifest['runs'])
                stored={row['id'] for row in f.app.state.service.db.all("SELECT id FROM generation_runs WHERE status='prepared'")}
                assert stored==submitted_runs
            shown=set()
            for offset in (0,50,100):
                payload=f.client.get('/api/generation/queue',headers=f.headers,params={'offset':offset}).json()
                assert payload['counts']['queued']==127
                shown.update(row['id'] for row in payload['runs'])
            assert submitted_runs<=shown and len(shown)==127
            assert not errors,errors
            browser.close()
            print(json.dumps({'queue_status_and_steps':'passed','keyboard_tabs_and_times':'passed',
                              'bulk_cancel':'passed','desktop_mobile':'passed','job_diagnostics':'passed',
                              'appending_100_and_25_runs':'passed'}))
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        f.doCleanups()


if __name__=='__main__':main()
