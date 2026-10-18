"""Private result transfer, exact-once submission and resume, without paid cloud calls."""
from copy import deepcopy
import io
import json
import os
from pathlib import Path
import shutil
import tarfile
import unittest
from unittest.mock import patch

import test_execution as fixtures
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.executor import execute
from _gbr_generation.planner import preflight, prepare_batch
from _gbr_worker.artifacts import pack, unpack
from _gbr_worker.contract import canonical, file_sha
from _gbr_worker.runpod import RemotePending
from _gbr_worker.serverless import ServerlessClient


class Store:
    def __init__(self, root):
        self.root, self.saved, self.deleted = root, {}, []
        root.mkdir()

    def upload_urls(self, key):
        return {'archive': 'https://storage.invalid/upload-archive', 'receipt': 'https://storage.invalid/upload-receipt'}

    def receipt(self, key):
        return self.saved.get(key)

    def download(self, key, destination):
        shutil.copyfile(self.root / 'outputs.tar', destination)

    def remove(self, key):
        self.deleted.append(key)
        self.saved.pop(key, None)


class ServerlessTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.store = Store(self.root / 'storage')
        self.spec = deepcopy(self.fixture.spec)
        self.spec['execution'].update(memory_profile='resident_stage',
            conditioning={'backend': 'runpod_serverless', 'address': 'endpoint-test'},
            diffusion={'backend': 'runpod_serverless', 'address': 'endpoint-test'})
        catalogue = Catalogue(self.fixture.built)
        checked = preflight(catalogue.select(all_inputs=True), self.spec, Styles(self.fixture.built / 'styles.csv'))
        self.manifest = prepare_batch(checked, self.spec, self.root / 'runtime', self.fixture.database, input_root=self.fixture.built,
                                     output_root=self.root / 'generated')
        self.requests, self.lost, self.complete_on_submit = [], False, True
        self.environment = patch.dict(os.environ, {'RUNPOD_API_KEY': 'fixture-session-secret'})
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def complete(self, job):
        remote = self.root / 'remote'
        remote.mkdir(exist_ok=True)
        path = remote / 'request.json'
        path.write_text(canonical(job))
        self.fixture.worker(None, path, remote)
        (remote / 'exit.json').write_text('{"returncode":0}')
        receipt = pack(remote, self.store.root / 'outputs.tar', job)
        self.store.saved[f"gbr-generations/{job['run_id']}/{job['attempt_id']}"] = receipt
        return receipt

    def client(self, endpoint):
        client = ServerlessClient(endpoint, store=self.store)
        def api(operation, body=None):
            self.requests.append((operation, body))
            if operation == 'run':
                if self.complete_on_submit:
                    self.complete(body['input']['job'])
                if self.lost:
                    raise RemotePending('injected lost submission response')
                return {'id': 'fixture-provider-job', 'status': 'IN_QUEUE'}
            if operation.startswith('status/'):
                return {'status': 'COMPLETED'}
            return {'workers': {'ready': 0}}
        client.api = api
        return client

    def execute(self, **kwargs):
        with patch('_gbr_generation.executor.ServerlessClient', side_effect=self.client):
            return execute(self.manifest, worker_python=self.root / 'no-local-worker', **kwargs)

    def test_serverless_publishes_verified_bundle_and_removes_only_its_temporary_objects(self):
        result = self.execute()
        self.assertEqual(result['status'], 'succeeded', result)
        output = Path(result['attempts'][0]['output'])
        self.assertEqual((output / 'source.yaml').read_bytes(), self.fixture.original)
        self.assertEqual(len(self.store.deleted), 1)
        self.assertTrue(self.store.deleted[0].startswith('gbr-generations/'))
        self.assertEqual(self.fixture.rows('SELECT execution_mode FROM generation_attempts'), [('runpod_serverless',)])
        self.assertEqual(len(self.requests), 1)
        task_body = self.requests[0][1]
        self.assertNotIn('fixture-session-secret', canonical(task_body))
        self.assertNotIn('AWS_SECRET_ACCESS_KEY', canonical(task_body))
        self.assertFalse(list(self.manifest.parent.glob('attempts/*/audio.flac')))
        self.assertEqual(self.fixture.source.read_bytes(), self.fixture.original)

    def test_lost_post_reply_resumes_from_storage_without_resubmitting_or_provider_job_id(self):
        self.lost = True
        first = self.execute()
        self.assertEqual(first['status'], 'running', first)
        second = self.execute(resume_remote=True)
        self.assertEqual(second['status'], 'succeeded', second)
        self.assertEqual(first['attempts'][0]['attempt_id'], second['attempts'][0]['attempt_id'])
        self.assertEqual(len(self.requests), 1)
        self.assertEqual(len(self.fixture.calls), 1)

    def test_unknown_submission_with_no_receipt_never_sends_a_second_post(self):
        self.lost, self.complete_on_submit = True, False
        first = self.execute()
        second = self.execute(resume_remote=True)
        self.assertEqual((first['status'], second['status']), ('running', 'running'))
        self.assertEqual(len(self.requests), 1)
        self.assertFalse(self.fixture.calls)
        job = json.loads((Path(first['attempts'][0]['diagnostics']) / 'request.json').read_text())
        self.complete(job)
        third = self.execute(resume_remote=True)
        self.assertEqual(third['status'], 'succeeded', third)
        self.assertEqual(len(self.requests), 1)

    def test_corrupt_transfer_does_not_publish_or_clean_remote_result(self):
        self.lost = True
        first = self.execute()
        archive = self.store.root / 'outputs.tar'
        original = archive.read_bytes()
        archive.write_bytes(b'truncated')
        result = self.execute(resume_remote=True)
        self.assertEqual(result['status'], 'running', result)
        self.assertFalse(self.store.deleted)
        self.assertFalse(list((self.root / 'generated').rglob('*.flac')))
        self.assertFalse(list(self.manifest.parent.glob('attempts/*/*.download')))
        archive.write_bytes(original)
        self.assertEqual(self.execute(resume_remote=True)['status'], 'succeeded')

    def test_archive_path_traversal_and_symlinks_are_rejected_before_replacing_files(self):
        job = {'run_id': 'a' * 32, 'attempt_id': 'b' * 32}
        directory = self.root / 'download'
        directory.mkdir()
        (directory / 'result.json').write_text('keep verified result')
        archive = self.root / 'malicious.tar'
        for link in (False, True):
            with self.subTest(link=link):
                with tarfile.open(archive, 'w') as tar:
                    member = tarfile.TarInfo('result.json' if link else '../escape')
                    member.size = 1
                    if link:
                        member.type, member.linkname = tarfile.SYMTYPE, '../escape'
                    tar.addfile(member, None if link else io.BytesIO(b'x'))
                receipt = {'protocol': 1, **job, 'sha256': file_sha(archive), 'size_bytes': archive.stat().st_size,
                           'files': {'result.json': {'sha256': 'irrelevant', 'size_bytes': 1}, 'exit.json': {'size_bytes': 0}}}
                with self.assertRaisesRegex(ValueError, 'archive member'):
                    unpack(archive, directory, receipt, job)
                self.assertEqual((directory / 'result.json').read_text(), 'keep verified result')
                self.assertFalse((self.root / 'escape').exists())


if __name__ == '__main__':
    unittest.main()
