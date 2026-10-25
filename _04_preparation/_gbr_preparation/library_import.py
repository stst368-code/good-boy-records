"""One-time, per-recording import of the porto showcase, including MP3-only files.

The shared DB stores the resumable job and receipts. Source files are removed
only after verified copies and their recording/release references commit.
"""
import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import threading
import unicodedata
import uuid
import os

from .service import canonical,digest,now
from .showcase import recording_folder
from .release_catalogue import share_token,release_url


def name_key(value):
    value=unicodedata.normalize('NFKD',str(value or '')).casefold()
    return re.sub(r'[^a-z0-9]+','-', ''.join(c for c in value if not unicodedata.combining(c))).strip('-')


class LibraryImport:
    def __init__(self,publisher):
        self.p=publisher;self.db=publisher.db;self.lock=threading.Lock();self.running=False
        if publisher.prep.enabled:
            state=self.state()
            if state['status']=='running':
                import psutil
                owner=state.get('owner',{})
                try:alive=bool(owner.get('pid') and abs(psutil.Process(owner['pid']).create_time()-owner.get('created_at',0))<.01)
                except psutil.NoSuchProcess:alive=False
                except psutil.AccessDenied:alive=True
                if not alive:
                    state.update(status='interrupted',message='Previous import controller stopped; resume to continue');self.persist(state)

    def state(self):
        row=self.db.one("SELECT value_json FROM platform_settings WHERE key='showcase_import'")
        value=json.loads(row['value_json']) if row else {'status':'not_started','completed':0,'failed':0,'items':[]}
        if value.get('publications'):
            counts=self.db.all("SELECT status,COUNT(*) n FROM processing_tasks WHERE id IN (SELECT value FROM json_each(?)) GROUP BY status",(canonical(value['publications']),))
            value['sync']={row['status']:row['n'] for row in counts}
        return value

    def persist(self,value):
        saved={k:v for k,v in value.items() if k!='sync'}
        self.db.execute("INSERT INTO platform_settings VALUES('showcase_import',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(canonical(saved),now()))

    def public_catalogue(self):
        path=self.p.root/'_96_website/_data/catalogue.json'
        saved=json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        return [(t,'public') for t in saved.get('tracks',[])]+[(t,'easter') for t in saved.get('easter_tracks',saved.get('easter',{}).get('tracks',[]))]

    def discover(self,root):
        if Path(root).expanduser().is_symlink():raise ValueError('Choose a real showcase directory, not a symlink')
        root=Path(root).expanduser().resolve()
        if not root.is_dir() or root.is_symlink():raise ValueError('Choose the existing porto showcase folder on this computer')
        for protected in (self.p.root/'_01_writing',self.p.root/'_02_generation',self.p.showcase.root):
            if root.is_relative_to(protected.resolve()):raise ValueError('Choose the old showcase, outside writing, worker output and the new showcase')
        groups={}
        for path in root.rglob('*'):
            if path.suffix.lower() not in {'.flac','.mp3'} or not path.is_file():continue
            if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Symlinks are not imported')
            key=(str(path.parent),path.stem.casefold());groups.setdefault(key,[]).append(path)
        items=[]
        for paths in groups.values():
            audio=next((p for p in paths if p.suffix.lower()=='.flac'),paths[0])
            files=[p for p in audio.parent.iterdir() if p.is_file() and (p.stem.casefold()==audio.stem.casefold() or p.name.casefold().startswith(audio.stem.casefold()+'.'))]
            relative='showcase/'+audio.relative_to(root).as_posix()
            public=[(track,room) for track,room in self.public_catalogue() if relative in list(track.get('audio',{}).get('sources',{}).values())]
            if len(public)>1:raise ValueError('Several old catalogue records claim '+relative)
            track,room=public[0] if public else (None,'easter' if audio.parent.name.casefold()=='easter' else 'public')
            if track:
                for field in ('yamlUrl','artwork_url','lyrics_url','playback_url'):
                    url=track.get(field)
                    if isinstance(url,str) and url.startswith('showcase/'):
                        file=root/url[len('showcase/'):]
                        if not file.resolve().is_relative_to(root):raise ValueError('Unsafe legacy public path')
                        if file.is_file() and file not in files:files.append(file)
            items.append({'source':str(audio),'files':[str(p) for p in files],'track':track,'room':room,
                          'status':'pending','title':(track or {}).get('displayTitle') or audio.stem})
        return root,items

    def start(self,directory,synchronise=True):
        self.p.prep.require()
        with self.lock:
            if self.running:return self.state()
            state=self.state();root=Path(directory).expanduser().resolve()
            if state.get('directory')==str(root) and state.get('status') in {'failed','interrupted','running','completed_with_issues'}:
                for item in state['items']:
                    if item['status']=='failed':item['status']='pending'
            elif state.get('directory')==str(root) and state.get('status')=='done':
                if synchronise:
                    state['synchronise']=True;state['publications']=self.queue_sync(state);self.persist(state)
                return state
            else:
                root,items=self.discover(directory)
                state={'id':uuid.uuid4().hex,'directory':str(root),'items':items,'completed':0,'failed':0,'created_at':now(),'synchronise':bool(synchronise)}
            state.update(status='running',synchronise=bool(synchronise),message='Import queued',updated_at=now(),owner=self.p.prep.owner());self.persist(state);self.running=True
            self.p.pool.submit(self.run,state)
            return state

    def normalise(self,item):
        from _gbr_yaml_editor.document import edit
        from _gbr_yaml_editor.cleanup import newline_repair,RENAMES,CHECKPOINTS
        from .library_yaml import update
        import yaml
        source=Path(item['source']);track=item.get('track') or {}
        file=next((Path(p) for p in item['files'] if Path(p).suffix.lower() in {'.yaml','.yml'} and Path(p).stem.casefold()==source.stem.casefold()),None)
        if file is None:file=next((Path(p) for p in item['files'] if Path(p).suffix.lower() in {'.yaml','.yml'}),None)
        raw=file.read_bytes() if file else ('title: '+json.dumps(item['title'],ensure_ascii=False)+'\n').encode('utf-8')
        meta=yaml.safe_load(raw.decode('utf-8-sig'))
        if not isinstance(meta,dict):raise ValueError('Legacy YAML is not a mapping')
        notes=[];operations=[]
        for old,new in RENAMES.items():
            if old in meta and new not in meta:
                operations.append({'kind':'rename','key':old,'new_key':new});meta[new]=meta[old];notes.append(old+' → '+new)
            elif old in meta and meta[old]!=meta[new]:raise ValueError('Conflicting legacy '+old+'/'+new+'; retained the source')
        for key,names in CHECKPOINTS.items():
            if meta.get(key) in names:
                operations.append({'kind':'set','key':key,'value':meta[key]+'.safetensors','position':'end'})
                notes.append(key+' exact checkpoint suffix restored')
        master=self.p.root/'_01_writing/_built/track_list.csv'
        songs=list(csv.DictReader(master.open(encoding='utf-8-sig',newline=''))) if master.is_file() else []
        candidates={name_key(meta.get('title')),name_key(track.get('displayTitle')),name_key(track.get('composition')),name_key(source.parent.name),name_key(source.stem)}
        matches=[]
        for row in songs:
            aliases={name_key(row.get('song_name')),name_key(Path(row.get('filename') or '').stem)}- {''}
            if candidates&aliases:matches.append(row)
        if not matches:matches=[row for row in songs if str(meta.get('song_id') or '')==row.get('song_id')]
        if not matches:
            prefixes=[(len(alias),row) for row in songs for alias in {name_key(row.get('song_name')),name_key(Path(row.get('filename') or '').stem)} if alias and any(key.startswith(alias+'-') for key in candidates)]
            if prefixes:
                longest=max(length for length,_ in prefixes);matches=[row for length,row in prefixes if length==longest]
        matched={row['song_id']:row for row in matches}
        if len(matched)>1:raise ValueError('Legacy identity matches multiple master songs; retained source')
        song=next(iter(matched.values()),None)
        genre=track.get('genre') or {}
        fields={'title':(song or {}).get('song_name') or track.get('displayTitle') or meta.get('title') or source.stem,
                'cluster':meta.get('cluster') or genre.get('parent_genre') or 'Unclassified',
                'style':meta.get('style') or meta.get('style_primary') or genre.get('child_genre') or track.get('variant') or 'Unspecified',
                'language':meta.get('language') or track.get('language') or 'en',
                'explicit':'yes' if (meta.get('explicit') if meta.get('explicit') is not None else track.get('explicit')) in (True,'yes','true') else 'no'}
        if song:fields.update(song_id=int(song['song_id']),variant_id=meta.get('variant_id') or 1)
        else:notes.append('Master song identity unavailable; kept as a legacy Library recording')
        if 'version' in meta and not meta.get('style') and str(meta.get('version') or '')==str(fields['style']):
            operations.append({'kind':'rename','key':'version','new_key':'style'})
        for key in ('lyrics','caption'):
            if isinstance(meta.get(key),str):
                repaired=newline_repair(meta[key])
                if repaired!=meta[key]:operations.append({'kind':'set','key':key,'value':repaired,'position':'end'});notes.append(key+' newline markers repaired')
        normal=update(edit(raw,operations),fields,self.p.root);values=yaml.safe_load(normal.decode('utf-8-sig'))
        return raw,normal,values,notes,file

    def public_audio(self,path,folder):
        """Reuse clean audio; strip legacy embedded metadata without re-encoding."""
        from mutagen.flac import FLAC
        from mutagen.mp3 import MP3
        from .publishing import public_flac
        from _review.indexer import audio_identity
        if path.suffix.lower()=='.flac':
            tags=FLAC(path)
            private=bool(tags.tags) or any(block.code not in {0,1,3,4} for block in tags.metadata_blocks)
        else:private=bool(MP3(path).tags)
        if not private:return path
        derived=folder/'_derived';derived.mkdir(exist_ok=True)
        target=derived/('public-'+digest(path)+path.suffix.lower())
        temporary=target.with_name('.'+target.name+'.pending')
        try:
            if path.suffix.lower()=='.flac':public_flac(path,temporary)
            else:
                shutil.copyfile(path,temporary);MP3(temporary).delete()
                if audio_identity(temporary)[0]!=audio_identity(path)[0]:raise ValueError('Public MP3 did not preserve decoded audio')
            if target.exists():
                if digest(target)!=digest(temporary):raise ValueError('Existing public audio changed; retained the legacy source')
            else:temporary.replace(target)
        finally:temporary.unlink(missing_ok=True)
        return target

    def import_one(self,item,state):
        from _review.indexer import audio_identity
        from _review.archive import verified_copy
        from .publishing import encoded
        source=Path(item['source']);receipt=self.db.one("SELECT id,generation_id,recipe_json FROM processing_tasks WHERE kind='library_import' AND json_extract(recipe_json,'$.original_audio')=? AND status='done'",(str(source),))
        if receipt:return {'generation_id':receipt['generation_id'],'receipt':receipt['id'],'resumed':True}
        raw,normal,meta,notes,yaml_path=self.normalise(item)
        aid,info=audio_identity(source);sha=digest(source)
        folder=recording_folder(self.p.showcase.root,meta,aid) if meta.get('song_id') and meta.get('variant_id') else self.p.showcase.root/'_legacy'/('_recording-'+aid)
        existing=self.db.all('SELECT g.id,m.yaml_sha256 FROM generations g JOIN generation_metadata m ON m.generation_id=g.id WHERE g.audio_id=?',(aid,))
        original_sha=hashlib.sha256(raw).hexdigest();normal_sha=hashlib.sha256(normal).hexdigest()
        exact=[r for r in existing if r['yaml_sha256'] in {original_sha,normal_sha}]
        if len(exact)>1 or not exact and len(existing)>1:raise ValueError('Several database generations share this audio; retained the source for identity review')
        gid=exact[0]['id'] if exact else existing[0]['id'] if existing else hashlib.sha256((aid+normal_sha).encode()).hexdigest()
        if self.db.one("SELECT 1 FROM processing_tasks WHERE generation_id=? AND status IN ('queued','running')",(gid,)):raise ValueError('Recording has active processing')
        old_audio=self.db.one('SELECT archive_path FROM audio WHERE id=?',(aid,))
        if old_audio and old_audio['archive_path'] and Path(old_audio['archive_path']).is_file() and Path(old_audio['archive_path']).resolve().is_relative_to(self.p.showcase.root.resolve()):
            # One recording already in managed storage must never get a second
            # bundle merely because the old export used another filename.
            raise ValueError('This audio already lives in the new showcase; retained the old export copy')
        plan=[];merge_root=None;current=None
        if old_audio and old_audio.get('archive_path'):
            candidate=Path(old_audio['archive_path'])
            if candidate.is_file() and candidate.resolve()!=source.resolve():
                if audio_identity(candidate)[0]!=aid:raise ValueError('The existing database audio changed; retained both sources')
                current=candidate
                if current.parent.name=='_source' and current.parent.parent.resolve().is_relative_to(self.p.prep.tracks.resolve()):merge_root=current.parent.parent
        for filename in item['files']:
            old=Path(filename)
            if old.is_symlink() or not old.resolve().is_relative_to(Path(state['directory'])):raise ValueError('Unsafe import source')
            target=folder/'_source'/('_originals' if old.suffix.lower() in {'.yaml','.yml','.bak'} else '')/old.name
            entry={'source':str(old),'destination':str(target),'sha256':digest(old)}
            if current and old==source:
                entry.update(destination=str(folder/'_source'/current.name),copy_source=str(current),target_sha256=digest(current),decoded_identity=aid)
            plan.append(entry)
        if current:
            owned=list(merge_root.rglob('*')) if merge_root else [p for p in current.parent.iterdir() if p.is_file() and (p==current or p.name.startswith(current.stem+'.'))]
            for old in owned:
                if not old.is_file():continue
                if old.is_symlink():raise ValueError('Existing recording contains a symlink; retained it')
                target=folder/old.relative_to(merge_root) if merge_root else folder/'_source'/old.name
                plan.append({'source':str(old),'destination':str(target),'sha256':digest(old)})
        # Persist a copy journal before filesystem mutations. The next explicit
        # retry verifies existing destinations and resumes the same recording.
        item['copy_plan']=plan;item['destination']=str(folder);self.persist(state)
        for entry in plan:verified_copy(Path(entry.get('copy_source',entry['source'])),Path(entry['destination']),entry.get('target_sha256',entry['sha256']),state['id'])
        canonical_audio=next(Path(entry['destination']) for entry in plan if entry['source']==str(source))
        sha=digest(canonical_audio)
        completed_yaml=(folder/'_derived'/'normalized.yaml') if current else canonical_audio.with_suffix('.yaml');completed_yaml.parent.mkdir(parents=True,exist_ok=True)
        if completed_yaml.exists() and completed_yaml.read_bytes()!=normal:raise ValueError('A different normalized YAML already exists; retained both source and destination')
        if not completed_yaml.exists():
            temporary=completed_yaml.with_name('.'+completed_yaml.name+'.'+state['id']+'.tmp')
            try:
                with temporary.open('wb') as stream:stream.write(normal);stream.flush();os.fsync(stream.fileno())
                temporary.replace(completed_yaml)
            finally:temporary.unlink(missing_ok=True)
        public={key:copy.deepcopy(value) for key,value in (item.get('track') or {}).items() if key in {
            'id','displayTitle','title','variant','version','shareToken','genre','audio','artwork_url','lyrics_url','playback_url',
            'lyrics','album','story','style','provenance','model','generation','composition','language','explicit','generation_datetime'}}
        public_id=public.get('id') or 'gbr-legacy-'+aid[:24]
        public.update(id=public_id,displayTitle=public.get('displayTitle') or meta['title'],title=public.get('displayTitle') or meta['title'],
                      shareToken=public.get('shareToken') or share_token(public_id,'release'),visibility=item['room'],
                      language=meta.get('language','en'),explicit=meta.get('explicit','no'),duration=info['duration'],song_id=meta.get('song_id'),variant_id=meta.get('variant_id'))
        if item.get('track'):public['legacy_id']=public_id
        if not item.get('track'):public.update(genre={'parent_genre':meta['cluster'],'child_genre':meta['style']},variant=meta['style'],lyrics={'raw':meta.get('lyrics','')})
        files=[];base=self.p.settings()['public_base']
        public_paths={}
        for role,key in [('cover','artwork_url'),('lyrics','lyrics_url'),('playback','playback_url')]:
            value=public.get(key)
            if isinstance(value,str) and value.startswith('showcase/'):
                old=str(Path(state['directory'])/value[len('showcase/'):]);entry=next((e for e in plan if e['source']==old),None)
                if entry:public_paths[role]=Path(entry['destination'])
            if role=='cover' and role not in public_paths:
                found=next((Path(e['destination']) for e in plan if Path(e['source']).suffix.lower() in {'.png','.jpg','.jpeg','.webp'}),None)
                if found:public_paths[role]=found
        mp3=next((Path(e['destination']) for e in plan if Path(e['source']).suffix.lower()=='.mp3'),None)
        if not mp3:
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise ValueError('FFmpeg is needed for the missing public MP3')
            mp3=folder/'_derived'/'audio.mp3';mp3.parent.mkdir(exist_ok=True)
            if not mp3.exists():
                temporary=mp3.with_name('.audio.pending.mp3')
                try:subprocess.run([ffmpeg,'-nostdin','-v','error','-y','-i',str(canonical_audio),'-map_metadata','-1','-c:a','libmp3lame','-q:a','2',str(temporary)],check=True,capture_output=True,timeout=900);temporary.replace(mp3)
                finally:temporary.unlink(missing_ok=True)
        assets=[('mp3',self.public_audio(mp3,folder),'audio/mpeg')]
        if source.suffix.lower()=='.flac':assets.append(('flac',self.public_audio(canonical_audio,folder),'audio/flac'))
        for role,path in public_paths.items():assets.append((role,path,('image/'+('jpeg' if path.suffix.lower() in {'.jpg','.jpeg'} else path.suffix[1:])) if role=='cover' else 'application/json'))
        urls={};pid=uuid.uuid4().hex;publication=uuid.uuid4().hex;receipt_id=uuid.uuid4().hex
        for role,path,mime in assets:
            checksum=digest(path);key='gbr/v2/objects/'+checksum[:2]+'/'+checksum+path.suffix
            files.append({'path':str(path),'sha256':checksum,'key':key,'size':path.stat().st_size,'content_type':mime})
            urls[role]=base+'/'+key
        # Existing website objects and share identities stay live while the new
        # catalogue is aligned. Only its verified pointer exposes these new URLs.
        public.update(audio={'sources':{'mp3':urls['mp3'],'flac':urls.get('flac')}},artwork_url=urls.get('cover'),lyrics_url=urls.get('lyrics'),playback_url=urls.get('playback'))
        if public.get('lyrics',{}).get('wordTiming') and urls.get('lyrics'):public['lyrics']['wordTiming']['src']=urls['lyrics']
        public['release_id']=pid
        recipe={'options':{'visibility':item['room'],'include_flac':source.suffix.lower()=='.flac','timed_lyrics':False},
                'track':public,'files':files,'source_sha256':sha,'yaml_sha256':normal_sha,
                'editorial':{'lyric_task_id':None,'lighting_task_id':None,'feature_artifact_id':None},
                'destination':{k:self.p.settings()[k] for k in ('endpoint','bucket','public_base','website_url')},
                'showcase':{'folder':str(folder),'cleanup_pending':[]},'imported':True}
        stamp=now();url=release_url(self.p.settings(),public,item['room']);song_key=hashlib.sha256(str(meta.get('song_id') or meta['title']).encode()).hexdigest()
        with self.db.transaction() as c:
            c.execute('INSERT OR IGNORE INTO songs VALUES(?,?,?,?)',(song_key,meta['title'],meta['style'],meta['cluster']))
            c.execute("INSERT OR IGNORE INTO audio(id,pcm_sha256,duration,sample_rate,bit_depth,channels,created_at,analysis_state) VALUES(?,?,?,?,?,?,?,'imported')",(aid,aid,info['duration'],info['sample_rate'],info['bit_depth'] or None,info['channels'],stamp))
            c.execute('INSERT OR IGNORE INTO generations VALUES(?,?,?,?,?,?,?,?,?,?)',(gid,aid,song_key,normal_sha,stamp,source.name,str(source.parent),'porto-showcase',None,stamp))
            c.execute("INSERT OR IGNORE INTO generation_metadata(generation_id,yaml_sha256,raw_yaml,raw_yaml_bytes,parsed_json,embedded_json,metadata_status,error) VALUES(?,?,?,?,?,'{}','valid',NULL)",(gid,normal_sha,normal.decode('utf-8-sig'),normal,canonical(meta)))
            # Existing generation metadata/review identities remain untouched.
            actual=c.execute('SELECT yaml_sha256 FROM generation_metadata WHERE generation_id=?',(gid,)).fetchone()[0];recipe['yaml_sha256']=actual
            c.execute('UPDATE audio SET archive_path=? WHERE id=?',(str(canonical_audio),aid))
            archived_yaml=next((e['destination'] for e in plan if Path(e['source']).suffix.lower() in {'.yaml','.yml'} and e['sha256']==actual),str(completed_yaml)) if existing else str(completed_yaml)
            c.execute('UPDATE generation_metadata SET yaml_archive_path=? WHERE generation_id=?',(archived_yaml,gid))
            c.execute("UPDATE media_files SET storage_state='consolidated' WHERE audio_id=? AND storage_state IN ('input','archived')",(aid,))
            # Move existing references with their one canonical recording bundle.
            for entry in plan:
                for table,key,column in [('media_files','id','path'),('generation_sources','id','yaml_path'),('generation_artifacts','id','uri')]:
                    c.execute(f'UPDATE {table} SET {column}=? WHERE {column}=?',(entry['destination'],entry['source']))
            if merge_root:
                from .showcase import remap
                for task in c.execute("SELECT id,recipe_json FROM processing_tasks WHERE generation_id=?",(gid,)).fetchall():
                    before=json.loads(task['recipe_json']);after=remap(before,merge_root,folder)
                    if before!=after:c.execute('UPDATE processing_tasks SET recipe_json=? WHERE id=?',(canonical(after),task['id']))
            c.execute("UPDATE media_files SET storage_state='consolidated' WHERE audio_id=? AND path<>? AND storage_state IN ('input','archived')",(aid,str(canonical_audio)))
            c.execute("INSERT OR IGNORE INTO media_files(audio_id,sha256,path,original_path,size,mtime_ns,storage_state,indexed_at) VALUES(?,?,?,?,?,?,'archived',?)",(aid,sha,str(canonical_audio),str(source),canonical_audio.stat().st_size,canonical_audio.stat().st_mtime_ns,stamp))
            media=c.execute("SELECT id FROM media_files WHERE path=? AND storage_state='archived' ORDER BY id DESC",(str(canonical_audio),)).fetchone()[0]
            c.execute('INSERT OR IGNORE INTO generation_sources(generation_id,media_id,yaml_path,original_yaml_path) VALUES(?,?,?,?)',(gid,media,str(completed_yaml),str(yaml_path) if yaml_path else None))
            c.execute("INSERT INTO publishing_selection(generation_id,status,selected_at,published_at,external_reference,updated_at) VALUES(?,'published',?,?,?,?) ON CONFLICT(generation_id) DO UPDATE SET status='published',external_reference=excluded.external_reference,updated_at=excluded.updated_at",(gid,stamp,stamp,url,stamp))
            receipt={'original_audio':str(source),'directory':state['directory'],'files':plan,'notes':notes,'lossy_source':source.suffix.lower()=='.mp3','public_id':public_id,'normalized_yaml':str(completed_yaml)}
            if current and digest(source)!=sha and source.suffix.lower()=='.flac':
                from mutagen.flac import FLAC
                receipt['original_container_tags']=dict(FLAC(source))
            for identifier,kind,body in [(receipt_id,'library_import',receipt),(pid,'release_prepare',recipe),(publication,'release_publish',{'prepared_id':pid,'visibility':item['room'],'title':public['displayTitle']})]:
                c.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at,telemetry_json) VALUES(?,?,?,'done',?,?,?,?,?,?)",(identifier,gid,kind,identifier,canonical(body),sha,stamp,stamp,canonical({'phase':'imported','url':url})))
            for index,entry in enumerate(files):c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(pid+'-'+str(index),'release_asset',entry['sha256'],entry['path'],entry['size'],canonical({'processing_task_id':pid,'source_generation_id':gid,'public_key':entry['key']})))
            if public_paths.get('cover'):
                path=public_paths['cover'];c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(receipt_id+'-cover','cover_artwork',digest(path),str(path),path.stat().st_size,canonical({'processing_task_id':receipt_id,'source_generation_id':gid})))
            c.execute("INSERT INTO review_history(generation_id,action,details_json,created_at) VALUES(?,'showcase_imported',?,?)",(gid,canonical({'receipt':receipt_id,'original_audio':str(source)}),stamp))
        return {'generation_id':gid,'receipt':receipt_id,'prepared_id':pid,'notes':notes,'lossy_source':source.suffix.lower()=='.mp3'}

    def cleanup(self,state):
        for item in state['items']:
            if item['status']!='done':continue
            item.pop('cleanup_pending',None)
            row=self.db.one('SELECT recipe_json FROM processing_tasks WHERE id=?',(item['result']['receipt'],))
            if not row:continue
            for entry in json.loads(row['recipe_json'])['files']:
                if any(other['status']!='done' and entry['source'] in other['files'] for other in state['items']):continue
                old,new=Path(entry['source']),Path(entry['destination'])
                if not old.exists():continue
                if not new.is_file() or new.is_symlink() or digest(new)!=entry.get('target_sha256',entry['sha256']):raise ValueError('An imported destination changed; retained legacy sources')
                if old.is_symlink() or digest(old)!=entry['sha256']:raise ValueError('A legacy source changed; retained it')
                writing=self.p.root/'_01_writing'
                if old.resolve().is_relative_to(writing.resolve()):continue
                try:old.unlink()
                except OSError:
                    if str(old) not in item.setdefault('cleanup_pending',[]):item['cleanup_pending'].append(str(old))
        for item in state['items']:
            if item['status']!='done':continue
            parents={Path(e['source']).parent for e in item.get('copy_plan',[])}
            root=Path(state['directory'])
            for folder in list(parents):
                if folder.is_relative_to(root):parents.update(p for p in folder.parents if p.is_relative_to(root) and p!=root)
            for folder in sorted(parents,key=lambda p:len(p.parts),reverse=True):
                try:folder.rmdir()
                except OSError:pass

    def run(self,state):
        try:
            for index,item in enumerate(state['items']):
                if self.p.stopping:
                    state.update(status='interrupted',message='Application closed; resume this import in Library');return
                if item['status']=='done':continue
                state.update(message=item['title'],current=index+1,updated_at=now());self.persist(state)
                try:item['result']=self.import_one(item,state);item['status']='done';item.pop('error',None)
                except Exception as exc:item.update(status='failed',error=str(exc))
                state['completed']=sum(i['status']=='done' for i in state['items']);state['failed']=sum(i['status']=='failed' for i in state['items']);self.persist(state)
            self.cleanup(state)
            if state.get('synchronise'):
                state['publications']=self.queue_sync(state)
            state.update(status='completed_with_issues' if state['failed'] or any(i.get('cleanup_pending') for i in state['items']) else 'done',updated_at=now(),message='Local showcase import finished'+('; R2 alignment queued' if state.get('synchronise') else ''))
        except Exception as exc:state.update(status='failed',error=str(exc))
        finally:self.persist(state);self.running=False

    def queue_sync(self,state):
        tasks=[]
        for item in state['items']:
            if item['status']!='done':continue
            gid=item['result']['generation_id']
            previous=self.db.one("SELECT id,status FROM processing_tasks WHERE generation_id=? AND id IN (SELECT value FROM json_each(?)) ORDER BY rowid DESC LIMIT 1",(gid,canonical(state.get('publications',[]))))
            if previous and previous['status'] in {'done','queued','running'}:tasks.append(previous['id'])
            else:
                pid=self.p.library.record(gid)['prepared_id']
                tasks.append(self.p.start_publish(pid)['id'])
        return tasks
