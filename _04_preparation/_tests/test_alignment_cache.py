"""Windows cache regressions without loading Torch, WhisperX or model weights."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace

import pytest


ROOT=Path(__file__).resolve().parents[2]


@pytest.fixture
def alignment():
    spec=importlib.util.spec_from_file_location('alignment_cache_worker',ROOT/'_99_tools/_workers_processing/alignment.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


@pytest.fixture
def whisper_models(monkeypatch):
    module=ModuleType('whisperx.alignment')
    module.DEFAULT_ALIGN_MODELS_TORCH={'en':'TORCH_EN','fr':'TORCH_FR'}
    module.DEFAULT_ALIGN_MODELS_HF={'ru':'alignment/russian','ja':'alignment/japanese','fr':'unused/french'}
    monkeypatch.setitem(sys.modules,'whisperx',ModuleType('whisperx'))
    monkeypatch.setitem(sys.modules,'whisperx.alignment',module)


@pytest.mark.parametrize('language,repositories',[
    ('en',['Systran/faster-whisper-large-v3']),
    ('ru',['Systran/faster-whisper-large-v3','alignment/russian']),
    ('ja',['Systran/faster-whisper-large-v3','alignment/japanese']),
    ('fr',['Systran/faster-whisper-large-v3']),
    ('xx',['Systran/faster-whisper-large-v3']),
])
def test_windows_probes_exact_asr_and_language_cache_before_downloads(alignment,whisper_models,tmp_path,monkeypatch,language,repositories):
    cache=tmp_path/'custom-hub-песня';calls=[]
    hub=ModuleType('huggingface_hub');hub.constants=SimpleNamespace(HF_HUB_CACHE=str(cache))
    download=ModuleType('huggingface_hub.file_download')
    def folder(*,repo_id,repo_type):
        assert repo_type=='model'
        return 'models--'+repo_id.replace('/','--')
    download.repo_folder_name=folder
    download.are_symlinks_supported=lambda path:calls.append(path) or False
    monkeypatch.setitem(sys.modules,'huggingface_hub',hub)
    monkeypatch.setitem(sys.modules,'huggingface_hub.file_download',download)
    monkeypatch.setattr(sys,'platform','win32')

    alignment.prepare_model_cache(language)

    assert calls==[cache/('models--'+repo.replace('/','--')) for repo in repositories]
    assert alignment.ASR_MODEL==repositories[0]


def test_linux_does_not_import_or_change_optional_model_cache(alignment,monkeypatch):
    monkeypatch.setattr(sys,'platform','linux')
    monkeypatch.setitem(sys.modules,'huggingface_hub',None)
    monkeypatch.setitem(sys.modules,'whisperx',None)
    alignment.prepare_model_cache('ru')


def test_cache_write_failures_are_not_hidden(alignment,whisper_models,tmp_path,monkeypatch):
    hub=ModuleType('huggingface_hub');hub.constants=SimpleNamespace(HF_HUB_CACHE=str(tmp_path))
    download=ModuleType('huggingface_hub.file_download')
    download.repo_folder_name=lambda **kwargs:'models--asr'
    def denied(path):raise PermissionError('Cache directory is not writable')
    download.are_symlinks_supported=denied
    monkeypatch.setitem(sys.modules,'huggingface_hub',hub)
    monkeypatch.setitem(sys.modules,'huggingface_hub.file_download',download)
    monkeypatch.setattr(sys,'platform','win32')
    with pytest.raises(PermissionError,match='not writable'):alignment.prepare_model_cache('en')


def test_real_legacy_hub_parallel_cache_uses_fallback_and_reuses_existing_blobs(alignment,whisper_models,tmp_path,monkeypatch):
    # Optional in the light app suite; also run against Hub 0.36.0, the older
    # WhisperX-compatible release exhibiting the actual Windows race.
    hub=pytest.importorskip('huggingface_hub')
    from huggingface_hub import file_download
    monkeypatch.setattr(hub.constants,'HF_HUB_CACHE',str(tmp_path))
    monkeypatch.setattr(hub.constants,'HF_HUB_DISABLE_SYMLINKS_WARNING',False)
    monkeypatch.setattr(sys,'platform','win32')
    probes=[]
    def unprivileged(source,destination,*args,**kwargs):
        probes.append((source,destination))
        error=OSError('A required privilege is not held by the client');error.winerror=1314
        raise error
    monkeypatch.setattr(file_download.os,'symlink',unprivileged)
    with pytest.warns(UserWarning,match='does not support them'):
        alignment.prepare_model_cache('ru')
    assert len(probes)==2

    files=[]
    for repo in [alignment.ASR_MODEL,'alignment/russian']:
        cache=tmp_path/file_download.repo_folder_name(repo_id=repo,repo_type='model')
        blobs=cache/'blobs';blobs.mkdir(exist_ok=True)
        snapshot=cache/'snapshots'/('a'*40);snapshot.mkdir(parents=True,exist_ok=True)
        for index in range(6):
            blob=blobs/str(index);data=f'{repo}: {index}'.encode('utf-8');blob.write_bytes(data)
            # A failed earlier download can leave an already-complete blob.
            files.append((blob,snapshot/f'{index}.bin',data,index!=0))
    def materialise(item):
        blob,target,data,new=item
        file_download._create_symlink(str(blob),str(target),new_blob=new)
        assert target.read_bytes()==data and not target.is_symlink()
        assert blob.exists()==(not new)
    with ThreadPoolExecutor(max_workers=8) as pool:list(pool.map(materialise,files))
    assert len(probes)==2  # Download threads never attempt privileged symlinks.

    # A subsequent load can use the same snapshot without deleting its cache.
    for _,target,data,_ in files:assert target.read_bytes()==data
