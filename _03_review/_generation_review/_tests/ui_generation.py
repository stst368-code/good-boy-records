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
for name in ('_02_generation', '_02_generation/_tests', '_98_database', '_03_review/_generation_review'):
    sys.path.insert(0, str(ROOT / name))
from _gbr_database.initialize import initialize
from _gbr_generation.executor import execute
from _gbr_generation.yaml_document import YamlDocument
from _review.app import create_app
from test_execution import ExecutionTests


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        built = root / '_01_writing/_built'
        shutil.copytree(ROOT / '_02_generation/_examples/_local_smoke', built)
        references = root / '_01_writing/_references'
        references.mkdir()
        shutil.copyfile(built / 'styles.csv', references / 'styles.csv')
        with (references / 'styles.csv').open('a') as stream:
            stream.write('second-template,Rock,Punk,second.txt\n')
        (references / 'unused.txt').write_text('Global Metadata\nChoral\nVocal Details\nClear singing\nArrangement\nChoir\n')
        (references / 'second.txt').write_text('Global Metadata\nPunk\nVocal Details\nLoud singing\nArrangement\nGuitar\n')
        source = built / '_yaml/gbr-local-smoke.yaml'
        original = source.read_bytes()
        fields = list(YamlDocument(original).fields)
        fields.insert(fields.index('title') + 1, 'explicit')
        template = built / 'track-template.yaml'
        template.write_text(''.join(field + ':\n' for field in fields))
        database = root / '_98_database/gbr.sqlite3'
        initialize(database)
        generated = root / '_02_generation/_generated'
        generated.mkdir(parents=True)
        config = root / 'config.json'
        config.write_text(json.dumps({'database_path': str(database), 'input_directories': [str(generated)],
            'generation': {'input_root': str(built)}, 'port': 18771}))
        app = create_app(config, start_worker=False, database_path=database)
        service, generation = app.state.service, app.state.generation
        generation.root = root
        app.state.yaml_editor.repo = root
        app.state.yaml_editor.default_order_template = template
        app.state.yaml_editor.backups = root / '_97_app/_runtime/yaml_edits'
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
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=18771, log_level='error', loop='_review.server_loop:create_loop'))
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
                page.locator('#yamlTab').click()
                page.wait_for_selector('.ye-file')
                page.get_by_label('Select _yaml/gbr-local-smoke.yaml', exact=True).check()
                page.locator('[data-role=kind]').select_option('add_template')
                page.locator('[data-role=template_fields]').fill('explicit')
                page.locator('[data-role=use_default]').check()
                page.locator('[data-role=default_text]').fill('no')
                page.locator('#ye-preview').click()
                page.wait_for_selector('#ye-save:not([disabled])')
                with page.expect_response('**/api/yaml-editor/apply'):
                    page.locator('#ye-save').click()
                assert YamlDocument(source.read_bytes()).values['explicit'] == 'no'
                assert source.read_bytes().index(b'explicit:') > source.read_bytes().index(b'title:')
                page.locator('#ye-history').click()
                page.locator('.ye-history-item button').first.click()
                page.wait_for_selector('#ye-save:not([disabled])')
                with page.expect_response('**/api/yaml-editor/apply'):
                    page.locator('#ye-save').click()
                assert source.read_bytes() == original
                page.locator('#generationTab').click()
                page.wait_for_selector('.generation-file')
                page.locator('#genSelectVisible').click()
                page.locator('#genPriority').select_option('form')
                assert page.locator('#genDuration').input_value() == '360'
                page.locator('summary', has_text='Experiment quantities and ranges').click()
                page.locator('#genRepetitions').fill('2')
                page.locator('#genCaptionCount').fill('2')
                page.locator('#genAddSweep').click()
                page.locator('#genSweeps input').first.fill('1:2:0.5')
                page.locator('#genAddSweep').click()
                page.locator('#genSweeps select').nth(1).select_option('sampler_steps')
                page.locator('#genSweeps input').nth(1).fill('20,30')
                page.locator('#genPreview').click()
                page.wait_for_selector('#genPlan:not([hidden])')
                assert '24 generations' in page.locator('#genPlanSummary').inner_text()
                assert not boundary.calls
                page.locator('#genRepetitions').fill('1')
                page.locator('#genCaptionCount').fill('1')
                page.locator('#genSweeps button').first.click()
                page.locator('#genSweeps button').first.click()
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
