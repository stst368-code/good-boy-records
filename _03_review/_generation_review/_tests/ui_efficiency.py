"""Browser integration for autoplay, keyboard review, recovery and plain LAN HTTP.

Uses synthetic media and a temporary database, never a real installation.
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
from _review.config import Config
from _review.mobile import enable_http


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        inp = root / 'input'
        inp.mkdir()
        sr = 8000
        t = np.arange(sr * 54) / sr
        for i in range(5):
            base = .11 * np.sin(2 * np.pi * (220 + i * 10) * t)
            added = np.where(t < 18, 0, np.where(t < 36, .04 * np.sin(2 * np.pi * 880 * t), .04 * np.sin(2 * np.pi * 1100 * t)))
            path = inp / f'track-{i}.flac'
            sf.write(path, base + added, sr, subtype='PCM_24')
            path.with_suffix('.yaml').write_text(f'title: Track {i}\ngenre: Test\n')
        config = root / 'config.json'
        config.write_text(json.dumps({'input_directories': [str(inp)], 'port': 18766, 'allowed_hosts': ['review.test']}))
        code = enable_http(Config(config))
        app = create_app(config, start_worker=True)
        service = app.state.service
        server = uvicorn.Server(uvicorn.Config(app, host='0.0.0.0', port=18766, log_level='error', loop='_review.server_loop:create_loop'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.started and service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n'] == 5:
                    break
                time.sleep(.1)
            assert server.started
            assert service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state='ready'")['n'] == 5
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.browser, headless=True, args=['--no-sandbox', '--disable-dev-shm-usage',  '--use-gl=angle', '--use-angle=swiftshader', '--enable-unsafe-swiftshader', '--no-proxy-server', '--host-resolver-rules=MAP review.test 127.0.0.1'])
                page = browser.new_page(viewport={'width': 1280, 'height': 1000})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:18766')
                page.wait_for_selector('#loginDialog[open]')
                page.locator('#password').fill(code)
                page.locator('#loginForm button[type=submit]').click()
                page.wait_for_selector('#reviewerDialog[open]')
                page.locator('#knownReviewers button',has_text='Simon').click()
                page.wait_for_selector('#reviewCard:not([hidden])')
                page.wait_for_function("()=>document.querySelectorAll('.snippet').length>0")
                page.evaluate('document.activeElement.blur()')
                page.keyboard.press('Space')
                page.wait_for_function("()=>!document.getElementById('audio').paused")
                page.keyboard.press('Space')
                page.wait_for_function("()=>document.getElementById('audio').paused")
                page.keyboard.press('s')
                assert page.locator('[data-mode=standard]').get_attribute('class') == 'active'
                assert service.db.one('SELECT COUNT(*) AS n FROM queue_skips')['n'] == 0
                page.keyboard.press('f')
                assert page.locator('[data-mode=fast]').get_attribute('class') == 'active'
                page.locator('#autoplayNext').check()
                page.evaluate('document.activeElement.blur()')
                page.keyboard.press('Space')
                page.wait_for_function("()=>!document.getElementById('audio').paused")
                first = service.db.one("SELECT current_generation_id FROM sessions WHERE id='main'")['current_generation_id']
                deadline = time.monotonic() + 5
                while service.prefetched_id is None and time.monotonic() < deadline:
                    page.wait_for_timeout(50)
                assert service.prefetched_id is not None
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 0
                page.keyboard.type('5432', delay=80)
                page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0")
                page.wait_for_function("()=>!document.getElementById('audio').paused&&document.getElementById('audio').src.includes('mode=fast')")
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 1
                review = service.db.one('SELECT * FROM reviews')
                assert review['generation_id'] == first
                assert 'fast' in json.loads(review['listening_json'])['modes_played']
                assert json.loads(review['listening_json'])['analysis_id']
                second = service.db.one("SELECT current_generation_id FROM sessions WHERE id='main'")['current_generation_id']
                assert first != second
                page.wait_for_function("()=>document.querySelectorAll('.rating-values button:disabled').length===0")
                page.keyboard.press('b')
                page.wait_for_selector('#correctionBadge:not([hidden])')
                assert page.evaluate("document.getElementById('audio').paused")
                page.locator('#skip').click()
                page.wait_for_selector('#correctionBadge[hidden]', state='attached')
                page.wait_for_function("()=>!document.getElementById('audio').paused")
                page.locator('#nextPoint').click()
                page.wait_for_function("()=>document.getElementById('audio').src.includes('mode=range')&&!document.getElementById('audio').paused")
                assert page.evaluate("localStorage.getItem('gbr-review-mode')") == 'fast'
                # A browser without FLAC playback transparently retries WAV, including pinned analysis.
                page.route('**/api/playback/**', lambda route: route.abort() if 'format=flac' in route.request.url else route.continue_())
                page.locator('[data-mode=full]').click()
                page.wait_for_function("()=>document.getElementById('audio').src.includes('format=wav')&&document.getElementById('audio').readyState>=1")
                page.unroute('**/api/playback/**')
                page.locator('[data-mode=fast]').click()
                # Server commits once; a failed following request must not enable a second score submission.
                failure = {'armed': True}
                def fail_one_next(route):
                    if failure['armed']:
                        failure['armed'] = False
                        route.fulfill(status=503, content_type='application/json', body='{"detail":"Synthetic interruption"}')
                    else:
                        route.continue_()
                page.route('**/api/queue/next*', fail_one_next)
                page.evaluate('document.activeElement.blur()')
                page.keyboard.type('4444', delay=80)
                page.wait_for_function("()=>document.getElementById('reviewMessage').textContent.includes('Retrying')")
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 2
                assert page.locator('.rating-values button:disabled').count() == 24
                page.keyboard.type('5555')
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 2
                page.wait_for_function("()=>document.querySelectorAll('.rating-values [aria-pressed=true]').length===0", timeout=10000)
                page.wait_for_function("()=>!document.getElementById('audio').paused")
                assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 2
                page.unroute('**/api/queue/next*')
                page.reload()
                page.wait_for_selector('#reviewCard:not([hidden])')
                assert page.locator('#autoplayNext').is_checked()
                assert page.locator('[data-mode=fast]').get_attribute('class') == 'active'
                assert page.evaluate("document.getElementById('audio').paused")
                # Non-localhost HTTP is not a secure context. Forward requests
                # to the temporary local server because this test environment
                # restricts direct private-address connections. This verifies
                # browser behavior, not physical-LAN reachability.
                phone = browser.new_page(viewport={'width': 390, 'height': 844})
                phone.on('pageerror', lambda error: errors.append(str(error)))
                def forward_http(route):
                    headers = route.request.all_headers()
                    headers['host'] = 'review.test:18766'
                    response = route.fetch(url=route.request.url.replace('review.test', '127.0.0.1'), headers=headers)
                    route.fulfill(response=response)
                phone.route('http://review.test:18766/**', forward_http)
                phone.goto('http://review.test:18766')
                phone.wait_for_selector('#loginDialog[open]')
                assert not phone.evaluate('window.isSecureContext')
                phone.locator('#password').fill(code)
                phone.locator('#loginForm button[type=submit]').click()
                phone.wait_for_selector('#reviewerDialog[open]')
                phone.locator('#knownReviewers button',has_text='Simon').click()
                phone.wait_for_selector('#reviewCard:not([hidden])')
                phone.locator('#play').click()
                phone.wait_for_function("()=>!document.getElementById('audio').paused")
                phone.locator('#settingsButton').click()
                phone.wait_for_selector('#settingsDialog[open]')
                assert phone.evaluate("!('serviceWorker' in navigator)")
                assert not errors, errors
                print(json.dumps({'autoplay_fast_keyboard_recovery': 'passed', 'nonsecure_http_login_playback': 'passed', 'physical_lan': 'not tested', 'js_errors': errors}))
                phone.unroute_all(behavior='wait')
                browser.close()
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
