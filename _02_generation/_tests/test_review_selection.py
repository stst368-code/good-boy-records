from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_02_generation'))
sys.path.insert(0, str(ROOT / '_03_review' / '_generation_review'))
from _gbr_generation.catalogue import Input
from _gbr_generation.review_selection import select_by_rating
from _review.db import Database


class ReviewSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'review.sqlite'
        Database(self.path)
        self.inputs = [Input(self.root / 'alpha.yaml', '1', '1', {'song_name': 'Alpha'}),
                       Input(self.root / 'beta.yaml', '2', '1', {'song_name': 'Beta'})]
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            self.simon = conn.execute('SELECT id FROM reviewers LIMIT 1').fetchone()[0]
            conn.execute("INSERT INTO reviewers VALUES('other','Other','other','then')")
            conn.execute("INSERT INTO songs VALUES('song','alpha','Punk','Rock')")
            conn.execute("INSERT INTO audio(id,pcm_sha256,duration,sample_rate,channels,created_at) VALUES('audio','hash',120,44100,2,'then')")
            for n in range(2):
                conn.execute("INSERT INTO generations(id,audio_id,song_id,metadata_key,original_filename,original_directory,created_at) VALUES(?,'audio','song',?,?,'input','then')", (f'g{n}', f'm{n}', f'alpha-{n}.flac'))
                conn.execute("INSERT INTO generation_metadata(generation_id,parsed_json,embedded_json,metadata_status) VALUES(?,?,'{}','valid')", (f'g{n}', json.dumps({'song_id': 1, 'variant_id': 1, 'cluster': 'Rock', 'style': 'Punk'})))
            for reviewer in [self.simon, 'other']:
                conn.execute('INSERT INTO assessments VALUES(?,?,?,?)', ('a-' + reviewer, 'audio', reviewer, 'then'))
            for n, (reviewer, score, assessment_revision) in enumerate([(self.simon, 1, 1), (self.simon, 4, 2), ('other', 2, 1)], 1):
                conn.execute('''INSERT INTO reviews(id,generation_id,audio_id,audio_quality,composition,consistency,realism,
                  average,bucket,created_at,mode,revision,session_id,listening_json,reviewer_id,assessment_id,assessment_revision)
                  VALUES(?,'g0','audio',?,?,?,?,?,?,'then','full',?,'main','{}',?,?,?)''',
                  (f'r{n}', score, score, score, score, score, score, n, reviewer, 'a-' + reviewer, assessment_revision))
            conn.commit()

    def test_latest_reviews_are_deduplicated_across_generation_metadata(self):
        chosen, evidence = select_by_rating(self.inputs, self.path, 3)
        self.assertEqual([i.song_id for i in chosen], ['1'])
        self.assertEqual(evidence['evidence'][0]['mean_latest_rating'], 3)
        self.assertEqual(evidence['evidence'][0]['review_ids'], ['r2', 'r3'])

    def test_reviewer_and_style_scope_are_explicit(self):
        self.assertEqual(select_by_rating(self.inputs, self.path, 3, reviewer_id=self.simon)[0], [])
        self.assertEqual(len(select_by_rating(self.inputs, self.path, 3, reviewer_id='other', cluster='Rock', style='Punk')[0]), 1)
        self.assertEqual(select_by_rating(self.inputs, self.path, 3, style='Swing')[0], [])
        with self.assertRaisesRegex(ValueError, 'Unknown reviewer'):
            select_by_rating(self.inputs, self.path, 3, reviewer_id='missing')

    def test_incomplete_explicit_identity_does_not_fall_back_to_a_title_guess(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute('UPDATE generation_metadata SET parsed_json=?', (json.dumps({'song_id': 1}),))
            conn.commit()
        self.assertEqual(select_by_rating(self.inputs, self.path, 5)[0], [])

    def test_rating_options_accept_current_and_legacy_style_metadata(self):
        for fields, cluster, style in [({'style_primary':' Punk '},'Cluster not supplied','Punk'),
                                       ({'genre':'Chiptune','cluster':'Electronic'},'Electronic','Chiptune'),
                                       ({'style':'','style_primary':'','intended_genre':'Swing'},'Cluster not supplied','Swing')]:
            with self.subTest(fields=fields):
                with closing(sqlite3.connect(self.path)) as conn:
                    conn.execute('UPDATE generation_metadata SET parsed_json=?', (json.dumps({'song_id':1,'variant_id':1,**fields}),))
                    conn.commit()
                chosen, evidence = select_by_rating(self.inputs,self.path,3,cluster=cluster,style=style)
                self.assertEqual([i.song_id for i in chosen],['1'])
                self.assertEqual(evidence['evidence'][0]['review_ids'],['r2','r3'])

    def test_ambiguous_legacy_titles_are_skipped(self):
        with closing(sqlite3.connect(self.path)) as conn:
            conn.execute("UPDATE generation_metadata SET parsed_json='{}'")
            conn.commit()
        inputs = self.inputs + [Input(self.root / 'other' / 'alpha.yaml', '3', '1', {})]
        chosen, evidence = select_by_rating(inputs, self.path, 5)
        self.assertEqual(chosen, [])
        self.assertEqual(evidence['ambiguous_review_generations_skipped'], ['g0', 'g1'])


if __name__ == '__main__':
    unittest.main()
