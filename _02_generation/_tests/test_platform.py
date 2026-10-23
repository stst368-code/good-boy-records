"""Shared caption provenance and fresh-device database selection."""
from contextlib import redirect_stdout
import csv
import importlib.util
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_02_generation'))
sys.path.insert(0, str(ROOT / '_97_app'))
sys.path.insert(0, str(ROOT / '_03_review/_generation_review'))
from _gbr_generation.catalogue import Styles, validate_template_format
from platform_commands import dispatch
from first_local_test import test_database as database_for_test
from run_gbr import prepare_configuration


class SharedReferenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def catalogue(self, source='custom', template='_custom_templates/demo.txt'):
        rows = [dict(ID='demo', cluster='Rock', style_primary='Punk', template=template, template_source=source)]
        with (self.root / 'styles.csv').open('w', newline='', encoding='utf-8-sig') as handle:
            writer = csv.DictWriter(handle, fieldnames=rows[0])
            writer.writeheader(); writer.writerows(rows)
        return Styles(self.root / 'styles.csv')

    def test_fresh_app_watches_generated_inbox_without_creating_an_extra_database(self):
        path = self.root / 'config.json'
        config = prepare_configuration(self.root, path)
        generated = self.root / '_02_generation/_generated'
        self.assertEqual(config.inputs, [generated])
        self.assertTrue(generated.is_dir())
        self.assertFalse(list(self.root.rglob('*.sqlite*')))
        config.values['input_directories'].insert(0, str(self.root / 'existing-review-inputs'))
        config.save()
        again = prepare_configuration(self.root, path)
        self.assertEqual(again.inputs, [self.root / 'existing-review-inputs', generated])

    def test_custom_row_is_selectable_and_preserves_its_provenance(self):
        catalogue = self.catalogue()
        self.assertEqual(catalogue.choose('Rock', 'Punk', 'demo')['template_source'], 'custom')

    def test_origin_and_path_must_agree(self):
        for source, path in [('custom', '_minimax_templates/demo.txt'), ('unknown', '_custom_templates/demo.txt'),
                             ('custom', '../_custom_templates/demo.txt'), ('custom', '_custom_templates/demo.yaml')]:
            with self.subTest(source=source, path=path), self.assertRaises(ValueError):
                self.catalogue(source, path)

    def test_check_references_reports_missing_templates_and_passes_real_ones(self):
        self.catalogue()
        with redirect_stdout(io.StringIO()) as missing:
            self.assertEqual(dispatch(['check-references', '--root', str(self.root)]), 2)
        self.assertIn('Template missing', missing.getvalue())
        (self.root / '_custom_templates').mkdir()
        (self.root / '_custom_templates/demo.txt').write_text('Global Metadata\nA custom caption\nVocal Details\nArrangement\n')
        with redirect_stdout(io.StringIO()) as success:
            self.assertEqual(dispatch(['check-references', '--root', str(self.root)]), 0)
        self.assertIn('"custom": 1', success.getvalue())

    def test_shared_catalogue_has_one_thousand_originals_without_duplicate_ids(self):
        catalogue = Styles(ROOT / '_01_writing/_references/styles.csv')
        self.assertEqual(len(catalogue.rows), 1000)
        self.assertEqual({row['template_source'] for row in catalogue.rows}, {'minimax'})
        self.assertEqual(len(catalogue.by_id), 1000)

    def test_custom_caption_requires_the_shared_heading_format_in_order(self):
        validate_template_format('Global Metadata\nVocal Details\nArrangement\n', 'custom')
        for text in ['A free-form caption', 'Arrangement\nVocal Details\nGlobal Metadata\n']:
            with self.assertRaises(ValueError):
                validate_template_format(text, 'custom')

    def test_test_runs_use_one_shared_database_and_isolation_is_explicit(self):
        self.assertEqual(database_for_test(self.root), ROOT / '_98_database/gbr.sqlite3')
        self.assertEqual(database_for_test(self.root, isolated=True), self.root / 'local-test.sqlite3')
        self.assertEqual(database_for_test(self.root, database=self.root / 'chosen.sqlite3'), self.root / 'chosen.sqlite3')
        with self.assertRaises(ValueError):
            database_for_test(self.root, isolated=True, database=self.root / 'chosen.sqlite3')

    def test_setup_help_cannot_start_an_install(self):
        with redirect_stdout(io.StringIO()), patch('platform_commands.subprocess.call', side_effect=AssertionError('Must not install')):
            with self.assertRaises(SystemExit) as result:
                dispatch(['setup-generation', '--help'])
        self.assertEqual(result.exception.code, 0)

    def test_runpod_setup_help_cannot_connect_or_install(self):
        with redirect_stdout(io.StringIO()), patch('subprocess.run', side_effect=AssertionError('Must not connect')):
            with self.assertRaises(SystemExit) as result:
                dispatch(['setup-runpod', '--help'])
        self.assertEqual(result.exception.code, 0)

    def test_cloud_test_uses_controller_environment_without_requiring_local_cuda(self):
        with patch('platform_commands.subprocess.call', return_value=0) as call:
            self.assertEqual(dispatch(['test-generation', '--runpod', 'ssh://root@203.0.113.10:12345', '--help']), 0)
        arguments = call.call_args[0][0]
        self.assertEqual(Path(arguments[1]).name, 'first_local_test.py')
        self.assertIn('--runpod', arguments)

    def test_local_only_setup_reuses_the_installed_worker_libraries(self):
        worker = self.root / '_99_tools/_workers_infdit'
        python = worker / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
        python.parent.mkdir(parents=True)
        python.touch()
        with patch('platform_commands.ROOT', self.root), patch('platform_commands.WORKER', worker), patch('platform_commands.subprocess.call', return_value=0) as call:
            self.assertEqual(dispatch(['test-generation', '--help']), 0)
        self.assertEqual(call.call_args[0][0][0], str(python))


if __name__ == '__main__':
    unittest.main()
