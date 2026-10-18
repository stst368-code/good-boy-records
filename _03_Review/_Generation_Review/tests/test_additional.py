import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient
from gbr_review.app import create_app
from gbr_review.config import Config, password_hash
from gbr_review.analysis import measure
from gbr_review.exports import dataset
from conftest import track,complete,archive_all


def test_transfer_between_distinct_filesystems(service):
    if not Path('/dev/shm').is_dir() or Path('/dev/shm').stat().st_dev==service.config.root.stat().st_dev:
        pytest.skip('A second writable filesystem is unavailable')
    with tempfile.TemporaryDirectory(prefix='gbr-review-test-',dir='/dev/shm') as tmp:
        service.config.values['archive_directory']=tmp
        path,aid,gid=track(service)
        audio_bytes=path.read_bytes();yaml_bytes=path.with_suffix('.yaml').read_bytes()
        complete(service,gid);archive_all(service)
        audio=Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
        yaml=Path(service.db.one('SELECT yaml_archive_path FROM generation_metadata')['yaml_archive_path'])
        assert audio.read_bytes()==audio_bytes and yaml.read_bytes()==yaml_bytes
        assert not path.exists()


def test_lan_requires_auth_and_cannot_edit_host_directories(service):
    path=service.config.path
    values={**service.config.values,'host':'0.0.0.0','password_hash':password_hash('password-long-enough')}
    path.write_text(json.dumps(values));app=create_app(path,False)
    with TestClient(app,headers={'X-GBR-Reviewer':app.state.service.default_reviewer_id},base_url='http://localhost',client=('192.0.2.25',1234)) as c:
        assert c.get('/api/queue/next').status_code==401
        assert c.post('/api/login',headers={'X-GBR-Request':'1'},json={'password':'password-long-enough'}).status_code==200
        assert c.get('/api/queue/next').status_code==200
        assert c.get('/api/settings').status_code==403
        assert c.get('/api/diagnostics').status_code==403
        app.state.service.config.values['password_hash']=password_hash('changed-password-long')
        assert c.get('/api/queue/next').status_code==401


def test_password_is_mandatory_before_binding_lan(tmp_path):
    path=tmp_path/'config.json';path.write_text(json.dumps({'host':'0.0.0.0'}))
    with pytest.raises(ValueError,match='requires a password'):Config(path)
    editable=Config(path,allow_insecure_lan=True)
    editable.values['password_hash']=password_hash('valid-new-password')
    editable.validate();editable.save()
    assert Config(path).values['password_hash']


def test_high_frequency_and_anti_phase_measurements(tmp_path):
    sr=48000;t=np.arange(sr*2)/sr;signal=.2*np.sin(2*np.pi*19000*t)
    path=tmp_path/'high.flac';sf.write(path,signal,sr,subtype='PCM_24')
    metrics,events,preview=measure(path,tmp_path/'high-cache')
    assert metrics['spectral_centroid_hz']>18000
    assert preview['frequency_axis']['max_hz']==20000
    sf.write(path,np.stack([signal,-signal],axis=1),sr,subtype='PCM_24')
    metrics,events,preview=measure(path,tmp_path/'phase-cache')
    assert metrics['stereo_correlation']<-.99
    assert any(e['type']=='possible_phase_cancellation' for e in events)


def test_duplicate_added_after_review_is_archived_without_review(service):
    _,aid,gid=track(service,frequency=333);complete(service,gid);archive_all(service)
    new_path,aid2,gid2=track(service,'new-location',frequency=333,metadata='title: song\ngenre: Chiptune\nencoder_cfg: 1.9\n')
    assert aid==aid2
    archive_all(service)
    assert not new_path.exists()
    assert len(list(service.config.archive.rglob('*.flac')))==1
    assert len(list(service.config.archive.rglob('*.yaml')))==2
    assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n']==1


def test_journal_resume_after_source_deleted(service):
    path,aid,gid=track(service);complete(service,gid)
    op=service.db.one('SELECT * FROM archive_operations');plan=service.archiver.plan(op)
    from gbr_review.archive import verified_copy
    for item in plan['items']:verified_copy(item['source'],item['destination'],item['sha256'],op['id'])
    path.unlink();path.with_suffix('.yaml').unlink()
    service.db.execute("UPDATE archive_operations SET status='running',plan_json=? WHERE id=?",(json.dumps(plan),op['id']))
    service.archiver.reconcile();archive_all(service)
    assert service.db.one('SELECT status FROM archive_operations')['status']=='done'


def test_deliberate_reassessment_is_distinct_from_correction(service):
    _,aid,gid=track(service);r=complete(service,gid)
    service.review(gid,{'audio_quality':4,'composition':4,'consistency':4,'realism':4},'full',r['review_id'],reassessment=True)
    assert service.db.all('SELECT action FROM review_history ORDER BY id')[-1]['action']=='reassessment'


def test_event_and_review_history_exports(service,tmp_path):
    _,aid,gid=track(service);service.analyzer.run(aid);r=complete(service,gid);complete(service,gid,(4,4,4,4),r['review_id'])
    for kind in ('reviews','events','review_history','segments','analyses','preferences'):
        path=dataset(service.db,tmp_path/(kind+'.json'),'json',kind)
        assert kind in json.loads(path.read_text())


def test_process_lock_prevents_second_worker(service):
    from gbr_review.__main__ import ProcessLock
    path=service.config.data/'application.lock';lock=ProcessLock(path)
    try:
        package = 'gbr_review' if (Path(__file__).resolve().parents[1] / 'gbr_review').is_dir() else 'review'
        code='from '+package+'.__main__ import ProcessLock;from pathlib import Path;ProcessLock(Path('+repr(str(path))+'))'
        p=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
        assert p.returncode!=0 and 'already running' in p.stderr
    finally:lock.close()
    second=ProcessLock(path);second.close()
