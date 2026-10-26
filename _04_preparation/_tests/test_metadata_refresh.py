"""Legacy identity repairs and on-disk edits must reach the complete release."""
import hashlib
import json
from pathlib import Path

import pytest
import yaml

from test_publishing import publication,wait,prepare,POINTER


def legacy(data):
    review,prep,_,_,gid,source,_=data
    raw=b'\xef\xbb\xbf# original import\r\ntitle: moses-supposes\r\ncluster: Rock\r\nstyle: Punk\r\nlanguage: en\r\nexplicit: no\r\nrecorded: 2020-01-02\r\nlyrics: |\r\n  first line\r\n  second line\r\ncustom: {keep: yes}\r\n'
    source.with_suffix('.yaml').write_bytes(raw)
    review.db.execute('UPDATE generation_metadata SET raw_yaml_bytes=?,raw_yaml=?,yaml_sha256=?,parsed_json=? WHERE generation_id=?',(raw,raw.decode('utf-8-sig'),hashlib.sha256(raw).hexdigest(),json.dumps(yaml.safe_load(raw.decode('utf-8-sig')),default=str),gid))
    return raw


def test_external_legacy_identity_edit_publishes_and_moves_using_current_ids(publication):
    review,prep,publisher,store,gid,source,_=publication
    original=legacy(publication);reviews=review.db.all('SELECT * FROM reviews')
    changed=original.replace(b'title:',b'song_id: 13\r\nvariant_id: 2\r\ntitle:').replace(b'explicit: no',b'explicit: yes')
    source.with_suffix('.yaml').write_bytes(changed)
    project=prep.projects()['projects'][0]
    assert project['song_id']==13 and project['variant_id']==2
    pid,recipe=prepare(publication)
    result=wait(review.db,publisher.start_publish(pid)['id']);assert result['status']=='done',result['error']
    final=prep.source(gid)[1]
    assert '_song-13/_variant-2/' in final.as_posix()
    assert final.with_suffix('.yaml').read_bytes()==changed
    assert recipe['track']['song_id']==13 and recipe['track']['variant_id']==2 and recipe['track']['explicit'] is True
    assert json.loads(store.objects[POINTER])['tracks'][0]['displayTitle']=='moses-supposes'
    assert review.db.one('SELECT raw_yaml_bytes FROM generation_metadata WHERE generation_id=?',(gid,))['raw_yaml_bytes']==original
    assert review.db.all('SELECT * FROM reviews')==reviews and not review.db.all('PRAGMA foreign_key_check')


def test_identity_picker_patches_only_ids_and_rejects_stale_or_unlisted_values(publication):
    review,prep,_,_,gid,source,_=publication;original=legacy(publication)
    choices=prep.metadata.catalogue(gid);assert choices['suggested_song_id']=='13'
    result=prep.metadata.set_identity(gid,13,1,choices['sha256'])
    expected=original.replace(b'title:',b'song_id: 13\r\nvariant_id: 1\r\ntitle:')
    assert source.with_suffix('.yaml').read_bytes()==expected
    assert result['metadata']['song_id']==13
    assert review.db.one("SELECT COUNT(*) n FROM processing_tasks WHERE kind='preparation_yaml'")['n']==1
    with pytest.raises(ValueError,match='changed'):prep.metadata.set_identity(gid,13,1,choices['sha256'])
    with pytest.raises(ValueError,match='track_list'):prep.metadata.set_identity(gid,999,1,result['sha256'])
    with pytest.raises(ValueError,match='registered'):prep.metadata.set_identity(gid,13,99,result['sha256'])


def test_identity_only_edit_keeps_approved_lyrics_but_lyric_edits_invalidate_them(publication):
    review,prep,_,_,gid,source,_=publication
    current=prep.editorial(gid)
    saved=prep.save_editorial(gid,'lyric_edit',{'source_sha256':current['source_sha256'],'parent_task_id':current['lyric_task_id'],'words':current['words'],'approved':True})
    raw=source.with_suffix('.yaml').read_bytes()
    source.with_suffix('.yaml').write_bytes(raw.replace(b'variant_id: 2',b'variant_id: 1'))
    assert prep.editorial(gid)['approved'] and prep.editorial(gid)['lyric_task_id']==saved['id']
    source.with_suffix('.yaml').write_bytes(raw.replace(b'first line',b'brand new words'))
    edited=prep.editorial(gid)
    assert not edited['approved'] and edited['lyric_task_id'] is None
    assert [w['text'] for w in edited['words']][:3]==['brand','new','words']
    prep.auto_align(gid)
    jobs=review.db.all("SELECT recipe_json FROM processing_tasks WHERE kind='lyric_alignment'")
    assert len(jobs)==2 and json.loads(jobs[-1]['recipe_json'])['words'][0]['text']=='brand'
    assert review.db.one('SELECT status FROM processing_tasks WHERE id=?',(saved['id'],))['status']=='done'


def test_language_and_caption_edits_reach_editorial_and_release(publication):
    _,prep,_,_,gid,source,_=publication
    raw=source.with_suffix('.yaml').read_bytes().replace(b'language: en',b'language: de')+b'caption: |\n  updated caption\n'
    source.with_suffix('.yaml').write_bytes(raw)
    assert prep.editorial(gid)['language']=='de'
    _,recipe=prepare(publication)
    assert recipe['track']['language']=='de'
    assert json.loads(prep.source(gid)[0]['parsed_json'])['caption']=='updated caption\n'


def test_invalid_or_missing_current_yaml_blocks_stale_publication_without_erasing_history(publication):
    review,prep,publisher,store,gid,source,_=publication
    pid,_=prepare(publication);current=source.with_suffix('.yaml');original=current.read_bytes()
    current.write_bytes(b'title: [unfinished\n')
    assert 'invalid' in prep.projects(include_completed=True)['projects'][0]['yaml_error']
    with pytest.raises(ValueError,match='invalid'):publisher.start_publish(pid)
    assert not store.objects
    current.write_bytes(original);assert prep.metadata.read(gid)['sha256']==hashlib.sha256(original).hexdigest()
    current.unlink()
    with pytest.raises(ValueError,match='missing'):publisher.start_publish(pid)
    current.write_bytes(original)
    review.db.execute("UPDATE generation_metadata SET yaml_sha256='corrupt' WHERE generation_id=?",(gid,))
    with pytest.raises(ValueError,match='checksum'):publisher.start_publish(pid)


def test_yamls_changed_after_package_creation_require_rebuilding_before_move(publication):
    review,prep,publisher,store,gid,source,_=publication
    pid,_=prepare(publication)
    source.with_suffix('.yaml').write_bytes(source.with_suffix('.yaml').read_bytes()+b'album_name: new album\n')
    result=wait(review.db,publisher.start_publish(pid)['id'])
    assert result['status']=='failed' and 'changed' in result['error']
    assert source.exists() and not store.objects and publisher.projects()['projects'][0]['can_return']
    publisher.discard(pid)
    assert prep.projects()['projects'][0]['generation_id']==gid


def test_edited_metadata_revisions_survive_publishing_and_later_library_changes(publication):
    review,prep,publisher,_,gid,source,_=publication;legacy(publication)
    current=prep.metadata.catalogue(gid);prep.metadata.set_identity(gid,13,1,current['sha256'])
    pid,_=prepare(publication);assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    draft=publisher.library.save(gid,{'parent_id':None,'changes':{'title':'Updated title'}})
    artifact=review.db.one('SELECT uri FROM generation_artifacts WHERE id=?',(draft['draft']['id']+'-yaml',))
    parsed=yaml.safe_load(Path(artifact['uri']).read_text(encoding='utf-8-sig'))
    assert parsed['song_id']==13 and parsed['variant_id']==1 and parsed['title']=='Updated title'
