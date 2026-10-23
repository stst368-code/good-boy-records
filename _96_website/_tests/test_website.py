"""Public website builds preserve identities, originals and private app data."""
from hashlib import sha256
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('website_build', ROOT / 'build.py')
website = importlib.util.module_from_spec(spec)
spec.loader.exec_module(website)


def fingerprints(root):
    return {p.relative_to(root).as_posix(): sha256(p.read_bytes()).hexdigest() for p in root.rglob('*') if p.is_file()}


class BuildTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root = self.base / 'website'
        shutil.copytree(ROOT, self.root, ignore=shutil.ignore_patterns('_site', '_showcase', '__pycache__', '.gbr-site-*'))

    def test_fresh_build_preserves_public_catalogue_and_excludes_private_files(self):
        (self.root / 'config.json').write_text('{"secret":"private"}')
        (self.root / 'gbr.sqlite3').write_bytes(b'database')
        (self.root / '_showcase').mkdir()
        (self.root / '_showcase/song.flac').write_bytes(b'audio')
        before = fingerprints(self.root / '_data')
        result = website.build(self.root)
        out = self.root / '_site'
        source = json.loads((self.root / '_data/catalogue.json').read_text())
        self.assertEqual(result['tracks'], source['trackCount'])
        self.assertEqual((out / 'catalogue.json').read_bytes(), (self.root / '_data/catalogue.json').read_bytes())
        self.assertEqual(before, fingerprints(self.root / '_data'))
        self.assertFalse(any(p.endswith(('.py', '.flac', '.yaml', '.bat', '.sqlite3')) or 'config' in p for p in fingerprints(out)))
        for name in ['cost-analysis.json','summary.json']:
            self.assertEqual((out / 'data/analytics' / name).read_bytes(), (self.root / '_data/_analytics' / name).read_bytes())
        self.assertIn('data-cost-export="available"', (out / 'analytics/index.html').read_text())
        self.assertIn('data-platform-export="absent"', (out / 'analytics/index.html').read_text())
        for document in json.loads((out / 'knowledge-manifest.json').read_text())['documents']:
            self.assertTrue((out / document['path']).is_file())

    def test_repeat_build_is_identical_and_navigation_is_inserted_once(self):
        website.build(self.root)
        first = fingerprints(self.root / '_site')
        website.build(self.root)
        self.assertEqual(first, fingerprints(self.root / '_site'))
        self.assertEqual((self.root / '_site/index.html').read_text().count('id="gbrAnalyticsButton"'), 1)
        self.assertNotIn('id="gbrAnalyticsButton"', (self.root / '_templates/landscape.html').read_text())

    def test_bad_catalogue_keeps_previous_completed_build(self):
        website.build(self.root)
        before = fingerprints(self.root / '_site')
        (self.root / '_data/catalogue.json').write_text('{"format":"gbr-sonic-catalogue-v1","trackCount":2,"tracks":[]}')
        with self.assertRaisesRegex(ValueError, 'trackCount'):
            website.build(self.root)
        self.assertEqual(before, fingerprints(self.root / '_site'))
        self.assertEqual(list(self.root.glob('.gbr-site-*')), [])

    def test_output_cannot_replace_sources_or_unrelated_content(self):
        private = self.base / 'unrelated'
        private.mkdir()
        (private / 'keep.txt').write_text('keep')
        for output in [self.root, self.base, self.root / '_data', self.root / '_showcase', private]:
            with self.subTest(output=output), self.assertRaises(ValueError):
                website.build(self.root, output)
        self.assertEqual((private / 'keep.txt').read_text(), 'keep')

    def test_build_from_another_working_directory(self):
        result = subprocess.run([sys.executable, str(self.root / 'build.py')], cwd=self.base, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / '_site/index.html').is_file())

    def test_optional_platform_projection_is_staged_and_invalid_refresh_keeps_completed_build(self):
        projection = {'format':'gbr-platform-analytics-v1','review_model':'four-metric-0-5',
                      'generated_at':'2026-10-19','count':0,'generations':[]}
        path = self.root / '_data/_analytics/platform.json'
        path.write_text(json.dumps(projection))
        result = website.build(self.root)
        self.assertEqual(result['platform_recordings'],0)
        self.assertIn('data-platform-export="available"',(self.root/'_site/analytics/index.html').read_text())
        before = fingerprints(self.root / '_site')
        projection['count']=1
        path.write_text(json.dumps(projection))
        with self.assertRaisesRegex(ValueError,'count'):website.build(self.root)
        self.assertEqual(before,fingerprints(self.root / '_site'))


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.media = self.base / 'curated'
        self.media.mkdir()
        self.output = self.base / 'catalogue.json'

    def refresh(self, *extra):
        return subprocess.run([sys.executable, str(ROOT / '_tools/build_catalogue.py'), '--showcase', str(self.media), '--output', str(self.output), *map(str,extra)], capture_output=True, text=True, timeout=30)

    def test_flat_and_legacy_yamls_keep_distinct_ids_share_tokens_sidecars_and_source_bytes(self):
        for name in ['one', 'two', 'three']:
            (self.media / (name + '.yaml')).write_bytes(b'\xef\xbb\xbf# keep\r\nsong_id: 13\r\nvariant_id: 2\r\ntitle: same-song\r\ncluster: Rock\r\nstyle_primary: Punk\r\nexplicit: no\r\nlyrics: |\r\n  first line\r\n  second line\r\n')
            (self.media / (name + '.mp3')).write_bytes(b'audio')
        for name in ['one', 'two']:
            (self.media / (name + '.lyrics.json')).write_text(json.dumps({'lines': [{'text': name}]}))
        legacy = self.media / 'legacy/legacy-punk'
        legacy.mkdir(parents=True)
        (legacy.parent / 'legacy.yaml').write_text('title: legacy\nstory: original\n')
        (legacy / 'legacy-punk.yaml').write_text('title: legacy\nversion: punk\nlyrics: |\n  legacy lyric\n')
        (legacy / 'legacy-punk.mp3').write_bytes(b'audio')
        before = {p: p.read_bytes() for p in self.media.rglob('*.yaml')}
        result = self.refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(self.output.read_text())
        self.assertEqual(data['trackCount'], 4)
        self.assertEqual(len({t['id'] for t in data['tracks']}), 4)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        rows = {t['yamlUrl']: t for t in data['tracks']}
        self.assertEqual(rows['showcase/one.yaml']['genre']['cluster'], 'Rock')
        self.assertEqual(rows['showcase/one.yaml']['lyrics_url'], 'showcase/one.lyrics.json')
        self.assertEqual(rows['showcase/two.yaml']['lyrics_url'], 'showcase/two.lyrics.json')
        self.assertIsNone(rows['showcase/three.yaml']['lyrics_url'])
        original = {t['yamlUrl']: (t['id'], t['shareToken']) for t in data['tracks']}
        path = self.media / 'one.yaml'
        path.write_bytes(path.read_bytes().replace(b'style_primary: Punk', b'style_primary: Updated'))
        hidden = {'id': 'easter-01-keep', 'audio': {'sources': {'mp3': 'showcase/easter/keep.mp3'}}}
        data['easter'] = {'enabled': True, 'count': 1, 'tracks': [hidden]}
        self.output.write_text(json.dumps(data))
        result = self.refresh()
        self.assertEqual(result.returncode, 0, result.stderr)
        updated = json.loads(self.output.read_text())
        self.assertEqual({t['yamlUrl']: (t['id'], t['shareToken']) for t in updated['tracks']}, original)
        self.assertEqual(updated['easter']['tracks'], [hidden])

    def test_empty_or_missing_curated_collection_does_not_erase_catalogue(self):
        shutil.copyfile(ROOT / '_data/catalogue.json', self.output)
        before = self.output.read_bytes()
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assertEqual(self.output.read_bytes(), before)
        self.media.rmdir()
        self.assertNotEqual(self.refresh().returncode, 0)
        self.assertEqual(self.output.read_bytes(), before)

    def test_native_bundle_snapshot_is_not_a_second_recording_and_shared_styles_set_order(self):
        bundle=self.media/'song-001'
        bundle.mkdir()
        yaml=b'song_id: 13\nvariant_id: 2\ntitle: A Song\ncluster: Native Cluster\nstyle: Native Style\nexplicit: no\ngeneration_datetime: 2610190201\nencoder_seed: 18446744073709551615\nlyrics: |\n  keep this\n'
        (bundle/'song-001.yaml').write_bytes(yaml)
        (bundle/'source.yaml').write_bytes(yaml)
        (bundle/'song-001.flac').write_bytes(b'audio')
        styles=self.base/'styles.csv'
        styles.write_text('ID,cluster,style_primary,template_source\ntemplate,Native Cluster,Native Style,custom\n')
        result=self.refresh('--styles',styles)
        self.assertEqual(result.returncode,0,result.stderr)
        tracks=json.loads(self.output.read_text())['tracks']
        self.assertEqual(len(tracks),1)
        self.assertEqual(tracks[0]['genre']['parent_order'],0)
        self.assertEqual(tracks[0]['genre']['taxonomy_order'],0)
        self.assertEqual(tracks[0]['displayTitle'],'A Song')
        self.assertFalse(tracks[0]['explicit'])
        self.assertEqual(tracks[0]['generation']['encoderSeed'],'18446744073709551615')
        self.assertEqual(tracks[0]['generation_datetime'],2610190201)
        self.assertEqual((bundle/'source.yaml').read_bytes(),yaml)

    def test_publishing_manifest_selects_exact_output_yaml_and_fails_without_erasing(self):
        for name in ['selected','other']:
            (self.media/(name+'.yaml')).write_text('title: '+name+'\nlyrics: |\n  verse\n')
            (self.media/(name+'.mp3')).write_bytes(b'audio')
        manifest=self.base/'publishing.json'
        row={'generation_id':'g1','yaml_sha256':sha256((self.media/'selected.yaml').read_bytes()).hexdigest(),'publishing_status':'selected'}
        manifest.write_text(json.dumps({'schema_version':1,'tracks':[row]}))
        result=self.refresh('--publishing-manifest',manifest)
        self.assertEqual(result.returncode,0,result.stderr)
        data=json.loads(self.output.read_text())
        self.assertEqual(data['trackCount'],1)
        self.assertEqual(data['tracks'][0]['generation_id'],'g1')
        before=self.output.read_bytes()
        manifest.write_text(json.dumps({'schema_version':1,'tracks':[{**row,'yaml_sha256':'absent'}]}))
        self.assertNotEqual(self.refresh('--publishing-manifest',manifest).returncode,0)
        self.assertEqual(self.output.read_bytes(),before)


if __name__ == '__main__':
    unittest.main()
