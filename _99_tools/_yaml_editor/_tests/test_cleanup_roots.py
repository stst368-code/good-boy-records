from pathlib import Path
from contextlib import redirect_stdout
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[3]
for folder in ('_02_generation', '_03_review/_generation_review', '_99_tools/_yaml_editor'):
    sys.path.insert(0, str(ROOT / folder))

from _gbr_yaml_editor.cleanup import main, select_roots


class CleanupRootsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        self.completed = self.repo / '_02_generation/_generated'
        self.completed.mkdir(parents=True)
        self.scratch = self.repo / '_02_generation/_runtime'
        self.scratch.mkdir()
        self.writing = self.repo / '_01_writing'
        self.writing.mkdir()

    def test_defaults_skip_protected_and_missing_without_blocking_completed(self):
        roots, warnings = select_roots(self.repo, [self.scratch, self.writing,
            self.repo / 'missing', self.completed, self.completed], automatic=True)
        self.assertEqual(roots, [self.completed.resolve()])
        self.assertEqual(len(warnings), 3)
        self.assertTrue(any(str(self.scratch) in warning for warning in warnings))

    def test_explicit_protected_folder_is_rejected_with_path(self):
        for folder in (self.scratch, self.writing):
            with self.subTest(folder=folder), self.assertRaises(ValueError) as caught:
                select_roots(self.repo, [self.completed, folder])
            self.assertIn(str(folder), str(caught.exception))
            self.assertIn('protected', str(caught.exception))

    def test_no_defaults_explains_candidates_and_root_option(self):
        with self.assertRaises(ValueError) as caught:
            select_roots(self.repo, [self.scratch, self.repo / 'missing'], automatic=True)
        self.assertIn('--root', str(caught.exception))
        self.assertIn(str(self.scratch), str(caught.exception))
        self.assertIn(str(self.repo / 'missing'), str(caught.exception))

    def test_file_is_not_a_completed_folder(self):
        file = self.repo / 'song.flac'
        file.write_bytes(b'fixture')
        with self.assertRaisesRegex(ValueError, 'not a directory'):
            select_roots(self.repo, [file])

    def test_legacy_completed_outputs_remain_eligible(self):
        outputs = self.scratch / 'local_test/outputs'
        outputs.mkdir(parents=True)
        roots, warnings = select_roots(self.repo, [outputs])
        self.assertEqual(roots, [outputs.resolve()])
        self.assertEqual(warnings, [])

    def test_symlink_to_completed_is_still_rejected(self):
        link = self.repo / 'linked'
        try:
            link.symlink_to(self.completed, target_is_directory=True)
        except OSError as exc:
            self.skipTest(str(exc))
        with self.assertRaisesRegex(ValueError, 'protected'):
            select_roots(self.repo, [link])

    def test_cli_default_preview_retains_skipped_folder_diagnostics(self):
        config = self.repo / 'review-config.json'
        config.write_text(json.dumps({'input_directories': [str(self.completed), str(self.scratch)],
                                     'archive_directory': str(self.repo / 'missing-archive')}))
        output = io.StringIO()
        plan = {'id': 'fixture', 'groups': [], 'errors': [], 'warnings': []}
        with patch('_gbr_yaml_editor.cleanup.make_plan', return_value=plan) as make_plan:
            with redirect_stdout(output):
                result = main(self.repo, ['preview', '--config', str(config)])
        self.assertEqual(result, 0)
        self.assertEqual(make_plan.call_args.args[2], [self.completed.resolve()])
        self.assertIn(str(self.scratch), output.getvalue())
        saved = self.repo / '_02_generation/_runtime/_cleanup/fixture/plan.json'
        self.assertEqual(len(json.loads(saved.read_text())['warnings']), 2)


if __name__ == '__main__':
    unittest.main()
