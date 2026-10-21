"""View-scoped, nonempty filter facets and recording-to-song identity."""
from pathlib import Path
from .metadata_identity import worker_intermediate
from .song_names import SongNames, base_name
from .util import inside

TITLE = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.title')),''),NULLIF(trim(json_extract(m.parsed_json,'$.song')),''),NULLIF(trim(json_extract(m.parsed_json,'$.song_title')),''),s.title)"
GENRE = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.style_primary')),''),NULLIF(trim(json_extract(m.parsed_json,'$.style')),''),NULLIF(trim(json_extract(m.parsed_json,'$.genre')),''),NULLIF(trim(json_extract(m.parsed_json,'$.intended_genre')),''),NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),s.genre,'Genre not supplied')"
CLUSTER = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),'Cluster not supplied')"
PROJECTION = f"{TITLE} AS title,{GENRE} AS genre,{CLUSTER} AS cluster,g.song_id AS db_song_id,g.original_filename,COALESCE(json_extract(m.parsed_json,'$.song_id'),json_extract(m.parsed_json,'$.canonical_song_id')) AS catalogue_song_id,COALESCE(json_type(m.parsed_json,'$.song_id'),json_type(m.parsed_json,'$.canonical_song_id')) AS catalogue_song_type"
FIELDS = (('titles','song_key'),('clusters','cluster'),('genres','genre'))


def song_names(service):
    rows=service.db.all(f'''SELECT g.id,g.audio_id,g.original_directory,{PROJECTION},
        EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id) AS reviewed
        FROM generations g JOIN songs s ON s.id=g.song_id
        LEFT JOIN generation_metadata m ON m.generation_id=g.id''')
    root=getattr(service,'song_catalogue_root',lambda:None)()
    return SongNames(rows,root)


def available_audio(db):
    result=set()
    locations=db.all("SELECT id AS audio_id,archive_path AS path FROM audio WHERE archive_path IS NOT NULL")
    locations+=db.all("SELECT audio_id,path FROM media_files WHERE storage_state IN ('input','archived')")
    for row in locations:
        if row['audio_id'] in result or not row['path'] or worker_intermediate(row['path']):continue
        try:
            if Path(row['path']).is_file():result.add(row['audio_id'])
        except OSError:continue
    return result


def prepare(filters,names=None):
    choices={}
    for key,field in FIELDS:
        values=filters.get(key,[]) or []
        choices[field if names or key!='titles' else 'title']={names.resolve(value) if names and key=='titles' else value.casefold() for value in values}
    legacy=filters.get('song') or ''
    resolved=names.resolve(legacy) if names and legacy else None
    legacy_key=resolved if resolved and (resolved.startswith('song:') or base_name(legacy)!=legacy) else None
    return {**filters,'_choices':choices,'_legacy_song_key':legacy_key}


def matches(row,filters,omit=None):
    if omit!='directory' and filters.get('directory') and not inside(row['original_directory'],filters['directory']):return False
    for field,choices in filters['_choices'].items():
        if field==omit:continue
        value=str(row.get(field) or '') if field=='song_key' else str(row[field]).casefold()
        if choices and value not in choices:return False
    if omit!='song_key' and filters.get('_legacy_song_key') and row.get('song_key')!=filters['_legacy_song_key']:return False
    return all(field==omit or key=='song' and (omit=='song_key' or filters.get('_legacy_song_key')) or not filters.get(key) or filters[key].casefold() in str(row[field]).casefold()
               for key,field in (('song','title'),('genre','genre')))


def publishing_rows(service,rid,bucket='',status='',bookmarked=False,names=None):
    bucket=None if bucket in ('','all',None) else int(bucket)
    if bucket is not None and not 0<=bucket<=5:raise ValueError('Invalid bucket')
    where="(review_id IS NOT NULL OR publishing_status='selected' OR EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=analytics_dataset.generation_id AND b.reviewer_id=?))"
    args=[rid]
    if bookmarked:
        where+=' AND EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=analytics_dataset.generation_id AND b.reviewer_id=?)';args.append(rid)
    for field,value in [('bucket',bucket),('publishing_status',status or None)]:
        if value is not None:where+=' AND '+field+'=?';args.append(value)
    saved=service.db.all('SELECT generation_id,song_id,publishing_status,external_reference,bucket,reviewer_id,review_id,EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=analytics_dataset.generation_id AND b.reviewer_id=?) AS own_bookmarked FROM analytics_dataset WHERE '+where,(rid,*args))
    names=names or song_names(service);resolved={r['id']:r for r in names.annotate(names.records)};present=available_audio(service.db)
    return [{**resolved[r['generation_id']],**r} for r in saved
            if r['generation_id'] in resolved and resolved[r['generation_id']]['audio_id'] in present
            and not worker_intermediate(resolved[r['generation_id']]['original_directory'])]



def options(service,rid,filters=None,scope='queue',bucket='',status='',bookmarked=False):
    filters=dict(filters or {});names=song_names(service)
    if scope=='selection':
        all_rows=publishing_rows(service,rid,names=names)
        bucket=None if bucket in ('','all',None) else int(bucket)
        if bucket is not None and not 0<=bucket<=5:raise ValueError('Invalid bucket')
        rows=[row for row in all_rows if (bucket is None or row['bucket']==bucket) and (not status or row['publishing_status']==status) and (not bookmarked or row['own_bookmarked'])]
    else:
        rows=names.annotate(names.records);present=available_audio(service.db) if scope=='queue' else None
        rows=[row for row in rows if not worker_intermediate(row['original_directory']) and
              (row['reviewed'] if scope=='rated' else not row['reviewed'] and row['audio_id'] in present)]
    # Convert legacy text sessions when the user reopens the chooser; source/history stay intact.
    selected={key:list(filters.get(key) or []) for key,_ in FIELDS}
    if not selected['titles'] and filters.get('song'):
        selected['titles']=list({names.values[row['song_key']] for row in names.annotate(names.records) if filters['song'].casefold() in row['title'].casefold()}) or [filters['song']]
    if not selected['genres'] and filters.get('genre'):
        selected['genres']=sorted({row['genre'] for row in names.records if filters['genre'].casefold() in row['genre'].casefold()}) or [filters['genre']]
    selected['titles']=names.selection(selected['titles'])
    effective=prepare({**filters,**selected,'song':'','genre':''},names)
    result={key:[] for key,_ in FIELDS};labels={}
    for key,field in FIELDS:
        groups={}
        for row in rows:
            if not matches(row,effective,omit=field):continue
            value=row[field]
            item=groups.setdefault(value,{'all':set(),'remaining':set()});item['all'].add(row['audio_id'])
            if not row['reviewed']:item['remaining'].add(row['audio_id'])
        for value,ids in groups.items():
            if not ids['all']:continue
            item={'value':names.values[value] if key=='titles' else value,'total':len(ids['all']),'remaining':len(ids['remaining'])}
            if key=='titles' and item['value']!=names.labels[value]:item['label']=names.labels[value]
            result[key].append(item)
        result[key].sort(key=lambda item:(item.get('label') or item['value']).casefold())
    for value in selected['titles']:labels[value]=names.labels.get(names.resolve(value),value)
    directories=set()
    for row in rows:
        if not matches(row,effective,omit='directory'):continue
        folder=Path(row['original_directory']).resolve()
        for root in service.config.inputs:
            if folder.is_relative_to(root):
                while True:
                    directories.add(str(folder))
                    if folder==root:break
                    folder=folder.parent
    if scope=='selection':
        eligible=[row for row in all_rows if matches(row,effective)]
        result['buckets']=sorted({row['bucket'] for row in eligible if row['bucket'] is not None and (not status or row['publishing_status']==status) and (not bookmarked or row['own_bookmarked'])},reverse=True)
        result['statuses']=sorted({row['publishing_status'] for row in eligible if (bucket is None or row['bucket']==bucket or row['publishing_status']=='selected') and (not bookmarked or row['own_bookmarked'])})
        result['has_bookmarks']=any(row['own_bookmarked'] for row in eligible)
    result.update(selection=selected,labels=labels,directories=sorted(directories))
    return result
