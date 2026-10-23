"""Prefilled review/selection/writing/generation/YAML choices in desktop and mobile Chromium."""
import argparse
import json
from pathlib import Path
import sys
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[3]
for folder in ('_01_writing/_tests','_01_writing','_02_generation/_tests','_03_review/_generation_review'):
    sys.path.insert(0,str(ROOT/folder))
import test_writing
from conftest import complete


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);parser.add_argument('--screenshots');args=parser.parse_args()
    fixture=test_writing.app.__wrapped__();f,writing=next(fixture);service=f.app.state.service
    editor=f.app.state.yaml_editor;editor.repo=f.root;editor.default_order_template=f.built/'track-template.yaml';editor.backups=f.root/'yaml_backups'
    inp=f.root/'music';inp.mkdir();sub=inp/'sub';sub.mkdir()
    service.config.values['input_directories']=[str(inp)];service.config.save()
    for i,(title,cluster,style,folder) in enumerate([('Ball','Rock','Punk',inp),('Ball Again','Rockabilly','Rockabilly',sub),('Other','Rock','Jazz',sub)]):
        path=folder/(title+'.flac');t=np.arange(16000*12)/16000
        sf.write(path,.1*np.sin(2*np.pi*(220+i*40)*t),16000,subtype='PCM_24')
        path.with_suffix('.yaml').write_text(f'title: {title}\ncluster: {cluster}\nstyle: {style}\n')
        service.indexer.index(path)
    service.start()
    server=uvicorn.Server(uvicorn.Config(f.app,host='127.0.0.1',port=18778,log_level='error',loop='_review.server_loop:create_loop'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    try:
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            if server.started and service.db.one("SELECT COUNT(*) n FROM audio WHERE analysis_state='ready'")['n']==3:break
            time.sleep(.1)
        assert server.started
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--disable-dev-shm-usage','--disable-gpu'])
            page=browser.new_page(viewport={'width':1600,'height':1000});errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
            page.goto('http://127.0.0.1:18778');page.locator('#knownReviewers button',has_text='Simon').click()
            page.locator('#queueButton').click();page.wait_for_selector('#queueDialog[open]')
            assert page.locator('#filterSong .choice-toggle').count()==3
            assert page.locator('#filterCluster .choice-toggle').count()==2
            page.locator('#filterSong').get_by_role('button',name='Track names: Ball',exact=True).click()
            page.locator('#filterCluster').get_by_role('button',name='Clusters: Rock',exact=True).click()
            page.locator('#filterGenre').get_by_role('button',name='Styles / genres: Punk',exact=True).click()
            page.wait_for_function("()=>document.getElementById('queuePreviewCount').textContent.startsWith('1 left')")
            assert service.db.one('SELECT COUNT(*) n FROM reviews')['n']==0
            if args.screenshots:page.screenshot(path=str(Path(args.screenshots)/'review-choices-desktop.png'),full_page=True)
            page.locator('#queueForm button.primary').click();page.wait_for_function("()=>document.getElementById('trackTitle').textContent==='Ball'")
            page.reload();page.locator('#queueButton').click();page.wait_for_selector('#queueDialog[open]')
            assert page.locator('#filterSong .choice-toggle[aria-pressed=true]').count()==1
            assert page.locator('#filterCluster .choice-toggle[aria-pressed=true]').count()==1
            # Switching the directory updates the actual options and counts without losing chosen values.
            assert page.locator('#filterDirectory option').evaluate_all('(items)=>items.filter(o=>!o.disabled).every(o=>!o.value.endsWith("/sub"))')
            page.locator('#filterSong').get_by_role('button',name='Any track',exact=True).click()
            page.locator('#filterGenre').get_by_role('button',name='Any style',exact=True).click()
            page.locator('#filterDirectory').select_option(str(sub));page.wait_for_function("()=>document.getElementById('queuePreviewCount').textContent.startsWith('1 left')")
            page.set_viewport_size({'width':390,'height':844});assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            assert page.locator('#queueDialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth')
            if args.screenshots:page.screenshot(path=str(Path(args.screenshots)/'review-choices-mobile.png'),full_page=True)
            page.locator('#queueForm button.primary').click();page.wait_for_function("()=>document.getElementById('trackTitle').textContent==='Other'")
            page.wait_for_function("()=>document.querySelectorAll('.rating-values button:disabled').length===0")
            for dimension in ('audio_quality','composition','consistency','realism'):
                page.locator(f'.rating[data-dimension={dimension}] button[data-score="5"]').click()
            page.wait_for_function("()=>document.getElementById('reviewCard').hidden===true")
            assert service.db.one('SELECT COUNT(*) n FROM reviews')['n']==1
            page.locator('#selectionTab').click();page.wait_for_selector('.candidate')
            page.locator('#selectionSong').get_by_role('button',name='Track names: Other',exact=True).click()
            page.locator('#selectionCluster').get_by_role('button',name='Clusters: Rock',exact=True).click()
            page.wait_for_function("()=>document.querySelectorAll('.candidate').length===1")
            assert page.locator('.candidate-title h3').inner_text()=='Other'
            page.set_viewport_size({'width':1600,'height':1000});page.locator('#generationTab').click();page.wait_for_selector('.generation-file')
            assert page.locator('#genRatingCluster option[value=Rock]').count()==1
            assert page.locator('#genRatingStyle option[value=Jazz]').count()==1
            assert page.locator('#genRatingStyle option[value=Punk]').count()==0
            page.locator('#writingTab').click();page.wait_for_selector('#writeCluster option[value=Rock]',state='attached')
            page.locator('#writeCluster').select_option('Rock');page.locator('#writeStyle').select_option('Punk')
            assert page.locator('#writeLanguage').input_value()=='en'
            assert page.locator('#writeLanguage option').count()>180
            page.locator('summary',has_text='Reference context').click();page.locator('#writeTemplateIds .choice-toggle').click()
            assert page.locator('#writeTemplateIds .choice-toggle[aria-pressed=true]').count()==1
            page.locator('#yamlTab').click();page.wait_for_selector('#ye-filter-key option[value=cluster]',state='attached')
            page.locator('#ye-filter-key').select_option('cluster');page.locator('#ye-track').select_option('gbr-local-smoke')
            operation=page.locator('.ye-operation').first;operation.locator('[data-role=key]').select_option('explicit')
            operation.locator('[data-role=known_value]').select_option('no')
            assert operation.locator('[data-role=value]').input_value()=='no'
            operation.locator('[data-role=key]').select_option('@new');operation.locator('[data-role=custom_key]').fill('new_parameter')
            assert operation.locator('[data-role=custom_key]').is_visible()
            operation.locator('[data-role=type]').select_option('boolean');operation.locator('[data-role=known_value]').select_option('false');assert operation.locator('[data-role=value]').input_value()=='false'
            operation.locator('[data-role=kind]').select_option('add_template')
            page.wait_for_selector('[data-role=template_fields] option[value=explicit]',state='attached')
            operation.locator('[data-role=template_fields]').select_option(['language','explicit'])
            assert len(operation.locator('[data-role=template_fields]').evaluate('(s)=>[...s.selectedOptions].map(o=>o.value)'))==2
            page.locator('#settingsButton').click();page.locator('#openWorkerSettings').click();page.wait_for_selector('#workerSettingsDialog[open]');page.locator('#workerBackend').select_option('runpod_dedicated');page.locator('#workerKeyChoice').select_option('@other');page.locator('#workerKey').fill('/example/private-key');assert page.locator('#workerKey').is_visible();page.locator('#workerKeyChoice').select_option('');assert not page.locator('#workerKey').is_visible();page.locator('#workersClose').click()
            # Reproduce the screenshot: IDs, variants and arbitrary run suffixes group once.
            catalogue=f.root/'review-catalogue';catalogue.mkdir()
            (catalogue/'track_list.csv').write_text('song_id,song_name,filename\n17,paws off the velvet,paws-off-the-velvet.yaml\n18,paws off,paws-off.yaml\n')
            service.song_catalogue_root=lambda:catalogue
            only=inp/'velvet-only';only.mkdir();generated=[]
            for i,(title,sid) in enumerate([('paws-off-the-velvet-v1_CFG-1.90_STEP-30_SEED-7',17),('paws-off-the-velvet-v3_CFG-1.70_STEP-30_SEED-42',None),('arbitrary-caption-and-configuration-tail',17),('paws-off-v2_CFG-1.90_STEP-30_SEED-7',18)]):
                folder=only if i<3 else inp;path=folder/(title+'.flac');t=np.arange(16000*12)/16000
                sf.write(path,.1*np.sin(2*np.pi*(700+i*50)*t),16000,subtype='PCM_24')
                path.with_suffix('.yaml').write_text(f'title: {title}\ncluster: Rock\nstyle: Punk\n'+(f'song_id: {sid}\n' if sid else ''))
                aid=service.indexer.index(path);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
                if i<3:generated.append(gid)
            page.locator('#reviewTab').click();page.locator('#queueButton').click();page.wait_for_selector('#queueDialog[open]')
            for id,label in [('filterSong','Any track'),('filterCluster','Any cluster'),('filterGenre','Any style')]:page.locator('#'+id).get_by_role('button',name=label,exact=True).click()
            page.locator('#filterDirectory').select_option('')
            velvet=page.locator('#filterSong').get_by_role('button',name='Track names: paws off the velvet',exact=True)
            velvet.wait_for();assert velvet.count()==1 and velvet.locator('.choice-count').inner_text()=='3'
            assert page.locator('#filterSong .choice-toggle').filter(has_text='CFG').count()==0
            velvet.click();page.wait_for_function("()=>document.getElementById('queuePreviewCount').textContent.startsWith('3 left')")
            if args.screenshots:page.screenshot(path=str(Path(args.screenshots)/'song-groups-and-facets.png'),full_page=True)
            for gid in generated:complete(service,gid)
            page.wait_for_function("()=>document.getElementById('queuePreviewCount').textContent.startsWith('0 left')",timeout=15000)
            assert page.locator('#filterSong').get_by_role('button',name='Track names: paws off the velvet',exact=True).count()==0
            assert page.locator('#filterSong .choice-stale').count()==1
            assert page.locator('#filterSong .choice-toggle .choice-count').evaluate_all('(items)=>items.every(i=>Number(i.textContent)>0)')
            page.locator('#filterSong').get_by_role('button',name='Any track',exact=True).click()
            page.wait_for_function("()=>!document.querySelector('#filterSong .choice-stale')")
            assert page.locator('#filterDirectory option').evaluate_all('(items)=>items.filter(o=>!o.disabled).every(o=>!o.value.endsWith("/velvet-only"))')
            page.set_viewport_size({'width':390,'height':844});assert page.locator('#queueDialog').evaluate('(d)=>d.scrollWidth<=d.clientWidth')
            assert f.source.read_bytes()==f.original
            assert not errors,errors
            browser.close();print(json.dumps({'exact_review_toggles_counts_and_restore':'passed','directory_scope':'passed','fourth_rating_submission':'passed','selection_choices':'passed','writing_generation_editor_lists':'passed','catalogue_suffix_grouping':'passed','exhausted_choices_and_directory_removal':'passed','desktop_mobile':'passed','js_errors':errors}))
    finally:
        server.should_exit=True;thread.join(timeout=10);service.close()
        try:next(fixture)
        except StopIteration:pass


if __name__=='__main__':main()
