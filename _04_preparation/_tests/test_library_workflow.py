"""End-stage boundaries, release revisions, room isolation and background alignment."""
import copy
import json
from pathlib import Path
import sys

import pytest

from test_publishing import publication,wait,image_bytes,prepare,POINTER
from test_preparation import prepared,promote
from _gbr_preparation.release_catalogue import destination


def published(data,art=False):
    review,prep,publisher,store,gid,_,_=data
    options={}
    if art:options['artwork_id']=prep.import_artwork(gid,image_bytes())['id']
    pid,recipe=prepare(data,**options)
    task=wait(review.db,publisher.start_publish(pid)['id']);assert task['status']=='done',task['error']
    return pid,json.loads(review.db.one('SELECT recipe_json FROM processing_tasks WHERE id=?',(pid,))['recipe_json'])


def test_finish_preparation_is_offline_and_only_publish_places_it_in_library(publication):
    review,prep,publisher,store,gid,_,_=publication
    assert prep.projects()['projects'] and not publisher.projects()['projects'] and not publisher.library.projects()['items']
    task=wait(review.db,publisher.finish_preparation(gid,{'include_flac':True})['id'])
    assert task['status']=='done',task['error']
    assert not store.objects and not prep.projects()['projects']
    queued=publisher.projects()['projects'];assert len(queued)==1 and queued[0]['prepared_id']==task['id']
    assert not publisher.library.projects()['items']
    assert wait(review.db,publisher.start_publish(task['id'])['id'])['status']=='done'
    assert not publisher.projects()['projects'] and not prep.projects()['projects']
    assert publisher.library.projects()['items'][0]['generation_id']==gid


def test_metadata_revision_reuses_audio_and_preserves_source_reviews_and_identity(publication):
    review,prep,publisher,store,gid,_,yaml=publication
    pid,original=published(publication)
    source=prep.source(gid)[1];audio={f['path']:Path(f['path']).read_bytes() for f in original['files'] if f['content_type'].startswith('audio/')}
    reviews=review.db.all('SELECT * FROM reviews')
    detail=publisher.library.save(gid,{'parent_id':None,'changes':{'title':'A new sleeve title','cluster':'Jazz','style':'Swing','language':'de','explicit':'no','display_order':3}})
    assert detail['draft']['id'] and detail['track']['displayTitle']!='A new sleeve title'
    task=wait(review.db,publisher.library.prepare_revision(gid,{'publish':False})['id']);assert task['status']=='done',task['error']
    revision=json.loads(task['recipe_json'])
    assert {f['path'] for f in revision['files'] if f['content_type'].startswith('audio/')}==set(audio)
    assert revision['track']['id']==original['track']['id'] and revision['track']['shareToken']==original['track']['shareToken']
    assert wait(review.db,publisher.start_publish(task['id'])['id'])['status']=='done'
    current=publisher.library.record(gid)['track'];assert current['displayTitle']=='A new sleeve title' and current['genre']['parent_genre']=='Jazz'
    assert source.read_bytes() and next(source.parent.glob('*.yaml')).read_bytes()==yaml
    assert review.db.all('SELECT * FROM reviews')==reviews
    assert all(Path(path).read_bytes()==value for path,value in audio.items())
    assert not (prep.tracks/source.parent.parent.name).exists()
    with pytest.raises(ValueError,match='changed'):publisher.library.save(gid,{'parent_id':None,'changes':{'title':'Stale'}})


def test_published_lyric_and_artwork_revisions_stay_in_the_one_bundle(publication):
    review,prep,publisher,store,gid,_,yaml=publication
    published(publication)
    result=publisher.library.save(gid,{'parent_id':None,'changes':{'lyrics':'[verse]\nnew words here\nand another line'}})
    editorial=prep.editorial(gid);assert len(editorial['words'])==6 and not editorial['approved']
    prep.save_editorial(gid,'lyric_edit',{'source_sha256':editorial['source_sha256'],'parent_task_id':editorial['lyric_task_id'],'words':editorial['words'],'approved':True})
    art=prep.import_artwork(gid,image_bytes())
    task=wait(review.db,publisher.library.prepare_revision(gid,{'artwork_id':art['id'],'publish':False})['id']);assert task['status']=='done',task['error']
    recipe=json.loads(task['recipe_json']);assert recipe['track']['lyrics']['raw'].startswith('[verse]\nnew words')
    assert recipe['track']['lyrics_url'] and recipe['track']['artwork_url']
    assert all(Path(f['path']).is_relative_to(prep.showcase) for f in recipe['files'])
    assert not list(prep.tracks.rglob('*.flac')) and not review.db.all('PRAGMA foreign_key_check')


def test_multiple_rooms_have_distinct_entrances_and_publication_catalogues(publication):
    review,prep,publisher,store,gid,_,_=publication
    pid,recipe=published(publication,art=True)
    settings=publisher.library.settings()
    settings['rooms'].extend([{'id':'demos','name':'The demo room','entry_track_id':recipe['track']['id'],'enabled':True,'order':3},
                              {'id':'rarities','name':'Rarities','entry_track_id':None,'enabled':True,'order':4}])
    config=publisher.library.save_settings(settings);assert config['settings']['revision']==1
    publisher.library.sync_settings()
    assert json.loads(store.objects['gbr/v2/library.json'])['rooms'][-2]['id']=='demos'
    for room in ['demos','rarities','easter','public']:
        task=wait(review.db,publisher.start_publish(pid,room)['id']);assert task['status']=='done',task['error']
        for other in publisher.library.room_ids():
            data=json.loads(store.objects.get(destination(other)[0],b'{"tracks":[]}'))
            assert any(t['id']==recipe['track']['id'] for t in data['tracks'])==(other==room)
    stale=copy.deepcopy(settings)
    with pytest.raises(ValueError,match='changed'):publisher.library.save_settings(stale)
    invalid=publisher.library.settings();invalid['rooms'][-1]['entry_track_id']=recipe['track']['id']
    with pytest.raises(ValueError,match='only one'):publisher.library.save_settings(invalid)


def test_alignment_is_automatically_queued_after_verified_promotion(prepared,monkeypatch):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    task=review.db.one("SELECT * FROM processing_tasks WHERE generation_id=? AND kind='lyric_alignment'",(gid,))
    assert task and task['status']=='waiting'
    for _ in range(3):prep.auto_align(gid)
    assert review.db.one("SELECT COUNT(*) n FROM processing_tasks WHERE kind='lyric_alignment'")['n']==1
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:True)
    prep.auto_align(gid);assert review.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='waiting'
    submitted=[];monkeypatch.setattr(prep.pool,'submit',lambda function,identifier:submitted.append(identifier))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:False)
    prep.auto_align(gid);prep.auto_align(gid)
    assert submitted==[task['id']]
    assert review.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='queued'


def test_invalid_library_yaml_cannot_half_save_a_lyric_revision(publication):
    review,prep,publisher,_,gid,_,_=publication;published(publication)
    before=review.db.all("SELECT id FROM processing_tasks WHERE generation_id=? AND kind IN ('library_edit','lyric_edit')",(gid,))
    review.db.execute('UPDATE generation_metadata SET raw_yaml_bytes=? WHERE generation_id=?',(b'title: [broken\n',gid))
    with pytest.raises(ValueError):publisher.library.save(gid,{'parent_id':None,'changes':{'lyrics':'replacement words'}})
    assert review.db.all("SELECT id FROM processing_tasks WHERE generation_id=? AND kind IN ('library_edit','lyric_edit')",(gid,))==before


def test_busy_alignment_worker_waits_on_the_same_task_instead_of_failing(prepared,monkeypatch):
    from types import SimpleNamespace
    review,prep,_,_,gid,_=prepared;promote(prepared)
    submitted=[]
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:False)
    monkeypatch.setattr(prep.pool,'submit',lambda fn,identifier:submitted.append(identifier))
    queued=prep.auto_align(gid);identifier=queued['id']
    monkeypatch.setattr(prep.processing,'execute',lambda *args,**kwargs:SimpleNamespace(returncode=1,stderr=b'Another GBR worker is already using this GPU'))
    prep._run(identifier)
    task=review.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
    assert task['status']=='waiting' and task['completed_at'] is None
    assert json.loads(task['telemetry_json'])['retry_after']>0
    prep.auto_align(gid);assert submitted==[identifier]
    review.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',('{}',identifier))
    prep.auto_align(gid);assert submitted==[identifier,identifier]


def test_return_to_preparation_discards_only_the_unpublished_package(publication):
    review,prep,publisher,store,gid,source,raw=publication
    identifier=publisher.finish_preparation(gid,{'include_flac':True})['id']
    assert wait(review.db,identifier)['status']=='done'
    assert publisher.projects()['projects'][0]['can_return']
    publisher.discard(identifier)
    assert not publisher.projects()['projects'] and prep.projects()['projects'][0]['generation_id']==gid
    assert source.is_file() and source.with_suffix('.yaml').read_bytes()==raw and not store.objects


def test_publication_waits_for_alignment_to_finish_before_moving_its_working_paths(publication):
    import time
    review,prep,publisher,_,gid,source,_=publication
    pid,_=prepare(publication,timed_lyrics=False)
    alignment=review.db.one("SELECT id FROM processing_tasks WHERE generation_id=? AND kind='lyric_alignment'",(gid,))
    review.db.execute("UPDATE processing_tasks SET status='running' WHERE id=?",(alignment['id'],))
    job=publisher.start_publish(pid)['id']
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            progress=json.loads(review.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(job,))['telemetry_json'])
            if progress.get('phase')=='waiting_processing':break
            time.sleep(.025)
        assert progress.get('phase')=='waiting_processing' and source.exists() and not list(prep.showcase.rglob('*.flac'))
    finally:review.db.execute("UPDATE processing_tasks SET status='done' WHERE id=?",(alignment['id'],))
    result=wait(review.db,job);assert result['status']=='done',result['error']


def test_new_alignment_does_not_invalidate_a_package_without_timed_lyrics(publication):
    review,prep,publisher,_,gid,_,_=publication
    pid,_=prepare(publication,timed_lyrics=False)
    current=prep.editorial(gid)
    prep.save_editorial(gid,'lyric_edit',{'source_sha256':current['source_sha256'],'parent_task_id':current['lyric_task_id'],'words':current['words'],'approved':True})
    result=wait(review.db,publisher.start_publish(pid)['id']);assert result['status']=='done',result['error']
