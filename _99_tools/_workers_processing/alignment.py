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
    import torch
    if sys.platform=='win32':
        import os
        dll_search=os.add_dll_directory(str(Path(torch.__file__).parent/'lib'))
    import whisperx
    from demucs.apply import apply_model
    from demucs.audio import AudioFile
    from demucs.pretrained import get_model
    import soundfile as sf
    device='cuda' if torch.cuda.is_available() else 'cpu';out=Path(request['output']).parent
    model=get_model('htdemucs');model.to(device);model.eval()
    audio=AudioFile(request['source']).read(streams=0,samplerate=model.samplerate,channels=model.audio_channels)
    reference=audio.mean(0);mean=reference.mean();std=reference.std()
    with torch.inference_mode():stems=apply_model(model,((audio-mean)/(std+1e-8))[None].to(device),device=device,shifts=0,split=True,overlap=.25)[0].cpu()*(std+1e-8)+mean
    vocals=stems[model.sources.index('vocals')];accompaniment=stems.sum(0)-vocals
    artifacts=[]
    for name,wave in [('vocals',vocals),('accompaniment',accompaniment)]:
        path=out/(name+'.wav');sf.write(path,wave.T.numpy(),model.samplerate,subtype='FLOAT');artifacts.append({'output':str(path),'artifact_kind':'stem_'+name})
    del model,stems,audio;gc.collect()
    if device=='cuda':torch.cuda.empty_cache()
    speech=whisperx.load_audio(str(out/'vocals.wav'));language=request['recipe']['language']
    asr=whisperx.load_model('large-v3',device,compute_type='float16' if device=='cuda' else 'int8',language=language,vad_method='silero')
    transcript=asr.transcribe(speech,batch_size=4,language=language)
    del asr;gc.collect()
    if device=='cuda':torch.cuda.empty_cache()
    aligner,metadata=whisperx.load_align_model(language_code=language,device=device)
    aligned=whisperx.align(transcript['segments'],aligner,metadata,speech,device,return_char_alignments=False)
    words,unmatched=match_words(request['recipe']['words'],aligned.get('word_segments',[]))
    data={'format':'gbr-lyrics-v1','source_sha256':request['source_sha256'],'language':language,'words':words,
          'unmatched_recognition':unmatched,'method':'WhisperX ASR word alignment matched to supplied YAML tokens; missing tokens stay untimed',
          'approved':False}
    path=out/'lyrics.json';path.write_text(json.dumps(data,ensure_ascii=False,allow_nan=False),encoding='utf-8')
    return {'output':str(path),'artifact_kind':'lyric_alignment','additional_outputs':artifacts,
        'software':{name:importlib.metadata.version(name) for name in ['torch','demucs','whisperx']},
        'hardware':{'gpu_name':torch.cuda.get_device_name() if device=='cuda' else None,'usable_vram_bytes':torch.cuda.get_device_properties(0).total_memory if device=='cuda' else None,'gpu_uuid':str(getattr(torch.cuda.get_device_properties(0),'uuid','')) or None if device=='cuda' else None},
        'measurements':{'device':device,'matched_words':sum(w['start'] is not None for w in words),'total_words':len(words)}}


if __name__=='__main__':
    request=json.load(sys.stdin)
    import torch
    from contextlib import nullcontext
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'_workers_infdit'))
    from _gbr_worker.hardware import discover,gpu_lock
    hardware=discover(torch,torch.device('cuda:0')) if torch.cuda.is_available() else {}
    lock=gpu_lock(hardware['gpu_uuid'] or hardware['address']) if hardware else nullcontext()
    from contextlib import redirect_stdout
    from _gbr_worker.telemetry import Journal
    with lock,redirect_stdout(sys.stderr):
        journal=Journal(Path(request['output']).parent/'resources.ndjson',torch=torch if hardware else None,device=torch.device('cuda:0') if hardware else None,hardware=hardware,interval_ms=100)
        try:
            with journal.span('separate_and_align',gpu=bool(hardware)):result=run(request)
        finally:journal.close()
        records=[json.loads(line) for line in (Path(request['output']).parent/'resources.ndjson').read_text().splitlines()]
        measured=next(row for row in reversed(records) if row['event']=='stage_end')
        result['measurements']['resources']=measured['resource']
        if hardware:result['hardware']=hardware
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))
