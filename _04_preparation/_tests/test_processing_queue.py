"""Queue admission, stale worker records, recovery and real subprocess progress."""
import json
import os
from pathlib import Path
import sys
import time

from test_preparation import prepared,promote,wait


def test_missing_environment_is_reported_as_setup_not_gpu_overload(prepared):
    _,prep,_,_,_,_=prepared;promote(prepared)
    status=prep.processing.status()
    assert not status['environment_ready'] and status['pending']==1
    assert 'setup-processing' in status['items'][0]['reason']
    assert 'busy' not in status['items'][0]['reason']


def test_stale_generation_records_do_not_block_but_live_worker_locks_do(prepared,monkeypatch):
    review,prep,_,_,_,_=prepared
    original=review.db.all
    rows=[{'address':'http://127.0.0.1:8190','gpu_uuid':'TEST-ALIGN-GPU','execution_mode':'comfyui'}]
    monkeypatch.setattr(review.db,'all',lambda sql,args=():rows if 'SELECT DISTINCT w.address' in sql else original(sql,args))
    assert not prep.local_generation_busy()
    from _gbr_worker.hardware import gpu_lock
    with gpu_lock('gbr-controller:comfyui:http://127.0.0.1:8190'):
        assert prep.local_generation_busy()
    assert not prep.local_generation_busy()
    with gpu_lock('TEST-ALIGN-GPU'):assert prep.local_generation_busy()


def test_ten_alignment_requests_admit_one_and_remain_visible_in_order(prepared,monkeypatch):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    monkeypatch.setattr(prep,'local_generation_busy',lambda:False)
    submitted=[];monkeypatch.setattr(prep.pool,'submit',lambda fn,identifier:submitted.append(identifier))
    requests=[prep.submit(gid,'lyric_alignment',{'language':lang}) for lang in ['en','de','fr','es','it','ja','ko','pl','ro','uk']]
    assert len(submitted)==1 and len({r['id'] for r in requests})==10
    status=prep.processing.status()
    assert status['pending']==10 and len(status['items'])==10
    assert sorted(r['position'] for r in status['items'])==list(range(1,11))
    assert all('behind' in row['reason'] for row in status['items'] if row['status']=='waiting')
    review.db.execute("UPDATE processing_tasks SET status='done' WHERE id=?",(submitted[0],))
    prep.processing.resume()
    assert len(submitted)==2 and submitted[1]==requests[1]['id']


def test_dead_controller_is_visible_as_interrupted_and_live_child_is_preserved(prepared,monkeypatch):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    task=review.db.one("SELECT * FROM processing_tasks WHERE kind='lyric_alignment'")
    owner=prep.owner();owner.update(pid=99999999,created_at=1)
    review.db.execute("UPDATE processing_tasks SET status='running',telemetry_json=? WHERE id=?",(json.dumps({'owner':owner,'child':{'pid':os.getpid(),'created_at':None}}),task['id']))
    prep.processing.recover()
    assert review.db.one('SELECT status FROM processing_tasks WHERE id=?',(task['id'],))['status']=='running'
    review.db.execute('UPDATE processing_tasks SET telemetry_json=? WHERE id=?',(json.dumps({'owner':owner}),task['id']))
    status=prep.processing.status()
    assert status['failed']==1 and status['items'][0]['status']=='interrupted'


def test_alignment_streams_stages_unicode_logs_and_publishes_real_timing_result(prepared,monkeypatch):
    review,prep,_,_,gid,_=prepared;promote(prepared)
    worker=prep.worker.with_name('alignment.py')
    worker.write_text('''import json,sys,time
from pathlib import Path
r=json.loads(sys.stdin.buffer.read());progress=Path(r['progress_file'])
for stage in ['separation','transcription']:
 progress.write_text(json.dumps({'phase':stage,'message':stage,'device':'cpu','stage_started_at':time.time()}),encoding='utf-8')
 print('worker '+stage+' — песня',file=sys.stderr,flush=True);time.sleep(1.2)
words=r['recipe']['words']
for i,w in enumerate(words):w.update(start=i*.2,end=i*.2+.15,confidence=.9)
path=Path(r['output']).with_suffix('.json');path.write_text(json.dumps({'source_sha256':r['source_sha256'],'words':words,'language':'en','approved':False,'method':'test alignment'}))
print(json.dumps({'output':str(path),'artifact_kind':'lyric_alignment','software':{'test':'1'},'measurements':{'device':'cpu','matched_words':len(words),'total_words':len(words)}}))
''',encoding='utf-8')
    monkeypatch.setattr(prep,'alignment_python',lambda:Path(sys.executable))
    task=prep.submit(gid,'lyric_alignment',{'language':'en'});identifier=task['id']
    deadline=time.monotonic()+10;phases=set();seen_log=False
    while time.monotonic()<deadline:
        row=review.db.one('SELECT status,telemetry_json FROM processing_tasks WHERE id=?',(identifier,));progress=json.loads(row['telemetry_json'])
        phases.add(progress.get('phase'));seen_log|='песня' in progress.get('log_tail','')
        if row['status'] in {'done','failed'}:break
        time.sleep(.05)
    result=wait(prep,identifier)
    assert {'separation','transcription'}<=phases and seen_log
    data=json.loads(result['telemetry_json']);assert data['measurements']['matched_words']==4 and data['child']['pid']
    assert 'песня' in data['log_tail'] and data['phase']=='complete'
    editorial=prep.editorial(gid);assert editorial['timed_words']==4 and editorial['timing_source']=='WhisperX alignment'
    assert not list(prep.tracks.rglob('.gbr-processing-*')) and not review.db.all('PRAGMA foreign_key_check')
    assert prep.submit(gid,'lyric_alignment',{'language':'en'})['id']==identifier
    submitted=[];monkeypatch.setattr(prep.pool,'submit',lambda fn,new_id:submitted.append(new_id))
    rerun=prep.submit(gid,'lyric_alignment',{'language':'en','force':True})
    assert rerun['id']!=identifier and submitted==[rerun['id']]
    assert prep.submit(gid,'lyric_alignment',{'language':'en','force':True})['id']==rerun['id']
