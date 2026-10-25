"""Cover discovery and R2-only hidden playback at desktop and mobile sizes."""
import argparse
from functools import partial
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
from urllib.parse import urlsplit
import wave

from playwright.sync_api import sync_playwright
from ui_website import Routes, IMAGE

ROOT=Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--browser',required=True);args=parser.parse_args()
    def song(identifier,title):
        return {'id':identifier,'title':title,'displayTitle':title,'variant':'Rock',
                'genre':{'parent_genre':'Rock','child_genre':'Rock','parent_colour':'#a89069','child_colour':'#c4aa82'},
                'artwork_url':'https://media.test/'+identifier+'.jpg','audio':{'sources':{'mp3':'https://media.test/'+identifier+'.mp3'}},
                'release_id':'release-'+identifier,'lyrics':{'raw':''}}
    chosen=song('normal-chosen','Chosen cover');other=song('normal-other','Other cover');another=song('normal-another','Another entrance')
    legacy=song('old-secret','Old secret');secret=song('gbr-99-1-secret','Secret recording');secret['visibility']='hidden'
    demo=song('gbr-99-2-demo','Demo recording');demo['visibility']='demos'
    saved={'tracks':[chosen,other,another],'easter':{'tracks':[legacy]}}
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as audio:
        audio.setnchannels(2);audio.setsampwidth(2);audio.setframerate(44100);audio.writeframes(b'\0'*44100*4*8)
    errors=[];empty_hidden=False
    class Handler(Routes):
        def do_GET(self):
            if urlsplit(self.path).path.endswith('/catalogue.json'):
                raw=json.dumps(saved).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(raw)
            else:super().do_GET()
    with ThreadingHTTPServer(('127.0.0.1',0),partial(Handler,directory=str(ROOT/'_site'))) as server:
        threading.Thread(target=server.serve_forever,daemon=True).start();base=f'http://127.0.0.1:{server.server_port}'
        with sync_playwright() as pw:
            browser=pw.chromium.launch(executable_path=args.browser,headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
            for width,height in [(1920,1080),(3840,2160),(390,844)]:
                requests=[]
                context=browser.new_context(viewport={'width':width,'height':height},is_mobile=width<700,has_touch=width<700)
                context.add_init_script('const raf=requestAnimationFrame.bind(window);let paints=0;window.requestAnimationFrame=cb=>document.querySelector(".app")&&cb.name==="draw"&&++paints>10?0:raf(cb)')
                def media(route):
                    path=urlsplit(route.request.url).path;requests.append(path);headers={'Access-Control-Allow-Origin':'*'}
                    if path=='/gbr/v2/library.json':route.fulfill(json={'format':'gbr-library-v1','sensitivity':1,'display_order':'genre','rooms':[{'id':'public','name':'Main player','enabled':True},{'id':'easter','name':'Easter eggs','enabled':True},{'id':'hidden','name':'Secret room','enabled':True,'entry_track_id':chosen['id']},{'id':'demos','name':'Demos','enabled':True,'entry_track_id':another['id']}]},headers=headers)
                    elif path=='/gbr/v2/rooms/demos/catalogue.json':route.fulfill(json={'format':'gbr-room-releases-v1','tracks':[demo]},headers=headers)
                    elif path=='/gbr/v2/catalogue.json':route.fulfill(json={'format':'gbr-public-releases-v1','tracks':[]},headers=headers)
                    elif path=='/gbr/v2/hidden/catalogue.json':route.fulfill(json={'format':'gbr-hidden-releases-v1','tracks':[] if empty_hidden else [secret],'withdrawn':[legacy['id']] if empty_hidden else []},headers=headers)
                    elif path=='/gbr/v2/easter-egg.json':route.fulfill(json={'format':'gbr-easter-egg-v1','enabled':True,'track_id':chosen['id']},headers=headers)
                    elif path.endswith('.mp3'):route.fulfill(body=buffer.getvalue(),content_type='audio/wav',headers=headers)
                    elif path.endswith('.jpg'):route.fulfill(body=IMAGE,content_type='image/png',headers=headers)
                    else:route.fulfill(status=404,body='',headers=headers)
                context.route('https://pub-*.r2.dev/**',media);context.route('https://media.test/**',media)
                page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'/good-boy-records/',wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks.length===3&&document.getElementById("loading").hidden')
                page.evaluate('camera.x=camera.targetX=playingAnchor.x;camera.y=camera.targetY=playingAnchor.y;camera.zoom=camera.targetZoom=.7;draw()')
                page.wait_for_function('(()=>{const r=document.getElementById("playingCoverScreen").getBoundingClientRect(),s=document.getElementById("stage").getBoundingClientRect();return r.width>0&&r.left>=s.left&&r.right<=s.right&&r.top>=s.top&&r.bottom<=s.bottom})()')
                page.evaluate('window.eggTaps=[];const tap=GBRcoverEgg.tap.bind(GBRcoverEgg);GBRcoverEgg.tap=t=>{window.eggTaps.push({id:t?.id,time:performance.now()});return tap(t)}')
                assert '/gbr/v2/hidden/catalogue.json' not in requests
                def select(identifier):page.evaluate('(id)=>select(tracks.findIndex(t=>t.id===id),false)',identifier)
                def tap():
                    rect=page.locator('#playingCoverScreen').bounding_box();x=rect['x']+rect['width']/2;y=rect['y']+rect['height']/2
                    if width<700:page.touchscreen.tap(x,y)
                    else:page.mouse.click(x,y)
                select(other['id']);tap();tap();tap();assert not page.url.endswith('hidden.html')
                select(chosen['id']);tap();tap();assert not page.url.endswith('hidden.html')
                # A track change and a cancelled gesture cannot complete a partial sequence.
                select(other['id']);select(chosen['id']);tap();assert not page.url.endswith('hidden.html')
                page.evaluate('window.GBRcoverEgg.reset()')
                rect=page.locator('#playingCoverScreen').bounding_box();x=rect['x']+rect['width']/2;y=rect['y']+rect['height']/2
                page.mouse.move(x,y);page.mouse.down();page.mouse.move(x+25,y);page.mouse.up()
                tap();tap();assert not page.url.endswith('hidden.html')
                tap()
                try:page.wait_for_url('**/hidden.html',timeout=10000)
                except Exception:
                    print(page.evaluate('({taps:window.eggTaps,track:tracks[index]?.id,mode:viewMode,pointer,rect:document.getElementById("playingCoverScreen").getBoundingClientRect().toJSON()})'),flush=True)
                    raise
                page.wait_for_function('typeof tracks!=="undefined"&&easter&&tracks.length===1&&document.getElementById("loading").hidden')
                assert page.evaluate('mainTracks.length')==0
                assert set(page.evaluate('tracks.map(t=>t.id)'))=={secret['id']}
                assert not set(page.evaluate('tracks.map(t=>t.id)'))&{chosen['id'],other['id']}
                select(secret['id']);link=page.evaluate('currentShareUrl()');assert '/hidden.html?s=' in link
                page.locator('#play').click();page.wait_for_function('!audio.paused&&audio.currentTime>0')
                assert page.evaluate('audio.src')=='https://media.test/gbr-99-1-secret.mp3'
                page.goto(link,wait_until='domcontentloaded');page.wait_for_function('typeof tracks!=="undefined"&&tracks[index]?.id==="gbr-99-1-secret"')
                assert urlsplit(page.url).path=='/good-boy-records/hidden.html' and not urlsplit(page.url).query
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                page.screenshot(path=f'/tmp/gbr-hidden-room-{width}.png',full_page=True)
                page.get_by_role('button',name='Main player',exact=True).click();page.wait_for_url('**/good-boy-records/')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks.length===3&&!easter')
                # Original Easter content and each additional room remain isolated.
                page.goto(base+'/good-boy-records/easter.html',wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks.length===1&&document.getElementById("loading").hidden')
                assert page.evaluate('tracks[0].id')==legacy['id']
                page.goto(base+'/good-boy-records/',wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks.length===3&&document.getElementById("loading").hidden')
                page.evaluate('camera.x=camera.targetX=playingAnchor.x;camera.y=camera.targetY=playingAnchor.y;camera.zoom=camera.targetZoom=.7;draw()')
                select(another['id']);tap();tap();tap()
                page.wait_for_url('**/hidden.html?room=demos')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks.length===1&&document.getElementById("loading").hidden')
                assert page.evaluate('tracks[0].id')==demo['id']
                demo_link=page.evaluate('currentShareUrl()');assert 'room=demos' in demo_link
                page.goto(demo_link,wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks[index]?.id==="gbr-99-2-demo"')
                assert 'room=demos' in page.url
                page.reload(wait_until='domcontentloaded')
                page.wait_for_function('typeof tracks!=="undefined"&&tracks[0]?.id==="gbr-99-2-demo"')
                if width==390:
                    empty_hidden=True;page.goto(base+'/good-boy-records/hidden.html',wait_until='domcontentloaded')
                    page.wait_for_function('document.getElementById("loading").textContent.includes("waiting for its first track")')
                    assert page.evaluate('tracks.length===0&&mainTracks.length===0')
                    page.get_by_role('button',name='Main player',exact=True).click();page.wait_for_url('**/good-boy-records/')
                context.close();print(f'Cover trigger, isolation, playback, share and return passed: {width}px',flush=True)
            browser.close()
        server.shutdown()
    assert not errors,errors


if __name__=='__main__':main()
