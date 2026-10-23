from contextlib import closing
import csv
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_02_generation'))
sys.path.insert(0, str(ROOT / '_98_database'))
sys.path.insert(0, str(ROOT / '_03_review' / '_generation_review'))
from _gbr_database.migration import migrate
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.planner import BatchRejected, preflight, prepare_batch, verify_plan
from _review.db import Database

PRESET = {'preset': {'model': 'test-checkpoint', 'dit': 'fp32', 'text_encoder': 'bf16',
                    'encoder_cfg': 1.5, 'top_k': 50, 'sampler': 'euler', 'scheduler': 'test-flow',
                    'sampler_cfg': 1.7, 'sampler_steps': 30}}


def write_csv(path, rows):
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.built = self.root / '_built'
        (self.built / '_yaml').mkdir(parents=True)
        self.track_rows = [dict(song_id='1', filename='alpha.yaml', song_name='Alpha', album_name='Demo', album_position='1'),
                           dict(song_id='2', filename='beta.yaml', song_name='Beta', album_name='Demo', album_position='2')]
        write_csv(self.built / 'track_list.csv', self.track_rows)
        write_csv(self.built / 'variant_list.csv', [dict(song_id='1', variant_id='2', filename='alpha-punk.yaml',
                  song_name='Alpha Punk', album_name='Variants', album_position='1')])
        for title, song, variant in [('alpha', 1, 1), ('beta', 2, 1), ('alpha-punk', 1, 2)]:
            raw = (f'song_id: {song}\nvariant_id: {variant}\ntitle: {title}\r\ncluster: Rock\nstyle: Punk\n'
                   'encoder_seed: 9007199254740993 # retain spelling\r\ncaption: |\n  A caption\nlyrics: |\r\n  Lyrics\r\n').encode()
            (self.built / '_yaml' / (title + '.yaml')).write_bytes(raw)
        self.styles_path = self.root / 'styles.csv'
        write_csv(self.styles_path, [dict(ID='punk-a', cluster='Rock', style_primary='Punk', template='templates/punk-a.txt'),
                    dict(ID='jazz-a', cluster='Jazz', style_primary='Swing', template='templates/jazz-a.txt'),
                    dict(ID='jazz-b', cluster='Jazz', style_primary='Swing', template='templates/jazz-b.txt')])
        (self.root / 'templates').mkdir()
        for filename in ['punk-a', 'jazz-a', 'jazz-b']:
            (self.root / 'templates' / (filename + '.txt')).write_text('New template\nWith details\n', encoding='utf-8')
        self.database = self.root / 'shared.sqlite3'
        review = self.root / 'review.sqlite'
        Database(review)
        legacy = self.root / 'legacy.sqlite3'
        with closing(sqlite3.connect(legacy)) as conn:
            conn.executescript('''CREATE TABLE jobs(job_id TEXT PRIMARY KEY,output_stem TEXT,params_json TEXT,status TEXT);
                CREATE TABLE counters(title_key TEXT PRIMARY KEY,last_value INTEGER);
                CREATE TABLE timings(timing_id TEXT PRIMARY KEY,job_id TEXT,worker_id TEXT,gpu_name TEXT,stage TEXT,
                    success INTEGER,duration_seconds REAL,billable INTEGER,estimated_cost REAL,cost_currency TEXT);''')
        migrate(legacy, review, self.database)

    def check(self, selected=None, spec=None):
        catalogue = Catalogue(self.built)
        return preflight(selected or catalogue.select(all_inputs=True), spec or PRESET, Styles(self.styles_path), self.root)

    def prepare(self, checked, **kwargs):
        return prepare_batch(checked, PRESET, self.root / 'runtime', self.database, input_root=self.built, **kwargs)

    def test_selection_supports_single_multiple_variant_album_and_order(self):
        catalogue = Catalogue(self.built)
        self.assertEqual([i.path.stem for i in catalogue.select(songs=['beta', 'alpha-punk'])], ['beta', 'alpha-punk'])
        self.assertEqual({i.path.stem for i in catalogue.select(songs=['1'])}, {'alpha', 'alpha-punk'})
        self.assertEqual([i.path.stem for i in catalogue.select(albums=['Demo'], order='album')], ['alpha', 'beta'])
        self.assertEqual([i.path.stem for i in catalogue.select(all_inputs=True, order='filename')], ['alpha-punk', 'alpha', 'beta'])

    def test_plan_freezes_seeds_source_copies_and_parameter_provenance(self):
        original = {p: p.read_bytes() for p in self.built.rglob('*') if p.is_file()}
        checked = self.check()
        self.assertTrue(all(item.ready for item in checked))
        path = self.prepare(checked)
        manifest = verify_plan(path)
        self.assertEqual(len(manifest['runs']), 3)
        for run in manifest['runs']:
            self.assertEqual(run['effective']['encoder_seed'], '9007199254740993')
            self.assertTrue(run['effective']['sampler_seed'].isdigit())
            self.assertEqual(run['provenance']['sampler_cfg'], 'preset')
            self.assertIn(b'encoder_seed: 9007199254740993 # retain spelling', (path.parent / run['request_yaml']).read_bytes())
            original_path = next(p for p in original if p.stem == run['title'])
            self.assertEqual((path.parent / run['snapshot']).read_bytes(), original[original_path])
        self.assertEqual(verify_plan(path), manifest)
        self.assertEqual(original, {p: p.read_bytes() for p in original})
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM generation_runs WHERE status="prepared"').fetchone()[0], 3)
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM generation_attempts').fetchone()[0], 0)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(), [])

    def test_cancel_and_drop_policies_record_exclusions(self):
        path = self.built / '_yaml' / 'beta.yaml'
        path.write_bytes(path.read_bytes().replace(b'song_id: 2', b'song_id: 999'))
        checked = self.check()
        with self.assertRaises(BatchRejected):
            self.prepare(checked)
        self.assertFalse((self.root / 'runtime').exists())
        manifest = verify_plan(self.prepare(checked, error_policy='drop'))
        self.assertEqual(len(manifest['runs']), 2)
        self.assertEqual([r['status'] for r in manifest['inputs']].count('excluded'), 1)
        with closing(sqlite3.connect(self.database)) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM generation_batch_inputs WHERE status='excluded'").fetchone()[0], 1)

    def test_source_edit_after_preflight_is_rejected(self):
        checked = self.check()
        checked[0].item.path.write_bytes(checked[0].document.raw + b'\n# changed\n')
        with self.assertRaisesRegex(ValueError, 'changed after preflight'):
            self.prepare(checked)
        self.assertFalse((self.root / 'runtime').exists())

    def test_resume_uses_frozen_copies_after_originals_change(self):
        checked = self.check()
        path = self.prepare(checked)
        checked[0].item.path.write_bytes(b'changed original')
        self.assertEqual(len(verify_plan(path)['runs']), 3)
        run = verify_plan(path)['runs'][0]
        (path.parent / run['request_yaml']).write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'changed'):
            verify_plan(path)

    def test_manifest_tampering_is_rejected_against_database_hash(self):
        path = self.prepare(self.check())
        path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'manifest'):
            verify_plan(path)

    def test_workspace_inside_built_is_refused(self):
        with self.assertRaisesRegex(ValueError, 'source writing directory'):
            prepare_batch(self.check(), PRESET, self.built / 'runtime', self.database, input_root=self.built)
        self.assertFalse((self.built / 'runtime').exists())

    def test_duplicate_ids_missing_files_and_uncatalogued_files_are_flagged(self):
        self.track_rows[1]['song_id'] = '1'
        self.track_rows.append(dict(song_id='3', filename='missing.yaml', song_name='Missing', album_name='', album_position=''))
        write_csv(self.built / 'track_list.csv', self.track_rows)
        (self.built / '_yaml' / 'unlisted.yaml').write_bytes(b'title: unlisted\nlyrics: |\n  words\n')
        codes = {issue.code for record in self.check() for issue in record.issues}
        self.assertTrue({'duplicate_catalogue_id', 'missing_file', 'uncatalogued_file'}.issubset(codes))

    def test_ambiguous_style_templates_require_an_explicit_id(self):
        path = self.built / '_yaml' / 'alpha.yaml'
        path.write_bytes(path.read_bytes().replace(b'Rock', b'Jazz').replace(b'Punk', b'Swing').replace(b'  A caption\n', b''))
        selected = Catalogue(self.built).select(files=['alpha.yaml'])
        self.assertFalse(self.check(selected)[0].ready)
        spec = {**PRESET, 'template_id': 'jazz-a'}
        record = self.check(selected, spec)[0]
        self.assertTrue(record.ready, record.summary())
        self.assertEqual(record.template['id'], 'jazz-a')
        manifest_path = prepare_batch([record], spec, self.root / 'runtime', self.database, input_root=self.built)
        manifest = verify_plan(manifest_path)
        self.assertIn(b'New template', (manifest_path.parent / manifest['runs'][0]['request_yaml']).read_bytes())

    def test_idempotency_key_cannot_prepare_a_duplicate_rerun(self):
        selected = Catalogue(self.built).select(files=['alpha.yaml'])
        spec = {**PRESET, 'request_id': 'review-rerun-1', 'rerun': {'sampler_seed': '123'}}
        record = self.check(selected, spec)[0]
        path = prepare_batch([record], spec, self.root / 'runtime', self.database, input_root=self.built)
        self.assertEqual(verify_plan(path)['runs'][0]['effective']['sampler_seed'], '123')
        with self.assertRaisesRegex(ValueError, 'already exists'):
            prepare_batch([record], spec, self.root / 'runtime', self.database, input_root=self.built)

    def test_hybrid_backend_and_cache_policy_are_frozen_without_worker_models(self):
        selected = Catalogue(self.built).select(files=['alpha.yaml'])
        spec = {**PRESET, 'execution': {'encoding_cache': 'disabled',
                'conditioning': {'backend': 'runpod_serverless', 'address': 'test-endpoint'},
                'diffusion': {'backend': 'local'}}}
        record = self.check(selected, spec)[0]
        path = prepare_batch([record], spec, self.root / 'runtime', self.database, input_root=self.built)
        execution = verify_plan(path)['runs'][0]['execution']
        self.assertEqual(execution['encoding_cache'], 'disabled')
        self.assertEqual(execution['conditioning']['backend'], 'runpod_serverless')
        self.assertEqual(execution['diffusion']['backend'], 'local')
        spec['execution']['diffusion']['model'] = 'must-not-be-worker-config'
        self.assertFalse(self.check(selected, spec)[0].ready)


if __name__ == '__main__':
    unittest.main()
