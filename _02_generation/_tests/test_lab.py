"""Short trials use the existing immutable experiment ledger and exact seeds."""
import sys
from pathlib import Path

import pytest
import test_app_generation
import test_execution
from _gbr_generation.executor import execute
from _gbr_generation.planner import verify_plan
from _gbr_generation.yaml_document import YamlDocument


@pytest.fixture
def lab():
    fixture = test_app_generation.GenerationAppTests('runTest')
    fixture.setUp()
    try:
        yield fixture
    finally:
        fixture.doCleanups()


def preview(fixture, **changes):
    body = {'input_root':str(fixture.built), 'purpose':'experiment', 'experiment_label':'Quick lab · Phrase test',
            'connection':{'backend':'local'}, 'lab':{'title':'Phrase test','caption':'Warm, dry vocal with short syncopated phrasing',
            'lyrics':'[verse]\nA short test phrase\n', 'source_path':str(fixture.source)},
            'parameters':{'duration':20,'encoder_seed':'18446744073709551614'},
            'experiments':{'sweeps':{'sampler_seed':'6021837534476744998,6021837534476744999','sampler_steps':'30,35'}}}
    body.update(changes)
    return fixture.client.post('/api/generation/preview',headers=fixture.headers,json=body)


def test_short_trials_freeze_text_seeds_and_stay_out_of_production(lab):
    response=preview(lab)
    assert response.status_code==200,response.text
    checked=response.json()
    assert checked['ready']==1 and not checked['invalid'] and checked['generation_count']==4,checked
    assert lab.source.read_bytes()==lab.original
    lab.generation.start(checked['token'])
    manifest_path=Path(lab.launched[-1]['manifest'])
    manifest=verify_plan(manifest_path)
    assert manifest['purpose']=='experiment'
    assert {run['effective']['duration'] for run in manifest['runs']}=={20}
    assert {run['effective']['sampler_seed'] for run in manifest['runs']}=={'6021837534476744998','6021837534476744999'}
    assert all(run['effective']['encoder_seed']=='18446744073709551614' for run in manifest['runs'])
    worker=test_execution.ExecutionTests('runTest');worker.calls=[]
    execute(manifest_path,worker_python=sys.executable,launcher=worker.worker,limit=4)
    assert all(call['lyrics']=='[verse]\nA short test phrase\n' for call in worker.calls)
    lab.app.state.service.indexer.scan()
    assert not lab.app.state.service.db.all('SELECT id FROM generations')
    assert not lab.client.get('/api/generation/jobs').json()['jobs']
    runs=lab.client.get('/api/generation/jobs/'+manifest['batch_id']).json()['runs']
    assert len(runs)==4
    audio=lab.client.get('/api/generation/experiments/'+runs[0]['id']+'/audio',headers={'Range':'bytes=0-99'})
    assert audio.status_code==206 and len(audio.content)==100
    assert lab.source.read_bytes()==lab.original


@pytest.mark.parametrize('change',[
    {'purpose':'production'},
    {'parameters':{'duration':360}},
    {'parameters':{'duration':0}},
    {'experiments':{'sweeps':{'duration':'10,20'}}},
    {'experiments':{'repetitions':17}},
])
def test_lab_limits_cannot_be_bypassed(lab,change):
    response=preview(lab,**change)
    assert response.status_code in {400,422},response.text
    assert not lab.launched


def test_lab_source_is_restricted_to_catalogue_and_blank_trials_work(lab):
    loaded=lab.client.get('/api/generation/lab/source',params={'root':str(lab.built),'path':str(lab.source)})
    assert loaded.status_code==200
    assert '[chorus]' in loaded.json()['fields']['lyrics']
    denied=lab.client.get('/api/generation/lab/source',params={'root':str(lab.built),'path':str(lab.database)})
    assert denied.status_code==400
    response=preview(lab,lab={'title':'Scratch idea','lyrics':'[verse]\nOne tiny experiment\n','caption':'Solo vocal, warm close microphone'})
    assert response.status_code==200 and response.json()['ready']==1,response.text
    assert lab.source.read_bytes()==lab.original


def test_unregistered_writing_drafts_can_be_auditioned_without_catalogue_edits(lab):
    path=lab.built/'_yaml/unregistered.yaml'
    path.write_text('title: Unregistered\ncluster: \nstyle: \nlyrics: old text\ncaption: old caption\n')
    catalogue=(lab.built/'track_list.csv').read_bytes()
    response=preview(lab,lab={'title':'Draft trial','lyrics':'[verse]\nTry this draft\n',
                             'caption':'A close and dry voice','source_path':str(path)})
    assert response.status_code==200 and response.json()['ready']==1,response.text
    assert (lab.built/'track_list.csv').read_bytes()==catalogue
