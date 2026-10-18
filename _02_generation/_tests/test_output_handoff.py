"""Real FLAC publication and reviewed-bundle movement preserve a single verified copy."""
from pathlib import Path
import shutil
import sys
import unittest
from unittest.mock import patch

import test_execution as fixtures
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.executor import recover
from _gbr_generation.planner import preflight, prepare_batch
from _gbr_worker.contract import file_sha
from _review.archive import verified_copy
from _review.config import Config
from _review.service import Service


class OutputHandoffTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ExecutionTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root = self.fixture.root
        self.generated = self.root / '_02_generation/_generated'

    def generate(self, layout='single'):
        fixture = self.fixture
        catalogue = Catalogue(fixture.built)
        checked = preflight(catalogue.select(all_inputs=True), fixture.spec, Styles(fixture.built / 'styles.csv'))
        manifest = prepare_batch(checked, fixture.spec, self.root / 'runtime', fixture.database,
            input_root=fixture.built, output_root=self.generated, output_layout=layout)
        result = fixture.run_plan(manifest)
        self.assertEqual(result['status'], 'succeeded', result)
        self.manifest, self.result = manifest, result
        return Path(result['attempts'][0]['output'])

    def service(self, output):
        config = Config(self.root / 'review-config.json')
        config.values.update(database_path=str(self.fixture.database), input_directories=[str(self.generated)])
        service = Service(config)
        self.addCleanup(service.close)
        service.indexer.index(next(output.glob('*.flac')))
        gid = service.db.one('SELECT id FROM generations')['id']
        service.queues.open(service.default_reviewer_id, gid)
        service.review(gid, dict(audio_quality=5, composition=5, consistency=5, realism=5), 'full')
        return service, service.db.one('SELECT * FROM archive_operations')

    def test_output_layouts_and_temporary_audio_cleanup(self):
        for layout, depth in [('single', 1), ('track', 2), ('album', 2)]:
            with self.subTest(layout=layout):
                output = self.generate(layout)
                self.assertEqual(len(output.relative_to(self.generated).parts), depth)
                self.assertEqual(len(list(output.iterdir())), 4)
                self.assertEqual((output / 'source.yaml').read_bytes(), self.fixture.original)
                self.assertEqual(self.fixture.source.read_bytes(), self.fixture.original)
                self.assertFalse((Path(self.result['attempts'][0]['diagnostics']) / 'audio.flac').exists())
        self.assertIn('gbr-hardware-test', str(output.parent))

    def test_review_moves_all_companions_updates_paths_and_recovers_without_regeneration(self):
        output = self.generate()
        hashes = {p.name: file_sha(p) for p in output.iterdir()}
        service, operation = self.service(output)
        service.archiver.process(operation)
        archived = service.db.one('SELECT * FROM archive_operations')
        self.assertEqual(archived['status'], 'done', archived)
        self.assertFalse(output.exists())
        self.assertEqual(list(self.generated.rglob('*.flac')), [])
        artifacts = service.db.all('SELECT * FROM generation_artifacts')
        self.assertEqual(len(artifacts), 4)
        self.assertEqual({a['sha256'] for a in artifacts}, set(hashes.values()))
        for artifact in artifacts:
            path = Path(artifact['uri'])
            self.assertTrue(path.is_relative_to(service.config.archive))
            self.assertEqual(file_sha(path), artifact['sha256'])
        self.assertEqual(len(list(service.config.archive.rglob('*.flac'))), 1)
        self.assertEqual(len(list(service.config.archive.rglob('*.*'))), 4)
        self.assertEqual(self.fixture.source.read_bytes(), self.fixture.original)
        run = self.result['attempts'][0]['run_id']
        self.assertEqual(recover(self.manifest, run)['status'], 'succeeded')
        self.assertEqual(len(self.fixture.calls), 1)
        self.assertEqual(self.fixture.rows('PRAGMA foreign_key_check'), [])

    def test_failed_companion_copy_keeps_the_inbox_and_can_resume_archive(self):
        output = self.generate()
        service, operation = self.service(output)
        def fail_report(source, destination, expected, operation_id):
            if str(source).endswith('generation.json'):
                raise OSError('simulated archive drive failure')
            return verified_copy(source, destination, expected, operation_id)
        with patch('_review.archive.verified_copy', side_effect=fail_report):
            service.archiver.process(operation)
        self.assertEqual(service.db.one('SELECT status FROM archive_operations')['status'], 'failed')
        self.assertEqual(len(list(output.iterdir())), 4)
        self.assertTrue(all(Path(row['uri']).parent == output for row in service.db.all('SELECT uri FROM generation_artifacts')))
        service.archiver.retry()
        service.archiver.process(service.db.one('SELECT * FROM archive_operations'))
        self.assertEqual(service.db.one('SELECT status FROM archive_operations')['status'], 'done')
        self.assertFalse(output.exists())

    def test_review_scan_ignores_unpublished_staging_bundles(self):
        output = self.generate()
        staging = self.generated / '.gbr-incomplete'
        shutil.copytree(output, staging)
        config = Config(self.root / 'review-config.json')
        config.values.update(database_path=str(self.fixture.database), input_directories=[str(self.generated)])
        service = Service(config)
        self.addCleanup(service.close)
        self.assertEqual(service.indexer.scan(), 1)
        self.assertEqual(service.db.one('SELECT COUNT(*) n FROM media_files')['n'], 1)


if __name__ == '__main__':
    unittest.main()
