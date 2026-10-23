"""Preview and migrate indexed legacy showcase bundles, without guessing identity."""
import copy
import json
from pathlib import Path
import re
import time
import uuid

from .service import canonical,digest,now
from .showcase import recording_folder


def roots(root):
    return [Path(root)/p for p in ('_96_website/_showcase','_showcase','showcase')]


class LegacyShowcase:
    def __init__(self,publisher):
        self.publisher=publisher;self.db=publisher.db;self.prep=publisher.prep
        self.plans={}

    def configuration(self):
        return {'roots':[{'path':str(p),'available':p.is_dir()} for p in roots(self.publisher.root)],
                'destination':str(self.publisher.showcase.root)}

    def public_track(self,source_root,yaml_path,audio_path):
        path=self.publisher.root/'_96_website/_data/catalogue.json'
        catalogue=json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        relative='showcase/'+yaml_path.relative_to(source_root).as_posix()
        matches=[t for t in catalogue.get('tracks',[]) if t.get('yamlUrl')==relative]
        if len(matches)>1:raise ValueError('Multiple public catalogue entries claim this YAML')
        if not matches:return None,{}
        track=copy.deepcopy(matches[0])
        if not re.fullmatch(r'[a-zA-Z0-9][a-zA-Z0-9_.-]{0,199}',str(track.get('id',''))):raise ValueError('Invalid legacy public track ID')
        if sum(t.get('id')==track['id'] for t in catalogue.get('tracks',[]))!=1:raise ValueError('Legacy public track ID is duplicated')
        if track.get('audio',{}).get('sources',{}).get('flac')!='showcase/'+audio_path.relative_to(source_root).as_posix():
            raise ValueError('Public catalogue FLAC does not identify this recording; retained both identities')
        public={}
        for role,url in [('cover',track.get('artwork_url')),('lyrics',track.get('lyrics_url')),
                         ('playback',track.get('playback_url'))]:
            if not url:continue
            if not isinstance(url,str) or not url.startswith('showcase/'):
                raise ValueError('Legacy '+role+' uses an external URL; retain it and resolve this recording separately')
            path=source_root/url.removeprefix('showcase/')
            if not path.resolve().is_relative_to(yaml_path.parent.resolve()) or path.is_symlink() or not path.is_file():
                raise ValueError('Legacy '+role+' is missing or shared outside this recording folder')
            if role=='cover' and path.suffix.lower() not in {'.png','.jpg','.jpeg','.webp'}:raise ValueError('Unsupported legacy cover format')
            if role!='cover':
                if path.suffix.lower()!='.json':raise ValueError('Unsupported legacy timing format')
                if not isinstance(json.loads(path.read_text(encoding='utf-8')),dict):raise ValueError('Invalid legacy timing JSON')
            public[role]={'relative':path.relative_to(yaml_path.parent).as_posix(),'sha256':digest(path)}
        # Keep the old public identity and presentation fields, not another copy
        # of lyrics, model settings or worker metadata already held by the DB.
        identity={k:track[k] for k in ('id','displayTitle','variant','version','shareToken','genre','story','style','provenance') if k in track}
        timing=track.get('lyrics',{}).get('wordTiming')
        if timing:identity['lyrics']={'wordTiming':timing}
        return identity,public

    def preview(self,directory):
        self.prep.require()
        root=next((p for p in roots(self.publisher.root) if str(p)==directory and p.is_dir()),None)
        if root is None:raise ValueError('Choose an available legacy showcase directory')
        if root.is_symlink():raise ValueError('Legacy showcase root must not be a symlink')
        folders=sorted({p.parent for p in root.rglob('*') if p.is_file() and p.suffix.lower() in {'.flac','.mp3'}})
        rows=[];plans={};seen=set()
        for folder in folders:
            row={'source':str(folder),'status':'blocked'}
            try:
                if folder==root or any(p!=folder and p.is_relative_to(folder) for p in folders):raise ValueError('Expected one self-contained recording folder; shared recording folders need manual separation')
                files=list(folder.iterdir())
                if any(p.is_symlink() for p in files) or not folder.resolve().is_relative_to(root.resolve()):raise ValueError('Symlink escapes are not migrated')
                flacs=[p for p in files if p.suffix.lower()=='.flac']
                if not flacs:raise ValueError('A lossless FLAC and its indexed database recording are required; retained the MP3 files')
                if len(flacs)!=1:raise ValueError('Multiple FLACs share this folder; separate their companions first')
                flac=flacs[0]
                yamls=[p for p in files if p.suffix.lower() in {'.yaml','.yml'} and not p.name.lower().endswith(('.source.yaml','.source.yml')) and p.name.lower() not in {'source.yaml','source.yml'}]
                matched=[p for p in yamls if p.stem==flac.stem]
                if len(matched)==1:yaml=matched[0]
                elif len(yamls)==1:yaml=yamls[0]
                else:raise ValueError('Cannot identify one completed YAML for this recording')
                if len(yamls)>1:raise ValueError('Several completed YAMLs share this folder; separate the recordings first')
                from _review.indexer import audio_identity
                aid=audio_identity(flac)[0];yh=digest(yaml)
                matches=self.db.all('''SELECT g.id,m.parsed_json,a.archive_path FROM generations g
                    JOIN generation_metadata m ON m.generation_id=g.id JOIN audio a ON a.id=g.audio_id
                    WHERE g.audio_id=? AND m.yaml_sha256=?''',(aid,yh))
                if len(matches)!=1:raise ValueError('Recording and exact YAML must match one indexed database generation; rescan/fix its identity first')
                match=matches[0];gid=match['id'];self.publisher.eligible(gid)
                self.publisher.catalogue_song(json.loads(match['parsed_json']))
                current=self.publisher.review.archiver.available(aid)
                if current is None or current.resolve()!=flac.resolve():raise ValueError('Database recording currently lives elsewhere; retained this extra copy')
                if aid in seen:raise ValueError('Another folder contains the same decoded recording')
                target=recording_folder(self.publisher.showcase.root,json.loads(match['parsed_json']),aid)
                if target.exists():raise ValueError('Recording already has a showcase destination; retained this folder')
                if self.db.one("SELECT 1 FROM processing_tasks WHERE kind='showcase_move' AND input_sha256=? AND status<>'cancelled'",(aid,)):raise ValueError('A move already exists for this recording; retry it from migration history')
                if self.db.one("SELECT 1 FROM processing_tasks t JOIN generations g ON g.id=t.generation_id WHERE g.audio_id=? AND t.status IN ('queued','running')",(aid,)):
                    raise ValueError('Recording has active work; wait for it to finish')
                if self.db.one("SELECT 1 FROM archive_operations WHERE audio_id=? AND status IN ('pending','running','failed')",(aid,)):raise ValueError('Finish the pending archive operation first')
                track,public=self.public_track(root,yaml,flac)
                plan={'source':str(folder),'destination':str(target/'_source'),'bundle':str(target),
                      'legacy_root':str(root),'items':self.publisher.showcase.inventory(folder),
                      'prepared_id':None,'legacy_track':track,'legacy_public':public,'generation_id':gid,'audio_id':aid,'yaml_sha256':yh,
                      'audio_relative':flac.name,'yaml_relative':yaml.name}
                row.update(status='ready',generation_id=gid,title=json.loads(match['parsed_json']).get('title') or folder.name,
                           destination=str(target),files=len(plan['items']),public_id=track.get('id') if track else None)
                plans[gid]=plan;seen.add(aid)
            except Exception as exc:row['error']=str(exc)
            rows.append(row)
        token=uuid.uuid4().hex
        self.plans={k:v for k,v in self.plans.items() if time.monotonic()-v['created']<1800}
        self.plans[token]={'created':time.monotonic(),'items':plans}
        return {'token':token,'rows':rows,'ready':len(plans),'blocked':sum(r['status']=='blocked' for r in rows)}

    def start(self,token,gids,synchronise):
        self.prep.require()
        preview=self.plans.get(token)
        if not preview or time.monotonic()-preview['created']>1800:raise ValueError('Migration preview expired; scan again')
        if not gids or len(set(gids))!=len(gids) or set(gids)-preview['items'].keys():raise ValueError('Select valid recordings from this preview')
        if synchronise:self.publisher.store()
        tasks=[]
        with self.prep.lock,self.db.transaction() as c:
            for gid in gids:
                plan=copy.deepcopy(preview['items'][gid]);current=self.publisher.eligible(gid)
                if current['yaml_sha256']!=plan['yaml_sha256'] or str(recording_folder(self.publisher.showcase.root,json.loads(current['parsed_json']),plan['audio_id']))!=plan['bundle']:
                    raise ValueError('Recording metadata changed; preview the migration again')
                old=c.execute("SELECT id FROM processing_tasks WHERE kind='showcase_move' AND input_sha256=? AND status<>'cancelled'",(plan['audio_id'],)).fetchone()
                if old:tasks.append(old[0]);continue
                if self.publisher.showcase.inventory(Path(plan['source']))!=plan['items']:raise ValueError('A legacy folder changed; scan again before migration')
                if Path(plan['bundle']).exists():raise ValueError('A showcase destination appeared; scan again')
                identifier=uuid.uuid4().hex;plan['synchronise']=synchronise
                c.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,telemetry_json) VALUES(?,?,'showcase_move','queued',?,?,?,?,?)",(identifier,gid,'showcase:'+plan['audio_id'],canonical(plan),plan['audio_id'],now(),canonical({'phase':'queued','message':'Legacy showcase migration'})))
                tasks.append(identifier)
        for identifier in tasks:self.publisher.pool.submit(self.run,identifier)
        return {'tasks':tasks}

    def run(self,identifier):
        row=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='showcase_move'",(identifier,))
        try:
            plan=json.loads(row['recipe_json']);self.publisher.eligible(row['generation_id'])
            result=self.publisher.showcase.move(row['generation_id'],None)
            if not plan.get('synchronise'):return
            # A retry resumes the same web package if one was already prepared.
            prepared=self.db.one("SELECT id,status FROM processing_tasks WHERE kind='release_prepare' AND json_extract(recipe_json,'$.options.legacy_migration_id')=? ORDER BY rowid DESC LIMIT 1",(identifier,))
            if prepared and prepared['status']=='done':self.publisher.start_publish(prepared['id'])
            elif not prepared or prepared['status'] not in {'queued','running'}:
                cover=self.db.one("SELECT id FROM generation_artifacts WHERE kind='cover_artwork' AND json_extract(metadata_json,'$.processing_task_id')=?",(identifier,))
                self.publisher.start_prepare(row['generation_id'],{'include_flac':True,'timed_lyrics':False,'artwork_id':cover['id'] if cover else None,'complete_publish':True,'legacy_migration_id':identifier})
        except Exception as exc:
            current=self.db.one('SELECT telemetry_json FROM processing_tasks WHERE id=?',(identifier,))
            progress=json.loads(current['telemetry_json'])
            self.db.execute("UPDATE processing_tasks SET status='failed',error=?,telemetry_json=? WHERE id=?",(str(exc),canonical(progress),identifier))

    def retry(self,identifier):
        row=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='showcase_move'",(identifier,))
        if not row or not json.loads(row['recipe_json']).get('legacy_root'):raise ValueError('Choose a legacy migration')
        if row['status'] in {'queued','running'}:raise ValueError('Migration is still active; restart the app only if its controller stopped')
        self.db.execute("UPDATE processing_tasks SET status='queued',error=NULL WHERE id=?",(identifier,))
        self.publisher.pool.submit(self.run,identifier)
        return {'id':identifier}

    def identity(self,gid):
        row=self.db.one("SELECT id,recipe_json FROM processing_tasks WHERE kind='showcase_move' AND generation_id=? AND status<>'cancelled' ORDER BY rowid DESC LIMIT 1",(gid,))
        if not row:return None
        plan=json.loads(row['recipe_json'])
        return {'task_id':row['id'],**plan} if plan.get('legacy_root') else None
