"""Production/experiment isolation using real SQLite, FLAC files and app routes.

The deterministic boundary worker does not run MiniMax or pretend to measure a GPU.
"""
from contextlib import closing
import csv
import io
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from unittest.mock import patch

import pytest

import test_app_generation
import test_execution
from _gbr_generation.executor import cleanup_outputs, execute
from _gbr_generation.experiment_outputs import ExperimentOutputs
from _gbr_generation.jobs import Jobs
from _gbr_generation.planner import open_database, verify_plan
from _gbr_worker.contract import file_sha


@pytest.fixture
def setup():
    fixture = test_app_generation.GenerationAppTests('runTest')
    fixture.setUp()
    worker = test_execution.ExecutionTests('runTest')
    worker.calls = []
    try:
        yield fixture, worker
    finally:
        fixture.doCleanups()


def prepare(f, *, purpose='experiment', **options):
    checked = f.preview(purpose=purpose, **options)
    assert checked['ready'] == 1, checked
    f.generation.start(checked['token'])
    path = Path(f.launched[-1]['manifest'])
    return path, verify_plan(path)


def generate(f, worker, **options):
    path, manifest = prepare(f, **options)
    result = execute(path, worker_python=sys.executable, launcher=worker.worker, limit=100)
    assert result['status'] == 'succeeded', result
    return path, manifest, result


def post(f, run, action):
    return f.client.post(f'/api/generation/experiments/{run["id"]}/{action}', headers=f.headers, json={})


def test_100_step_batch_shares_automatic_seeds_and_retains_independent_overrides(setup):
    f, worker = setup
    path, manifest = prepare(f, experiment_label='  Fixed encoding / steps 1–100  ',
        parameters={'encoder_seed': 'auto', 'sampler_seed': 'auto'}, parameter_priority='form',
        experiments={'seed_policy': 'shared', 'sweeps': {'sampler_steps': {'start': 1, 'stop': 100, 'step': 1}}})
    assert len(manifest['runs']) == 100
    assert [r['effective']['sampler_steps'] for r in manifest['runs']] == list(range(1, 101))
    assert len({r['effective']['encoder_seed'] for r in manifest['runs']}) == 1
    assert len({r['effective']['sampler_seed'] for r in manifest['runs']}) == 1
    assert all(r['purpose'] == 'experiment' for r in manifest['runs'])
    assert Path(manifest['output_root']) == f.generation.experimental / manifest['batch_id']
    assert f.generation.experimental not in f.app.state.service.config.inputs
    assert manifest['experiment_label'] == 'Fixed encoding / steps 1–100'
    jobs = Jobs(f.generation)
    assert not jobs.listing()['jobs']
    assert len(jobs.listing('experiment')['jobs']) == 1
    assert f.source.read_bytes() == f.original
    assert not worker.calls
    _, explicit = prepare(f, parameters={'encoder_seed': 'auto'}, parameter_priority='form',
        experiments={'seed_policy': 'shared', 'sweeps': {'encoder_seed': [10, 11], 'sampler_steps': [1, 2]}})
    assert [r['effective']['encoder_seed'] for r in explicit['runs']] == ['10', '10', '11', '11']


def test_experiments_never_enter_review_or_quality_stats_even_under_broad_roots(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker, experiments={'repetitions': 2})
    service = f.app.state.service
    service.config.values['input_directories'] = [str(f.root)]
    assert service.indexer.scan() == 0
    for name in ('audio', 'generations', 'reviews', 'generation_output_links', 'analytics_dataset'):
        assert service.db.one('SELECT COUNT(*) AS n FROM ' + name)['n'] == 0
    assert service.db.one('SELECT COUNT(*) AS n FROM generation_runs')['n'] == 2
    assert service.db.one('SELECT COUNT(*) AS n FROM generation_output_counters')['n'] == 0
    assert service.db.one('SELECT COUNT(*) AS n FROM generation_timing_spans')['n'] == 4
    # Embedded purpose survives copying outside the managed experiment directory.
    external = f.root / 'accidental-review-input'; external.mkdir()
    audio = next(Path(result['attempts'][0]['output']).glob('*.flac'))
    copied = external / 'renamed.flac'; shutil.copyfile(audio, copied)
    assert service.indexer.index(copied) is None
    assert service.db.one('SELECT COUNT(*) AS n FROM analytics_dataset')['n'] == 0
    assert not list(path.parent.glob('attempts/*/audio.flac'))
    assert not list(f.generation.generated.rglob('*.flac'))


def test_promotion_moves_exact_bundle_and_admits_only_selected_run(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker, experiments={'repetitions': 2})
    run = manifest['runs'][0]
    original = Path(result['attempts'][0]['output'])
    hashes = {p.name: file_sha(p) for p in original.iterdir()}
    timing = f.app.state.service.db.all('SELECT * FROM generation_timing_spans')
    response = post(f, run, 'promote')
    assert response.status_code == 200, response.text
    assert response.json()['state'] == 'promoted'
    assert not original.exists()
    destination = f.generation.generated / original.name
    assert {p.name: file_sha(p) for p in destination.iterdir()} == hashes
    assert len(list(f.generation.generated.rglob('*.flac'))) == 1
    assert len(list(f.generation.experimental.rglob('*.flac'))) == 1
    db = f.app.state.service.db
    assert db.one('SELECT COUNT(*) AS n FROM analytics_dataset')['n'] == 1
    assert db.one('SELECT run_id FROM generation_output_links')['run_id'] == run['id']
    assert db.one('SELECT COUNT(*) AS n FROM generation_runs')['n'] == 2
    assert db.all('SELECT * FROM generation_timing_spans') == timing
    assert all(Path(r['uri']).parent == destination for r in db.all('SELECT uri FROM generation_artifacts WHERE run_id=?', (run['id'],)))
    assert json.loads(db.one('SELECT requested_json FROM generation_runs WHERE id=?', (run['id'],))['requested_json'])['purpose'] == 'experiment'
    assert post(f, run, 'promote').status_code == 200
    assert post(f, run, 'delete-output').status_code == 400
    assert f.source.read_bytes() == f.original
    assert not db.all('PRAGMA foreign_key_check')


def test_promoted_bundle_follows_existing_review_archive_without_duplicate_files(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker)
    run = manifest['runs'][0]
    assert post(f, run, 'promote').status_code == 200
    service = f.app.state.service
    gid = service.db.one('SELECT id FROM generations')['id']
    service.queues.open(service.default_reviewer_id, gid)
    service.review(gid, dict(audio_quality=5, composition=5, consistency=5, realism=5), 'full')
    service.archiver.process(service.db.one('SELECT * FROM archive_operations'))
    assert service.db.one('SELECT status FROM archive_operations')['status'] == 'done'
    assert not list(f.generation.generated.rglob('*.flac'))
    assert not list(f.generation.experimental.rglob('*.flac'))
    assert len(list(service.config.archive.rglob('*.flac'))) == 1
    assert post(f, run, 'promote').status_code == 200
    assert service.db.one('SELECT COUNT(*) AS n FROM archive_operations')['n'] == 1
    assert f.client.get(f'/api/generation/experiments/{run["id"]}/audio').status_code == 200
    assert cleanup_outputs(path) == {'cleaned_attempts': []}
    assert service.db.one('SELECT COUNT(*) AS n FROM reviews')['n'] == 1
    assert not service.db.all('PRAGMA foreign_key_check')


def test_deleting_output_preserves_cache_provenance_timings_and_hardware(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker)
    run = manifest['runs'][0]
    output = Path(result['attempts'][0]['output'])
    cache = f.root / '_99_tools/_workers_infdit/runtime/encodings/test.safetensors'
    cache.parent.mkdir(parents=True); cache.write_bytes(b'untouched cache fixture')
    db = f.app.state.service.db
    tables = ('source_documents', 'generation_runs', 'generation_attempts', 'generation_timing_spans',
              'worker_sessions', 'generation_resource_measurements')
    before = {name: db.all('SELECT * FROM ' + name) for name in tables}
    response = post(f, run, 'delete-output')
    assert response.status_code == 200, response.text
    assert response.json() == {'run_id': run['id'], 'state': 'deleted', 'available': False, 'promoted_at': None}
    assert not output.exists() and not list(f.generation.experimental.rglob('*.flac'))
    assert cache.read_bytes() == b'untouched cache fixture'
    assert f.source.read_bytes() == f.original
    assert {name: db.all('SELECT * FROM ' + name) for name in tables} == before
    assert post(f, run, 'delete-output').status_code == 200
    assert post(f, run, 'promote').status_code == 400
    assert f.client.get(f'/api/generation/experiments/{run["id"]}/audio').status_code == 400
    assert cleanup_outputs(path) == {'cleaned_attempts': []}
    assert not execute(path, worker_python=sys.executable, launcher=worker.worker)['attempts']
    assert len(worker.calls) == 1


def test_playback_and_performance_export_include_failures_and_deleted_outputs(setup):
    f, worker = setup
    path, manifest = prepare(f, experiments={'sweeps': {'sampler_steps': [30, 1]}})
    def boundary(*args):
        worker.worker(*args, fail=bool(worker.calls))
    result = execute(path, worker_python=sys.executable, launcher=boundary, limit=100)
    assert result['status'] == 'failed'
    first = manifest['runs'][0]
    playback = f.client.get(f'/api/generation/experiments/{first["id"]}/audio')
    assert playback.status_code == 200 and playback.content.startswith(b'fLaC')
    details = f.client.get(f'/api/generation/jobs/{manifest["batch_id"]}?sort=steps').json()
    assert [r['parameters']['sampler_steps'] for r in details['runs']] == [1, 30]
    for row in details['runs']:
        actual = row['observations'][0]
        assert actual['wall_seconds'] >= 0
        assert actual['conditioning_seconds'] == 1
        assert actual['peak_allocated_bytes'] == 1234
        assert actual['peak_observed_device_bytes'] is None
        assert actual['estimated_cost'] is None
        assert actual['diffusion_seconds'] is None
        assert actual['gpu_name'] == 'test boundary'
    assert post(f, first, 'delete-output').status_code == 200
    exported = f.client.get(f'/api/generation/jobs/{manifest["batch_id"]}/performance.csv')
    assert exported.status_code == 200
    rows = list(csv.DictReader(io.StringIO(exported.content.decode('utf-8-sig'))))
    assert len(rows) == 2 and {r['status'] for r in rows} == {'succeeded', 'failed'}
    assert rows[0]['output_state'] == 'deleted'
    assert rows[0]['cache_status'] == 'miss' and rows[1]['cache_status'] == ''
    assert rows[0]['peak_observed_device_bytes'] == ''
    assert json.loads(rows[0]['timings'])


@pytest.mark.parametrize('action', ['promote', 'delete-output'])
def test_changed_files_and_unknown_companions_block_actions_before_mutation(setup, action):
    f, worker = setup
    path, manifest, result = generate(f, worker)
    output = Path(result['attempts'][0]['output']); run = manifest['runs'][0]
    original = {p.name: p.read_bytes() for p in output.iterdir()}
    (output / 'notes.txt').write_text('user data')
    response = post(f, run, action)
    assert response.status_code == 400 and 'Unexpected files' in response.text
    assert all((output / name).read_bytes() == raw for name, raw in original.items())
    (output / 'notes.txt').unlink()
    (output / 'source.yaml').write_bytes(b'changed')
    response = post(f, run, action)
    assert response.status_code == 400 and 'changed' in response.text
    assert ExperimentOutputs(f.generation).output(run['id'])['state'] == 'saved'
    assert f.source.read_bytes() == f.original


def test_locked_file_deletion_resumes_without_deleting_new_owners(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker, experiments={'repetitions': 2})
    run = manifest['runs'][0]; output = Path(result['attempts'][0]['output'])
    audio = next(output.glob('*.flac')); original_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == audio:
            raise PermissionError('Windows test file lock')
        return original_unlink(path, *args, **kwargs)
    with patch.object(Path, 'unlink', locked):
        response = post(f, run, 'delete-output')
    assert response.status_code == 400 and 'pending' in response.text
    assert ExperimentOutputs(f.generation).output(run['id'])['state'] == 'deleting'
    with closing(open_database(f.database)) as conn, conn:
        conn.execute("INSERT INTO generation_artifacts(id,run_id,kind,uri,metadata_json) VALUES('new-owner',?,'reference',?,'{}')",
                     (manifest['runs'][1]['id'], str(audio)))
    response = post(f, run, 'delete-output')
    assert response.status_code == 400 and 'Another run owns' in response.text
    assert audio.is_file()
    with closing(open_database(f.database)) as conn, conn:
        conn.execute("DELETE FROM generation_artifacts WHERE id='new-owner'")
    assert post(f, run, 'delete-output').status_code == 200
    assert not output.exists()
    assert len(list(f.generation.experimental.rglob('*.flac'))) == 1


def test_interrupted_promotion_reconciles_the_same_moved_files_and_ids(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker)
    run = manifest['runs'][0]; original = Path(result['attempts'][0]['output'])
    rename = Path.rename
    def interrupted(source, target):
        rename(source, target)
        raise OSError('test interruption after atomic move')
    with patch.object(Path, 'rename', interrupted):
        response = post(f, run, 'promote')
    assert response.status_code == 400 and 'pending' in response.text
    assert not original.exists()
    assert ExperimentOutputs(f.generation).output(run['id'])['state'] == 'promoting'
    assert post(f, run, 'promote').status_code == 200
    assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM generation_output_links')['n'] == 1
    assert len(list(f.root.rglob('*.flac'))) == 1


@pytest.mark.parametrize('action', ['promote', 'delete-output'])
def test_output_actions_wait_for_the_worker_cleanup_to_finish(setup, action):
    f, worker = setup
    path, manifest, result = generate(f, worker)
    run = manifest['runs'][0]; output = Path(result['attempts'][0]['output'])
    with closing(open_database(f.database)) as conn, conn:
        conn.execute("UPDATE generation_attempts SET status='running' WHERE run_id=?", (run['id'],))
    response = post(f, run, action)
    assert response.status_code == 400 and 'finishing worker cleanup' in response.text
    assert len(list(output.iterdir())) == 4
    assert ExperimentOutputs(f.generation).output(run['id'])['state'] == 'saved'


def test_production_default_and_host_protection_remain_intact(setup):
    f, worker = setup
    path, manifest, result = generate(f, worker, purpose='production')
    assert Path(manifest['output_root']) == f.generation.generated
    assert not Jobs(f.generation).listing('experiment')['jobs']
    assert len(f.client.get('/api/generation/jobs').json()['jobs']) == 1
    f.app.state.service.indexer.scan()
    assert f.app.state.service.db.one('SELECT COUNT(*) AS n FROM analytics_dataset')['n'] == 1
    assert post(f, manifest['runs'][0], 'delete-output').status_code == 400
    path, experiment, result = generate(f, worker)
    run = experiment['runs'][0]
    response = f.client.post(f'/api/generation/experiments/{run["id"]}/delete-output', json={})
    assert response.status_code == 403
    assert ExperimentOutputs(f.generation).output(run['id'])['available']
    response = f.client.post('/api/generation/preview', headers=f.headers,
        json={'input_root': str(f.built), 'purpose': 'invalid', 'connection': {'backend': 'local'}})
    assert response.status_code == 422


@pytest.mark.parametrize('specified,flags,expected', [({}, [], 'production'),
    ({'purpose': 'experiment'}, [], 'experiment'),
    ({'purpose': 'experiment'}, ['--purpose', 'production'], 'production')])
def test_cli_purpose_flags_and_parameter_files_use_the_same_output_policy(setup, capsys, specified, flags, expected):
    f, worker = setup
    spec = importlib.util.spec_from_file_location('gbr_generation_cli', test_execution.ROOT / '_02_generation/run_generation.py')
    cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
    cli.ROOT = f.root
    parameters = json.loads((f.built / 'parameters.json').read_text()) | specified
    source = f.root / 'parameters.json'; source.write_text(json.dumps(parameters))
    cli.main(['plan', '--all', '--built', str(f.built), '--parameters', str(source),
        '--database', str(f.database), '--on-error', 'cancel', *flags])
    result = json.loads(capsys.readouterr().out)
    manifest = verify_plan(result['manifest'])
    assert manifest['purpose'] == expected
    assert Path(manifest['output_root']) == (f.generation.experimental / manifest['batch_id'] if expected == 'experiment' else f.generation.generated)
    assert f.source.read_bytes() == f.original
    assert not worker.calls
