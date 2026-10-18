import json
from pathlib import Path
import sqlite3
import time

from fastapi.testclient import TestClient
import pytest

from _review.app import create_app
from _review.db import Database
from _review.exports import dataset, publishing_manifest
from conftest import track, complete, archive_all

WRITE = {'X-GBR-Request': '1'}
SCORES = {'audio_quality': 5, 'composition': 4, 'consistency': 3, 'realism': 4, 'mode': 'quick'}


def client(service):
    app = create_app(service.config.path, False)
    return app.state.service, TestClient(app, base_url='http://localhost', headers={'X-GBR-Reviewer': app.state.service.default_reviewer_id})


@pytest.mark.parametrize('distribution,bookmarked', [(False, False), (True, False), (False, True), (True, True)])
def test_independent_flags_before_review_and_shared_selection(service, distribution, bookmarked):
    s, c = client(service)
    _, aid, gid = track(s)
    assigned = c.get('/api/queue/next').json()['track']; assert assigned['id'] == gid
    response = c.post(f'/api/tracks/{gid}/flags', json={'distribution': distribution, 'bookmarked': bookmarked}, headers=WRITE)
    assert response.status_code == 200, response.text
    assert response.json()['distribution'] is distribution and response.json()['bookmarked'] is bookmarked
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 0
    assert s.db.one('SELECT COUNT(*) AS n FROM archive_operations')['n'] == 0
    assert publishing_manifest(s.db)['tracks'] == []
    if distribution:
        assert s.db.one('SELECT status FROM publishing_selection')['status'] == 'selected'
        assert c.get('/api/publishing?bucket=&status=selected').json()['items'][0]['generation_id'] == gid
    if bookmarked:
        assert c.get('/api/publishing?bucket=&bookmarked=true').json()['items'][0]['generation_id'] == gid
    assert c.post(f'/api/publishing/{gid}', json={'status': 'published'}, headers=WRITE).status_code == 400


def test_flags_and_reason_commit_with_specific_revision_and_no_track_leak(service):
    s, c = client(service)
    track(s, 'one');track(s, 'two', frequency=500)
    first = c.get('/api/queue/next').json()['track']
    body = {**SCORES, 'reason': 'Strong chorus.\nBookmark for later. <script>text only</script>', 'distribution': True, 'bookmarked': True, 'submission_id': 'first-save'}
    saved = c.post(f"/api/tracks/{first['id']}/review", json=body, headers=WRITE)
    assert saved.status_code == 200, saved.text
    r = s.db.one('SELECT * FROM reviews')
    assert r['reviewer_id'] == s.default_reviewer_id and r['reason'] == body['reason']
    assert json.loads(r['flags_json'])['bookmarked'] is True
    details = c.get(f"/api/tracks/{first['id']}/reviews").json()['reviews'][0]
    assert details['reason'] == body['reason']
    correction = c.get('/api/queue/back').json()['track']; assert correction['reason'] == body['reason']
    second = c.get('/api/queue/next').json()['track']
    assert second['id'] != first['id'] and 'reason' not in second
    assert second['flags'] == {'distribution': False, 'bookmarked': False, 'rejected': False, 'publishing_status': 'not_selected'}
    assert c.post(f"/api/tracks/{second['id']}/review", json={**SCORES, 'reason': ''}, headers=WRITE).status_code == 200
    assert s.db.one('SELECT reason FROM reviews WHERE generation_id=?', (second['id'],))['reason'] == ''
    changed = c.post(f"/api/tracks/{first['id']}/review", json={**SCORES, 'reason': 'Revised explanation', 'expected_review_id': r['id'], 'reassessment': True}, headers=WRITE)
    assert changed.status_code == 200
    revisions = s.db.all('SELECT reason,reviewer_id,assessment_id FROM reviews WHERE audio_id=? ORDER BY revision', (first['audio_id'],))
    assert [row['reason'] for row in revisions] == [body['reason'], 'Revised explanation']
    assert len({row['assessment_id'] for row in revisions}) == 1


def test_idempotent_retry_never_creates_another_review(service):
    s, c = client(service); _, _, gid = track(s);c.get('/api/queue/next')
    body = {**SCORES, 'submission_id': 'a-network-retry', 'reason': 'Keep this', 'bookmarked': True}
    first = c.post(f'/api/tracks/{gid}/review', json=body, headers=WRITE)
    again = c.post(f'/api/tracks/{gid}/review', json=body, headers=WRITE)
    assert first.status_code == again.status_code == 200
    assert first.json()['review_id'] == again.json()['review_id'] and again.json()['already_saved']
    edited = c.post(f'/api/tracks/{gid}/review', json={**body, 'reason': 'Different unsaved text'}, headers=WRITE)
    assert edited.status_code == 409 and edited.json()['code'] == 'submission_saved'
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 1


def test_failed_stale_review_rolls_back_reason_flags_and_bookmark(service):
    s, c = client(service);_, _, gid = track(s);c.get('/api/queue/next')
    c.post(f'/api/publishing/{gid}', json={'status': 'selected'}, headers=WRITE)
    result = c.post(f'/api/tracks/{gid}/review', json={**SCORES, 'reason': 'Retain in browser', 'distribution': False, 'bookmarked': True, 'expected_publishing_status': 'not_selected'}, headers=WRITE)
    assert result.status_code == 409 and result.json()['code'] == 'flags_changed'
    assert s.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 0
    assert s.db.one('SELECT COUNT(*) AS n FROM bookmarks')['n'] == 0
    assert s.db.one('SELECT status FROM publishing_selection')['status'] == 'selected'


def test_bookmarks_are_reviewer_specific_and_reopening_respects_leases(service):
    s, a = client(service);other = s.queues.select('Other')['id'];b = TestClient(a.app, base_url='http://localhost', headers={'X-GBR-Reviewer': other})
    for i in range(6):track(s, str(i), frequency=220+i*37)
    ga = a.get('/api/queue/next').json()['track']['id'];gb = b.get('/api/queue/next').json()['track']['id']
    a.post(f'/api/tracks/{ga}/flags', json={'bookmarked': True}, headers=WRITE)
    assert len(a.get('/api/publishing?bucket=&bookmarked=true').json()['items']) == 1
    assert b.get('/api/publishing?bucket=&bookmarked=true').json()['items'] == []
    before = s.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (other,))
    assert b.post('/api/queue/open', json={'generation_id': ga}, headers=WRITE).status_code == 409
    assert s.db.one('SELECT * FROM sessions WHERE reviewer_id=?', (other,)) == before
    a.post(f'/api/tracks/{ga}/skip', json={}, headers=WRITE)
    a.post('/api/queue', json={'song': 'does not match'}, headers=WRITE)
    reopened = a.post('/api/queue/open', json={'generation_id': ga}, headers=WRITE)
    assert reopened.status_code == 200 and reopened.json()['track']['id'] == ga
    assert a.get('/api/queue/next').json()['track']['id'] == ga
    assert b.get('/api/queue/next').json()['track']['id'] == gb
    assert json.loads(s.db.one('SELECT filters_json FROM sessions WHERE reviewer_id=?', (s.default_reviewer_id,))['filters_json'])['song'] == 'does not match'


def test_exports_include_reason_and_bookmark_dataset(service, tmp_path):
    _, _, gid = track(service);service.set_flags(gid, service.default_reviewer_id, bookmarked=True);complete(service, gid)
    for kind in ('reviews', 'generations', 'bookmarks', 'bookmark_history'):
        output=dataset(service.db, tmp_path/(kind+'.json'), 'json', kind);export=json.loads(output.read_text())
        assert export['schema_version'] == 4 and export[kind]
    assert 'review_reason' in json.loads((tmp_path/'generations.json').read_text())['generations'][0]


def test_directory_picker_and_settings_archive_guard(service, tmp_path):
    s, c = client(service); new=tmp_path/'new music';new.mkdir()
    listing=c.get('/api/directories', params={'path':str(tmp_path)}).json()
    assert {'name':'new music','path':str(new)} in listing['directories']
    settings=c.get('/api/settings').json()
    assert settings['archive_locked'] is False
    allowed={key:settings[key] for key in ('input_directories','archive_directory','quick_seconds','standard_seconds','fast_seconds','transition_sensitivity','fade_ms','scan_interval_seconds','reservation_queue_ahead','reservation_seconds')}
    allowed['input_directories'].append(str(new));assert c.post('/api/settings', json=allowed, headers=WRITE).status_code == 200
    assert str(new) in json.loads(s.config.path.read_text())['input_directories']
    remote=TestClient(c.app, base_url='http://localhost', client=('198.51.100.20',1234), headers={'X-GBR-Reviewer':s.default_reviewer_id})
    assert remote.get('/api/directories').status_code in (401,403)
    _, _, gid=track(s);complete(s,gid);archive_all(s)
    assert c.get('/api/settings').json()['archive_locked']
    allowed['archive_directory']=str(tmp_path/'different archive')
    assert c.post('/api/settings', json=allowed, headers=WRITE).status_code == 400


def test_same_host_lan_address_can_configure_but_other_device_cannot(service, monkeypatch):
    import _review.app as module
    monkeypatch.setattr(module.socket,'gethostbyname_ex',lambda name:(name,[],['192.0.2.55']))
    app=create_app(service.config.path,False)
    host=TestClient(app,base_url='http://192.0.2.55',client=('192.0.2.55',1234))
    assert host.get('/api/bootstrap').json()['can_configure']
    assert host.get('/api/settings').status_code==200
    remote=TestClient(app,base_url='http://192.0.2.55',client=('192.0.2.56',1234))
    assert not remote.get('/api/bootstrap').json()['can_configure']
    assert remote.get('/api/settings').status_code in (401,403)


def test_cache_full_wav_key_is_stable_and_cleanup_preserves_untracked_and_active(service):
    path, aid, gid=track(service,seconds=8)
    service.analyzer.run(aid)
    first=service.playback.render(gid,'full','wav');original=path.read_bytes();wav=first.read_bytes()
    service.config.values['fade_ms']=77;service.analyzer.retune(aid)
    assert service.playback.render(gid,'full','wav')==first and first.read_bytes()==wav
    unknown=first.parent/'old-unknown.wav';unknown.write_bytes(b'legacy cache')
    service.db.execute('UPDATE playback_cache SET last_used=?', (time.time()-10*86400,))
    response=service.playback.response(first)
    assert service.playback.cleanup(7,False)['protected_files']==1 and first.exists()
    service.playback.pins.clear()  # Simulated stream completion, no real response is sent.
    preview=service.playback.cleanup(7,True)
    assert preview['eligible_files']==1 and first.exists()
    cleaned=service.playback.cleanup(7,False)
    assert cleaned['removed_files']==1 and not first.exists() and unknown.exists()
    assert path.read_bytes()==original
    assert service.playback.render(gid,'full','wav').read_bytes()==wav


def test_existing_full_wav_is_reused_without_rewriting(service):
    import hashlib
    import soundfile as sf
    from _review.util import dumps
    path, aid, gid=track(service,seconds=8)
    service.analyzer.run(aid)
    analysis=service.db.one('SELECT id FROM analyses WHERE audio_id=?',(aid,))['id']
    duration=service.db.one('SELECT duration FROM audio WHERE id=?',(aid,))['duration']
    key=hashlib.sha256(dumps([aid,analysis,[(0,duration)],'wav',None,35]).encode()).hexdigest()
    folder=service.playback.cache_root;folder.mkdir(parents=True,exist_ok=True)
    legacy=folder/(key+'.wav');samples,sr=sf.read(path);sf.write(legacy,samples,sr,subtype='PCM_24')
    before=legacy.read_bytes();mtime=legacy.stat().st_mtime_ns
    service.analyzer.retune(aid)
    assert service.playback.render(gid,'full','wav')==legacy
    assert legacy.read_bytes()==before and legacy.stat().st_mtime_ns==mtime
    assert len(list(folder.glob('*.wav')))==1


@pytest.mark.parametrize('missing', [False,True])
def test_cache_keeps_assets_when_original_changed_or_missing(service, missing):
    path, _, gid=track(service);cached=service.playback.render(gid,'full','wav')
    service.db.execute('UPDATE playback_cache SET last_used=?', (time.time()-10*86400,))
    if missing:path.unlink()
    else:path.write_bytes(b'external modification')
    result=service.playback.cleanup(7,False)
    assert result['removed_files']==0 and result['unavailable_or_changed_source']==1 and cached.exists()


def test_feature_migration_failure_is_transactional(tmp_path,monkeypatch):
    import _review.db as module
    from test_reviewers import make_legacy
    from _review.migration import migrate
    path=tmp_path/'old.sqlite';make_legacy(path)
    with sqlite3.connect(path) as c:migrate(c,module.ANALYTICS_SQL)
    with sqlite3.connect(path) as c:before=list(c.execute('SELECT * FROM reviews'))
    monkeypatch.setattr(module,'FEATURE_ANALYTICS_SQL','INVALID SQL')
    with pytest.raises(sqlite3.OperationalError):Database(path)
    with sqlite3.connect(path) as c:
        assert list(c.execute('SELECT * FROM reviews'))==before
        assert 'reason' not in {r[1] for r in c.execute('PRAGMA table_info(reviews)')}
        assert not c.execute('SELECT 1 FROM schema_version WHERE version=3').fetchone()
    assert len(list((tmp_path/'backups').glob('before-review-features-*.sqlite')))==1
