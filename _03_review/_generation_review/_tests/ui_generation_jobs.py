"""Dashboard browser fixture: active stages, lazy song text and inline cancellation.

No inference is performed; run states are explicit UI fixtures in a real shared DB.
"""
import argparse
from contextlib import closing
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
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    server=None
    try:
        generation=f.generation
        generation.workers.inspect=lambda e:{'busy':False,'models':generation.native_choices()}
        generation.workers.save({'connection':{'backend':'local'}})
        preview=f.preview(experiments={'sweeps':{'sampler_cfg':[1.2,1.5,1.8]}});generation.start(preview['token'])
        task=f.launched[-1];path=Path(task['manifest']);manifest=verify_plan(path);run=manifest['runs'][0]
        attempt='b'*32;directory=path.parent/'attempts'/attempt;directory.mkdir(parents=True)
        (directory/'request.json').write_text(json.dumps(make_job(run,path.parent,attempt,directory)))
        with closing(open_database(f.database)) as db,db:
            db.execute("UPDATE generation_runs SET status='running',started_at=? WHERE id=?",('2026-10-20T18:00:00+00:00',run['id']))
            db.execute('INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)',(attempt,run['id'],None,'full_generation','running','local','{}','2026-10-20T18:00:00+00:00',None,None))
        for identifier,connection,state in [('a'*32,task['connection'],{'status':'running','run_id':run['id'],'stage':'diffusion','step':12,'steps':30}),('d'*32,normalize({'backend':'comfyui','address':'https://example-8188.proxy.runpod.net'}),{'status':'waiting_worker'})]:
            folder=generation.runtime/'app_jobs'/identifier;folder.mkdir(parents=True)
            (folder/'task.json').write_text(json.dumps({**task,'connection':connection,'worker_id':connection['backend']+'|'+connection.get('address','')}));atomic_state(folder/'state.json',state)
        generation.active=lambda identifier:identifier in {'a'*32,'d'*32}
        for status in ['succeeded','failed']:
            p=f.preview();generation.start(p['token']);other=verify_plan(f.launched[-1]['manifest'])
            with closing(open_database(f.database)) as db,db:db.execute('UPDATE generation_runs SET status=? WHERE batch_id=?',(status,other['batch_id']))
        server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18775,log_level='error',loop='_review.server_loop:create_loop'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+8
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser=playwright.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']);page=browser.new_page(viewport={'width':1920,'height':1080});errors=[];requests=[]
            page.on('pageerror',lambda e:errors.append(str(e)));page.on('request',lambda r:requests.append(r.url))
            page.goto('http://127.0.0.1:18775');page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#generationTab').click();page.wait_for_selector('.generation-file');page.locator('#genQueueTab').click()
            page.wait_for_selector('#genJobs [data-section=active] .job-batch')
            assert page.locator('#genJobs [data-section=active] .job-batch').count()==1
            assert page.locator('#genJobs [data-section=completed] .job-batch').count()==1
            assert page.locator('#genJobs [data-section=failed] .job-batch').count()==1
            card=page.locator('[data-batch="'+manifest['batch_id']+'"]');assert 'Worker busy' in card.inner_text()
            card.locator('summary',has_text='Tracks & configurations').click();page.wait_for_selector('.job-run')
            assert card.locator('.job-run').count()==3 and '12 / 30 steps' in card.inner_text()
            assert not any('/text' in url for url in requests)
            last=card.locator('.job-run').nth(2);last.locator('summary',has_text='Caption').click();last.locator('summary',has_text='Lyrics').click()
            page.wait_for_function("()=>document.querySelectorAll('.job-song-text').length>=2&&[...document.querySelectorAll('.job-song-text')].some(e=>e.textContent.includes('[chorus]'))")
            card.locator('.job-run').nth(1).get_by_role('button',name='Cancel',exact=False).click()
            page.wait_for_function("()=>document.querySelector('.job-run.cancelled')!==null")
            assert card.locator('.job-run').nth(2).locator('details[open]').count()==2
            atomic_state(generation.runtime/'app_jobs'/('a'*32)/'state.json',{'status':'running','run_id':run['id'],'stage':'decode'})
            page.locator('#genRefreshJobs').click();page.wait_for_function("()=>document.getElementById('genJobs').textContent.includes('Decoding audio')")
            assert f.source.read_bytes()==f.original
            page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/job-dashboard-desktop.png',full_page=True)
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth');page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/job-dashboard-mobile.png',full_page=True)
            assert not errors,errors
            browser.close();print(json.dumps({'batch_groups':'passed','live_stage_worker':'passed','caption_lyrics_lazy_drawers':'passed','inline_cancel':'passed','desktop_mobile':'passed','js_errors':errors}))
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        f.doCleanups()


if __name__=='__main__':main()
