"""Offline, journaled repair of indexed generation files. No second database.

Audio bytes and IDs never change. Only live paths/effective metadata are updated;
original paths, worker results, requests and writing/source snapshots stay intact.
"""
import argparse
import base64
from contextlib import closing
import csv
from datetime import datetime
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import uuid

from _gbr_generation.yaml_document import YamlDocument
from .document import Source, edit
from .service import Editor, atomic
from _review.indexer import audio_identity, number, as_text, parse_embedded
from _review.metadata_identity import worker_intermediate
from _review.song_names import SongNames, identifier
from _review.util import digest, dumps, now, slug

RENAMES = {'song_title': 'title', 'steps': 'sampler_steps',
           'text_encoder_model': 'text_encoder', 'diffusion_model': 'dit'}
PARAMETERS = ('model', 'dit', 'text_encoder', 'encoder_cfg', 'encoder_seed', 'top_k',
              'sampler', 'scheduler', 'sampler_cfg', 'sampler_seed', 'sampler_steps')
NUMBERS = {'encoder_cfg', 'top_k', 'sampler_cfg', 'sampler_steps'}
CHECKPOINTS = {
    'dit': ('minimax_music3_dit_fp32', 'minimax_music3_dit_bf16',
            'minimax_music3_dit_int8_convrot'),
    'text_encoder': ('minimax_music3_text_encoder_pruned_bf16',
                     'minimax_music3_text_encoder_pruned_int8_convrot'),
}


def packed(value):
    return {'bytes': base64.b64encode(value).decode()} if isinstance(value, bytes) else value


def unpacked(value):
    return base64.b64decode(value['bytes'], validate=True) if isinstance(value, dict) and set(value) == {'bytes'} else value


def read_rows(conn, table):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
        return []
    return [dict(row) for row in conn.execute('SELECT * FROM ' + table)]


def row_change(table, row, values):
    changed = {key: value for key, value in values.items() if row[key] != value}
    return {'table': table, 'id': row['id'] if table not in {'generation_metadata', 'generation_execution_state'}
            else row['generation_id'] if table == 'generation_metadata' else row['run_id'],
            'guard': {key: packed(value) for key, value in row.items()},
            'before': {key: packed(row[key]) for key in changed},
            'after': {key: packed(value) for key, value in changed.items()}} if changed else None


def protected(path, repo):
    path = Path(path)
    return (path.resolve().is_relative_to((repo / '_01_writing').resolve())
            or worker_intermediate(path) or any(part.startswith('.gbr-') for part in path.parts)
            or any(p.is_symlink() for p in (path, *path.parents)))


def newline_repair(value):
    # URL paths such as /news are text, never newline markers.
    parts = re.split(r'(https?://\S+)', value)
    for index in range(0, len(parts), 2):
        parts[index] = parts[index].replace('\\r\\n', '\n').replace('\\n', '\n')
        parts[index] = re.sub(r'/n(?=\s|\[|$)', '\n', parts[index])
    return ''.join(parts)


def repair_yaml(raw, template, effective, title, song_id, model_map):
    doc = YamlDocument(raw)
    operations, notes = [], []
    for old, new in RENAMES.items():
        if old in doc.values and new not in doc.values:
            operations.append({'kind': 'rename', 'key': old, 'new_key': new})
            notes.append(f'{old} → {new}')
        elif old in doc.values and new in doc.values and doc.values[old] != doc.values[new]:
            raise ValueError(f'Conflicting {old}/{new}; repair explicitly in the YAML editor')
    raw = edit(raw, operations) if operations else raw
    doc = YamlDocument(raw)
    operations = []
    if title and doc.values.get('title') != title:
        operations.append({'kind': 'set', 'key': 'title', 'value': title, 'position': 'start'})
        notes.append('catalogue title')
    if not doc.values.get('song_id') and song_id:
        operations.append({'kind': 'set', 'key': 'song_id', 'value': int(song_id), 'position': 'start'})
        notes.append('catalogue song ID')
    for field in ('lyrics', 'caption'):
        value = doc.values.get(field)
        if isinstance(value, str):
            repaired = newline_repair(value)
            if value != repaired:
                operations.append({'kind': 'set', 'key': field, 'value': repaired})
                notes.append(f'{field}: newline tokens → line breaks')
    if not doc.values.get('generation_datetime') and effective.get('generation_timestamp'):
        try:
            stamp = datetime.fromisoformat(str(effective['generation_timestamp']).replace('Z', '+00:00'))
            operations.append({'kind': 'set', 'key': 'generation_datetime', 'value': stamp.strftime('%y%m%d%H%M'),
                               'position': 'before', 'anchor': 'caption' if doc.values.get('caption') is not None else 'lyrics'})
            notes.append('generation_datetime from recorded generation timestamp (file mtime not used)')
        except ValueError:
            notes.append('generation timestamp could not be parsed; generation_datetime left unknown')
    for field in ('model', 'dit', 'text_encoder'):
        value = doc.values.get(field, effective.get(field))
        replacement = model_map.get(field, {}).get(value) if isinstance(value, str) else None
        if replacement is None and value in CHECKPOINTS.get(field, ()):
            replacement = value + '.safetensors'
        if replacement is not None and replacement != value:
            operations.append({'kind': 'set', 'key': field, 'value': replacement, 'position': 'end'})
            notes.append(f'{field}: {value} → {replacement}')
        elif isinstance(value, str) and value in {'int8', 'fp8', 'fp16', 'bf16', 'fp32'}:
            notes.append(f'{field}: precision descriptor {value!r} retained; exact checkpoint unknown')
    raw = edit(raw, operations) if operations else raw
    # Template supplies structure. Missing fields use known effective metadata;
    # otherwise copy the template default (blank remains null, not invented data).
    defaults = {key: effective[key] for key in template['keys']
                if key in effective and effective[key] is not None}
    operation = {'kind': 'add_template', 'keys': template['keys'],
                 'template_fields': template['fields'], 'template_indent': template['indent'],
                 'defaults': defaults, 'reorder': True}
    raw = edit(raw, [operation])
    return raw, notes


def file_change(source, destination, content=None):
    source, destination = Path(source), Path(destination)
    before = digest(source)
    after = hashlib.sha256(content).hexdigest() if content is not None else before
    if source == destination and before == after:
        return None
    if destination != source and destination.exists():
        raise ValueError(f'Destination already exists: {destination}')
    item = {'source': str(source), 'destination': str(destination), 'before': before, 'after': after,
            'mode': source.stat().st_mode & 0o777, 'mtime_ns': source.stat().st_mtime_ns}
    if content is not None and after != before:
        original = source.read_bytes()
        item.update(original=packed(original), content=packed(content),
                    diff=''.join(difflib.unified_diff(original.decode('utf-8-sig').splitlines(True),
                         content.decode('utf-8-sig').splitlines(True), fromfile=str(source), tofile=str(destination))))
    return item


def make_plan(repo, database, roots, template_path=None, model_map=None):
    repo, database = Path(repo).resolve(), Path(database).resolve(strict=True)
    roots = list(dict.fromkeys(Path(path).resolve(strict=True) for path in roots))
    if not roots or any(protected(root, repo) for root in roots):
        raise ValueError('Choose completed generation/review folders, outside writing and worker scratch')
    template = Editor(repo).order_template(template_path)
    model_map = model_map or {}
    if set(model_map) - {'model', 'dit', 'text_encoder'} or any(
            not isinstance(aliases, dict) or any(not isinstance(k, str) or not isinstance(v, str) or not v.strip()
                for k, v in aliases.items()) for aliases in model_map.values()):
        raise ValueError('Model map must contain model/dit/text_encoder objects of exact old-name: new-name aliases')
    plan = {'format': 'gbr-generation-cleanup-v1', 'id': uuid.uuid4().hex, 'created_at': now(),
            'database': str(database), 'repo': str(repo), 'roots': list(map(str, roots)),
            'template': {'path': template['path'], 'sha256': template['sha256']},
            'groups': [], 'errors': [], 'warnings': [], 'state': 'preview'}
    def allowed(path):
        return not protected(path, repo) and any(Path(path).resolve().is_relative_to(root) for root in roots)
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('BEGIN')
        tables = {name: read_rows(conn, name) for name in ('audio', 'media_files', 'generations',
                  'generation_metadata', 'generation_sources', 'generation_artifacts', 'generation_output_links',
                  'generation_execution_state', 'archive_operations', 'processing_tasks', 'generation_runs')}
    all_metadata = {row['generation_id']: row for row in tables['generation_metadata']}
    identities = []
    for gen in tables['generations']:
        meta = json.loads(all_metadata[gen['id']]['parsed_json'])
        identities.append({'id': gen['id'], 'title': str(meta.get('title') or gen['original_filename']),
                           'original_filename': gen['original_filename'], 'db_song_id': gen['song_id'],
                           'catalogue_song_id': meta.get('song_id'), 'catalogue_song_type': None})
    names = SongNames(identities, repo / '_01_writing/_built')
    masters = {}
    with (repo / '_01_writing/_built/track_list.csv').open(encoding='utf-8-sig', newline='') as handle:
        for row in csv.DictReader(handle):
            sid = identifier(row.get('song_id'));title = (row.get('song_name') or row.get('title') or '').strip()
            if not sid or not title or sid in masters:raise ValueError('Master catalogue contains missing/duplicate IDs or titles')
            masters[sid] = title
    variants = {(sid, '1') for sid in masters}
    variant_path = repo / '_01_writing/_built/variant_list.csv'
    if variant_path.is_file():
        with variant_path.open(encoding='utf-8-sig', newline='') as handle:
            for row in csv.DictReader(handle):
                pair = (identifier(row.get('song_id')), identifier(row.get('variant_id')))
                if pair[0] not in masters or pair[1] is None or pair in variants:
                    raise ValueError('Variant catalogue has missing/conflicting song/variant IDs')
                variants.add(pair)
    recorded = set()
    for audio in tables['audio']:
        media = [row for row in tables['media_files'] if row['audio_id'] == audio['id']
                 and row['storage_state'] in {'input', 'archived'} and Path(row['path']).is_file() and allowed(row['path'])]
        archived = audio.get('archive_path')
        if archived and Path(archived).is_file() and allowed(archived) and archived not in {r['path'] for r in media}:
            plan['errors'].append({'audio_id': audio['id'], 'error': f'Archive is not indexed: {archived}; scan/reconcile first'})
            continue
        if not media:
            continue
        recorded.update(row['path'] for row in media)
        try:
            if len(media) != 1:
                raise ValueError('Multiple active copies of this PCM; archive/reconcile before cleanup')
            source = Path(media[0]['path'])
            if digest(source) != media[0]['sha256'] or audio_identity(source)[0] != audio['pcm_sha256']:
                raise ValueError('Audio differs from indexed checksum/PCM; rescan before cleanup')
            generations = [g for g in tables['generations'] if g['audio_id'] == audio['id']]
            if any(op['audio_id'] == audio['id'] and op['status'] in {'pending', 'running', 'failed'} for op in tables['archive_operations']):
                raise ValueError('Pending archive/recovery operation; finish it first')
            gids = {g['id'] for g in generations}
            if any(t['generation_id'] in gids and t['status'] in {'queued', 'running'} for t in tables['processing_tasks']):
                raise ValueError('Recording has an active preparation task')
            run_ids = {l['run_id'] for l in tables['generation_output_links'] if l['review_generation_id'] in gids}
            if any(r['id'] in run_ids and r['status'] not in {'succeeded', 'failed', 'cancelled', 'interrupted'} for r in tables['generation_runs']):
                raise ValueError('Recording belongs to an active generation run')
            keys = {names.by_generation[g['id']] for g in generations}
            if len(keys) != 1 or not next(iter(keys)).startswith('song:'):
                raise ValueError('No unambiguous catalogue song ID; fix the catalogue/YAML first')
            key = next(iter(keys));sid = key[5:]
            if sid not in masters:raise ValueError('Song ID is absent from the master catalogue')
            title = masters[sid]
            stem = slug(title).casefold() + '__' + audio['pcm_sha256'][:12]
            destination = source.with_name(stem + '.flac')
            group = {'audio_id': audio['id'], 'generation_ids': sorted(gids), 'title': title,
                     'files': [], 'updates': [], 'notes': []}
            paths = {str(source): str(destination)}
            contents = {}
            yaml_for = {}
            for gen in generations:
                metadata = all_metadata[gen['id']]
                effective = json.loads(metadata['parsed_json'])
                # Explicit conflicting numeric IDs must be fixed, not overwritten.
                if effective.get('song_id') and identifier(effective['song_id']) != sid:
                    raise ValueError('Conflicting song IDs in existing generation metadata')
                if effective.get('variant_id') is not None and (sid, identifier(effective['variant_id'])) not in variants:
                    raise ValueError('Variant ID is absent from the variant catalogue')
                if effective.get('variant_id') is None:
                    group['notes'].append('variant_id unknown; not inferred from a generation number')
                candidates = [metadata['yaml_archive_path']] + [r['yaml_path'] for r in tables['generation_sources']
                    if r['generation_id'] == gen['id']]
                yp = next((Path(p) for p in candidates if p and Path(p).is_file() and allowed(p)
                           and not Path(p).name.endswith('.source.yaml') and Path(p).name != 'source.yaml'), None)
                if yp is None:
                    raise ValueError(f'Completed YAML missing for generation {gen["id"]}; recover/scan first')
                raw = yp.read_bytes()
                if len(raw) > 2 * 1024**2:raise ValueError('Completed YAML exceeds the 2 MiB editor limit')
                raw_values = YamlDocument(raw).values
                if raw_values.get('song_id') is not None and identifier(raw_values['song_id']) != sid:
                    raise ValueError('Completed YAML song ID conflicts with its indexed/catalogue identity')
                if raw_values.get('variant_id') is not None and (sid, identifier(raw_values['variant_id'])) not in variants:
                    raise ValueError('Completed YAML variant ID is absent from the variant catalogue')
                if (raw_values.get('variant_id') is not None and effective.get('variant_id') is not None
                        and identifier(raw_values['variant_id']) != identifier(effective['variant_id'])):
                    raise ValueError('Completed YAML variant ID conflicts with its indexed identity')
                if metadata['yaml_sha256'] != hashlib.sha256(raw).hexdigest():
                    raise ValueError(f'Completed YAML changed since indexing: {yp}')
                updated, notes = repair_yaml(raw, template, effective, title, sid, model_map)
                if str(yp) in contents and contents[str(yp)] != updated:
                    raise ValueError('Shared YAML has conflicting generation metadata')
                contents[str(yp)] = updated
                target = source.with_name(stem + ('' if not yaml_for or str(yp) in yaml_for.values()
                                                  else '__p' + hashlib.sha256(updated).hexdigest()[:12]) + '.yaml')
                if str(yp) in paths and paths[str(yp)] != str(target):
                    raise ValueError('Shared YAML has conflicting destinations')
                paths[str(yp)] = str(target);yaml_for[gen['id']] = str(yp)
                group['notes'].extend(notes)
                values = YamlDocument(updated).values
                # This is the effective overlay; FLAC embedded metadata stays as
                # original evidence. Source bytes and frozen requests never change.
                effective = parse_embedded(json.loads(metadata['embedded_json']))[0]
                effective.update(values)
                values = {'yaml_sha256': hashlib.sha256(updated).hexdigest(), 'raw_yaml_bytes': updated,
                          'raw_yaml': updated.decode('utf-8-sig'), 'parsed_json': dumps(effective),
                          'metadata_status': 'valid', 'error': None,
                          'yaml_archive_path': str(target) if metadata['yaml_archive_path'] else None}
                for field in PARAMETERS:
                    values[field] = (number(effective.get(field), field in {'top_k', 'sampler_steps'}) if field in NUMBERS
                                     else as_text(effective.get(field)))
                # Match the indexer's key calculation exactly, while retaining ID.
                metadata_key = hashlib.sha256(dumps({'yaml': values['yaml_sha256'], 'metadata': effective}).encode()).hexdigest()
                for update in (row_change('generation_metadata', metadata, values),
                               row_change('generations', gen, {'metadata_key': metadata_key})):
                    if update:group['updates'].append(update)
            updated_keys = {u['id']:u['after']['metadata_key'] for u in group['updates'] if u['table'] == 'generations'}
            keys_after = [updated_keys.get(g['id'], g['metadata_key']) for g in generations]
            if len(set(keys_after)) != len(keys_after):
                raise ValueError('Normalization would merge distinct historical metadata identities; retain/repair them explicitly')
            # Native companions belong to the same recording through explicit links.
            artifacts = [a for a in tables['generation_artifacts'] if a['run_id'] in run_ids
                         and Path(a['uri']).is_file() and allowed(a['uri'])]
            if any(not a['sha256'] or digest(a['uri']) != a['sha256'] for a in artifacts):
                raise ValueError('Native artifact checksum differs from the ledger; recover first')
            for artifact in artifacts:
                path = Path(artifact['uri'])
                if path.parent != source.parent or str(path) in paths:
                    continue
                if artifact['kind'] in {'source_yaml', 'generation_report'}:
                    if path.name in {'source.yaml', 'generation.json'}:
                        target = path
                    else:
                        suffix = '.source.yaml' if artifact['kind'] == 'source_yaml' else '.generation.json'
                        target = path.with_name(stem + '__g' + artifact['run_id'] + suffix)
                    paths[str(path)] = str(target)
            for artifact in artifacts:
                if artifact['kind'] != 'generation_report' or artifact['uri'] not in paths:
                    continue
                report_path = Path(artifact['uri']);report = json.loads(report_path.read_text(encoding='utf-8'))
                siblings = {a['kind']: a for a in artifacts if a['run_id'] == artifact['run_id'] and a['uri'] in paths}
                files = {}
                for filename, checksum in report.get('files', {}).items():
                    kind = 'audio' if filename.endswith('.flac') else 'source_yaml' if filename == 'source.yaml' else 'completed_yaml'
                    member = siblings.get(kind)
                    if member:
                        content = contents.get(member['uri'])
                        filename = Path(paths[member['uri']]).name
                        checksum = hashlib.sha256(content).hexdigest() if content is not None else digest(member['uri'])
                    files[filename] = checksum
                if report.get('files') != files:
                    report['files'] = files
                    contents[str(report_path)] = (json.dumps(report, ensure_ascii=False, indent=2) + '\n').encode()
            # Additional unregistered companions are never moved/deleted by guesswork.
            for old, new in paths.items():
                item = file_change(old, new, contents.get(old))
                if item:
                    item['stage'] = str(Path(new).with_name('.gbr-cleanup-' + plan['id'] + '-' + hashlib.sha256(old.encode()).hexdigest()[:12] + '.part'))
                    group['files'].append(item)
            if len({p.casefold() for p in paths.values()}) != len(paths):
                raise ValueError('Companion destination collision')
            for table, columns in [('audio', ('archive_path',)), ('media_files', ('path',)),
                                   ('generation_sources', ('yaml_path',)), ('generation_artifacts', ('uri',))]:
                for row in tables[table]:
                    values = {column: paths[row[column]] for column in columns if row[column] in paths}
                    if table == 'audio' and row['id'] == audio['id'] and values:
                        values['archive_stem'] = stem
                    if table == 'generation_artifacts' and row['uri'] in contents:
                        values.update(sha256=hashlib.sha256(contents[row['uri']]).hexdigest(), size_bytes=len(contents[row['uri']]))
                    update = row_change(table, row, values)
                    if update:group['updates'].append(update)
            for state in tables['generation_execution_state']:
                if state['run_id'] in run_ids and Path(state['output_directory']) == source.parent:
                    update = row_change('generation_execution_state', state, {'output_stem': stem})
                    if update:group['updates'].append(update)
            group['notes'] = list(dict.fromkeys(group['notes']))
            if group['files'] or group['updates']:
                plan['groups'].append(group)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            plan['errors'].append({'audio_id': audio['id'], 'error': str(exc)})
    for root in roots:
        for path in root.rglob('*'):
            if path.is_file() and path.suffix.casefold() == '.flac' and allowed(path) and str(path.resolve()) not in recorded:
                plan['warnings'].append(f'Unindexed audio retained: {path}. Run the review scan to include it.')
    # Refuse cross-recording shared paths/rows rather than create an unsafe plan.
    files, rows = set(), set()
    for group in plan['groups']:
        for item in group['files']:
            if item['source'] in files:raise ValueError('A file is shared by different recordings; reconcile first')
            files.add(item['source'])
        for update in group['updates']:
            identity = (update['table'], update['id'])
            if identity in rows:raise ValueError('A database row is shared by different recordings; reconcile first')
            rows.add(identity)
    return plan


KEYS = {'audio': 'id', 'media_files': 'id', 'generations': 'id', 'generation_sources': 'id',
        'generation_metadata': 'generation_id', 'generation_artifacts': 'id', 'generation_execution_state': 'run_id'}


def check_rows(conn, plan, side):
    for group in plan['groups']:
        for update in group['updates']:
            table = update['table'];key = KEYS[table]
            row = conn.execute(f'SELECT * FROM {table} WHERE {key}=?', (update['id'],)).fetchone()
            expected = {**update['guard'], **update[side]}
            if row is None or any(row[k] != unpacked(v) for k, v in expected.items()):
                raise ValueError(f'Database changed since preview/apply: {table} {update["id"]}')


def update_rows(conn, plan, side):
    for group in plan['groups']:
        for update in group['updates']:
            table = update['table'];key = KEYS[table];values = update[side]
            conn.execute(f'UPDATE {table} SET ' + ','.join(k + '=?' for k in values) + f' WHERE {key}=?',
                         [unpacked(v) for v in values.values()] + [update['id']])


def persist(plan_path, plan):
    atomic(Path(plan_path), (json.dumps(plan, ensure_ascii=False, indent=2) + '\n').encode())


def move_exclusive(source, destination):
    if source == destination:return
    # Same-folder links are exclusive on NTFS/ext4; never overwrite a conflict.
    os.link(source, destination)
    source.unlink()


def check_idle(conn, plan):
    audio_ids = {g['audio_id'] for g in plan['groups']}
    generation_ids = {gid for group in plan['groups'] for gid in group['generation_ids']}
    if any(row['audio_id'] in audio_ids and row['status'] in {'pending', 'running', 'failed'}
           for row in read_rows(conn, 'archive_operations')):
        raise ValueError('Archive/recovery work appeared since preview; finish it and preview again')
    if any(row['generation_id'] in generation_ids and row['status'] in {'queued', 'running'}
           for row in read_rows(conn, 'processing_tasks')):
        raise ValueError('A preparation task is active; finish it and preview again')


def apply_plan(plan_path, skip_errors=False):
    plan_path = Path(plan_path).resolve(strict=True)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['state'] == 'applied':return plan
    if plan['state'] != 'preview':raise ValueError('An interrupted cleanup must be undone before making a new plan')
    if plan['errors'] and not skip_errors:raise ValueError('Preview contains errors. Fix them or explicitly use --skip-errors')
    if digest(plan['template']['path']) != plan['template']['sha256']:raise ValueError('Template changed; preview again')
    from _review.__main__ import ProcessLock
    lock = ProcessLock(Path(plan['database']).with_name(Path(plan['database']).name + '.lock'))
    failure = None
    try:
        with closing(sqlite3.connect(Path(plan['database']).resolve(strict=True), timeout=1)) as conn:
            conn.row_factory = sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
            check_idle(conn, plan)
            check_rows(conn, plan, 'before')
            for group in plan['groups']:
                for item in group['files']:
                    source, target = Path(item['source']), Path(item['destination'])
                    if protected(source, Path(plan['repo'])) or protected(target, Path(plan['repo'])):
                        raise ValueError('Protected path in cleanup plan')
                    if digest(source) != item['before']:raise ValueError(f'File changed since preview: {source}')
                    if target != source and target.exists():raise ValueError(f'Destination exists: {target}')
                    if Path(item['stage']).exists():raise ValueError('Unexpected staging file; preview again')
            plan['state'] = 'applying';persist(plan_path, plan)
            for group in plan['groups']:
                for item in group['files']:
                    source, target = Path(item['source']), Path(item['destination'])
                    if 'content' in item:
                        stage = Path(item['stage'])
                        with stage.open('xb') as handle:
                            handle.write(unpacked(item['content']));handle.flush();os.fsync(handle.fileno())
                        if digest(stage) != item['after']:raise ValueError('Written YAML/report did not verify')
                        if target == source:os.replace(stage, target)
                        else:
                            move_exclusive(stage, target)
                            source.unlink()
                    else:move_exclusive(source, target)
                    os.chmod(target, item['mode']);os.utime(target, ns=(item['mtime_ns'], item['mtime_ns']))
                    if digest(target) != item['after']:raise ValueError(f'Final file did not verify: {target}')
                path = next((Path(i['destination']) for i in group['files'] if i['destination'].lower().endswith('.flac')), None)
                if path and audio_identity(path)[0] != group['audio_id']:raise ValueError('Decoded PCM changed')
            update_rows(conn, plan, 'after')
            # File bytes are unchanged for FLAC; its indexed stat can be refreshed
            # without creating a new media identity.
            for group in plan['groups']:
                conn.execute('INSERT INTO review_history(audio_id,generation_id,action,details_json,created_at) VALUES(?,?,?,?,?)',
                             (group['audio_id'], group['generation_ids'][0], 'generation_cleanup',
                              dumps({'cleanup_id': plan['id'], 'plan_path': str(plan_path), 'notes': group['notes'],
                                     'updates': group['updates'], 'files': [{k:v for k,v in item.items() if k not in {'content', 'diff'}} for item in group['files']]}), now()))
            conn.commit()
        plan['state'] = 'applied';persist(plan_path, plan)
    except Exception as exc:
        failure = exc
    finally:lock.close()
    if failure:
        if plan['state'] == 'applying':
            try:undo_plan(plan_path)
            except Exception as recovery:
                raise RuntimeError(f'Cleanup interrupted: {failure}. Undo journal retained at {plan_path}; recovery: {recovery}') from failure
        raise failure
    return plan


def undo_plan(plan_path):
    plan_path = Path(plan_path).resolve(strict=True);plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['state'] == 'undone':return plan
    if plan['state'] not in {'applied', 'applying', 'undoing'}:raise ValueError('Only applied/interrupted cleanup can be undone')
    from _review.__main__ import ProcessLock
    lock = ProcessLock(Path(plan['database']).with_name(Path(plan['database']).name + '.lock'))
    try:
        with closing(sqlite3.connect(Path(plan['database']).resolve(strict=True), timeout=1)) as conn:
            conn.row_factory = sqlite3.Row;conn.execute('PRAGMA foreign_keys=ON');conn.execute('BEGIN IMMEDIATE')
            check_idle(conn, plan)
            committed = conn.execute("SELECT action FROM review_history WHERE action IN ('generation_cleanup','generation_cleanup_undo') AND json_extract(details_json,'$.cleanup_id')=? ORDER BY id DESC LIMIT 1", (plan['id'],)).fetchone()
            committed = committed is not None and committed['action'] == 'generation_cleanup'
            check_rows(conn, plan, 'after' if committed else 'before')
            # Validate the ENTIRE batch before reversing any file.
            for group in plan['groups']:
                for item in group['files']:
                    source, target = Path(item['source']), Path(item['destination'])
                    if protected(source, Path(plan['repo'])) or protected(target, Path(plan['repo'])):
                        raise ValueError('Protected path in cleanup plan')
                    if source.exists() and digest(source) not in ({item['before'], item['after']} if source == target else {item['before']}):
                        raise ValueError(f'Source changed; retained it: {source}')
                    if target.exists() and target != source and digest(target) != item['after']:
                        raise ValueError(f'Target changed; retained it: {target}')
                    if not source.exists() and not target.exists() and 'original' not in item:
                        raise ValueError(f'Both audio paths unavailable: {source}')
            plan['state'] = 'undoing';persist(plan_path, plan)
            for group in reversed(plan['groups']):
                for item in reversed(group['files']):
                    source, target = Path(item['source']), Path(item['destination'])
                    if 'original' in item:
                        if not source.exists() or digest(source) != item['before']:
                            atomic(source, unpacked(item['original']), item['mode'])
                        if target != source and target.exists():target.unlink()
                    elif target != source and target.exists():
                        if source.exists():target.unlink()  # interrupted link-before-unlink
                        else:move_exclusive(target, source)
                    os.chmod(source, item['mode']);os.utime(source, ns=(item['mtime_ns'], item['mtime_ns']))
                    if digest(source) != item['before']:raise ValueError('Undo checksum mismatch')
                    Path(item['stage']).unlink(missing_ok=True)
            if committed:update_rows(conn, plan, 'before')
            conn.execute("INSERT INTO review_history(action,details_json,created_at) VALUES('generation_cleanup_undo',?,?)", (dumps({'cleanup_id': plan['id']}), now()))
            conn.commit()
        plan['state'] = 'undone';persist(plan_path, plan);return plan
    finally:lock.close()


def main(repo, arguments):
    parser = argparse.ArgumentParser(description='Preview/apply/undo historical generation cleanup. Stop GBR before apply/undo. Audio bytes and review IDs are preserved.')
    parser.add_argument('action', choices=['preview', 'apply', 'undo', 'forget'])
    parser.add_argument('plan', type=Path, nargs='?', help='Saved plan.json path for apply/undo/forget')
    parser.add_argument('--database', type=Path, default=repo / '_98_database/gbr.sqlite3')
    parser.add_argument('--config', type=Path, default=repo / '_03_review/_generation_review/config.json')
    parser.add_argument('--root', type=Path, action='append', help='Completed files folder; repeat. Default: configured review inputs/archive plus generated inbox')
    parser.add_argument('--template', type=Path, help='Order/default template; default: writing track-template.yaml')
    parser.add_argument('--model-map', type=Path, help='JSON {"dit":{"exact old":"exact new"},"text_encoder":{...}}; never infer quantization')
    parser.add_argument('--skip-errors', action='store_true', help='Apply only valid groups listed in this frozen preview')
    args = parser.parse_args(arguments)
    if args.action == 'preview':
        roots = args.root
        if roots is None:
            roots = [repo / '_02_generation/_generated']
            if args.config.is_file():
                from _review.config import Config
                config = Config(args.config);roots += [*config.inputs, config.archive]
            roots = [p for p in roots if p.is_dir()]
        aliases = json.loads(args.model_map.read_text(encoding='utf-8-sig')) if args.model_map else {}
        plan = make_plan(repo, args.database, roots, args.template, aliases)
        path = repo / '_02_generation/_runtime/_cleanup' / plan['id'] / 'plan.json'
        if len(json.dumps(plan).encode()) > 64 * 1024**2:raise ValueError('Preview exceeds 64 MiB; select smaller --root batches')
        path.parent.mkdir(parents=True);persist(path, plan)
        print(f'Preview: {len(plan["groups"])} recordings; {len(plan["errors"])} errors; {len(plan["warnings"])} warnings\nPlan: {path}')
        for group in plan['groups']:
            print('\n' + group['title'])
            for item in group['files']:
                print(f'  {item["source"]} → {item["destination"]}')
                if item.get('diff'):print(item['diff'])
            for note in group['notes']:print('  ' + note)
        for row in plan['errors']:print('ERROR: ' + row['audio_id'] + ': ' + row['error'])
        for warning in plan['warnings']:print('WARNING: ' + warning)
        return 2 if plan['errors'] else 0
    if args.plan is None:parser.error('This action requires the saved plan.json path')
    if args.action == 'apply':plan = apply_plan(args.plan, args.skip_errors)
    elif args.action == 'undo':plan = undo_plan(args.plan)
    else:
        plan = json.loads(args.plan.read_text(encoding='utf-8'))
        if plan['state'] not in {'applied', 'undone', 'preview'}:raise ValueError('Recover the interrupted operation before forgetting its journal')
        # Only our own plan file and empty journal directory; never recursive delete.
        if args.plan.name != 'plan.json' or args.plan.parent.name != plan['id']:raise ValueError('Not a cleanup journal directory')
        if set(args.plan.parent.iterdir()) != {args.plan}:raise ValueError('Journal directory contains other files; retained it')
        args.plan.unlink();args.plan.parent.rmdir()
        print('Cleanup journal removed. Existing shared database audit remains. Undo is no longer available.')
        return 0
    print(f'Cleanup {plan["id"]}: {plan["state"]} ({len(plan["groups"])} recordings)')
    return 0
