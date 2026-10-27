"""Manual writing through the real HTTP boundary and shared catalogue."""
import csv
import io
import json
from pathlib import Path
from unittest.mock import patch
import uuid

import pytest
from test_writing import app, CAPTION
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.yaml_document import YamlDocument
from _gbr_writing.authoring import SECTIONS, split_caption, compose

HEADERS='song_id,song_name,filename,author,inspiration_author,inspiration_track,album_bool,album_name,album_position,styles_linked,original_cluster,original_style'


def setup(f):
    for name in ('track_list.csv','variant_list.csv'):
        path=f.built/name;old=list(csv.DictReader(io.StringIO(path.read_text(encoding='utf-8-sig'))))
        names=HEADERS.split(',')
        if name.startswith('variant'):names.insert(1,'variant_id')
        out=io.StringIO(newline='');writer=csv.DictWriter(out,names,lineterminator='\r\n');writer.writeheader()
        writer.writerows({key:row.get(key,'') for key in names} for row in old)
        path.write_bytes(b'\xef\xbb\xbf'+out.getvalue().encode())


def base(**changes):
    return {'request_id':uuid.uuid4().hex,'title':'My ÉCHO Song','lyrics':'\n[VERSE]\n\nSING TO ME\n  \nÉCHO\n','author':'GBR',
            'inspiration_author':'Artist A','inspiration_track':'Song B','album_name':'First ALBUM','album_position':2,**changes}


def caption(**changes):
    return {'request_id':uuid.uuid4().hex,'cluster':'Rock','version':'Punk','fields':{'basic':'Fast guitars','vocal_style':'Quiet, then loud'},**changes}


def post(f,url,body):
    response=f.client.post('/api/writing/'+url,headers=f.headers,json=body)
    assert response.status_code==200,response.text
    return response.json()


def test_base_normalises_and_registers_yaml_csv_shared_db_once(app):
    f,w=app;setup(f);request=base()
    result=post(f,'base',request);song=str(result['publication']['song_id'])
    assert result['values']['title']=='my-écho-song' and result['values']['lyrics']=='[verse]\nsing to me\nécho'
    assert song=='900000002' and result['publication']['variant_id']==1
    target=Path(result['publication']['target']);assert target.name=='my-écho-song.yaml'
    assert b'\r\n' in target.read_bytes() and f.source.read_bytes()==f.original
    row=w.review.db.one('SELECT * FROM catalogue_songs WHERE song_id=?',(song,));metadata=json.loads(row['metadata_json'])
    assert metadata['author']=='gbr' and metadata['inspiration_author']=='artist a' and metadata['inspiration_track']=='song b'
    assert metadata['album_bool']=='t' and metadata['album_name']=='first album' and metadata['album_position']=='2'
    assert w.review.db.one('SELECT filename FROM catalogue_variants WHERE song_id=? AND variant_id=?',(song,'1'))['filename']=='_yaml/my-écho-song.yaml'
    assert post(f,'base',request)['id']==result['id']
    assert len(list(csv.DictReader(io.StringIO((f.built/'track_list.csv').read_text(encoding='utf-8-sig')))))==2
    assert (f.built/'track_list.csv').read_bytes().startswith(b'\xef\xbb\xbf')
    assert not list(f.root.rglob('*.tmp'))


def test_variant_loads_existing_base_preserves_source_and_allocates_next_ids(app):
    f,w=app;setup(f);original=f.source.read_bytes()
    loaded=f.client.get('/api/writing/base',params={'file':'_yaml/gbr-local-smoke.yaml'}).json()
    request=base(file=loaded['file'],source_sha256=loaded['sha256'],album_name='',album_position=None)
    result=post(f,'base',request)
    assert result['publication']['song_id']==900000001 and result['publication']['variant_id']==2
    assert f.source.read_bytes()==original
    assert YamlDocument(Path(result['publication']['target']).read_bytes()).values['caption']==YamlDocument(original).values['caption']
    again=post(f,'base',{**request,'request_id':uuid.uuid4().hex})
    assert again['publication']['variant_id']==3
    assert {row['variant_id'] for row in w.review.db.all('SELECT variant_id FROM catalogue_variants')}=={'1','2','3'}
    assert not any(i.issues for i in Catalogue(f.built).inputs)


def test_manual_save_resumes_after_windows_catalogue_lock_without_duplicate(app):
    f,w=app;setup(f);request=base()
    from _gbr_writing.authoring import Authoring
    authoring=Authoring(w)
    with patch('pathlib.Path.replace',side_effect=PermissionError('CSV open in Excel')):
        with pytest.raises(PermissionError):authoring.save_base(request,None)
    assert w.public(request['request_id'])['status']=='publishing'
    assert not w.review.db.one('SELECT * FROM catalogue_songs WHERE song_id=?',('900000002',))
    result=authoring.save_base(request,None)
    assert result['status']=='published' and len(list(f.built.rglob('my-écho-song.yaml')))==1
    assert w.review.db.one('SELECT * FROM catalogue_songs WHERE song_id=?',('900000002',))


def test_ids_respect_shared_db_and_pending_publications(app):
    f,w=app;setup(f)
    w.review.db.execute('INSERT INTO catalogue_songs VALUES(?,?,?)',('900000099','existing','{}'))
    first=post(f,'base',base());assert first['publication']['song_id']==900000100
    second=post(f,'base',base(title='Another'));assert second['publication']['song_id']==900000101


def test_caption_reference_sections_remain_editable_and_custom_pool_has_unique_names(app):
    f,w=app
    text=compose({key:'Example '+label+'\nSecond line' for _,fields in SECTIONS for key,label in fields})
    path=f.root/'_01_writing/_references/_minimax_templates/base.txt';path.write_text(text)
    response=f.client.get('/api/writing/caption-examples',params={'field':'vocal_style','q':'Example vocal'})
    assert response.status_code==200 and response.json()['total']==1
    example=response.json()['examples'][0];assert example['text']=='Example Vocal Style\nSecond line'
    before=path.read_bytes();request=caption(fields={'vocal_style':example['text']+' edited','primary':'Bright guitars'})
    first=post(f,'captions',request);assert first['id']=='rock-punk-2'
    assert post(f,'captions',request)==first
    second=post(f,'captions',caption());third=post(f,'captions',caption())
    assert second['id']=='rock-punk-3' and third['id']=='rock-punk-4'
    styles=Styles(f.root/'_01_writing/_references/styles.csv');row=styles.by_id[first['id']]
    assert row['template_source']=='custom' and row['version']=='Punk-2' and not row['song_id']
    assert Path(first['path']).parent.name=='_custom_templates'
    saved=split_caption(Path(first['path']).read_text());assert saved['vocal_style'].endswith('edited') and saved['primary']=='Bright guitars'
    assert path.read_bytes()==before and f.source.read_bytes()==f.original
    assert not list(f.root.rglob('*.tmp'))


def test_caption_optional_version_and_song_link_are_persisted_and_enforced(app):
    f,w=app
    first=post(f,'captions',caption(version='',song_id='900000001'))
    second=post(f,'captions',caption(version=''))
    assert first['id']=='rock' and second['id']=='rock-2'
    styles=Styles(f.root/'_01_writing/_references/styles.csv')
    assert styles.by_id[first['id']]['song_id']=='900000001'
    templates=f.client.get('/api/generation/templates').json()['templates']
    assert next(row for row in templates if row['id']==first['id'])['song_id']=='900000001'
    from _gbr_generation.planner import preflight
    selected=Catalogue(f.built).select(all_inputs=True)[0]
    selected.song_id='999'
    checked=preflight([selected],{'template_id':first['id'],'batch':{'cluster':'Rock','style':'Rock'}},styles,f.root/'_01_writing/_references',require_parameters=False)
    assert any('linked to another song' in issue.message for issue in checked[0].issues)


@pytest.mark.parametrize('changes',[{'title':'   '},{'lyrics':'\n\n'},{'album_name':'','album_position':2},{'album_position':0},{'title':'../oops'}])
def test_invalid_bases_do_not_write_files_or_ledger(app,changes):
    f,w=app;before=(f.built/'track_list.csv').read_bytes()
    response=f.client.post('/api/writing/base',headers=f.headers,json=base(**changes))
    assert response.status_code in {400,422}
    assert (f.built/'track_list.csv').read_bytes()==before and not w.tasks()


def test_manual_endpoints_keep_auth_csrf_and_reject_stale_source(app):
    f,w=app
    assert f.client.post('/api/writing/base',json=base()).status_code==403
    assert f.client.post('/api/writing/captions',json=caption()).status_code==403
    source=f.client.get('/api/writing/base',params={'file':'_yaml/gbr-local-smoke.yaml'}).json()
    f.source.write_bytes(f.original+b'# external edit\n')
    response=f.client.post('/api/writing/base',headers=f.headers,json=base(file=source['file'],source_sha256=source['sha256']))
    assert response.status_code==409 and not w.tasks()
    assert f.client.get('/api/writing/base',params={'file':'../../outside.yaml'}).status_code==400
    assert f.client.post('/api/writing/captions',headers=f.headers,json=caption(fields={'bad_field':'x'})).status_code==400


def test_caption_unicode_names_and_retry_after_locked_index_preserve_references(app):
    f,w=app
    from _gbr_writing.authoring import Authoring
    authoring=Authoring(w);request=caption(cluster='Électronique',version='Été')
    with patch('pathlib.Path.replace',side_effect=PermissionError('styles.csv is open')):
        with pytest.raises(PermissionError):authoring.save_caption(request,None)
    result=authoring.save_caption(request,None)
    assert result['id']=='electronique-ete'
    styles=Styles(f.root/'_01_writing/_references/styles.csv')
    assert styles.by_id[result['id']]['cluster']=='Électronique'
    assert styles.by_id[result['id']]['version']=='Été'
    assert len([r for r in styles.rows if r['ID']==result['id']])==1
    assert not list(f.root.rglob('*.tmp'))
