"""Hidden releases share the existing ledger/assets, never the main playlist."""
import json
from pathlib import Path

import pytest

from test_publishing import publication, prepare, wait
from _gbr_preparation.publishing import POINTER, encoded
from _gbr_preparation.release_catalogue import HIDDEN_POINTER
from _gbr_preparation.easter_egg import POINTER as EGG_POINTER


def tracks(store,pointer):return json.loads(store.objects[pointer])['tracks']


def test_hidden_publication_is_separate_and_withdrawal_retains_one_bundle(publication):
    review,prep,publisher,store,gid,source,yaml=publication
    existing={'id':'main-song','release_id':'keep'}
    store.objects[POINTER]=encoded({'format':'gbr-public-releases-v1','tracks':[existing]})
    pid,recipe=prepare(publication,visibility='hidden')
    task=wait(review.db,publisher.start_publish(pid)['id']);assert task['status']=='done',task['error']
    assert tracks(store,POINTER)==[existing]
    assert recipe['track']['id'] in json.loads(store.objects[POINTER])['withdrawn']
    hidden=tracks(store,HIDDEN_POINTER);assert len(hidden)==1 and hidden[0]['visibility']=='hidden'
    assert 'hidden.html?s=' in json.loads(task['telemetry_json'])['url']
    assert 'hidden.html?s=' in review.db.one('SELECT external_reference FROM publishing_selection')['external_reference']
    saved=json.loads(review.db.one('SELECT recipe_json FROM processing_tasks WHERE id=?',(pid,))['recipe_json'])
    assert all(Path(f['path']).is_file() for f in saved['files'])
    assert not source.exists() and next(prep.source(gid)[1].parent.glob('*.yaml')).read_bytes()==yaml
    publisher.start_withdraw(pid)
    assert tracks(store,HIDDEN_POINTER)==[] and tracks(store,POINTER)==[existing]
    assert all(f['key'] in store.objects for f in saved['files'])
    assert not review.db.all('PRAGMA foreign_key_check')


def test_move_existing_release_both_ways_reuses_assets_and_track_identity(publication):
    review,prep,publisher,store,gid,_,_=publication
    pid,recipe=prepare(publication)
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    files={p:p.read_bytes() for p in prep.showcase.rglob('*') if p.is_file()}
    keys={k for k in store.objects if k.startswith('gbr/v2/objects/')}
    for visibility,dest,other in [('hidden',HIDDEN_POINTER,POINTER),('public',POINTER,HIDDEN_POINTER)]:
        task=wait(review.db,publisher.start_publish(pid,visibility)['id']);assert task['status']=='done',task['error']
        assert len(tracks(store,dest))==1 and tracks(store,dest)[0]['id']==recipe['track']['id']
        assert tracks(store,other)==[]
        assert keys=={k for k in store.objects if k.startswith('gbr/v2/objects/')}
        assert files=={p:p.read_bytes() for p in prep.showcase.rglob('*') if p.is_file()}
        assert review.db.one('SELECT COUNT(*) n FROM generations')['n']==1


def test_uncertain_hidden_pointer_write_is_reconciled_on_retry(publication):
    review,_,publisher,store,_,_,_=publication
    pid,recipe=prepare(publication,visibility='hidden');original=store.compare_put;uncertain=True
    def compare(key,raw,**kwargs):
        nonlocal uncertain
        original(key,raw,**kwargs)
        if key==HIDDEN_POINTER and uncertain:uncertain=False;raise TimeoutError('unknown hidden outcome')
    store.compare_put=compare
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='failed'
    uploads=len(store.uploads)
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    assert len(store.uploads)==uploads and len(tracks(store,HIDDEN_POINTER))==1


def test_conflict_does_not_publish_hidden_track_into_main(publication):
    review,_,publisher,store,_,_,_=publication
    pid,_=prepare(publication,visibility='hidden');store.conflict=True
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='failed'
    assert POINTER not in store.objects and HIDDEN_POINTER not in store.objects
    store.conflict=False
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    assert tracks(store,POINTER)==[] and len(tracks(store,HIDDEN_POINTER))==1


def test_cover_trigger_uses_real_main_covers_and_is_remote_configured(publication):
    review,_,publisher,store,_,_,_=publication
    cover={'id':'album-cover','displayTitle':'Chosen song','variant':'Punk','artwork_url':'https://media.test/cover.jpg'}
    hidden={**cover,'id':'hidden-cover'}
    store.objects[POINTER]=encoded({'format':'gbr-public-releases-v1','tracks':[cover]})
    store.objects[HIDDEN_POINTER]=encoded({'format':'gbr-hidden-releases-v1','tracks':[hidden]})
    before=store.objects[POINTER]
    available=publisher.easter.configuration()
    assert [c['id'] for c in available['covers']]==['album-cover'] and available['hidden_count']==1
    with pytest.raises(ValueError):publisher.easter.save('hidden-cover')
    publisher.easter.save('album-cover')
    config=json.loads(store.objects[EGG_POINTER])
    assert config['enabled'] and config['track_id']=='album-cover'
    assert 'secret' not in store.objects[EGG_POINTER].decode() and store.objects[POINTER]==before
    assert publisher.easter.configuration()['trigger']['track_id']=='album-cover'
    publisher.easter.save(None)
    assert json.loads(store.objects[EGG_POINTER])['enabled'] is False
    assert review.db.one("SELECT COUNT(*) n FROM review_history WHERE action='easter_egg_configured'")['n']==2


def test_unknown_destination_and_unsafe_cover_are_rejected(publication):
    _,_,publisher,store,gid,_,_=publication
    with pytest.raises(ValueError):publisher.start_prepare(gid,{'visibility':'secret-ish'})
    store.objects[POINTER]=encoded({'format':'gbr-public-releases-v1','tracks':[{'id':'bad','artwork_url':'file:///E:/private.jpg'}]})
    assert publisher.easter.configuration()['covers']==[]
    with pytest.raises(ValueError):publisher.easter.save('bad')


@pytest.mark.parametrize('room',['hidden','easter','demos'])
def test_hidden_tracks_are_excluded_from_public_research_but_keep_local_reviews(publication,tmp_path,room):
    import sys
    from test_publishing import ROOT
    sys.path.insert(0,str(ROOT/'_96_website/_tools'))
    from export_platform import export
    review,_,publisher,_,_,_,_=publication
    before=review.db.all('SELECT * FROM reviews')
    if room=='demos':
        settings=publisher.library.settings();settings['rooms'].append({'id':'demos','name':'Demos','entry_track_id':None,'enabled':True,'order':3})
        publisher.library.save_settings(settings)
    prepare(publication,visibility=room)
    payload=export(review.db.path,output=tmp_path/'research.json')
    assert payload['count']==0
    assert review.db.all('SELECT * FROM reviews')==before


def test_new_preview_can_move_to_hidden_but_old_receipt_cannot_resurrect_it(publication):
    review,_,publisher,store,gid,_,_=publication
    old,_=prepare(publication)
    assert wait(review.db,publisher.start_publish(old)['id'])['status']=='done'
    new,_=prepare(publication,visibility='hidden')
    result=wait(review.db,publisher.start_publish(new)['id']);assert result['status']=='done',result['error']
    assert tracks(store,POINTER)==[] and tracks(store,HIDDEN_POINTER)[0]['release_id']==new
    result=wait(review.db,publisher.start_publish(old,'public')['id'])
    assert result['status']=='failed' and 'newer release' in result['error']
    with pytest.raises(ValueError,match='newer release'):publisher.start_withdraw(old)
    assert tracks(store,POINTER)==[] and tracks(store,HIDDEN_POINTER)[0]['release_id']==new
    assert review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(gid,))['status']=='published'


def test_failed_placement_can_be_withdrawn_from_both_catalogues(publication):
    review,_,publisher,store,gid,_,_=publication
    pid,_=prepare(publication)
    assert wait(review.db,publisher.start_publish(pid)['id'])['status']=='done'
    original=store.compare_put
    def conflict(key,raw,**kwargs):
        if key==POINTER:raise ValueError('conflicting main catalogue')
        return original(key,raw,**kwargs)
    store.compare_put=conflict
    result=wait(review.db,publisher.start_publish(pid,'hidden')['id']);assert result['status']=='failed'
    assert len(tracks(store,POINTER))==1
    store.compare_put=original;publisher.start_withdraw(pid)
    assert not tracks(store,POINTER) and not tracks(store,HIDDEN_POINTER)
    assert review.db.one('SELECT status FROM publishing_selection WHERE generation_id=?',(gid,))['status']=='withdrawn'


def test_cover_api_requires_host_and_existing_csrf_header(publication):
    import io
    from PIL import Image
    from fastapi.testclient import TestClient
    from _review.app import create_app
    review,_,publisher,store,_,_,_=publication
    cover={'id':'cover-api','displayTitle':'Cover','artwork_url':'https://media.test/cover.jpg'}
    store.objects[POINTER]=encoded({'format':'gbr-public-releases-v1','tracks':[cover]})
    buffer=io.BytesIO();Image.new('RGB',(800,800),'#8f7755').save(buffer,format='JPEG');store.objects['cover.jpg']=buffer.getvalue()
    app=create_app(review.config.path,start_worker=False);service=app.state.publisher
    service.root=publisher.root;service.config_path=publisher.config_path;service.secrets_path=publisher.secrets_path
    service.store_factory=lambda settings:store
    with TestClient(app,base_url='http://127.0.0.1') as client:
        assert client.get('/api/releases/easter-egg').status_code==200
        preview=client.get('/api/releases/easter-egg/covers/cover-api')
        assert preview.status_code==200 and preview.headers['content-type']=='image/jpeg'
        with Image.open(io.BytesIO(preview.content)) as image:assert image.size==(192,192)
        assert "img-src 'self' data:" in preview.headers['content-security-policy']
        assert client.get('/api/releases/easter-egg/covers/unknown').status_code==400
        assert client.post('/api/releases/easter-egg',json={'track_id':'cover-api'}).status_code==403
        assert client.post('/api/releases/easter-egg',json={'track_id':'cover-api'},headers={'x-gbr-request':'1'}).status_code==200
        assert client.post('/api/releases/none/publish',json={'visibility':'../../elsewhere'},headers={'x-gbr-request':'1'}).status_code==422
    with TestClient(app,base_url='http://127.0.0.1',client=('203.0.113.9',1234)) as client:
        assert client.get('/api/releases/easter-egg').status_code in {401,403}
