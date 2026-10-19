"""Place this file in ComfyUI/custom_nodes for verified GBR temporary-output cleanup."""
import hashlib
from pathlib import Path
import re
from aiohttp import web
import folder_paths
from server import PromptServer

@PromptServer.instance.routes.get('/gbr/capabilities')
async def capabilities(request):return web.json_response({'version':2,'verified_output_cleanup':True,'checkpoint_sha256':True})

@PromptServer.instance.routes.post('/gbr/ack')
async def acknowledge(request):
    data=await request.json();attempt=data.get('attempt_id','');sha=data.get('sha256','');file=data.get('file',{})
    if not re.fullmatch('[0-9a-f]{32}',attempt) or not re.fullmatch('[0-9a-f]{64}',sha):raise web.HTTPBadRequest()
    root=(Path(folder_paths.get_output_directory())/'gbr').resolve()
    filename=file.get('filename','');subfolder=file.get('subfolder','')
    if file.get('type','output')!='output' or Path(filename).name!=filename or '\\' in filename or not filename.startswith(attempt) or not filename.endswith('.flac') or not subfolder.startswith('gbr'):raise web.HTTPBadRequest()
    path=(Path(folder_paths.get_output_directory())/subfolder/filename).resolve()
    if not path.is_relative_to(root):raise web.HTTPForbidden()
    if path.is_file():
        h=hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
        if h.hexdigest()!=sha:raise web.HTTPConflict(text='Output bytes changed; retained')
        path.unlink()
    return web.json_response({'acknowledged':True})

NODE_CLASS_MAPPINGS={}
NODE_DISPLAY_NAME_MAPPINGS={}

_MODEL_HASH_CACHE={}

@PromptServer.instance.routes.post('/gbr/model-info')
async def model_info(request):
    data=await request.json();result={}
    categories={'text_encoder':['text_encoders','clip'],'dit':['diffusion_models','unet'],'vae':['vae']}
    if not isinstance(data,dict) or set(data)!=set(categories):raise web.HTTPBadRequest()
    import asyncio
    def describe():
        for role,aliases in categories.items():
            name=data[role]
            if not isinstance(name,str) or '..' in name.replace('\\','/').split('/') or name.startswith(('/','\\')):raise web.HTTPBadRequest()
            path=None
            for category in aliases:
                try:
                    candidate=folder_paths.get_full_path(category,name)
                    if candidate and any(Path(candidate).resolve().is_relative_to(Path(root).resolve()) for root in folder_paths.get_folder_paths(category)):
                        path=Path(candidate).resolve();break
                except KeyError:continue
            if not path or not path.is_file():raise web.HTTPNotFound(text='Checkpoint unavailable: '+role)
            stat=path.stat();key=(str(path),stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns,stat.st_ctime_ns)
            if key not in _MODEL_HASH_CACHE:
                h=hashlib.sha256()
                with path.open('rb') as stream:
                    for block in iter(lambda:stream.read(8*1024*1024),b''):h.update(block)
                after=path.stat()
                if (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)!=key[1:]:raise web.HTTPConflict(text='Checkpoint changed during hashing')
                _MODEL_HASH_CACHE[key]=h.hexdigest()
            result[role]={'filename':name,'sha256':_MODEL_HASH_CACHE[key],'size_bytes':stat.st_size}
        return result
    return web.json_response(await asyncio.to_thread(describe))
