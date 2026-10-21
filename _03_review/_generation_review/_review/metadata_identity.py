"""Distinguish published music from worker scratch audio without changing history."""
import json


def worker_intermediate(path):
    parts=str(path).replace('\\','/').casefold().split('/')
    if any(p.startswith('.gbr-') for p in parts):return True
    return any(p=='_02_generation' and i+1<len(parts) and parts[i+1] in {'_runtime','runtime'} for i,p in enumerate(parts))


def title(meta):
    return next((str(meta[k]).strip() for k in ('title','song','song_title') if meta.get(k) is not None and str(meta[k]).strip()),None)


def archive_naming(generations):
    first=generations[0]
    placeholder=worker_intermediate(first['original_directory']+'/'+first['original_filename']) and not title(json.loads(first['parsed_json']))
    name=first['title']
    if placeholder:name=next((title(json.loads(g['parsed_json'])) for g in generations if title(json.loads(g['parsed_json']))),name)
    return name,placeholder


def display_metadata(db,generation):
    meta=json.loads(generation['parsed_json'])
    if title(meta) or not worker_intermediate(generation['original_directory']+'/'+generation['original_filename']):return meta
    # The PCM identity is exact, independent of filename/tags/PCM container depth.
    # Retain the original generation, raw metadata, reviews and IDs unchanged.
    candidates=db.all("""SELECT m.parsed_json FROM generation_metadata m JOIN generations g ON g.id=m.generation_id
        WHERE g.audio_id=? AND m.metadata_status IN ('valid','embedded')
        ORDER BY CASE WHEN json_extract(m.parsed_json,'$.gbr_run_id') IS NOT NULL THEN 0 ELSE 1 END,g.rowid""",(generation['audio_id'],))
    for candidate in candidates:
        resolved=json.loads(candidate['parsed_json'])
        if title(resolved):return resolved
    return meta
