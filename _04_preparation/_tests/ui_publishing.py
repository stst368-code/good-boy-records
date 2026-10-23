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
                        page.locator('#pubSaveConfig').click();page.wait_for_function("()=>document.getElementById('pubConfigMessage').textContent.includes('Saved')")
                        assert page.locator('#pubConfig_secret_key').input_value()==''
                        page.locator('#pubCheckConfig').click();page.wait_for_function("()=>document.getElementById('pubConfigMessage').textContent.includes('Ready')")
                        page.locator('#settingsDialog [data-close=settingsDialog]').click()
                        # Destination changed since the first preview: discard and
                        # explicitly prepare against the saved bucket before publish.
                        page.get_by_role('button',name='Discard preview').first.click();page.wait_for_function("()=>!document.querySelector('#pubHistory audio')")
                        page.locator('#pubPrepare').click();page.wait_for_selector('#pubHistory audio')
                        page.get_by_role('button',name='Publish this preview').first.click()
                        page.wait_for_selector('#pubHistory a',timeout=25000)
                        assert '?s=' in page.locator('#pubHistory a').first.get_attribute('href')
                        page.get_by_role('button',name='Withdraw from website').first.click()
                        page.wait_for_function("()=>document.getElementById('pubProject').options.length===0")
                    else:
                        page.get_by_role('button',name='Discard preview').first.click()
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                    page.screenshot(path=f'/tmp/gbr-publishing-{width}.png',full_page=True)
                    page.close()
                browser.close()
            assert not errors,errors
            print('Browser publishing: desktop/mobile preparation, audition polling, settings/secrets, connection check, publication, share link, withdrawal and discard passed.')
        finally:server.should_exit=True;thread.join(15)


if __name__=='__main__':main()
