import json
import shutil
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC
from review.archive import verified_copy
from review.config import Config, password_hash, password_ok
from review.service import Service, Conflict
from review.util import digest, dumps, now
from conftest import track, complete, archive_all


@pytest.mark.parametrize("values,bucket", [((0,0,0,0),0),((0,0,1,1),1),((1,1,2,2),2),((2,2,3,3),3),((3,3,4,4),4),((4,4,5,5),5),((5,5,5,5),5)])
def test_half_up(service, values, bucket):
    _, aid, gid = track(service)
    complete(service, gid, values)
    row = service.db.one("SELECT * FROM latest_reviews WHERE audio_id=?", (aid,))
    assert row["average"] == sum(values)/4
    assert row["bucket"] == bucket


def test_byte_exact_archive_and_correction(service):
    path, aid, gid = track(service)
    raw_audio, raw_yaml = path.read_bytes(), path.with_suffix('.yaml').read_bytes()
    result = complete(service, gid)
    archive_all(service)
    a = service.db.one("SELECT * FROM audio WHERE id=?", (aid,))
    m = service.db.one("SELECT * FROM generation_metadata WHERE generation_id=?", (gid,))
    assert Path(a['archive_path']).read_bytes() == raw_audio
    assert Path(m['yaml_archive_path']).read_bytes() == raw_yaml
    assert not path.exists() and not path.with_suffix('.yaml').exists()
    old = a['archive_path']
    complete(service, gid, (1,1,1,1), result['review_id'])
    archive_all(service)
    a = service.db.one("SELECT * FROM audio WHERE id=?", (aid,))
    assert Path(a['archive_path']).parent.name == '1'
    assert Path(a['archive_path']).read_bytes() == raw_audio
    assert not Path(old).exists()
    assert len(service.db.all('SELECT * FROM reviews')) == 2
    history = service.db.all('SELECT * FROM review_history ORDER BY id')
    assert history[-1]['action'] == 'correction'
    assert json.loads(history[-1]['details_json'])['previous_scores']['realism'] == 5


def test_duplicate_pcm_distinct_provenance_and_tags(service):
    path, aid, gid = track(service, metadata='title: One song\ngenre: Chiptune\nencoder_cfg: 1.6\n')
    duplicate = path.with_name('duplicate.flac')
    shutil.copy2(path, duplicate)
    duplicate.with_suffix('.yaml').write_text('title: One song\ngenre: Chiptune\nencoder_cfg: 1.8\n')
    tagged = FLAC(duplicate);tagged['comment']='Different FLAC tags';tagged.save()
    assert service.indexer.index(duplicate) == aid
    assert service.db.one('SELECT COUNT(*) AS n FROM audio')['n'] == 1
    assert service.db.one('SELECT COUNT(*) AS n FROM generations')['n'] == 2
    assert service.db.one('SELECT COUNT(DISTINCT sha256) AS n FROM media_files')['n'] == 2
    complete(service,gid);archive_all(service)
    assert len(list(service.config.archive.rglob('*.flac'))) == 1
    assert len(list(service.config.archive.rglob('*.yaml'))) == 2
    assert service.next()['track'] is None
    dataset = service.db.all('SELECT * FROM analytics_dataset')
    assert sum(r['review_inherited'] for r in dataset) == 1
    assert {r['encoder_cfg'] for r in dataset} == {1.6,1.8}


def test_same_recording_and_metadata_indexes_once(service):
    path, aid, gid = track(service)
    duplicate = path.with_name('other.flac')
    shutil.copy2(path, duplicate);shutil.copy2(path.with_suffix('.yaml'), duplicate.with_suffix('.yaml'))
    service.indexer.index(duplicate);service.indexer.index(path)
    assert service.db.one('SELECT COUNT(*) AS n FROM generations')['n'] == 1
    assert service.db.one('SELECT COUNT(*) AS n FROM media_files')['n'] == 2


def test_reused_path_preserves_previous_media_identity(service):
    path, aid, gid = track(service, frequency=300)
    old = service.db.one('SELECT * FROM media_files')
    _, aid2, gid2 = track(service, frequency=500)
    assert aid != aid2 and gid != gid2
    rows = service.db.all('SELECT * FROM media_files ORDER BY id')
    assert len(rows)==2 and rows[0]['sha256']==old['sha256']
    assert rows[0]['storage_state']=='superseded'
    assert service.archiver.available(aid) is None


def test_missing_invalid_and_embedded_yaml(service):
    path, aid, gid = track(service,metadata='title: [not-valid\r\n')
    original = path.with_suffix('.yaml').read_bytes()
    assert service.db.one('SELECT metadata_status FROM generation_metadata')['metadata_status']=='invalid'
    complete(service,gid);archive_all(service)
    assert next(service.config.archive.rglob('*.yaml')).read_bytes()==original
    path2,_,_ = track(service,'missing',frequency=700)
    path2.with_suffix('.yaml').unlink()
    service.indexer.index(path2)
    assert service.db.one("SELECT 1 AS found FROM generation_metadata WHERE metadata_status='missing'")
    tags=FLAC(path2);tags['generation_yaml']='title: Recovered\ngenre: Jungle\nsampler_cfg: 1.4\n';tags.save()
    service.indexer.index(path2)
    assert service.db.one("SELECT sampler_cfg FROM generation_metadata WHERE metadata_status='embedded'")['sampler_cfg']==1.4
    _,aid3,gid3=track(service,'nonfinite',frequency=900,metadata='title: Valid audio\ngenre: Chiptune\nencoder_cfg: .nan\n')
    assert service.public_track(gid3)['title']=='Valid audio'
    assert service.db.one('SELECT metadata_status FROM generation_metadata WHERE generation_id=?',(gid3,))['metadata_status']=='invalid'


def test_corrupt_audio_is_logged_not_deleted(service):
    bad=service.config.inputs[0]/'bad.flac';bad.write_bytes(b'not an audio file')
    assert service.indexer.scan()==0 and bad.exists()
    assert service.db.one('SELECT COUNT(*) AS n FROM scan_errors')['n']==1


def test_collision_preserves_unrelated_media(service):
    path,aid,gid=track(service)
    target=service.config.archive/'5'/f'song__{aid[:8]}.flac';target.parent.mkdir(parents=True);target.write_bytes(b'UNRELATED')
    complete(service,gid);archive_all(service)
    assert target.read_bytes()==b'UNRELATED'
    actual=Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
    assert actual.name==f'song__{aid[:12]}.flac'


def test_failed_move_keeps_review_and_retry_recovers(service,monkeypatch):
    import review.archive as ar
    path,aid,gid=track(service);complete(service,gid)
    copy=ar.verified_copy
    monkeypatch.setattr(ar,'verified_copy',lambda *args: (_ for _ in ()).throw(OSError('drive disconnected')))
    archive_all(service)
    assert path.exists() and service.db.one('SELECT id FROM latest_reviews')
    assert service.db.one('SELECT status FROM archive_operations')['status']=='failed'
    monkeypatch.setattr(ar,'verified_copy',copy);service.archiver.retry();archive_all(service)
    assert service.db.one('SELECT status FROM archive_operations')['status']=='done'


def test_interruption_after_copy_recovers_using_journal(service):
    path,aid,gid=track(service);complete(service,gid)
    op=service.db.one('SELECT * FROM archive_operations');plan=service.archiver.plan(op)
    service.db.execute("UPDATE archive_operations SET status='running',plan_json=? WHERE id=?",(dumps(plan),op['id']))
    for item in plan['items']:verified_copy(item['source'],item['destination'],item['sha256'],op['id'])
    assert path.exists()
    service.archiver.reconcile();archive_all(service)
    assert not path.exists() and service.db.one('SELECT status FROM archive_operations')['status']=='done'


def test_source_mutation_is_not_deleted(service):
    path,aid,gid=track(service);complete(service,gid)
    op=service.db.one('SELECT * FROM archive_operations');plan=service.archiver.plan(op)
    service.db.execute('UPDATE archive_operations SET plan_json=? WHERE id=?',(dumps(plan),op['id']))
    path.write_bytes(b'NEW CONTENT')
    service.archiver.process(service.db.one('SELECT * FROM archive_operations'))
    assert path.read_bytes()==b'NEW CONTENT'
    assert service.db.one('SELECT status FROM archive_operations')['status']=='failed'


def test_superseded_pending_operations_do_not_undo_correction(service):
    _,aid,gid=track(service);r=complete(service,gid)
    complete(service,gid,(2,2,2,2),r['review_id']);archive_all(service)
    assert service.db.one('SELECT archive_bucket FROM audio')['archive_bucket']==2
    assert service.db.all('SELECT status FROM archive_operations ORDER BY rowid')[0]['status']=='superseded'


def test_stale_review_rejected(service):
    _,_,gid=track(service);r=complete(service,gid)
    with pytest.raises(Conflict):complete(service,gid)
    with pytest.raises(Conflict):complete(service,gid,previous='wrong')
    complete(service,gid,(4,4,4,4),r['review_id'])
    with pytest.raises(Conflict):complete(service,gid,previous=r['review_id'])


def test_skip_resume_and_queue_filters(service):
    _,_,g1=track(service,'Alpha',frequency=220);_,_,g2=track(service,'Beta',frequency=500)
    service.queue_settings('alphabetical','asc',{})
    assert service.next()['track']['id']==g1
    recreated=Service(service.config)
    assert recreated.next()['track']['id']==g1
    recreated.skip(g1)
    assert recreated.next()['track']['id']==g2
    recreated.skip(g2)
    assert recreated.next()['has_skips'] and recreated.next()['track'] is None
    recreated.queue_settings('alphabetical','asc',{'song':'Alpha'},True)
    assert recreated.next()['track']['id']==g1


def test_publishing_does_not_change_ratings_or_yaml(service):
    _,_,gid=track(service);complete(service,gid);archive_all(service)
    before=service.db.one('SELECT * FROM latest_reviews');m=service.db.one('SELECT yaml_archive_path FROM generation_metadata');yh=digest(m['yaml_archive_path'])
    with pytest.raises(ValueError):service.publishing(gid,'published')
    service.publishing(gid,'selected');service.publishing(gid,'published','GBR/song');service.publishing(gid,'withdrawn')
    assert service.db.one('SELECT * FROM latest_reviews')==before and digest(m['yaml_archive_path'])==yh
    p=service.db.one('SELECT * FROM publishing_selection')
    assert p['selected_at'] and p['published_at'] and p['external_reference']=='GBR/song'
    assert len(service.db.all('SELECT * FROM publishing_history'))==3


def test_backup_consistent_and_password_hash(service,tmp_path):
    import sqlite3
    _,_,gid=track(service);complete(service,gid)
    target=tmp_path/'backup.sqlite';service.db.backup(target)
    with sqlite3.connect(target) as c:assert c.execute('SELECT COUNT(*) FROM reviews').fetchone()[0]==1
    hashed=password_hash('ten-char-password')
    assert password_ok('ten-char-password',hashed) and not password_ok('wrong',hashed)
    assert 'ten-char-password' not in hashed
