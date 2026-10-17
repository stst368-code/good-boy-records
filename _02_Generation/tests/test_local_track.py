"""Full-song test entry point: real preflight and byte-preserving frozen plans."""
from contextlib import redirect_stdout, redirect_stderr
import csv
import io
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_02_Generation'))
import first_local_test as local_test
from gbr_generation.planner import verify_plan
from gbr_generation.yaml_document import YamlDocument
from gbr_database.initialize import initialize


class LocalTrackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        examples = self.root/'_02_Generation/examples/local_smoke'
        shutil.copytree(ROOT/'_02_Generation/examples/local_smoke', examples)
        self.built = self.root/'_01_Writing/_Built'
        (self.built/'_yaml').mkdir(parents=True)
        self.source = self.built/'_yaml/full-track-punk.yaml'
        self.original = (b'\xef\xbb\xbfsong_id: 13\r\nvariant_id: 2\r\ntitle: full-track\r\ncluster:\r\n'
                         b'version: do-not-interpret-me\r\nmodel:\r\ndit: fp32\r\ntext_encoder:\r\n'
                         b'caption: |\r\nlyrics: |-\r\n  [verse]\r\n  Every lyric line stays here\r\n  Second line  \r\n'
                         b'  [chorus]\r\n  The full song, untouched\r\nstory: "A private note" # keep\r\n')
        self.source.write_bytes(self.original)
        (self.built/'track_list.csv').write_text('song_id,filename,song_name\n13,full-track.yaml,full-track\n')
        (self.built/'variant_list.csv').write_text('song_id,variant_id,filename,original_style\n13,2,full-track-punk.yaml,punk-template\n')
        self.refs = self.root/'_01_Writing/_references'
        (self.refs/'_minimax_templates').mkdir(parents=True)
        self.caption = 'Global Metadata\nFast punk\nVocal Details\nA singer\nArrangement\nGuitar\n'
        (self.refs/'_minimax_templates/punk.txt').write_text(self.caption)
        with (self.refs/'styles.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=['ID','cluster','style_primary','template','template_source'])
            writer.writeheader()
            writer.writerow(dict(ID='punk-template',cluster='Rock',style_primary='Punk',template='_minimax_templates/punk.txt',template_source='minimax'))
        self.root_patch=patch.object(local_test,'ROOT',self.root)
        self.root_patch.start();self.addCleanup(self.root_patch.stop)

    def prepare(self, **kwargs):
        return local_test.prepare_inputs(filename='full-track-punk.yaml', **kwargs)

    def test_full_input_keeps_all_lyrics_and_uses_catalogue_caption(self):
        checked,spec,root,selection=self.prepare()
        record=checked[0]
        self.assertTrue(record.ready, record.summary())
        self.assertEqual(record.document.values['lyrics'], YamlDocument(self.original).values['lyrics'])
        self.assertEqual(record.effective['duration'], 360)
        self.assertEqual(record.effective['dit'], 'bf16')
        self.assertEqual(record.effective['caption'], self.caption)
        self.assertEqual(record.template['id'], 'punk-template')
        self.assertEqual(selection['template_selection'], 'catalogue.original_style')
        self.assertEqual(record.provenance['dit'], 'batch')
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertFalse((self.root/'_98_Database').exists())

    def test_explicit_duration_cache_and_template(self):
        checked,spec,_,selection=self.prepare(duration=180,encoding_cache='disabled',template_id='punk-template')
        self.assertTrue(checked[0].ready, checked[0].summary())
        self.assertEqual(checked[0].effective['duration'], 180)
        self.assertEqual(spec['execution']['encoding_cache'], 'disabled')
        self.assertEqual(selection['template_selection'],'explicit')

    def test_explicit_template_overrides_existing_caption_but_default_preserves_it(self):
        self.source.write_bytes(self.original.replace(b'caption: |\r\n',b'caption: An existing caption\r\n'))
        checked,_,_,_=self.prepare()
        self.assertTrue(checked[0].ready, checked[0].summary())
        self.assertEqual(checked[0].effective['caption'],'An existing caption')
        checked,_,_,_=self.prepare(template_id='punk-template')
        self.assertTrue(checked[0].ready, checked[0].summary())
        self.assertEqual(checked[0].effective['caption'],self.caption)

    def test_invalid_source_ids_still_block_full_track(self):
        self.source.write_bytes(self.original.replace(b'song_id: 13',b'song_id: 99'))
        checked,_,_,_=self.prepare()
        self.assertFalse(checked[0].ready)
        self.assertIn('catalogue_id_mismatch',[issue.code for issue in checked[0].issues])

    def test_preflight_errors_do_not_invoke_worker_or_create_database(self):
        self.source.write_bytes(self.original.replace(b'song_id: 13',b'song_id: 99'))
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()), patch.object(local_test.subprocess,'run',side_effect=AssertionError('No worker')):
            with self.assertRaises(SystemExit) as result:
                local_test.main(['--file','full-track-punk.yaml'])
        self.assertEqual(result.exception.code,2)
        self.assertFalse((self.root/'_98_Database').exists())

    def test_unrecognised_catalogue_template_cannot_be_guessed(self):
        with self.assertRaisesRegex(ValueError,'absent'):
            self.prepare(template_id='not-a-template')

    def test_duration_outside_native_limit_is_rejected(self):
        for duration in (0,361,float('nan')):
            with self.subTest(duration=duration):
                checked,_,_,_=self.prepare(duration=duration)
                self.assertFalse(checked[0].ready)

    def test_prepare_only_uses_shared_database_and_freezes_exact_source(self):
        database=self.root/'_98_Database/gbr.sqlite3'
        initialize(database)
        with redirect_stdout(io.StringIO()), patch.object(local_test.subprocess,'run',side_effect=AssertionError('No CUDA')), patch.object(local_test,'execute',side_effect=AssertionError('Must not execute')):
            self.assertEqual(local_test.main(['--file','full-track-punk.yaml','--prepare-only']),0)
        pointer=self.root/'_02_Generation/runtime/local_test/latest.json'
        path=Path(json.loads(pointer.read_text())['manifest'])
        manifest=verify_plan(path)
        self.assertEqual(manifest['database'],str(database))
        self.assertEqual(len(manifest['runs']),1)
        run=manifest['runs'][0]
        self.assertEqual((path.parent/run['snapshot']).read_bytes(),self.original)
        request=YamlDocument((path.parent/run['request_yaml']).read_bytes())
        original=YamlDocument(self.original)
        for field in ('lyrics','version','story','song_id','variant_id'):
            self.assertEqual(request.values[field],original.values[field])
        self.assertEqual(self.source.read_bytes(),self.original)
        self.assertFalse((pointer.parent/'local-test.sqlite3').exists())

    def test_retry_rejects_parameter_or_database_changes(self):
        for tail in (['--duration','180'],['--file','full-track-punk.yaml'],['--isolated'],['--encoding-cache','disabled']):
            with self.subTest(tail=tail), redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as result:
                local_test.main(['--retry',*tail])
            self.assertEqual(result.exception.code,2)

    def test_short_smoke_test_remains_30_seconds(self):
        checked,_,_,selection=local_test.prepare_inputs()
        self.assertTrue(checked[0].ready, checked[0].summary())
        self.assertEqual(checked[0].effective['duration'],30)
        self.assertEqual(selection['purpose'],'local_hardware_test')

    def test_template_without_file_rejected(self):
        with self.assertRaisesRegex(ValueError,'require --file'):
            local_test.prepare_inputs(template_id='punk-template')


if __name__=='__main__':
    unittest.main()
