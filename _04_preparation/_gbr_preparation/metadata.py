"""Preparation metadata revisions, separate from immutable generation provenance."""
import csv
from datetime import date,datetime
import hashlib
import json
import os
from pathlib import Path
import uuid
import sys

import yaml

from .service import canonical,digest,now


def lyric_hash(metadata):
    return hashlib.sha256(canonical([metadata.get('language') or 'en',metadata.get('lyrics') or '']).encode('utf-8')).hexdigest()


def json_value(value):
    if isinstance(value,(date,datetime)):return value.isoformat()
    raise TypeError('Unsupported YAML metadata value: '+type(value).__name__)


class PreparationMetadata:
    def __init__(self,prep):
        self.prep=prep;self.db=prep.db
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_yaml_editor'))

    def original(self,gid):
        row=self.db.one('SELECT * FROM generation_metadata WHERE generation_id=?',(gid,))
        if not row:raise KeyError('Recording YAML unavailable')
        return row

    def path(self,gid,row):
        audio=self.db.one('SELECT a.archive_path FROM audio a JOIN generations g ON g.audio_id=a.id WHERE g.id=?',(gid,))
        if not audio or not audio['archive_path']:return None
        source=Path(audio['archive_path']);roots=(self.prep.tracks,self.prep.showcase)
        if not any(source.resolve().is_relative_to(root.resolve()) for root in roots):return None
        bundle=source.parent.parent if source.parent.name=='_source' else source.parent
        if row['yaml_archive_path']:candidates=[Path(row['yaml_archive_path'])]
        else:
            bound=self.db.all('SELECT yaml_path FROM generation_sources WHERE generation_id=? AND yaml_path IS NOT NULL',(gid,))
            candidates=[Path(r['yaml_path']) for r in bound if Path(r['yaml_path']).resolve().is_relative_to(bundle.resolve())]
            if not candidates:candidates=[p for p in (source.with_suffix('.yaml'),source.with_suffix('.yml')) if p.is_file()]
        candidates=list(dict.fromkeys(candidates))
        if len(candidates)>1:raise ValueError('Several YAML files are bound to this recording; resolve its source before publishing')
        if not candidates:return None
        path=candidates[0]
        if path.is_symlink() or not path.resolve().is_relative_to(bundle.resolve()):raise ValueError('Recording YAML must stay inside its managed bundle')
        return path

    def read(self,gid):
        """Refresh editable Preparation copies; published bundles use saved revisions."""
        self.prep.require()
        with self.prep.lock:
            row=self.original(gid);path=self.path(gid,row)
            previous=self.db.one("SELECT id,recipe_json FROM processing_tasks WHERE generation_id=? AND kind='preparation_yaml' AND status='done' ORDER BY rowid DESC LIMIT 1",(gid,))
            revision=json.loads(previous['recipe_json']) if previous else None
            original=row['raw_yaml_bytes']
            if original is None:original=(row['raw_yaml'] or '').encode('utf-8')
            if original and row['yaml_sha256'] and hashlib.sha256(original).hexdigest()!=row['yaml_sha256']:
                raise ValueError('Original generation YAML checksum is inconsistent; restore its database backup before publishing')
            raw=original;metadata=json.loads(row['parsed_json']);checksum=row['yaml_sha256'];identifier=None
            if revision:
                metadata=revision['metadata'];checksum=revision['sha256'];identifier=previous['id']
                artifact=self.db.one('SELECT uri,sha256 FROM generation_artifacts WHERE id=?',(identifier+'-yaml',))
                if not artifact or not Path(artifact['uri']).is_file() or digest(artifact['uri'])!=artifact['sha256']:raise ValueError('Saved Preparation YAML revision is unavailable or changed')
                raw=Path(artifact['uri']).read_bytes()
            # Only the editable stage may change the effective recording data.
            editable=path and path.resolve().is_relative_to(self.prep.tracks.resolve())
            if editable:
                if not path.is_file():raise ValueError('Preparation YAML is missing: '+str(path))
                current=path.read_bytes();current_sha=hashlib.sha256(current).hexdigest()
                if current_sha!=checksum:
                    try:
                        # Source rejects duplicate/ambiguous keys, aliases and
                        # malformed documents without rewriting a single byte.
                        from _gbr_yaml_editor.document import Source
                        Source(current)
                        parsed=yaml.safe_load(current.decode('utf-8-sig'))
                        try:original_fields=yaml.safe_load(original.decode('utf-8-sig'))
                        except (yaml.YAMLError,UnicodeError):original_fields={}
                        inherited={k:v for k,v in json.loads(row['parsed_json']).items() if k not in (original_fields if isinstance(original_fields,dict) else {})}
                        parsed={**inherited,**parsed}
                        parsed=json.loads(json.dumps(parsed,ensure_ascii=False,allow_nan=False,default=json_value))
                        canonical(parsed)
                    except (ValueError,UnicodeError,yaml.YAMLError,TypeError,RecursionError) as exc:
                        raise ValueError('Preparation YAML is invalid; saved metadata was retained: '+str(exc)) from exc
                    identifier=uuid.uuid4().hex
                    destination=path.parent.parent/'_derived' if path.parent.name=='_source' else path.parent/'_derived'
                    destination.mkdir(parents=True,exist_ok=True)
                    snapshot=destination/('preparation-'+current_sha+'.yaml')
                    if snapshot.exists() and digest(snapshot)!=current_sha:raise ValueError('Preparation YAML snapshot changed; retained the current file')
                    if not snapshot.exists():
                        with snapshot.open('xb') as stream:stream.write(current);stream.flush();os.fsync(stream.fileno())
                    body={'parent_id':previous['id'] if previous else None,'source_path':str(path),'sha256':current_sha,'metadata':parsed}
                    with self.db.transaction() as c:
                        c.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at) VALUES(?,?,'preparation_yaml','done',?,?,?,?,?)",(identifier,gid,identifier,canonical(body),row['yaml_sha256'] or current_sha,now(),now()))
                        c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(identifier+'-yaml','preparation_yaml',current_sha,str(snapshot),len(current),canonical({'processing_task_id':identifier,'source_generation_id':gid})))
                    raw,metadata,checksum=current,parsed,current_sha
            return {'yaml':raw.decode('utf-8-sig',errors='replace'),'_raw_bytes':raw,'metadata':metadata,'sha256':checksum,'revision_id':identifier,
                    'path':str(path) if path else None,'original_yaml':original.decode('utf-8-sig',errors='replace'),
                    'editable':bool(editable),
                    'lyrics_sha256':lyric_hash(metadata),'original_lyrics_sha256':lyric_hash(json.loads(row['parsed_json']))}

    def catalogue(self,gid):
        from _review.song_names import SongNames,identifier,name_key,base_name
        current=self.read(gid);built=self.prep.root/'_01_writing/_built'
        def rows(filename):
            path=built/filename
            if not path.is_file():return []
            with path.open(encoding='utf-8-sig',newline='') as stream:return list(csv.DictReader(stream))
        tracks=rows('track_list.csv');variants=rows('variant_list.csv')+rows('variants_list.csv')
        names=SongNames([],built);matched=names.lookup(current['metadata'].get('title') or '',prefix=True)
        sid=identifier(current['metadata'].get('song_id')) or (matched[5:] if matched and matched.startswith('song:') else None)
        aliases={name_key(current['metadata'].get('title') or ''),name_key(base_name(Path(current['path']).stem)) if current['path'] else ''}
        found={identifier(r.get('variant_id')) for r in variants if identifier(r.get('song_id'))==sid and aliases&{name_key(r.get('song_name') or ''),name_key(Path(r.get('filename') or '').stem)}}-{None}
        return {'songs':[{'id':identifier(r.get('song_id')),'title':r.get('song_name') or r.get('title') or r.get('filename')} for r in tracks if identifier(r.get('song_id'))],
                'variants':[{'song_id':identifier(r.get('song_id')),'id':identifier(r.get('variant_id')),'title':r.get('song_name') or r.get('filename')} for r in variants if identifier(r.get('song_id')) and identifier(r.get('variant_id'))],
                'song_id':identifier(current['metadata'].get('song_id')),'variant_id':identifier(current['metadata'].get('variant_id')),
                'suggested_song_id':matched[5:] if matched and matched.startswith('song:') else None,
                'suggested_variant_id':next(iter(found)) if len(found)==1 else None,'sha256':current['sha256']}

    def set_identity(self,gid,song_id,variant_id,expected_sha256):
        from .library_yaml import update
        with self.prep.lock:
            current=self.read(gid);choices=self.catalogue(gid)
            if current['sha256']!=expected_sha256:raise ValueError('YAML changed; reload its current values before saving identity')
            if str(song_id) not in {r['id'] for r in choices['songs']}:raise ValueError('Choose a song from track_list.csv')
            allowed={'1'}|{r['id'] for r in choices['variants'] if r['song_id']==str(song_id)}
            if choices['song_id']==str(song_id) and choices['variant_id']:allowed.add(choices['variant_id'])
            if str(variant_id) not in allowed:raise ValueError('Choose a registered variant or the base variant (1)')
            path=Path(current['path']) if current['path'] else None
            if not path or not path.resolve().is_relative_to(self.prep.tracks.resolve()):raise ValueError('Edit published metadata through Library; identity changes apply to Preparation copies')
            raw=path.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=expected_sha256:raise ValueError('YAML changed while saving; reload before retrying')
            updated=update(raw,{'song_id':song_id,'variant_id':variant_id},self.prep.root)
            if updated!=raw:
                temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.tmp')
                try:
                    with temporary.open('xb') as stream:stream.write(updated);stream.flush();os.fsync(stream.fileno())
                    if digest(path)!=expected_sha256:raise ValueError('YAML changed while saving; the newer file was retained')
                    temporary.replace(path)
                finally:temporary.unlink(missing_ok=True)
            return self.read(gid)
