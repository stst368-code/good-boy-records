"""Real Preparation -> Publish -> Library workflows, rooms and import on desktop/mobile."""
import argparse
import json
from pathlib import Path
import socket
import shutil
import sys
import tempfile
import threading
import time
import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
for path in [ROOT,ROOT/'_04_preparation',ROOT/'_98_database',ROOT/'_03_review/_generation_review',ROOT/'_03_review/_generation_review/_tests']:
    sys.path.insert(0,str(path))
from _gbr_database.initialize import initialize
from _review.app import create_app
from conftest import complete,archive_all
from test_publishing import Store,image_bytes


def until(predicate,seconds=25):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        if predicate():return
        time.sleep(.03)
    raise AssertionError('Workflow timed out')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    errors=[];failures=[];checks=[]
    with tempfile.TemporaryDirectory() as temporary:
        directory=Path(temporary);source=directory/'input';source.mkdir()
        db=directory/'gbr.sqlite3';initialize(db)
        config=directory/'config.json';config.write_text(json.dumps({'input_directories':[str(source)],'database_path':str(db)}),encoding='utf-8')
        app=create_app(config,start_worker=False,database_path=db);review=app.state.service;prep=app.state.preparation;p=app.state.publisher
        repo=directory/'repo';writing=repo/'_01_writing/_built';writing.mkdir(parents=True)
        (writing/'track_list.csv').write_text('song_id,song_name,filename\n13,moses-supposes,song.yaml\n',encoding='utf-8')
        refs=repo/'_01_writing/_references';refs.mkdir();(refs/'styles.csv').write_text('cluster,style_primary\nRock,Punk\nJazz,Swing\n',encoding='utf-8')
        p.root=repo;p.config_path=directory/'publishing.json';p.secrets_path=directory/'publishing.secrets.json'
        prep.tracks=directory/'_tracks';prep.showcase=repo/'_05_showcase';p.showcase.root=prep.showcase;prep.references=directory/'artwork-references'
        prep.alignment_python=lambda:directory/'processing-not-installed'
        review.archiver.preparation_root=prep.tracks;review.archiver.showcase_root=prep.showcase
        store=Store();p.store_factory=lambda settings:store
        p.save_settings({'endpoint':'https://test.r2.cloudflarestorage.com','bucket':'gbr-public','public_base':'https://media.test','website_url':'https://test.github.io/good-boy-records/'})
        records=[]
        for index in range(3):
            audio=source/f'track-{index}.flac';t=np.arange(44100)/44100;wave=.15*np.sin(2*np.pi*(440+index*90)*t)
            sf.write(audio,np.stack([wave,wave],axis=1),44100,subtype='PCM_24')
            raw=('song_id: 13\r\nvariant_id: 2\r\ntitle: moses-supposes\r\ncluster: Rock\r\nstyle: Punk\r\nencoder_cfg: 1.7\r\ncaption: |\r\n  A Unicode caption: песня\r\nlyrics: |\r\n  first line\r\n  second line\r\n').encode('utf-8')
            if index==0:raw=raw.replace(b'song_id: 13\r\nvariant_id: 2\r\n',b'')
            audio.with_suffix('.yaml').write_bytes(raw);aid=review.indexer.index(audio);gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
            review.set_flags(gid,review.default_reviewer_id,distribution=True);complete(review,gid);archive_all(review);records.append((gid,raw))
        cover=directory/'cover.png';cover.write_bytes(image_bytes())
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            until(lambda:server.started)
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
                for width,(gid,raw) in zip([1440,3840,390],records):
                    page=browser.new_page(viewport={'width':width,'height':1000},is_mobile=width<700,has_touch=width<700)
                    page.on('pageerror',lambda error:errors.append(str(error)))
                    page.on('response',lambda response:failures.append((response.status,response.url)) if response.status>=500 else None)
                    page.goto(f'http://127.0.0.1:{port}');page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click()
                    page.locator('#preparationTab').click();page.wait_for_selector('#prepProject option',state='attached');page.locator('#prepProject').select_option(gid)
                    page.wait_for_function("()=>document.getElementById('prepYamlRaw').textContent.includes('encoder_cfg: 1.7')")
                    page.wait_for_selector('#prepQueueJobs .prep-task')
                    assert 'Processing environment missing' in page.locator('#prepQueueStatus').text_content()
                    assert 'песня' in page.locator('#prepYamlCaption').text_content()
                    assert 'first line\n' in page.locator('#prepYamlLyrics').text_content().replace('\r\n','\n')
                    page.wait_for_function("()=>document.getElementById('prepAlignmentStatus').textContent.includes('Alignment: waiting')")
                    original_raw=raw
                    if width==1440:
                        page.locator('#prepIdentitySong').select_option('13');page.locator('#prepIdentityVariant').select_option('2');page.locator('#prepSaveIdentity').click()
                        page.wait_for_function("()=>document.getElementById('prepYamlRaw').textContent.includes('song_id: 13')")
                        raw=raw.replace(b'title:',b'song_id: 13\r\nvariant_id: 2\r\ntitle:')+b'explicit: yes\r\n'
                        Path(prep.metadata.read(gid)['path']).write_bytes(raw)
                        page.wait_for_function("()=>document.getElementById('prepYamlRaw').textContent.includes('explicit: yes')")
                    page.locator('#prepArtworkFile').set_input_files(cover)
                    page.locator('#prepImportArtwork').click()
                    page.wait_for_function("()=>document.getElementById('prepReleaseArtwork').value!==''")
                    page.locator('#prepApproveLyrics').click();page.wait_for_function("()=>document.getElementById('prepApproveLyrics').textContent==='Approved'")
                    page.screenshot(path=f'/tmp/gbr-stage-preparation-{width}.png',full_page=True)
                    page.locator('#prepComplete').click();page.wait_for_selector('#publishingView:not([hidden])')
                    page.wait_for_selector(f'[data-pending] [data-publish]',timeout=25000)
                    assert review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(gid,))['status']=='selected'
                    assert not any(item['generation_id']==gid for item in p.library.projects()['items'])
                    assert not any(item['generation_id']==gid for item in prep.projects()['projects'])
                    pending=p.projects()['projects'];assert any(item['generation_id']==gid for item in pending)
                    page.screenshot(path=f'/tmp/gbr-stage-publish-{width}.png',full_page=True)
                    page.locator('[data-publish]').first.click()
                    until(lambda:review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(gid,))['status']=='published')
                    page.locator('#libraryTab').click();page.wait_for_selector(f'[data-edit="{gid}"]');page.locator(f'[data-edit="{gid}"]').click()
                    page.wait_for_selector('#libraryEdit:not([hidden])');page.locator('#libraryTitle').fill('Retitled '+str(width));page.locator('#libraryPublish').click()
                    until(lambda:p.library.record(gid)['track']['displayTitle']=='Retitled '+str(width))
                    assert next(prep.source(gid)[1].parent.glob('*.yaml')).read_bytes()==raw
                    assert review.db.one('SELECT raw_yaml_bytes FROM generation_metadata WHERE generation_id=?',(gid,))['raw_yaml_bytes']==original_raw
                    assert not p.projects()['projects']
                    page.locator('#libraryTimeline').click();page.wait_for_selector('#libraryEditorContainer #preparationView:not([hidden])')
                    assert 'encoder_cfg: 1.7' in page.locator('#prepYamlRaw').text_content()
                    page.locator('#prepBackLibrary').click();page.wait_for_selector('#libraryManager:not([hidden])')
                    page.screenshot(path=f'/tmp/gbr-stage-library-{width}.png',full_page=True)
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+2')
                    if width==390:
                        page.locator('[data-library-tab=rooms]').click()
                        for name,entry in [('Demos',records[0][0]),('Rarities',records[1][0])]:
                            page.locator('#libraryAddRoom').click();row=page.locator('.library-room').last
                            row.locator('[data-field=name]').fill(name);row.locator('[data-field=name]').dispatch_event('change')
                            track_id=p.library.record(entry)['track']['id'];row.locator('[data-field=entry_track_id]').select_option(track_id)
                        page.locator('#librarySaveRooms').click();page.wait_for_function("()=>document.getElementById('libraryStatus').textContent.includes('saved to the website')")
                        rooms=p.library.settings()['rooms'];assert len(rooms)==5 and rooms[-1]['entry_track_id']!=rooms[-2]['entry_track_id']
                        page.screenshot(path='/tmp/gbr-library-rooms-mobile.png',full_page=True)
                        page.locator('[data-library-tab=recordings]').click();page.locator(f'[data-edit="{gid}"]').click()
                        page.locator('#libraryRoom').select_option(rooms[-2]['id']);page.locator('#libraryPublish').click()
                        until(lambda:p.library.record(gid)['visibility']==rooms[-2]['id'])
                        assert 'room='+rooms[-2]['id'] in review.db.one('SELECT external_reference FROM publishing_selection WHERE generation_id=?',(gid,))['external_reference']
                        page.locator('[data-library-tab=import]').click()
                        old=directory/'porto-showcase'/'easter';old.mkdir(parents=True);mp3=old/'legacy.mp3'
                        audio_source=next(Path(item['path']) for item in p.library.record(gid)['recipe']['files'] if item['content_type']=='audio/mpeg')
                        shutil.copyfile(audio_source,mp3);mp3.with_suffix('.yaml').write_text('title: Legacy Easter sample\nlyrics: test\n',encoding='utf-8')
                        page.locator('#libraryImportPath').fill(str(old.parent));page.locator('#libraryImportSync').uncheck();page.locator('#libraryImportStart').click()
                        until(lambda:p.library_import.state()['status'] in {'done','completed_with_issues'})
                        assert p.library_import.state()['completed']==1,p.library_import.state()
                    checks.append({'width':width,'passed':True});print(json.dumps(checks[-1]),flush=True);page.close()
                browser.close()
            assert not errors,errors
            assert not failures,failures
            print(json.dumps({'workflows':checks,'js_errors':errors,'server_errors':failures,'checks':'staged publication, YAML, local audio reuse, Library revisions, rooms and MP3-only import'}))
        finally:server.should_exit=True;thread.join(15)


if __name__=='__main__':main()
