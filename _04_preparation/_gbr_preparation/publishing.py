"""Approved web releases on the existing processing/artifact/publishing ledger."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from urllib.parse import urlparse, quote
from urllib.request import Request, urlopen

from .service import canonical, digest, now
from .editorial import lighting
from .release_catalogue import (POINTER, destination, read_catalogue, remove_other,
                                release_url)

PUBLIC_BASE = 'https://pub-a5c11159f6144ae885f7795a5bf7086a.r2.dev'
DEFAULTS = {'endpoint': '', 'bucket': '', 'public_base': PUBLIC_BASE,
            'website_url': 'https://stst368-code.github.io/good-boy-records/',
            'profile': '', 'access_key_env': 'GBR_R2_ACCESS_KEY_ID', 'secret_key_env': 'GBR_R2_SECRET_ACCESS_KEY'}


def encoded(value):return (canonical(value)+'\n').encode('utf-8')


def write(path, raw):
    from _review.util import atomic_text
    atomic_text(path, raw.decode('utf-8'))


def public_flac(source, destination):
    """Strip private metadata from a public copy, preserving every PCM sample."""
    from mutagen.flac import FLAC
    from _review.indexer import audio_identity
    shutil.copyfile(source, destination)
    tags = FLAC(destination)
    tags.metadata_blocks = [block for block in tags.metadata_blocks if block.code in {0, 3}]
    tags.tags = None
    tags.cuesheet = None
    tags.save(deleteid3=True, padding=lambda _: 0)
    if audio_identity(destination)[0] != audio_identity(source)[0]:
        raise ValueError('Public FLAC did not preserve decoded audio')


def error_text(exc):
    # SDK exceptions can include endpoint/request details; never return credentials.
    if hasattr(exc, 'response'):
        return 'R2 request failed: '+str(exc.response.get('Error', {}).get('Code', 'unknown'))
    return str(exc)[:1500]


class R2:
    def __init__(self, settings):
        import boto3
        from botocore.config import Config
        self.settings = settings
        session = boto3.Session(profile_name=settings['profile'] or None)
        kwargs = {'endpoint_url': settings['endpoint'], 'region_name': 'auto',
                  'config': Config(signature_version='s3v4', retries={'max_attempts':2}, connect_timeout=10, read_timeout=60,
                                   request_checksum_calculation='when_required', response_checksum_validation='when_required')}
        key, secret = (os.environ.get(settings[name]) or settings.get(local) for name,local in
                       (('access_key_env','_access_key'),('secret_key_env','_secret_key')))
        if bool(key) != bool(secret):raise ValueError('Set both R2 credential environment variables')
        if key:kwargs.update(aws_access_key_id=key, aws_secret_access_key=secret)
        self.client = session.client('s3', **kwargs)
        self.bucket = settings['bucket']

    def read(self, key):
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key)
            with closing(result['Body']) as stream:
                raw = stream.read(8*1024**2+1)
            if len(raw)>8*1024**2:raise ValueError('Public catalogue exceeds 8 MiB')
            return raw, result['ETag']
        except self.client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] in {'NoSuchKey', '404'}:return None, None
            raise

    def compare_put(self, key, raw, etag=None, content_type='application/json', immutable=False):
        self.client.put_object(Bucket=self.bucket, Key=key, Body=raw, ContentType=content_type,
            CacheControl='public,max-age=31536000,immutable' if immutable else 'no-cache,max-age=0,must-revalidate',
            **({'IfMatch':etag} if etag else {'IfNoneMatch':'*'}))

    def upload(self, item):
        path=Path(item['path'])
        if digest(path)!=item['sha256']:raise ValueError('Prepared asset changed; build a new preview')
        try:
            with path.open('rb') as stream:
                self.client.put_object(Bucket=self.bucket, Key=item['key'], Body=stream, ContentLength=item['size'],
                    ContentType=item['content_type'], Metadata={'sha256':item['sha256']},
                    CacheControl='public,max-age=31536000,immutable', IfNoneMatch='*')
        except self.client.exceptions.ClientError as exc:
            if exc.response['Error']['Code'] not in {'PreconditionFailed', '412'}:raise
        # Verify exact remote bytes, including on retry of an existing object.
        self.verify(item)

    def verify(self, item):
        # Verification also works after a disposable local render has been removed.
        result=self.client.get_object(Bucket=self.bucket,Key=item['key']);h=hashlib.sha256();size=0
        with closing(result['Body']) as stream:
            for block in iter(lambda:stream.read(1024**2),b''):h.update(block);size+=len(block)
        if h.hexdigest()!=item['sha256'] or size!=item['size']:raise ValueError('Uploaded object failed checksum verification')

    def public_check(self, url, origin, expected=None):
        request=Request(url,headers={'Origin':origin, 'Range':'bytes=0-1023'})
        with urlopen(request,timeout=20) as result:
            data=result.read(1024)
            if result.headers.get('Access-Control-Allow-Origin') not in {'*', origin}:
                raise ValueError('R2 CORS must allow '+origin+' for GET/HEAD requests with the Range header')
            if not data:raise ValueError('Public object is empty')
            if expected and hashlib.sha256(data).hexdigest()!=expected:raise ValueError('Public content does not match the uploaded probe')


class Publisher:
    def __init__(self, root, review, preparation, store_factory=R2):
        self.root,self.review,self.prep,self.db=Path(root),review,preparation,review.db
        self.config_path=self.root/'_97_app/_runtime/publishing.json'
        self.secrets_path=self.config_path.with_name('publishing.secrets.json')
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='gbr-publishing')
        self.lock=threading.RLock();self.store_factory=store_factory
        from .showcase import Showcase
        self.showcase=Showcase(self)
        from .legacy_showcase import LegacyShowcase
        self.legacy=LegacyShowcase(self)
        from .easter_egg import EasterEgg
        self.easter=EasterEgg(self)
        # Interrupted requests are never replayed automatically. Publish retries
        # reconcile the remote pointer before changing local release state.
        if preparation.enabled:
            self.db.execute("UPDATE processing_tasks SET status='interrupted',error='Application restarted; retry explicitly to reconcile publication' WHERE kind IN ('release_prepare','release_publish') AND status IN ('queued','running')")
            self.db.execute("UPDATE processing_tasks SET status='interrupted',error='Application restarted; retry the legacy migration explicitly' WHERE kind='showcase_move' AND json_extract(recipe_json,'$.legacy_root') IS NOT NULL AND status IN ('queued','running')")

    def close(self):self.pool.shutdown(wait=True,cancel_futures=False)

    def settings(self):
        return {**DEFAULTS, **(json.loads(self.config_path.read_text(encoding="utf-8")) if self.config_path.is_file() else {})}

    def save_settings(self, values):
        if set(values)-set(DEFAULTS)-{'access_key','secret_key'}:raise ValueError('Unknown publishing setting')
        settings={**self.settings(), **{k:v for k,v in values.items() if k in DEFAULTS}}
        for name in ('public_base','website_url'):
            url=urlparse(settings[name])
            if url.scheme!='https' or not url.netloc or url.username or url.password or url.query or url.fragment:
                raise ValueError(name+' must be a public HTTPS URL without credentials/query parameters')
        if settings['endpoint'] and not re.fullmatch(r'https://[a-zA-Z0-9.-]+\.r2\.cloudflarestorage\.com/?',settings['endpoint']):
            raise ValueError('Use the R2 S3 API endpoint from the Cloudflare bucket settings')
        if settings['bucket'] and not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]',settings['bucket']):raise ValueError('Invalid bucket name')
        for name in ('access_key_env','secret_key_env'):
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',settings[name]):raise ValueError('Enter an environment variable name, not a credential')
        settings['public_base']=settings['public_base'].rstrip('/')
        if values.get('access_key') or values.get('secret_key'):
            if not values.get('access_key') or not values.get('secret_key'):raise ValueError('Enter both R2 key fields, or leave both blank to retain saved credentials')
            write(self.secrets_path,encoded({'access_key':values['access_key'],'secret_key':values['secret_key']}))
            try:self.secrets_path.chmod(0o600)
            except OSError:pass
        write(self.config_path,encoded(settings));return self.configuration()

    def configuration(self):
        settings=self.settings()
        return {'settings':settings,'configured':bool(settings['endpoint'] and settings['bucket']),
                'credentials_available':bool(self.secrets_path.is_file() or settings['profile'] or os.environ.get(settings['access_key_env']) and os.environ.get(settings['secret_key_env'])),
                'catalogue_url':settings['public_base']+'/'+POINTER}

    def store(self):
        settings=self.settings()
        if not settings['endpoint'] or not settings['bucket']:raise ValueError('Configure Publishing in Settings and run the connection check first')
        if self.secrets_path.is_file():
            credentials=json.loads(self.secrets_path.read_text(encoding="utf-8"))
            settings={**settings,'_access_key':credentials['access_key'],'_secret_key':credentials['secret_key']}
        return self.store_factory(settings)

    def check(self):
        store=self.store();s=self.settings()
        # A tiny public probe checks real write/read, URL mapping and browser CORS.
        raw=b'GBR public release connection check\n';sha=hashlib.sha256(raw).hexdigest()
        item={'key':'gbr/v2/check/'+sha+'.txt','sha256':sha,'size':len(raw),'content_type':'text/plain'}
        with __import__('tempfile').TemporaryDirectory(prefix='gbr-publish-check-') as directory:
            path=Path(directory)/'check.txt';path.write_bytes(raw);store.upload({**item,'path':str(path)})
        origin=urlparse(s['website_url']);store.public_check(s['public_base']+'/'+item['key'],origin.scheme+'://'+origin.netloc,sha)
        return {'ready':True,'catalogue_url':s['public_base']+'/'+POINTER}

    def publish_research(self):
        self.prep.require()
        import sys,tempfile
        sys.path.insert(0,str(self.root/'_96_website/_tools'))
        from export_platform import export
        settings=self.settings();store=self.store();pointer='gbr/v2/research.json'
        _,etag=store.read(pointer)
        with tempfile.TemporaryDirectory(prefix='gbr-public-research-') as directory:
            path=Path(directory)/'platform.json'
            payload=export(self.db.path,output=path,styles=self.root/'_01_writing/_references/styles.csv')
            sha=digest(path);key='gbr/v2/research/'+sha+'.json'
            store.upload({'path':str(path),'key':key,'sha256':sha,'size':path.stat().st_size,'content_type':'application/json'})
            origin=urlparse(settings['website_url']);store.public_check(settings['public_base']+'/'+key,origin.scheme+'://'+origin.netloc)
            manifest={'format':'gbr-public-research-v1','url':settings['public_base']+'/'+key,'sha256':sha,'recordings':payload['count'],'generated_at':payload['generated_at']}
            store.compare_put(pointer,encoded(manifest),etag=etag)
        self.db.execute("INSERT INTO review_history(action,details_json,created_at) VALUES('public_research_published',?,?)",(canonical(manifest),now()))
        return manifest

    def eligible(self, gid):
        self.prep.require()
        row=self.db.one('''SELECT g.id,g.audio_id,p.status,m.parsed_json,m.yaml_sha256 FROM generations g
            JOIN publishing_selection p ON p.generation_id=g.id JOIN generation_metadata m ON m.generation_id=g.id
            WHERE g.id=? AND p.status IN ('selected','published') AND EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id)''',(gid,))
        if not row:raise ValueError('Choose a reviewed recording flagged for distribution')
        return row

    def projects(self):
        rows=[p for p in self.prep.projects(include_completed=True).get('projects',[]) if p['ready'] and p['status'] in {'selected','published'}]
        for row in rows:
            row['artworks']=self.db.all("SELECT id,kind FROM generation_artifacts WHERE kind IN ('album_artwork','cover_artwork') AND json_extract(metadata_json,'$.source_generation_id')=?",(row['generation_id'],))
        return {'projects':[{k:p[k] for k in ('generation_id','audio_id','title','cluster','style','masters','artworks','status','completed')} for p in rows],
                'releases':self.releases()}

    def releases(self):
        if not self.prep.enabled:return []
        rows=self.db.all("SELECT id,generation_id,kind,status,error,recipe_json,telemetry_json,created_at FROM processing_tasks WHERE kind IN ('release_prepare','release_publish','showcase_move') ORDER BY rowid DESC LIMIT 100")
        for row in rows:
            recipe=json.loads(row.pop('recipe_json'));row['progress']=json.loads(row.pop('telemetry_json'))
            row['title']=recipe.get('track',{}).get('displayTitle') or (recipe.get('legacy_track') or {}).get('displayTitle') or recipe.get('title','Release')
            row['legacy_migration']=bool(recipe.get('legacy_root'))
            row['prepared_id']=recipe.get('prepared_id');row['track']=recipe.get('track')
            row['visibility']=recipe.get('visibility') or recipe.get('options',{}).get('visibility','public')
            row['files']=recipe.get('files',[])
            row['can_retry']=row['kind']=='release_publish' and row['status'] in {'failed','interrupted'}
            row['retained']=self.retained_preview(row['id'],row['generation_id'],recipe) if row['kind']=='release_prepare' else False
        return rows

    def retained_preview(self,pid,gid,recipe):
        if recipe.get('showcase'):return True
        moves=self.db.all("SELECT recipe_json FROM processing_tasks WHERE kind='showcase_move' AND status<>'cancelled' AND input_sha256=(SELECT audio_id FROM generations WHERE id=?)",(gid,))
        return any(any(f['relative'].startswith('_release/'+pid+'/') for f in json.loads(m['recipe_json'])['items']) for m in moves)

    def start_complete(self,gid,options):
        # One explicit action from Preparation. MP3 is always prepared; R2 work
        # follows the verified local move, without requiring another UI action.
        self.store()
        with self.lock:
            active=self.db.one("SELECT id FROM processing_tasks WHERE generation_id=? AND kind IN ('release_prepare','release_publish') AND status IN ('queued','running')",(gid,))
            if active:return {'id':active['id']}
            return self.start_prepare(gid,{**options,'complete_publish':True})

    def start_prepare(self,gid,options):
        destination(options.get('visibility','public'))
        self.eligible(gid);_,source=self.prep.source(gid,artifact_id=options.get('master_id'))
        if options.get('artwork_id'):
            art=self.db.one("SELECT * FROM generation_artifacts WHERE id=? AND kind IN ('album_artwork','cover_artwork') AND json_extract(metadata_json,'$.source_generation_id')=?",(options['artwork_id'],gid))
            if not art:raise ValueError('Artwork does not belong to this recording')
        identifier=uuid.uuid4().hex
        with self.db.transaction() as c:
            c.execute('''INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at)
                         VALUES(?,?,'release_prepare','queued',?,?,?,?)''',(identifier,gid,identifier,canonical(options),digest(source),now()))
        self.pool.submit(self._prepare,identifier);return {'id':identifier}

    def progress(self,identifier,**data):
        self.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(canonical(data),identifier))

    def catalogue_song(self,meta):
        with (self.root/'_01_writing/_built/track_list.csv').open(encoding='utf-8-sig',newline='') as stream:
            matches=[r for r in csv.DictReader(stream) if str(r.get('song_id'))==str(meta.get('song_id'))]
        if len(matches)!=1:raise ValueError('Song ID must match exactly one master catalogue row')
        song=matches[0];title=song.get('song_name') or song.get('title')
        if not title:raise ValueError('Master catalogue title is missing')
        return song,title

    def _prepare(self,identifier):
        directory=None
        try:
            task=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,));gid=task['generation_id']
            options=json.loads(task['recipe_json']);row=self.eligible(gid);meta=json.loads(row['parsed_json'])
            _,source=self.prep.source(gid,artifact_id=options.get('master_id'))
            if digest(source)!=task['input_sha256']:raise ValueError('Working audio changed before preparation')
            import soundfile as sf
            info=sf.info(source)
            if info.frames<=0:raise ValueError('Working audio is empty')
            if any(not re.fullmatch(r'[1-9][0-9]*',str(meta.get(key) or '')) for key in ('song_id','variant_id')):
                raise ValueError('Release YAML needs valid song_id and variant_id values; fix it before publishing')
            # Title/album identity comes from the current master catalogue.
            song,title=self.catalogue_song(meta)
            original=self.prep.source(gid)[1]
            bundle=original.parent.parent if original.resolve().is_relative_to(self.prep.showcase.resolve()) else self.prep.tracks/row['audio_id']
            directory=bundle/'_release'/identifier;directory.mkdir(parents=True)
            self.db.execute("UPDATE processing_tasks SET status='running',started_at=? WHERE id=?",(now(),identifier))
            self.progress(identifier,phase='encoding',message='Encoding web audio')
            ffmpeg=shutil.which('ffmpeg')
            if not ffmpeg:raise ValueError('FFmpeg is required to prepare web audio')
            mp3=directory/'audio.mp3'
            result=subprocess.run([ffmpeg,'-nostdin','-v','error','-n','-i',str(source),'-map','0:a:0','-map_metadata','-1',
                                   '-c:a','libmp3lame','-q:a','2','-ar','44100','-ac','2',str(mp3)],capture_output=True,text=True,timeout=900, encoding="utf-8", errors="replace")
            if result.returncode:raise ValueError('Web audio encoding failed: '+result.stderr[-1200:])
            paths=[(mp3,'audio/mpeg')]
            if options.get('include_flac',True):
                flac=directory/'audio.flac';public_flac(source,flac)
                paths.append((flac,'audio/flac'))
            art=None
            if options.get('artwork_id'):
                from PIL import Image,ImageOps
                artifact=self.db.one('SELECT * FROM generation_artifacts WHERE id=?',(options['artwork_id'],))
                source_art=self.prep.artifact(artifact['id'])
                if digest(source_art)!=artifact['sha256']:raise ValueError('Artwork changed')
                art=directory/'cover.jpg'
                with Image.open(source_art) as image:
                    ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'),(1024,1024)).save(art,format='JPEG',quality=93)
                paths.append((art,'image/jpeg'))
            editorial=self.prep.editorial(gid,options.get('master_id'))
            legacy=self.legacy.identity(gid)
            lyrics_url=None
            if options.get('timed_lyrics'):
                if not editorial['approved']:raise ValueError('Approve lyrics for this exact working audio in Preparation first')
                lines={}
                for word in editorial['words']:
                    if word.get('omitted'):continue
                    if word.get('start') is None or word.get('end') is None:raise ValueError('Approved lyrics contain untimed words')
                    lines.setdefault(word['line'],[]).append({k:word[k] for k in ('text','start','end')})
                payload={'format':'gbr-lyrics-public-v1','language':editorial['language'],'lines':[
                    {'text':' '.join(w['text'] for w in words),'start':min(w['start'] for w in words),'end':max(w['end'] for w in words),'words':words}
                    for words in lines.values()]}
                lyrics_url=directory/'lyrics.json';lyrics_url.write_bytes(encoded(payload));paths.append((lyrics_url,'application/json'))
            playback=None
            if editorial['features']:
                features=editorial['features'];payload=lighting({'sensitivity':editorial['sensitivity'],'sections':editorial['sections']},features)
                # Section labels are private editing notes; only numeric events ship.
                payload={k:payload[k] for k in ('format','duration','dance')}
                playback=directory/'playback.json';playback.write_bytes(encoded(payload));paths.append((playback,'application/json'))
            # Previously public sidecars can follow an unchanged legacy source.
            # They never become approvals for a different master or new edits.
            if legacy and options.get('legacy_migration_id')==legacy['task_id'] and not options.get('master_id'):
                for role,name in [('lyrics','lyrics.json'),('playback','playback.json')]:
                    item=legacy.get('legacy_public',{}).get(role)
                    if not item or (directory/name).exists():continue
                    path=Path(legacy['destination'])/item['relative']
                    if digest(path)!=item['sha256']:raise ValueError('Legacy public sidecar changed; retained the existing release')
                    output=directory/name;shutil.copyfile(path,output);paths.append((output,'application/json'))
            settings=self.settings();base=settings['public_base']
            files=[];urls={}
            for path,mime in paths:
                sha=digest(path);key='gbr/v2/objects/'+sha[:2]+'/'+sha+path.suffix
                files.append({'path':str(path),'sha256':sha,'key':key,'size':path.stat().st_size,'content_type':mime})
                urls[path.name]=base+'/'+key
            track_id='gbr-'+str(meta['song_id'])+'-'+str(meta.get('variant_id') or '1')+'-'+row['audio_id'][:16]
            from .release_catalogue import share_token
            track={'id':track_id,'displayTitle':title,'title':title,'composition':str(meta['song_id']),
                'shareToken':share_token(track_id,'release'),
                'variant':str(meta.get('style') or meta.get('style_primary') or ''),'version':str(meta.get('style') or meta.get('style_primary') or ''),
                'song_id':meta['song_id'],'variant_id':meta.get('variant_id'),'language':meta.get('language') or 'en',
                'explicit':meta.get('explicit'),'duration':info.duration,'album':{'name':song.get('album_name') or None,'position':song.get('album_position') or None},
                'genre':{'parent_genre':str(meta.get('cluster') or 'Unclassified'),'child_genre':str(meta.get('style') or meta.get('style_primary') or 'Unspecified'),
                         'parent_colour':'#a89069','child_colour':'#c4aa82','taxonomy_order':10000},
                'audio':{'sources':{'mp3':urls['audio.mp3'],'flac':urls.get('audio.flac')}},
                'artwork_url':urls.get('cover.jpg'),'lyrics':{'raw':str(meta.get('lyrics') or '')},
                'lyrics_url':urls.get('lyrics.json'),'playback_url':urls.get('playback.json'),
                'release_id':identifier,'generation_datetime':meta.get('generation_datetime')}
            track['visibility']=options.get('visibility','public')
            def checkpoint(value):return str(value or '').replace('\\','/').rsplit('/',1)[-1]
            track['model']={'name':checkpoint(meta.get('model')),'dit':checkpoint(meta.get('dit')),'textEncoder':checkpoint(meta.get('text_encoder'))}
            from _review.indexer import number
            track['generation']={name:number(meta.get(key)) for name,key in [('encoderCfg','encoder_cfg'),('topK','top_k'),('samplerCfg','sampler_cfg'),('steps','sampler_steps')]}
            for name,key in [('encoderSeed','encoder_seed'),('samplerSeed','sampler_seed'),('sampler','sampler'),('scheduler','scheduler')]:
                value=meta.get(key)
                track['generation'][name]=str(value) if value is not None and isinstance(value,(str,int)) else None
            if legacy and legacy.get('legacy_track'):
                old=legacy['legacy_track']
                track.update(id=old['id'],shareToken=old.get('shareToken') or share_token(old['id'],str(old.get('variant') or old.get('version') or '')))
                track['legacy_id']=old['id']
                if not (meta.get('style') or meta.get('style_primary')):
                    # Old public labels remain useful when a legacy YAML has
                    # not yet acquired the new cluster/style fields. Do not
                    # infer style from an unrelated YAML version field.
                    for key in ('genre','variant','version'):
                        if key in old:track[key]=old[key]
                for key in ('story','style','provenance'):
                    if key in old:track[key]=old[key]
                if track.get('lyrics_url') and old.get('lyrics',{}).get('wordTiming') and not options.get('timed_lyrics'):
                    track['lyrics']['wordTiming']={**old['lyrics']['wordTiming'],'src':track['lyrics_url']}
            recipe={'options':options,'track':track,'files':files,'source_sha256':task['input_sha256'],
                    'yaml_sha256':row['yaml_sha256'],'editorial':{k:editorial[k] for k in ('lyric_task_id','lighting_task_id','feature_artifact_id')},
                    'destination':{k:settings[k] for k in ('endpoint','bucket','public_base','website_url')}}
            self.eligible(gid)
            with self.db.transaction() as c:
                c.execute("UPDATE processing_tasks SET status='done',recipe_json=?,telemetry_json=?,completed_at=?,error=NULL WHERE id=?",(canonical(recipe),canonical({'phase':'prepared','message':'Preview ready; nothing uploaded'}),now(),identifier))
                for index,item in enumerate(files):
                    c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(identifier+'-'+str(index),'release_asset',item['sha256'],item['path'],item['size'],canonical({'processing_task_id':identifier,'public_key':item['key']})))
            if options.get('complete_publish'):self.start_publish(identifier)
        except Exception as exc:
            self.db.execute("UPDATE processing_tasks SET status='failed',error=?,completed_at=? WHERE id=?",(error_text(exc),now(),identifier))
            if directory:shutil.rmtree(directory,ignore_errors=True)

    def start_publish(self,identifier,visibility=None):
        self.prep.require()
        task=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='release_prepare' AND status='done'",(identifier,))
        if not task:raise ValueError('Prepare and review a release first')
        self.eligible(task['generation_id']);recipe=json.loads(task['recipe_json'])
        visibility=visibility or recipe.get('options',{}).get('visibility','public')
        destination(visibility)
        if not recipe.get('track'):raise ValueError('Release preview is incomplete')
        if not recipe.get('showcase') and not all(Path(f['path']).is_file() for f in recipe.get('files',[])):
            # A move may have committed before its receipt was saved. Its own
            # journal recovers those paths; pre-upgrade disposable previews do not.
            if not self.db.one("SELECT 1 FROM processing_tasks WHERE kind='showcase_move' AND input_sha256=(SELECT audio_id FROM generations WHERE id=?) AND status<>'cancelled'",(task['generation_id'],)):
                raise ValueError('Historical preview files are unavailable; prepare a fresh release to create its local MP3')
        publish_id=uuid.uuid4().hex
        with self.db.transaction() as c:
            active=c.execute("SELECT id FROM processing_tasks WHERE kind='release_publish' AND generation_id=? AND status IN ('queued','running')",(task['generation_id'],)).fetchone()
            if active:return {'id':active[0]}
            c.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at) VALUES(?,?,'release_publish','queued',?,?,?,?)",
                      (publish_id,task['generation_id'],'publish:'+identifier,canonical({'prepared_id':identifier,'title':recipe['track']['displayTitle'],'visibility':visibility}),task['input_sha256'],now()))
        self.pool.submit(self._publish,publish_id);return {'id':publish_id}

    def _publish(self,identifier):
        try:
            task=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,));request=json.loads(task['recipe_json']);pid=request['prepared_id']
            prepared=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(pid,));recipe=json.loads(prepared['recipe_json']);gid=task['generation_id']
            self.eligible(gid);settings=self.settings()
            if recipe['destination']!={k:settings[k] for k in recipe['destination']}:raise ValueError('Publishing destination changed; prepare a new preview')
            self.db.execute("UPDATE processing_tasks SET status='running',started_at=?,error=NULL WHERE id=?",(now(),identifier))
            if 'showcase' not in recipe:
                self.progress(identifier,phase='showcase',message='Verifying and moving completed recording')
                # Clear only disposable, unreferenced mix auditions. Masters,
                # YAML, corrected timing, light cues and artwork move together.
                if not self.db.one("SELECT 1 FROM processing_tasks t JOIN generations g ON g.id=t.generation_id WHERE g.audio_id=(SELECT audio_id FROM generations WHERE id=?) AND t.kind='showcase_move'",(gid,)):
                    self.prep.clean_previews(gid)
                moved=self.showcase.move(gid,pid)
                prepared=self.db.one('SELECT * FROM processing_tasks WHERE id=?',(pid,))
                recipe=json.loads(prepared['recipe_json']);recipe['showcase']=moved
                self.db.execute('UPDATE processing_tasks SET recipe_json=? WHERE id=?',(canonical(recipe),pid))
            else:
                moved=self.showcase.move(gid,pid)
                recipe['showcase']=moved
                self.db.execute('UPDATE processing_tasks SET recipe_json=? WHERE id=?',(canonical(recipe),pid))
            visibility=request.get('visibility') or recipe['options'].get('visibility','public')
            pointer,_=destination(visibility)
            recipe['options']['visibility']=visibility;recipe['track']['visibility']=visibility
            self.db.execute('UPDATE processing_tasks SET recipe_json=? WHERE id=?',(canonical(recipe),pid))
            store=self.store();catalogue,etag=read_catalogue(store,visibility)
            present=next((t for t in catalogue['tracks'] if t.get('id')==recipe['track']['id']),None)
            # A new preview can replace its predecessor; an older receipt cannot
            # resurrect itself over a newer preview on either destination.
            other,_=read_catalogue(store,'public' if visibility=='hidden' else 'hidden')
            other_track=next((t for t in other['tracks'] if t.get('id')==recipe['track']['id']),None)
            for current in (present,other_track):
                if current and current.get('release_id') not in {None,pid}:
                    newer=self.db.one('SELECT rowid FROM processing_tasks WHERE id=?',(current['release_id'],))
                    mine=self.db.one('SELECT rowid FROM processing_tasks WHERE id=?',(pid,))
                    if not newer or newer['rowid']>=mine['rowid']:raise ValueError('A newer release replaced this preview; use that release instead')
            publish_pointer=None
            if not present or present.get('release_id')!=pid or recipe['track']['id'] in catalogue.get('withdrawn',[]):
                row=self.eligible(gid)
                _,source=self.prep.source(gid,artifact_id=recipe['options'].get('master_id'))
                current=self.prep.editorial(gid,recipe['options'].get('master_id'))
                if digest(source)!=recipe['source_sha256'] or row['yaml_sha256']!=recipe['yaml_sha256'] or any(current[k]!=v for k,v in recipe['editorial'].items()):
                    raise ValueError('Audio, YAML or editorial approval changed; prepare a fresh preview')
                origin=urlparse(settings['website_url']);origin=origin.scheme+'://'+origin.netloc
                for index,item in enumerate(recipe['files']):
                    self.progress(identifier,phase='uploading',completed=index,total=len(recipe['files']),message=Path(item['path']).name)
                    store.upload(item);store.public_check(settings['public_base']+'/'+item['key'],origin)
                self.eligible(gid)
                catalogue['tracks']=[t for t in catalogue['tracks'] if t.get('id')!=recipe['track']['id']]+[recipe['track']]
                catalogue['withdrawn']=[t for t in catalogue.get('withdrawn',[]) if t!=recipe['track']['id']]
                catalogue['generated_at']=now();raw=encoded(catalogue)
                snapshot=pointer.rsplit('/',1)[0]+'/catalogues/'+hashlib.sha256(raw).hexdigest()+'.json'
                try:store.compare_put(snapshot,raw,immutable=True)
                except Exception:
                    saved,_=store.read(snapshot)
                    if saved!=raw:raise
                # One compare-and-swap prevents concurrent publishers losing tracks.
                # An unknown response is reconciled by the next explicit retry.
                publish_pointer=lambda:store.compare_put(pointer,raw,etag=etag)
            remove_other(store,recipe['track'],visibility,other_track.get('release_id') if other_track else None)
            self._record_published(identifier,pid,gid,recipe,settings,publish_pointer)
        except Exception as exc:
            self.db.execute("UPDATE processing_tasks SET status='failed',error=?,completed_at=? WHERE id=?",(error_text(exc),now(),identifier))

    def _record_published(self,identifier,pid,gid,recipe,settings,publish_pointer=None):
        visibility=recipe.get('options',{}).get('visibility','public')
        progress={'phase':'published','url':release_url(settings,recipe['track'],visibility),'visibility':visibility,
                  'catalogue_url':settings['public_base']+'/'+destination(visibility)[0]}
        if recipe.get('showcase'):
            progress.update(showcase=recipe['showcase']['folder'],cleanup_pending=recipe['showcase']['cleanup_pending'])
        with self.db.transaction() as c:
            old=c.execute('SELECT status FROM publishing_selection WHERE generation_id=?',(gid,)).fetchone()
            if not old or old[0] not in {'selected','published'}:raise ValueError('Distribution selection changed; publication was not approved')
            if publish_pointer:publish_pointer()
            from .release_catalogue import share_token
            url=progress['url']
            c.execute("UPDATE publishing_selection SET status='published',published_at=?,external_reference=?,updated_at=? WHERE generation_id=?",(now(),url,now(),gid))
            c.execute("INSERT INTO publishing_history(generation_id,old_status,new_status,external_reference,created_at) VALUES(?,?,'published',?,?)",(gid,old[0] if old else None,url,now()))
            for item in recipe['files']:
                # Keep the one local showcase file as its canonical artifact URI;
                # the already-recorded public key is its verified R2 location.
                if not recipe.get('showcase'):
                    c.execute("UPDATE generation_artifacts SET uri=? WHERE kind='release_asset' AND json_extract(metadata_json,'$.processing_task_id')=? AND sha256=?",('r2://'+settings['bucket']+'/'+item['key'],pid,item['sha256']))
            c.execute("UPDATE processing_tasks SET status='done',completed_at=?,telemetry_json=? WHERE id=?",(now(),canonical(progress),identifier))
        if recipe.get('showcase'):
            # Local Showcase is the completed bundle, not a disposable cache.
            # R2 contains only approved public assets, never its private YAML,
            # stems, reports, review reasons or intermediate working files.
            return
        # Compatibility for historical publication receipts.
        cleanup_pending=[]
        for item in recipe['files']:
            path=Path(item['path'])
            try:
                if path.is_file() and digest(path)==item['sha256']:path.unlink()
            except OSError:cleanup_pending.append(path.name)
        directory=Path(recipe['files'][0]['path']).parent
        try:directory.rmdir()
        except OSError:pass
        if cleanup_pending:self.progress(identifier,phase='published',url=url,cleanup_pending=cleanup_pending)

    def discard(self,pid):
        self.prep.require()
        task=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='release_prepare' AND status IN ('done','failed')",(pid,))
        if not task:raise ValueError('Choose a finished preview')
        if self.db.one("SELECT 1 FROM processing_tasks WHERE kind='release_publish' AND status IN ('queued','running') AND json_extract(recipe_json,'$.prepared_id')=?",(pid,)):
            raise ValueError('Wait for active publication before discarding its preview')
        recipe=json.loads(task['recipe_json'])
        if self.retained_preview(pid,task['generation_id'],recipe):
            raise ValueError('Completed showcase assets are retained; withdraw the website release instead')
        for item in recipe.get('files',[]):
            path=Path(item['path'])
            if path.is_file():
                if digest(path)!=item['sha256']:raise ValueError('Preview file changed; retained it')
                path.unlink()
        if recipe.get('files'):
            try:Path(recipe['files'][0]['path']).parent.rmdir()
            except OSError:pass
        self.db.execute("UPDATE processing_tasks SET status='cancelled' WHERE id=?",(pid,))
        return {'discarded':True}

    def start_withdraw(self,pid):
        self.prep.require()
        prepared=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='release_prepare'",(pid,))
        if not prepared:raise ValueError('Release unavailable')
        recipe=json.loads(prepared['recipe_json']);settings=self.settings();store=self.store()
        if recipe.get('destination')!={k:settings[k] for k in recipe.get('destination',{})}:raise ValueError('Use the original publishing destination to withdraw this release')
        with self.lock:
            if self.db.one("SELECT 1 FROM processing_tasks WHERE generation_id=? AND kind='release_publish' AND status IN ('queued','running')",(prepared['generation_id'],)):
                raise ValueError('Wait for active publication before withdrawing')
            visibility=recipe.get('options',{}).get('visibility','public')
            catalogues=[(v,*read_catalogue(store,v)) for v in (visibility,'public' if visibility=='hidden' else 'hidden')]
            for _,catalogue,_ in catalogues:
                target=next((t for t in catalogue['tracks'] if t.get('id')==recipe['track']['id']),None)
                if target and target.get('release_id')!=pid:raise ValueError('A newer release replaced this preview; withdraw that release instead')
            for placement,catalogue,etag in catalogues:
                if placement!=visibility and not any(t.get('id')==recipe['track']['id'] for t in catalogue['tracks']):continue
                catalogue['tracks']=[t for t in catalogue['tracks'] if t.get('id')!=recipe['track']['id']]
                catalogue['withdrawn']=list(dict.fromkeys([*catalogue.get('withdrawn',[]),recipe['track']['id']]))
                # Fence an uncertain write as well as an already-visible entry.
                catalogue['generated_at']=now();catalogue['revision']=uuid.uuid4().hex
                store.compare_put(destination(placement)[0],encoded(catalogue),etag=etag)
            self.review.publishing(prepared['generation_id'],'withdrawn')
            return {'withdrawn':True,'media_retained':True}

    def asset(self,pid,index):
        self.prep.require()
        row=self.db.one("SELECT recipe_json FROM processing_tasks WHERE id=? AND kind='release_prepare' AND status='done'",(pid,))
        if not row:raise KeyError('Release unavailable')
        files=json.loads(row['recipe_json']).get('files',[])
        if not 0<=index<len(files):raise KeyError('Release asset unavailable')
        item=files[index];path=Path(item['path'])
        if not any(path.resolve().is_relative_to(p.resolve()) for p in (self.prep.tracks,self.prep.showcase)) or not path.is_file() or digest(path)!=item['sha256']:raise KeyError('Release asset unavailable')
        return path,item['content_type']
