"""Catalogue choices and queue/count/selection filters agree on real recordings."""
import json
from pathlib import Path
import shutil

from fastapi.testclient import TestClient

from _review.app import create_app
from conftest import track, complete

WRITE = {'X-GBR-Request':'1'}


def client_for(service):
    app = create_app(service.config.path, False)
    client = TestClient(app, base_url='http://localhost')
    response = client.post('/api/reviewer/select', json={'name':'Simon'}, headers=WRITE)
    client.headers['X-GBR-Reviewer'] = response.json()['reviewer']['id']
    return app.state.service, client


def seed(service):
    return [track(service, name, frequency=frequency, metadata=f'title: {title}\ncluster: {cluster}\n{field}: {style}\n')
            for name,title,cluster,style,field,frequency in (
                ('one','Ball','Rock','Punk','style',220),
                ('two','Ball Again','Rockabilly','Punk Blues','style_primary',330),
                ('three','Other','Rock','Jazz','genre',440),
                ('four','Other','Electronic','Synth','style',550))]


def test_exact_multiple_choices_match_counts_and_queue_without_substring_collisions(service):
    seed(service)
    service, client = client_for(service)
    filters = {'titles':['Ball','Other'],'clusters':['Rock'],'genres':['Punk','Jazz']}
    preview = client.post('/api/queue/counts', json=filters, headers=WRITE)
    assert preview.status_code == 200, preview.text
    assert preview.json()['remaining'] == 2
    assert client.post('/api/queue', json={**filters,'order':'alphabetical'}, headers=WRITE).status_code == 200
    rid = service.default_reviewer_id
    with service.db.transaction() as connection:
        session = service.queues.session(connection, rid)
        rows = service.queues.candidates(connection, session)
    assert [row['title'] for row in rows] == ['Ball','Other']
    assert all(row['cluster']=='Rock' for row in rows)
    assert json.loads(session['filters_json'])['titles'] == ['Ball','Other']
    assert client.post('/api/queue/counts', json={'titles':['Missing']}, headers=WRITE).json()['remaining'] == 0
    assert client.post('/api/queue/counts', json={'titles':[]}, headers=WRITE).json()['remaining'] == 4


def test_options_use_metadata_and_distinct_audio_with_directory_scope_and_no_scratch(service):
    seeded = seed(service)
    path, aid, gid = seeded[0]
    duplicate = path.with_name('duplicate.flac');shutil.copyfile(path, duplicate)
    duplicate.with_suffix('.yaml').write_bytes(path.with_suffix('.yaml').read_bytes())
    service.indexer.index(duplicate)
    scratch = service.config.path.parent/'_02_generation/_runtime/attempts/a'
    service.db.execute('UPDATE generations SET original_directory=? WHERE id=?',(str(scratch),seeded[1][2]))
    service, client = client_for(service)
    options = client.get('/api/review/options').json()
    assert next(r for r in options['titles'] if r['value']=='Ball') == {'value':'Ball','total':1,'remaining':1}
    assert 'Ball Again' not in {r['value'] for r in options['titles']}
    assert {'Punk','Jazz','Synth'} == {r['value'] for r in options['genres']}
    assert client.get('/api/review/options', params={'directory':str(service.config.path.parent)}).status_code == 400
    sub = service.config.inputs[0]/'sub';sub.mkdir()
    service.db.execute('UPDATE generations SET original_directory=? WHERE id=?',(str(sub),gid))
    scoped = client.get('/api/review/options',params={'directory':str(sub)}).json()
    assert scoped['titles'] == [{'value':'Ball','total':1,'remaining':1}]


def test_preview_is_read_only_and_legacy_sessions_can_be_replaced_by_choices(service):
    seed(service);service, client = client_for(service)
    client.post('/api/queue', json={'song':'Ball'}, headers=WRITE)
    assert client.get('/api/queue/counts').json()['remaining'] == 2
    client.get('/api/queue/next')
    before = service.db.one('SELECT * FROM sessions WHERE reviewer_id=?',(service.default_reviewer_id,))
    leases = service.db.all('SELECT * FROM queue_reservations')
    assert client.post('/api/queue/counts',json={'titles':['Other']},headers=WRITE).json()['remaining']==2
    assert service.db.one('SELECT * FROM sessions WHERE reviewer_id=?',(service.default_reviewer_id,))==before
    assert service.db.all('SELECT * FROM queue_reservations')==leases
    assert client.post('/api/queue/counts',json={'titles':['Other']}).status_code == 403
    client.post('/api/queue',json={'titles':['Ball']},headers=WRITE)
    assert client.get('/api/queue/counts').json()['remaining']==1


def test_selection_options_and_exact_filters_use_style_primary_and_exclude_unselected(service):
    items = seed(service)
    for _,_,gid in items[:3]:complete(service,gid)
    service, client = client_for(service)
    options = client.get('/api/review/options?scope=selection').json()
    assert {'Ball','Ball Again','Other'} == {row['value'] for row in options['titles']}
    assert {'Punk','Punk Blues','Jazz'} == {row['value'] for row in options['genres']}
    result = client.get('/api/publishing',params=[('bucket',''),('titles','Ball'),('titles','Ball Again'),('clusters','Rockabilly'),('genres','Punk Blues')])
    assert result.status_code == 200,result.text
    assert [r['title'] for r in result.json()['items']] == ['Ball Again']
    assert result.json()['items'][0]['genre'] == 'Punk Blues'
    complete(service,items[3][2])
    assert 'Synth' in {r['value'] for r in client.get('/api/review/options?scope=selection').json()['genres']}
