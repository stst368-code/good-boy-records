from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from .publishing import Publisher


class Prepare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    master_id:str|None=None
    artwork_id:str|None=None
    include_flac:StrictBool=True
    timed_lyrics:StrictBool=False


class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    endpoint:str=Field(max_length=300)
    bucket:str=Field(max_length=63)
    public_base:str=Field(max_length=2000)
    website_url:str=Field(max_length=2000)
    access_key:str=Field(default='',max_length=500)
    secret_key:str=Field(default='',max_length=500)
    profile:str=Field(default='',max_length=100)


class LegacyPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    directory:str=Field(max_length=2000)


class LegacyApply(BaseModel):
    model_config=ConfigDict(extra='forbid')
    token:str=Field(max_length=64)
    generations:list[str]=Field(max_length=10000)
    synchronise:StrictBool=True


def router(root,review,preparation,local):
    service=Publisher(root,review,preparation)
    def host(request:Request):
        if not local(request):raise HTTPException(403,'Open Publishing on the host computer')
    routes=APIRouter(prefix='/api/releases',dependencies=[Depends(host)])
    @routes.get('/settings')
    def settings():return service.configuration()
    @routes.post('/settings')
    def save(body:Settings):return service.save_settings(body.model_dump())
    @routes.post('/check')
    def check():
        try:return service.check()
        except Exception as exc:
            from .publishing import error_text
            raise HTTPException(400,error_text(exc)) from exc
    @routes.get('/projects')
    def projects():return service.projects()
    @routes.get('/legacy')
    def legacy():return service.legacy.configuration()
    @routes.post('/legacy/preview')
    def preview_legacy(body:LegacyPreview):return service.legacy.preview(body.directory)
    @routes.post('/legacy/apply')
    def apply_legacy(body:LegacyApply):return service.legacy.start(body.token,body.generations,body.synchronise)
    @routes.post('/legacy/{identifier}/retry')
    def retry_legacy(identifier:str):return service.legacy.retry(identifier)
    @routes.post('/research')
    def research():
        try:return service.publish_research()
        except Exception as exc:
            from .publishing import error_text
            raise HTTPException(400,error_text(exc)) from exc
    @routes.post('/projects/{gid}/prepare')
    def prepare(gid:str,body:Prepare):return service.start_prepare(gid,body.model_dump())
    @routes.post('/projects/{gid}/complete')
    def complete(gid:str,body:Prepare):return service.start_complete(gid,body.model_dump())
    @routes.post('/projects/{gid}/reset-completion')
    def reset(gid:str):return service.showcase.reset(gid)
    @routes.post('/{identifier}/publish')
    def publish(identifier:str):return service.start_publish(identifier)
    @routes.post('/{identifier}/discard')
    def discard(identifier:str):return service.discard(identifier)
    @routes.post('/{identifier}/withdraw')
    def withdraw(identifier:str):
        try:return service.start_withdraw(identifier)
        except Exception as exc:
            from .publishing import error_text
            raise HTTPException(400,error_text(exc)) from exc
    @routes.get('/{identifier}/assets/{index}')
    def asset(identifier:str,index:int):
        path,mime=service.asset(identifier,index);return FileResponse(path,media_type=mime)
    return routes,service
