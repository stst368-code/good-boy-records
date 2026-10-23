"""Root/project-route browser regression using synthetic media, not full songs."""
import argparse
import base64
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import sys
import threading
from urllib.parse import urlsplit
import wave
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '_tools'))
from build_catalogue import share_token
IMAGE = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR1sAAAAASUVORK5CYII=')


class Routes(SimpleHTTPRequestHandler):
    def do_GET(self):
        for prefix in ('/porto/good-boy-records', '/good-boy-records'):
            if urlsplit(self.path).path.startswith(prefix + '/'):
                self.path = self.path[len(prefix):]
                break
        super().do_GET()

    def log_message(self, *_args):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--site', type=Path, default=ROOT / '_site')
    args = parser.parse_args()
    catalogue = json.loads((args.site / 'catalogue.json').read_text())
    recordings = json.loads((args.site / 'data/analytics/generations.json').read_text())['generations']
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000 * 8)
    errors, failures, media_paths, completed = [], [], [], []

    def media(route):
        path = urlsplit(route.request.url).path
        media_paths.append(path)
        headers = {'Access-Control-Allow-Origin': '*'}
        if path.endswith(('.flac', '.mp3', '.wav', '.ogg', '.m4a', '.opus')):
            route.fulfill(status=200, content_type='audio/wav', body=buffer.getvalue(), headers=headers)
        elif path.endswith(('.png', '.jpg', '.jpeg', '.webp')):
            route.fulfill(status=200, content_type='image/png', body=IMAGE, headers=headers)
        else:
            route.fulfill(status=200, json={'format': 'gbr-playback-v1', 'lines': [], 'frames': []}, headers=headers)

    with ThreadingHTTPServer(('127.0.0.1', 0), partial(Routes, directory=str(args.site.resolve()))) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        base = 'http://127.0.0.1:' + str(server.server_port)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(args.browser), headless=True,
                args=['--no-sandbox', '--use-angle=swiftshader', '--autoplay-policy=no-user-gesture-required'])
            for prefix, width, height in [('/',1440,1000),('/good-boy-records/',1280,900),('/good-boy-records/',390,844),('/porto/good-boy-records/',320,740)]:
                context = browser.new_context(viewport={'width': width, 'height': height}, is_mobile=width<700, has_touch=width<700)
                context.route('https://pub-*.r2.dev/**', media)
                page = context.new_page()
                page.set_default_timeout(15000)
                page.on('pageerror', lambda error: errors.append({'message': error.message, 'stack': error.stack}))
                page.on('response', lambda response: failures.append(response.url) if response.url.startswith(base) and response.status>=400 else None)
                page.goto(base+prefix, wait_until='domcontentloaded')
                page.wait_for_function('typeof mainTracks!=="undefined" && mainTracks.length==='+str(len(catalogue['tracks'])))
                assert page.evaluate('easterTracks.length') == len(catalogue['easter']['tracks'])
                assert page.evaluate('audio.volume') == .5
                assert '/showcase/' in page.evaluate('audio.src')
                first = page.evaluate('({id:mainTracks[0].id,variant:variantOf(mainTracks[0]),token:shareTokenFor(mainTracks[0],"m")})')
                assert first['token'] == share_token(first['id'], first['variant'])
                assert page.evaluate('(()=>{const t=mainTracks[0],k=shareTokenFor(t,"m");return shareTokenFor({...t,variant:"edited",shareToken:k},"m")===k})()')
                page.wait_for_function('audio.readyState>=1')
                page.evaluate('setPower(true)')
                page.locator('#play').click()
                page.wait_for_function('!audio.paused')
                page.evaluate('audio.pause()')
                page.locator('#tracksButton').click()
                assert page.locator('.track-song-group').count()>0
                page.locator('#tracksButton').click()
                page.wait_for_function('openTab===null')
                page.locator('#filterButton').click()
                before = page.evaluate('tracks.filter(isTrackEligible).length')
                page.locator('.filter-clusters button').first.click()
                assert page.evaluate('tracks.filter(isTrackEligible).length') < before
                page.locator('.filter-clusters button').first.click()
                assert page.evaluate('tracks.filter(isTrackEligible).length') == before
                page.locator('#panelClose').click()
                page.locator('#storyButton').click()
                assert page.locator('#panelBody').inner_text().strip()
                page.evaluate('Object.assign(tracks[index],{song_id:"13",variant_id:"2",explicit:false,generation_datetime:"2610190201"});renderStoryPanel()')
                facts=page.locator('#panelBody').inner_text()
                for label in ['Song ID','Variant ID','Explicit','No','2610190201']:
                    assert label.casefold() in facts.casefold(),(label,facts)
                page.locator('#panelClose').click()
                page.locator('#knowledgeButton').click()
                page.wait_for_function('knowledgeReady && knowledgeNodes.length>0')
                assert page.evaluate('GBR_KNOWLEDGE_NODES.find(n=>n.id==="generation-engine").markdown.includes("native")')
                page.locator('#knowledgeButton').click()
                for hidden in (False, True):
                    page.evaluate('setEasterState('+str(hidden).lower()+');select('+('0' if hidden else 'Math.min(4,tracks.length-1)')+',false)')
                    identifier = page.evaluate('tracks[index].id')
                    link = page.evaluate('currentShareUrl()')
                    assert urlsplit(link).path == prefix
                    page.goto(link, wait_until='domcontentloaded')
                    try:page.wait_for_function('typeof tracks!=="undefined" && tracks[index]?.id==='+json.dumps(identifier))
                    except Exception:
                        print(json.dumps({'expected':identifier,'link':link,'state':page.evaluate('({id:tracks[index]?.id,token:pendingShareToken,hasToken:shareIndex.has(pendingShareToken),main:mainTracks.length,hidden:easterTracks.length})'),'errors':errors}),flush=True)
                        raise
                    assert urlsplit(page.url).query == ''
                    assert page.evaluate('easter') == hidden
                page.locator('#gbrAnalyticsButton').click()
                frame = page.frame_locator('#gbrObservatory iframe')
                frame.locator('#sideCount').wait_for(state='attached')
                page.wait_for_function('document.querySelector("#gbrObservatory iframe").contentDocument.querySelector("#sideCount").textContent.includes('+json.dumps(str(len(recordings)))+')')
                for view in ['parameters','audio','precision','archive','explorer']:
                    frame.locator('[data-view="'+view+'"]').click()
                    assert frame.locator('#viewContent').inner_text().strip()
                    if view=='audio':
                        assert frame.locator('.signal-radar').is_visible()
                        assert 'NaN' not in frame.locator('.signal-radar').inner_html()
                if not frame.locator('#search').is_visible():
                    frame.locator('#efFilter').click()
                frame.locator('#search').fill('__not_a_real_track__')
                assert '0' in frame.locator('#metrics').inner_text()
                frame.locator('#reset').click()
                frame.locator('.home-link').click()
                page.wait_for_function('!document.body.classList.contains("gbr-observatory-open")')
                completed.append({'route':prefix,'width':width,'passed':True})
                print(json.dumps(completed[-1]),flush=True)
                context.close()
            browser.close()
        server.shutdown()
    assert not errors, errors
    assert not failures, failures
    assert media_paths and any('/showcase/' in path for path in media_paths)
    assert all('/showcase/' in path or path in {'/gbr/v2/catalogue.json','/gbr/v2/research.json'} for path in media_paths)
    print(json.dumps({'workflows':completed,'tracks':len(catalogue['tracks']),'hidden_tracks':len(catalogue['easter']['tracks']),
                      'historical_analytics_records':len(recordings),'javascript_errors':errors,'asset_errors':failures,'media':'synthetic'}))


if __name__ == '__main__':
    main()
