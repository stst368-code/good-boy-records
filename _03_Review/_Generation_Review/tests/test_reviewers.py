from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sqlite3
import threading

from fastapi.testclient import TestClient
import pytest

from review.app import create_app
from review.db import Database, SCHEMA
from review.exports import dataset
from conftest import track

WRITE = {"X-GBR-Request": "1"}
BODY = {"audio_quality": 5, "composition": 4, "consistency": 3, "realism": 5, "mode": "fast"}


def select(client, name):
    response = client.post('/api/reviewer/select', json={'name': name}, headers=WRITE)
    assert response.status_code == 200, response.text
    rid = response.json()['reviewer']['id']
    client.headers['X-GBR-Reviewer'] = rid
    return rid


def seed(s, count=8):
    return [track(s, f'song-{i}', frequency=220 + i * 31)[2] for i in range(count)]


@pytest.fixture
def clients(service):
    app = create_app(service.config.path, False)
    with TestClient(app, base_url='http://localhost') as a, TestClient(app, base_url='http://localhost') as b:
        alice = select(a, 'Simon')
        bob = select(b, 'Alex')
        yield app.state.service, a, b, alice, bob


def current(client):
    response = client.get('/api/queue/next?include_preview=true')
    assert response.status_code == 200, response.text
    return response.json()


def save(client, gid, **extra):
    return client.post(f'/api/tracks/{gid}/review', json={**BODY, **extra}, headers=WRITE)


def test_identity_required_and_names_are_trimmed_casefolded(clients):
    s, a, b, simon, _ = clients
    blank = TestClient(create_app(s.config.path, False), base_url='http://localhost')
    assert blank.get('/api/bootstrap').json()['reviewer'] is None
    assert blank.get('/api/queue/next').status_code == 428
    for name in ('', '   ', '\t\n'):
        assert blank.post('/api/reviewer/select', json={'name': name}, headers=WRITE).status_code == 400
    assert select(blank, '  sImOn  ') == simon
    assert blank.get('/api/bootstrap').json()['reviewer']['display_name'] == 'Simon'
    assert len(s.queues.names()) == 2
    assert select(blank, 'Straße') == select(blank, 'STRASSE')


def test_concurrent_allocation_is_exclusive_and_server_persisted(clients):
    s, a, b, ar, br = clients
    seed(s, 10)
    barrier = threading.Barrier(2)
    def allocate(client):
        barrier.wait()
        return current(client)['track']['audio_id']
    with ThreadPoolExecutor(max_workers=2) as pool:
        one, two = list(pool.map(allocate, (a, b)))
    assert one != two
    rows = s.db.all('SELECT * FROM queue_reservations')
    assert len(rows) == 6
    assert len({r['audio_id'] for r in rows}) == 6
    assert {r['reviewer_id'] for r in rows} == {ar, br}
    assert current(a)['track']['audio_id'] == one
    assert current(b)['track']['audio_id'] == two


def test_duplicate_audio_and_yaml_cannot_bypass_reservation(clients):
    s, a, b, _, _ = clients
    _, aid, g1 = track(s, 'one', metadata='title: Duplicate\ngenre: Chiptune\nsampler_steps: 20\n')
    _, same, g2 = track(s, 'two', metadata='title: Duplicate\ngenre: Chiptune\nsampler_steps: 40\n')
    assert aid == same and g1 != g2
    assigned = current(a)['track']
    assert current(b)['reason'] == 'reserved'
    assert save(b, g2).status_code == 409
    assert save(a, assigned['id']).status_code == 200
    assert current(b)['reason'] == 'first_pass_complete'
    assert save(b, g1).json()['code'] == 'already_reviewed'
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 1


def test_prefetch_uses_only_the_selected_reviewers_reserved_next(clients):
    s, a, b, ar, br = clients
    seed(s)
    ga, gb = current(a)['track']['id'], current(b)['track']['id']
    before = s.db.all('SELECT * FROM sessions ORDER BY id')
    ra = a.post('/api/queue/prefetch', json={'current_id': ga, 'mode': 'full'}, headers=WRITE).json()
    rb = b.post('/api/queue/prefetch', json={'current_id': gb, 'mode': 'full'}, headers=WRITE).json()
    assert ra['prepared'] and rb['prepared'] and ra['track_id'] != rb['track_id']
    assert s.db.one('SELECT reviewer_id FROM queue_reservations WHERE generation_id=?', (ra['track_id'],))['reviewer_id'] == ar
    assert s.db.one('SELECT reviewer_id FROM queue_reservations WHERE generation_id=?', (rb['track_id'],))['reviewer_id'] == br
    assert s.db.all('SELECT * FROM sessions ORDER BY id') == before
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 0
    assert save(a, ga).status_code == 200
    assert current(a)['track']['id'] == ra['track_id']


def test_skip_back_and_corrections_do_not_change_other_reviewer(clients):
    s, a, b, ar, br = clients
    seed(s)
    ga, gb = current(a)['track']['id'], current(b)['track']['id']
    bob_session = s.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (br,))
    bob_leases = s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,))
    result = save(a, ga).json()
    assert a.get('/api/queue/back').json()['track']['review_id'] == result['review_id']
    assert b.get('/api/queue/back').json()['track'] is None
    assert b.get(f'/api/tracks/{ga}?reassess=true').status_code == 409
    assert save(b, ga, expected_review_id=result['review_id']).status_code == 409
    next_a = current(a)['track']['id']
    correction = save(a, ga, expected_review_id=result['review_id'], realism=4)
    assert correction.status_code == 200
    assert current(a)['track']['id'] == next_a
    assert a.post(f'/api/tracks/{next_a}/skip', json={}, headers=WRITE).status_code == 200
    assert s.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (br,)) == bob_session
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,)) == bob_leases
    assert current(b)['track']['id'] == gb
    assert {r['reviewer_id'] for r in s.db.all('SELECT * FROM reviews')} == {ar}
    assert len({r['assessment_id'] for r in s.db.all('SELECT * FROM reviews')}) == 1
    assert save(a, ga, expected_review_id=result['review_id']).status_code == 409


def test_filter_change_releases_only_own_reservations_and_skips(clients):
    s, a, b, ar, br = clients
    seed(s)
    ga = current(a)['track']['id']
    current(b)
    b_before = s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,))
    a.post(f'/api/tracks/{ga}/skip', json={}, headers=WRITE)
    current(a)
    assert a.post('/api/queue', json={'order': 'alphabetical', 'song': 'nonexistent'}, headers=WRITE).status_code == 200
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,)) == []
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,)) == b_before
    assert s.db.one('SELECT COUNT(*) AS n FROM queue_skips')['n'] == 1
    assert b.post('/api/queue', json={'return_skipped': True}, headers=WRITE).status_code == 200
    assert s.db.one('SELECT COUNT(*) AS n FROM queue_skips')['n'] == 1
    assert a.post('/api/queue', json={'return_skipped': True}, headers=WRITE).status_code == 200
    assert s.db.one('SELECT COUNT(*) AS n FROM queue_skips')['n'] == 0


def test_change_identity_releases_old_queue_and_keeps_other_client(clients):
    s, a, b, ar, br = clients
    seed(s)
    current(a); current(b)
    before = s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,))
    sam = select(a, 'Sam')
    assert sam != ar
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,)) == []
    assert s.db.one('SELECT current_generation_id FROM sessions WHERE reviewer_id=?', (ar,))['current_generation_id'] is None
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,)) == before
    assert current(a)['track']


def test_expired_reservation_can_be_reclaimed_and_stale_submit_rejected(clients):
    s, a, b, ar, br = clients
    seed(s, 1)
    clock = [1000.]
    s.queues.clock = lambda: clock[0]
    ga = current(a)['track']['id']
    clock[0] += s.config.values['reservation_seconds'] + 1
    assert current(b)['track']['id'] == ga
    response = save(a, ga)
    assert response.status_code == 409 and response.json()['code'] == 'reservation_lost'
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 0
    assert a.post('/api/reviewer/heartbeat', json={'current_id': ga}, headers=WRITE).json()['valid_current'] is False
    assert current(a)['reason'] == 'reserved'
    assert s.db.one('SELECT reviewer_id FROM queue_reservations')['reviewer_id'] == br


def test_heartbeat_renews_active_queue_and_reconnect_retains_position(clients):
    s, a, b, ar, _ = clients
    seed(s, 4)
    clock = [1000.]
    s.queues.clock = lambda: clock[0]
    ga = current(a)['track']['id']
    first_expiry = s.db.one('SELECT expires_at FROM queue_reservations WHERE reviewer_id=?', (ar,))['expires_at']
    clock[0] += 100
    assert a.post('/api/reviewer/heartbeat', json={'current_id': ga}, headers=WRITE).json()['valid_current']
    assert all(r['expires_at'] > first_expiry for r in s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,)))
    reconnect = TestClient(create_app(s.config.path, False), base_url='http://localhost', headers={'X-GBR-Reviewer': ar})
    # Match the simulated wall clock across services for this reconnection.
    reconnect.app.state.service.queues.clock = s.queues.clock
    assert current(reconnect)['track']['id'] == ga
    clock[0] += 181
    assert current(a)['track']['id'] == ga  # Expired but nobody else claimed it.


def test_expired_prefetch_cannot_reclaim_an_abandoned_queue(clients):
    s, a, b, ar, _ = clients
    seed(s, 3)
    ga = current(a)['track']['id']
    s.db.execute('UPDATE queue_reservations SET expires_at=0 WHERE reviewer_id=?', (ar,))
    assert not a.post('/api/queue/prefetch', json={'current_id': ga, 'mode': 'full'}, headers=WRITE).json()['prepared']
    assert s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,)) == []


def test_live_queue_size_change_releases_excess_on_heartbeat(clients):
    s, a, b, ar, br = clients
    seed(s, 10)
    ga, gb = current(a)['track']['id'], current(b)['track']['id']
    s.config.values.update(reservation_queue_ahead=0, reservation_seconds=30)
    s.config.validate()
    assert a.post('/api/reviewer/heartbeat', json={'current_id': ga}, headers=WRITE).json()['valid_current']
    own = s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,))
    assert len(own) == 1 and own[0]['generation_id'] == ga
    assert len(s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,))) == 3
    assert a.get('/api/bootstrap').json()['heartbeat_seconds'] == 10
    assert b.post('/api/reviewer/heartbeat', json={'current_id': gb}, headers=WRITE).json()['valid_current']
    assert len(s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (br,))) == 1


@pytest.mark.parametrize('ahead', [0, 1, 4])
def test_configurable_small_queue_ahead(clients, ahead):
    s, a, b, ar, _ = clients
    seed(s, 8)
    s.config.values['reservation_queue_ahead'] = ahead
    result = current(a)
    assert result['reserved_ahead'] == ahead
    assert len(s.db.all('SELECT * FROM queue_reservations WHERE reviewer_id=?', (ar,))) == ahead + 1


def test_first_pass_complete_stops_assignment(clients):
    s, a, b, ar, br = clients
    seed(s, 2)
    s.config.values['reservation_queue_ahead'] = 0
    ga = current(a)['track']['id']
    gb = current(b)['track']['id']
    assert save(a, ga).status_code == 200
    assert current(a)['reason'] == 'reserved'
    assert save(b, gb).status_code == 200
    for client in (a, b):
        result = current(client)
        assert result['reason'] == 'first_pass_complete' and result['track'] is None
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 2
    assert s.db.one('SELECT COUNT(*) AS n FROM assessments')['n'] == 2


def test_review_and_history_exports_include_stable_identity(clients, tmp_path):
    s, a, b, ar, br = clients
    seed(s, 8)
    ga = current(a)['track']['id']; gb = current(b)['track']['id']
    ra = save(a, ga).json()['review_id']
    save(b, gb)
    save(a, ga, expected_review_id=ra, composition=3)
    for kind in ('reviews', 'review_history', 'generations'):
        path = dataset(s.db, tmp_path / (kind+'.json'), 'json', kind)
        data = json.loads(path.read_text())[kind]
        reviewed = [r for r in data if r.get('reviewer_id')]
        assert {r['reviewer_id'] for r in reviewed} == {ar, br}
        assert {r['reviewer_name'] for r in reviewed} == {'Simon', 'Alex'}
    values = s.db.all('SELECT * FROM reviews WHERE reviewer_id=? ORDER BY assessment_revision', (ar,))
    assert [r['assessment_revision'] for r in values] == [1, 2]
    assert len({r['assessment_id'] for r in values}) == 1


def test_composition_metadata_does_not_invent_genre_or_leak_settings(clients):
    s, a, _, _, _ = clients
    _, _, gid = track(s, metadata='title: Unknown\nsampler_steps: 35\n')
    result = a.get(f'/api/tracks/{gid}').json()
    assert result['genre'] == 'Genre not supplied'
    assert 'sampler_steps' not in result
    _, _, gid2 = track(s, 'tagged', frequency=500, metadata='title: Tagged\ncluster: Electronic\ngenre: Chiptune\n')
    result = a.get(f'/api/tracks/{gid2}').json()
    assert result['cluster'] == 'Electronic' and result['genre'] == 'Chiptune'


def make_legacy(path):
    with sqlite3.connect(path) as c:
        c.executescript(SCHEMA)
        c.execute("INSERT INTO songs VALUES('song','Old title','Chiptune','Electronic')")
        c.execute("INSERT INTO audio(id,pcm_sha256,duration,sample_rate,bit_depth,channels,archive_path,archive_bucket,created_at) VALUES('audio','hash',60,44100,24,2,'/original.flac',4,'2026-10-10')")
        c.execute("INSERT INTO generations(id,audio_id,song_id,metadata_key,original_filename,original_directory,created_at) VALUES('generation','audio','song','yaml','original.flac','/original','2026-10-10')")
        c.execute("INSERT INTO generation_metadata(generation_id,yaml_sha256,raw_yaml,raw_yaml_bytes,parsed_json,embedded_json,metadata_status) VALUES('generation','yamlhash','original yaml',?, '{}','{}','valid')", (b'original yaml',))
        for revision, values in [(1, (5,4,4,5)), (2, (4,3,3,4))]:
            rid='r'+str(revision)
            c.execute('INSERT INTO reviews VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (rid,'generation','audio',*values,sum(values)/4,int(sum(values)/4+.5),'2026-10-10','quick',revision,'main','r1' if revision==2 else None,'{}'))
            c.execute("INSERT INTO review_history(audio_id,generation_id,action,previous_review_id,review_id,details_json,created_at) VALUES('audio','generation',?,?,?,'{}','2026-10-10')", ('review' if revision==1 else 'correction','r1' if revision==2 else None,rid))
        c.execute("INSERT INTO sessions(id,current_generation_id,previous_generation_id,updated_at) VALUES('main',NULL,'generation','2026-10-10')")
        c.execute("INSERT INTO queue_skips VALUES('main','audio')")
        c.execute("INSERT INTO publishing_selection(generation_id,status,selected_at,updated_at) VALUES('generation','selected','2026-10-10','2026-10-10')")
        c.execute("INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,status,created_at,updated_at) VALUES('op','audio','generation','r2',4,'done','2026-10-10','2026-10-10')")
        before = {table:list(c.execute('SELECT * FROM '+table)) for table in ('reviews','review_history','audio','generation_metadata','publishing_selection','archive_operations','queue_skips')}
    return before


def test_legacy_migration_is_backed_up_transactional_and_preserves_data(tmp_path):
    path=tmp_path/'review.sqlite'; before=make_legacy(path)
    db=Database(path)
    assert db.migration_backup.is_file()
    with sqlite3.connect(db.migration_backup) as c:
        assert 'reviewer_id' not in {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
        assert list(c.execute('SELECT * FROM reviews')) == before['reviews']
    with sqlite3.connect(path) as c:
        for table, values in before.items():
            after=list(c.execute('SELECT * FROM '+table))
            assert [r[:len(values[0])] for r in after] == values, table
        assert c.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert list(c.execute('PRAGMA foreign_key_check'))==[]
    simon=db.one("SELECT * FROM reviewers WHERE name_key='simon'")
    assert {r['reviewer_id'] for r in db.all('SELECT * FROM reviews')} == {simon['id']}
    assert {r['reviewer_id'] for r in db.all('SELECT * FROM review_history')} == {simon['id']}
    assert [r['assessment_revision'] for r in db.all('SELECT * FROM reviews ORDER BY revision')] == [1,2]
    again=Database(path)
    assert again.migration_backup is None
    assert len(list((tmp_path/'backups').glob('*.sqlite'))) == 1
    assert again.one('SELECT COUNT(*) AS n FROM reviewers')['n'] == 1


def test_migration_failure_rolls_back_every_attribution_change(tmp_path, monkeypatch):
    import review.db as module
    path=tmp_path/'review.sqlite'; before=make_legacy(path)
    monkeypatch.setattr(module,'ANALYTICS_SQL','INVALID SQL')
    with pytest.raises(sqlite3.OperationalError):Database(path)
    with sqlite3.connect(path) as c:
        assert list(c.execute('SELECT * FROM reviews')) == before['reviews']
        assert 'reviewer_id' not in {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
        assert not c.execute('SELECT 1 FROM schema_version WHERE version=2').fetchone()
    assert len(list((tmp_path/'backups').glob('*.sqlite'))) == 1


def test_migration_rerun_does_not_reattribute_new_reviews(clients):
    s, a, b, ar, br = clients
    seed(s)
    gid=current(b)['track']['id']
    saved=save(b,gid).json()['review_id']
    before=s.db.one('SELECT * FROM reviews WHERE id=?',(saved,))
    Database(s.db.path)
    assert s.db.one('SELECT * FROM reviews WHERE id=?',(saved,)) == before
    assert before['reviewer_id'] == br
    assert s.db.one("SELECT COUNT(*) AS n FROM reviewers WHERE name_key='simon'")['n']==1
