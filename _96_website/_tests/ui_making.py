"""Process guide browser checks, including an explicitly synthetic A/B bank."""
import argparse
from copy import deepcopy
from functools import partial
from http.server import ThreadingHTTPServer
import io
import json
from pathlib import Path
import threading
import wave
from playwright.sync_api import sync_playwright
from ui_website import Routes, IMAGE, ROOT


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser',type=Path,required=True)
    parser.add_argument('--site',type=Path,default=ROOT/'_site')
    parser.add_argument('--screenshots',type=Path)
    args=parser.parse_args()
    if args.screenshots:args.screenshots.mkdir(parents=True,exist_ok=True)
    payload=json.loads((args.site/'how-its-made/tour.json').read_text())
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as wav:
        wav.setnchannels(2);wav.setsampwidth(2);wav.setframerate(44100);wav.writeframes(b'\0'*44100*4*8)
    errors=[]
    with ThreadingHTTPServer(('127.0.0.1',0),partial(Routes,directory=str(args.site.resolve()))) as server:
        threading.Thread(target=server.serve_forever,daemon=True).start()
        base=f'http://127.0.0.1:{server.server_port}/good-boy-records/'
        with sync_playwright() as p:
            browser=p.chromium.launch(executable_path=str(args.browser),headless=True,args=['--no-sandbox','--use-angle=swiftshader','--autoplay-policy=no-user-gesture-required'])
            for width,height in [(1440,1000),(3840,2160),(390,844)]:
                page=browser.new_page(viewport={'width':width,'height':height})
                page.on('pageerror',lambda e:errors.append(str(e)))
                page.goto(base+'how-its-made/')
                page.locator('[role=tab][aria-selected=true]').wait_for()
                assert page.locator('[role=tab]').count()==5
                assert page.locator('[role=tab][aria-selected=true]').count()==1
                page.get_by_role('tab',name='2. Generation',exact=True).click()
                assert page.locator('#stageTitle').inner_text()=='Generation'
                assert page.locator('[role=tab][aria-selected=true]').count()==1
                page.locator('[data-node=engine]').click()
                assert page.locator('#detailTitle').inner_text()=='Choose an execution path'
                page.locator('.next-steps button').filter(has_text='ComfyUI').click()
                assert page.locator('#detailTitle').inner_text()=='ComfyUI workflow'
                page.goto(base+'how-its-made/#generation/sampling')
                lab=page.locator('[data-experiment=sampling-steps]')
                lab.wait_for();lab.locator('input[type=range]').fill('50')
                assert lab.locator('output').inner_text()=='50'
                assert lab.locator('audio').is_hidden()
                assert 'not been published' in lab.locator('.experiment-state').inner_text()
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
                if width==3840:
                    assert page.locator('.making-wheel').bounding_box()['width']>=850
                    assert page.locator('#stepDetail').bounding_box()['width']>=1000
                if args.screenshots:page.screenshot(path=str(args.screenshots/f'how-made-{width}.png'))
                page.get_by_role('button',name='Platform & database',exact=True).click()
                assert page.locator('#topicDialog').is_visible()
                page.keyboard.press('Escape');assert not page.locator('#topicDialog').is_visible()
                page.get_by_role('tab',name='2. Generation',exact=True).focus();page.keyboard.press('ArrowRight')
                assert page.locator('#stageTitle').inner_text()=='Review'
                page.close();print(json.dumps({'width':width,'layout_and_navigation':'passed'}),flush=True)
            # Fixture clips exercise real playback mechanics, without claiming
            # any controlled generation results exist in the published content.
            fixture=deepcopy(payload)
            sampling=next(n for s in fixture['stages'] if s['id']=='generation' for n in s['nodes'] if n['id']=='sampling')
            experiment=next(b for b in sampling['body'] if b['type']=='experiment')
            experiment['samples']=[{'value':v,'audio':f'https://example.test/steps-{v}.mp3'} for v in [1,2]]
            sampling['body'] += [{'type':'paragraph','text':'<img src=x onerror=alert(1)>'},{'type':'media','kind':'image','src':'https://example.test/demo.gif','alt':'Animated example'},{'type':'media','kind':'video','src':'https://example.test/demo.mp4','poster':'https://example.test/demo.webp','caption':'Video example'}]
            page=browser.new_page(viewport={'width':1600,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
            page.route('**/how-its-made/tour.json',lambda r:r.fulfill(json=fixture))
            def media(route):
                if route.request.url.endswith('.mp3'):
                    raw=buffer.getvalue();request_range=route.request.headers.get('range','');headers={'Access-Control-Allow-Origin':'*','Accept-Ranges':'bytes'}
                    if request_range.startswith('bytes='):
                        first,last=request_range[6:].split('-',1);start=int(first or 0);end=min(int(last) if last else len(raw)-1,len(raw)-1)
                        headers['Content-Range']=f'bytes {start}-{end}/{len(raw)}';route.fulfill(status=206,content_type='audio/wav',body=raw[start:end+1],headers=headers)
                    else:route.fulfill(content_type='audio/wav',body=raw,headers=headers)
                elif route.request.url.endswith('.mp4'):route.fulfill(status=404,body='Expected unavailable fixture')
                else:route.fulfill(content_type='image/png',body=IMAGE)
            page.route('https://example.test/**',media)
            page.goto(base+'how-its-made/#generation/sampling');lab=page.locator('.playground');audio=lab.locator('audio')
            page.wait_for_function('document.querySelector(".experiment-audio")?.readyState>=1')
            audio.evaluate('a=>a.play()');page.wait_for_function('document.querySelector(".experiment-audio").currentTime>2.2');audio.evaluate('a=>a.pause()');position=audio.evaluate('a=>a.currentTime')
            lab.get_by_role('button',name='B · 2',exact=True).click()
            page.wait_for_function('document.querySelector(".experiment-audio").readyState>=1 && document.querySelector(".experiment-audio").currentTime>2')
            assert audio.evaluate('a=>a.paused')
            assert abs(audio.evaluate('a=>a.currentTime')-position)<.15
            audio.evaluate('a=>a.play()')
            lab.get_by_role('button',name='A · 1',exact=True).click()
            page.wait_for_function('!document.querySelector(".experiment-audio").paused && document.querySelector(".experiment-audio").currentTime>2')
            lab.locator('input').fill('3')
            assert audio.is_hidden() and audio.evaluate('a=>a.paused')
            assert 'No audio has been published' in lab.locator('.experiment-state').inner_text()
            assert page.get_by_text('<img src=x onerror=alert(1)>',exact=True).is_visible()
            assert page.locator('img[onerror]').count()==0
            assert page.locator('video').evaluate('v=>v.controls && v.preload==="none" && v.playsInline')
            assert page.locator('img[alt="Animated example"]').get_attribute('src').endswith('.gif')
            page.close();print(json.dumps({'synthetic_ab_playback':'passed','media_blocks':'passed','unavailable_samples':'passed','safe_text':'passed'}),flush=True)
            # The existing player opens the new guide and returns without
            # invoking the old canvas Knowledge mode.
            page=browser.new_page(viewport={'width':1440,'height':1000});page.on('pageerror',lambda e:errors.append(str(e)))
            def public_media(r):
                if r.request.url.endswith(('.flac','.mp3')):r.fulfill(content_type='audio/wav',body=buffer.getvalue(),headers={'Access-Control-Allow-Origin':'*'})
                elif r.request.url.endswith(('.jpg','.png','.webp')):r.fulfill(content_type='image/png',body=IMAGE)
                else:r.fulfill(status=404,body='No public fixture')
            page.route('https://pub-*.r2.dev/**',public_media)
            page.goto(base);page.wait_for_function('typeof tracks!=="undefined"&&tracks.length>0')
            assert page.locator('#knowledgeButton').text_content()=='How it’s made'
            page.locator('#knowledgeButton').click();frame=page.frame_locator('#gbrMaking iframe');frame.locator('[role=tab]').first.wait_for()
            assert page.locator('body').get_attribute('data-view')=='tracks'
            frame.locator('.back-player').click();page.wait_for_function('!document.body.classList.contains("gbr-making-open")')
            page.keyboard.press('k');frame.get_by_role('button',name='Evidence & analytics',exact=True).click();frame.get_by_role('link',name='Open the Signal Observatory →',exact=True).click()
            page.wait_for_function('document.body.classList.contains("gbr-observatory-open") && !document.body.classList.contains("gbr-making-open")')
            page.close();browser.close()
        server.shutdown()
    assert not errors,errors
    print(json.dumps({'embedded_player_and_analytics':'passed','javascript_errors':errors}))


if __name__=='__main__':main()
