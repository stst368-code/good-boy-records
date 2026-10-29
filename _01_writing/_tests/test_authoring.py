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
from _gbr_writing.lyrics import normalize_lyrics

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


def test_caption_auditions_match_exact_elements_good_latest_ratings_and_available_audio(app):
    import numpy as np
    import soundfile as sf
    f,w=app;service=w.review
    reference=compose({'vocal_style':'Gentle lead vocals','basic':'Rock band'})
    (f.root/'_01_writing/_references/_minimax_templates/base.txt').write_text(reference)
    def recording(name,score,caption_text):
        path=f.root/(name+'.flac');sf.write(path,np.sin(np.arange(16000)*(.03+score*.001)),16000,subtype='PCM_24')
        path.with_suffix('.yaml').write_text('title: '+name+'\ncaption: '+json.dumps(caption_text)+'\nlyrics: words\n')
        aid=service.indexer.index(path);gid=service.db.one('SELECT id FROM generations WHERE audio_id=?',(aid,))['id'];rid=service.default_reviewer_id
        with service.db.transaction() as db:
            session=service.queues.session(db,rid)
            db.execute('INSERT INTO queue_reservations VALUES(?,?,?,?,?,?)',(aid,rid,gid,0,service.queues.clock(),service.queues.clock()+180))
            db.execute('UPDATE sessions SET current_generation_id=? WHERE id=?',(gid,session['id']))
        service.review(gid,dict.fromkeys(['audio_quality','composition','consistency','realism'],score),'quick')
        return gid,path
    good,audio=recording('good-song',5,compose({'vocal_style':'Gentle lead vocals','primary':'Different instruments'}))
    poor,_=recording('poor-song',3,reference)
    other,_=recording('other-song',4,compose({'vocal_style':'Rough shouted vocals'}))
    response=f.client.get('/api/writing/caption-auditions',params={'field':'vocal_style','template_id':'base'})
    assert response.status_code==200,response.text
    rows=response.json()['recordings'];assert [row['id'] for row in rows]==[good]
    assert rows[0]['bucket']==5 and rows[0]['playback_url'].startswith('/api/playback/')
    playback=f.client.get(rows[0]['playback_url']);assert playback.status_code==200 and playback.headers['content-type']=='audio/flac'
    audio.unlink()
    assert not f.client.get('/api/writing/caption-auditions',params={'field':'vocal_style','template_id':'base'}).json()['recordings']


@pytest.mark.parametrize('source,expected',[
    ('[Intro: alalala]','[intro]'),('[Verse 2: soft]','[verse]'),('[Verse2]','[verse]'),
    ('[PRE_CHORUS 1]','[pre-chorus]'),('[Pre–Chorus: building]','[pre-chorus]'),
    ('[Refrain]','[chorus]'),('[Hook 2]','[chorus]'),('[CHROUS]','[chorus]'),
    ('[Final Chorus: big]','[chorus]'),('[Post Chorus]','[post-chorus]'),
    ('[Middle 8]','[bridge]'),('[Gap]','[instrumental]'),('[Silence: 4 bars]','[instrumental]'),
    ('[Instrumental Break]','[instrumental]'),('[Breakdown]','[instrumental]'),
    ('[Guitar Solo: distorted]','[solo]'),('[Flute solo]','[solo]'),
    ('[Verse: guitar solo behind the singer]','[verse]'),('[Coda]','[outro]'),
    ('[Spoken Intro]','[intro]'),('[2nd Verse]','[verse]'),
])
def test_lyric_section_aliases_and_descriptions(source,expected):
    assert normalize_lyrics(source)==expected
    assert normalize_lyrics(expected)==expected


def test_section_normalisation_keeps_inline_sung_words_and_removes_blank_lines():
    assert normalize_lyrics('[Intro: gentle] HELLO\n\nÉCHO [Refrain] SING AGAIN\n[Gap][Solo]')==(
        '[intro]\nhello\nécho\n[chorus]\nsing again\n[instrumental]\n[solo]')


@pytest.mark.parametrize('lyrics',['[Mystery Section]\nwords','[Verse\nwords','[verse]]\nwords','[[verse]]\nwords'])
def test_invalid_tags_fail_before_any_save(app,lyrics):
    f,w=app;before=(f.built/'track_list.csv').read_bytes()
    response=f.client.post('/api/writing/base',headers=f.headers,json=base(lyrics=lyrics))
    assert response.status_code==400 and 'tag' in response.text
    assert not w.tasks() and (f.built/'track_list.csv').read_bytes()==before


def test_base_and_variant_save_canonical_tags_without_rewriting_source_or_other_fields(app):
    f,w=app;setup(f);original=f.source.read_bytes()
    request=base(lyrics='[Intro: alalala]\nHELLO\n\n[Refrain 2]\nÉCHO\n[Silence]\n[Flute Solo]')
    first=post(f,'base',request);assert first['values']['lyrics']=='[intro]\nhello\n[chorus]\nécho\n[instrumental]\n[solo]'
    source=f.client.get('/api/writing/base',params={'file':'_yaml/gbr-local-smoke.yaml'}).json()
    variant=post(f,'base',base(file=source['file'],source_sha256=source['sha256'],lyrics='[Verse2: quiet] KEEP THESE WORDS'))
    result=Path(variant['publication']['target']).read_bytes()
    assert variant['values']['lyrics']=='[verse]\nkeep these words'
    from _gbr_yaml_editor.document import Source
    before,after=Source(original),Source(result)
    for entry in before.entries:
        if entry.name in {'title','lyrics','song_id','variant_id'}:continue
        actual=after.one(entry.name)
        assert before.text[entry.start:entry.end]==after.text[actual.start:actual.end]
    assert f.source.read_bytes()==original and not list(f.root.rglob('*.tmp'))
