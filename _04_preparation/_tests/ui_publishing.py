"""Real app release flow against an isolated R2-compatible memory store."""
import argparse,json,re,socket,sys,tempfile,threading,time
from pathlib import Path
import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[2]
for p in [ROOT,ROOT/'_98_database',ROOT/'_03_review/_generation_review',Path(__file__).parent]:sys.path.insert(0,str(p))
from _gbr_database.initialize import initialize
from _review.app import create_app
from test_publishing import Store


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    errors=[]
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory);source=root/'input';source.mkdir();db=root/'shared.sqlite3';initialize(db)
        config=root/'config.json';config.write_text(json.dumps({'input_directories':[str(source)],'database_path':str(db)}))
        app=create_app(config,start_worker=False);review=app.state.service;prep=app.state.preparation;publisher=app.state.publisher
        prep.tracks=root/'_tracks';review.archiver.preparation_root=prep.tracks
        prep.showcase=root/'_05_showcase';publisher.showcase.root=prep.showcase;review.archiver.showcase_root=prep.showcase
        publisher.config_path=root/'publishing.json';publisher.secrets_path=root/'publishing.secrets.json'
        store=Store();publisher.store_factory=lambda settings:store
        audio=source/'song.flac';t=np.arange(4*44100)/44100;sf.write(audio,np.stack([.1*np.sin(2*np.pi*330*t)]*2,axis=1),44100,subtype='PCM_24')
        audio.with_suffix('.yaml').write_text('song_id: 13\nvariant_id: 2\ntitle: moses-supposes\ncluster: Rock\nstyle: Punk\nlanguage: en\nlyrics: |\n  one line\n  next line\n')
        aid=review.indexer.index(audio);gid=review.db.one('SELECT id FROM generations')['id'];review.queues.open(review.default_reviewer_id,gid)
        review.review(gid,dict(audio_quality=5,composition=4,consistency=4,realism=4),'full',distribution=True)
        for op in review.db.all("SELECT * FROM archive_operations WHERE status='pending'"):review.archiver.process(op)
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
        server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error',loop='_review.server_loop:create_loop'))
        thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
        try:
            deadline=time.monotonic()+20
            while not server.started and time.monotonic()<deadline:time.sleep(.05)
            assert server.started
            with sync_playwright() as pw:
                browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
                for width in [1440,390]:
                    review.publishing(gid,'selected')
                    page=browser.new_page(viewport={'width':width,'height':1000})
                    page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{port}');page.wait_for_selector('#reviewerDialog[open]')
                    page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#publishingTab').click()
                    page.wait_for_function("()=>document.getElementById('pubProject').options.length===1")
                    page.locator('#pubPrepare').click();page.wait_for_function("()=>document.getElementById('pubHistory').textContent.includes('Preview · prepared')",timeout=25000)
                    if width==1440:
                        page.locator('#pubHistory audio').first.evaluate('(a)=>a.play()')
                        page.wait_for_timeout(4500)
                        # An unchanged history poll keeps the same media element.
                        assert page.locator('#pubHistory audio').first.evaluate('(a)=>a.currentTime')>1
                        page.locator('#settingsButton').click();page.locator('#pubConfig summary').click()
                        page.wait_for_function("()=>document.getElementById('pubConfig_public_base').value.startsWith('https://')")
                        for field,value in [('endpoint','https://test.r2.cloudflarestorage.com'),('bucket','gbr-public'),('public_base','https://media.test'),('website_url','https://test.github.io/good-boy-records/'),('access_key','saved-key'),('secret_key','saved-secret')]:page.locator('#pubConfig_'+field).fill(value)
                        from unittest.mock import patch
                        with patch('_gbr_preparation.rclone.connection',return_value={'endpoint':'https://test.r2.cloudflarestorage.com','_access_key':'existing-rclone-key','_secret_key':'existing-rclone-secret','_session_token':''}):
                            page.locator('#pubUseRclone').click()
                            page.wait_for_function("()=>document.getElementById('pubConfigMessage').textContent.includes('using rclone r2')")
                            assert publisher.settings()['rclone_remote']=='r2' and not publisher.secrets_path.exists()
                            assert page.locator('#pubConfig_access_key').input_value()=='' and page.locator('#pubConfig_secret_key').input_value()==''
                            assert 'existing-rclone-secret' not in page.locator('body').inner_text()
                            page.screenshot(path='/tmp/gbr-rclone-settings.png',full_page=True)
                        # Explicitly entering manual keys can still replace the source.
                        page.locator('#pubConfig_access_key').fill('saved-key');page.locator('#pubConfig_secret_key').fill('saved-secret')
                        page.locator('#pubSaveConfig').click();page.wait_for_function("()=>document.getElementById('pubConfigMessage').textContent==='Saved. Run the public connection check.'")
                        assert page.locator('#pubConfig_secret_key').input_value()==''
                        page.locator('#pubCheckConfig').click();page.wait_for_function("()=>document.getElementById('pubConfigMessage').textContent.includes('Ready')")
                        page.locator('#settingsDialog [data-close=settingsDialog]').click()
                        # Destination changed since the first preview: discard and
                        # explicitly prepare against the saved bucket before publish.
                        page.get_by_role('button',name='Discard preview').first.click();page.wait_for_function("()=>!document.querySelector('#pubHistory audio')")
                        page.locator('#pubPrepare').click();page.wait_for_selector('#pubHistory audio')
                        page.get_by_role('button',name='Complete & publish this preview').first.click()
                        page.wait_for_selector('#pubHistory a',timeout=25000)
                        assert '?s=' in page.locator('#pubHistory a').first.get_attribute('href')
                        page.get_by_role('button',name='Withdraw from website').first.click()
                        page.wait_for_function("()=>document.getElementById('pubProject').options.length===0")
                    else:
                        page.get_by_role('button',name='Discard preview').first.click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=f'/tmp/gbr-publishing-{width}.png',full_page=True)
                    page.close()
                for width,frequency in [(1440,550),(390,660)]:
                    # Exercise the single action in Preparation with fresh
                    # recordings, not a republish of the earlier receipt.
                    candidate=source/f'completion-{width}.flac'
                    sf.write(candidate,np.stack([.1*np.sin(2*np.pi*frequency*t)]*2,axis=1),44100,subtype='PCM_24')
                    raw=audio.with_suffix('.yaml').read_bytes() if audio.with_suffix('.yaml').exists() else review.db.one('SELECT raw_yaml_bytes FROM generation_metadata LIMIT 1')['raw_yaml_bytes']
                    candidate.with_suffix('.yaml').write_bytes(raw)
                    aid=review.indexer.index(candidate);current=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
                    review.queues.open(review.default_reviewer_id,current)
                    review.review(current,dict(audio_quality=5,composition=4,consistency=4,realism=4),'full',distribution=True)
                    for op in review.db.all("SELECT * FROM archive_operations WHERE status='pending'"):review.archiver.process(op)
                    page=browser.new_page(viewport={'width':width,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{port}');page.wait_for_selector('#reviewerDialog[open]')
                    page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#preparationTab').click()
                    page.wait_for_selector('#prepWorkspace:not([hidden])');page.locator('#prepProject').select_option(current)
                    from PIL import Image
                    artwork=root/'cover.png';Image.new('RGB',(64,64),'#8f7755').save(artwork)
                    page.locator('#prepArtworkFile').set_input_files(artwork);page.locator('#prepImportArtwork').click()
                    page.wait_for_function("()=>document.getElementById('prepReleaseArtwork').value!==''")
                    page.screenshot(path=f'/tmp/gbr-completion-{width}.png',full_page=True)
                    if width==390:page.locator('#prepReleaseVisibility').select_option('hidden')
                    page.locator('#prepComplete').click()
                    page.wait_for_selector('#publishingView:not([hidden])')
                    page.wait_for_function("()=>document.getElementById('pubHistory').textContent.includes('Publication · published')",timeout=25000)
                    deadline=time.monotonic()+20
                    while time.monotonic()<deadline:
                        if review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(current,))['status']=='published':break
                        time.sleep(.03)
                    assert review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(current,))['status']=='published'
                    moved=prep.source(current)[1];assert moved.is_relative_to(prep.showcase)
                    assert next(moved.parent.glob('*.yaml')).read_bytes()==raw
                    assert not (prep.tracks/aid).exists()
                    prepared=review.db.one("SELECT id,recipe_json FROM processing_tasks WHERE generation_id=? AND kind='release_prepare' AND status='done' ORDER BY rowid DESC LIMIT 1",(current,))
                    public_track=json.loads(prepared['recipe_json'])['track']
                    pointer='gbr/v2/hidden/catalogue.json' if width==390 else 'gbr/v2/catalogue.json'
                    assert any(t['id']==public_track['id'] for t in json.loads(store.objects[pointer])['tracks'])
                    if width==390:
                        assert not any(t['id']==public_track['id'] for t in json.loads(store.objects['gbr/v2/catalogue.json'])['tracks'])
                    else:
                        page.locator('#settingsButton').click();page.locator('#pubEaster summary').click()
                        page.wait_for_function("()=>document.getElementById('pubEasterCover').options.length>1")
                        page.locator('#pubEasterCover').select_option(public_track['id'])
                        assert page.locator('#pubEasterPreview').is_visible()
                        page.wait_for_function('()=>document.getElementById("pubEasterPreview").naturalWidth>0')
                        page.locator('#pubEasterSave').click()
                        page.wait_for_function("()=>document.getElementById('pubEasterStatus').textContent.includes('Saved')")
                        assert json.loads(store.objects['gbr/v2/easter-egg.json'])['track_id']==public_track['id']
                        page.screenshot(path='/tmp/gbr-easter-settings.png',full_page=True)
                        page.locator('#settingsDialog [data-close=settingsDialog]').click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.close()
                from test_legacy_showcase import make_legacy
                publisher.root=root/'repo';writing=publisher.root/'_01_writing/_built';writing.mkdir(parents=True)
                shutil=__import__('shutil');shutil.copyfile(ROOT/'_01_writing/_built/track_list.csv',writing/'track_list.csv')
                for width,frequency in [(1440,770),(390,880)]:
                    candidate=source/f'legacy-{width}.flac';sf.write(candidate,np.stack([.1*np.sin(2*np.pi*frequency*t)]*2,axis=1),44100,subtype='PCM_24')
                    candidate.with_suffix('.yaml').write_bytes(raw)
                    aid=review.indexer.index(candidate);current=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
                    review.queues.open(review.default_reviewer_id,current);review.review(current,dict(audio_quality=5,composition=4,consistency=4,realism=4),'full',distribution=True)
                    for op in review.db.all("SELECT * FROM archive_operations WHERE status='pending'"):review.archiver.process(op)
                    old_source=prep.source(current)[1]
                    legacy_data=make_legacy((review,prep,publisher,store,current,old_source,raw))
                    old_root=legacy_data[7];track=legacy_data[8];track['id']=f'legacy-ui-{width}';track['shareToken']='ui'+str(width).zfill(6)
                    (publisher.root/'_96_website/_data/catalogue.json').write_text(json.dumps({'tracks':[track]}))
                    bad=old_root/'unmatched';bad.mkdir(exist_ok=True);(bad/'bad.flac').write_bytes(b'invalid audio')
                    page=browser.new_page(viewport={'width':width,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
                    page.goto(f'http://127.0.0.1:{port}');page.wait_for_selector('#reviewerDialog[open]');page.locator('#knownReviewers button',has_text='Simon').click();page.locator('#publishingTab').click()
                    page.locator('#pubLegacy summary').click();page.wait_for_function("()=>!document.getElementById('pubLegacyScan').disabled")
                    page.locator('#pubLegacyScan').click();page.wait_for_function("()=>document.getElementById('pubLegacyStatus').textContent.includes('1 ready · 1 blocked')")
                    assert page.locator('#pubLegacyRows input[type=checkbox]').count()==1
                    page.locator('#pubLegacyApply').click();deadline=time.monotonic()+25
                    while time.monotonic()<deadline:
                        if review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(current,))['status']=='published':break
                        page.wait_for_timeout(100)
                    assert review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(current,))['status']=='published'
                    assert prep.source(current)[1].is_relative_to(prep.showcase)
                    public=next(t for t in json.loads(store.objects['gbr/v2/catalogue.json'])['tracks'] if t['id']==track['id'])
                    assert public['shareToken']==track['shareToken'] and public['lyrics_url']
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=f'/tmp/gbr-legacy-migration-{width}.png',full_page=True);page.close()
                browser.close()
            assert not errors,errors
            print('Browser publishing: desktop/mobile preview, settings, check, publication, share link, withdrawal, discard, artwork attachment, one-action completion and legacy migration/alignment passed.')
        finally:server.should_exit=True;thread.join(15)


if __name__=='__main__':main()
