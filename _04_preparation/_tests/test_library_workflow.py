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


def test_pending_recording_exposes_its_latest_failure_even_beyond_recent_history(publication):
    review,prep,publisher,store,gid,source,raw=publication
    pid,_=prepare(publication);store.fail_key='.mp3'
    first=wait(review.db,publisher.start_publish(pid,'hidden')['id'])
    assert first['status']=='failed'
    # A busy library can have more than 100 subsequent preparation events. The
    # recording still needs its own current status, outside that history window.
    for index in range(101):
        review.db.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at) VALUES(?,?,'release_prepare','failed',?,'{}','test','2026-10-29')",('old-history-'+str(index),gid,'old-history-'+str(index)))
    result=publisher.projects();item=result['projects'][0]
    assert not any(r['id']==first['id'] for r in result['releases'])
    assert item['publication']['id']==first['id']
    assert item['publication']['status']=='failed'
    assert item['publication']['error']=='simulated upload failure'
    assert item['publication']['visibility']=='hidden'
    assert item['publication']['prepared_id']==pid
    assert item['publication']['completed_at']
    store.fail_key=None
    second=wait(review.db,publisher.start_publish(pid,'hidden')['id'])
    assert second['status']=='done'
    assert publisher.publication_state(gid)['id']==second['id']
    assert not publisher.projects()['projects']
    assert publisher.library.projects()['items'][0]['generation_id']==gid
    assert not review.db.all('PRAGMA foreign_key_check')


def test_waiting_publication_releases_executor_for_other_tracks_and_resumes_same_job(publication):
    import time
    import numpy as np
    import soundfile as sf
    review,prep,publisher,store,gid,source,raw=publication
    pid,_=prepare(publication)
    # A second recording of the same song must not wait for the first one's
    # alignment. It has a different decoded audio identity and managed bundle.
    audio=review.config.inputs[0]/'second.flac'
    signal=.15*np.sin(2*np.pi*550*np.arange(44100)/44100)
    sf.write(audio,np.stack([signal,signal],axis=1),44100,subtype='PCM_24')
    audio.with_suffix('.yaml').write_bytes(raw)
    aid=review.indexer.index(audio);other_gid=review.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
    review.queues.open(review.default_reviewer_id,other_gid)
    review.review(other_gid,dict(audio_quality=5,composition=4,consistency=4,realism=4),'full',distribution=True)
    for op in review.db.all("SELECT * FROM archive_operations WHERE status='pending'"):review.archiver.process(op)
    other_pid=publisher.start_prepare(other_gid,{'include_flac':True})['id']
    assert wait(review.db,other_pid)['status']=='done'
    alignment=review.db.one("SELECT id FROM processing_tasks WHERE generation_id=? AND kind='lyric_alignment'",(gid,))['id']
    review.db.execute("UPDATE processing_tasks SET status='running' WHERE id=?",(alignment,))
    first=publisher.start_publish(pid)['id']
    try:
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            state=publisher.publication_state(gid)
            if state['progress'].get('phase')=='waiting_processing':break
            time.sleep(.025)
        assert state['progress']['phase']=='waiting_processing'
        assert state['progress']['blocking_tasks'][0]['id']==alignment
        assert publisher.start_publish(pid)['id']==first
        second=wait(review.db,publisher.start_publish(other_pid)['id'])
        assert second['status']=='done',second['error']
        assert source.is_file()
        assert review.db.one('SELECT status FROM processing_tasks WHERE id=?',(first,))['status']=='queued'
    finally:
        review.db.execute("UPDATE processing_tasks SET status='done' WHERE id=?",(alignment,))
    result=wait(review.db,first);assert result['status']=='done',result['error']
    assert len(review.db.all("SELECT id FROM processing_tasks WHERE generation_id=? AND kind='release_publish'",(gid,)))==1
    assert len(publisher.library.projects()['items'])==2
    assert not publisher.projects()['projects']
    assert not review.db.all('PRAGMA foreign_key_check')


def test_closing_a_waiting_publication_interrupts_it_without_moving_files(publication):
    import time
    review,prep,publisher,store,gid,source,raw=publication
    pid,_=prepare(publication)
    alignment=review.db.one("SELECT id FROM processing_tasks WHERE generation_id=? AND kind='lyric_alignment'",(gid,))['id']
    review.db.execute("UPDATE processing_tasks SET status='running' WHERE id=?",(alignment,))
    identifier=publisher.start_publish(pid)['id']
    deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if publisher.publication_state(gid)['progress'].get('phase')=='waiting_processing':break
        time.sleep(.025)
    publisher.close()
    assert not publisher.wait_thread.is_alive()
    row=review.db.one('SELECT status,error FROM processing_tasks WHERE id=?',(identifier,))
    assert row['status']=='interrupted' and 'retry publication' in row['error']
    assert source.is_file() and source.with_suffix('.yaml').read_bytes()==raw
    assert not store.objects
    review.db.execute("UPDATE processing_tasks SET status='done' WHERE id=?",(alignment,))
