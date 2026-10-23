from contextlib import closing
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
import sys
import time

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC,MetadataBlock

ROOT=Path(__file__).resolve().parents[2]
for p in [ROOT,ROOT/'_04_preparation',ROOT/'_98_database',ROOT/'_03_review/_generation_review']:sys.path.insert(0,str(p))
from _gbr_database.initialize import initialize
from _gbr_preparation.service import Preparation,digest
from _gbr_preparation.publishing import Publisher,R2,POINTER,encoded
from _review.config import Config
from _review.service import Service
from _review.indexer import audio_identity


class Store:
    def __init__(self):self.objects={};self.fail_key=None;self.unknown=False;self.uploads=[];self.checked=[];self.conflict=False
    def read(self,key):
        raw=self.objects.get(key);return raw,hashlib.md5(raw).hexdigest() if raw is not None else None
    def compare_put(self,key,raw,etag=None,**kwargs):
        current,actual=self.read(key)
        if self.conflict and key==POINTER:raise ValueError('concurrent publisher changed catalogue')
        if (etag is None and current is not None) or (etag is not None and etag!=actual):raise ValueError('compare-and-swap failed')
        self.objects[key]=raw
        if self.unknown and key==POINTER:self.unknown=False;raise TimeoutError('unknown outcome')
    def upload(self,item):
        if self.fail_key and self.fail_key in item['key']:raise ValueError('simulated upload failure')
        raw=Path(item['path']).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=item['sha256']:raise ValueError('asset changed')
        if item['key'] in self.objects and self.objects[item['key']]!=raw:raise ValueError('object conflict')
        self.objects[item['key']]=raw;self.uploads.append(item['key'])
    def public_check(self,url,origin,expected=None):self.checked.append((url,origin))


def wait(db,identifier):
    end=time.monotonic()+20
    while time.monotonic()<end:
        row=db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
        if row['status'] in {'done','failed'}:return row
        time.sleep(.025)
    raise AssertionError('Timed out')


@pytest.fixture
def publication(tmp_path):
    root=tmp_path/'repo';(root/'_98_database/_gbr_database').mkdir(parents=True)
    shutil.copyfile(ROOT/'_98_database/_gbr_database/processing.sql',root/'_98_database/_gbr_database/processing.sql')
    writing=root/'_01_writing/_built';writing.mkdir(parents=True)
    (writing/'track_list.csv').write_text('song_id,song_name,filename,album_name,album_position\n13,moses-supposes,song.yaml,Dog Days,2\n')
    database=tmp_path/'gbr.sqlite3';initialize(database)
    inp=tmp_path/'input';inp.mkdir()
    config=tmp_path/'config.json';config.write_text(json.dumps({'database_path':str(database),'input_directories':[str(inp)]}))
    review=Service(Config(config));prep=Preparation(root,review)
    audio=inp/'audio.flac';signal=.15*np.sin(2*np.pi*330*np.arange(44100)/44100)
    sf.write(audio,np.stack([signal,signal],axis=1),44100,subtype='PCM_24')
    tags=FLAC(audio);tags['GBR_METADATA']='private endpoint ssh://private';tags['COMMENT']='PRIVATE_REASON'
    application=MetadataBlock(b'TESTPRIVATE_APP_PAYLOAD');application.code=2;tags.metadata_blocks.append(application);tags.save()
    yaml=b'song_id: 13\nvariant_id: 2\ntitle: wrong-suffixed-title\ncluster: Rock\nstyle: Punk\nlanguage: en\nexplicit: yes\nlyrics: |\n  first line\n  second line\n'
    audio.with_suffix('.yaml').write_bytes(yaml)
    aid=review.indexer.index(audio);gid=review.db.one('SELECT id FROM generations')['id']
    review.queues.open(review.default_reviewer_id,gid)
    review.review(gid,dict(audio_quality=5,composition=4,consistency=4,realism=4),'full',distribution=True,reason='PRIVATE_REASON',tags=[{'tag_id':'vocal_tone','polarity':'positive'}])
    for op in review.db.all("SELECT * FROM archive_operations WHERE status='pending'"):review.archiver.process(op)
    source=prep.source(gid)[1];store=Store();publisher=Publisher(root,review,prep,store_factory=lambda settings:store)
    publisher.save_settings({'endpoint':'https://test.r2.cloudflarestorage.com','bucket':'gbr-public','public_base':'https://media.test','website_url':'https://test.github.io/good-boy-records/'})
    yield review,prep,publisher,store,gid,source,yaml
    publisher.close();review.close()


def prepare(data,**extra):
    review,prep,publisher,store,gid,source,yaml=data
    identifier=publisher.start_prepare(gid,{'include_flac':True,**extra})['id'];row=wait(review.db,identifier)
    assert row['status']=='done',row['error']
    return identifier,json.loads(row['recipe_json'])


def test_real_web_package_strips_private_tags_preserves_source_and_pcm(publication):
    review,prep,publisher,store,gid,source,yaml=publication
    before=source.read_bytes();saved_reviews=review.db.all('SELECT * FROM reviews');saved_tags=review.db.all('SELECT * FROM review_tags')
    pid,recipe=prepare(publication)
    assert not store.objects
    assert recipe['track']['displayTitle']=='moses-supposes' and recipe['track']['album']['name']=='Dog Days'
    assert 'private' not in json.dumps(recipe['track']).lower() and 'yamlUrl' not in recipe['track']
    flac=next(Path(i['path']) for i in recipe['files'] if i['path'].endswith('.flac'))
    assert not FLAC(flac).tags and not FLAC(flac).pictures
    assert b'PRIVATE_APP_PAYLOAD' not in flac.read_bytes() and b'PRIVATE_APP_PAYLOAD' in source.read_bytes()
    assert audio_identity(flac)[0]==audio_identity(source)[0]
    assert source.read_bytes()==before and next(source.parent.glob('*.yaml')).read_bytes()==yaml
    for item in recipe['files']:assert digest(item['path'])==item['sha256']
    assert review.db.all('SELECT * FROM reviews')==saved_reviews and review.db.all('SELECT * FROM review_tags')==saved_tags
    assert not review.db.all('PRAGMA foreign_key_check')


def test_success_verified_publication_merges_tracks_and_removes_web_duplicates(publication):
    review,prep,publisher,store,gid,source,yaml=publication
    store.objects[POINTER]=encoded({'format':'gbr-public-releases-v1','tracks':[{'id':'existing','release_id':'previous'}]})
    pid,recipe=prepare(publication)
    job=publisher.start_publish(pid)['id'];row=wait(review.db,job);assert row['status']=='done',row['error']
    tracks=json.loads(store.objects[POINTER])['tracks'];assert len(tracks)==2 and tracks[0]['id']=='existing'
    assert len(store.checked)==len(recipe['files'])
    assert review.db.one('SELECT status FROM publishing_selection')['status']=='published'
    assert '?s=' in json.loads(row['telemetry_json'])['url']
    assert source.exists() and all(not Path(item['path']).exists() for item in recipe['files'])
    assert all(a['uri'].startswith('r2://') for a in review.db.all("SELECT uri FROM generation_artifacts WHERE kind='release_asset'"))
    retry=publisher.start_publish(pid)['id'];assert wait(review.db,retry)['status']=='done'
    assert len(json.loads(store.objects[POINTER])['tracks'])==2
    publisher.start_withdraw(pid)
    assert [t['id'] for t in json.loads(store.objects[POINTER])['tracks']]==['existing']
    assert review.db.one('SELECT status FROM publishing_selection')['status']=='withdrawn'
    assert len(store.objects)>1 # no object deletion on withdrawal


def test_failed_upload_does_not_advance_catalogue_or_publishing_state(publication):
    review,_,publisher,store,gid,source,_=publication
    original=encoded({'format':'gbr-public-releases-v1','tracks':[]});store.objects[POINTER]=original
    pid,recipe=prepare(publication);store.fail_key='.flac'
    row=wait(review.db,publisher.start_publish(pid)['id']);assert row['status']=='failed'
    assert store.objects[POINTER]==original and review.db.one('SELECT status FROM publishing_selection')['status']=='selected'
    assert source.exists() and all(Path(i['path']).exists() for i in recipe['files'])
    store.fail_key=None;assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'


def test_unknown_pointer_write_response_reconciles_without_reencoding(publication):
    review,_,publisher,store,_,_,_=publication
    pid,recipe=prepare(publication);store.unknown=True
    first=wait(review.db,publisher.start_publish(pid)['id']);assert first['status']=='failed'
    assert json.loads(store.objects[POINTER])['tracks'][0]['release_id']==pid
    uploads=len(store.uploads)
    second=wait(review.db,publisher.start_publish(pid)['id']);assert second['status']=='done'
    assert len(store.uploads)==uploads


@pytest.mark.parametrize('change',['source','yaml','selection','conflict'])
def test_stale_or_conflicting_releases_never_replace_public_catalogue(publication,change):
    review,_,publisher,store,gid,source,_=publication
    pid,recipe=prepare(publication)
    if change=='source':source.write_bytes(source.read_bytes()+b'changed')
    elif change=='yaml':review.db.execute("UPDATE generation_metadata SET yaml_sha256='changed' WHERE generation_id=?",(gid,))
    elif change=='selection':review.publishing(gid,'withdrawn')
    elif change=='conflict':store.conflict=True
    if change=='selection':
        with pytest.raises(ValueError):publisher.start_publish(pid)
    else:assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='failed'
    assert POINTER not in store.objects


def test_timing_approval_is_exact_audio_bound_and_preview_discard_removes_derivatives(publication):
    review,_,publisher,_,gid,source,_=publication
    row=wait(review.db,publisher.start_prepare(gid,{'timed_lyrics':True})['id'])
    assert row['status']=='failed' and 'Approve lyrics' in row['error']
    pid,recipe=prepare(publication,include_flac=False)
    assert len(recipe['files'])==1 and recipe['track']['audio']['sources']['flac'] is None
    publisher.discard(pid)
    assert source.exists() and all(not Path(i['path']).exists() for i in recipe['files'])


def test_credentials_never_returned_in_configuration_or_release(publication):
    _,_,publisher,_,_,_,_=publication
    publisher.save_settings({'access_key':'private-key','secret_key':'private-secret'})
    result=publisher.configuration();assert result['credentials_available']
    assert 'private-key' not in json.dumps(result) and 'private-secret' not in json.dumps(result)
    assert publisher.secrets_path.is_file()
    assert 'access_key' not in json.loads(publisher.config_path.read_text())
    with pytest.raises(ValueError):publisher.save_settings({'public_base':'https://key:secret@media.test'})


def test_check_probes_public_get_and_cors(publication):
    _,_,publisher,store,_,_,_=publication
    result=publisher.check();assert result['ready'] and store.checked[0][1]=='https://test.github.io'


def test_share_token_matches_existing_catalogue_algorithm():
    from _gbr_preparation.release_catalogue import share_token
    spec=importlib.util.spec_from_file_location('public_catalogue',ROOT/'_96_website/_tools/build_catalogue.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    assert share_token('gbr-13-2-abcdefgh','Punk')==module.share_token('gbr-13-2-abcdefgh','Punk')


def test_research_snapshot_uses_existing_public_projection_without_private_data(publication):
    review,_,publisher,store,_,source,_=publication
    publisher.root=ROOT
    result=publisher.publish_research()
    assert result['recordings']==1
    pointer=json.loads(store.objects['gbr/v2/research.json'])
    assert pointer['sha256']==result['sha256']
    raw=store.objects['gbr/v2/research/'+result['sha256']+'.json']
    assert b'PRIVATE_REASON' not in raw and str(source).encode() not in raw
    assert json.loads(raw)['review_model']=='four-metric-0-5'
    assert review.db.one("SELECT 1 found FROM review_history WHERE action='public_research_published'")['found']


def r2_store():
    return R2({'endpoint':'https://test.r2.cloudflarestorage.com','bucket':'gbr-public','profile':'',
               'access_key_env':'TEST_ONLY_GBR_KEY','secret_key_env':'TEST_ONLY_GBR_SECRET',
               '_access_key':'test-key','_secret_key':'test-secret'})


def test_sdk_uses_conditional_publication_and_exact_download_verification(tmp_path):
    from botocore.stub import Stubber
    from botocore.response import StreamingBody
    store=r2_store();raw=b'public audio';path=tmp_path/'audio.mp3';path.write_bytes(raw);sha=digest(path)
    item={'path':str(path),'key':'gbr/v2/objects/test.mp3','sha256':sha,'size':len(raw),'content_type':'audio/mpeg'}
    with Stubber(store.client) as stub:
        from botocore.stub import ANY
        stub.add_response('put_object',{}, {'Bucket':'gbr-public','Key':item['key'],'Body':ANY,'ContentLength':len(raw),
            'ContentType':'audio/mpeg','Metadata':{'sha256':sha},'CacheControl':'public,max-age=31536000,immutable','IfNoneMatch':'*'})
        stub.add_response('get_object',{'Body':StreamingBody(io.BytesIO(raw),len(raw))},{'Bucket':'gbr-public','Key':item['key']})
        store.upload(item)
        stub.add_response('put_object',{}, {'Bucket':'gbr-public','Key':POINTER,'Body':b'{}','ContentType':'application/json',
            'CacheControl':'no-cache,max-age=0,must-revalidate','IfMatch':'etag-1'})
        store.compare_put(POINTER,b'{}',etag='etag-1');stub.assert_no_pending_responses()


def test_existing_remote_object_is_not_trusted_without_checksum(tmp_path):
    from botocore.stub import Stubber
    from botocore.response import StreamingBody
    store=r2_store();path=tmp_path/'audio.mp3';path.write_bytes(b'correct')
    item={'path':str(path),'key':'gbr/v2/objects/test.mp3','sha256':digest(path),'size':7,'content_type':'audio/mpeg'}
    with Stubber(store.client) as stub:
        stub.add_client_error('put_object','PreconditionFailed',http_status_code=412)
        stub.add_response('get_object',{'Body':StreamingBody(io.BytesIO(b'corrupt'),7)},{'Bucket':'gbr-public','Key':item['key']})
        with pytest.raises(ValueError,match='checksum'):store.upload(item)


def test_app_publishing_settings_use_existing_csrf_and_host_protection(tmp_path):
    from fastapi.testclient import TestClient
    from _review.app import create_app
    config=tmp_path/'config.json';config.write_text('{}')
    app=create_app(config,start_worker=False)
    publisher=app.state.publisher;publisher.config_path=tmp_path/'settings.json';publisher.secrets_path=tmp_path/'secrets.json'
    body={'endpoint':'https://test.r2.cloudflarestorage.com','bucket':'gbr-public','public_base':'https://media.test',
          'website_url':'https://test.github.io/good-boy-records/','access_key':'private-key','secret_key':'private-secret'}
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.post('/api/releases/settings',json=body).status_code==403
        result=client.post('/api/releases/settings',json=body,headers={'x-gbr-request':'1'})
        assert result.status_code==200,result.text
        assert 'private-secret' not in result.text and 'private-key' not in result.text
        assert 'private-secret' not in client.get('/api/releases/settings').text
        assert client.post('/api/releases/missing/publish',json={},headers={'x-gbr-request':'1'}).status_code==400
    # A remote client cannot read host settings in a local-only installation.
    with TestClient(app,base_url='http://127.0.0.1',client=('203.0.113.9',1234)) as client:
        assert client.get('/api/releases/settings').status_code in {401,403}


def test_selection_revoked_at_final_commit_keeps_public_pointer_unchanged(publication,monkeypatch):
    review,_,publisher,store,gid,_,_=publication
    pid,_=prepare(publication);original=publisher._record_published
    def revoke(*args,**kwargs):
        review.publishing(gid,'withdrawn')
        return original(*args,**kwargs)
    monkeypatch.setattr(publisher,'_record_published',revoke)
    row=wait(review.db,publisher.start_publish(pid)['id'])
    assert row['status']=='failed' and POINTER not in store.objects


def test_selection_page_withdrawal_removes_the_same_public_entry(publication):
    from fastapi.testclient import TestClient
    from _review.app import create_app
    review,_,publisher,store,gid,_,_=publication
    pid,_=prepare(publication);assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    app=create_app(review.config.path,start_worker=False)
    app.state.publisher.close();app.state.publisher=publisher
    with TestClient(app,base_url='http://127.0.0.1') as client:
        result=client.post('/api/publishing/'+gid,json={'status':'withdrawn'},headers={'x-gbr-request':'1'})
        assert result.status_code==200,result.text
    assert not json.loads(store.objects[POINTER])['tracks']
    assert review.db.one('SELECT status FROM publishing_selection')['status']=='withdrawn'
