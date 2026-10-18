"""Technical flags are separate from ratings, including on existing review data."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from _review.triage import technical_triage

NORMAL = {'duration': 10, 'rms_dbfs': -18, 'clipped_fraction': 0, 'true_peak_dbtp_4x': -1,
          'stereo_correlation': .4, 'channel_balance_db': 0}


class TriageTests(unittest.TestCase):
    def test_clean_measurements_are_not_a_quality_rating(self):
        result = technical_triage(NORMAL, [{'type': 'repeated_texture', 'start': 3, 'end': 4}])
        self.assertEqual(result['status'], 'no_strong_flags')
        self.assertIsNone(result['human_rating'])
        self.assertNotIn('score', result)

    def test_missing_and_nonfinite_data_never_look_clean(self):
        for missing in [{}, {**NORMAL, 'rms_dbfs': None}, {**NORMAL, 'clipped_fraction': float('nan')},
                        {**NORMAL, 'duration': 0}, {**NORMAL, 'clipped_fraction': 2}]:
            self.assertEqual(technical_triage(missing)['status'], 'not_assessed')

    def test_signal_flags_keep_measured_evidence(self):
        for changes, expected in [({'rms_dbfs': -90}, 'near_silent_recording'),
                                  ({'clipped_fraction': .02}, 'frequent_full_scale_samples'),
                                  ({'true_peak_dbtp_4x': 1}, 'estimated_intersample_overs'),
                                  ({'stereo_correlation': -.8}, 'possible_mono_cancellation'),
                                  ({'channel_balance_db': 20}, 'channel_imbalance')]:
            result = technical_triage({**NORMAL, **changes})
            self.assertEqual(result['status'], 'flagged')
            self.assertIn(expected, [flag['code'] for flag in result['flags']])
            self.assertTrue(result['flags'][0]['evidence'])

    def test_interior_gap_retains_time_range_but_opening_and_ending_are_ignored(self):
        events = [{'type': 'possible_dropout', 'start': a, 'end': b} for a, b in [(0, 2), (4, 6), (8, 10)]]
        result = technical_triage(NORMAL, events)
        self.assertEqual([(f['start'], f['end']) for f in result['flags']], [(4, 6)])

    def test_streamed_audio_measurements_feed_triage(self):
        import numpy as np
        import soundfile as sf
        from _review.analysis import measure
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            x = np.sin(np.arange(44100 * 3) * 2 * np.pi * 440 / 44100) * .1
            sf.write(root / 'tone.flac', np.stack([x, x], axis=1), 44100, subtype='PCM_24')
            metrics, events, _ = measure(root / 'tone.flac', root / 'normal')
            self.assertEqual(technical_triage(metrics, events)['status'], 'no_strong_flags')
            sf.write(root / 'phase.flac', np.stack([x, -x], axis=1), 44100, subtype='PCM_24')
            metrics, events, _ = measure(root / 'phase.flac', root / 'phase')
            self.assertIn('possible_mono_cancellation', [f['code'] for f in technical_triage(metrics, events)['flags']])

    def test_analysis_store_adds_triage_without_writing_a_review(self):
        from types import SimpleNamespace
        from _review.analysis import Analyzer
        from _review.db import Database
        with tempfile.TemporaryDirectory() as temporary:
            db = Database(Path(temporary) / 'review.sqlite')
            db.execute("INSERT INTO audio(id,pcm_sha256,duration,sample_rate,channels,created_at) VALUES('a','a',1,44100,2,'now')")
            analyzer = Analyzer(SimpleNamespace(values={'fast_seconds': 10, 'quick_seconds': 20, 'standard_seconds': 50}), db, None)
            analyzer.store('a', {**NORMAL, 'duration': 1}, [], {'duration': 1, 'features': []}, 'preview.json', 'spectrum.png')
            stored = json.loads(db.one('SELECT metrics_json FROM analyses')['metrics_json'])
            self.assertEqual(stored['technical_triage']['status'], 'no_strong_flags')
            self.assertEqual(db.one('SELECT COUNT(*) AS n FROM reviews')['n'], 0)
            self.assertEqual(db.one("SELECT analysis_state FROM audio WHERE id='a'")['analysis_state'], 'ready')


if __name__ == '__main__':
    unittest.main()
