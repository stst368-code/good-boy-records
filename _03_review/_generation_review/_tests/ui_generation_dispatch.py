"""Cluster picker, preference restore and late worker assignment in Chromium.

Controllers are explicit UI fixtures; no inference or paid pod is started.
"""
import argparse
import json
from pathlib import Path
import sys
import threading
import time
import uuid
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'_02_generation/_tests'),str(ROOT/'_02_generation'),str(ROOT/'_98_database'),str(ROOT/'_03_review/_generation_review')]
import test_app_generation
from test_comfy_execution import server as comfy_server
from _gbr_generation.planner import verify_plan


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    f=test_app_generation.GenerationAppTests('runTest');f.setUp();server=None
    try:
        g=f.generation;active=set()
        g.workers.inspect=lambda e:{'busy':False,'models':g.native_choices()}
        native=g.workers.save({'connection':{'backend':'local'}})
        def launch(task,credentials=None):
            f.launched.append(task);folder=g.runtime/'app_jobs'/uuid.uuid4().hex;folder.mkdir(parents=True)
            (folder/'task.json').write_text(json.dumps(task));(folder/'state.json').write_text('{"status":"running"}')
            active.add(folder.name);return {'task_id':folder.name,'batch_id':task['batch_id']}
        g.launch=launch;g.active=lambda id:id in active
        refs=f.root/'_01_writing/_references'
        (refs/'styles.csv').write_text('ID,cluster,style_primary,template\na1,Rock,Punk,a1.txt\na2,Rock,Punk,a2.txt\nb,Jazz,Choral,b.txt\n')
        for id in ('a1','a2','b'):(refs/(id+'.txt')).write_text('Global Metadata\n'+id+'\nVocal Details\nClear singing\nArrangement\nGuitar\n')
        server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18776,log_level='error',loop='_review.server_loop:create_loop'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.05)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu']);page=browser.new_page(viewport={'width':1600,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:18776');page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#generationTab').click();page.wait_for_selector('.caption-cluster-toggle')
            assert page.locator('.caption-cluster-toggle[aria-pressed=true]').count()==2
            page.get_by_role('button',name='Clear clusters',exact=True).click();assert page.locator('.caption-cluster-toggle[aria-pressed=true]').count()==0
            page.get_by_role('button',name='Caption cluster Rock',exact=True).click();page.get_by_role('button',name='Caption cluster Jazz',exact=True).click()
            page.locator('.caption-cluster',has=page.get_by_role('button',name='Caption cluster Rock',exact=True)).locator('summary').click()
            page.get_by_label('Template version for Rock',exact=True).select_option('a2')
            page.locator('#genCaptionCount').fill('2');page.locator('#genRepetitions').fill('2');page.locator('#genWorker').select_option(native['id'])
            page.locator('#genSelectVisible').click();page.locator('#genPreview').click();page.wait_for_selector('#genPlan:not([hidden])')
            assert '4 generations' in page.locator('#genPlanSummary').inner_text()
            page.reload();page.locator('#generationTab').click();page.wait_for_selector('.caption-cluster-toggle')
            assert page.get_by_label('Template version for Rock',exact=True).input_value()=='a2'
            assert page.locator('.caption-cluster-toggle[aria-pressed=true]').count()==2
            page.locator('#genSelectVisible').click();page.locator('#genPreview').click();page.wait_for_selector('#genPlan:not([hidden])')
            page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/caption-clusters-desktop.png',full_page=True)
            page.locator('#genStart').click();page.wait_for_selector('.job-batch.active');manifest=verify_plan(f.launched[0]['manifest']);raw=Path(f.launched[0]['manifest']).read_bytes()
            assert {r['template']['id'] for r in manifest['runs']}=={'a2','b'}
            auto=page.get_by_role('checkbox',name='Automatically add available workers to batch '+manifest['batch_id'][:8]);assert not auto.is_checked();auto.check()
            page.wait_for_function("()=>document.querySelector('.job-auto-workers input')?.checked===true")
            inspect=g.workers.inspect
            with comfy_server() as (address,state):
                g.workers.inspect=lambda e:inspect(e) if e['connection']['backend']=='local' else type(g.workers).inspect(g.workers,e)
                pod=g.workers.save({'connection':{'backend':'comfyui','address':address}})
                page.locator('#genRefreshJobs').click();page.get_by_role('combobox',name='Additional worker for batch '+manifest['batch_id'][:8]).select_option(pod['id'])
                page.get_by_role('button',name='Add worker',exact=True).click();page.wait_for_function("()=>document.querySelectorAll('.job-batch.active .job-worker').length===2")
                assert len(f.launched)==2 and f.launched[-1]['worker_id']==pod['id'] and state['submissions']==0
                assert Path(f.launched[0]['manifest']).read_bytes()==raw and f.source.read_bytes()==f.original
                page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/late-worker-desktop.png',full_page=True)
                page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/late-worker-mobile.png',full_page=True)
            assert not errors,errors;browser.close()
            print(json.dumps({'cluster_toggles':'passed','linked_versions':'passed','caption_preference_restore':'passed','late_worker_api_ui':'passed','auto_join_switch':'passed','desktop_mobile':'passed','inference':'not run','js_errors':errors}))
    finally:
        if server:server.should_exit=True;thread.join(timeout=10)
        f.doCleanups()


if __name__=='__main__':main()
