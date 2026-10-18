"""Review decisions and queue counts use the same records and directory scope."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from review.app import create_app


class ReviewFlagsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = create_app(self.root / 'config.json', start_worker=False)
        self.service = self.app.state.service
        self.db = self.service.db
        self.rid = self.service.default_reviewer_id
        self.client = TestClient(self.app, base_url='http://localhost')
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {'X-GBR-Request': '1', 'X-GBR-Reviewer': self.rid}
        self.service.config.values['input_directories'] = [str(self.root / 'inputs')]
        self.gids = []
        for index, subdir in enumerate(['album-a', 'album-a', 'album-b']):
            path = self.root / 'inputs' / subdir / f'song-{index}.flac'
            path.parent.mkdir(parents=True, exist_ok=True)
            wave = np.full((441, 2), index * .05, dtype=np.float32)
            sf.write(path, wave, 44100, subtype='PCM_24')
            path.with_suffix('.yaml').write_text(f'title: song-{index}\ncluster: Punk\nlyrics: A line\n')
            self.service.indexer.index(path)
            self.gids.append(self.db.one('SELECT id FROM generations WHERE original_filename=?', (path.name,))['id'])

    def review(self, gid, scores=None, **flags):
        self.service.queues.open(self.rid, gid)
        body = dict(audio_quality=5, composition=5, consistency=5, realism=5, mode='full', **flags)
        body.update(scores or {})
        response = self.client.post('/api/tracks/' + gid + '/review', headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_any_dimension_at_three_or_lower_rejects_and_records_snapshot(self):
        gid = self.gids[0]
        self.review(gid, {'composition': 3}, distribution=False, bookmarked=False, rejected=False, submission_id='one')
        flags = self.service.track_flags(gid, self.rid)
        self.assertTrue(flags['rejected'])
        snapshot = json.loads(self.db.one('SELECT flags_json FROM reviews')['flags_json'])
        self.assertTrue(snapshot['automatic_rejection'])
        self.assertEqual(self.db.one('SELECT bucket FROM reviews')['bucket'], 5)
        # Retrying a lost response compares the requested toggle, not its derived rejection.
        body = dict(audio_quality=5, composition=3, consistency=5, realism=5, mode='full', distribution=False,
                    bookmarked=False, rejected=False, submission_id='one')
        retry = self.client.post('/api/tracks/' + gid + '/review', headers=self.headers, json=body)
        self.assertTrue(retry.json()['already_saved'], retry.text)
        self.assertEqual(self.db.one('SELECT COUNT(*) n FROM reviews')['n'], 1)

    def test_publish_or_bookmark_protects_low_ratings_and_both_can_be_set(self):
        for gid, flags in zip(self.gids, [{'distribution': True}, {'bookmarked': True}, {'distribution': True, 'bookmarked': True}]):
            self.review(gid, {'audio_quality': 0}, **flags)
            current = self.service.track_flags(gid, self.rid)
            self.assertFalse(current['rejected'])
            self.assertTrue(current['distribution'] or current['bookmarked'])

    def test_manual_reject_before_review_can_be_cleared_by_bookmark(self):
        gid = self.gids[0]
        rejected = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers, json={'rejected': True})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertTrue(rejected.json()['rejected'])
        bookmarked = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers, json={'bookmarked': True})
        self.assertTrue(bookmarked.json()['bookmarked'])
        self.assertFalse(bookmarked.json()['rejected'])

    def test_conflicting_or_stale_flags_are_refused_without_changes(self):
        gid = self.gids[0]
        self.service.set_flags(gid, self.rid, distribution=True)
        before = self.service.track_flags(gid, self.rid)
        conflict = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers, json={'rejected': True})
        self.assertEqual(conflict.status_code, 400)
        stale = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers,
                                 json={'distribution': False, 'rejected': True, 'expected_publishing_status': 'not_selected'})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.service.track_flags(gid, self.rid), before)

    def test_counts_follow_subdirectory_review_skip_and_reservation(self):
        self.assertEqual(self.client.get('/api/queue/counts', headers=self.headers).json()['remaining'], 3)
        directory = str(self.root / 'inputs/album-a')
        count = lambda: self.client.get('/api/queue/counts', headers=self.headers, params={'directory': directory}).json()
        self.assertEqual(count()['remaining'], 2)
        self.review(self.gids[0])
        self.assertEqual((count()['remaining'], count()['reviewed']), (1, 1))
        self.service.queues.open(self.rid, self.gids[1])
        self.service.skip(self.gids[1], self.rid)
        self.assertEqual((count()['remaining'], count()['skipped']), (1, 1))
        self.assertEqual(count()['in_queue'], 0)
        self.assertEqual(self.client.get('/api/queue/counts', headers=self.headers, params={'directory': str(self.root / 'outside')}).status_code, 400)

    def test_removing_protection_from_a_saved_low_rating_restores_rejection(self):
        gid = self.gids[0]
        self.review(gid, {'consistency': 3}, bookmarked=True)
        self.assertFalse(self.service.track_flags(gid, self.rid)['rejected'])
        response = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers, json={'bookmarked': False})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['rejected'])
        protected = self.client.post('/api/tracks/' + gid + '/flags', headers=self.headers, json={'distribution': True})
        self.assertFalse(protected.json()['rejected'])


if __name__ == '__main__':
    unittest.main()
