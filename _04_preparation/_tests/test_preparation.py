from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import time
import numpy as np
import pytest
import soundfile as sf

ROOT=Path(__file__).resolve().parents[2]
for path in [ROOT,ROOT/'_04_preparation',ROOT/'_98_database',ROOT/'_03_review/_generation_review',ROOT/'_03_review/_generation_review/_tests']:sys.path.insert(0,str(path))
from _gbr_database.initialize import initialize
from _gbr_preparation.service import Preparation,digest,mix_recipe
from _review.config import Config
from _review.service import Service
from conftest import complete,archive_all

@pytest.fixture
def prepared(tmp_path):
    root=tmp_path/'repo'
    for relative in ['_98_database/_gbr_database/processing.sql','_99_tools/_workers_processing/worker.py']:
        path=root/relative;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/relative,path)
    database=tmp_path/'shared.sqlite3';initialize(database)
    inp=tmp_path/'input';inp.mkdir()
    config=tmp_path/'config.json';config.write_text(json.dumps({'database_path':str(database),'input_directories':[str(inp)]}))
    service=Service(Config(config));prep=Preparation(root,service)
    path=inp/'track.flac';t=np.arange(16000*4)/16000
    sf.write(path,.2*np.sin(2*np.pi*330*t),16000,subtype='PCM_24')
    raw=b'\xef\xbb\xbfsong_id: 13\r\nvariant_id: 2\r\ntitle: A test song\r\ncluster: Rock\r\nstyle: Punk\r\nlyrics: |\r\n  first line\r\n  second line\r\n'
    path.with_suffix('.yaml').write_bytes(raw)
    aid=service.indexer.index(path);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
    yield service,prep,path,aid,gid,raw
    service.close()


def promote(data):
    service,prep,path,aid,gid,raw=data
    service.set_flags(gid,service.default_reviewer_id,distribution=True)
    complete(service,gid)
    archive_all(service)
    return prep.source(gid)[1]


def wait(prep,identifier):
    limit=time.monotonic()+25
    while time.monotonic()<limit:
        row=prep.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
        if row['status'] in ('done','failed'):
            assert row['status']=='done',row['error']
            return row
        time.sleep(.03)
    raise AssertionError('Preparation job timed out')


def test_selected_requires_completed_review_then_moves_once_and_preserves_recipe(prepared):
    service,prep,path,aid,gid,raw=prepared
    service.set_flags(gid,service.default_reviewer_id,distribution=True)
    assert not service.db.one("SELECT 1 FROM archive_operations WHERE operation_type='prepare'")
    complete(service,gid);archive_all(service)
    source=prep.source(gid)[1]
    assert source.is_relative_to(prep.tracks)
    assert not path.exists()
    assert len(list(source.parent.glob('*.yaml')))==1
    assert next(source.parent.glob('*.yaml')).read_bytes()==raw
    prep.sync_selection();prep.sync_selection()
    assert service.db.one("SELECT COUNT(*) n FROM archive_operations WHERE operation_type='prepare'")['n']==1
    assert len(list(prep.root.rglob('*.flac')))==1
    assert prep.projects()['projects'][0]['ready']


def test_withdrawn_selection_cancels_pending_promotion(prepared):
    service,prep,path,aid,gid,raw=prepared
    service.set_flags(gid,service.default_reviewer_id,distribution=True)
    complete(service,gid)
    service.set_flags(gid,service.default_reviewer_id,distribution=False)
    archive_all(service)
    assert service.db.one("SELECT status FROM archive_operations WHERE operation_type='prepare'")['status']=='cancelled'
    assert not list(prep.tracks.rglob('*.flac'))


def test_correction_keeps_prepared_source_in_place(prepared):
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared);before=digest(source)
    previous=service.db.one('SELECT id FROM latest_reviews WHERE audio_id=?',(aid,))['id']
    complete(service,gid,(4,4,4,4),previous=previous);archive_all(service)
    assert prep.source(gid)[1]==source
    assert digest(source)==before


def test_real_preview_master_and_eviction_preserve_source(prepared):
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared);before=digest(source)
    first=prep.submit(gid,'mix_preview',{'mix':{'gain_db':-6},'start':1,'seconds':1});wait(prep,first['id'])
    row=prep.tasks(gid)[0];artifact=row['artifacts'][0]
    clip,rate=sf.read(prep.artifact(artifact['id']))
    assert abs(len(clip)/rate-1)<.01
    assert abs(np.sqrt(np.mean(clip**2))-(.2/np.sqrt(2)*10**(-6/20)))<.001
    assert prep.submit(gid,'mix_preview',{'mix':{'gain_db':-6},'start':1,'seconds':1})['id']==first['id']
    master=prep.submit(gid,'mix_master',{'mix':{'gain_db':-3}});wait(prep,master['id'])
    newer=prep.submit(gid,'mix_preview',{'mix':{'gain_db':-12},'start':0,'seconds':1});wait(prep,newer['id'])
    assert not prep.db.one('SELECT uri FROM generation_artifacts WHERE id=?',(first['id']+'-output',)) is None
    assert not artifact['id']==newer['id']+'-output'
    assert len(list((source.parent.parent/'_derived').glob('*.wav')))==1
    assert len(list((source.parent.parent/'_derived').glob('*.flac')))==1
    assert digest(source)==before
    assert not list(prep.root.rglob('.gbr-processing-*'))


def test_convolution_limiter_and_whole_track_fade_do_not_move_audio(prepared):
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared)
    result=prep.submit(gid,'mix_preview',{'mix':{'reverb':.2,'limiter_db':-1,'fade_in':.25,'fade_out':.5},'start':1,'seconds':1})
    row=wait(prep,result['id']);metrics=json.loads(row['telemetry_json'])['measurements']
    assert metrics['duration']==pytest.approx(1,abs=.01)
    assert metrics['sample_peak_dbfs']<=-1+.05
    audio,_=sf.read(prep.artifact(result['id']+'-output'))
    assert np.sqrt(np.mean(audio[:800]**2))>.03 # Fade-in was at source start, not at clip start.


def test_artwork_brief_is_repeatable_and_references_are_content_bound(prepared):
    service,prep,path,aid,gid,raw=prepared
    promote(prepared)
    folder=prep.references/'_global';folder.mkdir(parents=True)
    from PIL import Image
    Image.new('RGB',(4,4),'red').save(folder/'example.png')
    refs=prep.reference_list();assert len(refs)==1
    data={'references':[refs[0]['id']],'direction':'Woodcut in cobalt and gold'}
    first=prep.artwork(gid,data);second=prep.artwork(gid,data)
    assert first['id']==second['id']
    assert first['status']=='brief_only'
    assert 'first line\nsecond line' in first['prompt']
    assert 'Woodcut' in first['prompt']
    assert next(prep.source(gid)[1].parent.glob('*.yaml')).read_bytes()==raw
    Image.new('RGB',(4,4),'blue').save(folder/'example.png')
    with pytest.raises(ValueError,match='missing or changed'):prep.artwork(gid,data)


def test_visual_mapping_has_source_identity_and_missing_models_are_not_faked(prepared):
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared)
    result=prep.submit(gid,'visuals',{});wait(prep,result['id'])
    data=json.loads(prep.artifact(result['id']+'-output').read_text())
    assert data['source_sha256']==digest(source)
    assert len(data['frames'])==40
    assert all(0<=frame['energy']<=1 for frame in data['frames'])
    with pytest.raises(ValueError,match='not installed'):prep.submit(gid,'artwork',{})


def test_one_idempotent_shared_ledger_and_invalid_controls(prepared):
    service,prep,path,aid,gid,raw=prepared
    before=service.db.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
    Preparation(prep.root,service).close()
    assert service.db.all("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")==before
    assert service.db.path==service.config.database
    for value in [{'gain_db':float('nan')},{'limiter_db':True},{'unknown':1}]:
        with pytest.raises(ValueError):mix_recipe(value)


def test_r2_plan_deduplicates_and_detects_changed_artifacts_without_writes(prepared):
    from _gbr_preparation.storage_plan import plan
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared);sha=digest(source)
    for identifier in ['one','two']:
        service.db.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(identifier,'audio',sha,str(source),None if identifier=='one' else source.stat().st_size,'{}'))
    before=hashlib.sha256(service.db.path.read_bytes()).hexdigest()
    data=plan(service.db)
    assert data['dry_run'] and len(data['private_objects'])==2 # Audio + canonical recipe.
    audio_object=next(o for o in data['private_objects'] if o['sha256']==sha)
    assert {'one','two'}<=set(audio_object['artifact_ids'])
    assert any(ref.startswith('media:') for ref in audio_object['artifact_ids'])
    assert audio_object['key'].endswith(sha)
    assert hashlib.sha256(service.db.path.read_bytes()).hexdigest()==before
    service.db.execute("UPDATE generation_artifacts SET size_bytes=0 WHERE id='two'")
    assert plan(service.db)['conflicts'][0]['artifact_id']=='two'


def test_recovery_keeps_history_and_blocks_live_or_unknown_controller(prepared):
    import socket,os,psutil
    from _gbr_preparation.service import canonical
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared)
    recipe={'mix':mix_recipe({}),'start':0,'seconds':1}
    identifier=prep._insert(gid,'mix_preview',recipe,digest(source))
    with pytest.raises(ValueError,match='live controller'):prep.retry(identifier)
    service.db.execute("UPDATE processing_tasks SET telemetry_json='{}' WHERE id=?",(identifier,))
    with pytest.raises(ValueError,match='unknown'):prep.retry(identifier)
    service.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical({'owner':{'pid':999999999,'created_at':0,'host':socket.gethostname()}}),identifier))
    retried=prep.retry(identifier);wait(prep,retried['id'])
    assert retried['id']!=identifier
    assert service.db.one('SELECT status FROM processing_tasks WHERE id=?',(identifier,))['status']=='interrupted'
    assert len(prep.tasks(gid))==2


def test_original_writing_yaml_is_copied_but_never_removed(prepared,monkeypatch):
    import _review.archive as archive_module
    service,prep,path,aid,gid,raw=prepared
    monkeypatch.setattr(archive_module,'__file__',str(prep.root/'_03_review/_generation_review/_review/archive.py'))
    writing=prep.root/'_01_writing/_built/song.yaml';writing.parent.mkdir(parents=True);writing.write_bytes(raw)
    service.db.execute('UPDATE generation_sources SET yaml_path=? WHERE generation_id=?',(str(writing),gid))
    promote(prepared)
    assert writing.read_bytes()==raw
    assert next(prep.source(gid)[1].parent.glob('*.yaml')).read_bytes()==raw


def test_bypassed_master_reuses_canonical_pcm_without_a_duplicate_flac(prepared):
    service,prep,path,aid,gid,raw=prepared
    source=promote(prepared)
    result=prep.submit(gid,'mix_master',{'mix':{}});wait(prep,result['id'])
    assert prep.artifact(result['id']+'-output')==source
    assert len(list(prep.tracks.rglob('*.flac')))==1
    assert source.is_file()


def test_processing_history_download_is_complete_and_temporary_export_is_removed(prepared):
    from fastapi.testclient import TestClient
    from _review.app import create_app
    service,prep,path,aid,gid,raw=prepared
    promote(prepared)
    brief=prep.artwork(gid,{'direction':'Copper engraving'})
    before=service.db.all('SELECT * FROM processing_tasks')
    app=create_app(service.config.path,start_worker=False,database_path=service.db.path)
    with TestClient(app,base_url='http://localhost') as client:
        response=client.get('/api/export/json?kind=processing_tasks')
        assert response.status_code==200
        exported=response.json()['processing_tasks']
        assert exported==before
        assert exported[0]['id']==brief['id']
        assert 'Copper engraving' in json.loads(exported[0]['recipe_json'])['prompt']
        assert not list((app.state.service.config.data/'exports').glob('*'))
    assert service.db.all('SELECT * FROM processing_tasks')==before
