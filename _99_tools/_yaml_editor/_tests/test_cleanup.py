"""Real FLAC + SQLite tests for offline historical repair and recovery."""
import json
from pathlib import Path
import sys
import numpy as np
import pytest
import soundfile as sf
from mutagen.flac import FLAC

ROOT = Path(__file__).resolve().parents[3]
for path in (ROOT, ROOT/'_02_generation', ROOT/'_03_review/_generation_review', ROOT/'_99_tools/_yaml_editor'):
    sys.path.insert(0, str(path))
from _review.config import Config
from _review.service import Service
from _review.indexer import audio_identity
from _gbr_generation.yaml_document import YamlDocument
from _gbr_yaml_editor import cleanup


@pytest.fixture
def legacy(tmp_path):
    repo = tmp_path/'repo';writing = repo/'_01_writing/_built';writing.mkdir(parents=True)
    (writing/'track_list.csv').write_text('song_id,song_name,filename\n13,moses-supposes,moses-supposes.yaml\n')
    (writing/'variant_list.csv').write_text('song_id,variant_id,song_name,filename\n13,2,moses-supposes-punk,moses-supposes-punk.yaml\n')
    (writing/'track-template.yaml').write_text('song_id:\nvariant_id:\ntitle:\ncluster:\nstyle:\nexplicit: no\nlanguage: en\ndit:\ntext_encoder:\nsampler_steps:\ncaption: |\nlyrics: |\n')
    original = writing/'moses-supposes.yaml';original.write_bytes(b'title: source must stay untouched\nlyrics: "a\\nb"\n')
    inp = repo/'_02_generation/_generated';inp.mkdir(parents=True)
    config = tmp_path/'config.json';config.write_text(json.dumps({'input_directories': [str(inp)]}))
    service = Service(Config(config))
    path = inp/'audio.flac';data = .1*np.sin(2*np.pi*220*np.arange(16000)/16000)
    sf.write(path, data, 16000, subtype='PCM_24')
    raw = (b'\xef\xbb\xbf# retained header\r\nsong_id: 13\r\nvariant_id: 2\r\n'
           b'title: moses-supposes-v2_CFG-1.6\r\ncluster: Rock\r\n'
           b'dit: minimax_music3_dit_fp32\r\ntext_encoder: bf16\r\nsteps: 25\r\n'
           b"lyrics: 'one\\ntwo/n [chorus]'\r\ncaption: 'calm\\nsoft'\r\n"
           b'custom: [a, b] # keep exactly\r\n')
    path.with_suffix('.yaml').write_bytes(raw)
    tags = FLAC(path);tags['GBR_YAML'] = raw.decode('utf-8-sig');tags['custom_tag'] = 'retained';tags.save()
    aid = service.indexer.index(path);gid = service.db.one('SELECT id FROM generations')['id']
    yield service, repo, path, aid, gid, raw
    service.close()


def preview(data):
    service, repo, path, aid, gid, raw = data
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not plan['errors'], plan['errors']
    journal = repo/'_02_generation/_runtime/_cleanup'/plan['id']/'plan.json'
    journal.parent.mkdir(parents=True);cleanup.persist(journal, plan)
    return plan, journal


def test_preview_apply_reindex_undo_preserve_pcm_ids_and_originals(legacy):
    service, repo, path, aid, gid, raw = legacy
    original = repo/'_01_writing/_built/moses-supposes.yaml';original_bytes = original.read_bytes()
    before_audio = path.read_bytes()
    # Existing bookmark, assessment and review identities are independent of names.
    service.set_flags(gid, service.default_reviewer_id, bookmarked=True)
    bookmarks = service.db.all('SELECT * FROM bookmarks')
    plan, journal = preview(legacy)
    assert path.read_bytes() == before_audio and path.with_suffix('.yaml').read_bytes() == raw
    assert not (path.parent/('moses-supposes__'+aid[:12]+'.flac')).exists()
    cleanup.apply_plan(journal)
    target = path.with_name('moses-supposes__'+aid[:12]+'.flac')
    assert target.read_bytes() == before_audio
    assert audio_identity(target)[0] == aid
    assert not path.exists() and not path.with_suffix('.yaml').exists()
    updated = target.with_suffix('.yaml').read_bytes();values = YamlDocument(updated).values
    assert values['title'] == 'moses-supposes'
    assert values['lyrics'] == 'one\ntwo\n [chorus]'
    assert values['caption'] == 'calm\nsoft'
    assert values['sampler_steps'] == 25 and 'steps' not in values
    assert values['dit'] == 'minimax_music3_dit_fp32.safetensors'
    assert values['text_encoder'] == 'bf16' # Never guess a checkpoint from precision.
    assert updated.startswith(b'\xef\xbb\xbf# retained header\r\n')
    assert b'custom: [a, b] # keep exactly\r\n' in updated
    assert list(values)[:8] == ['song_id','variant_id','title','cluster','style','explicit','language','dit']
    assert service.db.all('SELECT * FROM bookmarks') == bookmarks
    service.indexer.index(target)
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == 1
    assert service.db.one('SELECT id FROM generations')['id'] == gid
    assert original.read_bytes() == original_bytes
    again = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not again['errors'] and not again['groups'] # idempotent
    cleanup.undo_plan(journal);cleanup.undo_plan(journal)
    assert path.read_bytes() == before_audio and path.with_suffix('.yaml').read_bytes() == raw
    assert not target.exists()
    service.indexer.index(path)
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == 1
    assert service.db.all('SELECT * FROM bookmarks') == bookmarks


def test_reject_changed_input_before_touching_any_files(legacy):
    service, repo, path, aid, gid, raw = legacy
    plan, journal = preview(legacy)
    path.with_suffix('.yaml').write_bytes(raw + b'# edited\r\n')
    with pytest.raises(ValueError, match='changed since preview'):cleanup.apply_plan(journal)
    assert path.exists() and json.loads(journal.read_text())['state'] == 'preview'


def test_reject_changed_db_and_destination_conflict(legacy):
    service, repo, path, aid, gid, raw = legacy
    plan, journal = preview(legacy)
    target = Path(next(i['destination'] for i in plan['groups'][0]['files'] if i['source']==str(path)))
    target.write_bytes(b'other file')
    with pytest.raises(ValueError, match='Destination exists'):cleanup.apply_plan(journal)
    assert target.read_bytes() == b'other file' and path.exists()
    target.unlink()
    service.db.execute("UPDATE generation_metadata SET text_encoder='changed' WHERE generation_id=?", (gid,))
    with pytest.raises(ValueError, match='Database changed'):cleanup.apply_plan(journal)
    assert path.exists()


def test_disk_failure_rolls_back_files_and_database(legacy, monkeypatch):
    service, repo, path, aid, gid, raw = legacy
    plan, journal = preview(legacy)
    original = cleanup.update_rows
    def fail(*args):raise OSError('simulated disk error before commit')
    monkeypatch.setattr(cleanup, 'update_rows', fail)
    with pytest.raises(OSError, match='simulated'):cleanup.apply_plan(journal)
    monkeypatch.setattr(cleanup, 'update_rows', original)
    assert path.exists() and path.with_suffix('.yaml').read_bytes() == raw
    assert json.loads(journal.read_text())['state'] == 'undone'
    assert service.db.one('SELECT id FROM generations')['id'] == gid
    assert not list(path.parent.glob('.gbr-*'))


def test_abrupt_termination_then_undo_recovers(legacy, monkeypatch):
    service, repo, path, aid, gid, raw = legacy
    plan, journal = preview(legacy)
    class Killed(BaseException):pass
    monkeypatch.setattr(cleanup, 'update_rows', lambda *a: (_ for _ in ()).throw(Killed()))
    with pytest.raises(Killed):cleanup.apply_plan(journal)
    assert json.loads(journal.read_text())['state'] == 'applying'
    cleanup.undo_plan(journal)
    assert path.exists() and path.with_suffix('.yaml').read_bytes() == raw
    assert not list(path.parent.glob('.gbr-*'))


def test_undo_refuses_later_edits_without_clobbering(legacy):
    _, _, path, aid, _, _ = legacy
    plan, journal = preview(legacy);cleanup.apply_plan(journal)
    target = path.with_name('moses-supposes__'+aid[:12]+'.yaml')
    target.write_bytes(target.read_bytes() + b'# new edit\r\n')
    with pytest.raises(ValueError, match='Target changed'):cleanup.undo_plan(journal)
    assert target.read_bytes().endswith(b'# new edit\r\n')


def test_protected_roots_and_ambiguous_models(legacy):
    service, repo, path, aid, gid, raw = legacy
    for root in (repo/'_01_writing', repo/'_02_generation/_runtime'):
        root.mkdir(exist_ok=True)
        with pytest.raises(ValueError, match='outside writing'):cleanup.make_plan(repo, service.db.path, [root])
    plan = cleanup.make_plan(repo, service.db.path, [path.parent], model_map={'text_encoder': {'bf16': 'exact-real-model.safetensors'}})
    assert not plan['errors']
    assert 'bf16 → exact-real-model.safetensors' in ' '.join(plan['groups'][0]['notes'])


def test_malformed_yaml_is_flagged_and_skip_errors_is_explicit(legacy):
    service, repo, path, aid, gid, raw = legacy
    # Real indexer tolerates malformed old metadata; cleanup refuses to guess.
    broken = b'song_id: 13\ntitle: a\ntitle: b\n'
    path.with_suffix('.yaml').write_bytes(broken)
    service.db.execute('UPDATE generation_metadata SET yaml_sha256=? WHERE generation_id=?',
                       (cleanup.hashlib.sha256(broken).hexdigest(), gid))
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert len(plan['errors']) == 1 and not plan['groups']
    journal = repo/'plan.json';cleanup.persist(journal, plan)
    with pytest.raises(ValueError, match='Preview contains errors'):cleanup.apply_plan(journal)
    cleanup.apply_plan(journal, skip_errors=True)
    assert path.exists() and path.with_suffix('.yaml').read_bytes() == broken


def test_newline_repairs_do_not_rewrite_urls_or_real_newlines():
    assert cleanup.newline_repair('A/n [verse]\\nB\nC https://site.test/news') == 'A\n [verse]\nB\nC https://site.test/news'
    assert cleanup.newline_repair('real\nline') == 'real\nline'


def test_archived_reviews_tag_revisions_and_publishing_survive_cleanup(legacy):
    service, repo, path, aid, gid, raw = legacy
    service.queues.open(service.default_reviewer_id, gid)
    scores = dict(audio_quality=5, composition=4, consistency=4, realism=4)
    first = service.review(gid, scores, 'full', reason='Keep this reason', bookmarked=True,
                           tags=[{'tag_id':'vocal_tone','polarity':'positive'}])['review_id']
    service.review(gid, scores, 'full', expected_review_id=first,
                   tags=[{'tag_id':'vocal_tone','polarity':'negative'}])
    for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending'"):
        service.archiver.process(op)
    assert not service.db.one("SELECT 1 FROM archive_operations WHERE status IN ('pending','running','failed')")
    archived = Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
    before = {table:service.db.all('SELECT * FROM '+table) for table in ['reviews','review_tags','bookmarks','publishing_selection','assessments']}
    data = service, repo, archived, aid, gid, raw
    plan, journal = preview(data);cleanup.apply_plan(journal)
    current = Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
    assert current.name == 'moses-supposes__'+aid[:12]+'.flac'
    assert not archived.exists() and current.exists()
    assert all(service.db.all('SELECT * FROM table'.replace('table', table)) == rows for table, rows in before.items())
    service.indexer.index(current)
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == 1
    assert not service.db.all('PRAGMA foreign_key_check')
    # Reindex schedules a harmless archive verification; finish before undo.
    for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending'"):
        service.archiver.process(op)
    cleanup.undo_plan(journal)
    assert archived.exists()


@pytest.mark.parametrize('missing_yaml', [False, True])
def test_native_bundle_receipts_artifacts_and_source_snapshot(legacy, missing_yaml):
    service, repo, path, aid, gid, raw = legacy
    source = path.parent/'source.yaml';source.write_bytes(b'# frozen source\nlyrics: original\n')
    report = path.parent/'generation.json'
    report.write_text(json.dumps({'run_id':'run-1', 'source_sha256':cleanup.digest(source),
                                 'worker_result':{'parameters':'original, never rewrite'},
                                 'files':{p.name:cleanup.digest(p) for p in (source,path,path.with_suffix('.yaml'))}}))
    with service.db.transaction() as conn:
        conn.executescript((ROOT/'_98_database/_gbr_database/schema.sql').read_text())
        conn.execute('INSERT INTO source_documents VALUES(?,?,?,?)', (cleanup.digest(source),source.read_bytes(),str(source),'now'))
        conn.execute('''INSERT INTO generation_runs(id,source_sha256,title,status,requested_json,effective_json,created_at)
                        VALUES(?,?,?,'succeeded','{}','{}','now')''', ('run-1',cleanup.digest(source),'original title'))
        for kind, p in [('audio',path), ('completed_yaml',path.with_suffix('.yaml')), ('source_yaml',source), ('generation_report',report)]:
            conn.execute('INSERT INTO generation_artifacts VALUES(?,?,?,?,?,?,?,?)', (kind,'run-1',kind,cleanup.digest(p),str(p),p.stat().st_size,None,'{}'))
        conn.execute('INSERT INTO generation_output_links VALUES(?,?,?)', ('run-1',gid,'audio'))
        conn.execute('INSERT INTO generation_execution_state VALUES(?,?,?,?)', ('run-1','original-revision','audio',str(path.parent)))
    frozen = service.db.all('SELECT * FROM generation_runs')
    before_report = report.read_bytes();before_source = source.read_bytes()
    if missing_yaml:
        path.with_suffix('.yaml').unlink()
    plan,journal = preview(legacy);cleanup.apply_plan(journal)
    assert source.read_bytes() == before_source
    marker = json.loads(report.read_text())
    assert marker['worker_result'] == json.loads(before_report)['worker_result']
    for filename,checksum in marker['files'].items():assert cleanup.digest(report.parent/filename) == checksum
    for artifact in service.db.all('SELECT * FROM generation_artifacts'):
        assert cleanup.digest(artifact['uri']) == artifact['sha256']
        assert Path(artifact['uri']).stat().st_size == artifact['size_bytes']
    assert service.db.all('SELECT * FROM generation_runs') == frozen
    assert service.db.one('SELECT output_stem FROM generation_execution_state')['output_stem'] == 'moses-supposes__'+aid[:12]
    assert service.db.all('PRAGMA foreign_key_check') == []
    cleanup.undo_plan(journal)
    assert path.exists() and report.read_bytes() == before_report and source.read_bytes() == before_source
    assert path.with_suffix('.yaml').exists() is (not missing_yaml)


def test_application_lock_blocks_cleanup(legacy):
    service, _, path, _, _, _ = legacy
    from _review.__main__ import ProcessLock
    plan,journal = preview(legacy)
    lock = ProcessLock(service.db.path.with_name(service.db.path.name+'.lock'))
    try:
        with pytest.raises(SystemExit, match='already using'):cleanup.apply_plan(journal)
    finally:lock.close()
    assert path.exists()


def test_crash_after_commit_before_journal_finalization_is_recoverable(legacy, monkeypatch):
    service, _, path, _, gid, raw = legacy
    plan,journal = preview(legacy)
    real_persist = cleanup.persist
    class Killed(BaseException):pass
    def crash_on_applied(path, document):
        if document['state'] == 'applied':raise Killed()
        return real_persist(path, document)
    monkeypatch.setattr(cleanup,'persist',crash_on_applied)
    with pytest.raises(Killed):cleanup.apply_plan(journal)
    assert not path.exists()
    assert json.loads(journal.read_text())['state'] == 'applying'
    monkeypatch.setattr(cleanup,'persist',real_persist)
    cleanup.undo_plan(journal)
    assert path.exists() and path.with_suffix('.yaml').read_bytes() == raw
    assert service.db.one('SELECT id FROM generations')['id'] == gid


def test_partial_audio_move_and_partial_text_stage_are_recoverable(legacy, monkeypatch):
    _, _, path, _, _, raw = legacy
    plan,journal = preview(legacy)
    class Killed(BaseException):pass
    def die_after_link(source, target):
        cleanup.os.link(source,target)
        raise Killed()
    monkeypatch.setattr(cleanup,'move_exclusive',die_after_link)
    with pytest.raises(Killed):cleanup.apply_plan(journal)
    text_item = next(i for i in plan['groups'][0]['files'] if 'content' in i)
    Path(text_item['stage']).write_bytes(b'partial write')
    cleanup.undo_plan(journal)
    assert path.exists() and path.with_suffix('.yaml').read_bytes() == raw
    assert len(list(path.parent.glob('*.flac'))) == 1
    assert not list(path.parent.glob('.gbr-*'))


def test_preview_reports_progress_and_hashes_audio_once(legacy, monkeypatch):
    service, repo, path, _, _, raw = legacy
    real_digest = cleanup.digest
    reads = []
    def tracked_digest(filename):
        reads.append(Path(filename))
        return real_digest(filename)
    monkeypatch.setattr(cleanup, 'digest', tracked_digest)
    messages = []
    before_audio = path.read_bytes()
    plan = cleanup.make_plan(repo, service.db.path, [path.parent], progress=messages.append)
    assert not plan['errors']
    assert reads.count(path) == 1
    assert any('Loaded media_files:' in message for message in messages)
    assert any('1/1 indexed recordings' in message and str(path) in message for message in messages)
    assert any('Checking for unindexed FLACs:' in message for message in messages)
    assert messages[-1].startswith('Preview checked in ')
    assert path.read_bytes() == before_audio and path.with_suffix('.yaml').read_bytes() == raw


def test_preview_prunes_worker_scratch_and_deduplicates_nested_roots(legacy, monkeypatch):
    service, repo, path, _, _, _ = legacy
    scratch = repo / '_02_generation/_runtime/attempts/private'
    scratch.mkdir(parents=True)
    (scratch / 'audio.flac').write_bytes(b'worker scratch; never inspect')
    nested = path.parent / 'older'
    nested.mkdir()
    extra = nested / 'unindexed.flac'
    extra.write_bytes(b'unindexed; report without decoding')
    real_walk = cleanup.os.walk
    visited = []
    def tracked_walk(*args, **kwargs):
        for entry in real_walk(*args, **kwargs):
            visited.append(Path(entry[0]))
            yield entry
    monkeypatch.setattr(cleanup.os, 'walk', tracked_walk)
    plan = cleanup.make_plan(repo, service.db.path, [repo / '_02_generation', nested])
    assert not plan['errors']
    assert len(plan['warnings']) == 1 and str(extra) in plan['warnings'][0]
    assert not any(directory.is_relative_to(repo / '_02_generation/_runtime') for directory in visited)


def test_cli_plan_above_64_mib_can_preview_apply_and_undo(legacy, monkeypatch, capsys):
    service, repo, path, aid, _, raw = legacy
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not plan['errors']
    # Exercise the former size gate through the real CLI and journal lifecycle,
    # without needing thousands of long audio files in the test fixture.
    plan['large_collection_fixture'] = ['x' * 1024**2] * 65
    monkeypatch.setattr(cleanup, 'make_plan', lambda *args, **kwargs: plan)
    before = path.read_bytes()
    assert cleanup.main(repo, ['preview', '--root', str(path.parent),
                               '--database', str(service.db.path)]) == 0
    journal = repo / '_02_generation/_runtime/_cleanup' / plan['id'] / 'plan.json'
    assert journal.stat().st_size > 64 * 1024**2
    assert 'Plan size: 65.' in capsys.readouterr().out
    assert path.read_bytes() == before and path.with_suffix('.yaml').read_bytes() == raw
    cleanup.apply_plan(journal)
    target = path.with_name('moses-supposes__' + aid[:12] + '.flac')
    assert target.read_bytes() == before
    cleanup.undo_plan(journal)
    assert path.read_bytes() == before and path.with_suffix('.yaml').read_bytes() == raw
    with journal.open(encoding='utf-8') as handle:
        restored = json.load(handle)
    assert restored['state'] == 'undone'
    assert restored['large_collection_fixture'] == plan['large_collection_fixture']
    assert not list(journal.parent.glob('*.tmp'))


@pytest.mark.parametrize('failure', ['write', 'fsync', 'replace'])
def test_streamed_journal_failure_keeps_previous_recovery_data(tmp_path, monkeypatch, failure):
    journal = tmp_path / 'plan.json'
    original = {'state': 'preview', 'original': 'Recover this exact journal'}
    cleanup.persist(journal, original)
    before = journal.read_bytes()
    def broken(*args, **kwargs):
        if failure == 'write':
            args[1].write('{"partial":')
        raise OSError('simulated disk failure')
    if failure == 'write':
        monkeypatch.setattr(cleanup.json, 'dump', broken)
    else:
        monkeypatch.setattr(cleanup.os, failure, broken)
    with pytest.raises(OSError, match='simulated disk failure'):
        cleanup.persist(journal, {'state': 'applying', 'new': 'Uncommitted replacement'})
    assert journal.read_bytes() == before
    assert list(tmp_path.iterdir()) == [journal]


def test_missing_completed_yaml_is_recovered_and_undo_removes_only_created_file(legacy):
    service, repo, path, aid, gid, raw = legacy
    path.with_suffix('.yaml').unlink()
    original_audio = path.read_bytes()
    plan, journal = preview(legacy)
    assert len([item for item in plan['groups'][0]['files'] if item.get('created')]) == 1
    target = path.with_name('moses-supposes__' + aid[:12] + '.yaml')
    cleanup.apply_plan(journal)
    assert YamlDocument(target.read_bytes()).values['title'] == 'moses-supposes'
    assert service.db.one('SELECT yaml_archive_path FROM generation_metadata')['yaml_archive_path'] == str(target)
    assert service.db.one('SELECT yaml_path FROM generation_sources')['yaml_path'] == str(target)
    assert service.db.one('SELECT id FROM generations')['id'] == gid
    cleanup.undo_plan(journal)
    assert path.read_bytes() == original_audio
    assert not path.with_suffix('.yaml').exists() and not target.exists()
    assert service.db.one('SELECT raw_yaml_bytes FROM generation_metadata')['raw_yaml_bytes'] == raw


def test_missing_yaml_recovery_refuses_corrupted_database_snapshot(legacy):
    service, repo, path, _, _, _ = legacy
    path.with_suffix('.yaml').unlink()
    service.db.execute('UPDATE generation_metadata SET raw_yaml_bytes=?', (b'corrupted snapshot',))
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not plan['groups']
    assert 'no checksum-verified database snapshot' in plan['errors'][0]['error']
    assert plan['errors'][0]['title']
    assert str(path) in plan['errors'][0]['paths']


def test_edited_recovered_yaml_is_preserved_on_undo(legacy):
    _, _, path, aid, _, _ = legacy
    path.with_suffix('.yaml').unlink()
    plan, journal = preview(legacy)
    cleanup.apply_plan(journal)
    target = path.with_name('moses-supposes__' + aid[:12] + '.yaml')
    target.write_bytes(target.read_bytes() + b'# later edit\n')
    with pytest.raises(ValueError, match='Recovered YAML changed'):
        cleanup.undo_plan(journal)
    assert target.read_bytes().endswith(b'# later edit\n')


def test_recovery_rolls_back_after_database_write_failure(legacy, monkeypatch):
    _, _, path, _, _, _ = legacy
    path.with_suffix('.yaml').unlink()
    original_audio = path.read_bytes()
    _, journal = preview(legacy)
    def fail(*args):
        raise OSError('database failure during recovery')
    monkeypatch.setattr(cleanup, 'update_rows', fail)
    with pytest.raises(OSError, match='database failure'):
        cleanup.apply_plan(journal)
    assert path.read_bytes() == original_audio
    assert not list(path.parent.glob('*.yaml'))
    assert json.loads(journal.read_text())['state'] == 'undone'


def test_adjacent_yaml_relinks_stale_sources_without_creating_another_copy(legacy):
    service, repo, path, aid, gid, _ = legacy
    stale = str(path.parent / 'old-name.yaml')
    service.db.execute('UPDATE generation_sources SET yaml_path=?', (stale,))
    service.db.execute('UPDATE generation_metadata SET yaml_archive_path=?', (stale,))
    plan, journal = preview(legacy)
    assert not any(item.get('created') for item in plan['groups'][0]['files'])
    cleanup.apply_plan(journal)
    target = path.with_name('moses-supposes__' + aid[:12] + '.yaml')
    assert service.db.one('SELECT yaml_path FROM generation_sources')['yaml_path'] == str(target)
    service.indexer.index(target.with_suffix('.flac'))
    assert service.db.one('SELECT COUNT(*) n FROM generations')['n'] == 1
    assert service.db.one('SELECT id FROM generations')['id'] == gid


def test_exact_catalogue_alias_resolves_legacy_song_title_and_map_is_explicit(legacy):
    service, repo, path, aid, gid, raw = legacy
    metadata = service.db.one('SELECT parsed_json FROM generation_metadata')['parsed_json']
    values = json.loads(metadata)
    values.pop('title', None);values.pop('song_id', None)
    values['song_title'] = 'moses-supposes-v2_CFG-1.6'
    service.db.execute('UPDATE generation_metadata SET parsed_json=?', (json.dumps(values),))
    service.db.execute("UPDATE songs SET title='legacy unknown'")
    service.db.execute("UPDATE generations SET original_filename='audio.flac',song_id=(SELECT id FROM songs LIMIT 1)")
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not plan['errors']
    # An explicit mapping authorises the correction even if old metadata has
    # a numeric ID absent from the current catalogue; no silent fuzzy matching.
    values['song_id'] = 999
    service.db.execute('UPDATE generation_metadata SET parsed_json=?', (json.dumps(values),))
    blocked = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert blocked['errors']
    mapped = cleanup.make_plan(repo, service.db.path, [path.parent], identity_map={aid: '13'})
    assert not mapped['errors'] and mapped['identity_overrides'] == {aid: '13'}


def test_issues_csv_roundtrip_and_invalid_confirmation_is_rejected(legacy):
    service, repo, path, aid, _, _ = legacy
    path.with_suffix('.yaml').unlink()
    service.db.execute('UPDATE generation_metadata SET raw_yaml_bytes=NULL,raw_yaml=NULL')
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    report = path.parent / 'issues.csv'
    report.write_bytes(cleanup.issues_report(plan))
    assert cleanup.read_identity_map(report) == {}
    rows = list(cleanup.csv.DictReader(report.read_text(encoding='utf-8-sig').splitlines()))
    rows[0]['song_id'] = '13'
    with report.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = cleanup.csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader();writer.writerows(rows)
    assert cleanup.read_identity_map(report) == {aid: '13'}
    with pytest.raises(ValueError, match='unknown audio ID'):
        cleanup.make_plan(repo, service.db.path, [path.parent], identity_map={aid: '999'})


def test_archiver_reconcile_repairs_missing_archive_index_and_preserves_reviews(legacy):
    service, _, path, _, gid, _ = legacy
    service.queues.open(service.default_reviewer_id, gid)
    service.review(gid, dict(audio_quality=4, composition=4, consistency=4, realism=4), 'full')
    for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending'"):
        service.archiver.process(op)
    archive = Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
    assert archive.exists()
    before = service.db.all('SELECT * FROM reviews')
    service.db.execute("UPDATE media_files SET storage_state='missing' WHERE path=?", (str(archive),))
    service.archiver.reconcile()
    pending = service.db.all("SELECT * FROM archive_operations WHERE status='pending'")
    assert len(pending) == 1
    service.archiver.process(pending[0])
    assert service.db.one("SELECT 1 AS n FROM media_files WHERE path=? AND storage_state='archived'", (str(archive),))
    assert service.db.all('SELECT * FROM reviews') == before
    assert not service.db.all('PRAGMA foreign_key_check')


def test_archiver_reconcile_consolidates_reviewed_duplicates(legacy):
    service, _, path, _, gid, _ = legacy
    service.queues.open(service.default_reviewer_id, gid)
    service.review(gid, dict(audio_quality=4, composition=4, consistency=4, realism=4), 'full')
    for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending'"):
        service.archiver.process(op)
    archive = Path(service.db.one('SELECT archive_path FROM audio')['archive_path'])
    original = archive.read_bytes()
    duplicate = path.parent / 'duplicate.flac'
    duplicate.write_bytes(original)
    duplicate.with_suffix('.yaml').write_bytes(archive.with_suffix('.yaml').read_bytes())
    service.indexer.index(duplicate)
    service.db.execute("UPDATE archive_operations SET status='superseded' WHERE status='pending'")
    service.archiver.reconcile()
    pending = service.db.all("SELECT * FROM archive_operations WHERE status='pending'")
    assert len(pending) == 1
    service.archiver.process(pending[0])
    assert not duplicate.exists() and archive.read_bytes() == original
    assert service.db.one("SELECT COUNT(*) n FROM media_files WHERE storage_state IN ('input','archived')")['n'] == 1


def test_three_metadata_revisions_can_share_the_second_yaml_target(legacy):
    service, repo, path, aid, _, raw = legacy
    second_yaml = path.parent / 'second.yaml'
    second_yaml.write_bytes(raw.replace(b'custom: [a, b]', b'custom: [c, d]'))
    for number in (2, 3):
        other = path.parent / f'revision-{number}.flac'
        other.write_bytes(path.read_bytes())
        tags = FLAC(other);tags['custom_tag'] = f'revision-{number}';tags.save()
        other.with_suffix('.yaml').write_bytes(second_yaml.read_bytes())
        service.indexer.index(other)
        gid = service.db.one('SELECT id FROM generations WHERE original_filename=?', (other.name,))['id']
        service.db.execute('UPDATE generation_sources SET yaml_path=? WHERE generation_id=?', (str(second_yaml), gid))
        service.db.execute('UPDATE generation_metadata SET yaml_archive_path=? WHERE generation_id=?', (str(second_yaml), gid))
        service.db.execute("UPDATE media_files SET storage_state='superseded' WHERE path=?", (str(other),))
    plan = cleanup.make_plan(repo, service.db.path, [path.parent])
    assert not plan['errors']
    assert len(plan['groups']) == 1
    assert len([item for item in plan['groups'][0]['files'] if item['destination'].endswith('.yaml')]) == 2


def test_recovery_refuses_destination_added_after_preview(legacy):
    _, _, path, _, _, _ = legacy
    path.with_suffix('.yaml').unlink()
    plan, journal = preview(legacy)
    item = next(item for item in plan['groups'][0]['files'] if item.get('created'))
    target = Path(item['destination'])
    target.write_bytes(b'User file; never overwrite')
    with pytest.raises(ValueError, match='Recovery destination appeared'):
        cleanup.apply_plan(journal)
    assert target.read_bytes() == b'User file; never overwrite' and path.exists()


def test_cli_issues_report_can_be_confirmed_and_edited_report_is_not_deleted(legacy, monkeypatch, capsys):
    service, repo, path, aid, _, _ = legacy
    values = json.loads(service.db.one('SELECT parsed_json FROM generation_metadata')['parsed_json'])
    values['song_id'] = 999
    service.db.execute('UPDATE generation_metadata SET parsed_json=?', (json.dumps(values),))
    args = ['preview', '--root', str(path.parent), '--database', str(service.db.path)]
    assert cleanup.main(repo, args) == 2
    plans = list((repo / '_02_generation/_runtime/_cleanup').glob('*/plan.json'))
    assert len(plans) == 1
    report = plans[0].parent / 'issues.csv'
    assert report.exists()
    rows = list(cleanup.csv.DictReader(report.read_text(encoding='utf-8-sig').splitlines()))
    assert rows[0]['audio_id'] == aid and rows[0]['title']
    rows[0]['song_id'] = '13'
    with report.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = cleanup.csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader();writer.writerows(rows)
    with pytest.raises(ValueError, match='Edited issues.csv retained'):
        cleanup.main(repo, ['forget', str(plans[0])])
    assert report.exists()
    assert cleanup.main(repo, [*args, '--identity-map', str(report)]) == 0
    confirmed = next(plan for plan in (repo / '_02_generation/_runtime/_cleanup').glob('*/plan.json') if plan != plans[0])
    cleanup.apply_plan(confirmed)
    assert json.loads(service.db.one('SELECT parsed_json FROM generation_metadata')['parsed_json'])['song_id'] == 13
    cleanup.undo_plan(confirmed)
    assert json.loads(service.db.one('SELECT parsed_json FROM generation_metadata')['parsed_json'])['song_id'] == 999


def test_archiver_recovery_does_not_write_a_corrupt_snapshot(legacy):
    service, _, path, _, gid, _ = legacy
    service.queues.open(service.default_reviewer_id, gid)
    service.review(gid, dict(audio_quality=4, composition=4, consistency=4, realism=4), 'full')
    path.with_suffix('.yaml').unlink()
    service.db.execute('UPDATE generation_metadata SET raw_yaml_bytes=?', (b'corrupt database copy',))
    op = service.db.one("SELECT * FROM archive_operations WHERE status='pending'")
    service.archiver.process(op)
    result = service.db.one('SELECT * FROM archive_operations WHERE id=?', (op['id'],))
    assert result['status'] == 'failed' and 'No checksum-verified YAML snapshot' in result['error']
    assert path.exists()
    assert not (service.config.data / 'recovery').exists()

