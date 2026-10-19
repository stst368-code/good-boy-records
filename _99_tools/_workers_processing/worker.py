"""CPU preparation worker. GPU alignment/image environments remain independent."""
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys


def render(request):
    import numpy as np
    import soundfile as sf
    ffmpeg=shutil.which('ffmpeg')
    if not ffmpeg:raise RuntimeError('FFmpeg is required for previews and master candidates')
    version=subprocess.run([ffmpeg,'-version'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    recipe=request['recipe'];mix=recipe['mix'];duration=float(request['duration'])
    output=Path(request['output']).with_suffix('.wav' if request['kind']=='mix_preview' else '.flac')
    filters=[]
    if mix['gain_db']:filters.append(f"volume={mix['gain_db']}dB")
    if mix['bass_db']:filters.append(f"bass=g={mix['bass_db']}")
    if mix['mid_db']:filters.append(f"equalizer=f=1000:t=q:w=1:g={mix['mid_db']}")
    if mix['treble_db']:filters.append(f"treble=g={mix['treble_db']}")
    command=[ffmpeg,'-hide_banner','-v','error','-nostdin','-n','-i',request['source']]
    input_label='0:a';prefix=''
    if request.get('stems'):
        command+=['-i',request['stems']['vocals'],'-i',request['stems']['accompaniment']]
        prefix=f"[1:a]volume={mix['vocal_db']}dB[v];[2:a]volume={mix['accompaniment_db']}dB[m];[v][m]amix=inputs=2:normalize=0[stem_mix];";input_label='stem_mix'
    graph=prefix+'['+input_label+']'+','.join(filters or ['anull'])+'[tone];'
    if mix['reverb']:
        rate=44100
        t=np.arange(int(rate*mix['room_seconds']))/rate
        impulse=np.random.default_rng(0).standard_normal(len(t))*np.exp(-6.9*t/mix['room_seconds'])*mix['reverb']/math.sqrt(rate*.1)
        impulse[:min(len(t),int(.02*rate))]=0
        impulse[0]=1
        ir=output.parent/'room-ir.wav';sf.write(ir,impulse,rate,subtype='FLOAT')
        command+=['-i',str(ir)]
        help_text=subprocess.run([ffmpeg,'-hide_banner','-h','filter=afir'],capture_output=True,text=True,check=True).stdout
        normalise='irnorm=-1' if 'irnorm' in help_text else 'gtype=none'
        graph+=f'[tone][{3 if request.get("stems") else 1}:a]afir=dry=1:wet=1:irgain=1:irfmt=mono:{normalise}[room];'
        source='room'
    else:source='tone'
    final=[]
    if mix['limiter_db'] is not None:
        final.append(f"alimiter=limit={10**(mix['limiter_db']/20)}:level=0:latency=1")
    if mix['fade_in']:final.append(f"afade=t=in:st=0:d={min(duration,mix['fade_in'])}")
    if mix['fade_out']:final.append(f"afade=t=out:st={max(0,duration-mix['fade_out'])}:d={min(duration,mix['fade_out'])}")
    # Fades use whole-recording time before audition trimming; preview and master share a chain.
    if request['kind']=='mix_preview':final+=['atrim=start='+str(recipe['start'])+':duration='+str(recipe['seconds']),'asetpts=PTS-STARTPTS']
    else:final.append('atrim=end_sample='+str(round(duration*sf.info(request['stems']['vocals']).samplerate) if request.get('stems') else sf.info(request['source']).frames))
    graph+=f'[{source}]'+','.join(final or ['anull'])+'[out]'
    command+=['-filter_complex',graph,'-map','[out]','-map_metadata','-1','-c:a',
              'pcm_s24le' if request['kind']=='mix_preview' else 'flac']
    if request['kind']=='mix_master':command+=['-sample_fmt','s32','-bits_per_raw_sample','24']
    command+=[str(output)]
    bypass=request['kind']=='mix_master' and not request.get('stems') and not any(mix[key] for key in ['gain_db','bass_db','mid_db','treble_db','reverb','fade_in','fade_out']) and mix['limiter_db'] is None
    if bypass:
        # Keep bit depth/container bytes for a true bypass. This temporary hard
        # link is removed with the task directory after the source is reused.
        try:os.link(request['source'],output)
        except OSError:shutil.copyfile(request['source'],output)
    else:
        process=subprocess.run(command,capture_output=True,text=True,timeout=550)
        if process.returncode:raise RuntimeError(process.stderr[-5000:])
    info=sf.info(output)
    peak=0.;squares=0.;samples=0
    for block in sf.blocks(output,blocksize=65536,dtype='float64',always_2d=True):
        if not np.isfinite(block).all():raise ValueError('Rendered audio contains invalid samples')
        peak=max(peak,float(np.max(np.abs(block))));squares+=float(np.sum(block**2));samples+=block.size
    return {'output':str(output),'artifact_kind':'preview_audio' if request['kind']=='mix_preview' else 'master_candidate',
            'software':{'ffmpeg':version if not bypass else None,'processing_engine':'bypass' if bypass else 'ffmpeg','numpy':np.__version__,'soundfile':sf.__version__},
            'measurements':{'duration':info.duration,'sample_rate':info.samplerate,'channels':info.channels,'subtype':info.subtype,
                'sample_peak_dbfs':20*math.log10(peak) if peak>0 else None,
                'rms_dbfs':10*math.log10(squares/samples) if squares>0 and samples else None,
                'limiter_kind':'sample_peak' if mix['limiter_db'] is not None else None}}


def visuals(request):
    import numpy as np
    import soundfile as sf
    info=sf.info(request['source']);step=max(1,info.samplerate//10)
    rows=[]
    for index,block in enumerate(sf.blocks(request['source'],blocksize=step,dtype='float32',always_2d=True)):
        spectrum=(np.abs(np.fft.rfft(block*np.hanning(len(block))[:,None],axis=0))**2).mean(axis=1)
        frequencies=np.fft.rfftfreq(len(block),1/info.samplerate);total=float(spectrum.sum())
        rows.append({'t':round(index*step/info.samplerate,4),'rms':float(np.sqrt(np.mean(block.astype('float64')**2))),
                     'bass':float(spectrum[frequencies<250].sum()/total) if total else 0,
                     'brightness':float(spectrum[frequencies>3000].sum()/total) if total else 0})
    max_rms=max((r['rms'] for r in rows),default=0)
    for row in rows:row['energy']=row.pop('rms')/max_rms if max_rms else 0
    output=Path(request['output']).with_suffix('.json')
    events=[]
    previous=0.;last=-10.
    for row in rows:
        if row['energy']>.65 and row['energy']-previous>.12 and row['t']-last>.45:
            events.append({'time':row['t'],'strength':row['energy'],'duration':.8});last=row['t']
        previous=row['energy']
    output.write_text(json.dumps({'format':'gbr-playback-v1','fps':10,'duration':info.duration,
        'source_sha256':request['source_sha256'],'feature_algorithm':'channel-rms-power-v2','frames':rows,'dance':{'events':events,'algorithm':'rms-rise-v1'},'limitations':['Energy/frequency measurements, not beat detection or musical-quality scoring.']},allow_nan=False),encoding='utf-8')
    return {'output':str(output),'artifact_kind':'visual_features',
            'software':{'numpy':np.__version__,'soundfile':sf.__version__},'measurements':{'frames':len(rows)}}


def main():
    if sys.argv[1:]==['--doctor']:
        import importlib.util
        modules={name:importlib.util.find_spec(name) is not None for name in ['numpy','soundfile','demucs','whisperx']}
        print(json.dumps({'ffmpeg':shutil.which('ffmpeg'),'modules':modules,
            'ready_adapters':(['mix_preview','mix_master','visuals'] if all(modules[n] for n in ['numpy','soundfile']) and shutil.which('ffmpeg') else [])+(['lyric_alignment'] if modules['demucs'] and modules['whisperx'] and shutil.which('ffmpeg') else []),
            'pending_adapters':(['lyric_alignment'] if not (modules['demucs'] and modules['whisperx']) else [])+['image_diffusion']}))
        return
    request=json.load(sys.stdin)
    if request['kind'] in {'mix_preview','mix_master'}:result=render(request)
    elif request['kind']=='visuals':result=visuals(request)
    else:raise ValueError('Unsupported preparation worker operation')
    print(json.dumps(result,allow_nan=False))


if __name__=='__main__':
    main()
