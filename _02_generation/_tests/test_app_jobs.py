"""Background queue controls operate on frozen runs and never retry inference implicitly."""
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

import test_execution as fixtures
from _gbr_generation import app_jobs
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan


class AppQueueTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        second = self.fixture.built / '_yaml/another-song.yaml'
        second.write_bytes(self.fixture.original.replace(b'900000001', b'900000002').replace(b'title: gbr-local-smoke', b'title: Another song'))
        with (self.fixture.built / 'track_list.csv').open('a') as stream:
            stream.write('900000002,another-song.yaml,Another song,GBR hardware test,2\n')
        self.manifest = self.fixture.prepare()
        self.folder = self.root / 'app-job'
        self.folder.mkdir()
        self.task = self.folder / 'task.json'
        self.task.write_text(json.dumps({'manifest': str(self.manifest), 'batch_id': self.manifest.parent.name,
            'connection': {'backend': 'local', 'worker_python': sys.executable}}))

    def run_task(self, worker):
        def dispatch(manifest, **options):
            return execute(manifest, launcher=worker, **options)
        with patch.object(app_jobs, 'prepare_worker', return_value={'hardware': {'gpu_name': 'CPU boundary fixture'}}), \
             patch.object(app_jobs, 'execute', side_effect=dispatch):
            return app_jobs.run_task(self.task)

    def test_pause_finishes_current_song_and_continue_runs_only_remaining_inputs(self):
        def pause_after_first(*args):
            self.fixture.worker(*args)
            (self.folder / 'pause').touch()
        self.assertEqual(self.run_task(pause_after_first), 0)
        self.assertEqual(json.loads((self.folder / 'state.json').read_text())['status'], 'paused')
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(sorted(row[0] for row in self.fixture.rows('SELECT status FROM generation_runs')), ['prepared', 'succeeded'])
        (self.folder / 'pause').unlink()
        self.assertEqual(self.run_task(self.fixture.worker), 0)
        self.assertEqual(json.loads((self.folder / 'state.json').read_text())['status'], 'succeeded')
        self.assertEqual(len(self.fixture.calls), 2)
        self.assertNotEqual(self.fixture.calls[0]['run_id'], self.fixture.calls[1]['run_id'])

    def test_failed_song_stops_queue_without_implicit_retry(self):
        self.assertEqual(self.run_task(lambda *args: self.fixture.worker(*args, fail=True)), 1)
        self.assertEqual(json.loads((self.folder / 'state.json').read_text())['status'], 'failed')
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(sorted(row[0] for row in self.fixture.rows('SELECT status FROM generation_runs')), ['failed', 'prepared'])

    def test_cleanup_controller_does_not_prepare_a_worker_or_call_inference(self):
        task = json.loads(self.task.read_text())
        self.task.write_text(json.dumps({**task, 'cleanup': True}))
        with patch.object(app_jobs, 'cleanup_outputs', return_value={'cleaned_attempts': ['fixture']}) as cleanup, \
             patch.object(app_jobs, 'prepare_worker') as prepare, patch.object(app_jobs, 'execute') as inference:
            self.assertEqual(app_jobs.run_task(self.task), 0)
        cleanup.assert_called_once()
        prepare.assert_not_called()
        inference.assert_not_called()


if __name__ == '__main__':
    unittest.main()
