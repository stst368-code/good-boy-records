"""Optional isolated Demucs/WhisperX worker; imports GPU libraries only on execution."""
import difflib
import gc
import importlib.metadata
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import time


ASR_MODEL='Systran/faster-whisper-large-v3'


def report(request,phase,message,**values):
    if not request.get('progress_file'):return
    data=request.setdefault('_progress',{})
    data.update(phase=phase,message=message,stage_started_at=time.time(),**values)
    path=Path(request['progress_file']);temporary=path.with_suffix('.tmp')
    try:
        temporary.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
        for attempt in range(3):
            try:temporary.replace(path);break
            except PermissionError:time.sleep(.02)
    except OSError:pass  # A status-file lock must never fail a model run.


def prepare_model_cache(language):
    """Finish Windows symlink probes before Hub download threads can race them."""
    if sys.platform!='win32':return
    from huggingface_hub import constants
    from huggingface_hub.file_download import are_symlinks_supported,repo_folder_name
    from whisperx.alignment import DEFAULT_ALIGN_MODELS_HF,DEFAULT_ALIGN_MODELS_TORCH

    # WhisperX 3.8.6 requires Hub <1.0. Its first probe publishes a provisional
    # True, letting another download attempt an unprivileged Windows symlink.
    # Probe each actual repository directory serially so Hub's own move/copy
    # fallback is selected before snapshot_download starts its thread pool.
    repositories=[ASR_MODEL]
    if language not in DEFAULT_ALIGN_MODELS_TORCH and language in DEFAULT_ALIGN_MODELS_HF:
        repositories.append(DEFAULT_ALIGN_MODELS_HF[language])
    for repository in dict.fromkeys(repositories):
        folder=Path(constants.HF_HUB_CACHE)/repo_folder_name(repo_id=repository,repo_type='model')
        are_symlinks_supported(folder)


def match_words(expected, recognised):
    def normal(text):return ''.join(c for c in text.casefold() if c.isalnum())
    a=[normal(w['text']) for w in expected];b=[normal(w.get('word','')) for w in recognised]
    result=[dict(w) for w in expected];used=set()
    for block in difflib.SequenceMatcher(None,a,b,autojunk=False).get_matching_blocks():
        for offset in range(block.size):
            index=block.a+offset;found=recognised[block.b+offset];used.add(block.b+offset)
            start,end,score=found.get('start'),found.get('end'),found.get('score')
            if start is not None and end is not None and end>start:
                result[index].update(start=float(start),end=float(end),confidence=float(score) if score is not None and math.isfinite(float(score)) and 0<=float(score)<=1 else None)
    return result,[word for index,word in enumerate(recognised) if index not in used]


def run(request):
    report(request,'imports','Loading WhisperX and Demucs')
    import torch
    if sys.platform=='win32':
        import os
        dll_search=os.add_dll_directory(str(Path(torch.__file__).parent/'lib'))
    import whisperx
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from demucs.pretrained import get_model
    import soundfile as sf
    language=request['recipe']['language']
    prepare_model_cache(language)
    device='cuda' if torch.cuda.is_available() else 'cpu';out=Path(request['output']).parent
    report(request,'separation_model','Loading / downloading Demucs model',device=device)
    model=get_model('htdemucs');model.to(device);model.eval()
    report(request,'separation','Separating vocals and accompaniment')
    audio=AudioFile(request['source']).read(streams=0,samplerate=model.samplerate,channels=model.audio_channels)
    reference=audio.mean(0);mean=reference.mean();std=reference.std()
    with torch.inference_mode():stems=apply_model(model,((audio-mean)/(std+1e-8))[None].to(device),device=device,shifts=0,split=True,overlap=.25)[0].cpu()*(std+1e-8)+mean
    vocals=stems[model.sources.index('vocals')];accompaniment=stems.sum(0)-vocals
    artifacts=[]
    for name,wave in [('vocals',vocals),('accompaniment',accompaniment)]:
        path=out/(name+'.wav');sf.write(path,wave.T.numpy(),model.samplerate,subtype='FLOAT');artifacts.append({'output':str(path),'artifact_kind':'stem_'+name})
    del model,stems,audio;gc.collect()
    if device=='cuda':torch.cuda.empty_cache()
    speech=whisperx.load_audio(str(out/'vocals.wav'))
    report(request,'asr_model','Loading / downloading Whisper large-v3')
    asr=whisperx.load_model(ASR_MODEL,device,compute_type='float16' if device=='cuda' else 'int8',language=language,vad_method='silero')
    report(request,'transcription','Transcribing the separated vocals')
    transcript=asr.transcribe(speech,batch_size=4,language=language)
    del asr;gc.collect()
    if device=='cuda':torch.cuda.empty_cache()
    report(request,'alignment_model','Loading / downloading language alignment model')
    aligner,metadata=whisperx.load_align_model(language_code=language,device=device)
    report(request,'alignment','Aligning recognised words to the audio')
    aligned=whisperx.align(transcript['segments'],aligner,metadata,speech,device,return_char_alignments=False)
    report(request,'matching','Matching audio timings to the supplied lyrics')
    words,unmatched=match_words(request['recipe']['words'],aligned.get('word_segments',[]))
    matched=sum(w['start'] is not None for w in words)
    if words and not matched:raise RuntimeError('WhisperX matched 0 of '+str(len(words))+' supplied words. Check the language and worker log; the visible word positions remain estimates.')
    data={'format':'gbr-lyrics-v1','source_sha256':request['source_sha256'],'language':language,'words':words,
          'unmatched_recognition':unmatched,'method':'WhisperX ASR word alignment matched to supplied YAML tokens; missing tokens stay untimed',
          'approved':False}
    path=out/'lyrics.json';path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return {'output':str(path),'artifact_kind':'lyric_alignment','additional_outputs':artifacts,
        'software':{name:importlib.metadata.version(name) for name in ['torch','demucs','whisperx']},
        'hardware':{'gpu_name':torch.cuda.get_device_name() if device=='cuda' else None,'usable_vram_bytes':torch.cuda.get_device_properties(0).total_memory if device=='cuda' else None,'gpu_uuid':str(getattr(torch.cuda.get_device_properties(0),'uuid','')) or None if device=='cuda' else None},
        'measurements':{'device':device,'matched_words':matched,'total_words':len(words)}}


if __name__=='__main__':
    request=json.loads(sys.stdin.buffer.read().decode('utf-8'))
    report(request,'starting','Loading PyTorch and checking the local processing device')
    import torch
    from contextlib import nullcontext
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_workers_infdit'))
    from _gbr_worker.hardware import discover,gpu_lock
    hardware=discover(torch,torch.device('cuda:0')) if torch.cuda.is_available() else {}
    report(request,'device','Waiting to acquire the local processing device',device='cuda' if hardware else 'cpu',gpu_name=hardware.get('gpu_name'),vram_bytes=hardware.get('usable_vram_bytes'))
    lock=gpu_lock(hardware['gpu_uuid'] or hardware['address']) if hardware else nullcontext()
    from contextlib import redirect_stdout
    from _gbr_worker.telemetry import Journal
    with lock,redirect_stdout(sys.stderr):
        journal=Journal(Path(request['output']).parent/'resources.ndjson',torch=torch if hardware else None,device=torch.device('cuda:0') if hardware else None,hardware=hardware,interval_ms=100)
        try:
            with journal.span('separate_and_align',gpu=bool(hardware)):result=run(request)
        finally:journal.close()
        records=[json.loads(line) for line in (Path(request['output']).parent/'resources.ndjson').read_text(encoding="utf-8").splitlines()]
        measured=next(row for row in reversed(records) if row['event']=='stage_end')
        result['measurements']['resources']=measured['resource']
        if hardware:result['hardware']=hardware
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
