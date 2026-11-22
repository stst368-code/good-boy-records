"""Bounded-memory edit-list rendering shared by previews and full masters."""
from pathlib import Path
import hashlib
import math
import shutil
import subprocess
from .project_model import spans,warp,sound_positions

BLOCK=65536


def checksum(path):
    result=hashlib.sha256()
    with open(path,'rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):result.update(block)
    return result.hexdigest()


def music_blocks(source,segment,rate,limit=None):
    import numpy as np
    origin=round(segment['at']*rate)
    size=round(segment['until']*rate)-origin
    count=min(size,max(0,limit-origin)) if limit is not None else size
    if segment['kind']=='gap':
        for at in range(0,count,BLOCK):yield np.zeros((min(BLOCK,count-at),source.channels),dtype='float32')
        return
    gain=10**(segment.get('gain_db',0)/20)
    if segment.get('mode')=='mute':
        for at in range(0,count,BLOCK):yield np.zeros((min(BLOCK,count-at),source.channels),dtype='float32')
        return
    pieces=warp(segment)
    first_frame=max(0,round(segment['start']*rate))
    last_frame=min(source.frames-1,math.ceil(segment['end']*rate)-1)
    for left,right,begin,end in pieces:
        first,last=round((segment['at']+left)*rate)-origin,round((segment['at']+right)*rate)-origin
        until=min(last,count)
        for at in range(first,until,BLOCK):
            frames=np.arange(at,min(at+BLOCK,until))
            positions=(begin+((frames+origin)/rate-segment['at']-left)/(right-left)*(end-begin))*rate
            if end<begin:positions-=1
            positions=np.clip(positions,first_frame,last_frame)
            low=max(0,int(np.floor(positions.min())));high=min(source.frames,int(np.ceil(positions.max()))+2)
            source.seek(low);data=source.read(high-low,dtype='float32',always_2d=True)
            base=np.floor(positions).astype('int64')-low
            fraction=(positions-np.floor(positions))[:,None]
            block=data[base]*(1-fraction)+data[np.minimum(base+1,len(data)-1)]*fraction
            if segment.get('mode') not in {'normal','mute'}:
                edge_start,edge_end=(first,last) if segment.get('mode')=='stutter' else (0,size)
                edge=np.minimum(1,np.minimum(frames-edge_start,edge_end-1-frames)/max(1,round(rate*.002)))
                block*=np.maximum(0,edge[:,None])
            yield (block*gain).astype('float32')


def section_effects(path,segment,request,index):
    effects=[effect for effect in segment.get('effects',[]) if effect['strength'] and not effect.get('bypassed')]
    if not effects:return path
    import soundfile as sf
    from .mastering import PRESETS,effect_graph
    ffmpeg=shutil.which('ffmpeg')
    folder=Path(request['output']).parent/('section-'+str(index));folder.mkdir()
    info=sf.info(path)
    # afir has two option names across supported FFmpeg versions.
    gain=None
    if any('room' in PRESETS[e['preset']] for e in effects):
        options=subprocess.run([ffmpeg,'-hide_banner','-h','filter=afir'],capture_output=True,text=True,check=True).stdout
        gain='irnorm=-1' if 'irnorm' in options else 'gtype=none'
    output,filters,impulses=effect_graph(effects,'0:a',info,folder,1,gain,segment.get('effect_offset',0))
    command=[ffmpeg,'-nostdin','-v','error','-i',str(path)]
    for impulse in impulses:command+=['-i',str(impulse)]
    target=folder/'processed.wav'
    command+=['-filter_complex',';'.join(filters),'-map','['+output+']','-t',str(info.duration),
              '-c:a','pcm_f32le',str(target)]
    process=subprocess.run(command,capture_output=True,timeout=180)
    if process.returncode:raise RuntimeError('Section effect failed: '+process.stderr.decode('utf-8',errors='replace')[-1200:])
    return target


def assemble(request):
    import numpy as np
    import soundfile as sf
    project=request['recipe']['song_project']
    folder=Path(request['output']).parent
    music=folder/'arrangement.wav'
    info=sf.info(request['source']);rate=info.samplerate
    if info.channels not in (1,2):raise ValueError('Song timeline supports mono or stereo music')
    if request.get('stems'):
        stem_mix=folder/'source-stem-mix.wav';mix=project['mix'];stems=request['stems']
        graph=f"[0:a]volume={mix['vocal_db']}dB[v];[1:a]volume={mix['accompaniment_db']}dB[a];[v][a]amix=inputs=2:normalize=0:duration=longest,apad,atrim=end_sample={info.frames}[out]"
        process=subprocess.run([shutil.which('ffmpeg'),'-nostdin','-v','error','-i',stems['vocals'],'-i',stems['accompaniment'],
            '-filter_complex',graph,'-map','[out]','-ar',str(rate),'-ac',str(info.channels),'-c:a','pcm_f32le',str(stem_mix)],
            capture_output=True,timeout=180)
        if process.returncode:raise ValueError('Separated stem mix failed')
        request={**request,'source':str(stem_mix)}
    timeline=spans(project)
    # Keep the entire lead-in for causal filter/automation state, plus one
    # second of lookahead for FFT convolution and limiter buffering. Never
    # assemble the unused rest of a song just to audition a short range.
    limit=round((request['recipe']['start']+request['recipe']['seconds']+1)*rate) if request['kind']=='mix_preview' else None
    with sf.SoundFile(request['source']) as source,sf.SoundFile(music,'w',samplerate=rate,channels=info.channels,subtype='FLOAT') as target:
        for index,segment in enumerate(timeline):
            if limit is not None and round(segment['at']*rate)>=limit:break
            if segment.get('effects'):
                path=folder/('music-'+str(index)+'.wav')
                with sf.SoundFile(path,'w',samplerate=rate,channels=info.channels,subtype='FLOAT') as clip:
                    for block in music_blocks(source,segment,rate,limit):clip.write(block)
                effected=section_effects(path,segment,request,index)
                count=round(segment['until']*rate)-round(segment['at']*rate);written=0
                if limit is not None:count=min(count,limit-round(segment['at']*rate))
                for block in sf.blocks(effected,blocksize=BLOCK,dtype='float32',always_2d=True):
                    block=block[:count-written];target.write(block);written+=len(block)
                    if written==count:break
                if written<count:target.write(np.zeros((count-written,info.channels),dtype='float32'))
            else:
                for block in music_blocks(source,segment,rate,limit):target.write(block)
    if not project['sounds']:return music
    events=[event for event in sound_positions(project) if limit is None or event['position']*rate<limit]
    if not events:return music
    handles={}
    try:
        for clip_id in {e['clip_id'] for e in events}:
            item=request['recipe']['clips'][clip_id]
            if checksum(item['path'])!=item['sha256']:raise ValueError('Sound clip changed before rendering')
            path=Path(item['path']);clip_info=sf.info(path)
            if clip_info.samplerate!=rate or clip_info.channels!=info.channels:
                target=folder/(clip_id+'.wav')
                command=[shutil.which('ffmpeg'),'-nostdin','-v','error','-i',str(path)]
                if clip_info.channels==2 and info.channels==1:command+=['-af','pan=mono|c0=0.5*c0+0.5*c1']
                process=subprocess.run(command+['-ar',str(rate),'-ac',str(info.channels),'-c:a','pcm_f32le',str(target)],capture_output=True,timeout=60)
                if process.returncode:raise ValueError('Cannot resample a sound clip')
                path=target
            handles[clip_id]=sf.SoundFile(path)
        output=folder/'song-with-sounds.wav'
        at=0
        with sf.SoundFile(output,'w',samplerate=rate,channels=info.channels,subtype='FLOAT') as target:
            for block in sf.blocks(music,blocksize=BLOCK,dtype='float32',always_2d=True):
                additions=np.zeros_like(block)
                for event in events:
                    start=round(event['position']*rate);count=round(event['duration']*rate)
                    low,high=max(start,at),min(start+count,at+len(block))
                    if high<=low:continue
                    offset=low-start;size=high-low
                    clip=handles[event['clip_id']];clip.seek(round(event['trim']*rate)+offset)
                    data=clip.read(size,dtype='float32',always_2d=True)
                    if len(data)!=size:raise ValueError('Sound clip ended before its saved trim')
                    times=(np.arange(size)+offset)/rate
                    envelope=np.ones(size)
                    if event['fade_in']:envelope*=np.minimum(1,times/event['fade_in'])
                    if event['fade_out']:envelope*=np.minimum(1,np.maximum(0,(event['duration']-times)/event['fade_out']))
                    section=slice(low-at,high-at)
                    if event.get('duck_db'):
                        block[section]*=(1+(10**(event['duck_db']/20)-1)*envelope[:,None])
                    data*=envelope[:,None]*10**(event['gain_db']/20)
                    if info.channels==2:
                        data[:,0]*=1-max(0,event.get('pan',0))
                        data[:,1]*=1+min(0,event.get('pan',0))
                    additions[section]+=data
                target.write(block+additions);at+=len(block)
        for clip_id in handles:
            item=request['recipe']['clips'][clip_id]
            if checksum(item['path'])!=item['sha256']:raise ValueError('Sound clip changed while rendering')
        return output
    finally:
        for handle in handles.values():handle.close()


def sidecars(request,audio,features):
    """All published sidecars refer to the final rendered audio's checksum."""
    import json
    from .project_model import projected_words,raw_lyrics,spans
    from .editorial import lighting
    project=request['recipe']['song_project'];sha=checksum(audio)
    words=projected_words(project)
    import soundfile as sf
    final_duration=sf.info(audio).duration
    words=[{**word,'start':max(0,min(final_duration,word['start'])),'end':min(final_duration,word['end'])} for word in words]
    words=[word for word in words if word['end']>word['start']]
    payload={'format':'gbr-lyrics-v1','source_sha256':sha,'language':project['language'],
             'words':words,'raw_lyrics':raw_lyrics(words),'approved':project['approved'],
             'song_project_id':request['recipe']['song_project_id']}
    output=Path(request['output']).parent/'song-lyrics.json'
    output.write_text(json.dumps(payload,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    outputs=[{'output':str(output),'artifact_kind':'project_lyrics'}]
    data=json.loads(Path(features['output']).read_text(encoding='utf-8'))
    offsets={s['id']:s['at'] for s in spans(project)}
    sections=[]
    for cue in project['lighting']['cues']:
        at=offsets[cue['segment_id']]+cue['offset'] if cue.get('segment_id') else cue['at']
        end=min(data['duration'],at+cue['duration'])
        if end>at:sections.append({'start':at,'end':end,'boost':cue['boost'],'label':cue['label']})
    lights=lighting({'sensitivity':project['lighting']['sensitivity'],'sections':sections},data)
    light_path=output.with_name('song-lighting.json')
    light_path.write_text(json.dumps(lights,allow_nan=False),encoding='utf-8')
    outputs.extend([{'output':features['output'],'artifact_kind':'project_visuals'},
                    {'output':str(light_path),'artifact_kind':'project_lighting'}])
    return outputs
