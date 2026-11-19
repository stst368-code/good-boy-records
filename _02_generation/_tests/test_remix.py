"""Reviewed recipes can be edited and swept without losing seeds or source history."""
import json
from pathlib import Path
import shutil
import sys

import pytest

import test_app_generation
import test_execution
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan
from _gbr_generation.yaml_document import YamlDocument


@pytest.fixture
def recording():
    fixture = test_app_generation.GenerationAppTests('runTest')
    fixture.setUp()
    try:
        seed = '18446744073709551614'
        fixture.source.write_bytes(fixture.source.read_bytes().replace(b'987654321',seed.encode()))
        preview = fixture.preview(parameters={'caption':'The actual generated caption','sampler_seed':seed,
                                               'encoder_seed':'123456789'},parameter_priority='form')
        fixture.generation.start(preview['token'])
        manifest_path = Path(fixture.launched[-1]['manifest'])
        worker = test_execution.ExecutionTests('runTest')
        worker.calls = []
        execute(manifest_path,worker_python=sys.executable,launcher=worker.worker)
        fixture.app.state.service.indexer.scan()
        generation_id = fixture.app.state.service.db.one('SELECT id FROM generations')['id']
        yield fixture,generation_id,verify_plan(manifest_path),worker
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('steps,expected',[('30,35',[30,35]),({'start':30,'stop':40,'step':5},[30,35,40])])
def test_remix_keeps_exact_seeds_and_creates_linked_lyric_caption_versions(recording,steps,expected):
    fixture,generation_id,original,worker = recording
    source_bytes = fixture.source.read_bytes()
    loaded = fixture.client.get('/api/generation/remix/'+generation_id).json()
    assert loaded['parameters']['sampler_seed']=='18446744073709551614'
    assert loaded['fields']['caption']=='The actual generated caption'
    fields = {**loaded['fields'],'lyrics':'[verse]\nA different lyric version\n',
              'caption':'A different mood and arrangement','style':'Custom remix style'}
    request = {'generation_id':generation_id,'fields':fields}
    preview = fixture.preview(remix=request,experiments={'sweeps':{'sampler_steps':steps}})
    assert preview['ready']==1 and preview['invalid']==0 and preview['generation_count']==len(expected),preview
    fixture.generation.start(preview['token'])
    path = Path(fixture.launched[-1]['manifest'])
    manifest = verify_plan(path)
    assert [r['effective']['sampler_steps'] for r in manifest['runs']]==expected
    for run in manifest['runs']:
        assert run['parent_review_generation_id']==generation_id
        assert run['effective']['sampler_seed']==loaded['parameters']['sampler_seed']
        assert run['effective']['encoder_seed']==loaded['parameters']['encoder_seed']
        assert run['effective']['caption']==fields['caption']
        assert run['effective']['style']==fields['style']
        assert YamlDocument((path.parent/run['snapshot']).read_bytes()).values['lyrics']==fields['lyrics']
    execute(path,worker_python=sys.executable,launcher=worker.worker,limit=len(expected))
    assert [job['parameters']['sampler_steps'] for job in worker.calls[1:]]==expected
    assert all(job['lyrics']==fields['lyrics'] for job in worker.calls[1:])
    assert fixture.source.read_bytes()==source_bytes
    saved = fixture.client.get('/api/generation/remix/'+generation_id,params={'revision':manifest['batch_id']}).json()
    assert saved['fields']['lyrics']==fields['lyrics'] and saved['fields']['caption']==fields['caption']
    assert saved['versions'][0]['generations']==len(expected)
    assert verify_plan(Path(fixture.launched[0]['manifest']))==original


@pytest.mark.parametrize('legacy',[False,True])
def test_remix_uses_saved_recipe_after_audio_and_writing_files_are_gone(recording,legacy):
    fixture,generation_id,original,_ = recording
    if legacy:
        db=fixture.app.state.service.db
        meta=json.loads(db.one('SELECT parsed_json FROM generation_metadata WHERE generation_id=?',(generation_id,))['parsed_json'])
        meta.pop('gbr_run_id',None)
        db.execute('DELETE FROM generation_output_links WHERE review_generation_id=?',(generation_id,))
        db.execute('UPDATE generation_metadata SET parsed_json=? WHERE generation_id=?',(json.dumps(meta),generation_id))
    fixture.source.unlink()
    shutil.rmtree(fixture.generation.generated)
    response = fixture.client.get('/api/generation/remix/'+generation_id)
    assert response.status_code==200
    source = response.json()
    assert '[chorus]' in source['fields']['lyrics']
    preview = fixture.preview(remix={'generation_id':generation_id,'fields':source['fields']})
    assert preview['ready']==1 and preview['invalid']==0,preview
    fixture.generation.start(preview['token'])
    manifest = verify_plan(fixture.launched[-1]['manifest'])
    assert manifest['runs'][0]['effective']==original['runs'][0]['effective']


def test_remix_preview_freezes_text_and_rejects_another_recordings_revision(recording):
    fixture,generation_id,_,_ = recording
    source = fixture.client.get('/api/generation/remix/'+generation_id).json()
    preview = fixture.preview(remix={'generation_id':generation_id,'fields':{'lyrics':'[verse]\nFirst edit\n'}})
    next_preview = fixture.preview(remix={'generation_id':generation_id,'fields':{'lyrics':'[verse]\nSecond edit\n'}})
    fixture.generation.start(preview['token'])
    first = Path(fixture.launched[-1]['manifest'])
    frozen = verify_plan(first)
    assert YamlDocument((first.parent/frozen['runs'][0]['snapshot']).read_bytes()).values['lyrics']=='[verse]\nFirst edit\n'
    fixture.generation.start(next_preview['token'])
    assert verify_plan(first)==frozen
    response = fixture.client.get('/api/generation/remix/'+generation_id,params={'revision':'f'*32})
    assert response.status_code==400
    response = fixture.client.post('/api/generation/preview',headers=fixture.headers,json={
        'input_root':'','connection':{'backend':'local'},'remix':{'generation_id':generation_id,'fields':{'sampler_seed':'1'}},
    })
    assert response.status_code==400
