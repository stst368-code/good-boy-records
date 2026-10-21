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


def test_song_identity_groups_arbitrary_and_legacy_suffixes_without_prefix_collisions(service):
    catalogue=service.config.path.parent/'catalogue';catalogue.mkdir()
    (catalogue/'track_list.csv').write_text('song_id,song_name,filename\n17,paws off the velvet,paws-off-the-velvet.yaml\n18,paws off,paws-off.yaml\n')
    (catalogue/'variant_list.csv').write_text('song_id,variant_id,song_name,filename\n17,2,velvet punk,velvet-punk.yaml\n')
    titles=[('paws-off-the-velvet-v1_CFG-1.90_STEP-30_SEED-7',17),
            ('anything-at-all-on-the-end',17),
            ('paws-off-the-velvet-v3_CFG-1.70_STEP-40_SEED-42',None),
            ('paws-off-v2_CFG-1.90_STEP-30_SEED-7',None),
            ('velvet-punk-localized-caption',None)]
    files=[]
    for i,(title,sid) in enumerate(titles):
        files.append(track(service,'identity-'+str(i),frequency=200+i*50,metadata=f'title: {title}\ncluster: Rock\nstyle: Punk\n'+(f'song_id: {sid}\nvariant_id: 1\n' if sid else '')))
    originals={path.with_suffix('.yaml'):path.with_suffix('.yaml').read_bytes() for path,_,_ in files}
    service,client=client_for(service);service.song_catalogue_root=lambda:catalogue
    options=client.get('/api/review/options').json()
    assert [(r['value'],r['label'],r['remaining']) for r in options['titles']]==[('song:18','paws off',1),('song:17','paws off the velvet',4)]
    for choice in ['song:17','paws off the velvet','paws-off-the-velvet',titles[0][0],titles[2][0]]:
        response=client.post('/api/queue/counts',json={'titles':[choice]},headers=WRITE)
        assert response.json()['remaining']==4,(choice,response.text)
    assert client.post('/api/queue/counts',json={'titles':['paws off']},headers=WRITE).json()['remaining']==1
    assert client.get('/api/queue/counts',params={'song':titles[0][0]}).json()['remaining']==4
    assert client.get('/api/queue/counts',params={'song':'paws-off'}).json()['remaining']==1
    restored=client.post('/api/review/options',json={'titles':[titles[0][0]]},headers=WRITE).json()
    assert restored['selection']['titles']==['song:17']
    assert restored['labels']['song:17']=='paws off the velvet'
    for _,_,gid in files[:2]:complete(service,gid)
    published=client.get('/api/publishing',params={'bucket':'','titles':'song:17'}).json()['items']
    assert {row['generation_id'] for row in published}=={row[2] for row in files[:2]}
    assert all(path.read_bytes()==raw for path,raw in originals.items())
    assert service.db.one('SELECT COUNT(*) n FROM reviews')['n']==2


def test_legacy_names_strip_known_run_suffixes_and_keep_distinct_song_titles(service):
    for i,title in enumerate(['paws-off-the-velvet-v1_CFG-1.90_STEP-30_SEED-7','paws-off-the-velvet-v2',
                              'Ball','Ball Again','Summer of 69','Step By Step']):
        track(service,'legacy-'+str(i),frequency=250+i*25,metadata=f'title: {title}\n')
    service,client=client_for(service);service.song_catalogue_root=lambda:None
    options=client.get('/api/review/options').json()
    assert {row['value'] for row in options['titles']}=={'paws-off-the-velvet','Ball','Ball Again','Summer of 69','Step By Step'}
    assert client.post('/api/queue/counts',json={'titles':['paws-off-the-velvet']},headers=WRITE).json()['remaining']==2
    assert client.post('/api/queue/counts',json={'titles':['Ball']},headers=WRITE).json()['remaining']==1


def test_queue_facets_hide_reviewed_missing_and_empty_directories_while_rated_and_selection_keep_content(service):
    items=seed(service);sub=service.config.inputs[0]/'pending';sub.mkdir()
    empty=service.config.inputs[0]/'empty';empty.mkdir()
    service.db.execute('UPDATE generations SET original_directory=? WHERE id=?',(str(sub),items[2][2]))
    for _,_,gid in items[:2]:complete(service,gid)
    items[3][0].unlink()  # An old database entry is not available audio content.
    service,client=client_for(service)
    options=client.get('/api/review/options').json()
    assert options['titles']==[{'value':'Other','total':1,'remaining':1}]
    assert {row['value'] for row in options['clusters']}=={'Rock'}
    assert {row['value'] for row in options['genres']}=={'Jazz'}
    assert str(sub) in options['directories'] and str(empty) not in options['directories']
    assert client.get('/api/queue/counts').json()['remaining']==1
    assert {row['value'] for row in client.get('/api/review/options?scope=rated').json()['titles']}=={'Ball','Ball Again'}
    assert {row['value'] for row in client.get('/api/review/options?scope=selection').json()['titles']}=={'Ball','Ball Again'}
    complete(service,items[2][2])
    finished=client.get('/api/review/options').json()
    assert all(finished[key]==[] for key in ('titles','clusters','genres','directories'))
    assert client.get('/api/queue/counts').json()['remaining']==0
    exhausted=client.post('/api/review/options',json={'titles':['Other'],'clusters':['Rock']},headers=WRITE).json()
    assert exhausted['titles']==[] and exhausted['selection']['titles']==['Other']


def test_facets_only_offer_nonempty_combinations_and_do_not_change_the_saved_queue(service):
    items=seed(service);sub=service.config.inputs[0]/'rock-only';sub.mkdir()
    service.db.execute('UPDATE generations SET original_directory=? WHERE id=?',(str(sub),items[0][2]))
    service,client=client_for(service)
    filters={'titles':['Ball'],'clusters':['Rock'],'genres':['Punk']}
    before=service.db.one('SELECT * FROM sessions WHERE reviewer_id=?',(service.default_reviewer_id,))
    response=client.post('/api/review/options',json=filters,headers=WRITE)
    assert response.status_code==200,response.text
    options=response.json()
    assert [r['value'] for r in options['titles']]==['Ball']
    assert [r['value'] for r in options['clusters']]==['Rock']
    assert [r['value'] for r in options['genres']]==['Punk']
    assert options['directories']==sorted([str(sub),str(service.config.inputs[0])])
    assert all(row['remaining']>0 for key in ('titles','clusters','genres') for row in options[key])
    assert service.db.one('SELECT * FROM sessions WHERE reviewer_id=?',(service.default_reviewer_id,))==before
    assert client.post('/api/review/options',json=filters).status_code==403


def test_selection_bucket_status_and_bookmark_filters_only_offer_existing_content(service):
    items=seed(service);complete(service,items[0][2]);complete(service,items[1][2],values=(4,4,4,4))
    service,client=client_for(service);rid=service.default_reviewer_id
    service.set_flags(items[0][2],rid,distribution=True,bookmarked=True)
    options=client.post('/api/review/options?scope=selection&bucket=5',json={},headers=WRITE).json()
    assert options['buckets']==[5,4]
    assert options['statuses']==['selected'] and options['has_bookmarks']
    assert [row['value'] for row in options['titles']]==['Ball']
    narrow=client.post('/api/review/options?scope=selection&bucket=4&bookmarked=true',json={},headers=WRITE).json()
    assert narrow['titles']==[] and narrow['statuses']==['selected'] and narrow['has_bookmarks']
    assert narrow['buckets']==[5]
    assert client.post('/api/review/options?scope=selection&status=rejected',json={},headers=WRITE).json()['titles']==[]
