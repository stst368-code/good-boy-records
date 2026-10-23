"""Worker scratch files must not outrank completed recipes in review/archive."""
import json
import shutil
import numpy as np
import pytest
import soundfile as sf
from _review.metadata_identity import worker_intermediate
from conftest import track,complete,archive_all


@pytest.mark.parametrize('field',['style','style_primary'])
def test_current_style_fields_drive_review_and_filtered_counts(service,field):
    _,aid,gid=track(service,metadata='title: moses-supposes\n'+field+': New Age\ncluster: Electronic\n')
    assert service.public_track(gid)['genre']=='New Age'
    with service.db.transaction() as c:
        session=service.queues.session(c,service.default_reviewer_id)
        session['filters_json']=json.dumps({'genre':'New Age'})
        assert len(service.queues.candidates(c,session))==1
        assert service.queues.count_scope(c,session)['remaining']==1


def test_broad_and_direct_runtime_inputs_never_index_worker_audio(service):
    published,aid,gid=track(service,'moses-supposes',metadata='song_id: 13\ntitle: moses-supposes\nstyle: New Age\ncluster: Electronic\n')
    scratch=service.config.inputs[0]/'_02_generation/_runtime/batches/b/attempts/a/audio.flac'
    scratch.parent.mkdir(parents=True);shutil.copy2(published,scratch)
    assert service.indexer.scan()==1
    with pytest.raises(ValueError,match='scratch audio'):service.indexer.index(scratch)
    service.config.values['input_directories']=[str(scratch.parent)]
    assert service.indexer.scan()==0
    assert len(service.db.all('SELECT * FROM generations'))==1 and scratch.exists()
    assert worker_intermediate(r'E:\good-boy-records\_02_Generation\_runtime\attempts\audio.flac')
    assert not worker_intermediate(r'E:\good-boy-records\_02_generation\_generated\song\song.flac')
    assert not worker_intermediate(r'E:\good-boy-records\_02_generation\_runtime\local_test\batches\b\outputs\song\song.flac')
    assert worker_intermediate(r'E:\good-boy-records\_02_generation\_runtime\batches\b\attempts\a\outputs\audio.flac')


def test_prior_scratch_identity_displays_and_archives_with_published_title_without_rewriting_reviews(service):
    raw=service.config.inputs[0]/'audio.flac'
    sf.write(raw,np.sin(np.arange(16000)*.04)*.1,16000,subtype='PCM_24')
    aid=service.indexer.index(raw)
    old=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
    before=service.db.one('SELECT * FROM generation_metadata WHERE generation_id=?',(old,))
    scratch=raw.parent/'_02_generation/_runtime/batches/b/attempts/a/audio.flac'
    scratch.parent.mkdir(parents=True);raw.rename(scratch);raw=scratch
    service.db.execute('UPDATE generations SET original_directory=? WHERE id=?',(str(raw.parent),old))
    service.db.execute('UPDATE media_files SET path=?,original_path=? WHERE audio_id=?',(str(raw),str(raw),aid))
    final=service.config.inputs[0]/'moses-supposes-new-age-0002.flac';shutil.copy2(raw,final)
    yaml=final.with_suffix('.yaml');yaml.write_text('song_id: 13\nvariant_id: 2\ntitle: moses-supposes\nstyle: New Age\ncluster: Electronic\n')
    original=yaml.read_bytes();assert service.indexer.index(final)==aid
    assert service.archiver.available(aid)==final
    selected=service.public_track(old)
    assert selected['title']=='moses-supposes' and selected['genre']=='New Age' and selected['cluster']=='Electronic'
    assert service.db.one('SELECT * FROM generation_metadata WHERE generation_id=?',(old,))==before
    with service.db.transaction() as c:
        session=service.queues.session(c,service.default_reviewer_id)
        session['filters_json']=json.dumps({'genre':'New Age'})
        candidates=service.queues.candidates(c,session)
    assert len(candidates)==1 and candidates[0]['id']!=old
    service.db.execute('UPDATE audio SET archive_stem=? WHERE id=?',('audio__'+aid[:8],aid))
    reviewed=complete(service,old);archive_all(service)
    archived=service.db.one('SELECT archive_path FROM audio WHERE id=?',(aid,))['archive_path']
    assert 'moses-supposes__' in archived
    assert service.db.one('SELECT generation_id FROM reviews WHERE id=?',(reviewed['review_id'],))['generation_id']==old
    assert service.public_track(old)['title']=='moses-supposes'
    from pathlib import Path
    assert any(p.read_bytes()==original for p in Path(archived).parent.glob('*.yaml'))
    assert yaml.exists() is False
    assert raw.is_file()  # Worker ownership/cleanup never belongs to Review.
    # Reproduce an already-archived placeholder name from an older app. The
    # next scan journals a rename, without creating a review or editing YAML.
    archived=Path(archived);old_name='audio__'+aid[:8];bad=archived.with_name(old_name+'.flac')
    archived.rename(bad)
    sidecar=archived.with_suffix('.yaml');bad_yaml=bad.with_suffix('.yaml');sidecar.rename(bad_yaml)
    service.db.execute('UPDATE audio SET archive_path=?,archive_stem=? WHERE id=?',(str(bad),old_name,aid))
    service.db.execute('UPDATE media_files SET path=? WHERE path=?',(str(bad),str(archived)))
    service.db.execute('UPDATE generation_metadata SET yaml_archive_path=? WHERE yaml_archive_path=?',(str(bad_yaml),str(sidecar)))
    reviews=service.db.all('SELECT * FROM reviews');history=service.db.all('SELECT * FROM review_history')
    archive_setting=service.config.values['archive_directory']
    service.config.values['archive_directory']='another-archive'
    service.indexer.repair_archive_names()
    assert not service.db.all("SELECT * FROM archive_operations WHERE status='pending'")
    service.config.values['archive_directory']=archive_setting
    assert service.indexer.scan()==0;assert service.indexer.scan()==0
    assert len(service.db.all("SELECT * FROM archive_operations WHERE status='pending'"))==1
    archive_all(service)
    fixed=Path(service.db.one('SELECT archive_path FROM audio WHERE id=?',(aid,))['archive_path'])
    assert fixed.name.startswith('moses-supposes__') and fixed.is_file() and not bad.exists()
    assert fixed.with_suffix('.yaml').read_bytes()==original
    assert service.db.all('SELECT * FROM reviews')==reviews and service.db.all('SELECT * FROM review_history')==history
    assert raw.is_file()
