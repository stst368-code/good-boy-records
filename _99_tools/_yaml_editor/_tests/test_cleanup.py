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


def test_native_bundle_receipts_artifacts_and_source_snapshot(legacy):
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

