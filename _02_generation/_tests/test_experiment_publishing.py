"""Real audio/shared-DB publication tests; the R2 boundary is an in-memory store.

Synthetic tones exercise encoding and playback, not MiniMax quality or performance.
"""
import hashlib
import io
import json
from pathlib import Path
import shutil
import sys
import threading
import time
from unittest.mock import patch
from urllib.parse import urlsplit

import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

import test_app_generation
import test_execution
from _gbr_generation.executor import execute
from _gbr_generation.experiment_outputs import ExperimentOutputs
from _gbr_generation.experiment_publishing import POINTER, KIND, grid, public_controls
from _gbr_generation.planner import verify_plan


class Store:
    def __init__(self):
        self.objects = {}; self.uploads = []; self.fail_upload = False
        self.lost_reply = False; self.fail_read = False; self.conflict = False
        self.block = None; self.entered = threading.Event()

    def read(self, key):
        if key == POINTER and self.fail_read:
            self.fail_read = False; raise TimeoutError('test lost reconciliation response')
        raw = self.objects.get(key)
        return raw, hashlib.md5(raw).hexdigest() if raw is not None else None

    def compare_put(self, key, raw, etag=None, **kwargs):
        if self.conflict and key == POINTER:
            self.conflict = False
            self.objects[key] = json.dumps({'format': 'gbr-public-experiments-v1', 'sets': [
                {'id': 'other-comparison', 'title': 'Another comparison', 'slot': '', 'parameter_field': 'sampler_cfg',
                 'url': 'https://media.test/other.json', 'sha256': 'a'*64}]}).encode()
        current, actual = self.read(key)
        if etag != actual or etag is None and current is not None:
            raise ValueError('test concurrent publisher changed index')
        self.objects[key] = raw
        if key == POINTER and self.lost_reply:
            self.lost_reply = False; self.fail_read = True
            raise TimeoutError('test lost successful write response')

    def upload(self, item):
        self.entered.set()
        if self.block is not None and not self.block.wait(10):
            raise TimeoutError('test upload blocked')
        if self.fail_upload:
            raise ValueError('test upload failure')
        raw = Path(item['path']).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == item['sha256']
        assert item['key'] not in self.objects or self.objects[item['key']] == raw
        self.objects[item['key']] = raw; self.uploads.append(item['key'])

    def public_check(self, url, origin, expected=None):
        assert urlsplit(url).path.lstrip('/') in self.objects

    def verify(self, item):
        raw = self.objects.get(item['key'])
        if raw is None or len(raw) != item['size'] or hashlib.sha256(raw).hexdigest() != item['sha256']:
            raise ValueError('test public object failed checksum verification')


def configure(f, store):
    publisher = f.app.state.publisher
    publisher.config_path = f.root / '_97_app/_runtime/publishing.json'
    publisher.secrets_path = publisher.config_path.with_name('publishing.secrets.json')
    publisher.store_factory = lambda settings: store
    publisher.save_settings({'endpoint': 'https://test.r2.cloudflarestorage.com', 'bucket': 'test-public',
        'public_base': 'https://pub-a5c11159f6144ae885f7795a5bf7086a.r2.dev',
        'website_url': 'https://stst368-code.github.io/good-boy-records/'})


def generate(f, *, seconds=.1, experiments=None):
    p = f.preview(purpose='experiment', experiment_label='Steps test',
        parameters={'encoder_seed': 'auto', 'sampler_seed': 'auto', 'caption': 'PRIVATE_CAPTION'}, parameter_priority='form',
        experiments=experiments or {'seed_policy': 'shared', 'sweeps': {'sampler_steps': [1, 2, 3]}})
    assert p['ready'] == 1, p
    f.generation.start(p['token'])
    path = Path(f.launched[-1]['manifest']); manifest = verify_plan(path)
    worker = test_execution.ExecutionTests('runTest'); worker.calls = []
    def boundary(*args):
        worker.worker(*args)
        job = worker.calls[-1]
        # Deliberately distinct fixture tones, with enough duration for A/B seeking.
        wave = .1*np.sin(np.arange(int(44100*seconds))*2*np.pi*(330+job['parameters']['sampler_steps']*30)/44100)
        sf.write(args[2]/'audio.flac', np.stack([wave,wave],axis=1), 44100, subtype='PCM_24')
    result = execute(path, worker_python=sys.executable, launcher=boundary, limit=100)
    assert result['status'] == 'succeeded', result
    return manifest


@pytest.fixture
def setup():
    f = test_app_generation.GenerationAppTests('runTest'); f.setUp()
    f.source.write_bytes(f.original + b'private_note: PRIVATE_NOTE\n')
    f.original = f.source.read_bytes()
    store = Store(); configure(f, store)
    manifest = generate(f)
    try:
        yield f, store, manifest
    finally:
        f.doCleanups()


def options(f, manifest):
    response = f.client.get(f'/api/generation/jobs/{manifest["batch_id"]}/comparison-options')
    assert response.status_code == 200, response.text
    return response.json()


def start(f, manifest, **changes):
    choices = options(f, manifest); axis = choices['parameters'][0]; group = axis['groups'][0]
    body = {'parameter': axis['id'], 'group': group['id'], 'title': 'Listen to the steps', 'slot': 'sampling-steps', 'format': 'flac'} | changes
    response = f.client.post(f'/api/generation/jobs/{manifest["batch_id"]}/publish-comparison', headers=f.headers, json=body)
    assert response.status_code == 200, response.text
    return response.json()['id']


def wait(f, identifier):
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        _, data = f.generation.experiment_publisher.row(identifier)
        if data['status'] not in {'queued', 'running'}:
            return data
        time.sleep(.02)
    raise AssertionError('Publication did not finish')


def test_publication_uses_separate_index_lossless_audio_and_no_review_rows(setup):
    f, store, manifest = setup
    music = b'{"format":"gbr-public-releases-v1","tracks":[]}'
    store.objects['gbr/v2/catalogue.json'] = music
    before = f.app.state.service.db.all('SELECT * FROM generation_timing_spans')
    choices = options(f, manifest)
    assert [p['id'] for p in choices['parameters']] == ['sampler_steps']
    assert choices['parameters'][0]['groups'][0]['count'] == 3
    assert choices['targets'][0]['id'] == 'sampling-steps'
    data = wait(f, start(f, manifest))
    assert data['status'] == 'published', data.get('error')
    catalogue = json.loads(store.objects[POINTER]); assert len(catalogue['sets']) == 1
    descriptor = json.loads(store.objects[urlsplit(catalogue['sets'][0]['url']).path.lstrip('/')])
    assert descriptor['format'] == 'gbr-public-experiment-v1'
    assert [s['value'] for s in descriptor['samples']] == [1, 2, 3]
    assert descriptor['samples'][0]['measurements']['peak_observed_device_bytes'] is None
    assert descriptor['samples'][0]['measurements']['wall_seconds'] >= 0
    assert '?experiment=' in data['url']
    for sample in descriptor['samples']:
        raw = store.objects[urlsplit(sample['audio']).path.lstrip('/')]
        public = FLAC(io.BytesIO(raw)); assert not public.tags and not public.pictures
        assert all(b.code in {0,1,3} for b in public.metadata_blocks)  # Empty format padding is harmless.
        source = ExperimentOutputs(f.generation).audio(sample['run_id'])
        actual, rate = sf.read(io.BytesIO(raw), dtype='int32', always_2d=True)
        original, original_rate = sf.read(source, dtype='int32', always_2d=True)
        assert rate == original_rate and np.array_equal(actual,original)
    public_data = b'\n'.join(store.objects.values())
    for private in (b'PRIVATE_NOTE', b'PRIVATE_CAPTION', b'local://test-host', str(f.root).encode(), b'GBR_YAML'):
        assert private not in public_data
    assert store.objects['gbr/v2/catalogue.json'] == music
    for table in ('audio','generations','reviews','publishing_selection','generation_output_links'):
        assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM '+table)['n'] == 0
    assert f.app.state.service.db.all('SELECT * FROM generation_timing_spans') == before
    assert f.source.read_bytes() == f.original
    assert not list(f.generation.experiment_publisher.runtime.rglob('*.flac'))
    assert not f.app.state.service.db.all('PRAGMA foreign_key_check')


def test_website_audio_survives_deleting_every_local_experiment_output(setup):
    f, store, manifest = setup
    data = wait(f,start(f,manifest)); assert data['status'] == 'published'
    saved = dict(store.objects)
    for run in manifest['runs']:
        assert ExperimentOutputs(f.generation).action(run['id'],'delete')['state'] == 'deleted'
    assert not list(f.generation.experimental.rglob('*.flac'))
    assert store.objects == saved
    for key, raw in saved.items():
        if key.endswith('.flac'):
            assert sf.info(io.BytesIO(raw)).frames > 0
    assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM generation_runs')['n'] == 3


def test_unknown_successful_upload_can_retry_after_local_sources_are_gone(setup):
    f, store, manifest = setup
    store.lost_reply = True
    with patch.object(f.generation.experiment_publisher,'prepare_audio',wraps=f.generation.experiment_publisher.prepare_audio) as renderer:
        identifier = start(f,manifest); failed = wait(f,identifier)
        assert failed['status'] == 'failed' and POINTER in store.objects
        for run in manifest['runs']:
            ExperimentOutputs(f.generation).action(run['id'],'delete')
        response = f.client.post(f'/api/generation/experiment-publications/{identifier}/retry',headers=f.headers,json={})
        assert response.status_code == 200
        assert wait(f,identifier)['status'] == 'published'
        assert renderer.call_count == 3
    assert len(json.loads(store.objects[POINTER])['sets']) == 1


def test_failed_upload_retains_one_render_and_retries_without_duplicate_public_assets(setup):
    f, store, manifest = setup; store.fail_upload = True
    identifier = start(f,manifest)
    assert wait(f,identifier)['status'] == 'failed'
    assert POINTER not in store.objects
    assert len(list(f.generation.experiment_publisher.runtime.rglob('*.flac'))) == 1
    store.fail_upload = False
    f.generation.experiment_publisher.retry(identifier)
    assert wait(f,identifier)['status'] == 'published'
    assert len([k for k in store.objects if k.endswith('.flac')]) == 3
    assert not list(f.generation.experiment_publisher.runtime.rglob('*.flac'))


def test_concurrent_catalogue_change_is_preserved_then_merged_on_retry(setup):
    f, store, manifest = setup; store.conflict = True
    identifier = start(f,manifest); assert wait(f,identifier)['status'] == 'failed'
    assert json.loads(store.objects[POINTER])['sets'][0]['id'] == 'other-comparison'
    f.generation.experiment_publisher.retry(identifier)
    assert wait(f,identifier)['status'] == 'published'
    assert len(json.loads(store.objects[POINTER])['sets']) == 2


def test_retry_rechecks_public_audio_before_registering_the_comparison(setup):
    f, store, manifest = setup; store.conflict = True
    identifier = start(f,manifest); data = wait(f,identifier)
    assert data['status'] == 'failed'
    item = data['files'][0]; saved = store.objects[item['key']]
    store.objects[item['key']] = b'changed remote bytes'
    f.generation.experiment_publisher.retry(identifier)
    assert wait(f,identifier)['status'] == 'failed'
    assert len(json.loads(store.objects[POINTER])['sets']) == 1
    store.objects[item['key']] = saved
    f.generation.experiment_publisher.retry(identifier)
    assert wait(f,identifier)['status'] == 'published'


def test_source_moves_and_deletes_are_blocked_only_while_upload_is_active(setup):
    f, store, manifest = setup; store.block = threading.Event()
    identifier = start(f,manifest); assert store.entered.wait(5)
    try:
        f.app.state.service.config.values['input_directories'] = [str(f.root)]
        assert f.app.state.service.indexer.scan() == 0
        assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM generations')['n'] == 0
        for action in ('promote','delete'):
            with pytest.raises(ValueError,match='being published'):
                ExperimentOutputs(f.generation).action(manifest['runs'][0]['id'],action)
    finally:
        store.block.set()
    assert wait(f,identifier)['status'] == 'published'
    ExperimentOutputs(f.generation).action(manifest['runs'][0]['id'],'delete')


def test_withdrawal_removes_the_index_entry_without_deleting_audio_or_reviews(setup):
    f, store, manifest = setup
    identifier = start(f,manifest); assert wait(f,identifier)['status'] == 'published'
    saved_assets = {k:v for k,v in store.objects.items() if k != POINTER}
    response = f.client.post(f'/api/generation/experiment-publications/{identifier}/withdraw',headers=f.headers,json={})
    assert response.status_code == 200 and response.json()['status'] == 'withdrawn'
    assert not json.loads(store.objects[POINTER])['sets']
    assert {k:v for k,v in store.objects.items() if k != POINTER} == saved_assets
    assert f.generation.experiment_publisher.withdraw(identifier)['status'] == 'withdrawn'


@pytest.mark.skipif(not shutil.which('ffmpeg'),reason='MP3 rendering requires FFmpeg')
def test_smaller_mp3_option_is_playable_and_has_no_private_source_tags(setup):
    from mutagen.mp3 import MP3
    f, store, manifest = setup
    data = wait(f,start(f,manifest,format='mp3')); assert data['status'] == 'published',data.get('error')
    for key,raw in store.objects.items():
        if key.endswith('.mp3'):
            assert MP3(io.BytesIO(raw)).info.length > 0
            assert b'PRIVATE_' not in raw and b'GBR_YAML' not in raw


def test_changed_audio_and_host_protection_prevent_publication(setup):
    f, store, manifest = setup
    choices = options(f,manifest); group = choices['parameters'][0]['groups'][0]
    body = {'parameter':'sampler_steps','group':group['id'],'title':'Test','slot':'sampling-steps'}
    route = f'/api/generation/jobs/{manifest["batch_id"]}/publish-comparison'
    assert f.client.post(route,json=body).status_code == 403
    source = ExperimentOutputs(f.generation).audio(manifest['runs'][0]['id'])
    tags = FLAC(source);tags['COMMENT']='edited after generation';tags.save()
    identifier = start(f,manifest)
    assert wait(f,identifier)['status'] == 'failed'
    assert not store.objects


def test_shared_seed_sweeps_offer_separate_repetitions_and_random_seeds_do_not(setup):
    f, store, manifest = setup
    repeated = generate(f,experiments={'seed_policy':'shared','repetitions':2,'sweeps':{'sampler_steps':[1,2]}})
    assert len(options(f,repeated)['parameters'][0]['groups']) == 2
    random = generate(f,experiments={'seed_policy':'randomize','sweeps':{'sampler_steps':[1,2,3]}})
    assert not options(f,random)['parameters']
    assert grid([1.0,1.2,1.5]) == {'min':1.0,'max':1.5,'step':.1}
    assert public_controls({'dit':r'C:\private\model.safetensors','caption':'private'}) == {'dit':'model.safetensors'}


def test_restart_keeps_the_same_publication_and_requires_explicit_retry(setup):
    from _gbr_generation.experiment_publishing import ExperimentPublisher
    f, store, manifest = setup
    with patch.object(f.app.state.publisher.pool,'submit') as submit:
        identifier = start(f,manifest)
        assert submit.call_count == 1
    restarted = ExperimentPublisher(f.generation,f.app.state.publisher)
    assert restarted.row(identifier)[1]['status'] == 'interrupted'
    assert not store.objects
    f.generation.experiment_publisher = restarted
    restarted.retry(identifier)
    assert wait(f,identifier)['status'] == 'published'
    assert len(json.loads(store.objects[POINTER])['sets']) == 1
