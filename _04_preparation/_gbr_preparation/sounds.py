"""An appendable, content-addressed sample library; imports never retain upload paths."""
from pathlib import Path
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import threading

MAX_UPLOAD = 32*1024**2
MAX_SECONDS = 120
BUILTIN_VERSION = 2
BUILTIN_LABELS={'horn':('Air horn','Stabs'),'lift':('Needle lift','Vinyl'),'drop':('Needle drop','Vinyl'),'crackle':('Vinyl surface','Textures'),'impact':('Deep impact','Stabs'),'riser':('Noise riser','Transitions')}
RECORDED = {
    'horn': ('guitarguy1985','https://freesound.org/people/guitarguy1985/sounds/68999/'),
    'lift': ('Stratocube','https://freesound.org/people/Stratocube/sounds/456280/'),
    'drop': ('HenKonen','https://freesound.org/people/HenKonen/sounds/682161/'),
}


class SoundLibrary:
    def __init__(self, preparation):
        self.prep=preparation
        self.root=preparation.root/'_04_preparation/_references/_sounds'
        self.lock=threading.RLock()
        self.seeded=False

    def storage(self):
        if self.root.is_symlink():raise ValueError('Sound library cannot be a symbolic link')
        self.root.mkdir(parents=True,exist_ok=True)
        if not self.root.resolve().is_relative_to(self.prep.root.resolve()):
            raise ValueError('Sound library escaped GBR storage')
        return self.root

    def register(self,path,name,category='Imported',builtin=False):
        from .service import digest,canonical,now
        import soundfile as sf
        import numpy as np
        info=sf.info(path)
        if not 0<info.duration<=MAX_SECONDS+.001:raise ValueError('Sound clips must be between 0 and 120 seconds')
        sha=digest(path);identifier='sound-'+sha
        target=self.storage()/(sha+'.wav')
        if target.is_symlink():raise ValueError('Invalid sound library file')
        if target.exists():
            if digest(target)!=sha:raise ValueError('Sound library checksum mismatch')
        else:shutil.copyfile(path,target)
        peaks=[]
        for block in sf.blocks(target,blocksize=max(1,math.ceil(info.frames/160)),dtype='float32',always_2d=True):
            if not np.isfinite(block).all():raise ValueError('Sound clip contains invalid samples')
            peaks.append(float(np.max(np.abs(block))))
        meta={'name':str(name).strip()[:160] or 'Untitled sound','category':str(category).strip()[:80] or 'Imported',
              'duration':info.duration,'sample_rate':info.samplerate,'channels':info.channels,
              'imported_at':now(),'builtin':builtin,'peaks':peaks}
        self.prep.db.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
            (identifier,'sound_clip',sha,str(target),target.stat().st_size,canonical(meta)))
        return self.item(identifier)

    def seed(self):
        """Bundled CC0 horn/needle recordings and three original textures."""
        with self.lock:
            if self.seeded:return
            import numpy as np
            import soundfile as sf
            rate=44100
            for key,name,category,seconds in [
                ('horn','Air horn','Stabs',1.4),('lift','Needle lift','Vinyl',.32),
                ('drop','Needle drop','Vinyl',.38),('crackle','Vinyl surface','Textures',4),
                ('impact','Deep impact','Stabs',1.8),('riser','Noise riser','Transitions',3)]:
                existing=self.prep.db.one("SELECT id,uri FROM generation_artifacts WHERE kind='sound_clip' AND json_extract(metadata_json,'$.builtin_key')=? AND json_extract(metadata_json,'$.builtin_version')=?",(key,BUILTIN_VERSION))
                if existing and Path(existing['uri']).is_file():continue
                if key in RECORDED:
                    source=Path(__file__).resolve().parents[1]/'_references/_builtin_sounds'/(key+'.mp3')
                    wave,rate=sf.read(source,dtype='float32',always_2d=True)
                    active=np.flatnonzero(np.max(np.abs(wave),axis=1)>10**(-52/20))
                    if len(active):wave=wave[max(0,active[0]-round(rate*.01)):min(len(wave),active[-1]+round(rate*.03))]
                    fade=min(len(wave),round(rate*.003));wave[:fade]*=np.linspace(0,1,fade)[:,None]
                    with tempfile.TemporaryDirectory(prefix='.seed-',dir=self.storage()) as temp:
                        path=Path(temp)/'clip.wav';sf.write(path,wave,rate,subtype='PCM_24')
                        item=self.register(path,name,category,True)
                    metadata=self.item(item['id'])['_metadata']
                    metadata.update(builtin_key=key,builtin_version=BUILTIN_VERSION,author=RECORDED[key][0],
                                    source_url=RECORDED[key][1],license='CC0-1.0')
                    from .service import canonical
                    self.prep.db.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?',(canonical(metadata),item['id']))
                    continue
                t=np.arange(round(rate*seconds))/rate
                rng=np.random.default_rng(218+len(key));noise=rng.standard_normal(len(t))
                if key=='crackle':
                    wave=noise*.012
                    for index in rng.integers(0,len(t)-128,50):
                        wave[index:index+128]+=rng.uniform(-.6,.6)*np.exp(-np.arange(128)/12)
                elif key=='impact':
                    wave=np.sin(2*np.pi*(42*t+24*(1-np.exp(-t*9))/9))*np.exp(-t*3)
                    wave+=noise*np.exp(-t*20)*.15
                else:
                    wave=np.diff(noise,prepend=0)*.2*(t/seconds)**1.5
                    wave+=np.sin(2*np.pi*(120*t+900*t*t/seconds))*.08*(t/seconds)
                wave=wave/max(1,float(np.max(np.abs(wave))))*.7
                wave*=np.minimum(1,(seconds-t)/.012)
                with tempfile.TemporaryDirectory(prefix='.seed-',dir=self.storage()) as temp:
                    path=Path(temp)/'clip.wav'
                    sf.write(path,np.column_stack([wave,wave]),rate,subtype='PCM_24')
                    item=self.register(path,name,category,True)
                metadata=self.item(item['id'])['_metadata'];metadata.update(builtin_key=key,builtin_version=BUILTIN_VERSION)
                from .service import canonical
                self.prep.db.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?',(canonical(metadata),item['id']))
            self.seeded=True

    def item(self,identifier):
        row=self.prep.db.one("SELECT * FROM generation_artifacts WHERE id=? AND kind='sound_clip'",(identifier,))
        if not row:raise KeyError('Sound clip not found')
        meta=json.loads(row['metadata_json'])
        return {'id':row['id'],'sha256':row['sha256'],**{k:v for k,v in meta.items() if k!='builtin_key'},
                '_metadata':meta,'_path':row['uri']}

    @staticmethod
    def public(item):
        return {**{k:v for k,v in item.items() if not k.startswith('_')},
                'preset':item.get('_metadata',{}).get('builtin_key')}

    def list(self):
        self.prep.require();self.seed()
        items=[self.item(row['id']) for row in self.prep.db.all("SELECT id FROM generation_artifacts WHERE kind='sound_clip' ORDER BY rowid DESC")]
        visible=[]
        for item in items:
            legacy=item.get('builtin') and item.get('builtin_version')!=BUILTIN_VERSION
            if legacy and (item['name'],item['category'])==BUILTIN_LABELS.get(item['_metadata'].get('builtin_key')):continue
            public=self.public(item)
            if legacy:public['preset']=None
            visible.append(public)
        return {'sounds':visible,
            'max_seconds':MAX_SECONDS,'max_upload_bytes':MAX_UPLOAD}

    def audio(self,identifier):
        item=self.item(identifier);path=Path(item['_path'])
        if path.is_symlink() or not path.resolve().is_relative_to(self.storage().resolve()) or not path.is_file():
            for saved in self.prep.db.all("SELECT id FROM generation_artifacts WHERE kind='song_clip' AND sha256=? AND json_extract(metadata_json,'$.clip_id')=?",(item['sha256'],identifier)):
                try:
                    restored=self.prep.artifact(saved['id'])
                    from .service import digest
                    if digest(restored)==item['sha256']:return restored
                except (FileNotFoundError,ValueError):continue
            raise FileNotFoundError('Sound clip unavailable')
        return path

    def import_audio(self,raw,name,category='Imported'):
        self.prep.require()
        if not raw or len(raw)>MAX_UPLOAD:raise ValueError('Import an audio clip up to 32 MiB')
        ffmpeg=shutil.which('ffmpeg')
        if not ffmpeg:raise ValueError('FFmpeg is required to import sound clips')
        with self.lock,tempfile.TemporaryDirectory(prefix='.import-',dir=self.storage()) as temp:
            source=Path(temp)/'upload';source.write_bytes(raw);target=Path(temp)/'clip.wav'
            process=subprocess.run([ffmpeg,'-nostdin','-v','error','-i',str(source),'-map','0:a:0','-vn',
                '-t',str(MAX_SECONDS+.1),'-ac','2','-ar','44100','-c:a','pcm_s24le',str(target)],
                capture_output=True,timeout=60)
            if process.returncode:raise ValueError('Cannot decode this audio file; try WAV, MP3, FLAC, OGG or M4A')
            return self.public(self.register(target,name,category))

    def rename(self,identifier,name,category):
        from .service import canonical
        with self.lock:
            item=self.item(identifier);meta=item['_metadata']
            meta.update(name=str(name).strip()[:160] or item['name'],category=str(category).strip()[:80] or 'Imported')
            self.prep.db.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?',(canonical(meta),identifier))
            return self.public(self.item(identifier))

    def freeze(self,gid,clip_ids):
        """A recording carries its used samples through archive moves."""
        from .service import canonical,digest
        folder=self.prep.bundle(gid)/'_sounds'
        if folder.is_symlink():raise ValueError('Invalid project sound directory')
        folder.mkdir(parents=True,exist_ok=True)
        manifest={}
        for identifier in sorted(set(clip_ids)):
            item=self.item(identifier);source=self.audio(identifier)
            if digest(source)!=item['sha256']:raise ValueError('Sound clip changed; import a new copy')
            target=folder/(item['sha256']+'.wav')
            if target.is_symlink():raise ValueError('Invalid saved sound clip')
            if not target.exists():
                try:os.link(source,target)
                except OSError:shutil.copyfile(source,target)
            if digest(target)!=item['sha256']:raise ValueError('Saved sound clip checksum mismatch')
            self.prep.db.execute('INSERT OR IGNORE INTO generation_artifacts VALUES(?,NULL,?,?,?,?,NULL,?)',
                ('song-sound-'+gid+'-'+item['sha256'],'song_clip',item['sha256'],str(target),target.stat().st_size,
                 canonical({'source_generation_id':gid,'clip_id':identifier})))
            manifest[identifier]={'sha256':item['sha256'],'path':str(target),'duration':item['duration']}
        return manifest
