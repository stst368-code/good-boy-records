"""Permanent, previewed removal of unrecoverable local recordings.

The existing shared DB records the commit marker. No backup DB, trash copy or
filename-pattern deletion is created. Apply can resume after a file is locked.
"""
from collections import defaultdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3

from _review.util import digest, now, dumps


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def row_hash(row):
    from .cleanup import packed
    return hashlib.sha256(json.dumps({k: packed(v) for k, v in row.items()}, sort_keys=True,
                                     ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


def snapshot(conn):
    tables, foreign = {}, {}
    for entry in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
        table = entry[0]
        tables[table] = [dict(row) for row in conn.execute('SELECT rowid AS __prune_rowid__,* FROM ' + quoted(table))]
        keys = defaultdict(list)
        for fk in conn.execute('PRAGMA foreign_key_list(' + quoted(table) + ')'):
            keys[fk['id']].append(dict(fk))
        foreign[table] = list(keys.values())
    return tables, foreign


def dependencies(tables, foreign, audio_ids):
    selected = {name: set() for name in tables}
    gids = {r['id'] for r in tables.get('generations', []) if r['audio_id'] in audio_ids}
    # Telemetry belongs to a removed run only if none of its outputs survives.
    outputs = defaultdict(set)
    for row in tables.get('generation_output_links', []):
        outputs[row['run_id']].add(row['review_generation_id'])
    runs = {run for run, members in outputs.items() if members and members <= gids}
    legacy = defaultdict(set)
    for row in tables.get('legacy_output_links', []):
        legacy[(row['source_id'], row['job_id'])].add(row['review_generation_id'])
    jobs = {key for key, members in legacy.items() if members and members <= gids}
    for table, rows in tables.items():
        if table == 'sessions':
            continue
        for row in rows:
            direct = (table == 'audio' and row['id'] in audio_ids
                      or row.get('audio_id') in audio_ids
                      or any(row.get(key) in gids for key in ('generation_id', 'review_generation_id', 'generation_a', 'generation_b'))
                      or table == 'generations' and row['id'] in gids
                      or table == 'generation_runs' and row['id'] in runs
                      or table in {'legacy_generation_jobs', 'legacy_generation_timings'}
                      and (row.get('source_id'), row.get('job_id')) in jobs)
            if direct:
                selected[table].add(row['__prune_rowid__'])
    changed = True
    while changed:
        changed = False
        tasks = {row['id'] for row in tables.get('processing_tasks', [])
                 if row['__prune_rowid__'] in selected.get('processing_tasks', set())}
        shared_artifacts = {row['artifact_id'] for row in tables.get('generation_output_links', [])
                            if row['review_generation_id'] not in gids and row.get('artifact_id')}
        for row in tables.get('generation_artifacts', []):
            if row['id'] in shared_artifacts:
                continue
            try:
                metadata = json.loads(row['metadata_json'])
                if not isinstance(metadata, dict):metadata = {}
            except (ValueError, TypeError):
                metadata = {}
            if metadata.get('processing_task_id') in tasks and row['__prune_rowid__'] not in selected['generation_artifacts']:
                selected['generation_artifacts'].add(row['__prune_rowid__']);changed = True
        for child, groups in foreign.items():
            if child == 'sessions':
                continue
            for group in groups:
                group = sorted(group, key=lambda row: row['seq'])
                parent = group[0]['table']
                if not selected.get(parent):
                    continue
                # Rerun ancestry is a reference, not ownership of the new run.
                if child == 'generation_runs' and any(fk['from'] == 'parent_review_generation_id' for fk in group):
                    continue
                targets = {tuple(row[fk['to']] for fk in group) for row in tables[parent]
                           if row['__prune_rowid__'] in selected[parent]}
                for row in tables[child]:
                    rid = row['__prune_rowid__']
                    value = tuple(row[fk['from']] for fk in group)
                    if rid not in selected[child] and all(v is not None for v in value) and value in targets:
                        selected[child].add(rid);changed = True
        # Historical actions did not always declare their review foreign keys.
        reviews = {r['id'] for r in tables.get('reviews', []) if r['__prune_rowid__'] in selected.get('reviews', set())}
        for row in tables.get('review_history', []):
            if any(row.get(k) in reviews for k in ('review_id', 'previous_review_id')):
                rid = row['__prune_rowid__']
                if rid not in selected['review_history']:
                    selected['review_history'].add(rid);changed = True
    updates = []
    for table, columns in [('sessions', ('current_generation_id', 'previous_generation_id', 'opened_generation_id')),
                           ('generation_runs', ('parent_review_generation_id',))]:
        for row in tables.get(table, []):
            if row['__prune_rowid__'] in selected[table]:
                continue
            values = {key: None for key in columns if row.get(key) in gids}
            if values:
                updates.append({'table': table, 'rowid': row['__prune_rowid__'], 'sha256': row_hash(row), 'values': values})
    guards = {table: [{'rowid': row['__prune_rowid__'], 'sha256': row_hash(row)} for row in rows
                      if row['__prune_rowid__'] in selected[table]] for table, rows in tables.items() if selected[table]}
    return selected, guards, updates


FILE_COLUMNS = {'audio': ('archive_path',), 'media_files': ('path', 'original_path'),
                'generation_metadata': ('yaml_archive_path',), 'generation_sources': ('yaml_path', 'original_yaml_path'),
                'generation_artifacts': ('uri',), 'analyses': ('preview_path', 'spectrogram_path'),
                'source_documents': ('source_path',), 'generation_batch_inputs': ('source_path',)}


def file_references(tables, cache_root=None):
    refs = defaultdict(list)
    for table, columns in FILE_COLUMNS.items():
        for row in tables.get(table, []):
            for column in columns:
                value = row.get(column)
                if value and isinstance(value, str) and '://' not in value:
                    refs[str(Path(value).resolve())].append((table, row))
    if cache_root:
        for row in tables.get('playback_cache', []):
            name = row['filename']
            if name and Path(name).name == name and name not in {'.', '..'}:
                refs[str((Path(cache_root) / name).resolve())].append(('playback_cache', row))
    return refs


def protected_path(path, repo):
    path = Path(path)
    # A terminal, ledger-owned worker output is removable too. Ownership and
    # active-run checks are separate; never sweep a worker directory by name.
    return (path.resolve().is_relative_to((Path(repo) / '_01_writing').resolve())
            or any(part.startswith('.gbr-') for part in path.parts)
            or any(p.is_symlink() for p in (path, *path.parents)))


def protected_reference(value, owners, repo):
    for table, row in owners:
        for column in FILE_COLUMNS.get(table, ()):
            raw = row.get(column)
            if raw and '://' not in raw and str(Path(raw).resolve()) == value and protected_path(Path(raw), repo):
                return True
    return False


def owner_audio_ids(table, row, tables):
    if table == 'audio':return {row['id']}
    if row.get('audio_id'):return {row['audio_id']}
    generation_ids = {row.get('generation_id')}
    if table == 'generation_artifacts':
        generation_ids.update(link['review_generation_id'] for link in tables.get('generation_output_links', [])
                              if link['run_id'] == row.get('run_id') or link.get('artifact_id') == row['id'])
    return {g['audio_id'] for g in tables.get('generations', []) if g['id'] in generation_ids}


def make_plan(plan, config_path=None, progress=None):
    emit = progress or (lambda message: None)
    with closing(sqlite3.connect(Path(plan['database']).as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        tables, foreign = snapshot(conn)
    requested = {row['audio_id'] for row in plan['errors']}
    # An active job is not an unrecoverable historical recording. Likewise a
    # live website release must retain its withdrawal records until withdrawn.
    gids = {row['id'] for row in tables.get('generations', []) if row['audio_id'] in requested}
    blocked = set()
    reasons = []
    generation_audio = {row['id']: row['audio_id'] for row in tables.get('generations', [])}
    for row in tables.get('processing_tasks', []):
        if row.get('generation_id') in gids and row['status'] in {'queued', 'running'}:
            blocked.add(generation_audio[row['generation_id']])
    run_audio = defaultdict(set)
    for row in tables.get('generation_output_links', []):
        if row['review_generation_id'] in gids:
            run_audio[row['run_id']].add(generation_audio[row['review_generation_id']])
    for row in tables.get('generation_runs', []):
        if row['status'] not in {'succeeded', 'failed', 'cancelled', 'interrupted'}:
            blocked.update(run_audio.get(row['id'], ()))
    for row in tables.get('publishing_selection', []):
        if row['generation_id'] in gids and row['status'] == 'published':
            blocked.add(generation_audio[row['generation_id']])
    for aid in sorted(blocked):
        reasons.append({'path': aid, 'reason': 'Live job or published release retained; not classified as unrecoverable'})
    audio_ids = requested - blocked
    selected, guards, updates = dependencies(tables, foreign, audio_ids)
    cache_root = None
    if config_path and Path(config_path).is_file():
        from _review.config import Config
        cache_root = str(Config(config_path).data / 'cache/playback')
    references = file_references(tables, cache_root)
    files, retained = [], reasons
    audio_paths = {str(Path(row[column]).resolve()): row.get('audio_id', row.get('id'))
                   for table, column in [('audio', 'archive_path'), ('media_files', 'path'), ('media_files', 'original_path')]
                   for row in tables.get(table, []) if row.get(column) and row['__prune_rowid__'] in selected.get(table, set())}
    from _review.indexer import audio_identity
    for index, (value, owners) in enumerate(references.items(), 1):
        if not any(row['__prune_rowid__'] in selected.get(table, set()) for table, row in owners):
            continue
        path = Path(value)
        if not path.exists():
            continue
        if (not path.is_file() or protected_path(path, Path(plan['repo']))
                or protected_reference(value, owners, Path(plan['repo']))):
            retained.append({'path': value, 'reason': 'Protected Writing input, staging file, symlink or non-file'});continue
        if any(row['__prune_rowid__'] not in selected.get(table, set()) for table, row in owners):
            retained.append({'path': value, 'reason': 'Shared with a surviving recording'});continue
        emit(f'Checking removal candidate {index}: {value}')
        sha = digest(path)
        if value in audio_paths:
            try:
                identity = audio_identity(path)[0]
            except (OSError, ValueError, RuntimeError):
                retained.append({'path': value, 'reason': 'Undecodable audio retained; PCM ownership cannot be verified'});continue
            if identity != audio_paths[value]:
                retained.append({'path': value, 'reason': 'Audio identity differs from the database; unrelated bytes retained'});continue
        else:
            expected = {row.get('sha256') for table, row in owners if table == 'generation_artifacts'}
            expected.update(row.get('yaml_sha256') for table, row in owners if table == 'generation_metadata')
            generations = {row['generation_id'] for table, row in owners if table == 'generation_sources'}
            expected.update(row['yaml_sha256'] for row in tables.get('generation_metadata', []) if row['generation_id'] in generations)
            expected.discard(None)
            if expected and sha not in expected:
                retained.append({'path': value, 'reason': 'File differs from recorded checksum; unrelated bytes retained'});continue
        files.append({'path': value, 'sha256': sha})
    for group in plan['groups']:
        for item in group.get('duplicate_files', []):
            path = Path(item['path'])
            owners = references.get(str(path.resolve()), [])
            allowed = {group['audio_id']}
            if (protected_path(path, Path(plan['repo'])) or protected_reference(str(path.resolve()), owners, Path(plan['repo']))
                    or any(not owner_audio_ids(table, row, tables) or not owner_audio_ids(table, row, tables) <= allowed
                           for table, row in owners)):
                retained.append({'path': str(path), 'reason': 'Duplicate is protected or shared with another recording'});continue
            files.append(dict(item))
    return {'audio_ids': sorted(audio_ids), 'rows': guards, 'updates': updates, 'files': files,
            'recordings': [{'audio_id': row['audio_id'], 'title': row.get('title') or row['audio_id'], 'reason': row['error']}
                           for row in plan['errors'] if row['audio_id'] in audio_ids],
            'retained': retained, 'cache_root': cache_root, 'state': 'planned'}


def committed(plan, conn=None):
    if conn is None:
        with closing(sqlite3.connect(plan['database'])) as opened:
            return committed(plan, opened)
    return conn.execute("SELECT 1 FROM review_history WHERE action='generation_cleanup_prune' AND json_extract(details_json,'$.cleanup_id')=?",
                        (plan['id'],)).fetchone() is not None


def validate(plan, conn):
    tables, foreign = snapshot(conn)
    _, rows, updates = dependencies(tables, foreign, set(plan['pruning']['audio_ids']))
    if rows != plan['pruning']['rows'] or updates != plan['pruning']['updates']:
        raise ValueError('Pruning records changed since preview; preview again')
    references = file_references(tables, plan['pruning'].get('cache_root'))
    selected_ids = {table: {row['rowid'] for row in entries} for table, entries in rows.items()}
    for item in plan['pruning']['files']:
        path = Path(item['path'])
        owners = references.get(str(path.resolve()), [])
        if (protected_path(path, Path(plan['repo'])) or protected_reference(str(path.resolve()), owners, Path(plan['repo']))
                or not path.is_file() or digest(path) != item['sha256']):
            raise ValueError(f'Pruning file changed since preview: {path}')
        survivor = item.get('survivor_audio_id')
        if any(row['__prune_rowid__'] not in selected_ids.get(table, set())
               and not (survivor and owner_audio_ids(table, row, tables) and owner_audio_ids(table, row, tables) <= {survivor})
               for table, row in owners):
            raise ValueError(f'Pruning file is now shared with a surviving recording: {path}')


def validate_plan(plan):
    with closing(sqlite3.connect(Path(plan['database']).as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        validate(plan, conn)


def apply(plan_path, plan):
    from .cleanup import persist
    from _review.__main__ import ProcessLock
    removal = plan['pruning']
    if removal['state'] == 'done':
        return plan
    if not removal['audio_ids'] and not removal['files']:
        removal['state'] = 'done'
        persist(plan_path, plan)
        return plan
    lock = ProcessLock(Path(plan['database']).with_name(Path(plan['database']).name + '.lock'))
    try:
        with closing(sqlite3.connect(plan['database'])) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute('PRAGMA foreign_keys=ON')
            conn.execute('BEGIN IMMEDIATE')
            if not committed(plan, conn):
                validate(plan, conn)
                conn.execute('PRAGMA defer_foreign_keys=ON')
                for update in removal['updates']:
                    columns = list(update['values'])
                    conn.execute('UPDATE ' + quoted(update['table']) + ' SET ' + ','.join(quoted(k) + '=?' for k in columns)
                                 + ' WHERE rowid=?', [update['values'][k] for k in columns] + [update['rowid']])
                for table, rows in removal['rows'].items():
                    conn.executemany('DELETE FROM ' + quoted(table) + ' WHERE rowid=?', [(row['rowid'],) for row in rows])
                if conn.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('Pruning would leave broken database relationships; no deletion committed')
                conn.execute('INSERT INTO review_history(action,details_json,created_at) VALUES(?,?,?)',
                             ('generation_cleanup_prune', dumps({'cleanup_id': plan['id'], 'recordings': len(removal['audio_ids']),
                                                                 'files': len(removal['files']), 'removed_recordings': removal['recordings'],
                                                                 'removed_files': removal['files'], 'retained': removal['retained']}), now()))
                conn.commit()
            else:
                conn.rollback()
        removal['state'] = 'committed'
        persist(plan_path, plan)
        with closing(sqlite3.connect(Path(plan['database']).as_uri() + '?mode=ro', uri=True)) as reader:
            reader.row_factory = sqlite3.Row
            current, _ = snapshot(reader)
            references = file_references(current, removal.get('cache_root'))
        # Only verified, explicitly listed files are unlinked. A lock or process
        # interruption leaves a resumable journal, not a second copy or DB.
        for index, item in enumerate(removal['files'], 1):
            path = Path(item['path'])
            if not path.exists():
                continue
            owners = references.get(str(path.resolve()), [])
            survivor = item.get('survivor_audio_id')
            if owners and not (survivor and all(owner_audio_ids(table, row, current)
                                               and owner_audio_ids(table, row, current) <= {survivor} for table, row in owners)):
                raise ValueError(f'File acquired a new database owner after pruning; retained it: {path}')
            if protected_path(path, Path(plan['repo'])) or digest(path) != item['sha256']:
                raise ValueError(f'Pruning file changed after database commit; retained it: {path}')
            print(f'[{index}/{len(removal["files"])}] Removing {path}', flush=True)
            path.unlink()
        for root in (Path(plan['repo']) / '_02_generation/_generated', Path(plan['repo']) / '_04_preparation/_tracks',
                     Path(plan['repo']) / '_05_showcase'):
            for item in removal['files']:
                folder = Path(item['path']).parent
                while folder != root and folder.is_relative_to(root):
                    try:
                        folder.rmdir()
                    except OSError:
                        break
                    folder = folder.parent
        removal['state'] = 'done'
        persist(plan_path, plan)
        return plan
    finally:
        lock.close()


def recover_existing(repo, database, config_path, progress=None):
    """Reuse indexing and verified archive recovery; never start GPU analysis."""
    from _review.__main__ import ProcessLock
    from _review.config import Config
    from _review.service import Service
    from _review.indexer import audio_identity
    from .cleanup import protected
    from . import cleanup
    emit = progress or (lambda message: None)
    if not Path(config_path).is_file():
        raise ValueError('Automatic recovery needs the existing review --config file')
    # Finish a previous automatic removal BEFORE scanning can re-import a file
    # whose DB record was committed away but whose unlink hit a Windows lock.
    journals = Path(repo) / '_02_generation/_runtime/_cleanup'
    for journal in sorted(journals.glob('*/plan.json')):
        previous = json.loads(journal.read_text(encoding='utf-8'))
        if not previous.get('automatic') or Path(previous['database']) != Path(database).resolve():
            continue
        if previous['state'] in {'applying', 'undoing'}:
            raise RuntimeError(f'An interrupted file repair must be recovered before scanning. Run: '
                               f'py -3.12 _97_app/run_gbr.py cleanup-generations undo "{journal}"')
        if previous['state'] == 'applied' and previous.get('pruning', {}).get('state') != 'done':
            emit('Resuming previous automatic removal: ' + str(journal))
            try:
                cleanup.apply_plan(journal)
            except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                raise RuntimeError(f'{exc}\nResume: py -3.12 _97_app/run_gbr.py cleanup-generations apply "{journal}"') from exc
        cleanup.forget_automatic_journal(repo, journal)
    config = Config(config_path)
    config.values['database_path'] = str(Path(database).resolve(strict=True))
    lock = ProcessLock(Path(database).with_name(Path(database).name + '.lock'))
    service = None
    try:
        service = Service(config)
        service.archiver.preparation_root = Path(repo) / '_04_preparation/_tracks'
        service.archiver.showcase_root = Path(repo) / '_05_showcase'
        emit('Rescanning configured completed inputs; no audio analysis or GPU work is started')
        service.indexer.scan(progress=emit)
        for row in service.db.all('SELECT id,archive_path FROM audio WHERE archive_path IS NOT NULL'):
            path = Path(row['archive_path'])
            if protected(path, Path(repo)) or not path.is_file():
                continue
            emit('Reconciling archived recording: ' + str(path))
            indexed = service.db.one("SELECT audio_id FROM media_files WHERE path=? AND storage_state IN ('input','archived')", (str(path),))
            try:
                if not indexed and audio_identity(path)[0] != row['id']:
                    emit('Retained archive with mismatched PCM identity: ' + str(path));continue
                service.indexer.index(path)
            except (OSError, ValueError, RuntimeError) as exc:
                emit(f'Archive metadata remains unrecoverable: {path}: {exc}')
        service.archiver.retry()
        service.archiver.reconcile()
        for op in service.db.all("SELECT * FROM archive_operations WHERE status='pending' ORDER BY created_at,rowid"):
            definition = service.db.one('''SELECT 1 AS n FROM generation_metadata m JOIN generations g ON g.id=m.generation_id
                WHERE g.audio_id=? AND (m.yaml_sha256 IS NOT NULL OR m.raw_yaml_bytes IS NOT NULL OR m.raw_yaml IS NOT NULL) LIMIT 1''', (op['audio_id'],))
            if not definition:
                emit('No recoverable YAML; avoiding archive copies before removal: ' + op['audio_id'])
                continue
            emit('Recovering archive: ' + op['audio_id'])
            service.archiver.process(op)
        emit('Automatic index/archive recovery finished; unresolved cases will be evaluated for removal')
    finally:
        if service is not None:
            service.close()
        lock.close()
