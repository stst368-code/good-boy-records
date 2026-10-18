import json
from pathlib import Path
import numpy as np
import pytest
import soundfile as sf
import pyloudnorm as pyln
from fastapi.testclient import TestClient
from gbr_review.app import create_app
from gbr_review.config import password_hash
from gbr_review.analysis import measure,select_segments
from gbr_review.exports import dataset,publishing_manifest,validation
from conftest import track,complete,archive_all


def test_signal_analysis_snippets_and_versions(service):
    path,aid,gid=track(service,seconds=70,stereo=True)
    sr=16000;t=np.arange(sr*70)/sr
    signal=.13*(1+.6*np.sin(2*np.pi*t/16))*np.sin(2*np.pi*330*t)
    signal[sr*30:sr*32]=0
    sf.write(path,np.stack([signal,signal*.9],axis=1),sr,subtype='PCM_24')
    aid=service.indexer.index(path);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id']
    service.analyzer.run(aid)
    an=service.db.one('SELECT * FROM analyses WHERE audio_id=?',(aid,));metrics=json.loads(an['metrics_json'])
    raw,sample_rate=sf.read(path,always_2d=True)
    reference=pyln.Meter(sample_rate).integrated_loudness(raw)
    assert abs(metrics['integrated_lufs']-reference)<.1
    events=service.db.all('SELECT * FROM audio_events WHERE audio_id=?',(aid,))
    assert any(e['type']=='possible_dropout' for e in events)
    for mode,target in [('quick',20),('standard',50)]:
        segments=service.db.all('SELECT * FROM segments WHERE analysis_id=? AND mode=? ORDER BY ordinal',(an['id'],mode))
        assert abs(sum(s['end']-s['start'] for s in segments)-target)<.01
        assert all(a['end']<=b['start'] for a,b in zip(segments,segments[1:]))
        rendered=service.playback.render(gid,mode,'wav')
        assert abs(sf.info(rendered).duration-target)<.01
    service.analyzer.run(aid)
    assert len(service.db.all('SELECT * FROM analyses WHERE audio_id=?',(aid,)))==2


def test_silent_and_short_audio(service):
    path,aid,gid=track(service,seconds=1)
    sf.write(path,np.zeros(16000),16000,subtype='PCM_16');aid=service.indexer.index(path)
    service.analyzer.run(aid)
    analysis=service.db.one('SELECT * FROM analyses WHERE audio_id=?',(aid,))
    metrics=json.loads(analysis['metrics_json'])
    assert metrics['integrated_lufs'] is None
    preview=json.loads(Path(analysis['preview_path']).read_text())
    assert select_segments(preview,[],20,'quick')==[{'start':0.,'end':1.,'reason':'complete_short_recording'}]


def test_full_and_range_playback_preserve_sample_rate(service):
    path,aid,gid=track(service,seconds=5)
    assert service.playback.render(gid,'full')==path
    wav=service.playback.render(gid,'full','wav')
    assert sf.info(wav).samplerate==16000
    assert np.array_equal(sf.read(path,dtype='int32')[0],sf.read(wav,dtype='int32')[0])
    snippet=service.playback.render(gid,'range','wav',1,2)
    raw=sf.read(snippet)[0]
    assert len(raw)==16000 and raw[0]==0 and raw[-1]==0
    with pytest.raises(ValueError):service.playback.render(gid,'range','wav',2,1)


def test_loudness_matched_blind_comparison(service):
    p,aid,g1=track(service,'a',seconds=5,frequency=220,metadata='title: Same song\ngenre: Chiptune\n')
    p2,aid2,g2=track(service,'b',seconds=5,frequency=440,metadata='title: Same song\ngenre: Chiptune\n')
    service.analyzer.run(aid);service.analyzer.run(aid2)
    result=service.comparison(g1,g2);cmp=service.comparisons[result['id']]
    outputs=[service.playback.render(cmp[s],'full','wav',match_lufs=cmp['target']) for s in ('a','b')]
    levels=[pyln.Meter(16000).integrated_loudness(sf.read(p)[0]) for p in outputs]
    assert abs(levels[0]-levels[1])<.1
    assert max(np.abs(sf.read(p)[0]).max() for p in outputs)<1
    with pytest.raises(ValueError):service.comparison(g1,g1)


@pytest.fixture
def client(service):
    app=create_app(service.config.path,False)
    with TestClient(app,headers={'X-GBR-Reviewer':app.state.service.default_reviewer_id},base_url='http://localhost') as client:
        yield client,app.state.service


def test_api_no_bias_data_and_range_requests(client):
    c,s=client;path,aid,gid=track(s)
    s.analyzer.run(aid)
    data=c.get('/api/queue/next').json()['track']
    assert data['genre']=='Chiptune'
    assert not {'sampler','scheduler','average','bucket','scores','model'}&set(data)
    preview=c.get(f'/api/tracks/{gid}/preview').json()
    assert all(set(m)=={'id','start','end'} for m in preview['markers'])
    audio=c.get(f'/api/playback/{gid}',headers={'Range':'bytes=0-9'})
    assert audio.status_code==206 and len(audio.content)==10
    response=c.post(f'/api/tracks/{gid}/review',headers={'X-GBR-Request':'1'},json={'audio_quality':5,'composition':4,'consistency':4,'realism':5,'mode':'quick'})
    assert response.status_code==200 and 'bucket' not in response.json()
    back=c.get('/api/queue/back').json()['track']
    assert back['scores']['realism']==5 and 'average' not in back
    analysis=s.db.one('SELECT * FROM analyses WHERE audio_id=?',(aid,))
    Path(analysis['preview_path']).unlink()
    assert c.get(f'/api/tracks/{gid}/preview').json()=={'ready':False}
    assert s.db.one('SELECT analysis_state FROM audio WHERE id=?',(aid,))['analysis_state']=='pending'


def test_auth_csrf_host_and_validation(client):
    c,s=client;_,_,gid=track(s)
    body={'audio_quality':5,'composition':4,'consistency':4,'realism':5,'mode':'quick'}
    assert c.post(f'/api/tracks/{gid}/review',json=body).status_code==403
    assert c.post('/api/scan',json={},headers={'X-GBR-Request':'1','Origin':'https://evil.example'}).status_code==403
    assert c.get('/api/queue/next',headers={'Host':'evil.example'}).status_code==400
    assert c.post(f'/api/tracks/{gid}/review',json={**body,'realism':True},headers={'X-GBR-Request':'1'}).status_code==422
    assert c.post(f'/api/tracks/{gid}/review',json={**body,'realism':6},headers={'X-GBR-Request':'1'}).status_code==422
    s.config.values['password_hash']=password_hash('test-password-long')
    assert c.get('/api/queue/next').status_code==401
    assert c.post('/api/login',json={'password':'wrong'},headers={'X-GBR-Request':'1'}).status_code==401
    assert c.post('/api/login',json={'password':'test-password-long'},headers={'X-GBR-Request':'1'}).status_code==200
    assert c.get('/api/queue/next').status_code==200


def test_exports_and_manifest(service,tmp_path):
    _,aid,gid=track(service);service.analyzer.run(aid);complete(service,gid);archive_all(service);service.publishing(gid,'selected')
    for format in ('csv','parquet','json'):
        target=dataset(service.db,tmp_path/f'export.{format}',format)
        assert target.stat().st_size>0
    manifest=publishing_manifest(service.db)
    assert manifest['tracks'][0]['yaml_path'] and manifest['tracks'][0]['audio_path']
    assert validation(service.db,service.archiver)['foreign_key_errors']==[]


def test_settings_restrict_paths_and_archive_changes(client):
    c,s=client;_,_,gid=track(s);complete(s,gid);archive_all(s)
    current=c.get('/api/settings').json()
    body={k:current[k] for k in ('input_directories','archive_directory','quick_seconds','standard_seconds','scan_interval_seconds','fade_ms')}
    r=c.post('/api/settings',headers={'X-GBR-Request':'1'},json={**body,'archive_directory':'another-archive'})
    assert r.status_code==400
    invalid={**body,'input_directories':[str(s.config.archive/'inside')]}
    assert c.post('/api/settings',headers={'X-GBR-Request':'1'},json=invalid).status_code==400
    assert c.get('/api/settings').json()['input_directories']==current['input_directories']
    r=c.post('/api/queue',headers={'X-GBR-Request':'1'},json={'directory':'/unconfigured'})
    assert r.status_code==400


def test_all_bucket_selection(client):
    c,s=client;_,_,gid=track(s);complete(s,gid)
    assert c.get('/api/publishing?bucket=').status_code==200
    assert len(c.get('/api/publishing?bucket=').json()['items'])==1
