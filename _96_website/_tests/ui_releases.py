"""Public player smoke test for a new R2 release, including its share token."""
import argparse,io,json,sys,threading,wave
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT.parent/'_04_preparation'))
from _gbr_preparation.release_catalogue import share_token
from ui_website import Routes,IMAGE


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    identifier='gbr-13-2-0000000000000000';token=share_token(identifier,'release')
    baseline=json.loads((ROOT/'_data/catalogue.json').read_text())
    track={'id':identifier,'shareToken':token,'release_id':'new-release','displayTitle':'New web release','title':'New web release',
        'variant':'Punk','language':'en','duration':8,'genre':{'parent_genre':'Rock','child_genre':'Punk','parent_colour':'#a89069','child_colour':'#c4aa82'},
        'audio':{'sources':{'mp3':'https://test-media.example/audio.mp3','flac':None}},'artwork_url':None,
        'lyrics':{'raw':'first line\nnext line'},'lyrics_url':'https://test-media.example/lyrics.json','playback_url':'https://test-media.example/playback.json'}
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as audio:audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(44100);audio.writeframes(b'\0'*44100*4*8)
    def media(route):
        path=urlsplit(route.request.url).path;headers={'Access-Control-Allow-Origin':'*'}
        if path=='/gbr/v2/catalogue.json':route.fulfill(json={'format':'gbr-public-releases-v1','tracks':[track]},headers=headers)
        elif path=='/audio.mp3':route.fulfill(body=buffer.getvalue(),content_type='audio/wav',headers=headers)
        elif path=='/lyrics.json':route.fulfill(json={'lines':[{'text':'first line','start':0,'end':4,'words':[{'text':'first','start':0,'end':2},{'text':'line','start':2,'end':4}]}]},headers=headers)
        elif path=='/playback.json':route.fulfill(json={'dance':{'events':[{'time':0,'strength':1,'duration':8}]}},headers=headers)
        elif path.endswith(('.png','.jpg','.webp')):route.fulfill(body=IMAGE,content_type='image/png',headers=headers)
        else:route.fulfill(status=404,body='',headers=headers)
    errors=[]
    with ThreadingHTTPServer(('127.0.0.1',0),partial(Routes,directory=str(ROOT/'_site'))) as server:
        threading.Thread(target=server.serve_forever,daemon=True).start()
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
            for route,width in [('/',1280),('/good-boy-records/',390)]:
                context=browser.new_context(viewport={'width':width,'height':900})
                context.route('https://pub-*.r2.dev/**',media);context.route('https://test-media.example/**',media)
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(f'http://127.0.0.1:{server.server_port}{route}?s={token}',wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks[index]?.id==='+json.dumps(identifier),timeout=30000)
                assert page.evaluate('mainTracks.length')==len(baseline['tracks'])+1 and page.evaluate('easterTracks.length')==len(baseline['easter']['tracks'])
                assert urlsplit(page.url).query=='' and page.evaluate('currentShareUrl()').endswith('?s='+token)
                assert page.evaluate('audioPools(tracks[index]).flac.length')==0
                page.wait_for_function('audio.readyState>=1');page.evaluate('setPower(true)');page.locator('#play').click()
                page.wait_for_function('!audio.paused&&lyricLines.length===1&&activePlaybackVisuals?.dance?.events.length===1')
                assert page.evaluate('audio.src')=='https://test-media.example/audio.mp3'
                context.close()
                print(f'Public release browser passed: {route} at {width}px',flush=True)
            browser.close()
        server.shutdown()
    assert not errors,errors


if __name__=='__main__':main()
