"""Permanent removal uses actual FLAC/SQLite fixtures and keeps survivors intact."""
import json
from pathlib import Path
import sqlite3

import numpy as np
import pytest
import soundfile as sf

from test_cleanup import legacy, ROOT
from _gbr_yaml_editor import cleanup, pruning


@pytest.fixture
def collection(legacy):
    service, repo, good, good_aid, good_gid, raw = legacy
    bad = good.parent / 'unmatched.flac'
    sf.write(bad, .1 * np.sin(2 * np.pi * 330 * np.arange(16000) / 16000), 16000, subtype='PCM_24')
    bad.with_suffix('.yaml').write_bytes(raw.replace(b'song_id: 13', b'song_id: 999').replace(b'title: moses-supposes-v2_CFG-1.6', b'title: unlisted-song'))
    bad_aid = service.indexer.index(bad)
    bad_gid = service.db.one('SELECT id FROM generations WHERE audio_id=?', (bad_aid,))['id']
    return service, repo, good, good_aid, good_gid, bad, bad_aid, bad_gid


def preview(collection):
    service, repo, good, _, _, _, _, _ = collection
    plan = cleanup.make_plan(repo, service.db.path, [good.parent])
    plan['pruning'] = pruning.make_plan(plan, service.config.path)
    journal = repo / '_02_generation/_runtime/_cleanup' / plan['id'] / 'plan.json'
    journal.parent.mkdir(parents=True)
    cleanup.persist(journal, plan)
    return plan, journal


def test_recover_then_prune_keeps_good_recording_and_all_definitions(collection):
    service, repo, good, good_aid, good_gid, bad, bad_aid, bad_gid = collection
    service.set_flags(good_gid, service.default_reviewer_id, bookmarked=True)
    service.queues.open(service.default_reviewer_id, bad_gid)
    service.review(bad_gid, dict(audio_quality=2, composition=2, consistency=2, realism=2), 'full',
                   reason='Discard this obsolete recording', tags=[{'tag_id': 'vocal_tone', 'polarity': 'negative'}])
    reviewers = service.db.all('SELECT * FROM reviewers')
    definitions = service.db.all('SELECT * FROM review_tag_definitions')
    source = repo / '_01_writing/_built/moses-supposes.yaml'
    source_bytes = source.read_bytes()
    good_bytes = good.read_bytes()
    plan, journal = preview(collection)
    assert plan['pruning']['audio_ids'] == [bad_aid]
    assert good.exists() and bad.exists()  # Preview never removes files.
    cleanup.apply_plan(journal)
    target = good.with_name('moses-supposes__' + good_aid[:12] + '.flac')
    assert target.read_bytes() == good_bytes and source.read_bytes() == source_bytes
    assert not bad.exists() and not bad.with_suffix('.yaml').exists()
    assert service.db.one('SELECT id FROM audio')['id'] == good_aid
    assert service.db.one('SELECT id FROM generations')['id'] == good_gid
    assert not service.db.all('SELECT * FROM reviews')
    assert not service.db.all('SELECT * FROM review_tags')
    assert service.db.all('SELECT * FROM reviewers') == reviewers
    assert service.db.all('SELECT * FROM review_tag_definitions') == definitions
    assert service.db.one('SELECT generation_id FROM bookmarks')['generation_id'] == good_gid
    assert not service.db.all('PRAGMA foreign_key_check')
    assert json.loads(journal.read_text())['pruning']['state'] == 'done'
    with pytest.raises(ValueError, match='cannot be undone'):
        cleanup.undo_plan(journal)
    cleanup.apply_plan(journal)  # Idempotent retry does not touch survivors.


def test_changed_bad_file_stops_before_recoveries_or_deletions(collection):
    service, _, good, _, _, bad, _, _ = collection
    _, journal = preview(collection)
    bad.with_suffix('.yaml').write_bytes(b'User changed the file after preview')
    with pytest.raises(ValueError, match='Pruning file changed'):
        cleanup.apply_plan(journal)
    assert good.exists() and bad.exists()
    assert service.db.one('SELECT COUNT(*) n FROM audio')['n'] == 2


def test_new_review_stops_stale_plan_before_deleting_anything(collection):
    service, _, good, _, _, bad, _, bad_gid = collection
    _, journal = preview(collection)
    service.queues.open(service.default_reviewer_id, bad_gid)
    service.review(bad_gid, dict(audio_quality=3, composition=3, consistency=3, realism=3), 'full')
    with pytest.raises(ValueError, match='Pruning records changed'):
        cleanup.apply_plan(journal)
    assert good.exists() and bad.exists()


def test_locked_file_resumes_without_repeating_database_deletion(collection, monkeypatch):
    service, _, _, _, _, bad, bad_aid, _ = collection
    _, journal = preview(collection)
    original_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == bad:
            raise PermissionError('simulated Windows lock')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    with pytest.raises(PermissionError, match='Windows lock'):
        cleanup.apply_plan(journal)
    assert bad.exists()
    assert not service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))
    assert json.loads(journal.read_text())['pruning']['state'] == 'committed'
    monkeypatch.setattr(Path, 'unlink', original_unlink)
    cleanup.apply_plan(journal)
    assert not bad.exists() and not bad.with_suffix('.yaml').exists()
    assert service.db.one("SELECT COUNT(*) n FROM review_history WHERE action='generation_cleanup_prune'")['n'] == 1
    assert json.loads(journal.read_text())['pruning']['state'] == 'done'


def test_shared_yaml_and_writing_input_are_never_deleted(collection):
    service, repo, _, _, good_gid, bad, _, bad_gid = collection
    media = service.db.one('SELECT media_id FROM generation_sources WHERE generation_id=?', (good_gid,))['media_id']
    shared = bad.with_suffix('.yaml')
    service.db.execute('INSERT INTO generation_sources(generation_id,media_id,yaml_path) VALUES(?,?,?)', (good_gid, media, str(shared)))
    writing = repo / '_01_writing/_built/original.yaml'
    writing.write_bytes(shared.read_bytes())
    bad_media = service.db.one('SELECT media_id FROM generation_sources WHERE generation_id=?', (bad_gid,))['media_id']
    service.db.execute('INSERT INTO generation_sources(generation_id,media_id,yaml_path) VALUES(?,?,?)', (bad_gid, bad_media, str(writing)))
    plan, journal = preview(collection)
    retained = {r['path'] for r in plan['pruning']['retained']}
    assert str(shared) in retained and str(writing) in retained
    cleanup.apply_plan(journal)
    assert shared.exists() and writing.exists() and not bad.exists()


def test_prune_preserves_rerun_with_surviving_output_and_clears_ancestry(collection):
    service, _, _, _, good_gid, _, _, bad_gid = collection
    with service.db.transaction() as conn:
        conn.executescript((ROOT / '_98_database/_gbr_database/schema.sql').read_text())
        conn.execute("INSERT INTO source_documents VALUES('snapshot',X'00','source.yaml','now')")
        conn.execute("INSERT INTO generation_runs(id,source_sha256,title,status,requested_json,effective_json,created_at,parent_review_generation_id) VALUES('survivor-run','snapshot','Good','succeeded','{}','{}','now',?)", (bad_gid,))
        conn.execute("INSERT INTO generation_output_links VALUES('survivor-run',?,NULL)", (good_gid,))
    _, journal = preview(collection)
    cleanup.apply_plan(journal)
    row = service.db.one("SELECT * FROM generation_runs WHERE id='survivor-run'")
    assert row and row['parent_review_generation_id'] is None
    assert service.db.one("SELECT * FROM generation_output_links WHERE run_id='survivor-run'")
    assert not service.db.all('PRAGMA foreign_key_check')


def test_published_recording_is_not_orphaned_from_public_website(collection):
    service, _, _, _, _, bad, bad_aid, bad_gid = collection
    service.db.execute("INSERT INTO publishing_selection(generation_id,status,updated_at) VALUES(?,'published','now')", (bad_gid,))
    plan, journal = preview(collection)
    assert bad_aid not in plan['pruning']['audio_ids']
    cleanup.apply_plan(journal)
    assert bad.exists() and service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))


def test_unknown_foreign_key_dependents_are_removed_transactionally(collection):
    service, _, _, _, _, _, _, bad_gid = collection
    with service.db.transaction() as conn:
        conn.execute('CREATE TABLE future_child(id TEXT PRIMARY KEY, generation TEXT REFERENCES generations(id))')
        conn.execute('CREATE TABLE future_grandchild(id TEXT PRIMARY KEY, child TEXT REFERENCES future_child(id))')
        conn.execute("INSERT INTO future_child VALUES('child',?)", (bad_gid,))
        conn.execute("INSERT INTO future_grandchild VALUES('grandchild','child')")
    _, journal = preview(collection)
    cleanup.apply_plan(journal)
    assert not service.db.all('SELECT * FROM future_child')
    assert not service.db.all('SELECT * FROM future_grandchild')
    assert not service.db.all('PRAGMA foreign_key_check')


def test_auto_recovers_and_prunes_without_csv_or_extra_setup(collection, capsys):
    service, repo, _, good_aid, _, bad, bad_aid, _ = collection
    result = cleanup.main(repo, ['auto', '--prune-unrecoverable', '--database', str(service.db.path),
                                '--config', str(service.config.path)])
    assert result == 0
    assert not bad.exists() and not service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))
    assert service.db.one('SELECT id FROM audio WHERE id=?', (good_aid,))
    output = capsys.readouterr().out
    assert 'PERMANENT REMOVAL: 1 recordings' in output
    assert 'permanent removal done' in output


def test_verified_duplicate_is_consolidated_while_recording_survives(legacy):
    service, repo, good, aid, gid, raw = legacy
    copy = good.parent / 'duplicate.flac'
    copy.write_bytes(good.read_bytes())
    copy.with_suffix('.yaml').write_bytes(raw)
    service.indexer.index(copy)
    original = good.read_bytes()
    plan = cleanup.make_plan(repo, service.db.path, [good.parent], deduplicate=True)
    assert not plan['errors']
    plan['pruning'] = pruning.make_plan(plan, service.config.path)
    assert not plan['pruning']['audio_ids'] and len(plan['pruning']['files']) == 1
    journal = repo / 'deduplicate-plan.json'
    cleanup.persist(journal, plan)
    cleanup.apply_plan(journal)
    assert service.db.one('SELECT id FROM audio')['id'] == aid
    assert service.db.one('SELECT id FROM generations')['id'] == gid
    active = service.db.all("SELECT * FROM media_files WHERE storage_state IN ('input','archived')")
    assert len(active) == 1 and Path(active[0]['path']).read_bytes() == original
    assert len(list(good.parent.glob('*.flac'))) == 1


def test_metadata_only_history_does_not_block_valid_completed_yaml(legacy):
    service, repo, good, aid, gid, raw = legacy
    row = service.db.one('SELECT * FROM generations WHERE id=?', (gid,))
    service.db.execute('''INSERT INTO generations(id,audio_id,song_id,metadata_key,original_filename,original_directory,created_at)
                         VALUES('metadata-only',?,?, 'old-empty-metadata','audio.flac',?,'old')''',
                       (aid, row['song_id'], str(good.parent)))
    service.db.execute("INSERT INTO generation_metadata(generation_id,parsed_json,embedded_json,metadata_status) VALUES('metadata-only',?, '{}','missing')",
                       (json.dumps({'title': 'moses-supposes'}),))
    plan = cleanup.make_plan(repo, service.db.path, [good.parent])
    assert not plan['errors']
    assert any('metadata-only' in note for note in plan['groups'][0]['notes'])
    journal = repo / 'sparse-history-plan.json'
    cleanup.persist(journal, plan);cleanup.apply_plan(journal)
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == 2
    assert service.db.one("SELECT raw_yaml_bytes FROM generation_metadata WHERE generation_id='metadata-only'")['raw_yaml_bytes'] is None


def test_symlink_file_reference_never_removes_its_external_target(collection):
    service, _, _, _, _, bad, _, bad_gid = collection
    link = bad.parent / 'linked.yaml'
    external = bad.parent / 'external.yaml'
    external.write_bytes(bad.with_suffix('.yaml').read_bytes())
    try:
        link.symlink_to(external)
    except OSError as exc:
        pytest.skip(str(exc))
    service.db.execute('UPDATE generation_sources SET yaml_path=?,original_yaml_path=? WHERE generation_id=?',
                       (str(link), str(link), bad_gid))
    plan, journal = preview(collection)
    assert any(row['path'] == str(external) for row in plan['pruning']['retained'])
    cleanup.apply_plan(journal)
    assert link.exists() and external.exists()


def test_playback_cache_is_removed_with_unrecoverable_recording(collection):
    service, _, _, _, _, _, bad_aid, _ = collection
    cache = service.config.data / 'cache/playback/preview.opus'
    cache.parent.mkdir(parents=True)
    cache.write_bytes(b'cached audio preview')
    service.playback.remember(cache, bad_aid)
    plan, journal = preview(collection)
    assert any(row['path'] == str(cache) for row in plan['pruning']['files'])
    cleanup.apply_plan(journal)
    assert not cache.exists() and not service.db.all('SELECT * FROM playback_cache')


def test_new_owner_after_interruption_is_not_deleted_on_retry(collection, monkeypatch):
    service, _, _, _, _, bad, bad_aid, _ = collection
    _, journal = preview(collection)
    original_unlink = Path.unlink
    def locked(path, *args, **kwargs):
        if path == bad:raise PermissionError('locked for test')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    with pytest.raises(PermissionError):cleanup.apply_plan(journal)
    monkeypatch.setattr(Path, 'unlink', original_unlink)
    # An external scan can re-import the same file between retries. Preserve it.
    service.indexer.index(bad)
    with pytest.raises(ValueError, match='new database owner'):
        cleanup.apply_plan(journal)
    assert bad.exists() and service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))


def test_native_telemetry_and_processing_assets_are_removed_without_orphans(collection):
    service, _, _, _, _, bad, _, bad_gid = collection
    master = bad.parent / 'obsolete-master.flac'
    master.write_bytes(bad.read_bytes())
    with service.db.transaction() as conn:
        conn.executescript((ROOT / '_98_database/_gbr_database/schema.sql').read_text())
        conn.executescript((ROOT / '_98_database/_gbr_database/processing.sql').read_text())
        conn.execute("INSERT INTO source_documents VALUES('snapshot',X'00','source.yaml','now')")
        conn.execute("INSERT INTO generation_runs(id,source_sha256,title,status,requested_json,effective_json,created_at) VALUES('bad-run','snapshot','Bad','succeeded','{}','{}','now')")
        conn.execute("INSERT INTO generation_attempts(id,run_id,stage,status,execution_mode,actual_models_json) VALUES('attempt','bad-run','generation','succeeded','local','{}')")
        conn.execute("INSERT INTO generation_timing_spans(id,attempt_id,stage,started_at,includes_children) VALUES('span','attempt','diffusion','now',0)")
        conn.execute("INSERT INTO generation_resource_measurements(attempt_id,stage,device_id,coverage,offload_json,details_json) VALUES('attempt','diffusion','gpu','full','{}','{}')")
        conn.execute("INSERT INTO generation_artifacts VALUES('audio','bad-run','audio',?,?,?,NULL,'{}')", (cleanup.digest(bad), str(bad), bad.stat().st_size))
        conn.execute("INSERT INTO generation_output_links VALUES('bad-run',?,'audio')", (bad_gid,))
        conn.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at) VALUES('process',?,'mix_master','done','unique','{}','hash','now')", (bad_gid,))
        conn.execute("INSERT INTO generation_artifacts VALUES('master',NULL,'mix_master',?,?,?,NULL,?)", (cleanup.digest(master), str(master), master.stat().st_size, json.dumps({'processing_task_id': 'process'})))
    _, journal = preview(collection)
    cleanup.apply_plan(journal)
    for table in ['generation_runs', 'generation_attempts', 'generation_timing_spans', 'generation_resource_measurements',
                  'generation_artifacts', 'generation_output_links', 'processing_tasks']:
        assert not service.db.all('SELECT * FROM ' + table), table
    assert not bad.exists() and not master.exists()
    assert not service.db.all('PRAGMA foreign_key_check')


def test_delete_failure_rolls_back_database_and_keeps_files(collection):
    service, _, _, _, _, bad, bad_aid, _ = collection
    service.db.execute("CREATE TRIGGER simulate_delete_failure BEFORE DELETE ON audio BEGIN SELECT RAISE(ABORT,'simulated deletion failure'); END")
    _, journal = preview(collection)
    with pytest.raises(sqlite3.IntegrityError, match='simulated deletion failure'):
        cleanup.apply_plan(journal)
    assert bad.exists() and service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))
    assert not pruning.committed(json.loads(journal.read_text()))
    assert not service.db.all('PRAGMA foreign_key_check')
    service.db.execute('DROP TRIGGER simulate_delete_failure')
    cleanup.apply_plan(journal)
    assert not bad.exists()


def test_database_commit_before_journal_write_is_resumable(collection, monkeypatch):
    service, _, _, _, _, bad, bad_aid, _ = collection
    _, journal = preview(collection)
    original = cleanup.persist
    def fail(path, plan):
        if plan.get('pruning', {}).get('state') == 'committed':
            raise OSError('journal write interrupted after commit')
        original(path, plan)
    monkeypatch.setattr(cleanup, 'persist', fail)
    with pytest.raises(OSError, match='journal write interrupted'):
        cleanup.apply_plan(journal)
    assert bad.exists() and not service.db.one('SELECT id FROM audio WHERE id=?', (bad_aid,))
    assert json.loads(journal.read_text())['pruning']['state'] == 'planned'
    monkeypatch.setattr(cleanup, 'persist', original)
    cleanup.apply_plan(journal)
    assert not bad.exists() and json.loads(journal.read_text())['pruning']['state'] == 'done'


def test_legacy_jobs_and_timings_use_composite_ownership_and_keep_survivors(collection):
    service, _, _, _, good_gid, _, _, bad_gid = collection
    with service.db.transaction() as conn:
        conn.executescript((ROOT / '_98_database/_gbr_database/schema.sql').read_text())
        conn.execute('CREATE TABLE legacy_generation_jobs(source_id TEXT REFERENCES legacy_import_sources(source_id),job_id TEXT,status TEXT,PRIMARY KEY(source_id,job_id))')
        # Imported timing tables historically did not declare a job foreign key.
        conn.execute('CREATE TABLE legacy_generation_timings(source_id TEXT REFERENCES legacy_import_sources(source_id),timing_id TEXT,job_id TEXT,PRIMARY KEY(source_id,timing_id))')
        conn.execute("INSERT INTO legacy_import_sources VALUES('legacy','hash','original.sqlite','now','{}')")
        for key, gid in [('bad', bad_gid), ('good', good_gid)]:
            conn.execute("INSERT INTO legacy_generation_jobs VALUES('legacy',?,'done')", (key,))
            conn.execute("INSERT INTO legacy_generation_timings VALUES('legacy',?,?)", (key, key))
            conn.execute("INSERT INTO legacy_output_links VALUES('legacy',?,?,'parameters_match','{}')", (key, gid))
    _, journal = preview(collection)
    cleanup.apply_plan(journal)
    assert [row['job_id'] for row in service.db.all('SELECT * FROM legacy_generation_jobs')] == ['good']
    assert [row['job_id'] for row in service.db.all('SELECT * FROM legacy_generation_timings')] == ['good']
    assert service.db.one('SELECT COUNT(*) n FROM legacy_import_sources')['n'] == 1
    assert not service.db.all('PRAGMA foreign_key_check')


def test_active_processing_job_is_retained(collection):
    service, _, _, _, _, bad, bad_aid, bad_gid = collection
    with service.db.transaction() as conn:
        conn.executescript((ROOT / '_98_database/_gbr_database/schema.sql').read_text())
        conn.executescript((ROOT / '_98_database/_gbr_database/processing.sql').read_text())
        conn.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at) VALUES('active',?,'mix_preview','running','active','{}','hash','now')", (bad_gid,))
    plan, journal = preview(collection)
    assert bad_aid not in plan['pruning']['audio_ids']
    cleanup.apply_plan(journal)
    assert bad.exists() and service.db.one("SELECT * FROM processing_tasks WHERE id='active'")


def test_exact_ledger_owned_old_worker_output_is_not_left_behind(collection):
    service, repo, _, _, _, bad, bad_aid, _ = collection
    worker_copy = repo / '_02_generation/_runtime/attempts/old-attempt/audio.flac'
    worker_copy.parent.mkdir(parents=True)
    worker_copy.write_bytes(bad.read_bytes())
    service.db.execute('''INSERT INTO media_files(audio_id,sha256,path,original_path,size,mtime_ns,storage_state,indexed_at)
                          VALUES(?,?,?,?,?,?,'superseded','old')''',
                       (bad_aid, cleanup.digest(worker_copy), str(worker_copy), str(worker_copy),
                        worker_copy.stat().st_size, worker_copy.stat().st_mtime_ns))
    plan, journal = preview(collection)
    assert any(row['path'] == str(worker_copy) for row in plan['pruning']['files'])
    cleanup.apply_plan(journal)
    assert not worker_copy.exists()


@pytest.mark.parametrize('retry', ['apply', 'auto'])
def test_auto_retry_clears_its_one_time_journal_after_locked_file_is_released(collection, monkeypatch, retry):
    service, repo, _, _, _, bad, _, _ = collection
    original = Path.unlink
    def locked(path, *args, **kwargs):
        if path == bad:raise PermissionError('simulated lock')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', locked)
    with pytest.raises(RuntimeError, match='Cleanup journal:'):
        cleanup.main(repo, ['auto', '--prune-unrecoverable', '--database', str(service.db.path),
                            '--config', str(service.config.path)])
    journals = list((repo / '_02_generation/_runtime/_cleanup').glob('*/plan.json'))
    assert len(journals) == 1
    monkeypatch.setattr(Path, 'unlink', original)
    arguments = (['apply', str(journals[0])] if retry == 'apply' else
                 ['auto', '--prune-unrecoverable', '--database', str(service.db.path), '--config', str(service.config.path)])
    assert cleanup.main(repo, arguments) == 0
    assert not journals[0].parent.exists() and not bad.exists()


def test_auto_does_not_archive_known_unrecoverable_audio_before_deleting_it(collection, monkeypatch):
    from _review.archive import Archiver
    service, repo, _, _, _, bad, bad_aid, bad_gid = collection
    service.queues.open(service.default_reviewer_id, bad_gid)
    service.review(bad_gid, dict(audio_quality=1, composition=1, consistency=1, realism=1), 'full')
    bad.with_suffix('.yaml').unlink()
    service.db.execute('UPDATE generation_metadata SET yaml_sha256=NULL,raw_yaml=NULL,raw_yaml_bytes=NULL WHERE generation_id=?', (bad_gid,))
    calls = []
    original = Archiver.process
    def tracked(archiver, op):
        calls.append(op['audio_id'])
        return original(archiver, op)
    monkeypatch.setattr(Archiver, 'process', tracked)
    assert cleanup.main(repo, ['auto', '--prune-unrecoverable', '--database', str(service.db.path),
                                '--config', str(service.config.path)]) == 0
    assert bad_aid not in calls and not bad.exists()
