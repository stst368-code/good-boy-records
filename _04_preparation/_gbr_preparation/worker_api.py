"""Bearer-authenticated worker routes, independent of browser sign-in cookies."""
from pathlib import Path
import tempfile
import zipfile
from fastapi import APIRouter,Depends,HTTPException,Request
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .remote_processing import MAX_BUNDLE


class Worker(BaseModel):
    model_config=ConfigDict(extra='forbid')
    id:str=Field(pattern='^[a-f0-9]{32}$')
    name:str=Field(default='Preparation PC',max_length=64)
    device:str=Field(default='auto',max_length=40)
    available:StrictBool=True


def worker_router(prep):
    async def authorized(request:Request):
        if not prep.remote.authorized(request.headers.get('authorization','')):
            raise HTTPException(401,'Pair this preparation worker with GBR first')
    routes=APIRouter(prefix='/api/processing-worker',dependencies=[Depends(authorized)])

    @routes.post('/claim')
    async def claim(body:Worker):
        return await run_in_threadpool(prep.remote.claim,body.model_dump())

    @routes.post('/heartbeat')
    async def heartbeat(body:Worker):
        return prep.remote.heartbeat(body.model_dump())

    @routes.get('/tasks/{identifier}/source')
    def source(identifier:str,request:Request):
        return FileResponse(prep.remote.source(identifier,request.headers.get('x-gbr-lease','')),media_type='application/octet-stream')

    @routes.get('/tasks/{identifier}/vocals')
    def vocals(identifier:str,request:Request):
        return FileResponse(prep.remote.vocals(identifier,request.headers.get('x-gbr-lease','')),media_type='application/octet-stream')

    @routes.post('/tasks/{identifier}/progress')
    async def progress(identifier:str,request:Request):
        return prep.remote.progress(identifier,request.headers.get('x-gbr-lease',''),await request.json())

    @routes.post('/tasks/{identifier}/fail')
    async def fail(identifier:str,request:Request):
        body=await request.json()
        return prep.remote.fail(identifier,request.headers.get('x-gbr-lease',''),str(body.get('error') or 'Preparation worker failed'))

    @routes.post('/tasks/{identifier}/result')
    async def result(identifier:str,request:Request):
        lease=request.headers.get('x-gbr-lease','');prep.remote.task(identifier,lease)
        with tempfile.TemporaryDirectory(prefix='gbr-worker-upload-') as temporary:
            path=Path(temporary)/'result.zip';size=0
            with path.open('wb') as stream:
                async for chunk in request.stream():
                    size+=len(chunk)
                    if size>MAX_BUNDLE:raise HTTPException(413,'Worker result exceeds 1 GiB')
                    stream.write(chunk)
            try:return await run_in_threadpool(prep.remote.complete,identifier,lease,path)
            except (zipfile.BadZipFile,UnicodeError) as error:raise ValueError('Invalid worker result bundle') from error
    return routes
