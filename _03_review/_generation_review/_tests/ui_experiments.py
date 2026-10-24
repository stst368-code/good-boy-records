"""Browser checks for experiment isolation, playback, output actions and step sweeps.

Only deterministic test-boundary FLACs are generated; no inference or paid workers.
"""
import argparse
from pathlib import Path
import sys
import threading
import time

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / '_02_generation/_tests'), str(ROOT / '_02_generation'),
               str(ROOT / '_98_database'), str(ROOT / '_03_review/_generation_review')]
import test_app_generation
import test_execution
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', required=True)
    parser.add_argument('--screenshots', type=Path)
    args = parser.parse_args()
    f = test_app_generation.GenerationAppTests('runTest')
    f.setUp()
    server = None
    try:
        generation = f.generation
        generation.workers.inspect = lambda entry: {'busy': False, 'models': generation.native_choices()}
        generation.workers.save({'connection': {'backend': 'local'}})
        checked = f.preview(purpose='experiment', experiment_label='Encoding shortlist',
            experiments={'seed_policy': 'shared', 'sweeps': {'sampler_steps': [5, 1]}})
        generation.start(checked['token'])
        path = Path(f.launched[-1]['manifest'])
        manifest = verify_plan(path)
        boundary = test_execution.ExecutionTests('runTest'); boundary.calls = []
        assert execute(path, worker_python=sys.executable, launcher=boundary.worker, limit=2)['status'] == 'succeeded'
        production = f.preview(); generation.start(production['token'])
        server = uvicorn.Server(uvicorn.Config(f.app, host='127.0.0.1', port=18784,
            log_level='error', loop='_review.server_loop:create_loop'))
        thread = threading.Thread(target=server.run, daemon=True); thread.start()
        deadline = time.monotonic() + 10
        while not server.started and time.monotonic() < deadline:
            time.sleep(.05)
        assert server.started
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
                args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
            page = browser.new_page(viewport={'width': 1920, 'height': 1080})
            errors = []; page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://127.0.0.1:18784')
            page.locator('#knownReviewers button', has_text='Simon').click()
            page.locator('#generationTab').click()
            page.wait_for_selector('.generation-file')
            assert page.locator('#genPurpose').input_value() == 'production'
            page.locator('#genQueueTab').click()
            page.wait_for_selector('.job-batch')
            assert page.locator('.job-batch').count() == 1
            assert 'Encoding shortlist' not in page.locator('#genJobs').inner_text()
            page.locator('#genExperimentsTab').click()
            card = page.locator(f'[data-batch="{manifest["batch_id"]}"]')
            card.wait_for(); card.locator('summary', has_text='Tracks & configurations').click()
            card.locator('.job-run').first.wait_for()
            assert card.locator('.job-run').count() == 2
            assert card.locator('audio').count() == 2
            assert 'test boundary' in card.inner_text()
            assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM generations')['n'] == 0
            card.locator('audio').first.evaluate('audio => audio.play()')
            page.wait_for_function('()=>document.querySelector(".experiment-result audio").readyState>=2')
            assert card.locator('audio').first.evaluate('audio=>audio.duration') > 0
            with page.expect_download() as download:
                card.get_by_role('link', name='Export performance CSV').click()
            assert download.value.suggested_filename.endswith('-performance.csv')
            card.get_by_label('Order experiment results').select_option('steps')
            page.wait_for_function('(id)=>document.querySelector(".job-run").dataset.run===id', arg=manifest['runs'][1]['id'])
            page.on('dialog', lambda dialog: dialog.accept())
            card.locator('.job-run').nth(1).get_by_role('button', name='Delete output files', exact=True).click()
            page.wait_for_function('()=>document.getElementById("genJobs").textContent.includes("Output files deleted")')
            assert card.locator('audio').count() == 1
            card.locator('.job-run').first.get_by_role('button', name='Send to review', exact=True).click()
            page.wait_for_function('()=>document.getElementById("genJobs").textContent.includes("Sent to review")')
            assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM generations')['n'] == 1
            assert len(list(generation.generated.rglob('*.flac'))) == 1
            assert not list(generation.experimental.rglob('*.flac'))
            assert len(boundary.calls) == 2
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if args.screenshots:
                args.screenshots.mkdir(parents=True, exist_ok=True)
                page.screenshot(path=str(args.screenshots / 'experiments-desktop.png'), full_page=True)
            page.set_viewport_size({'width': 390, 'height': 844})
            assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
            if args.screenshots:
                page.screenshot(path=str(args.screenshots / 'experiments-mobile.png'), full_page=True)
            page.set_viewport_size({'width': 1920, 'height': 1080})
            page.locator('#genSetupTab').click()
            page.locator('#genPurpose').select_option('experiment')
            assert page.locator('#genSeedPolicy').input_value() == 'shared'
            page.locator('#genExperimentLabel').fill('Steps 1–100')
            page.locator('#genCaptionMode').select_option('yaml')
            page.locator('#genSelectVisible').click()
            page.locator('#genStepsMode').select_option('range')
            for field, value in zip(page.locator('#genStepsRange input').all(), ['1', '100', '1']):
                field.fill(value)
            page.locator('#genPreview').click()
            page.wait_for_function('()=>document.getElementById("genPlanSummary").textContent.includes("100 generations")')
            page.locator('#genStart').click()
            page.wait_for_function('()=>document.getElementById("genJobs").textContent.includes("Steps 1–100")')
            assert page.locator('#genExperimentsTab').get_attribute('class') == 'active'
            assert f.source.read_bytes() == f.original
            assert not errors, errors
            browser.close()
            print('PASS: purpose controls, isolated queues, FLAC playback, CSV download, sorting, deletion, promotion, desktop/mobile and 100-step batch; no JS errors')
    finally:
        if server:
            server.should_exit = True; thread.join(timeout=10)
        f.doCleanups()


if __name__ == '__main__':
    main()
