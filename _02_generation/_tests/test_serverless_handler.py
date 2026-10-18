"""The endpoint reuses frozen attempts and transfers failed-worker diagnostics, without CUDA."""
from copy import deepcopy
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_execution as fixtures
from _gbr_generation.planner import verify_plan
from _gbr_generation.executor import make_job
from _gbr_worker.artifacts import unpack
import serverless_handler


class HandlerTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        manifest_path = self.fixture.prepare()
        run = verify_plan(manifest_path)['runs'][0]
        self.job = make_job(run, manifest_path.parent, 'a' * 32, self.fixture.root / 'controller')
        for stage in ('conditioning', 'diffusion'):
            self.job['execution'][stage] = {'backend': 'runpod_serverless', 'address': 'endpoint-fixture'}
        self.workspace = self.fixture.root / 'volume'
        self.uploads = {}
        def upload(url, source):
            self.uploads[url] = source.read_bytes()
        self.patches = [patch.object(serverless_handler, 'workspace', return_value=self.workspace),
                        patch.object(serverless_handler, 'upload', side_effect=upload), patch.dict(os.environ)]
        for item in self.patches:
            item.start()
            self.addCleanup(item.stop)
        self.event = {'input': {'job': self.job, 'uploads': {'archive': 'https://storage.invalid/archive', 'receipt': 'https://storage.invalid/receipt'}}}

    def test_handler_uploads_once_reuses_attempt_and_refuses_changed_request(self):
        def run(command, **kwargs):
            request = Path(command[-1])
            self.fixture.worker(None, request, request.parent)
            return 0
        with patch.object(serverless_handler.subprocess, 'call', side_effect=run) as process:
            receipt = serverless_handler.handler(self.event)
            self.assertEqual(serverless_handler.handler(self.event), receipt)
            self.assertEqual(process.call_count, 1)
        self.assertEqual(len(self.uploads), 2)
        self.assertFalse(list(self.workspace.glob('attempts/*/audio.flac')))
        folder = self.fixture.root / 'verified-download'
        folder.mkdir()
        archive = self.fixture.root / 'download.tar'
        archive.write_bytes(self.uploads['https://storage.invalid/archive'])
        unpack(archive, folder, receipt, self.job)
        self.assertEqual(json.loads((folder / 'result.json').read_text())['status'], 'succeeded')
        changed = deepcopy(self.event)
        changed['input']['job']['lyrics'] += '\nChanged line'
        with self.assertRaisesRegex(ValueError, 'different frozen inputs'):
            serverless_handler.handler(changed)

    def test_hard_worker_failure_keeps_available_measurements_in_a_downloadable_failed_result(self):
        def die(command, **kwargs):
            directory = Path(command[-1]).parent
            (directory / 'events.ndjson').write_text('{"event":"worker_started"}\n')
            return 137
        with patch.object(serverless_handler.subprocess, 'call', side_effect=die):
            receipt = serverless_handler.handler(self.event)
        self.assertIn('events.ndjson', receipt['files'])
        self.assertIn('exit.json', receipt['files'])
        result = json.loads(next(self.workspace.glob('attempts/*/result.json')).read_text())
        self.assertEqual(result['status'], 'failed')
        self.assertIn('137', result['error'])
        self.assertNotIn('actual_parameters', result)


if __name__ == '__main__':
    unittest.main()
