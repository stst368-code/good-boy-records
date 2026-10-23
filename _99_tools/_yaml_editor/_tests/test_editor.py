import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _gbr_yaml_editor.document import edit, repair_text
from _gbr_yaml_editor.service import Editor
from _gbr_generation.yaml_document import YamlDocument


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

    def test_add_template_fields_defaults_order_and_exact_undo(self):
        template = self.root / 'custom-template.yaml'
        template.write_text('song_id:\nexplicit:\nduration: 360\ncaption: |\nlyrics: |\n')
        p = self.editor.preview(self.root, ['a.yaml'], [{'kind': 'add_template', 'template_path': str(template),
            'defaults': {'explicit': 'no'}}])
        self.assertEqual(p['errors'], 0, p)
        result = self.editor.apply(p['token'])
        expected = DocumentTests.RAW.replace(b'caption:', b'explicit: "no"\r\nduration: 360\r\ncaption:')
        self.assertEqual(self.a.read_bytes(), expected)
        self.assertEqual(YamlDocument(expected).values['explicit'], 'no')
        self.assertEqual(template.read_text(), 'song_id:\nexplicit:\nduration: 360\ncaption: |\nlyrics: |\n')
        unchanged = self.editor.preview(self.root, ['a.yaml'], [{'kind': 'add_template', 'template_path': str(template), 'defaults': {'explicit': 'yes'}}])
        self.assertEqual(unchanged['unchanged'], 1)
        restore = self.editor.restore_preview(result['batch'])
        self.editor.apply(restore['token'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW)

    def test_template_subset_and_frozen_preview(self):
        template = self.root / 'custom-template.yaml'
        template.write_text('explicit:\nsong_id:\nother: [1, 2]\ncaption:\nlyrics: |\n')
        preview = self.editor.preview(self.root, ['a.yaml'], [{'kind': 'add_template', 'template_path': str(template),
            'fields': ['explicit'], 'defaults': {'explicit': 'no'}, 'reorder': False}])
        template.write_text('unrelated: changed\n')
        self.editor.apply(preview['token'])
        self.assertEqual(self.a.read_bytes(), DocumentTests.RAW.replace(b'song_id:', b'explicit: "no"\r\nsong_id:'))
    def test_backup_flush_uses_a_write_capable_descriptor_on_windows(self):
        import os
        opened = {}
        original_open, original_fsync = Path.open, os.fsync
        def track_open(path, mode='r', *args, **kwargs):
            handle = original_open(path, mode, *args, **kwargs)
            if path.name == 'before.zip':
                opened[handle.fileno()] = (handle, mode)
            return handle
        def windows_commit(descriptor):
            entry = opened.get(descriptor)
            if entry and not entry[0].closed and not any(flag in entry[1] for flag in ('+', 'w', 'a', 'x')):
                raise OSError(9, 'Bad file descriptor')
            return original_fsync(descriptor)
        preview = self.editor.preview(self.root, ['a.yaml'], self.ops)
        with patch.object(Path, 'open', track_open), patch('_gbr_yaml_editor.service.os.fsync', side_effect=windows_commit):
            result = self.editor.apply(preview['token'])
        self.assertEqual(result['saved'], 1)
        self.assertTrue(any('+' in mode for _, mode in opened.values()))
        self.assertEqual(self.editor.history()[0]['state'], 'committed')

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
        with patch('_gbr_yaml_editor.service.os.replace', side_effect=fail):
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

    def order_template(self, raw=b'song_id:\ntitle:\nlyrics: |\n'):
        path=self.editor.default_order_template
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(raw)
        return path

    def test_reorder_defaults_to_the_project_yaml_template(self):
        template=self.order_template()
        original=b'lyrics: |-\n  one\n  two\nprivate_note: {x: yes} # keep\ntitle: Song\nsong_id: 1\n'
        self.a.write_bytes(original)
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder'}])
        self.assertEqual(preview['ready'],1,preview)
        self.assertEqual(preview['order_templates'][0]['keys'],['song_id','title','lyrics'])
        self.editor.apply(preview['token'])
        self.assertEqual(self.a.read_bytes(),b'song_id: 1\nprivate_note: {x: yes} # keep\ntitle: Song\nlyrics: |-\n  one\n  two\n')
        self.assertEqual(template.read_bytes(),b'song_id:\ntitle:\nlyrics: |\n')

    def test_custom_template_orders_only_fields_and_never_copies_values(self):
        template=self.root/'my template.yml'
        template.write_text('lyrics: Dummy lyrics\nsong_id: 999\ntitle: Different title\n')
        original=b'title: Actual Song\ncustom: [1, 2] # unchanged\nsong_id: 7\nlyrics: |-\n  My lyrics\n'
        self.a.write_bytes(original)
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder','order_source':'template','template_path':str(template)}])
        self.editor.apply(preview['token'])
        self.assertEqual(self.a.read_bytes(),b'lyrics: |-\n  My lyrics\ncustom: [1, 2] # unchanged\nsong_id: 7\ntitle: Actual Song\n')
        self.assertEqual(YamlDocument(self.a.read_bytes()).values,YamlDocument(original).values)

    def test_template_missing_fields_are_not_added(self):
        self.order_template()
        self.a.write_bytes(b'lyrics: text\ntitle: Song\n')
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder'}])
        self.editor.apply(preview['token'])
        self.assertEqual(self.a.read_bytes(),b'title: Song\nlyrics: text\n')
        self.assertNotIn('song_id',YamlDocument(self.a.read_bytes()).values)

    def test_invalid_or_missing_order_template_blocks_preview(self):
        with self.assertRaisesRegex(ValueError,'unavailable'):
            self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder'}])
        self.order_template(b'title:\ntitle:\n')
        with self.assertRaisesRegex(ValueError,'Duplicate'):
            self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder'}])
        self.assertEqual(self.a.read_bytes(),DocumentTests.RAW)

    def test_template_order_is_frozen_with_preview_and_journal(self):
        template=self.order_template(b'lyrics:\ncaption:\nsong_id:\n')
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder'}])
        checksum=preview['order_templates'][0]['sha256']
        template.write_text('song_id:\ncaption:\nlyrics:\n')
        result=self.editor.apply(preview['token'])
        journal=json.loads((self.editor.backups/result['batch']/'journal.json').read_text())
        self.assertEqual(journal['operations'][0]['template_sha256'],checksum)
        self.assertEqual(journal['operations'][0]['keys'],['lyrics','caption','song_id'])
        self.assertEqual(list(YamlDocument(self.a.read_bytes()).fields),['lyrics','caption','song_id'])

    def test_manual_order_does_not_require_any_template(self):
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder','order_source':'manual','keys':['lyrics','song_id']}])
        self.assertEqual(preview['ready'],1,preview)
        self.assertEqual(preview['order_templates'],[])

    def test_template_mode_ignores_client_supplied_key_order(self):
        self.order_template(b'lyrics:\ncaption:\nsong_id:\n')
        preview=self.editor.preview(self.root,['a.yaml'],[{'kind':'reorder','order_source':'template','keys':['song_id','caption','lyrics']}])
        self.editor.apply(preview['token'])
        self.assertEqual(list(YamlDocument(self.a.read_bytes()).fields),['lyrics','caption','song_id'])

    def test_same_template_is_read_once_per_preview(self):
        self.order_template()
        with patch.object(self.editor,'order_template',wraps=self.editor.order_template) as read:
            preview=self.editor.preview(self.root,['a.yaml','b.yaml'],[{'kind':'reorder'},{'kind':'reorder'}])
        self.assertEqual(read.call_count,1)
        self.assertEqual(len(preview['order_templates']),1)

    def test_text_read_stale_hash_refused(self):
        file = self.editor.read(self.root, 'a.yaml')
        self.a.write_bytes(DocumentTests.RAW + b'new: 3\n')
        p = self.editor.preview(self.root, ['a.yaml'], text=file['text'], expected={'a.yaml':file['sha256']})
        self.assertEqual(p['errors'], 1)

    def test_retention_bounded(self):
        from _gbr_yaml_editor import service
        with patch.object(service, 'RETENTION', 2):
            for value in (4,5,6):
                p = self.editor.preview(self.root, ['a.yaml'], [{'kind':'set','key':'song_id','value':value}])
                self.editor.apply(p['token'])
        self.assertEqual(len(self.editor.history()), 2)


if __name__ == '__main__':
    unittest.main()
