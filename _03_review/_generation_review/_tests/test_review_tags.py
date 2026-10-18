"""Atomic observations, historical compatibility and lossless review transfer."""
import copy
import csv
import json
import sqlite3
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from _review.app import create_app
from _review.db import Database, SCHEMA, ANALYTICS_SQL, FEATURE_ANALYTICS_SQL
from _review import migration, features_migration, tags
from _review.exports import dataset
from _review.review_transfer import bundle, restore
from conftest import track
from test_reviewers import make_legacy

SCORES = dict(audio_quality=5, composition=4, consistency=4, realism=4)
SELECTED = [{'tag_id': 'vocal_delivery', 'polarity': 'negative'},
            {'tag_id': 'instrument_interest', 'polarity': 'positive'}]


def save(service, gid, **extra):
    if not extra.get('expected_review_id'):
        service.queues.open(service.default_reviewer_id, gid)
    return service.review(gid, SCORES, 'quick', **extra)['review_id']


def selected(db, rid):
    with closing(db.connect()) as c:
        return tags.selections(c, rid)


def test_submission_is_atomic_with_tags_reason_scores_and_revision_history(service):
    _, aid, gid = track(service)
    reason = 'Robotic phrasing.\nInstrumental parts are engaging.'
    first = save(service, gid, tags=SELECTED, reason=reason, submission_id='first')
    assert selected(service.db, first) == sorted(SELECTED, key=lambda t: t['tag_id'])
    assert service.db.one('SELECT tags_recorded,reason FROM reviews WHERE id=?', (first,)) == {'tags_recorded': 1, 'reason': reason}
    saved = service.public_track(gid, correction=True)
    assert saved['tags'] == selected(service.db, first) and saved['reason'] == reason
    second = save(service, gid, expected_review_id=first, tags=[{'tag_id': 'vocal_delivery', 'polarity': 'positive'}])
    assert len(selected(service.db, first)) == 2 and selected(service.db, second)[0]['polarity'] == 'positive'
    assert service.db.one('SELECT reason FROM reviews WHERE id=?', (second,))['reason'] == reason
    history = json.loads(service.db.one('SELECT details_json FROM review_history WHERE review_id=?', (second,))['details_json'])
    assert history['previous_tags'] == selected(service.db, first) and history['tags'] == selected(service.db, second)
    # Omitting tags in an older client preserves the latest selection.
    third = save(service, gid, expected_review_id=second)
    assert selected(service.db, third) == selected(service.db, second)
    fourth = save(service, gid, expected_review_id=third, tags=[])
    assert selected(service.db, fourth) == [] and len(selected(service.db, first)) == 2
    assert service.db.all('PRAGMA foreign_key_check') == []


@pytest.mark.parametrize('observations', [
    [{'tag_id': 'missing', 'polarity': 'positive'}],
    [{'tag_id': 'vocal_tone', 'polarity': 'neutral'}],
    [{'tag_id': 'vocal_tone', 'polarity': 'positive'}, {'tag_id': 'vocal_tone', 'polarity': 'negative'}],
    [{'tag_id': 'vocal_tone', 'polarity': 'positive', 'extra': 'no'}],
    [{'tag_id': [], 'polarity': 'positive'}],
])
def test_invalid_tags_roll_back_entire_submission(service, observations):
    _, _, gid = track(service)
    service.queues.open(service.default_reviewer_id, gid)
    before = {t: service.db.all('SELECT * FROM ' + t) for t in ['reviews', 'review_tags', 'review_history', 'publishing_selection', 'bookmarks', 'archive_operations', 'sessions']}
    with pytest.raises(ValueError):
        service.review(gid, SCORES, 'full', tags=observations, distribution=True, bookmarked=True, reason='Keep this')
    assert before == {t: service.db.all('SELECT * FROM ' + t) for t in before}


def test_retry_with_reordered_tags_is_idempotent_and_changed_tags_conflict(service):
    _, _, gid = track(service)
    first = save(service, gid, tags=SELECTED, submission_id='lost-response')
    retry = service.review(gid, SCORES, 'quick', tags=list(reversed(SELECTED)), submission_id='lost-response')
    assert retry['already_saved'] and retry['review_id'] == first
    from _review.reviewers import Conflict
    with pytest.raises(Conflict):
        service.review(gid, SCORES, 'quick', tags=[], submission_id='lost-response')
    assert service.db.one('SELECT COUNT(*) n FROM reviews')['n'] == 1


def test_vocabulary_rename_disable_and_order_preserve_historical_associations(service):
    _, _, gid = track(service)
    first = save(service, gid, tags=SELECTED)
    service.db.execute("UPDATE review_tag_definitions SET label='Expression',sort_order=20,active=0 WHERE id='vocal_delivery'")
    Database(service.db.path)  # Startup must not reseed or overwrite local vocabulary edits.
    historical = json.loads(service.db.one('SELECT tags_json FROM review_dataset WHERE id=?', (first,))['tags_json'])
    assert next(t for t in historical if t['tag_id'] == 'vocal_delivery')['label'] == 'Expression'
    assert service.public_track(gid, correction=True)['tags'] == selected(service.db, first)
    second = save(service, gid, expected_review_id=first, tags=SELECTED)
    assert selected(service.db, second) == selected(service.db, first)
    with pytest.raises(ValueError, match='inactive'):
        save(service, gid, expected_review_id=second, tags=[{'tag_id': 'vocal_delivery', 'polarity': 'positive'}])
    save(service, gid, expected_review_id=second, tags=[])
    assert service.db.one('SELECT COUNT(*) n FROM review_tags WHERE review_id=?', (first,))['n'] == 2


def test_api_returns_vocabulary_tags_and_history_and_never_requires_tags(service):
    _, _, gid = track(service)
    app = create_app(service.config.path, False)
    headers = {'X-GBR-Reviewer': service.default_reviewer_id, 'X-GBR-Request': '1'}
    with TestClient(app, base_url='http://localhost', headers=headers) as client:
        vocabulary = client.get('/api/bootstrap').json()['review_tags']
        assert len(vocabulary) == 20 and len({t['id'] for t in vocabulary}) == 20
        client.post('/api/queue/open', json={'generation_id': gid})
        result = client.post(f'/api/tracks/{gid}/review', json={**SCORES, 'mode': 'quick', 'tags': SELECTED, 'reason': 'Original reason'})
        assert result.status_code == 200, result.text
        review_id = result.json()['review_id']
        reopened = client.get('/api/queue/back').json()['track']
        assert reopened['tags'] == selected(service.db, review_id) and reopened['reason'] == 'Original reason'
        details = client.get(f'/api/tracks/{gid}/reviews').json()['reviews']
        assert len(json.loads(details[0]['tags_json'])) == 2
        assert client.get('/api/review-tags/analytics?group_by=sampler,scheduler,dit,text_encoder').status_code == 200
        assert client.get('/api/review-tags/analytics?group_by=arbitrary_sql').status_code == 400
        response = client.post(f'/api/tracks/{gid}/review', json={**SCORES, 'mode': 'quick', 'expected_review_id': review_id, 'tags': []})
        assert response.status_code == 200 and selected(service.db, response.json()['review_id']) == []
        assert client.post('/api/import/reviews', json={'payload': bundle(service.db)}).status_code == 200


def test_tag_statistics_count_recordings_not_aliases_revisions_or_missing_tags(service):
    metadata = 'title: Test\ncluster: Rock\ngenre: Punk\ndit: bf16\ntext_encoder: fp8\nencoder_cfg: 1.3\ntop_k: 40\nsampler: heun\nscheduler: cosine\nsampler_cfg: 2.2\nsampler_steps: 28\n'
    _, aid, gid = track(service, metadata=metadata)
    track(service, 'other-yaml-same-pcm', metadata=metadata+'edition: 2\n')
    first = save(service, gid, tags=SELECTED)
    save(service, gid, expected_review_id=first, tags=[SELECTED[0]])
    _, _, untagged = track(service, 'untagged', frequency=990, metadata=metadata)
    save(service, untagged)  # Historical/missing observations are not positive judgments.
    groups = ['text_encoder', 'dit', 'sampler', 'scheduler', 'encoder_cfg', 'sampler_cfg', 'top_k', 'sampler_steps', 'cluster']
    report = tags.statistics(service.db, groups)
    assert len(report['population']) == 1 and report['population'][0]['reviewed_recordings'] == 2
    assert report['population'][0]['tags_recorded_reviews'] == 1 and report['population'][0]['tagged_recordings'] == 1
    assert len(report['observations']) == 1
    row = report['observations'][0]
    assert row['tag_id'] == 'vocal_delivery' and row['recordings'] == 1 and row['recording_frequency'] == .5
    assert row['polarity'] == 'negative' and row['dit'] == 'bf16' and row['text_encoder'] == 'fp8'
    assert row['audio_quality'] == 5 and row['composition'] == 4 and row['cluster'] == 'Rock'
    assert len(tags.statistics(service.db, ['song_id', 'genre'])['population']) == 1


@pytest.mark.parametrize('format', ['json', 'csv', 'parquet'])
def test_exported_review_and_analytics_rows_include_tags(service, tmp_path, format):
    _, _, gid = track(service)
    rid = save(service, gid, tags=SELECTED, reason='Reason\nwith lines')
    for kind in ['generations', 'reviews', 'review_history', 'review_tags', 'tag_definitions', 'tag_summary']:
        output = dataset(service.db, tmp_path/(kind+'.'+format), format, kind)
        if format == 'json':
            payload = json.loads(output.read_text()); assert payload['schema_version'] == 4
            data = payload[kind]
        elif format == 'csv':
            with output.open(encoding='utf-8-sig', newline='') as f: data = list(csv.DictReader(f))
        else:
            import pyarrow.parquet as pq
            data = pq.read_table(output).to_pylist()
        assert data
        if kind == 'reviews': assert len(json.loads(data[0]['tags_json'])) == 2 and data[0]['reason'] == 'Reason\nwith lines'
        if kind == 'generations': assert len(json.loads(data[0]['review_tags_json'])) == 2
        if kind == 'review_tags': assert len(data) == 2 and data[0]['review_id'] == rid


def test_review_json_round_trip_preserves_all_revisions_tags_reasons_and_reviewer(service, tmp_path):
    _, _, gid = track(service)
    first = save(service, gid, tags=SELECTED, reason='Never overwrite\nthese words')
    save(service, gid, expected_review_id=first, tags=[SELECTED[1]], reason='A separate revision')
    payload = json.loads(dataset(service.db, tmp_path/'reviews.json', 'json', 'reviews').read_text())
    # A separate catalogue copy, with its existing audio/generation identities intact.
    destination = Database(tmp_path/'destination.sqlite')
    with closing(service.db.connect()) as source, closing(destination.connect()) as target:
        source.backup(target)
    with destination.transaction() as c:
        c.execute('DELETE FROM archive_operations'); c.execute('DELETE FROM review_history'); c.execute('DELETE FROM review_tags'); c.execute('DELETE FROM reviews'); c.execute('DELETE FROM assessments')
    before_files = sorted(str(p) for p in service.config.inputs[0].iterdir())
    assert restore(destination, payload) == {'imported': 2, 'already_present': 0}
    assert restore(destination, payload) == {'imported': 0, 'already_present': 2}
    for table, order in [('reviews', 'id'), ('review_tags', 'review_id,tag_id')]:
        assert destination.all('SELECT * FROM '+table+' ORDER BY '+order) == service.db.all('SELECT * FROM '+table+' ORDER BY '+order)
    assert destination.one('SELECT COUNT(*) n FROM review_history')['n'] == 2
    assert before_files == sorted(str(p) for p in service.config.inputs[0].iterdir())
    assert destination.all('PRAGMA foreign_key_check') == []
    conflict = copy.deepcopy(payload); conflict['reviews'][-1]['reason'] = 'Overwrite attempt'
    with pytest.raises(ValueError, match='nothing was overwritten'):
        restore(destination, conflict)
    assert destination.one('SELECT reason FROM reviews WHERE id=?', (first,))['reason'] == 'Never overwrite\nthese words'


def test_import_missing_recording_rolls_back_all_changes(service):
    _, _, gid = track(service); save(service, gid, tags=SELECTED)
    payload = bundle(service.db); payload['reviews'][0]['generation_id'] = 'missing'
    before = service.db.all('SELECT * FROM reviews')
    with pytest.raises(ValueError, match='does not match'):
        restore(service.db, payload)
    assert service.db.all('SELECT * FROM reviews') == before


def test_large_review_export_reimports_through_host_api_and_invalid_data_is_reported(service):
    _, _, gid = track(service)
    previous = None
    for i in range(3):
        extra = {'expected_review_id': previous} if previous else {}
        previous = save(service, gid, tags=SELECTED, reason='Long reason preserved. '*850, **extra)
    payload = bundle(service.db)
    assert len(json.dumps({'payload': payload})) > 100000
    app = create_app(service.config.path, False)
    headers = {'X-GBR-Reviewer': service.default_reviewer_id, 'X-GBR-Request': '1'}
    with TestClient(app, base_url='http://localhost', headers=headers) as client:
        response = client.post('/api/import/reviews', json={'payload': payload})
        assert response.status_code == 200 and response.json()['already_present'] == 3
        corrupt = copy.deepcopy(payload); corrupt['reviews'][0]['audio_quality'] = 999
        assert client.post('/api/import/reviews', json={'payload': corrupt}).status_code == 400
        # Ordinary score submissions retain their existing size limit.
        assert client.post(f'/api/tracks/{gid}/review', json={'unexpected': 'x'*100001}).status_code == 413
    remote = TestClient(app, base_url='http://localhost', client=('198.51.100.20', 1234), headers=headers)
    assert remote.post('/api/import/reviews', json={'payload': payload}).status_code in (401, 403)


def legacy_v3(path):
    make_legacy(path)
    with sqlite3.connect(path) as c:
        migration.migrate(c, ANALYTICS_SQL)
        features_migration.migrate(c, FEATURE_ANALYTICS_SQL)
        c.execute("UPDATE reviews SET reason='Legacy reason, preserved' ")


def test_existing_database_migration_is_lossless_idempotent_and_backed_up_once(tmp_path):
    path = tmp_path/'existing.sqlite'; legacy_v3(path)
    with sqlite3.connect(path) as c:
        before_reviews = list(c.execute('SELECT * FROM reviews'))
        before_history = list(c.execute('SELECT * FROM review_history'))
    db = Database(path)
    with sqlite3.connect(path) as c:
        assert [row[:-1] for row in c.execute('SELECT * FROM reviews')] == before_reviews
        assert list(c.execute('SELECT * FROM review_history')) == before_history
    assert db.one('SELECT COUNT(*) n FROM review_tags')['n'] == 0
    assert all(row['tags_recorded'] == 0 for row in db.all('SELECT tags_recorded FROM reviews'))
    assert len(db.all('SELECT * FROM review_tag_definitions')) == 20
    assert db.migration_backup and db.migration_backup.is_file()
    again = Database(path); assert again.migration_backup is None
    assert len(list((tmp_path/'backups').glob('*.sqlite'))) == 1
    assert db.all('PRAGMA foreign_key_check') == []


def test_failed_tag_migration_rolls_back_ddl_and_data(tmp_path, monkeypatch):
    path = tmp_path/'existing.sqlite'; legacy_v3(path)
    import _review.db as module
    monkeypatch.setattr(module, 'FEATURE_ANALYTICS_SQL', 'INVALID SQL')
    with pytest.raises(sqlite3.OperationalError): Database(path)
    with sqlite3.connect(path) as c:
        assert not c.execute('SELECT 1 FROM schema_version WHERE version=4').fetchone()
        assert not c.execute("SELECT 1 FROM sqlite_master WHERE name='review_tags'").fetchone()
        assert 'tags_recorded' not in {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
        assert c.execute('SELECT reason FROM reviews LIMIT 1').fetchone()[0] == 'Legacy reason, preserved'
