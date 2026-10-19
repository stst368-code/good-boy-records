"""Writing through the real app and HTTP boundary; no model download or inference."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import shutil
import sys
import threading
from unittest.mock import patch
import pytest

ROOT = Path(__file__).resolve().parents[2]
for folder in ('_02_generation/_tests','_01_writing','_99_tools/_yaml_editor'):
    sys.path.insert(0,str(ROOT/folder))
import test_app_generation
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.yaml_document import YamlDocument
from _gbr_yaml_editor.document import Source
from _gbr_writing.references import retrieve
from _review.reviewers import Conflict

CAPTION = 'Global Metadata\nPunk guitars and a restless groove.\nVocal Details\nExpressive, clear vocals.\nArrangement\nA growing band texture and a sharp ending.\n'


@contextmanager
def worker(extra=None, gate=None):
    calls=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,data):
            raw=json.dumps(data,ensure_ascii=False).encode();self.send_response(200);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
        def do_GET(self):self.send({'status':'ok'} if self.path=='/health' else {'data':[{'id':'writing-fixture'}]})
        def do_POST(self):
            data=json.loads(self.rfile.read(int(self.headers['Content-Length'])));calls.append(data)
            if gate:gate.wait(timeout=10)
            task=json.loads(data['messages'][1]['content'])['task']
            fields={'new':{'title':'ball-supernova','lyrics':'[verse]\nFresh lyric\n','caption':CAPTION,'story':'A ball becomes a star.'},
                    'lyrics':{'lyrics':'[verse]\nChanged lyric\n'},'caption':{'caption':CAPTION},
                    'both':{'lyrics':'[verse]\nChanged lyric\n','caption':CAPTION},'translate':{'lyrics':'[verse]\nEin neuer Text\n'}}[task]
            fields.update(extra or {})
            self.send({'choices':[{'finish_reason':'stop','message':{'content':json.dumps(fields,ensure_ascii=False)}}],
                       'usage':{'prompt_tokens':200,'completion_tokens':90},'timings':{'predicted_per_second':22}})
    http=ThreadingHTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{http.server_port}',calls
    finally:
        if gate:gate.set()
        http.shutdown();http.server_close();thread.join(timeout=5)


@pytest.fixture
def app():
    f=test_app_generation.GenerationAppTests('runTest');f.setUp();writing=f.app.state.writing;writing.root=f.root
    master=f.built/'track_list.csv';lines=master.read_text().splitlines()
    master.write_text(lines[0]+',original_cluster,original_style\n'+'\n'.join(line+',,' for line in lines[1:])+'\n')
    (f.built/'track-template.yaml').write_bytes(b'song_id:\r\nvariant_id:\r\ntitle:\r\ncluster:\r\nstyle:\r\nlanguage: en\r\nexplicit: "no"\r\ncaption: |\r\nlyrics: |\r\n')
    references=f.root/'_01_writing/_references'
    (references/'styles.csv').write_text('ID,cluster,style_primary,template,template_source\nbase,Rock,Punk,_minimax_templates/base.txt,minimax\n')
    (references/'_minimax_templates').mkdir();(references/'_minimax_templates/base.txt').write_text(CAPTION)
    lore=f.root/'_01_writing/_lore';lore.mkdir();shutil.copyfile(ROOT/'_01_writing/_lore/gbr_lore.yaml',lore/'gbr_lore.yaml')
    f.headers['x-gbr-reviewer']=f.client.post('/api/reviewer/select',headers=f.headers,json={'name':'Simon'}).json()['reviewer']['id']
    try:yield f,writing
    finally:writing.pool.shutdown(wait=True,cancel_futures=True);f.doCleanups()


def request(kind='new', **values):
    return {'kind':kind,'brief':'A punk song about Bam Bam chasing a ball','files':[] if kind=='new' else ['_yaml/gbr-local-smoke.yaml'],
            'language':'en' if kind=='new' else '', 'cluster':'','style':'','use_lore':True,'use_styles':True,'use_lyrics':True,'template_ids':[],**values}


def submit(f,w,endpoint,kind='new',**values):
    w.save_settings({'endpoint':endpoint})
    response=f.client.post('/api/writing/tasks',headers=f.headers,json=request(kind,**values));assert response.status_code==200,response.text
    ids=response.json()['tasks']
    for identifier in ids:w.futures[identifier].result(timeout=10)
    return w.public(ids[0])


def test_new_song_draft_edit_publish_and_model_telemetry_share_database(app):
    f,w=app
    with worker() as (endpoint,calls):
        task=submit(f,w,endpoint,cluster='Rock',style='Punk')
        assert task['status']=='ready',task
        assert task['warnings']==['No matching lyric references; continuing without them.']
        assert not (f.built/'_yaml/ball-supernova.yaml').exists()
        assert calls[0]['response_format']=={'type':'json_object'}
        row=w.task(task['id']);assert row['worker_session_id']
        assert f.app.state.service.db.one('SELECT provider FROM worker_sessions WHERE id=?',(row['worker_session_id'],))['provider']=='llama_cpp'
        updated=w.update(task['id'],{'lyrics':'[verse]\nA revised lyric\n'},task['revision'])
        assert len(updated['details']['edits'])==1
        with pytest.raises(Conflict):w.update(task['id'],{'lyrics':'stale'},task['revision'])
        built=w.publish(task['id'],'keep',updated['revision'])
        assert built['status']=='published'
        target=Path(built['publication']['target']);assert target.read_bytes().startswith(b'song_id:')
        assert YamlDocument(target.read_bytes()).values['lyrics']=='[verse]\nA revised lyric\n'
        catalogue=Catalogue(f.built);assert len([i for i in catalogue.inputs if i.path==target])==1
        assert next(i for i in catalogue.inputs if i.path==target).metadata['original_style']=='base'
        assert w.publish(task['id'],'keep',updated['revision'])['publication']==built['publication']
        assert len(list(f.root.rglob('*.sqlite3')))==1


def test_caption_only_preserves_all_other_field_spans_order_bom_and_crlf(app):
    f,w=app
    original=b'\xef\xbb\xbfsong_id: 900000001\r\nvariant_id: 1\r\ntitle: gbr-local-smoke\r\n# keep this comment\r\nunknown: [a, b]\r\nencoder_seed: "18446744073709551615"\r\ncaption: |- # caption comment\r\n  Old caption\r\nlyrics: |+\r\n  [verse]\r\n  Original line\r\n\r\n'
    f.source.write_bytes(original)
    with worker() as (endpoint,_):task=submit(f,w,endpoint,'caption')
    assert task['status']=='ready',task
    result=w.task(task['id'])['result_yaml'];before=Source(original);after=Source(result)
    assert result.startswith(b'\xef\xbb\xbf') and list(YamlDocument(result).fields)==list(YamlDocument(original).fields)
    for entry in before.entries:
        if entry.name=='caption':continue
        new=after.one(entry.name);assert before.text[entry.start:entry.end]==after.text[new.start:new.end]
    assert YamlDocument(result).values['lyrics']==YamlDocument(original).values['lyrics']
    assert f.source.read_bytes()==original


def test_keep_creates_unique_variant_and_archive_replace_preserves_original_bytes(app):
    f,w=app;original=f.source.read_bytes()
    with worker() as (endpoint,_):task=submit(f,w,endpoint,'lyrics')
    saved=w.publish(task['id'],'keep',task['revision'])
    assert saved['publication']['variant_id']==2
    assert f.source.read_bytes()==original
    assert sum(i.song_id=='900000001' and i.variant_id=='2' for i in Catalogue(f.built).inputs)==1
    with worker() as (endpoint,_):second=submit(f,w,endpoint,'lyrics')
    with patch('pathlib.Path.replace',side_effect=OSError('simulated catalogue lock')):
        with pytest.raises(OSError):w.publish(second['id'],'archive_replace',second['revision'])
    assert w.public(second['id'])['status']=='publishing'
    archive=Path(w.public(second['id'])['publication']['archive']);assert archive.read_bytes()==original
    result=w.publish(second['id'],'archive_replace',second['revision'])
    assert result['status']=='published' and f.source.read_bytes()!=original
    assert archive.read_bytes()==original and not list(f.root.rglob('*.tmp'))


def test_source_or_catalogue_conflicts_never_overwrite_user_edits(app):
    f,w=app
    with worker() as (endpoint,_):task=submit(f,w,endpoint,'lyrics')
    changed=f.source.read_bytes()+b'# user edit\n';f.source.write_bytes(changed)
    with pytest.raises(Conflict,match='Source YAML changed'):w.publish(task['id'],'archive_replace',task['revision'])
    assert f.source.read_bytes()==changed and not (f.root/'_01_writing/_archive').exists()


def test_lore_uses_relevant_characters_and_missing_lyrics_are_not_an_error(app):
    f,w=app;refs,warnings=retrieve(f.root,request(use_styles=False),{})
    lore=json.loads(refs[0]['text']);assert 'bam_bam' in lore['characters'] and 'doja' not in lore['characters']
    assert refs[0]['sha256'] and 'No matching lyric references' in warnings[0]
    folder=f.root/'_01_writing/_references/_lyrics';folder.mkdir();(folder/'ball.txt').write_text('Bam Bam chases a ball.\n[verse]\nExample only, never copy.\n')
    refs,warnings=retrieve(f.root,request(use_styles=False),{});assert any(r['kind']=='lyrics' for r in refs) and not warnings


def test_llm_template_is_catalogued_once_and_accepted_by_generation(app):
    f,w=app
    with worker() as (endpoint,_):task=submit(f,w,endpoint,'caption',cluster='Rock',style='Punk')
    result=w.save_caption_template(task['id'],'punk-llm-test')
    assert Path(result['path']).read_text()==CAPTION
    assert w.save_caption_template(task['id'],'punk-llm-test')==result
    styles=Styles(f.root/'_01_writing/_references/styles.csv')
    assert styles.by_id['punk-llm-test']['template_source']=='llm'
    assert sum(row['ID']=='punk-llm-test' for row in styles.rows)==1


def test_invalid_output_fails_without_publishing_or_touching_sources(app):
    f,w=app;original=f.source.read_bytes()
    with worker(extra={'sampler_seed':'1'}) as (endpoint,_):task=submit(f,w,endpoint,'lyrics')
    assert task['status']=='failed' and 'unrequested fields' in task['error']
    assert f.source.read_bytes()==original
    assert not f.app.state.service.db.all("SELECT * FROM writing_tasks WHERE status='published'")


def test_batch_preflight_is_atomic_and_queued_jobs_can_be_cancelled(app):
    f,w=app;gate=threading.Event()
    with worker(gate=gate) as (endpoint,calls):
        w.save_settings({'endpoint':endpoint})
        with pytest.raises(ValueError):w.submit(request('lyrics',files=['_yaml/gbr-local-smoke.yaml','_yaml/missing.yaml']))
        assert not w.tasks() and not calls
        first=w.submit(request())['tasks'][0];second=w.submit(request())['tasks'][0]
        w.cancel(second);gate.set();w.futures[first].result(timeout=10);w.futures[second].result(timeout=10)
        assert w.public(second)['status']=='cancelled' and len(calls)==1


def test_writing_endpoints_keep_host_auth_csrf_and_validate_translation(app):
    f,w=app
    assert f.client.post('/api/writing/tasks',json=request()).status_code==403
    response=f.client.post('/api/writing/tasks',headers=f.headers,json=request('translate'))
    assert response.status_code==400 and 'target language' in response.text
    assert f.client.get('/tools/writing/writing.js').status_code==200
    assert f.client.get('/api/writing/catalogue').status_code==200
