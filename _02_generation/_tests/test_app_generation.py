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
for folder in ('_02_generation', '_98_database', '_03_review/_generation_review', '_99_tools/_workers_infdit'):
    sys.path.insert(0, str(ROOT / folder))
from _gbr_database.initialize import initialize
from _gbr_generation.connections import normalize
from _gbr_generation.planner import verify_plan
from _review.app import create_app
from _review.config import password_hash


class GenerationAppTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.built = self.root / '_01_writing/_built'
        shutil.copytree(ROOT / '_02_generation/_examples/_local_smoke', self.built)
        refs = self.root / '_01_writing/_references'
        refs.mkdir()
        shutil.copyfile(self.built / 'styles.csv', refs / 'styles.csv')
        self.source = self.built / '_yaml/gbr-local-smoke.yaml'
        self.original = self.source.read_bytes()
        self.database = self.root / '_98_database/gbr.sqlite3'
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

    def test_load_and_preview_share_default_and_container_root_resolution(self):
        for value in ['', '   ', str(self.root), str(self.root / '_01_writing'),
                      '_01_writing/_built', '_01_Writing\\_Built']:
            with self.subTest(root=value):
                loaded = self.client.get('/api/generation/catalogue', params={'root': value})
                self.assertEqual(loaded.status_code, 200, loaded.text)
                self.assertEqual(Path(loaded.json()['root']), self.built)
                checked = self.preview(input_root=value, files=['_yaml/gbr-local-smoke.yaml'])
                self.assertEqual(checked['ready'], 1)
                self.assertEqual(self.generation.previews[checked['token']]['root'], self.built)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.root / 'track_list.csv').exists())
        self.assertFalse(self.launched)

    def test_saved_blank_or_container_paths_are_normalized_without_losing_worker_settings(self):
        config = self.app.state.service.config
        for value in [None, '', '   ', str(self.root), str(self.root / '_01_writing')]:
            with self.subTest(root=value):
                config.values['generation'] = {'input_root': value, 'storage_bucket': 'keep-bucket',
                    'parameters': {'sampler_cfg': 2.4}, 'layout': 'album'}
                returned = self.client.get('/api/generation/preferences').json()
                self.assertEqual(Path(returned['input_root']), self.built)
                saved = self.client.post('/api/generation/preferences', headers=self.headers,
                    json={'input_root': '' if value is None else value, 'connection': {'backend': 'local'}})
                self.assertEqual(saved.status_code, 200, saved.text)
                values = json.loads(config.path.read_text())['generation']
                self.assertEqual(Path(values['input_root']), self.built)
                self.assertEqual(values['storage_bucket'], 'keep-bucket')
                self.assertEqual(values['parameters'], {'sampler_cfg': 2.4})
                self.assertEqual(values['layout'], 'album')

    def test_custom_catalogue_is_used_and_invalid_explicit_folders_do_not_fall_back(self):
        custom = self.root / 'custom writing'
        shutil.copytree(self.built, custom)
        loaded = self.client.get('/api/generation/catalogue', params={'root': str(custom)})
        self.assertEqual(loaded.status_code, 200, loaded.text)
        checked = self.preview(input_root=str(custom))
        self.assertEqual(self.generation.previews[checked['token']]['root'], custom)
        missing = self.root / 'wrong writing folder'; missing.mkdir()
        for method in ['load', 'preview']:
            with self.subTest(action=method):
                response = self.client.get('/api/generation/catalogue', params={'root': str(missing)}) if method == 'load' else self.client.post(
                    '/api/generation/preview', headers=self.headers,
                    json={'input_root': str(missing), 'all_inputs': True, 'connection': {'backend': 'local'}})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn(str(missing), response.json()['detail'])
        self.assertFalse(self.launched)

    def test_explicit_catalogue_at_repository_root_remains_supported(self):
        for name in ['track_list.csv', 'variant_list.csv']:
            shutil.copyfile(self.built / name, self.root / name)
        master = self.root / 'track_list.csv'
        master.write_text(master.read_text().replace('gbr-local-smoke.yaml', '_yaml/gbr-local-smoke.yaml'))
        shutil.copytree(self.built / '_yaml', self.root / '_yaml')
        checked = self.preview(input_root=str(self.root), all_inputs=False, files=['_yaml/gbr-local-smoke.yaml'])
        self.assertEqual(self.generation.previews[checked['token']]['root'], self.root)
        self.assertEqual(checked['ready'], 1)

    def test_successful_start_registers_inbox_and_is_single_use(self):
        checked = self.preview(parameters={'duration': 360}, parameter_priority='form', layout='album')
        response = self.client.post('/api/generation/start', headers=self.headers, json={'token': checked['token']})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.launched), 1)
        manifest = verify_plan(self.launched[0]['manifest'])
        self.assertEqual(manifest['output_root'], str(self.root / '_02_generation/_generated'))
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

    def test_caption_diversity_ranges_repetitions_and_frozen_seed_lineage(self):
        refs = self.root / '_01_writing/_references'
        (refs / 'styles.csv').write_text('ID,cluster,style_primary,template\na,Rock,Punk,a.txt\nb,Rock,Folk,b.txt\n')
        (refs / 'a.txt').write_text('Loud punk guitar\n')
        (refs / 'b.txt').write_text('Acoustic folk guitar\n')
        preview = self.preview(caption_mode='random', caption_count=2,
            experiments={'repetitions': 2, 'seed_policy': 'randomize', 'sweeps': {'sampler_cfg': '1:2:0.5', 'sampler_steps': [20, 30]}})
        self.assertEqual((preview['ready'], preview['generation_count']), (1, 24))
        first = deepcopy(preview['inputs'][0]['experiments'])
        result = self.client.post('/api/generation/start', headers=self.headers, json={'token': preview['token']})
        self.assertEqual(result.status_code, 200, result.text)
        manifest = verify_plan(self.launched[0]['manifest'])
        runs = manifest['runs']
        self.assertEqual(len(runs), 24)
        self.assertEqual(len({r['snapshot'] for r in runs}), 1)
        self.assertEqual({r['template']['id'] for r in runs}, {'a', 'b'})
        self.assertEqual({r['effective']['sampler_cfg'] for r in runs}, {1, 1.5, 2})
        self.assertEqual({r['effective']['sampler_steps'] for r in runs}, {20, 30})
        self.assertEqual(len({r['effective']['encoder_seed'] for r in runs}), 24)
        self.assertEqual(len({r['effective']['sampler_seed'] for r in runs}), 24)
        self.assertEqual([r['effective']['caption'] for r in runs], [c['effective']['caption'] for c in first])
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_invalid_sweep_value_prevents_the_input_from_running(self):
        preview = self.preview(experiments={'sweeps': {'sampler': ['euler', 'not-a-sampler']}})
        self.assertEqual((preview['ready'], preview['invalid'], preview['generation_count']), (0, 1, 0))
        response = self.client.post('/api/generation/start', headers=self.headers, json={'token': preview['token']})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.launched)

    def test_random_captions_stay_inside_the_requested_low_rating_combination(self):
        refs = self.root / '_01_writing/_references'
        (refs / 'styles.csv').write_text('ID,cluster,style_primary,template\na,Rock,Punk,a.txt\nb,Jazz,Choral,b.txt\nc,Rock,Punk,c.txt\n')
        for name in ('a', 'b', 'c'):
            (refs / (name + '.txt')).write_text('Caption ' + name)
        with patch('_gbr_generation.web_service.select_by_rating', side_effect=lambda inputs, *args, **kwargs: (inputs, {'filter_fixture': True})):
            result = self.preview(caption_mode='random', caption_count=2,
                rating_filter={'threshold': 3, 'cluster': 'Rock', 'style': 'Punk'})
        self.assertEqual((result['ready'], result['generation_count']), (1, 2))
        self.assertEqual({case['template']['id'] for case in result['inputs'][0]['experiments']}, {'a', 'c'})

    def test_continuing_an_old_setup_failure_restores_auto_setup_but_resume_never_installs(self):
        folder = self.root / 'old-task'
        folder.mkdir()
        task = {'manifest': str(self.root / 'frozen-manifest.json'), 'batch_id': 'old-batch',
                'install': False, 'connection': {'backend': 'runpod_dedicated', 'auto_setup': True}}
        with patch.object(self.generation, 'task', return_value=(folder, task)), patch.object(self.generation, 'active', return_value=False):
            self.generation.action('a' * 32, 'continue')
            self.assertTrue(self.launched[-1]['install'])
            self.generation.action('a' * 32, 'resume')
            self.assertFalse(self.launched[-1]['install'])
            task['connection']['auto_setup'] = False
            self.generation.action('a' * 32, 'retry')
            self.assertFalse(self.launched[-1]['install'])

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
