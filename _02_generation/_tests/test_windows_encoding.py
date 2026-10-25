"""Exercise the Windows cp1252 failure on any host, using real UTF-8 job data."""
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

import pytest
import test_app_generation
import test_execution
from test_comfy_execution import server
from _gbr_generation.executor import execute, cleanup_outputs
from _gbr_generation.planner import json_text

WORDS='Mōses · Mulțumesc · 日本語'


@contextmanager
def windows_text_default():
    """Path.read_text/write_text obtain their implicit encoding through Path.open."""
    original=Path.open
    def cp1252(path,mode='r',buffering=-1,encoding=None,errors=None,newline=None):
        if 'b' not in mode and encoding is None:encoding='cp1252'
        return original(path,mode,buffering,encoding,errors,newline)
    with patch.object(Path,'open',cp1252):yield


def test_job_dashboard_and_resume_read_utf8_records_under_windows_default():
    f=test_app_generation.GenerationAppTests('runTest');f.setUp()
    try:
        preview=f.preview();f.generation.start(preview['token'])
        folder=f.generation.runtime/'app_jobs'/('c'*32);folder.mkdir(parents=True)
        task={**f.launched[-1],'description':WORDS}
        raw=json_text(task).encode('utf-8');(folder/'task.json').write_bytes(raw)
        (folder/'state.json').write_bytes(json_text({'status':'failed','error':WORDS}).encode('utf-8'))
        with pytest.raises(UnicodeDecodeError):raw.decode('cp1252')
        with windows_text_default():
            assert f.generation.task(folder.name)[1]['description']==WORDS
            listed=f.generation.tasks()
            assert len(listed)==1 and listed[0]['error']==WORDS
            f.generation.action(folder.name,'resume')
        assert f.launched[-1]['description']==WORDS and f.launched[-1]['resume'] is True
        assert (folder/'task.json').read_bytes()==raw
        assert f.source.read_bytes()==f.original
    finally:f.doCleanups()


def test_comfy_generation_and_cleanup_preserve_unicode_and_never_resubmit():
    f=test_execution.ExecutionTests('runTest');f.setUp()
    try:
        f.original=f.original.replace(b'A little light across the room',WORDS.encode('utf-8'))
        f.source.write_bytes(f.original)
        with server() as (address,state):
            f.spec['batch']={'sampler':'euler','scheduler':'simple','text_encoder':'bf16','dit':'fp32'}
            f.spec['execution']={'conditioning':{'backend':'comfyui','address':address},'diffusion':{'backend':'comfyui','address':address},'encoding_cache':'prefer'}
            f.spec.pop('model_revision',None);manifest=f.prepare()
            with windows_text_default():
                result=execute(manifest)
                assert result['status']=='succeeded',result
                assert state['acked']
                assert not execute(manifest)['attempts']
                cleanup_outputs(manifest)
            assert state['submissions']==1 and f.source.read_bytes()==f.original
            output=Path(result['attempts'][0]['output'])
            assert (output/'source.yaml').read_bytes()==f.original
            completed=next(p for p in output.glob('*.yaml') if p.name!='source.yaml').read_bytes()
            assert WORDS.encode('utf-8') in completed and b'\r\n' in completed
    finally:f.doCleanups()


def test_native_child_uses_utf8_when_host_environment_requests_cp1252(monkeypatch):
    f=test_execution.ExecutionTests('runTest');f.setUp()
    try:
        worker=f.root/'unicode-worker';worker.mkdir()
        (worker/'worker.py').write_text('import sys\nprint('+repr(WORDS)+',flush=True)\nprint("UTF8="+str(sys.flags.utf8_mode),file=sys.stderr,flush=True)\nraise SystemExit(1)\n',encoding='utf-8')
        manifest=f.prepare()
        monkeypatch.setenv('PYTHONUTF8','0');monkeypatch.setenv('PYTHONIOENCODING','cp1252')
        with patch('_gbr_generation.executor.WORKER',worker):result=execute(manifest,worker_python=sys.executable)
        assert result['status']=='failed'  # Deliberately no inference or fabricated success.
        log=(Path(result['attempts'][0]['diagnostics'])/'stderr.log').read_text(encoding='utf-8')
        assert WORDS in log and 'UTF8=1' in log and 'UnicodeEncodeError' not in log
    finally:f.doCleanups()
