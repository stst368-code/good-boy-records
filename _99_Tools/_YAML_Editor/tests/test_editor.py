import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gbr_yaml_editor.document import edit, repair_text
from gbr_yaml_editor.service import Editor
from gbr_generation.yaml_document import YamlDocument


class DocumentTests(unittest.TestCase):
    RAW = b'\xef\xbb\xbf# header\r\nsong_id: 1\r\ncaption: "Jazz" # keep\r\nlyrics: |-\r\n  one\r\n  two\r\n# tail\r\n'

    def test_patch_scalar_leaves_other_bytes(self):
        result = edit(self.RAW, [{'kind':'set','key':'song_id','value':22}])
        self.assertEqual(result, self.RAW.replace(b'song_id: 1', b'song_id: 22'))

    def test_missing_value_has_template_position(self):
        result = edit(self.RAW, [{'kind':'set','key':'sampler_seed','value':42}])
        self.assertEqual(result, self.RAW.replace(b'caption:', b'sampler_seed: 42\r\ncaption:'))

    def test_multiline_only_rewrites_explicit_caption(self):
        result = edit(self.RAW, [{'kind':'set','key':'caption','value':'New\ncaption\n'}])
        self.assertEqual(result, self.RAW.replace(b'"Jazz" # keep\r\n', b'| # keep\r\n  New\r\n  caption\r\n'))
        self.assertEqual(YamlDocument(result).values['lyrics'], 'one\ntwo')

    def test_chomping_and_indented_first_line(self):
        for value in (' Leading\nsecond', 'a\nb\n', 'a\nb\n\n', '\n\n', '  a\nb\n'):
            with self.subTest(value=value):
                result = edit(self.RAW, [{'kind':'set','key':'caption','value':value}])
                self.assertEqual(YamlDocument(result).values['caption'], value)

    def test_rename_and_remove_leave_lyrics_and_gap_comments(self):
        raw = b'title: Song # mine\n# lyrics comment\nlyrics: |\n  a\n  b\n'
        result = edit(raw, [{'kind':'rename','key':'title','new_key':'name'}])
        self.assertEqual(result, raw.replace(b'title:', b'name:'))
        self.assertEqual(edit(raw, [{'kind':'remove','key':'title'}]), raw.split(b'\n',1)[1])

    def test_rename_collision_refused(self):
        with self.assertRaisesRegex(ValueError, 'already exists'):
            edit(self.RAW, [{'kind':'rename','key':'song_id','new_key':'lyrics'}])

    def test_reorder_only_requested_slots(self):
        raw = b'# h\na: 1\n# stay\nuntouched: [1, 2] # yes\nc: |\n  text\n...\n'
        result = edit(raw, [{'kind':'reorder','keys':['c','a']}])
        self.assertEqual(result, b'# h\nc: |\n  text\n# stay\nuntouched: [1, 2] # yes\na: 1\n...\n')

    def test_duplicate_repair_requires_explicit_keep(self):
        raw = b'song_id: 1\nsong_id: 2\nlyrics: |\n  x\n'
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            edit(raw, [{'kind':'set','key':'song_id','value':3}])
        self.assertEqual(edit(raw, [{'kind':'deduplicate','key':'song_id','keep':'last'}]), raw.split(b'\n',1)[1])

    def test_raw_repair_preserves_mixed_eols_bom_and_last_line(self):
        raw = b'\xef\xbb\xbfsong_id: x\r\ncaption: jazz\nlyrics: |-\r\n  a\r\n  b'
        result = repair_text(raw, 'song_id: 4\ncaption: jazz\nlyrics: |-\n  a\n  b')
        self.assertEqual(result, raw.replace(b'song_id: x', b'song_id: 4'))
        self.assertEqual(repair_text(raw, raw.decode('utf-8-sig').replace('\r\n','\n')), raw)

    def test_invalid_raw_repair_refused(self):
        with self.assertRaises(ValueError):
            repair_text(self.RAW, 'song_id: [')

    def test_noop_retains_original_exactly(self):
        self.assertEqual(edit(self.RAW, [{'kind':'set','key':'caption','value':'Jazz'}]), self.RAW)

    def test_unanchored_insert_requires_position(self):
        with self.assertRaisesRegex(ValueError, 'Choose'):
            edit(self.RAW, [{'kind':'set','key':'custom','value':1}])

    def test_nested_values_preserved(self):
        raw = b'title: Song\nextra:\n  x: [1, 2]\n  y: {a: yes}\nlyrics: |\n  a\n'
        result = edit(raw, [{'kind':'set','key':'title','value':'New'}])
        self.assertEqual(result, raw.replace(b'Song', b'New'))

    def test_null_and_quoted_keys(self):
        raw = b'"song_id": # c\nlyrics: x\n'
        result = edit(raw, [{'kind':'set','key':'song_id','value':2}])
        self.assertEqual(YamlDocument(result).values['song_id'], 2)
        self.assertIn(b'# c', result)


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.editor = Editor(self.root, self.root/'backups')
        self.a = self.root/'a.yaml'
        self.a.write_bytes(DocumentTests.RAW)
        self.b = self.root/'b.yaml'
        self.b.write_bytes(DocumentTests.RAW)
        self.ops = [{'kind':'set','key':'song_id','value':3}]

    def test_scan_flags_invalid_and_does_not_write(self):
        self.b.write_text('a: [')
        result = self.editor.scan(self.root)
        self.assertEqual(len(result['files']), 2)
        self.assertTrue(result['files'][1]['issues'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)

    def test_preview_apply_and_exact_restore(self):
        p = self.editor.preview(self.root, ['a.yaml'], self.ops)
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)
        result = self.editor.apply(p['token'])
        self.assertEqual(result['saved'], 1)
        new_editor = Editor(self.root, self.root/'backups')
        restore = new_editor.restore_preview(result['batch'])
        self.assertEqual(restore['ready'], 1)
        new_editor.apply(restore['token'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)

    def test_stale_file_blocks_entire_batch(self):
        p = self.editor.preview(self.root, ['a.yaml', 'b.yaml'], self.ops)
        self.b.write_bytes(b'other: change\n')
        with self.assertRaisesRegex(ValueError, 'changed after preview'):
            self.editor.apply(p['token'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)

    def test_explicit_skip_errors(self):
        self.b.write_bytes(b'broken: [')
        p = self.editor.preview(self.root, ['a.yaml','b.yaml'], self.ops)
        with self.assertRaisesRegex(ValueError, 'errors'):
            self.editor.apply(p['token'])
        result = self.editor.apply(p['token'], skip_errors=True)
        self.assertEqual((result['saved'], result['skipped']), (1,1))
        self.assertEqual(self.b.read_bytes(), b'broken: [')

    def test_failed_replace_rolls_back_changed_files(self):
        p = self.editor.preview(self.root, ['a.yaml','b.yaml'], self.ops)
        import os
        original = os.replace
        def fail(src, dest):
            if Path(dest) == self.b:
                raise OSError('simulated failure')
            return original(src, dest)
        with patch('gbr_yaml_editor.service.os.replace', side_effect=fail):
            with self.assertRaises(OSError):
                self.editor.apply(p['token'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)
        self.assertEqual(self.b.read_bytes(), DocumentTests.RAW)
        self.assertEqual(self.editor.history()[0]['state'], 'rolled_back')

    def test_restore_does_not_overwrite_later_edits(self):
        p = self.editor.preview(self.root, ['a.yaml'], self.ops)
        batch = self.editor.apply(p['token'])['batch']
        self.a.write_text('new: external\n')
        restore = self.editor.restore_preview(batch)
        self.assertEqual(restore['errors'], 1)
        self.assertEqual(restore['ready'], 0)

    def test_symlink_and_traversal_refused(self):
        (self.root/'linked.yaml').symlink_to(self.a)
        for path in ('../a.yaml', 'linked.yaml', '/a.yaml'):
            p = self.editor.preview(self.root, [path], self.ops)
            self.assertEqual(p['errors'], 1)

    def test_expired_preview_refused(self):
        p = self.editor.preview(self.root, ['a.yaml'], self.ops)
        self.editor.previews[p['token']]['created'] = 0
        with self.assertRaisesRegex(ValueError, 'expired'):
            self.editor.apply(p['token'])

    def test_catalogue_ids_are_not_inferred_from_titles(self):
        (self.root/'track_list.csv').write_text('song_id,filename\n42,a.yaml\n')
        (self.root/'variant_list.csv').write_text('song_id,variant_id,filename\n')
        p = self.editor.preview(self.root, ['a.yaml'], [{'kind':'catalogue_ids','missing_only':False}])
        self.assertEqual(p['ready'], 1, p)
        self.editor.apply(p['token'])
        self.assertEqual(YamlDocument(self.a.read_bytes()).values['song_id'], 42)
        self.assertEqual(YamlDocument(self.a.read_bytes()).values['variant_id'], 1)

    def test_duplicate_catalogue_match_refused(self):
        (self.root/'track_list.csv').write_text('song_id,filename\n42,a.yaml\n43,a.yaml\n')
        (self.root/'variant_list.csv').write_text('song_id,variant_id,filename\n')
        p = self.editor.preview(self.root, ['a.yaml'], [{'kind':'catalogue_ids'}])
        self.assertEqual(p['errors'], 1)

    def test_text_read_stale_hash_refused(self):
        file = self.editor.read(self.root, 'a.yaml')
        self.a.write_bytes(DocumentTests.RAW + b'new: 3\n')
        p = self.editor.preview(self.root, ['a.yaml'], text=file['text'], expected={'a.yaml':file['sha256']})
        self.assertEqual(p['errors'], 1)

    def test_retention_bounded(self):
        from gbr_yaml_editor import service
        with patch.object(service, 'RETENTION', 2):
            for value in (4,5,6):
                p = self.editor.preview(self.root, ['a.yaml'], [{'kind':'set','key':'song_id','value':value}])
                self.editor.apply(p['token'])
        self.assertEqual(len(self.editor.history()), 2)


if __name__ == '__main__':
    unittest.main()
