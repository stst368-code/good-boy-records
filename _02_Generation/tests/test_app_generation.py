"""App previews freeze actual inputs and never dispatch an invalid or duplicate batch."""
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
for folder in ('_02_Generation', '_98_Database', '_03_Review/_Generation_Review', '_99_Tools/_workers_infdit'):
    sys.path.insert(0, str(ROOT / folder))
from gbr_database.initialize import initialize
from gbr_generation.connections import normalize
from gbr_generation.planner import verify_plan
from review.app import create_app
from review.config import password_hash


class GenerationAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.built = self.root / '_01_Writing/_Built'
        shutil.copytree(ROOT / '_02_Generation/examples/local_smoke', self.built)
        refs = self.root / '_01_Writing/_references'
        refs.mkdir()
        shutil.copyfile(self.built / 'styles.csv', refs / 'styles.csv')
        self.source = self.built / '_yaml/gbr-local-smoke.yaml'
        self.original = self.source.read_bytes()
        self.database = self.root / '_98_Database/gbr.sqlite3'
        initialize(self.database)
        self.app = create_app(self.root / 'review-config.json', start_worker=False, database_path=self.database)
        self.generation = self.app.state.generation
        self.generation.root = self.root
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-GBR-Request': '1'}
        self.launched = []
        def launch(task, credentials=None):
            self.launched.append(task)
            return {'task_id': 'fixture', 'batch_id': task.get('batch_id')}
        self.launch = patch.object(self.generation, 'launch', side_effect=launch).start()
        self.addCleanup(patch.stopall)

    def preview(self, **extra):
        body = {'input_root': str(self.built), 'all_inputs': True, 'connection': {'backend': 'local'}}
        body.update(extra)
        response = self.client.post('/api/generation/preview', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_yaml_priority_and_form_priority_show_actual_duration_before_dispatch(self):
        result = self.preview()
        self.assertEqual(result['parameters']['duration'], 360)
        self.assertEqual(result['inputs'][0]['effective']['duration'], 30)
        override = self.preview(parameters={'duration': 360}, parameter_priority='form')
        self.assertEqual(override['inputs'][0]['effective']['duration'], 360)
        self.assertEqual(override['inputs'][0]['provenance']['duration'], 'batch')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse(self.launched)

    def test_successful_start_registers_inbox_and_is_single_use(self):
        checked = self.preview(parameters={'duration': 360}, parameter_priority='form', layout='album')
        response = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.launched), 1)
        manifest = verify_plan(self.launched[0]['manifest'])
        self.assertEqual(manifest['output_root'], str(self.root / '_02_Generation/generated'))
        self.assertEqual(manifest['output_layout'], 'album')
        self.assertEqual(manifest['runs'][0]['effective']['duration'], 360)
        self.assertIn(Path(manifest['output_root']), self.app.state.service.config.inputs)
        retry = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']})
        self.assertEqual(retry.status_code, 400)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_stale_source_cannot_be_dispatched(self):
        checked = self.preview()
        self.source.write_bytes(self.original + b'# changed after preview\n')
        response = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('Source changed', response.text)
        self.assertFalse(self.launched)

    def test_invalid_input_cancels_until_explicitly_dropped(self):
        second = self.built / '_yaml/broken.yaml'
        second.write_text('song_id: 42\nvariant_id: 1\ntitle: Missing lyrics\n')
        with (self.built / 'track_list.csv').open('a') as stream:
            stream.write('42,broken.yaml,Missing lyrics,GBR hardware test,2\n')
        checked = self.preview()
        self.assertEqual((checked['ready'], checked['invalid']), (1, 1))
        cancelled = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']})
        self.assertEqual(cancelled.status_code, 400, cancelled.text)
        self.assertFalse(self.launched)
        started = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token'], 'skip_errors': True})
        self.assertEqual(started.status_code, 200, started.text)
        manifest = verify_plan(self.launched[0]['manifest'])
        self.assertEqual(len(manifest['runs']), 1)
        self.assertEqual({item['status'] for item in manifest['inputs']}, {'ready', 'excluded'})
        self.assertTrue(second.is_file())

    def test_config_saves_key_path_but_session_credentials_never_reach_disk_or_response(self):
        body = {'connection': {'backend': 'runpod_dedicated',
                 'address': 'ssh root@203.0.113.10 -p 22146 -i ~/.ssh/runpod_gbr'},
                'storage_endpoint': 'https://example.invalid', 'storage_bucket': 'private',
                'secrets': {'api_key': 'fixture-runpod-secret', 's3_secret_key': 'fixture-storage-secret'}}
        response = self.client.post('/api/generation/preferences', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['has_api_key'])
        connection = response.json()['connections'][0]
        self.assertEqual(connection['address'], 'ssh://root@203.0.113.10:22146')
        self.assertTrue(connection['identity_file'].endswith('runpod_gbr'))
        self.assertNotIn('fixture-runpod-secret', response.text)
        config = self.app.state.service.config
        self.assertNotIn('fixture-storage-secret', config.path.read_text())
        self.assertNotIn('fixture-runpod-secret', config.path.read_text())
        self.client.post('/api/generation/worker', headers=self.headers, json={'connection': {'backend': 'local'}}).raise_for_status()
        self.assertEqual(config.values['generation']['storage_bucket'], 'private')
        self.assertFalse(any('model' in connection for connection in config.values['generation']['connections']))

    def test_generation_uses_existing_host_auth_and_csrf_protection(self):
        self.assertEqual(self.client.post('/api/generation/start', json={'token': 'anything'}).status_code, 403)
        self.assertEqual(self.client.get('/tools/generation/generation.js').status_code, 200)
        self.assertIn('generationTab', self.client.get('/').text)
        remote = TestClient(self.app, base_url='http://localhost', client=('203.0.113.20', 123))
        self.assertEqual(remote.get('/api/generation/preferences').status_code, 401)
        self.app.state.service.config.values['password_hash'] = password_hash('fixture password')
        remote.post('/api/login', headers=self.headers, json={'password': 'fixture password'}).raise_for_status()
        self.assertEqual(remote.get('/api/generation/preferences').status_code, 403)

    def test_cleanup_action_only_launches_cleanup_and_never_sets_retry(self):
        folder = self.generation.runtime / 'app_jobs' / ('a' * 32)
        folder.mkdir(parents=True)
        (folder / 'task.json').write_text(json.dumps({'manifest': 'frozen.json', 'batch_id': 'b', 'connection': {'backend': 'local'}}))
        self.generation.action(folder.name, 'cleanup')
        self.assertTrue(self.launched[-1]['cleanup'])
        self.assertFalse(self.launched[-1]['retry_failed'])
        self.assertFalse(self.launched[-1]['resume'])

    def test_a_review_only_database_cannot_generate(self):
        with closing(sqlite3.connect(self.database)) as connection:
            connection.execute('DELETE FROM platform_migration_report')
            connection.commit()
        result = self.client.post('/api/generation/preview', headers=self.headers,
            json={'input_root': str(self.built), 'all_inputs': True, 'connection': {'backend': 'local'}})
        self.assertEqual(result.status_code, 400, result.text)
        self.assertFalse(self.launched)

    def test_private_key_and_connection_command_validation(self):
        for address in ['ssh root@host -p 22 -o ProxyCommand=command', 'ssh root@host -p 22; command',
                        'ssh://root:password@host:22']:
            with self.subTest(address=address), self.assertRaises(ValueError):
                normalize({'backend': 'runpod_dedicated', 'address': address})
        with self.assertRaisesRegex(ValueError, 'private RunPod SSH key'):
            normalize({'backend': 'runpod_dedicated', 'address': 'ssh://root@host:22', 'identity_file': 'key.pub'})

    def test_rerun_overrides_only_requested_values_and_request_id_prevents_a_second_batch(self):
        checked = self.preview(rerun={'sampler_steps': 40}, request_id='review-button-fixture')
        effective = checked['inputs'][0]['effective']
        self.assertEqual((effective['sampler_steps'], effective['duration'], effective['sampler_cfg']), (40, 30, 1.7))
        self.assertEqual(checked['inputs'][0]['provenance']['sampler_steps'], 'rerun')
        self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']}).raise_for_status()
        again = self.preview(rerun={'sampler_steps': 40}, request_id='review-button-fixture')
        response = self.client.post('/api/generation/start', headers=self.headers, json={'token': again['token']})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn('Rerun request already exists', response.text)
        self.assertEqual(len(self.launched), 1)
        self.assertEqual(self.source.read_bytes(), self.original)


if __name__ == '__main__':
    unittest.main()
