"""The actual porto tree shapes: paired audio, MP3-only and flat Easter files."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import pytest
import soundfile as sf
import yaml

from test_publishing import publication,image_bytes,wait,POINTER
from _gbr_preparation.library_import import LibraryImport
from _gbr_preparation.service import digest


def audio(path,frequency):
    path.parent.mkdir(parents=True,exist_ok=True)
    wave=.15*np.sin(2*np.pi*frequency*np.arange(4410)/44100)
    if path.suffix=='.mp3':
        wav=path.with_suffix('.wav');sf.write(wav,np.stack([wave,wave],axis=1),44100,subtype='PCM_24')
        subprocess.run(['ffmpeg','-nostdin','-v','error','-y','-i',str(wav),str(path)],check=True);wav.unlink()
    else:sf.write(path,np.stack([wave,wave],axis=1),44100,subtype='PCM_24')


def wait_import(publisher,timeout=30):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=publisher.library_import.state()
        if state['status']!='running':return state
        time.sleep(.025)
    raise AssertionError('Import timed out')


def test_mixed_showcase_import_preserves_public_ids_formats_sources_and_room(publication,tmp_path):
    review,prep,publisher,store,_,_,_=publication
    old=tmp_path/'porto'/'showcase';a=old/'moses-supposes'/'moses-pop'/'moses-pop.flac';audio(a,610)
    b=old/'moses-supposes'/'moses-rock'/'moses-rock.mp3';audio(b,710)
    c=old/'easter'/'odd-one.flac';audio(c,810)
    d=old/'easter'/'odd-two.mp3';audio(d,910)
    raw=b'\xef\xbb\xbf# preserve this\r\ntitle: moses-supposes\r\nversion: Pop\r\nsteps: 25\r\ntext_encoder_model: minimax_music3_text_encoder_pruned_bf16\r\nlyrics: |\r\n  first /n second\r\ncustom: {keep: yes}\r\n'
    for path in [a,b,c,d]:path.with_suffix('.yaml').write_bytes(raw);path.with_suffix('.png').write_bytes(image_bytes())
    shared=old/'easter'/'easter.lyrics.json';shared.write_text('{"format":"gbr-word-lyrics-v2","words":[]}',encoding='utf-8')
    def track(path,index):
        relative='showcase/'+path.relative_to(old).as_posix();stem=relative.rsplit('.',1)[0]
        return {'id':'old-'+str(index),'displayTitle':'moses-supposes','variant':'Pop','shareToken':'KEEP000'+str(index),
                'genre':{'parent_genre':'Pop','child_genre':'Pop'},'audio':{'sources':{'mp3':relative if path.suffix=='.mp3' else None,'flac':relative if path.suffix=='.flac' else None}},
                'yamlUrl':stem+'.yaml','artwork_url':stem+'.png','lyrics_url':'showcase/easter/easter.lyrics.json' if path.parent.name=='easter' else None}
    tracks=[track(path,i+1) for i,path in enumerate([a,b,c,d])]
    catalogue=publisher.root/'_96_website/_data/catalogue.json';catalogue.parent.mkdir(parents=True,exist_ok=True)
    catalogue.write_text(json.dumps({'tracks':tracks[:2],'easter':{'tracks':tracks[2:]}}),encoding='utf-8')
    reviews=review.db.all('SELECT * FROM reviews')
    publisher.library_import.start(str(old),False);state=wait_import(publisher)
    assert state['status']=='done',state
    assert state['completed']==4 and state['failed']==0 and not store.objects
    imported=publisher.library.projects()['items'];assert len(imported)==4
    assert {i['track']['id'] for i in imported}=={'old-1','old-2','old-3','old-4'}
    assert sum(i['visibility']=='easter' for i in imported)==2
    assert all(i['track']['shareToken'].startswith('KEEP') for i in imported)
    assert review.db.all('SELECT * FROM reviews')==reviews
    assert not review.db.all('PRAGMA foreign_key_check')
    assert not any(path.exists() for path in [a,b,c,d,shared])
    assert len(list(prep.showcase.rglob('*.flac')))==2
    for item in state['items']:
        gid=item['result']['generation_id'];record=publisher.library.record(gid)
        metadata=review.db.one('SELECT parsed_json FROM generation_metadata WHERE generation_id=?',(gid,))
        parsed=json.loads(metadata['parsed_json']);assert parsed['language']=='en' and parsed['style']=='Pop' and parsed['lyrics']=='first \n second\n'
        assert parsed['sampler_steps']==25 and parsed['text_encoder']=='minimax_music3_text_encoder_pruned_bf16.safetensors'
        source=prep.source(gid)[1]
        assert next((source.parent/'_originals').glob('*.yaml')).read_bytes()==raw
        current=source.with_suffix('.yaml').read_bytes();assert current.startswith(b'\xef\xbb\xbf# preserve this\r\n') and b'custom: {keep: yes}\r\n' in current
        assert current.index(b'language:')<current.index(b'lyrics:')
        assert all(Path(f['path']).is_file() for f in record['recipe']['files'])
    before=review.db.all("SELECT id FROM processing_tasks WHERE kind='library_import'")
    publisher.library_import.start(str(old),False)
    assert review.db.all("SELECT id FROM processing_tasks WHERE kind='library_import'")==before


def test_import_consolidates_an_existing_reviewed_recording_without_faking_reviews(publication,tmp_path):
    review,prep,publisher,store,gid,source,raw=publication
    old=tmp_path/'porto-showcase';old.mkdir();copy=old/'same-song.flac';shutil.copyfile(source,copy);copy.with_suffix('.yaml').write_bytes(raw)
    previous_audio=source.read_bytes();reviews=review.db.all('SELECT * FROM reviews')
    publisher.library_import.start(str(old),False);state=wait_import(publisher)
    assert state['status']=='done',state
    assert state['items'][0]['result']['generation_id']==gid
    assert not source.exists() and not copy.exists()
    assert len(list(prep.showcase.glob('**/_source/*.flac')))==1
    # Private legacy tags remain with the original. The public FLAC is the
    # metadata-stripped asset, not a second working/source copy.
    public=[Path(f['path']) for f in publisher.library.record(gid)['recipe']['files'] if f['content_type']=='audio/flac']
    assert len(public)==1 and b'PRIVATE_APP_PAYLOAD' not in public[0].read_bytes()
    assert prep.source(gid)[1].read_bytes()==previous_audio
    assert review.db.all('SELECT * FROM reviews')==reviews
    assert not review.db.all('PRAGMA foreign_key_check')


def test_import_aligns_r2_without_shipping_private_audio_metadata(publication,tmp_path):
    from mutagen.flac import FLAC,MetadataBlock
    from mutagen.mp3 import MP3
    from mutagen.id3 import TXXX
    review,prep,publisher,store,_,_,_=publication
    old=tmp_path/'old-public';flac=old/'one'/'one.flac';mp3=old/'two'/'two.mp3'
    audio(flac,1050);audio(mp3,1150)
    tags=FLAC(flac);tags['workflow']='PRIVATE_LEGACY_WORKFLOW'
    block=MetadataBlock(b'PRIVPRIVATE_LEGACY_APPLICATION');block.code=2;tags.metadata_blocks.append(block);tags.save()
    tags=MP3(mp3)
    if tags.tags is None:tags.add_tags()
    tags.tags.add(TXXX(encoding=3,desc='workflow',text='PRIVATE_LEGACY_MP3'));tags.save()
    original_flac=flac.read_bytes();original_mp3=mp3.read_bytes()
    for path in [flac,mp3]:path.with_suffix('.yaml').write_text('title: moses-supposes\nlyrics: old words\n',encoding='utf-8')
    reviews=review.db.all('SELECT * FROM reviews')
    publisher.library_import.start(str(old),True);state=wait_import(publisher)
    assert state['status']=='done',state
    for identifier in state['publications']:
        task=wait(review.db,identifier);assert task['status']=='done',task['error']
    assert len(json.loads(store.objects[POINTER])['tracks'])==2
    assert len(publisher.library.projects()['items'])==2
    assert all(b'PRIVATE_LEGACY' not in raw for raw in store.objects.values())
    assert not any(key.endswith(('.yaml','.generation.json')) for key in store.objects)
    retained=[prep.source(i['result']['generation_id'])[1].read_bytes() for i in state['items']]
    assert original_flac in retained and original_mp3 in retained
    assert review.db.all('SELECT * FROM reviews')==reviews and not review.db.all('PRAGMA foreign_key_check')


def test_interrupted_copy_resumes_and_bad_recordings_do_not_delete_good_sources(publication,tmp_path,monkeypatch):
    review,prep,publisher,_,_,_,_=publication
    old=tmp_path/'old';valid=old/'song'/'song.flac';audio(valid,1210);valid.with_suffix('.yaml').write_text('title: moses-supposes\nlyrics: test\n',encoding='utf-8')
    broken=old/'broken'/'bad.mp3';broken.parent.mkdir();broken.write_bytes(b'not audio')
    original=publisher.library_import.import_one;failed=True
    def interrupted(item,state):
        nonlocal failed
        result=original(item,state)
        if failed:failed=False;raise RuntimeError('Interrupted after DB commit')
        return result
    monkeypatch.setattr(publisher.library_import,'import_one',interrupted)
    publisher.library_import.start(str(old),False);first=wait_import(publisher)
    assert first['status']=='completed_with_issues' and valid.exists() and broken.exists()
    monkeypatch.setattr(publisher.library_import,'import_one',original)
    publisher.library_import.start(str(old),False);second=wait_import(publisher)
    assert second['completed']==1 and second['failed']==1
    assert not valid.exists() and broken.exists()
    assert review.db.one("SELECT COUNT(*) n FROM processing_tasks WHERE kind='library_import'")['n']==1


def test_import_refuses_writing_and_new_showcase_paths(publication):
    _,_,publisher,_,_,_,_=publication
    for directory in ['_01_writing','_02_generation','_05_showcase']:
        path=publisher.root/directory;path.mkdir(exist_ok=True)
        with pytest.raises(ValueError):publisher.library_import.discover(path)


def test_locked_legacy_source_cleanup_finishes_on_resume(publication,tmp_path,monkeypatch):
    review,_,publisher,_,_,_,_=publication
    old=tmp_path/'locked-showcase';source=old/'song'/'song.flac';audio(source,1410)
    source.with_suffix('.yaml').write_text('title: moses-supposes\nlyrics: test\n',encoding='utf-8')
    unlink=Path.unlink
    def locked(path,*args,**kwargs):
        if path==source:raise PermissionError('Simulated Windows file lock')
        return unlink(path,*args,**kwargs)
    monkeypatch.setattr(Path,'unlink',locked)
    publisher.library_import.start(str(old),False);first=wait_import(publisher)
    assert first['status']=='completed_with_issues' and first['items'][0]['cleanup_pending'] and source.exists()
    monkeypatch.setattr(Path,'unlink',unlink)
    publisher.library_import.start(str(old),False);second=wait_import(publisher)
    assert second['status']=='done' and not second['items'][0].get('cleanup_pending') and not source.exists()
    assert review.db.one("SELECT COUNT(*) n FROM processing_tasks WHERE kind='library_import'")['n']==1
