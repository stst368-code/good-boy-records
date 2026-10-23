"""Desktop density and phone regression checks against a built site.

Run with --browser /path/to/chromium. Media is synthetic; no uploads or paid
services are used. Animation is throttled only in this software-rendered test.
"""
import argparse
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browser', type=Path, required=True)
    parser.add_argument('--site', type=Path, default=ROOT / '_site')
    parser.add_argument('--width', type=int, action='append', help='Run only the specified viewport width(s)')
    args = parser.parse_args()
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(44100)
        wav.writeframes(b'\0' * 44100 * 4)

    def media(route):
        if route.request.url.endswith(('.flac', '.mp3')):
            route.fulfill(content_type='audio/wav', body=audio.getvalue(), headers={'Access-Control-Allow-Origin': '*'})
        elif route.request.url.endswith(('.jpg', '.png', '.webp')):
            route.fulfill(content_type='image/png', body=IMAGE)
        else:
            route.fulfill(status=404, body='No published fixture')

    errors, results = [], []
    with ThreadingHTTPServer(('127.0.0.1', 0), partial(Routes, directory=str(args.site.resolve()))) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=str(args.browser), headless=True,
                args=['--no-sandbox', '--use-angle=swiftshader'])
            for width, height, density in [(1024,768,1), (1440,900,1), (1920,1080,2), (2560,1440,1), (3840,2160,1), (390,844,1)]:
                if args.width and width not in args.width:
                    continue
                page = browser.new_page(viewport={'width':width,'height':height}, device_scale_factor=density)
                page.add_init_script('const raf=requestAnimationFrame.bind(window);window.requestAnimationFrame=cb=>setTimeout(()=>raf(cb),100)')
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.route('https://pub-*.r2.dev/**', media)
                page.goto(f'http://127.0.0.1:{server.server_port}/good-boy-records/')
                page.wait_for_function('typeof tracks!=="undefined" && nodes.length>0 && document.querySelector("#playingCoverScreen").dataset.renderSize')
                sleeve = page.locator('#playingCoverScreen').bounding_box()
                rack = page.locator('.hardware').bounding_box()
                assert sleeve['width'] == sleeve['height']
                assert rack['width'] < width * .4 if width > 900 else rack['width'] == width
                assert rack['y'] + rack['height'] <= height + 2
                if width == 3840:
                    assert sleeve['width'] >= 700 and rack['width'] >= 750
                    assert page.locator('#deskGenreZones').bounding_box()['width'] >= 85
                if width == 1920:
                    # High-DPI alone must not double CSS dimensions.
                    assert sleeve['width'] == 360
                if width < 900:
                    assert sleeve['width'] == 252 and rack['height'] == 164

                page.locator('#tracksButton').click()
                page.wait_for_function('''()=>{const r=document.querySelector('#sidePanel').getBoundingClientRect();return r.x>=0&&r.right<=innerWidth&&r.bottom<=innerHeight+1}''')
                panel = page.locator('#sidePanel').bounding_box()
                if width == 3840:
                    assert panel['width'] >= 1600
                    assert page.locator('.track-song-copy b').first.bounding_box()['height'] >= 32
                page.locator('.track-song-group summary').first.click()
                assert page.locator('.track-version-row:visible').count() > 0
                page.locator('#panelClose').click()
                page.locator('#filterButton').click()
                before = page.evaluate('tracks.filter(isTrackEligible).length')
                page.locator('.filter-clusters button').first.click()
                assert page.evaluate('tracks.filter(isTrackEligible).length') < before
                page.locator('.filter-clusters button').first.click()
                page.locator('#panelClose').click()
                if width >= 1440:
                    # Canvas painting and pointer selection must agree at the
                    # larger density, rather than only scaling the DOM sleeve.
                    target = page.evaluate('''()=>{const r=canvas.getBoundingClientRect();for(const n of nodes){const p=viewToScreen(n.x,n.y);if(p.x<20||p.x>r.width-20||p.y<110||p.y>r.height-90)continue;const hit=hitNode(p.x+r.left,p.y+r.top);if(hit.best?.n.i===n.i&&n.i!==index)return {x:p.x+r.left,y:p.y+r.top,i:n.i}}return null}''')
                    assert target is not None
                    page.mouse.click(target['x'], target['y'])
                    page.wait_for_function('index==='+str(target['i']))
                    page.evaluate('camera.zoom=camera.targetZoom=10')
                    assert page.evaluate('nodeDisplaySize(nodes[0])') >= (550 if width == 3840 else 290)
                    assert page.locator('#playingCoverScreen').bounding_box()['width'] == sleeve['width']
                page.locator('#gbrAnalyticsButton').click()
                frame = page.frame_locator('#gbrObservatory iframe')
                frame.locator('#sideCount').wait_for(state='attached')
                if width > 900:
                    layout = frame.locator('.lab-body').bounding_box()
                    assert layout['width'] >= width * .98
                    assert frame.locator('.lab-side').bounding_box()['x'] <= 1
                    frame.locator('[data-open-choose]').first.click()
                    frame.locator('.ef-palette').wait_for()
                    palette = frame.locator('.ef-palette').bounding_box()
                    assert palette['x'] >= 0 and palette['x']+palette['width'] <= width+1
                    assert palette['y'] >= 0 and palette['y']+palette['height'] <= height+1
                frame.locator('.home-link').click()
                page.wait_for_function('!document.body.classList.contains("gbr-observatory-open")')
                if width == 3840:
                    # Moving a window to a smaller display must recompute both
                    # geometry and the phone dock, without a reload.
                    page.set_viewport_size({'width':390,'height':844})
                    page.wait_for_function('document.querySelector("#playingCoverScreen").getBoundingClientRect().width===252')
                    assert page.locator('.hardware').bounding_box()['height'] == 164
                results.append({'width':width,'height':height,'density':density,'sleeve':sleeve['width'],'passed':True})
                print(json.dumps(results[-1]), flush=True)
                page.close()
            browser.close()
        server.shutdown()
    assert not errors, errors
    print(json.dumps({'javascript_errors':errors,'viewports':len(results)}))


if __name__ == '__main__':
    main()
