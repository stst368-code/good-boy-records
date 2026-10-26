"""Published recordings, editable release revisions and rooms on the shared DB."""
import copy
import json
import math
import re
import uuid
import sys
from pathlib import Path

from .service import canonical,now
from .release_catalogue import destination,release_url

DEFAULTS={'revision':0,'sensitivity':1,'display_order':'genre','default_room':'public',
          'include_flac':True,'timed_lyrics':True,'rooms':[
              {'id':'public','name':'Main player','entry_track_id':None,'order':0,'enabled':True},
              {'id':'easter','name':'Easter eggs','entry_track_id':None,'order':1,'enabled':True},
              {'id':'hidden','name':'Secret room','entry_track_id':None,'order':2,'enabled':True}]}
PUBLIC_POINTER='gbr/v2/library.json'


class Library:
    def __init__(self,publisher):
        self.publisher=publisher;self.prep=publisher.prep;self.db=publisher.db
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_yaml_editor'))

    def settings(self):
        if not self.prep.enabled:return copy.deepcopy(DEFAULTS)
        row=self.db.one("SELECT value_json FROM platform_settings WHERE key='library'")
        if row:return json.loads(row['value_json'])
        defaults=copy.deepcopy(DEFAULTS)
        previous=self.db.one("SELECT details_json FROM review_history WHERE action='easter_egg_configured' ORDER BY id DESC LIMIT 1")
        if previous:
            trigger=json.loads(previous['details_json'])
            if trigger.get('enabled'):defaults['rooms'][2]['entry_track_id']=trigger.get('track_id')
        return defaults

    def room_ids(self):return [r['id'] for r in self.settings()['rooms']]

    def room(self,identifier):
        room=next((r for r in self.settings()['rooms'] if r['id']==identifier and r['enabled']),None)
        if not room:raise ValueError('Choose an enabled Library room')
        return room

    def save_settings(self,values):
        self.prep.require()
        if not isinstance(values,dict) or set(values)-set(DEFAULTS):raise ValueError('Unknown Library setting')
        with self.prep.lock,self.db.transaction() as c:
            current=self.settings()
            if values.get('revision')!=current['revision']:raise ValueError('Library settings changed; reload before saving')
            result={**current,**values};rooms=result['rooms']
            if not isinstance(rooms,list) or not 2<=len(rooms)<=100:raise ValueError('Use 2–100 rooms')
            ids=set();entries=set()
            covers={t['id'] for t in self.covers()}
            for room in rooms:
                if not isinstance(room,dict) or set(room)-{'id','name','entry_track_id','order','enabled'}:raise ValueError('Invalid room')
                identifier=room.get('id');destination(identifier)
                if identifier in ids:raise ValueError('Room IDs must be unique')
                ids.add(identifier)
                if not isinstance(room.get('name'),str) or not 1<=len(room['name'].strip())<=100:raise ValueError('Give each room a short name')
                if type(room.get('order')) is not int or type(room.get('enabled')) is not bool:raise ValueError('Invalid room order or availability')
                entry=room.get('entry_track_id')
                if entry:
                    if entry not in covers:raise ValueError('Choose a published cover for the room entrance')
                    if entry in entries:raise ValueError('A cover can open only one room')
                    entries.add(entry)
                if identifier=='public' and (entry or not room['enabled']):raise ValueError('The main player must stay enabled without a cover entrance')
            if not {'public','easter'}<=ids or set(self.room_ids())-ids:raise ValueError('Disable existing rooms instead of removing their IDs and history')
            if result['default_room'] not in {r['id'] for r in rooms if r['enabled']}:raise ValueError('Choose an enabled default room')
            if isinstance(result['sensitivity'],bool) or not isinstance(result['sensitivity'],(float,int)) or not math.isfinite(result['sensitivity']) or not .25<=result['sensitivity']<=3:raise ValueError('Sensitivity must be 0.25–3')
            if result['display_order'] not in {'genre','title','album','newest','manual'}:raise ValueError('Choose a display order')
            if type(result['include_flac']) is not bool or type(result['timed_lyrics']) is not bool:raise ValueError('Invalid publishing defaults')
            result['revision']+=1
            c.execute("INSERT INTO platform_settings VALUES('library',?,?) ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json,updated_at=excluded.updated_at",(canonical(result),now()))
        return self.configuration()

    def covers(self):
        tracks={}
        path=self.publisher.root/'_96_website/_data/catalogue.json'
        if path.is_file():
            for track in json.loads(path.read_text(encoding='utf-8-sig')).get('tracks',[]):tracks[track['id']]=track
        for item in self.projects()['items']:
            if item['visibility']=='public':tracks[item['track']['id']]=item['track']
            else:tracks.pop(item['track']['id'],None)
        return [{'id':t['id'],'title':t.get('displayTitle') or t['id'],'variant':t.get('variant',''),'artwork_url':t.get('artwork_url')}
                for t in tracks.values() if t.get('artwork_url')]

    def configuration(self):
        genres={}
        import csv
        path=self.publisher.root/'_01_writing/_references/styles.csv'
        if path.is_file():
            with path.open(encoding='utf-8-sig',newline='') as stream:
                for row in csv.DictReader(stream):
                    cluster=row.get('cluster');style=row.get('style_primary') or row.get('style')
                    if cluster and style:genres.setdefault(cluster,set()).add(style)
        for item in self.projects()['items']:
            genre=item['track'].get('genre',{});cluster=genre.get('parent_genre');style=genre.get('child_genre')
            if cluster and style:genres.setdefault(cluster,set()).add(style)
        return {'settings':self.settings(),'covers':self.covers(),'genres':{k:sorted(v) for k,v in genres.items()},
                'legacy_directory':r'C:\Users\Simon\OneDrive\porto\good-boy-records\showcase',
                'public_settings_url':self.publisher.settings()['public_base']+'/'+PUBLIC_POINTER}

    def sync_settings(self):
        from .publishing import encoded
        settings=self.settings();p=self.publisher;store=p.store()
        # No credentials, filesystem paths or private operational settings ship.
        payload={'format':'gbr-library-v1',**settings}
        raw=encoded(payload);_,etag=store.read(PUBLIC_POINTER)
        store.compare_put(PUBLIC_POINTER,raw,etag=etag)
        self.db.execute("INSERT INTO review_history(action,details_json,created_at) VALUES('library_settings_published',?,?)",(canonical({'revision':settings['revision']}),now()))
        return {'published':True,'revision':settings['revision']}

    def record(self,gid):
        row=self.db.one("SELECT * FROM publishing_selection WHERE generation_id=? AND status='published'",(gid,))
        if not row:raise ValueError('Choose a published Library recording')
        tasks=self.db.all("SELECT id,recipe_json,telemetry_json,created_at FROM processing_tasks WHERE generation_id=? AND kind='release_publish' AND status='done' ORDER BY rowid DESC",(gid,))
        for task in tasks:
            request=json.loads(task['recipe_json']);pid=request.get('prepared_id')
            prepared=self.db.one("SELECT * FROM processing_tasks WHERE id=? AND kind='release_prepare' AND status='done'",(pid,))
            if prepared:
                recipe=json.loads(prepared['recipe_json'])
                source=self.db.one('SELECT a.archive_path FROM audio a JOIN generations g ON g.audio_id=a.id WHERE g.id=?',(gid,))
                return {'generation_id':gid,'prepared_id':pid,'publication_id':task['id'],'track':recipe['track'],
                        'master_id':recipe.get('options',{}).get('master_id'),
                        'lossy_source':Path(source['archive_path'] or '').suffix.lower()=='.mp3',
                        'visibility':request.get('visibility') or recipe.get('options',{}).get('visibility','public'),
                        'published_at':task['created_at'],'recipe':recipe}
        raise ValueError('This historical publication needs the one-time showcase import before editing')

    def projects(self):
        if not self.prep.enabled:return {'items':[]}
        items=[]
        for row in self.db.all("SELECT generation_id FROM publishing_selection WHERE status='published' ORDER BY published_at DESC"):
            try:
                item=self.record(row['generation_id']);item.pop('recipe');items.append(item)
            except ValueError:continue
        return {'items':items}

    def draft(self,gid):
        row=self.db.one("SELECT id,recipe_json FROM processing_tasks WHERE generation_id=? AND kind='library_edit' AND status='done' ORDER BY rowid DESC LIMIT 1",(gid,))
        return {'id':row['id'],'changes':json.loads(row['recipe_json'])['changes']} if row else {'id':None,'changes':{}}

    def detail(self,gid):
        item=self.record(gid);recipe=item.pop('recipe');item['draft']=self.draft(gid)
        item['draft']['applied']=not item['draft']['id'] or recipe.get('options',{}).get('library_draft')==item['draft']['id']
        item['editorial']=self.prep.editorial(gid,item.get('master_id'))
        item['work']=self.db.all("SELECT id,kind,status,error,telemetry_json,recipe_json FROM processing_tasks WHERE generation_id=? AND kind IN ('release_prepare','release_publish') ORDER BY rowid DESC LIMIT 8",(gid,))
        item['artworks']=self.db.all("SELECT id,kind FROM generation_artifacts WHERE kind IN ('cover_artwork','album_artwork') AND json_extract(metadata_json,'$.source_generation_id')=?",(gid,))
        return item

    def save(self,gid,body):
        record=self.record(gid);changes=body.get('changes',{})
        allowed={'title','cluster','style','language','explicit','lyrics','album_name','album_position','display_order'}
        if not isinstance(changes,dict) or set(changes)-allowed:raise ValueError('Unknown release metadata field')
        for name,value in changes.items():
            if name=='explicit':
                if value not in {'yes','no',None}:raise ValueError('Explicit must be yes or no')
            elif name in {'display_order','album_position'}:
                if value is not None and (type(value) is not int or not 0<=value<=100000):raise ValueError('Use a nonnegative integer for '+name)
            elif not isinstance(value,str) or len(value)>(50000 if name=='lyrics' else 2000):raise ValueError('Invalid '+name)
        if 'title' in changes and not changes['title'].strip():raise ValueError('Title cannot be empty')
        if changes.get('language') and not re.fullmatch('[a-z]{2,3}',changes['language']):raise ValueError('Use a language code such as en')
        with self.prep.lock:
            self.prep.editable(gid);current=self.draft(gid)
            if body.get('parent_id')!=current['id']:raise ValueError('Library draft changed; reload before saving')
            if not changes:return self.detail(gid)
            merged={**current['changes'],**changes};identifier=uuid.uuid4().hex
            # Validate and render before committing a draft or replacing lyric
            # sidecars; a malformed historical YAML must not half-save changes.
            from .library_yaml import update
            raw=self.prep.metadata.read(gid)['_raw_bytes']
            updated=update(raw or b'title: Unknown\n',merged,self.publisher.root)
            self.db.execute("INSERT INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at) VALUES(?,?,'library_edit','done',?,?,?,?,?)",(identifier,gid,identifier,canonical({'parent_id':current['id'],'changes':merged}),self.prep.source(gid)[0]['audio_id'],now(),now()))
            if 'lyrics' in changes and changes['lyrics']!=current['changes'].get('lyrics',record['track'].get('lyrics',{}).get('raw','')):
                self.prep.replace_lyrics(gid,changes['lyrics'],changes.get('language'))
            self.write_yaml(gid,updated,identifier)
        return self.detail(gid)

    def write_yaml(self,gid,updated,identifier):
        path=self.prep.bundle(gid)/'_derived'/('library-'+identifier+'.yaml');path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(updated)
        from .service import digest
        self.db.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',(identifier+'-yaml','library_yaml',digest(path),str(path),len(updated),canonical({'processing_task_id':identifier,'source_generation_id':gid})))

    def prepare_revision(self,gid,body):
        current=self.record(gid);draft=self.draft(gid);options=current['recipe'].get('options',{})
        master=body.get('master_id',options.get('master_id'))
        return self.publisher.start_prepare(gid,{**options,'complete_publish':body.get('publish',True),'finish_preparation':False,
            'reuse_release_id':current['prepared_id'],'library_draft':draft['id'],
            'master_id':master,'visibility':body.get('room') or body.get('visibility') or current['visibility'],
            'include_flac':body.get('include_flac',options.get('include_flac',True)),
            'artwork_id':body.get('artwork_id',options.get('artwork_id')),
            'timed_lyrics':bool(self.prep.editorial(gid,master)['approved'])})
