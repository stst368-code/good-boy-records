"""Test the shared player across filtered, paged, and published recordings."""
import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import numpy as np
import soundfile as sf
import uvicorn
from fastapi.staticfiles import StaticFiles
from playwright.sync_api import expect, sync_playwright

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO / path) for path in ('_98_database', '_03_review/_generation_review')]
from _gbr_database.initialize import initialize
from _review.app import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    parser.add_argument('--screenshots')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        database = root / 'gbr.sqlite3'
        initialize(database)
        config = root / 'config.json'
        config.write_text(json.dumps({'database_path': str(database)}))
        mp3 = root / 'published.mp3'
        sf.write(mp3, .15 * np.sin(2 * np.pi * 220 * np.arange(16_000 * 7) / 16_000), 16_000)
        flac = root / 'source.flac'
        sf.write(flac, sf.read(mp3)[0], 16_000)

        app = create_app(config, start_worker=False)
        app.mount('/fixture-audio', StaticFiles(directory=root), name='fixture-audio')
        origin = 'http://127.0.0.1:18781'
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18781, log_level='error'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            with sync_playwright() as pw:
                browser = pw.chromium.launch(executable_path=args.browser, args=['--no-sandbox'])
                page = browser.new_page(viewport={'width': 1440, 'height': 900}, service_workers='block')
                errors = []
                requests = []
                page.on('pageerror', lambda error: errors.append(str(error)))

                def selection(route):
                    query = parse_qs(urlparse(route.request.url).query)
                    offset = int(query['offset'][0])
                    requests.append(offset)
                    rows = [{
                        'generation_id': f'selection-{i}', 'title': f'Selection {i + 1:02}',
                        'genre': 'Chiptune', 'audio_duration': 7, 'review_id': 'reviewed',
                        'publishing_status': 'not_selected', 'flags': {'bookmarked': False},
                    } for i in range(offset, min(offset + 50, 52))]
                    route.fulfill(json={'items': rows, 'has_more': offset + len(rows) < 52})

                page.route('**/api/review/options?scope=selection*', lambda route: route.fulfill(json={
                    'titles': [], 'clusters': [], 'genres': [], 'selection': {'titles': [], 'clusters': [], 'genres': []},
                    'labels': {}, 'buckets': [5], 'statuses': ['not_selected'], 'has_bookmarks': False,
                }))
                page.route('**/api/publishing?*', selection)
                page.route('**/api/playback/selection-*', lambda route: route.fulfill(response=route.fetch(url=f'{origin}/fixture-audio/{flac.name}')))
                releases = [{
                    'generation_id': f'published-{i}', 'prepared_id': f'release-{i}',
                    'visibility': 'public', 'playback_url': f'/api/releases/release-{i}/assets/0' if i < 3 else None,
                    'track': {'displayTitle': f'Release {i}', 'genre': {'parent_genre': 'Folk'}, 'duration': 7},
                } for i in range(1, 4)]
                page.route('**/api/releases/library', lambda route: route.fulfill(json={'items': releases}))
                page.route('**/api/releases/release-*/assets/0', lambda route: route.fulfill(response=route.fetch(url=f'{origin}/fixture-audio/{mp3.name}')))
                page.goto(origin)
                page.locator('#knownReviewers').get_by_role('button', name='Simon').click()
                expect(page.locator('#nowPlaying')).to_be_visible()

                page.locator('#selectionTab').click()
                expect(page.locator('.candidate')).to_have_count(50)
                page.locator('.candidate').last.get_by_role('button', name='Play Selection 50').click()
                expect(page.locator('#playerTitle')).to_have_text('Selection 50')
                page.wait_for_function("() => document.querySelector('#audio').currentTime > 0.4")
                page.locator('#libraryTab').click()
                expect(page.locator('#libraryRecordings .listen-row')).to_have_count(3)
                assert page.evaluate("!document.querySelector('#audio').paused")
                page.locator('#playerNext').click()
                expect(page.locator('#playerTitle')).to_have_text('Selection 51')
                assert requests == [0, 50]
                page.locator('#selectionTab').click()
                expect(page.locator('.candidate')).to_have_count(52)
                page.locator('#playerPrevious').click()
                expect(page.locator('#playerTitle')).to_have_text('Selection 50')

                page.locator('#libraryTab').click()
                expect(page.get_by_role('button', name='Play Release 3')).to_be_disabled()
                page.get_by_role('button', name='Play Release 1').click()
                page.wait_for_function("() => document.querySelector('#audio').currentTime > 0.2")
                expect(page.locator('#playerTitle')).to_have_text('Release 1')
                page.locator('#reviewTab').click()
                expect(page.locator('#empty')).to_be_visible()
                assert page.evaluate("!document.querySelector('#audio').paused")
                page.locator('#playerNext').click()
                expect(page.locator('#playerTitle')).to_have_text('Release 2')
                page.wait_for_function("() => document.querySelector('#audio').currentTime > 0.2")
                page.locator('#playerPrevious').click()
                expect(page.locator('#playerTitle')).to_have_text('Release 1')
                page.wait_for_function("() => document.querySelector('#audio').currentTime > 0.2")
                page.evaluate("document.querySelector('#audio').currentTime = 6.8")
                expect(page.locator('#playerTitle')).to_have_text('Release 2')
                page.locator('#libraryTab').click()
                page.locator('[data-library-tab="rooms"]').click()
                assert page.evaluate("!document.querySelector('#audio').paused")
                page.locator('#play').click()
                expect(page.locator('#play')).to_have_attribute('aria-label', 'Play')
                page.locator('[data-library-tab="recordings"]').click()

                for width in (1440, 390, 320):
                    page.set_viewport_size({'width': width, 'height': 844})
                    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                    assert page.locator('#nowPlaying').evaluate("(bar) => Math.abs(bar.getBoundingClientRect().bottom - innerHeight) < 1")
                    if args.screenshots:
                        target = Path(args.screenshots)
                        target.mkdir(parents=True, exist_ok=True)
                        page.screenshot(path=target / f'library-{width}.png')
                assert not errors, errors
                browser.close()
                print(json.dumps({'shared_playback': 'passed', 'collection_paging': 'passed',
                                  'library_mp3': 'passed', 'continuous_play': 'passed',
                                  'desktop_and_mobile': 'passed', 'js_errors': errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
