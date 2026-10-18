"""Generation -> review -> archive through the real UI/API with a CPU FLAC boundary fixture.

No native inference, model download or paid cloud resource is used.
Run: python tests/ui_generation.py --browser /path/to/chromium
"""
import argparse
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import uuid

import uvicorn
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[3]
for name in ('_02_Generation', '_02_Generation/tests', '_98_Database', '_03_Review/_Generation_Review'):
    sys.path.insert(0, str(ROOT / name))
from gbr_database.initialize import initialize
from gbr_generation.executor import execute
from review.app import create_app
from test_execution import ExecutionTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        built = root / '_01_Writing/_Built'
        shutil.copytree(ROOT / '_02_Generation/examples/local_smoke', built)
        references = root / '_01_Writing/_references'
        references.mkdir()
        shutil.copyfile(built / 'styles.csv', references / 'styles.csv')
        source = built / '_yaml/gbr-local-smoke.yaml'
        original = source.read_bytes()
        database = root / '_98_Database/gbr.sqlite3'
        initialize(database)
        generated = root / '_02_Generation/generated'
        generated.mkdir(parents=True)
        config = root / 'config.json'
        config.write_text(json.dumps({'database_path': str(database), 'input_directories': [str(generated)],
            'generation': {'input_root': str(built)}, 'port': 18771}))
        app = create_app(config, start_worker=False, database_path=database)
        service, generation = app.state.service, app.state.generation
        generation.root = root
        boundary = ExecutionTests()
        boundary.calls = []

        def launch(task, credentials=None):
            folder = generation.runtime / 'app_jobs' / uuid.uuid4().hex
            folder.mkdir(parents=True)
            (folder / 'task.json').write_text(json.dumps(task))
            result = execute(task['manifest'], worker_python=sys.executable, launcher=boundary.worker)
            (folder / 'state.json').write_text(json.dumps({'status': result['status']}))
            (folder / 'controller.log').write_text('CPU boundary fixture: ' + json.dumps(result))
            service.indexer.scan()
            return {'task_id': folder.name, 'batch_id': task['batch_id']}

        generation.launch = launch
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18771, log_level='error', loop='review.server_loop:create_loop'))
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started and time.monotonic() < deadline:
                time.sleep(.05)
            assert server.started
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(executable_path=args.browser, headless=True,
                    args=['--no-sandbox', '--disable-dev-shm-usage', '--disable-gpu'])
                page = browser.new_page(viewport={'width': 1366, 'height': 768})
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.goto('http://127.0.0.1:18771')
                page.locator('#knownReviewers button', has_text='Simon').click()
                page.locator('#generationTab').click()
                page.wait_for_selector('.generation-file')
                page.locator('#genSelectVisible').click()
                page.locator('#genPriority').select_option('form')
                assert page.locator('#genDuration').input_value() == '360'
                page.locator('#genPreview').click()
                page.wait_for_selector('#genPlan:not([hidden])')
                assert '1 ready' in page.locator('#genPlanSummary').inner_text()
                assert not boundary.calls
                page.locator('#genStart').click()
                page.wait_for_function("()=>document.getElementById('genJobs').textContent.includes('1 / 1 complete')")
                assert len(boundary.calls) == 1
                assert boundary.calls[0]['parameters']['duration'] == 360
                assert source.read_bytes() == original
                artifacts = service.db.all('SELECT * FROM generation_artifacts')
                assert len(artifacts) == 4 and all(Path(row['uri']).is_relative_to(generated) for row in artifacts)
                page.locator('#reviewTab').click()
                page.wait_for_selector('#reviewCard:not([hidden])')
                assert '1 left' in page.locator('#queueCount').inner_text()
                for dimension in ('audio_quality', 'composition', 'consistency', 'realism'):
                    page.locator(f'[data-dimension={dimension}] .rating-values [data-score="5"]').click()
                page.wait_for_function("()=>document.getElementById('queueCount').textContent.includes('0 left')")
                for operation in service.db.all("SELECT * FROM archive_operations WHERE status='pending'"):
                    service.archiver.process(operation)
                assert service.db.one('SELECT status FROM archive_operations')['status'] == 'done'
                assert not list(generated.rglob('*.flac'))
                assert len(list(service.config.archive.rglob('*.*'))) == 4
                assert source.read_bytes() == original
                page.locator('#generationTab').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert not errors, errors
                browser.close()
                print(json.dumps({'selection_preview_dispatch_review_archive': 'passed', 'native_inference': 'not run', 'js_errors': errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
