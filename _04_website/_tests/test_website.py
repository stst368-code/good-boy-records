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
        self.assertFalse((out / 'data/analytics/cost-analysis.json').exists())
        self.assertFalse((out / 'data/analytics/summary.json').exists())
        self.assertIn('data-cost-export="absent"', (out / 'analytics/index.html').read_text())
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


class CatalogueTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.media = self.base / 'curated'
        self.media.mkdir()
        self.output = self.base / 'catalogue.json'

    def refresh(self):
        return subprocess.run([sys.executable, str(ROOT / '_tools/build_catalogue.py'), '--showcase', str(self.media), '--output', str(self.output)], capture_output=True, text=True, timeout=30)

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


if __name__ == '__main__':
    unittest.main()
