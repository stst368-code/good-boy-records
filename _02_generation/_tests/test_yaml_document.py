from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from _gbr_generation.parameters import output_changes, resolve
from _gbr_generation.planner import write_completed_yaml
from _gbr_generation.yaml_document import YamlDocument, YamlError

RAW = (b'\xef\xbb\xbf# preserve\r\nsong_id: 1\nvariant_id: 1\r\ntitle: "caf\xc3\xa9"\n'
       b'custom: {unknown: [one, two]} # preserve\r\nencoder_cfg: 1.5 # cfg\n'
       b"encoder_seed: '9007199254740993'\r\nsampler_steps:    # keep\n"
       b'caption: | # caption\r\n  A caption.\r\nlyrics: |-\n  [verse]\r\n  caf\xc3\xa9\n\n  last line  ')


class YamlTests(unittest.TestCase):
    def test_noop_is_byte_identical_including_bom_and_mixed_newlines(self):
        self.assertEqual(YamlDocument(RAW).with_generation_values({}), RAW)

    def test_unknown_nonfinite_metadata_is_preserved_without_normalization(self):
        raw = b'title: test\nunknown: {value: .nan}\nencoder_cfg: 1.5\nlyrics: |\n  words\n'
        self.assertEqual(YamlDocument(raw).with_generation_values({'encoder_cfg': 1.7}), raw.replace(b'1.5', b'1.7'))

    def test_scalar_patches_keep_unrelated_bytes_exact(self):
        expected = RAW.replace(b'encoder_cfg: 1.5', b'encoder_cfg: 1.7').replace(b'sampler_steps:', b'sampler_steps: 40')
        output = YamlDocument(RAW).with_generation_values({'encoder_cfg': 1.7, 'sampler_steps': 40})
        self.assertEqual(output, expected)

    def test_missing_fields_follow_template_order_without_reordering_existing_keys(self):
        raw = b'title: test\n# keep\nlyrics: |\r\n  Lyrics\r\n'
        output = YamlDocument(raw).with_generation_values({'caption': 'Caption\n', 'sampler_cfg': 1.7,
                                                          'encoder_seed': '18446744073709551615', 'style': 'punk'})
        self.assertLess(output.index(b'style:'), output.index(b'encoder_seed:'))
        self.assertLess(output.index(b'encoder_seed:'), output.index(b'sampler_cfg:'))
        self.assertLess(output.index(b'sampler_cfg:'), output.index(b'caption:'))
        self.assertLess(output.index(b'caption:'), output.index(b'lyrics:'))
        self.assertEqual(output[output.index(b'lyrics:'):], raw[raw.index(b'lyrics:'):])

    def test_duplicate_keys_including_nested_mappings_are_rejected(self):
        for raw in [b'title: a\ntitle: b\n', b'title: a\ncustom: {key: a, key: b}\n']:
            with self.subTest(raw=raw), self.assertRaisesRegex(YamlError, 'Duplicate'):
                YamlDocument(raw)

    def test_aliases_tags_flow_documents_and_multiple_documents_are_rejected(self):
        for raw in [b'title: &x a\nlyrics: *x\n', b'title: !!str a\n', b'{title: a}', b'title: a\n---\ntitle: b']:
            with self.subTest(raw=raw), self.assertRaises(YamlError):
                YamlDocument(raw)

    def test_lyrics_and_unknown_fields_cannot_be_changed_by_generation(self):
        for field in ['lyrics', 'custom', 'title', 'song_id', 'variant_id', 'version']:
            with self.subTest(field=field), self.assertRaises(YamlError):
                YamlDocument(RAW).with_generation_values({field: 'changed'})

    def test_fields_are_never_appended_without_a_template_anchor(self):
        with self.assertRaisesRegex(YamlError, 'anchor'):
            YamlDocument(b'title: test\n').with_generation_values({'sampler_cfg': 1.7})

    def test_caption_replacement_preserves_lyrics_and_header_comment(self):
        for caption in ['new caption', 'new\ncaption\n', 'new\ncaption\n\n', '  leading spaces\nplain\n']:
            with self.subTest(caption=caption):
                output = YamlDocument(RAW).with_generation_values({'caption': caption})
                self.assertEqual(YamlDocument(output).values['caption'], caption)
                self.assertIn(b'# caption\r\n', output)
                self.assertEqual(output[output.index(b'lyrics:'):], RAW[RAW.index(b'lyrics:'):])
                self.assertEqual(output[:output.index(b'caption:')], RAW[:RAW.index(b'caption:')])

    def test_numerically_unchanged_controls_keep_original_spelling(self):
        document = YamlDocument(RAW)
        changes = output_changes(document, {'encoder_cfg': 1.5, 'encoder_seed': '9007199254740993'})
        self.assertEqual(changes, {})
        self.assertEqual(document.with_generation_values(changes), RAW)

    def test_rerun_overrides_win_even_when_yaml_has_a_fixed_seed(self):
        values, provenance = resolve(YamlDocument(RAW), {
            'preset': {'encoder_cfg': 1.6}, 'batch': {'encoder_cfg': 1.7},
            'rerun': {'encoder_cfg': 1.8, 'encoder_seed': '18446744073709551615'},
            'field_precedence': {'encoder_cfg': ['batch', 'yaml']}})
        self.assertEqual(values['encoder_cfg'], 1.8)
        self.assertEqual(values['encoder_seed'], '18446744073709551615')
        self.assertEqual(provenance['encoder_cfg'], 'rerun')

    def test_completion_timestamp_is_quoted_local_time_and_original_is_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'song.yaml'
            write_completed_yaml(RAW, {'encoder_cfg': 1.7}, output, succeeded=True,
                                 completed_at=datetime(2026, 10, 17, 14, 23, tzinfo=timezone.utc))
            document = YamlDocument(output.read_bytes())
            self.assertEqual(document.values['generation_datetime'], '2610171523')
            self.assertIn(b'generation_datetime: "2610171523"', output.read_bytes())
            self.assertEqual(output.read_bytes().split(b'lyrics:', 1)[1], RAW.split(b'lyrics:', 1)[1])
            with self.assertRaises(FileExistsError):
                write_completed_yaml(RAW, {}, output, succeeded=True)

    def test_failed_generation_cannot_write_a_completed_yaml(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'song.yaml'
            with self.assertRaises(ValueError):
                write_completed_yaml(RAW, {}, output, succeeded=False)
            self.assertFalse(output.exists())

    def test_publication_failure_leaves_no_partial_sidecar(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'song.yaml'
            with patch('_gbr_generation.planner.os.link', side_effect=OSError('publication failed')):
                with self.assertRaises(OSError):
                    write_completed_yaml(RAW, {}, output, succeeded=True)
            self.assertEqual(list(Path(directory).iterdir()), [])


if __name__ == '__main__':
    unittest.main()
