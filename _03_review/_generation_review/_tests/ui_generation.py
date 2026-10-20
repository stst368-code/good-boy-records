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
from test_comfy_execution import server as comfy_server


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
            'generation': {'input_root': ''}, 'port': 18771}))
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
            result = execute(task['manifest'], worker_python=sys.executable, launcher=boundary.worker,worker_id=task.get('worker_id'))
            (folder / 'state.json').write_text(json.dumps({'status': result['status']}))
            (folder / 'controller.log').write_text('[2026-10-20T17:00:00.000+00:00] CPU boundary fixture: ' + json.dumps(result))
            service.indexer.scan()
            return {'task_id': folder.name, 'batch_id': task['batch_id']}

        generation.launch = launch
        inspect_worker = generation.workers.inspect
        def inspect(entry):
            if entry['connection']['backend'] == 'local':
                return {'busy':False,'hardware':{'gpu_name':'CPU boundary fixture'},'models':generation.native_choices()}
            return inspect_worker(entry)
        generation.workers.inspect = inspect
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
                assert page.locator('#genRoot').input_value() == str(built)
                # A repository/container path loads the standard catalogue and
                # replaces the visible path with the root actually loaded.
                page.locator('#genRoot').fill(str(root))
                assert page.locator('.generation-file').count() == 0
                page.locator('#genScan').click();page.wait_for_selector('.generation-file')
                assert page.locator('#genRoot').input_value() == str(built)
                page.locator('#genSelectVisible').click()
                assert page.locator('#genSelected').inner_text().startswith('1 selected')
                # Changing to an invalid folder cannot retain the previous selection.
                wrong=root/'wrong catalogue';wrong.mkdir()
                page.locator('#genRoot').fill(str(wrong))
                assert page.locator('#genSelected').inner_text().startswith('0 selected')
                with page.expect_response('**/api/generation/catalogue?*') as failed:
                    page.locator('#genScan').click()
                assert failed.value.status == 400
                assert page.locator('.generation-file').count() == 0 and not boundary.calls
                page.locator('#genRoot').fill('')
                page.locator('#genScan').click();page.wait_for_selector('.generation-file')
                assert page.locator('#genRoot').input_value() == str(built)
                page.locator('#genSelectVisible').click()
                page.locator('#genDurationMode').select_option('fixed')
                assert page.locator('#genDuration').input_value() == '360'
                page.locator('#genRepetitions').fill('2')
                page.locator('#genCaptionCount').fill('2')
                page.locator('#genCfgMode').select_option('range')
                for control,value in zip(page.locator('#genCfgRange input').all(),['1','2','0.5']):control.fill(value)
                page.locator('#genStepsMode').select_option('list')
                page.locator('#genStepsList').fill('20,30')
                page.locator('#genPreview').click()
                page.wait_for_selector('#genPlan:not([hidden])')
                assert '24 generations' in page.locator('#genPlanSummary').inner_text()
                assert not boundary.calls
                page.locator('#genRepetitions').fill('1')
                page.locator('#genCaptionCount').fill('1')
                page.locator('#genCfgMode').select_option('yaml')
                page.locator('#genStepsMode').select_option('yaml')
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
                page.locator('#genJobs details summary').first.click()
                assert 'CPU boundary fixture' in page.locator('#genJobs pre').first.inner_text()
                page.locator('#genClearLogs').click()
                page.wait_for_function("()=>document.getElementById('genJobs').textContent.includes('Log view cleared')")
                logfile=next((generation.runtime/'app_jobs').glob('*/controller.log'))
                with logfile.open('a') as stream:stream.write('\n[2026-10-20T17:00:01.000+00:00] New event after clear\n')
                page.locator('#genRefreshJobs').click()
                page.wait_for_function("()=>document.getElementById('genJobs').textContent.includes('New event after clear')")
                assert 'CPU boundary fixture' not in page.locator('#genJobs pre').first.inner_text()
                page.locator('#genRestoreLogs').click()
                page.wait_for_function("()=>document.getElementById('genJobs').textContent.includes('CPU boundary fixture')")
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
                with comfy_server() as (url,comfy):
                    assert page.locator('#genBackend').count() == 0
                    page.locator('#genWorkerSettings').click()
                    page.wait_for_selector('#workerSettingsDialog[open]')
                    page.locator('#workerNew').click()
                    page.locator('#workerBackend').select_option('comfyui')
                    page.locator('#workerAddress').fill(url)
                    assert page.locator('#workerKeyWrap').is_hidden()
                    with page.expect_response('**/api/generation/workers/query'):
                        page.locator('#workerQuery').click()
                    page.wait_for_function("()=>document.getElementById('workerStatus').textContent.includes('Online')")
                    assert 'test GPU' in page.locator('#workerGpu').inner_text()
                    page.locator('#workerEncoder').select_option('minimax_music3_text_encoder_bf16.safetensors')
                    page.locator('#workerDit').select_option('minimax_music3_dit_fp32.safetensors')
                    page.locator('#workerSampler').select_option('euler')
                    page.locator('#workerScheduler').select_option('simple')
                    with page.expect_response('**/api/generation/workers'):
                        page.locator('#workerSave').click()
                    page.wait_for_function("()=>!document.getElementById('workerSave').disabled")
                    page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/workers-desktop.png',full_page=True)
                    page.locator('#workersClose').click()
                    with page.expect_response('**/api/generation/workers/query'):
                        page.locator('#genWorker').select_option('comfyui|'+url)
                    assert page.locator('#genModelPairs select').first.locator('option',has_text='minimax_music3_text_encoder_bf16.safetensors').count()==1
                    assert page.locator('#genSamplerPairs [data-field=scheduler] option',has_text='simple').count()==1
                    page.locator('#genSamplingMode').select_option('pairs')
                    page.locator('#genSamplerPairs [data-field=sampler]').select_option('euler')
                    page.locator('#genSamplerPairs [data-field=scheduler]').select_option('simple')
                    page.locator('#genAddSamplerPair').click()
                    page.locator('#genSamplerPairs [data-field=sampler]').nth(1).select_option('dpmpp_2m_sde_gpu')
                    page.locator('#genSamplerPairs [data-field=scheduler]').nth(1).select_option('ddim_uniform')
                    page.locator('#genPreview').click();page.wait_for_selector('#genPlan:not([hidden])')
                    assert '2 generations' in page.locator('#genPlanSummary').inner_text()
                    page.locator('#genSamplerPairs button').nth(1).click()
                    page.locator('#genPreview').click();page.wait_for_selector('#genPlan:not([hidden])')
                    assert '1 ready' in page.locator('#genPlanSummary').inner_text()
                    assert comfy['submissions']==0
                    with page.expect_response('**/api/generation/workers/query'):
                        page.locator('#genWorker').select_option('local|')
                    page.locator('#genSamplingMode').select_option('worker')
                    assert page.locator('#genSamplerPairs [data-field=scheduler] option',has_text='flow_match_euler').count()==1
                    # Worker setup is also reachable from the application settings.
                    page.locator('#settingsButton').click();page.locator('#openWorkerSettings').click()
                    page.wait_for_selector('#workerSettingsDialog[open]')
                    page.set_viewport_size({'width':390,'height':844})
                    assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                    page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/workers-mobile.png',full_page=True)
                    page.locator('#workersClose').click()
                page.set_viewport_size({'width': 390, 'height': 844})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/generation-mobile.png',full_page=True)
                page.set_viewport_size({'width':1920,'height':1080})
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                page.screenshot(path='/workspace/scratch/286b0af2d5d4/app_stage/generation-desktop.png',full_page=True)
                assert not errors, errors
                browser.close()
                print(json.dumps({'selection_preview_dispatch_review_archive': 'passed', 'native_inference': 'not run', 'worker_settings_and_discovery':'passed', 'js_errors': errors}))
        finally:
            server.should_exit = True
            thread.join(timeout=10)


if __name__ == '__main__':
    main()
