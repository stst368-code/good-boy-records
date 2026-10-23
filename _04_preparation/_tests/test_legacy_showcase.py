"""Legacy migration uses the existing reviewed recording and immutable YAML."""
import json
from pathlib import Path
import shutil
import time

import pytest

from test_publishing import publication,wait,image_bytes,POINTER
from _gbr_preparation.service import canonical,digest
from _gbr_preparation.showcase import remap


@pytest.fixture
def legacy(publication):
    return make_legacy(publication)


def make_legacy(publication):
    review,prep,publisher,store,gid,source,yaml=publication
    root=publisher.root/'_96_website/_showcase';folder=root/'song'/'song-punk';folder.mkdir(parents=True)
    old=source.parent
    for path in list(old.iterdir()):shutil.move(str(path),folder/path.name)
    with review.db.transaction() as c:
        for table,key,column in [('audio','id','archive_path'),('media_files','id','path'),('generation_metadata','generation_id','yaml_archive_path')]:
            for row in c.execute(f'SELECT {key},{column} FROM {table} WHERE {column} IS NOT NULL').fetchall():
                c.execute(f'UPDATE {table} SET {column}=? WHERE {key}=?',(remap(row[1],old,folder),row[0]))
    source=folder/source.name
    (folder/'cover.png').write_bytes(image_bytes())
    (folder/'lyrics.json').write_text('{"format":"gbr-word-lyrics-v2","words":[{"text":"first","start":0.1,"end":0.5}]}')
    (folder/'playback.json').write_text('{"format":"gbr-playback-v1","dance":{"events":[]}}')
    yp=next(folder.glob('*.yaml'));prefix='showcase/song/song-punk/'
    track={'id':'old-song-punk','displayTitle':'Old Song','variant':'punk','shareToken':'KEEPthis',
           'yamlUrl':prefix+yp.name,'artwork_url':prefix+'cover.png','lyrics_url':prefix+'lyrics.json','playback_url':prefix+'playback.json',
           'lyrics':{'raw':'first line','wordTiming':{'src':prefix+'lyrics.json','approved':False}},
           'audio':{'sources':{'flac':prefix+source.name,'mp3':prefix+'old.mp3'}}}
    catalogue=publisher.root/'_96_website/_data/catalogue.json';catalogue.parent.mkdir(parents=True,exist_ok=True)
    catalogue.write_text(json.dumps({'tracks':[track]}))
    return review,prep,publisher,store,gid,source,yaml,root,track


def scan(legacy):return legacy[2].legacy.preview(str(legacy[7]))


def test_preview_is_read_only_and_exact_identity_is_preserved(legacy):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    before={str(p):digest(p) for p in root.rglob('*') if p.is_file()}
    snapshot=review.db.all('SELECT * FROM processing_tasks')
    plan=scan(legacy)
    assert plan['ready']==1 and plan['blocked']==0
    assert plan['rows'][0]['public_id']=='old-song-punk'
    assert before=={str(p):digest(p) for p in root.rglob('*') if p.is_file()}
    assert snapshot==review.db.all('SELECT * FROM processing_tasks') and not store.objects


def test_migrate_bundle_moves_companions_without_touching_yaml_or_reviews(legacy):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    reviews=review.db.all('SELECT * FROM reviews');plan=scan(legacy)
    job=publisher.legacy.start(plan['token'],[gid],False)['tasks'][0]
    row=wait(review.db,job);assert row['status']=='done',row['error']
    target=prep.source(gid)[1]
    assert not source.exists() and target.is_relative_to(prep.showcase)
    assert next(target.parent.glob('*.yaml')).read_bytes()==yaml
    assert (target.parent/'cover.png').is_file() and (target.parent/'playback.json').is_file()
    assert review.db.all('SELECT * FROM reviews')==reviews and not review.db.all('PRAGMA foreign_key_check')
    assert not prep.projects()['projects'] and not store.objects
    publisher.legacy.run(job);assert not source.exists() # explicit idempotent retry
    assert not scan(legacy)['ready']


def test_r2_alignment_keeps_old_id_token_cover_and_public_timings(legacy):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    plan=scan(legacy);job=publisher.legacy.start(plan['token'],[gid],True)['tasks'][0]
    assert wait(review.db,job)['status']=='done'
    deadline=time.monotonic()+20;release=None
    while time.monotonic()<deadline:
        release=review.db.one("SELECT id FROM processing_tasks WHERE kind='release_publish'")
        if release:break
        time.sleep(.025)
    assert release and wait(review.db,release['id'])['status']=='done'
    public=json.loads(store.objects[POINTER])['tracks'][0]
    assert public['id']==track['id'] and public['shareToken']=='KEEPthis' and public['legacy_id']==track['id']
    assert public['lyrics_url'].startswith('https://media.test/gbr/v2/objects/') and public['artwork_url']
    assert public['lyrics']['wordTiming']['approved'] is False # not promoted to approval
    assert public['playback_url'] and public['audio']['sources']['mp3']
    assert not any(k.endswith('.yaml') for k in store.objects)
    prepared=review.db.one("SELECT id FROM processing_tasks WHERE kind='release_prepare'")['id']
    publisher.start_withdraw(prepared)
    assert json.loads(store.objects[POINTER])['withdrawn']==[track['id']]
    review.publishing(gid,'selected')
    assert wait(review.db,publisher.start_publish(prepared)['id'])['status']=='done'
    assert not json.loads(store.objects[POINTER])['withdrawn']


@pytest.mark.parametrize('change',['yaml','identity','unselected','duplicate_flac','missing_cover'])
def test_bad_inputs_are_individually_flagged_without_moves(legacy,change):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    if change=='yaml':next(source.parent.glob('*.yaml')).write_bytes(yaml+b'# changed\n')
    elif change=='identity':review.db.execute("UPDATE generation_metadata SET parsed_json=? WHERE generation_id=?",(canonical({'song_id':13,'variant_id':None}),gid))
    elif change=='unselected':review.publishing(gid,'withdrawn')
    elif change=='duplicate_flac':shutil.copyfile(source,source.with_name('second.flac'))
    else:(source.parent/'cover.png').unlink()
    plan=scan(legacy)
    assert plan['ready']==0 and plan['blocked']==1 and plan['rows'][0]['error']
    assert source.exists() and not prep.showcase.exists() and not store.objects


def test_changed_preview_does_not_move_any_files(legacy):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    plan=scan(legacy);(source.parent/'new-note.txt').write_text('changed')
    with pytest.raises(ValueError,match='changed'):publisher.legacy.start(plan['token'],[gid],False)
    assert source.exists() and not prep.showcase.exists()


def test_writing_directory_cannot_be_used_as_a_migration_source(legacy):
    publisher=legacy[2]
    with pytest.raises(ValueError,match='legacy'):publisher.legacy.preview(str(publisher.root/'_01_writing'))


def test_locked_legacy_file_is_reported_and_cleanup_can_resume(legacy,monkeypatch):
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    plan=scan(legacy);unlink=Path.unlink
    def locked(path,*args,**kwargs):
        if path==source:raise PermissionError('in use')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',locked)
    job=publisher.legacy.start(plan['token'],[gid],False)['tasks'][0]
    row=wait(review.db,job);assert row['status']=='done' and json.loads(row['telemetry_json'])['cleanup_pending']
    monkeypatch.setattr(Path,'unlink',unlink);publisher.legacy.run(job)
    assert not source.exists() and not json.loads(review.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(job,))['telemetry_json'])['cleanup_pending']


def test_interrupted_database_switch_reuses_the_same_move(legacy,monkeypatch):
    import _gbr_preparation.showcase as module
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    plan=scan(legacy);original=module.remap
    def fail(*args):raise RuntimeError('Interrupted before database switch')
    monkeypatch.setattr(module,'remap',fail)
    identifier=publisher.legacy.start(plan['token'],[gid],False)['tasks'][0]
    assert wait(review.db,identifier)['status']=='failed' and source.exists()
    assert prep.review.archiver.available(review.db.one('SELECT audio_id FROM generations WHERE id=?',(gid,))['audio_id'])==source
    monkeypatch.setattr(module,'remap',original);publisher.legacy.retry(identifier)
    assert wait(review.db,identifier)['status']=='done' and not source.exists()
    assert review.db.one("SELECT COUNT(*) n FROM processing_tasks WHERE kind='showcase_move'")['n']==1


def test_public_audio_mismatch_is_blocked_before_any_move(legacy):
    publisher=legacy[2];track=legacy[8]
    track['audio']['sources']['flac']='showcase/another-recording.flac'
    (publisher.root/'_96_website/_data/catalogue.json').write_text(json.dumps({'tracks':[track]}))
    result=scan(legacy)
    assert result['blocked']==1 and result['ready']==0
    assert 'does not identify' in result['rows'][0]['error']


def test_migration_api_requires_host_csrf_and_moves_only_selected_recording(legacy):
    from fastapi.testclient import TestClient
    from test_publishing import create_test_app
    review,prep,publisher,store,gid,source,yaml,root,track=legacy
    app=create_test_app(review,prep)
    app.state.publisher.root=publisher.root;app.state.publisher.showcase.root=publisher.showcase.root
    with TestClient(app,base_url='http://127.0.0.1') as client:
        body={'directory':str(root)};headers={'x-gbr-request':'1'}
        assert client.post('/api/releases/legacy/preview',json=body).status_code==403
        plan=client.post('/api/releases/legacy/preview',json=body,headers=headers)
        assert plan.status_code==200,plan.text
        result=client.post('/api/releases/legacy/apply',json={'token':plan.json()['token'],'generations':[gid],'synchronise':False},headers=headers)
        assert result.status_code==200,result.text
        assert wait(review.db,result.json()['tasks'][0])['status']=='done'
        assert not source.exists() and not store.objects
