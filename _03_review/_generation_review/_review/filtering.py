"""Live review vocabulary and exact multi-choice filters, without another catalogue."""
from .metadata_identity import worker_intermediate
from .util import inside


TITLE = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.title')),''),NULLIF(trim(json_extract(m.parsed_json,'$.song')),''),NULLIF(trim(json_extract(m.parsed_json,'$.song_title')),''),s.title)"
GENRE = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.style_primary')),''),NULLIF(trim(json_extract(m.parsed_json,'$.style')),''),NULLIF(trim(json_extract(m.parsed_json,'$.genre')),''),NULLIF(trim(json_extract(m.parsed_json,'$.intended_genre')),''),NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),s.genre,'Genre not supplied')"
CLUSTER = "COALESCE(NULLIF(trim(json_extract(m.parsed_json,'$.cluster')),''),'Cluster not supplied')"
PROJECTION = f"{TITLE} AS title,{GENRE} AS genre,{CLUSTER} AS cluster"


def prepare(filters):
    return {**filters, '_choices': {field:{value.casefold() for value in filters.get(key,[]) or []}
            for key,field in (('titles','title'),('clusters','cluster'),('genres','genre'))}}


def matches(row, filters):
    if filters.get('directory') and not inside(row['original_directory'], filters['directory']):return False
    for field, choices in filters['_choices'].items():
        if choices and str(row[field]).casefold() not in choices:return False
    # Persisted sessions and older clients retain their original substring semantics.
    return all(not filters.get(key) or filters[key].casefold() in str(row[field]).casefold()
               for key, field in (('song','title'),('genre','genre')))


def options(db, directory='', allowed=None):
    rows = db.all(f'''SELECT g.id,g.audio_id,g.original_directory,{PROJECTION},
        EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id) AS reviewed
        FROM generations g JOIN songs s ON s.id=g.song_id
        LEFT JOIN generation_metadata m ON m.generation_id=g.id''')
    groups = {key:{} for key in ('titles','clusters','genres')}
    for row in rows:
        if worker_intermediate(row['original_directory']) or directory and not inside(row['original_directory'],directory):continue
        if allowed is not None and row['id'] not in allowed:continue
        for key, field in (('titles','title'),('clusters','cluster'),('genres','genre')):
            value = str(row[field])
            item = groups[key].setdefault(value, {'all':set(),'remaining':set()})
            item['all'].add(row['audio_id'])
            if not row['reviewed']:item['remaining'].add(row['audio_id'])
    return {key:[{'value':value,'total':len(ids['all']),'remaining':len(ids['remaining'])}
                 for value,ids in sorted(values.items(),key=lambda pair:pair[0].casefold())]
            for key,values in groups.items()}
