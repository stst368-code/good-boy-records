"""Two independent Chromium clients; temporary synthetic media only.

Run: python tests/ui_reviewers.py [--browser /path/to/chromium]
Optional --screenshots <folder> writes internal layout captures.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
import time

import numpy as np
import soundfile as sf
import uvicorn
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _review.app import create_app


def identity(page, name):
    page.wait_for_selector('#reviewerDialog[open]')
    known = page.locator('#knownReviewers button', has_text=name)
    if known.count():
        known.click()
    else:
        page.locator('#newReviewerName').fill(name)
        page.locator('#reviewerForm button[type=submit]').click()
    page.wait_for_selector('#reviewerDialog[open]', state='hidden')
    page.wait_for_selector('#reviewCard:not([hidden])')
    page.wait_for_function("()=>document.querySelectorAll('.snippet').length>0")
    return page.evaluate("localStorage.getItem('gbr-reviewer-id')")


def current(service, rid):
    return service.db.one('SELECT current_generation_id FROM sessions WHERE reviewer_id=?', (rid,))['current_generation_id']


def wait_current(page, service, rid, previous):
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        gid = current(service, rid)
        if gid and gid != previous:
            title = service.public_track(gid)['title']
            page.wait_for_function('title=>document.getElementById("trackTitle").textContent===title', arg=title)
            return gid
        page.wait_for_timeout(40)
    raise AssertionError('Reviewer queue did not advance')


def api(page, path, body=None):
    return page.evaluate("""async ({path,body})=>{
      const options={headers:{'X-GBR-Reviewer':localStorage.getItem('gbr-reviewer-id')}};
      if(body!==null){options.method='POST';options.headers['Content-Type']='application/json';options.headers['X-GBR-Request']='1';options.body=JSON.stringify(body);}
      const response=await fetch('/api'+path,options);return {status:response.status,body:await response.json()};
    }""", {'path': path, 'body': body})


def layout(page, width):
    page.set_viewport_size({'width': width, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    sizes = page.locator('.rating-values button').evaluate_all('(buttons)=>buttons.map(b=>({w:b.getBoundingClientRect().width,h:b.getBoundingClientRect().height}))')
    assert len(sizes) == 24 and all(s['w'] >= 44 and s['h'] >= 48 for s in sizes), sizes
    assert page.locator('#speed,#zoom,#pan,.rating-intro').count() == 0
    assert page.locator('.rating-guidance').count() == 4
    assert page.locator('#ratingReminder').is_visible()
    assert page.locator('#keyboardHelp').count() == 0
    assert page.locator('#ratingReminder').evaluate('p=>getComputedStyle(p).textAlign') == 'center'
    assert page.evaluate('document.getElementById("audio").playbackRate') == 1
    for row in page.locator('.rating').all():
        assert row.locator('.rating-guidance').is_visible()
        for low, high in ((0, 0), (1, 2), (3, 4), (5, 5)):
            anchor = row.locator(f'.rating-anchor[data-score-min="{low}"] .anchor-label')
            anchor.scroll_into_view_if_needed()
            assert anchor.is_visible()
            a = anchor.bounding_box()
            b = row.locator(f'.rating-values [data-score="{low}"]').bounding_box()
            end = row.locator(f'.rating-values [data-score="{high}"]').bounding_box()
            assert a['y'] >= b['y'] + b['height']
            assert abs(a['x'] + a['width']/2 - (b['x'] + end['x'] + end['width'])/2) < 1
            if low != high:
                bracket = row.locator(f'.rating-anchor[data-score-min="{low}"] .rating-bracket').bounding_box()
                gap = row.locator('.rating-values').evaluate('g=>parseFloat(getComputedStyle(g).columnGap)')
                assert abs(bracket['x'] - b['x'] + gap/2) < 1
                assert abs(bracket['x'] + bracket['width'] - end['x'] - end['width'] - gap/2) < 1
                assert a['y'] > bracket['y'] + bracket['height']
    page.evaluate('scrollTo(0,0)')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    parser.add_argument('--screenshots')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / 'input'
        inp.mkdir()
        sr = 16000
        t = np.arange(sr * 12) / sr
        for i in range(10):
            path = inp / f'track-{i}.flac'
            sf.write(path, .12 * np.sin(2 * np.pi * (200 + i * 37) * t), sr, subtype='PCM_24')
            path.with_suffix('.yaml').write_text(f'title: Track {i}\ngenre: Chiptune\ncluster: Electronic\nsampler_steps: 40\n', encoding='utf-8')
        config = root / 'config.json'
        config.write_text(json.dumps({'input_directories': [str(inp)], 'port': 18767}))
        app = create_app(config, start_worker=True)
        service = app.state.service
        clock = [time.time()]
        service.queues.clock = lambda: clock[0]
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18767, log_level='error', loop='_review.server_loop:create_loop'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                if server.started and service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n'] == 10:
                    break
                time.sleep(.1)
            assert server.started
            assert service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n'] == 10
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.browser, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage', '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader'])
                one = browser.new_context(viewport={'width': 1280, 'height': 1000})
                two = browser.new_context(viewport={'width': 390, 'height': 844})
                a = one.new_page()
                b = two.new_page()
                errors = []
                for page in (a, b):
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto('http://127.0.0.1:18767')
                ar, br = identity(a, 'Simon'), identity(b, 'Alex')
                ga, gb = current(service, ar), current(service, br)
                assert ga != gb
                leases = service.db.all('SELECT * FROM queue_reservations')
                assert len(leases) == 6 and len({r['audio_id'] for r in leases}) == 6
                assert b.locator('#back').is_disabled()
                assert b.locator('#compositionGenre').inner_text() == 'Electronic / Chiptune'
                assert 'sampler_steps' not in b.locator('#reviewCard').inner_text()
                for width in (390, 320):
                    layout(b, width)
                b.set_viewport_size({'width': 390, 'height': 844})
                if args.screenshots:
                    out = Path(args.screenshots)
                    out.mkdir(parents=True, exist_ok=True)
                    a.screenshot(path=str(out/'reviewers-desktop.png'), full_page=True)
                    b.screenshot(path=str(out/'reviewers-phone.png'), full_page=True)
                a.reload()
                a.wait_for_selector('#reviewCard:not([hidden])')
                assert not a.locator('#reviewerDialog').is_visible()
                assert a.locator('#reviewerName').inner_text() == 'Simon'
                assert current(service, ar) == ga
                a.locator('#autoplayNext').check()
                a.locator('[data-mode=fast]').click()
                a.locator('#play').click()
                a.wait_for_function("()=>!document.getElementById('audio').paused")
                a.evaluate('document.activeElement.blur()')
                a.keyboard.type('5432', delay=80)
                a.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0")
                a.wait_for_function("()=>!document.getElementById('audio').paused")
                assert current(service, br) == gb and b.locator('#back').is_disabled()
                review = service.db.one('SELECT * FROM reviews')
                assert review['reviewer_id'] == ar and review['generation_id'] == ga
                pending = current(service, ar)
                a.locator('#back').click()
                a.wait_for_selector('#correctionBadge:not([hidden])')
                assert a.evaluate('document.getElementById("audio").paused')
                a.locator('[data-dimension=realism] [data-score="4"]').click()
                a.wait_for_selector('#correctionBadge[hidden]', state='attached')
                a.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0")
                assert current(service, ar) == pending and current(service, br) == gb
                a.locator('#skip').click()
                wait_current(a, service, ar, pending)
                a.wait_for_function("()=>document.querySelectorAll('.rating-values button:disabled').length===0")
                assert current(service, br) == gb
                assert service.db.one('SELECT COUNT(*) AS n FROM queue_skips')['n'] == 1
                a.locator('#changeUser').click()
                sam = identity(a, 'Sam')
                assert service.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,)) == []
                assert current(service, br) == gb
                a.locator('#changeUser').click()
                assert identity(a, 'simon') == ar
                assert service.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (sam,)) == []
                assert a.locator('#back').is_enabled()
                before_b = service.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (br,))
                a.locator('#queueButton').click()
                a.wait_for_selector('#queueDialog[open]')
                assert a.locator('#filterGenre .choice-toggle[aria-pressed=true]').count() == 0
                assert a.locator('#filterSong .choice-toggle[aria-pressed=true]').count() == 0
                a.locator('#filterGenre').get_by_role('button',name='Styles / genres: Chiptune',exact=True).click()
                a.locator('#order').select_option('alphabetical')
                a.locator('#queueForm button.primary').click()
                a.wait_for_selector('#queueDialog[open]', state='hidden')
                a.wait_for_function("()=>document.getElementById('audio').src.includes('/api/playback/')")
                # Filtering may legitimately retain the same current recording.
                deadline = time.monotonic() + 10
                while not current(service, ar) and time.monotonic() < deadline:
                    a.wait_for_timeout(40)
                assert service.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (br,)) == before_b
                # Expire both simulated disconnected clients, then let Alex
                # claim Simon's visible recording. Old scores must not save.
                stale = current(service, ar)
                stale_title = service.public_track(stale)['title']
                clock[0] += 181
                assert api(b, '/queue', {'order': 'alphabetical', 'direction': 'asc', 'song': stale_title, 'genre': '', 'directory': ''})['status'] == 200
                b.reload()
                b.wait_for_selector('#reviewCard:not([hidden])')
                assert current(service, br) == stale
                for dimension in ('audio_quality', 'composition', 'consistency', 'realism'):
                    button = a.locator(f'[data-dimension={dimension}] [data-score="5"]')
                    if button.is_disabled():
                        break  # A heartbeat can also detect the lost reservation.
                    button.click()
                a.wait_for_selector('#resumeQueue:not([hidden])')
                assert a.locator('.rating-values button:disabled').count() == 24
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 2
                a.locator('#resumeQueue').click()
                a.wait_for_selector('#resumeQueue[hidden]', state='attached')
                assert current(service, ar) != stale
                assert a.evaluate('document.getElementById("audio").paused')
                for dimension in ('audio_quality', 'composition', 'consistency', 'realism'):
                    b.locator(f'[data-dimension={dimension}] [data-score="4"]').click()
                b.wait_for_selector('#empty:not([hidden])')
                assert service.db.one('SELECT reviewer_id FROM reviews WHERE generation_id=?', (stale,))['reviewer_id'] == br
                assert api(a, '/reviewer/heartbeat', {'current_id': current(service, ar)})['body']['valid_current']
                assert not errors, errors
                print(json.dumps({'two_clients_isolation_expiry_reconnect': 'passed', 'desktop_390_320_layout': 'passed', 'js_errors': errors}))
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
