"""Existing data moves once; immutable payloads and database identity survive layout changes."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from _gbr_layout import migrate, manifest_paths, relocated_path, task_paths, _running, _native_created


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
        process.write_text(json.dumps({'pid': os.getpid(), 'created': 12345.0}))
        with patch.dict(sys.modules, {'psutil': None}), patch('_gbr_layout._native_created', return_value=12345.0), self.assertRaisesRegex(RuntimeError, 'Active controller'):
            migrate(self.root)
        self.assertTrue(process.exists())

    def fake_processes(self, commands, parents=()):
        class ProcessError(Exception):
            pass
        return SimpleNamespace(Error=ProcessError,
            Process=lambda: SimpleNamespace(parents=lambda: [SimpleNamespace(pid=pid) for pid in parents]),
            process_iter=lambda attrs: [SimpleNamespace(info={'pid': pid, 'cmdline': args, 'cwd': str(self.root)}) for pid, args in commands])

    def test_bootstrap_ancestor_is_not_a_second_app_but_another_launcher_is(self):
        args = ['python', str(self.root / '_97_App/run_gbr.py'), '--open']
        fake = self.fake_processes([(24092, args)], parents=[24092])
        with patch.dict(sys.modules, {'psutil': fake}), patch.object(sys, 'argv', [args[1], '--open']):
            _running(self.root)
        fake = self.fake_processes([(24092, args)], parents=[])
        with patch.dict(sys.modules, {'psutil': fake}), patch.object(sys, 'argv', [args[1], '--open']), self.assertRaisesRegex(RuntimeError, '24092'):
            _running(self.root)

    def test_unrelated_python_in_same_environment_does_not_block_startup(self):
        fake = self.fake_processes([(24092, [str(self.root / '_03_review/_generation_review/.venv/Scripts/python.exe'), '-m', 'pip', '--version'])])
        with patch.dict(sys.modules, {'psutil': fake}):
            _running(self.root)

    def test_relative_controllers_and_module_servers_still_block(self):
        for args in [['python', '_02_generation/_gbr_generation/app_jobs.py'], ['python', '-m', '_review']]:
            with self.subTest(args=args):
                fake = self.fake_processes([(24092, args)], parents=[24092])
                with patch.dict(sys.modules, {'psutil': fake}), self.assertRaisesRegex(RuntimeError, '24092'):
                    _running(self.root)

    def test_reused_saved_pid_does_not_block_before_dependencies_are_available(self):
        process = self.root / '_02_Generation/runtime/app_jobs/task/process.json'
        process.parent.mkdir(parents=True)
        process.write_text(json.dumps({'pid': os.getpid(), 'created': 0}))
        with patch.dict(sys.modules, {'psutil': None}):
            migrate(self.root)
        self.assertFalse(process.exists())

    def test_windows_native_identity_uses_creation_time_and_checks_exit_status(self):
        import ctypes
        kernel = Mock()
        kernel.OpenProcess.return_value = 123
        def exit_status(handle, code):
            code._obj.value = 259
            return 1
        kernel.GetExitCodeProcess.side_effect = exit_status
        epoch = 1792350000.25
        def times(handle, created, exited, system, user):
            value = round((epoch + 11644473600) * 10_000_000)
            created._obj.dwLowDateTime = value & 0xffffffff
            created._obj.dwHighDateTime = value >> 32
            return 1
        kernel.GetProcessTimes.side_effect = times
        with patch('_gbr_layout.os.name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True):
            self.assertAlmostEqual(_native_created(24092), epoch, places=4)
            def finished(handle, code):
                code._obj.value = 0
                return 1
            kernel.GetExitCodeProcess.side_effect = finished
            self.assertIsNone(_native_created(24092))
        self.assertEqual(kernel.CloseHandle.call_count, 2)

    def test_windows_denied_process_inspection_does_not_assume_an_active_controller_is_dead(self):
        import ctypes
        kernel = Mock(); kernel.OpenProcess.return_value = 0
        with patch('_gbr_layout.os.name', 'nt'), patch.object(ctypes, 'WinDLL', return_value=kernel, create=True), patch.object(ctypes, 'get_last_error', return_value=5, create=True):
            with self.assertRaisesRegex(RuntimeError, 'Cannot inspect'):
                _native_created(24092)

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
