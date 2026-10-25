"""Create the public research projection from a read-only shared GBR database.

This is a reusable export, never a database migration or a media publisher.
One row represents one PCM recording; only current reviewer revisions contribute.
"""
import argparse
from collections import defaultdict
from contextlib import closing
import csv
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PureWindowsPath
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]
METRICS = ('audio_quality', 'composition', 'consistency', 'realism')
PARAMETERS = ('model', 'dit', 'text_encoder', 'encoder_cfg', 'encoder_seed', 'top_k',
              'sampler', 'scheduler', 'sampler_cfg', 'sampler_seed', 'sampler_steps')
RESOURCE_FIELDS = ('stage', 'baseline_used_bytes', 'peak_allocated_bytes',
                   'peak_reserved_bytes', 'peak_observed_device_bytes',
                   'sampling_interval_ms', 'coverage')


def object_json(value):
    result = json.loads(value or '{}')
    return result if isinstance(result, dict) else {}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def mean(values):
    values = [v for v in values if finite(v)]
    return sum(values) / len(values) if values else None


def maximum(values):
    values = [v for v in values if finite(v)]
    return max(values) if values else None


def records(conn, sql, values=()):
    return [dict(r) for r in conn.execute(sql, values)]


def pricing(spans):
    """Inclusive parent prices replace descendants, never add to them."""
    by_id = {s['id']: s for s in spans}
    priced = {s['id']: s for s in spans if finite(s['estimated_cost']) and s['currency'] == 'USD'}
    counted = []
    for span in priced.values():
        parent, visited, covered = span['parent_id'], set(), False
        while parent in by_id and parent not in visited:
            visited.add(parent)
            if parent in priced and by_id[parent]['includes_children']:
                covered = True
                break
            parent = by_id[parent]['parent_id']
        if not covered:
            counted.append(span)
    complete = any(s['stage'] == 'total' and s['parent_id'] is None and s['includes_children'] for s in counted)
    known = sum(s['estimated_cost'] for s in counted) if counted else None
    return {'estimated_cost_usd': known if complete else None, 'known_cost_usd': known,
            'pricing_complete': complete, 'cost_basis': sorted({s['cost_basis'] for s in counted if s['cost_basis']})}


def telemetry(conn, run_id):
    result = []
    for attempt in records(conn, '''SELECT a.*,w.gpu_name,w.usable_vram_bytes,w.hardware_json,w.environment_json
            FROM generation_attempts a LEFT JOIN worker_sessions w ON w.id=a.worker_session_id
            WHERE a.run_id=? ORDER BY a.started_at,a.id''', (run_id,)):
        identifier = attempt['id']
        spans = records(conn, 'SELECT * FROM generation_timing_spans WHERE attempt_id=?', (identifier,))
        resources = records(conn, 'SELECT * FROM generation_resource_measurements WHERE attempt_id=? ORDER BY stage,device_id', (identifier,))
        hardware = object_json(attempt['hardware_json'])
        models = object_json(attempt['actual_models_json'])
        versions = object_json(attempt['environment_json'])
        total = next((s['duration_seconds'] for s in spans if s['stage'] == 'total' and s['parent_id'] is None), None)
        result.append({'attempt_id': identifier, 'status': attempt['status'],
            'execution_mode': attempt['execution_mode'], 'gpu_name': attempt['gpu_name'],
            'usable_vram_bytes': attempt['usable_vram_bytes'], 'wall_seconds': total,
            'cpu': hardware.get('cpu'), 'ram_total_bytes': hardware.get('ram_total_bytes'),
            'model_revision': models.get('revision'),
            'software': {key: versions.get(key) for key in ('torch', 'diffusers', 'transformers')},
            'stages': [{'stage': s['stage'], 'duration_seconds': s['duration_seconds'],
                        'includes_children': bool(s['includes_children'])} for s in spans],
            'resources': [{key: r[key] for key in RESOURCE_FIELDS} for r in resources],
            'peak_allocated_bytes': maximum(r['peak_allocated_bytes'] for r in resources),
            'peak_reserved_bytes': maximum(r['peak_reserved_bytes'] for r in resources),
            'peak_observed_device_bytes': maximum(r['peak_observed_device_bytes'] for r in resources),
            **pricing(spans)})
    return result


def project(conn, style_sources=None):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type IN ('table','view')")}
    required = {'audio', 'generations', 'generation_metadata', 'songs', 'latest_reviewer_reviews', 'publishing_selection'}
    if required - tables:
        raise ValueError('Shared review schema is incomplete; missing: ' + ', '.join(sorted(required - tables)))
    # Every nonpublic room stays out of public research track listings too.
    # The studio's original review, telemetry and analytical records remain intact.
    placements={}
    if 'processing_tasks' in tables:
        for task in records(conn,"SELECT generation_id,kind,recipe_json FROM processing_tasks WHERE kind IN ('release_prepare','release_publish') AND status='done' ORDER BY rowid"):
            recipe=object_json(task['recipe_json'])
            room=recipe.get('visibility') or recipe.get('options',{}).get('visibility','public')
            # A nonpublic draft suppresses research immediately. Preparing a
            # public revision cannot reveal it before publication succeeds.
            if task['kind']=='release_publish' or room!='public':placements[task['generation_id']]=room
    hidden={gid for gid,value in placements.items() if value!='public'}
    has_runs = {'generation_output_links', 'generation_runs', 'generation_attempts', 'worker_sessions',
                'generation_timing_spans', 'generation_resource_measurements'} <= tables
    definitions = records(conn, 'SELECT id,category,label,active FROM review_tag_definitions ORDER BY category_order,sort_order,id') if 'review_tag_definitions' in tables else []
    observations = defaultdict(list)
    if 'review_tags' in tables:
        for tag in records(conn, 'SELECT review_id,tag_id,polarity FROM review_tags ORDER BY tag_id'):
            observations[tag.pop('review_id')].append(tag)
    reviews = defaultdict(list)
    for review in records(conn, 'SELECT * FROM latest_reviewer_reviews ORDER BY revision,id'):
        reviews[review['audio_id']].append(review)
    generations = defaultdict(list)
    for generation in records(conn, '''SELECT g.*,s.title,s.genre,s.cluster,m.parsed_json,m.embedded_json,m.metadata_status,m.yaml_sha256,
            m.model,m.dit,m.text_encoder,m.encoder_cfg,m.encoder_seed,m.top_k,m.sampler,m.scheduler,m.sampler_cfg,
            m.sampler_seed,m.sampler_steps,m.generation_duration,p.status AS publishing_status
            FROM generations g JOIN songs s ON s.id=g.song_id
            JOIN generation_metadata m ON m.generation_id=g.id
            LEFT JOIN publishing_selection p ON p.generation_id=g.id ORDER BY g.created_at,g.id'''):
        generations[generation['audio_id']].append(generation)
    output = []
    for audio in records(conn, 'SELECT id,pcm_sha256,duration FROM audio ORDER BY id'):
        choices = generations[audio['id']]
        if not choices or any(g['id'] in hidden for g in choices):
            continue
        current = reviews[audio['id']]
        # Prefer metadata actually reviewed. Aliases of identical PCM cannot multiply N.
        preferred = current[-1]['generation_id'] if current else choices[-1]['id']
        generation = next((g for g in choices if g['id'] == preferred), choices[-1])
        metadata = object_json(generation['parsed_json'])
        embedded = object_json(generation['embedded_json'])
        links = records(conn, '''SELECT DISTINCT r.* FROM generation_output_links l JOIN generation_runs r ON r.id=l.run_id
                    WHERE l.review_generation_id=?''', (generation['id'],)) if has_runs else []
        run = links[0] if len(links) == 1 else None
        effective = object_json(run['effective_json']) if run else {}
        requested = object_json(run['requested_json']) if run else {}
        execution = requested.get('execution') if isinstance(requested.get('execution'), dict) else {}
        values = {key: effective.get(key, generation.get(key) if generation.get(key) is not None else metadata.get(key)) for key in PARAMETERS}
        for key in ('encoder_seed', 'sampler_seed'):
            values[key] = str(values[key]) if values[key] is not None else None
        metrics = {key: mean(r[key] for r in current) for key in METRICS}
        attempts = telemetry(conn, run['id']) if run else []
        succeeded = [a for a in attempts if a['status'] == 'succeeded']
        actual = succeeded[-1] if succeeded else None
        cache_status = None
        if run and actual and 'generation_artifacts' in tables:
            for artifact in records(conn, "SELECT metadata_json FROM generation_artifacts WHERE run_id=? AND kind='audio'", (run['id'],)):
                artifact_meta = object_json(artifact['metadata_json'])
                encoding = artifact_meta.get('encoding') or {}
                if artifact_meta.get('attempt_id') == actual['attempt_id'] and encoding.get('status') in {'hit','miss','disabled'}:
                    cache_status = encoding['status']
        template_id = run['template_id'] if run else None
        source = (style_sources or {}).get(template_id) or metadata.get('caption_source')
        # Preserve the public site's historical "sample" category for MiniMax templates.
        source = 'sample' if source == 'minimax' else source if source in ('sample', 'custom') else None
        row = {'id': audio['id'], 'audio_hash': audio['pcm_sha256'],
            'generation_id': generation['id'], 'generation_ids': [g['id'] for g in choices],
            'run_id': run['id'] if run else None, 'ambiguous_run_link': len(links) > 1,
            'song_id': run['song_id'] if run else metadata.get('song_id', generation['song_id']),
            'variant_id': run['variant_id'] if run else metadata.get('variant_id'),
            'title': run['title'] if run else metadata.get('title') or generation['title'],
            'cluster': run['cluster'] if run else metadata.get('cluster') or generation['cluster'],
            'version': run['style'] if run else metadata.get('style') or metadata.get('style_primary') or generation['genre'],
            'generation_timestamp': generation['generation_timestamp'] or (run['completed_at'] if run else None),
            'generation_datetime': metadata.get('generation_datetime'), 'duration': audio['duration'],
            'audio_file': PureWindowsPath(generation['original_filename']).name,
            'missing_yaml': not bool(generation['yaml_sha256']),
            'published': any(g['publishing_status'] == 'published' for g in choices),
            'publishing_status': generation['publishing_status'] or 'not_selected',
            'review_model': 'four-metric-0-5', 'review_metrics': metrics,
            'quality_score': mean(metrics.values()), 'review_count': len(current),
            'current_reviews': [{'review_id': r['id'],
                'reviewer_key': hashlib.sha256(r['reviewer_id'].encode()).hexdigest()[:16],
                'reviewed_at': r['created_at'], 'revision': r['assessment_revision'],
                'metrics': {key: r[key] for key in METRICS},
                'tags_recorded': bool(r.get('tags_recorded')), 'tags': observations[r['id']]} for r in current],
            'caption_source': source, 'template_id': template_id,
            'encoding_cache': execution.get('encoding_cache'), 'cache_status': cache_status,
            'memory_profile': execution.get('memory_profile'),
            'gpu_name': actual['gpu_name'] if actual else None,
            'usable_vram_bytes': actual['usable_vram_bytes'] if actual else None,
            'execution_mode': actual['execution_mode'] if actual else None,
            'wall_seconds': actual['wall_seconds'] if actual else None,
            'peak_allocated_bytes': actual['peak_allocated_bytes'] if actual else None,
            'peak_observed_device_bytes': actual['peak_observed_device_bytes'] if actual else None,
            'attempts': attempts, **values}
        if 'analyses' in tables:
            analysis = conn.execute('SELECT metrics_json FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1', (audio['id'],)).fetchone()
            metrics_json = object_json(analysis[0]) if analysis else {}
            row['audio_analysis'] = {key: metrics_json.get(key) for key in (
                'sample_peak_dbfs', 'true_peak_dbtp_4x', 'clipped_samples', 'rms_dbfs',
                'integrated_lufs', 'loudness_range_lu', 'spectral_centroid_hz',
                'spectral_flatness', 'spectral_flux', 'stereo_correlation', 'event_count')}
        for field in ('caption', 'lyrics'):
            text = effective.get(field, metadata.get(field, embedded.get(field)))
            if isinstance(text, str):
                row[field + '_words'] = len(text.split())
                row[field + '_chars'] = len(text)
        output.append(row)
    return {'format': 'gbr-platform-analytics-v1', 'generated_at': datetime.now(timezone.utc).isoformat(timespec='seconds'),
            'review_model': 'four-metric-0-5', 'review_scope': 'latest revision per reviewer and PCM recording',
            'recording_scope': 'one row per PCM recording; metadata of the most recently reviewed generation',
            'count': len(output), 'review_count': sum(r['review_count'] for r in output),
            'tag_definitions': definitions, 'generations': output}


def validate(payload):
    if payload.get('format') != 'gbr-platform-analytics-v1' or payload.get('review_model') != 'four-metric-0-5':
        raise ValueError('Invalid platform research export')
    rows = payload.get('generations')
    if not isinstance(rows, list) or payload.get('count') != len(rows):
        raise ValueError('Platform export count does not match its recordings')
    if len({r['id'] for r in rows}) != len(rows):
        raise ValueError('Platform export contains duplicate recordings')
    return payload


def export(database, output=ROOT / '_data/_analytics/platform.json', styles=None):
    database, output = Path(database).resolve(strict=True), Path(output).resolve()
    if output == database:
        raise ValueError('Export cannot replace its database')
    sources = {}
    if styles and Path(styles).is_file():
        with Path(styles).open(encoding='utf-8-sig', newline='') as stream:
            sources = {r['ID']: r.get('template_source') for r in csv.DictReader(stream)}
    # mode=ro respects WAL and prevents migrations, backups, or accidental writes.
    with closing(sqlite3.connect(database.as_uri() + '?mode=ro', uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA query_only=ON')
        conn.execute('BEGIN')
        payload = validate(project(conn, sources))
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix='.gbr-public-', suffix='.tmp', dir=output.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='\n') as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)
    return payload


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT.parent / '_98_database/gbr.sqlite3')
    parser.add_argument('--output', type=Path, default=ROOT / '_data/_analytics/platform.json')
    parser.add_argument('--styles', type=Path, default=ROOT.parent / '_01_writing/_references/styles.csv')
    args = parser.parse_args()
    try:
        payload = export(args.database, args.output, args.styles)
        print(json.dumps({'output': str(args.output), 'recordings': payload['count'], 'current_reviews': payload['review_count']}))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, 'Cannot export public GBR research: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
