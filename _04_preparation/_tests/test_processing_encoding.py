"""UTF-8 paths and lyric/feature text cross worker pipes without data loss."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from unittest.mock import patch

import numpy as np
import soundfile as sf

from test_preparation import ROOT, prepared, promote, wait
from _gbr_preparation.service import digest

WORDS='Mōses · Mulțumesc · 日本語'


def test_cpu_worker_accepts_utf8_json_even_with_cp1252_stdio(tmp_path):
    source=tmp_path/(WORDS+'.flac');sf.write(source,np.zeros(1600),16000,subtype='PCM_24')
    request={'kind':'visuals','source':str(source),'source_sha256':digest(source),'duration':.1,
             'recipe':{},'output':str(tmp_path/(WORDS+'-output'))}
    child=subprocess.run([sys.executable,str(ROOT/'_99_tools/_workers_processing/worker.py')],
        input=json.dumps(request,ensure_ascii=False).encode('utf-8'),capture_output=True,
        env={**os.environ,'PYTHONUTF8':'0','PYTHONIOENCODING':'cp1252'},timeout=20)
    assert child.returncode==0,child.stderr.decode('utf-8',errors='replace')
    result=json.loads(child.stdout.decode('utf-8'))
    assert WORDS in result['output'] and Path(result['output']).is_file()
    assert json.loads(Path(result['output']).read_text(encoding='utf-8'))['source_sha256']==digest(source)


def test_preparation_roundtrips_unicode_worker_protocol_and_sidecars(prepared,monkeypatch):
    review,prep,_,_,gid,raw=prepared
    prep.tracks=prep.root/('_tracks_'+WORDS);review.archiver.preparation_root=prep.tracks
    source=promote(prepared)
    worker=prep.root/'utf8-worker.py'
    worker.write_text('''import json,sys
from pathlib import Path
request=json.load(sys.stdin)
assert sys.flags.utf8_mode == 1
output=Path(request['output']).with_suffix('.json')
data={'format':'gbr-playback-v1','source_sha256':request['source_sha256'],'frames':[],'note':NOTE}
output.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
print(json.dumps({'output':str(output),'artifact_kind':'visual_features','software':{'fixture':'1'},'measurements':{'note':NOTE}},ensure_ascii=False))
'''.replace('NOTE',repr(WORDS)),encoding='utf-8')
    prep.worker=worker
    monkeypatch.setenv('PYTHONUTF8','0');monkeypatch.setenv('PYTHONIOENCODING','cp1252')
    original=Path.open
    def cp1252(path,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
        if 'b' not in mode and encoding is None:encoding='cp1252'
        return original(path,mode,buffering,encoding,errors,newline)
    with patch.object(Path,'open',cp1252):
        task=prep.submit(gid,'visuals',{});row=wait(prep,task['id'])
        assert json.loads(row['telemetry_json'])['measurements']['note']==WORDS
        assert prep.editorial(gid)['features']['note']==WORDS
    assert next(source.parent.glob('*.yaml')).read_bytes()==raw
    assert not review.db.all('PRAGMA foreign_key_check')


def test_invalid_utf8_worker_data_fails_without_replacing_characters(prepared):
    review,prep,_,_,gid,raw=prepared;source=promote(prepared)
    worker=prep.root/'invalid-worker.py'
    worker.write_text("import sys\nsys.stdout.buffer.write(b'{\"note\":\"\\x8d\"}')\n",encoding='utf-8');prep.worker=worker
    task=prep.submit(gid,'visuals',{});deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        row=review.db.one('SELECT * FROM processing_tasks WHERE id=?',(task['id'],))
        if row['status'] in {'done','failed'}:break
        time.sleep(.02)
    assert row['status']=='failed' and 'utf-8' in row['error']
    assert not review.db.one("SELECT 1 FROM generation_artifacts WHERE json_extract(metadata_json,'$.processing_task_id')=?",(task['id'],))
    assert next(source.parent.glob('*.yaml')).read_bytes()==raw
