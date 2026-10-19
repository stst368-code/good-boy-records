"""Managed-file cleanup and checkpoint hashing without importing a Comfy server."""
import asyncio
import hashlib
import importlib.util
from pathlib import Path
import sys
import types
import pytest

ROOT=Path(__file__).resolve().parents[2]

@pytest.fixture
def bridge(tmp_path,monkeypatch):
    class HTTPError(Exception):
        def __init__(self,*args,**kwargs):super().__init__(*args)
    web=types.SimpleNamespace(json_response=lambda value:value,HTTPBadRequest=HTTPError,HTTPForbidden=HTTPError,HTTPNotFound=HTTPError,HTTPConflict=HTTPError)
    monkeypatch.setitem(sys.modules,'aiohttp',types.SimpleNamespace(web=web))
    class Routes:
        def get(self,*args):return lambda function:function
        def post(self,*args):return lambda function:function
    monkeypatch.setitem(sys.modules,'server',types.SimpleNamespace(PromptServer=types.SimpleNamespace(instance=types.SimpleNamespace(routes=Routes()))))
    output=tmp_path/'output';output.mkdir();models=tmp_path/'models';models.mkdir()
    monkeypatch.setitem(sys.modules,'folder_paths',types.SimpleNamespace(get_output_directory=lambda:str(output),get_full_path=lambda category,name:str(models/name) if (models/name).is_file() else None,get_folder_paths=lambda category:[str(models)]))
    spec=importlib.util.spec_from_file_location('bridge_under_test',ROOT/'_99_tools/_workers_comfyui/gbr_bridge.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module,output,models,HTTPError


def request(value):
    class Request:
        async def json(self):return value
    return Request()


def test_acknowledgement_deletes_only_exact_verified_owned_output(bridge):
    module,output,models,error=bridge;folder=output/'gbr';folder.mkdir();attempt='a'*32
    owned=folder/(attempt+'_00001_.flac');owned.write_bytes(b'published bytes');other=folder/'unrelated.flac';other.write_bytes(b'keep')
    payload={'attempt_id':attempt,'file':{'filename':owned.name,'subfolder':'gbr','type':'output'},'sha256':hashlib.sha256(owned.read_bytes()).hexdigest()}
    assert asyncio.run(module.acknowledge(request(payload)))['acknowledged']
    assert not owned.exists() and other.read_bytes()==b'keep'
    assert asyncio.run(module.acknowledge(request(payload)))['acknowledged'] # A repeated acknowledgement is safe.


def test_acknowledgement_preserves_changed_bytes_and_rejects_traversal(bridge):
    module,output,models,error=bridge;folder=output/'gbr';folder.mkdir();attempt='b'*32;owned=folder/(attempt+'.flac');owned.write_bytes(b'changed')
    payload={'attempt_id':attempt,'file':{'filename':owned.name,'subfolder':'gbr','type':'output'},'sha256':'0'*64}
    with pytest.raises(error):asyncio.run(module.acknowledge(request(payload)))
    assert owned.read_bytes()==b'changed'
    payload['file']['subfolder']='gbr/../../elsewhere'
    with pytest.raises(error):asyncio.run(module.acknowledge(request(payload)))


def test_checkpoint_hashes_are_verified_and_cache_invalidates_on_changed_file(bridge):
    module,output,models,error=bridge
    for name in ['encoder','dit','vae']:(models/(name+'.safetensors')).write_bytes(name.encode())
    payload={'text_encoder':'encoder.safetensors','dit':'dit.safetensors','vae':'vae.safetensors'}
    first=asyncio.run(module.model_info(request(payload)));second=asyncio.run(module.model_info(request(payload)))
    assert first==second and first['dit']['sha256']==hashlib.sha256(b'dit').hexdigest()
    (models/'dit.safetensors').write_bytes(b'different checkpoint')
    changed=asyncio.run(module.model_info(request(payload)));assert changed['dit']['sha256']!=first['dit']['sha256']


def test_checkpoint_names_cannot_escape_model_folders(bridge):
    module,output,models,error=bridge
    with pytest.raises(error):asyncio.run(module.model_info(request({'text_encoder':'../private','dit':'dit','vae':'vae'})))
