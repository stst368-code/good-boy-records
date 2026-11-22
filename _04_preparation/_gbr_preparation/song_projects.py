"""Immutable song project revisions using the existing processing ledger."""
from copy import deepcopy
from pathlib import Path
import hashlib
import json
import uuid
from base64 import b64encode
import zlib
from .project_model import validate, duration


class SongProjects:
    def __init__(self,preparation):
        self.prep=preparation

    def revision(self,gid,identifier=None):
        sql="SELECT * FROM processing_tasks WHERE generation_id=? AND kind='song_project' AND status='done'"
        row=self.prep.db.one(sql+(' AND id=?' if identifier else ' ORDER BY rowid DESC LIMIT 1'),
                             (gid,identifier) if identifier else (gid,))
        if identifier and not row:raise KeyError('Song project revision not found')
        if row:row['recipe']=json.loads(row.pop('recipe_json'))
        return row

    def versions(self,gid):
        return self.prep.db.all("SELECT id,created_at,json_extract(recipe_json,'$.label') label FROM processing_tasks WHERE generation_id=? AND kind='song_project' AND status='done' ORDER BY rowid DESC LIMIT 100",(gid,))

    def get(self,gid,identifier=None,input_artifact_id=None):
        self.prep.require()
        latest=self.revision(gid,identifier)
        if latest:
            recipe=latest['recipe']
            return {'revision_id':latest['id'],'source_sha256':latest['input_sha256'],
                    'source_artifact_id':recipe.get('input_artifact_id'),
                    'source_duration':recipe['source_duration'],'lyric_task_id':recipe.get('lyric_task_id'),
                    'lyrics_sha256':recipe['lyrics_sha256'],'project':recipe['project'],
                    'duration':duration(recipe['project']),'versions':self.versions(gid)}
        current=self.prep.editorial(gid,input_artifact_id)
        from .service import DEFAULT_MIX
        document={'version':1,'segments':[{'id':uuid.uuid4().hex,'kind':'audio','start':0,
                    'end':current['duration'],'mode':'normal','gain_db':0,'effects':[],'effect_offset':0,'label':'Original music'}],
                  'sounds':[],'words':current['words'],'approved':current['approved'],
                  'language':current['language'],'artwork_id':None,'mix':{**DEFAULT_MIX,'limiter_db':-1},
                  'stem_task_id':None,
                  'lighting':{'sensitivity':current['sensitivity'],'cues':[]}}
        return {'revision_id':None,'source_sha256':current['source_sha256'],
                'source_artifact_id':input_artifact_id,'source_duration':current['duration'],
                'lyrics_sha256':current['lyrics_sha256'],'lyric_task_id':current['lyric_task_id'],
                'project':document,'duration':current['duration'],'versions':[]}

    def save(self,gid,data):
        from .service import digest,canonical
        with self.prep.lock:
            self.prep.require();self.prep.editable(gid)
            current=self.revision(gid)
            if data.get('parent_revision_id')!=(current['id'] if current else None):
                raise ValueError('This song was saved elsewhere. Reload its latest version before saving')
            source_id=data.get('input_artifact_id')
            if current and source_id!=current['recipe'].get('input_artifact_id'):
                raise ValueError('A song project keeps its original source. Render it before starting a different arrangement')
            row,source=self.prep.source(gid,artifact_id=source_id)
            if digest(source)!=data.get('source_sha256'):raise ValueError('Source audio changed; reload the song')
            editorial=self.prep.editorial(gid,source_id)
            baseline=current['recipe']['project']['words'] if current else editorial['words']
            document=validate(data.get('project'),row['duration'],baseline)
            if document.get('stem_task_id'):self.prep.stems(gid,document['stem_task_id'],data['source_sha256'])
            art=document.get('artwork_id')
            if art and not self.prep.db.one("SELECT id FROM generation_artifacts WHERE id=? AND kind IN ('album_artwork','cover_artwork') AND json_extract(metadata_json,'$.source_generation_id')=?",(art,gid)):
                raise ValueError('Choose artwork belonging to this recording')
            manifest=self.prep.sounds.freeze(gid,[c['clip_id'] for c in document['sounds']])
            for clip in document['sounds']:
                if clip['trim']+clip['duration']>manifest[clip['clip_id']]['duration']+.001:
                    raise ValueError('Sound trim exceeds the imported clip')
            if current and canonical(document)==canonical(current['recipe']['project']):
                return self.get(gid)
            recipe={'version':1,'project':document,'input_artifact_id':source_id,
                    'source_duration':row['duration'],'parent_revision_id':data.get('parent_revision_id'),
                    'lyrics_sha256':editorial['lyrics_sha256'],'lyric_task_id':editorial['lyric_task_id'],
                    'clip_hashes':{key:value['sha256'] for key,value in manifest.items()},
                    'label':str(data.get('label') or 'Song edit')[:160]}
            identifier=self.prep._insert(gid,'song_project',recipe,data['source_sha256'],'done')
            result=self.get(gid,identifier)
            try:result['reused_master_id']=self.reuse_audio(gid,identifier)
            except (OSError,ValueError,KeyError):
                # The project is already safely saved. A missing cache can be
                # regenerated by the normal render action without losing edits.
                result['reused_master_id']=None
            return result

    @staticmethod
    def audio_key(project,source_sha,clip_hashes):
        from .service import canonical
        music=[{k:v for k,v in section.items() if k!='label'} for section in project['segments']]
        sounds=[{k:v for k,v in clip.items() if k not in {'id','label'}} for clip in project['sounds']]
        return hashlib.sha256(canonical([source_sha,music,sounds,project['mix'],
            project.get('stem_task_id'),clip_hashes]).encode()).hexdigest()

    def reuse_audio(self,gid,revision_id):
        """Rebind new annotations to a verified master without another DSP pass."""
        from .service import canonical,digest,now
        from .project_audio import sidecars
        import tempfile
        saved=self.revision(gid,revision_id);new=saved['recipe'];project=new['project']
        signature=self.audio_key(project,saved['input_sha256'],new.get('clip_hashes',{}))
        candidates=self.prep.db.all("""SELECT t.recipe_json,t.id task_id,a.id,a.sha256
            FROM processing_tasks t JOIN generation_artifacts a
            ON json_extract(a.metadata_json,'$.processing_task_id')=t.id
            WHERE t.generation_id=? AND t.kind='mix_master' AND t.status='done'
            AND a.kind='master_candidate' AND t.input_sha256=?
            ORDER BY t.rowid DESC LIMIT 25""",(gid,saved['input_sha256']))
        for candidate in candidates:
            recipe=json.loads(candidate['recipe_json'])
            if not recipe.get('song_project'):continue
            old_hashes={key:value['sha256'] for key,value in recipe.get('clips',{}).items()}
            if signature!=self.audio_key(recipe['song_project'],saved['input_sha256'],old_hashes):continue
            try:master=self.prep.artifact(candidate['id'])
            except FileNotFoundError:continue
            if digest(master)!=candidate['sha256']:continue
            features=self.prep.db.one("""SELECT * FROM generation_artifacts
                WHERE kind='project_visuals' AND json_extract(metadata_json,'$.processing_task_id')=?""",(candidate['task_id'],))
            if not features or self.prep._editorial_sidecar(gid,features,candidate['sha256']) is None:continue
            features_path=self.prep.artifact(features['id'])
            recipe={**recipe,'song_project':project,'song_project_id':revision_id,
                    'lyrics_sha256':new['lyrics_sha256'],'reused_master_id':candidate['id']}
            destination=self.prep.bundle(gid)/'_derived'
            with tempfile.TemporaryDirectory(prefix='.song-annotations-',dir=destination) as temporary:
                request={'recipe':recipe,'output':str(Path(temporary)/'song')}
                outputs=sidecars(request,master,{'output':str(features_path)})
                published=[('master_candidate',candidate['sha256'],master)]
                for item in outputs:
                    path=Path(item['output']);sha=digest(path);target=destination/(sha+'.json')
                    if path.resolve()!=target.resolve():
                        if target.exists():
                            if digest(target)!=sha:raise ValueError('Song annotation collision')
                        else:shutil_copy(path,target)
                    published.append((item['artifact_kind'],sha,target))
            identifier=self.prep._insert(gid,'mix_master',recipe,saved['input_sha256'],'done')
            with self.prep.db.transaction() as c:
                for index,(kind,sha,path) in enumerate(published):
                    c.execute('INSERT INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                        (identifier+('-output' if index==0 else '-'+str(index)),kind,sha,str(path),path.stat().st_size,
                         canonical({'processing_task_id':identifier,'source_generation_id':gid})))
                c.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',
                    (canonical({'phase':'complete','message':'Existing master reused; annotations updated','audio_reused':True}),identifier))
            task=self.prep.db.one('SELECT * FROM processing_tasks WHERE id=?',(identifier,))
            self.attach_render(task,published)
            return identifier+'-output'
        return None

    def render_recipe(self,gid,identifier):
        from .service import digest
        row=self.revision(gid,identifier);recipe=row['recipe']
        manifest={}
        for clip_id,sha in recipe.get('clip_hashes',{}).items():
            item=self.prep.db.one("SELECT id FROM generation_artifacts WHERE kind='song_clip' AND sha256=? AND json_extract(metadata_json,'$.source_generation_id')=?",(sha,gid))
            if not item:raise ValueError('Saved sound clip is unavailable')
            path=self.prep.artifact(item['id'])
            if digest(path)!=sha:raise ValueError('Saved sound clip bytes changed')
            manifest[clip_id]={'sha256':sha,'path':str(path)}
        return {**deepcopy(recipe),'song_project_id':identifier,'source_sha256':row['input_sha256'],'clips':manifest}

    def attach_render(self,task,published):
        """Make final-audio lyrics/lights visible to the existing release pipeline."""
        from .service import digest,canonical,now
        recipe=json.loads(task['recipe_json'])
        if not recipe.get('song_project_id') or task['kind']!='mix_master':return
        master=next((p for p in published if p[0]=='master_candidate'),None)
        if not master:return
        for artifact_kind,sha,path in published:
            alias={'project_lyrics':'lyric_edit','project_visuals':'visuals','project_lighting':'visual_edit'}.get(artifact_kind)
            if not alias:continue
            identifier=task['id']+'-'+alias
            alias_recipe={'song_project_id':recipe['song_project_id'],'render_task_id':task['id'],
                          'lyrics_sha256':recipe['lyrics_sha256'],'output_sha256':sha}
            metadata={'processing_task_id':identifier,'source_generation_id':task['generation_id']}
            if alias=='lyric_edit':metadata['lyrics_backup_zlib']=b64encode(zlib.compress(path.read_bytes())).decode('ascii')
            fingerprint=hashlib.sha256(canonical([identifier,sha]).encode()).hexdigest()
            with self.prep.db.transaction() as c:
                c.execute('INSERT OR IGNORE INTO processing_tasks(id,generation_id,kind,status,fingerprint,recipe_json,input_sha256,created_at,completed_at) VALUES(?,?,?,?,?,?,?,?,?)',
                    (identifier,task['generation_id'],alias,'done',fingerprint,canonical(alias_recipe),master[1],now(),now()))
                c.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                    (identifier+'-output','visual_features' if alias=='visuals' else alias,sha,str(path),path.stat().st_size,canonical(metadata)))

    def assert_release(self,gid,master_id,artwork_id):
        """Prevent finishing a project with audio from an earlier arrangement."""
        row=self.revision(gid)
        if not row:return
        chosen=self.prep.db.one("SELECT t.recipe_json FROM generation_artifacts a JOIN processing_tasks t ON t.id=json_extract(a.metadata_json,'$.processing_task_id') WHERE a.id=? AND t.generation_id=? AND t.status='done' AND a.kind='master_candidate'",(master_id or '',gid))
        if not chosen or json.loads(chosen['recipe_json']).get('song_project_id')!=row['id']:
            raise ValueError('Render and select a master of the latest saved song timeline before finishing preparation')
        if (artwork_id or None)!=(row['recipe']['project'].get('artwork_id') or None):
            raise ValueError('Save the selected artwork with your song timeline and render its latest version')


def shutil_copy(source,target):
    """Never change an existing content-addressed artifact while rebinding."""
    import os
    import shutil
    import tempfile
    with tempfile.NamedTemporaryFile(dir=target.parent,prefix='.song-',delete=False) as output:
        temporary=Path(output.name)
        try:
            with source.open('rb') as incoming:shutil.copyfileobj(incoming,output)
            output.flush();os.fsync(output.fileno())
        except BaseException:
            output.close();temporary.unlink(missing_ok=True);raise
    try:temporary.replace(target)
    finally:temporary.unlink(missing_ok=True)
