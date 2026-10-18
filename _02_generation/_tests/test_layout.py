"""Existing data moves once; immutable payloads and database identity survive layout changes."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from _gbr_layout import migrate, manifest_paths, relocated_path, task_paths


class DirectoryLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_move_existing_data_and_references_without_rewriting_frozen_payloads(self):
        source = self.root / '_01_Writing/_Built/_yaml/song.yaml'
        source.parent.mkdir(parents=True)
        raw = b'\xef\xbb\xbfexplicit: "yes"\r\nlyrics: |-\r\n  a\r\n  b\r\n'
        source.write_bytes(raw)
        output = self.root / '_02_Generation/generated/song/audio.flac'
        output.parent.mkdir(parents=True)
        output.write_bytes(b'audio fixture: never duplicate')
        database = self.root / '_98_Database/gbr.sqlite3'
        database.parent.mkdir()
        with sqlite3.connect(database) as conn:
            conn.execute('CREATE TABLE generation_artifacts(id TEXT,uri TEXT)')
            conn.execute('INSERT INTO generation_artifacts VALUES(?,?)', ('a', str(output)))
            conn.execute('CREATE TABLE generations(id TEXT,original_directory TEXT)')
            conn.execute('INSERT INTO generations VALUES(?,?)', ('g', str(output.parent)))
            conn.execute('CREATE TABLE sessions(id TEXT,filters_json TEXT)')
            conn.execute('INSERT INTO sessions VALUES(?,?)', ('s', json.dumps({'directory': str(output.parent), 'genre': 'Rock'})))
        frozen = json.dumps({'database': str(database), 'output_root': str(output.parent.parent)}).encode()
        manifest = self.root / '_02_Generation/runtime/batches/one/manifest.json'
        manifest.parent.mkdir(parents=True)
        manifest.write_bytes(frozen)
        migrate(self.root)
        new_source = self.root / '_01_writing/_built/_yaml/song.yaml'
        new_audio = self.root / '_02_generation/_generated/song/audio.flac'
        new_manifest = self.root / '_02_generation/_runtime/batches/one/manifest.json'
        self.assertEqual(new_source.read_bytes(), raw)
        self.assertEqual(new_audio.read_bytes(), b'audio fixture: never duplicate')
        self.assertEqual(new_manifest.read_bytes(), frozen)
        with sqlite3.connect(self.root / '_98_database/gbr.sqlite3') as conn:
            self.assertEqual(conn.execute('SELECT uri FROM generation_artifacts').fetchone()[0], str(new_audio))
            self.assertEqual(conn.execute('SELECT original_directory FROM generations').fetchone()[0], str(new_audio.parent))
            self.assertEqual(json.loads(conn.execute('SELECT filters_json FROM sessions').fetchone()[0]), {'directory': str(new_audio.parent), 'genre': 'Rock'})
        self.assertFalse(database.exists())
        self.assertFalse(output.exists())
        with patch('_gbr_layout.ROOT', self.root):
            self.assertEqual(manifest_paths(json.loads(frozen))['database'], str(self.root / '_98_database/gbr.sqlite3'))
            self.assertEqual(task_paths({'manifest': str(manifest)})['manifest'], str(new_manifest))
        self.assertEqual(migrate(self.root)['moved_files'], 0)

    def test_conflicts_are_reported_before_any_file_moves(self):
        old = self.root / '_02_Generation/generated/song/audio.flac'
        new = self.root / '_02_generation/_generated/song/audio.flac'
        for path, raw in ((old, b'old'), (new, b'new')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            migrate(self.root)
        self.assertEqual(old.read_bytes(), b'old')
        self.assertEqual(new.read_bytes(), b'new')

    def test_a_live_controller_blocks_data_moves(self):
        import psutil
        process = self.root / '_02_Generation/runtime/app_jobs/task/process.json'
        process.parent.mkdir(parents=True)
        process.write_text(json.dumps({'pid': os.getpid(), 'created': 0}))
        with patch.dict(sys.modules, {'psutil': None}), self.assertRaisesRegex(RuntimeError, 'Active controller'):
            migrate(self.root)
        self.assertTrue(process.exists())

    def test_foreign_paths_and_caption_content_are_not_rewritten(self):
        self.assertEqual(relocated_path('/somewhere/else/_01_Writing/song.yaml', self.root), '/somewhere/else/_01_Writing/song.yaml')
        payload = {'database': str(self.root / '_98_Database/gbr.sqlite3'), 'caption': '_01_Writing appears in a lyric'}
        with patch('_gbr_layout.ROOT', self.root):
            self.assertEqual(manifest_paths(payload)['caption'], payload['caption'])

    def test_retired_bytecode_does_not_block_reusing_the_new_package(self):
        old = self.root / '_03_Review/_Generation_Review/review/__pycache__/config.cpython-312.pyc'
        new = self.root / '_03_review/_generation_review/_review/__pycache__/config.cpython-312.pyc'
        for path, data in ((old, b'old compiled cache'), (new, b'new compiled cache')):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        result = migrate(self.root)
        self.assertEqual(result['discarded_old_bytecode'], 1)
        self.assertEqual(new.read_bytes(), b'new compiled cache')
        self.assertFalse(old.exists())

    def test_repository_source_directories_follow_the_convention(self):
        skip = {'.git', '.venv', '__pycache__', '.pytest_cache', 'runtime', '_runtime', '_generated'}
        for parent, dirs, files in os.walk(ROOT):
            dirs[:] = [name for name in dirs if name not in skip]
            for name in dirs:
                self.assertEqual(name, name.lower(), str(Path(parent) / name))
                self.assertTrue(name.startswith('_'), str(Path(parent) / name))
