from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse,Response
from pydantic import BaseModel,ConfigDict,Field,StrictBool
from typing import Literal
from .publishing import Publisher


class Prepare(BaseModel):
    model_config=ConfigDict(extra='forbid')
    master_id:str|None=None
    artwork_id:str|None=None
    include_flac:StrictBool=True
    timed_lyrics:StrictBool=False
    visibility:str=Field(default='public',pattern=r'^[a-z][a-z0-9-]{0,63}$')


class Publish(BaseModel):
    model_config=ConfigDict(extra='forbid')
    visibility:str|None=Field(default=None,pattern=r'^[a-z][a-z0-9-]{0,63}$')


class CoverTrigger(BaseModel):
    model_config=ConfigDict(extra='forbid')
    track_id:str|None=Field(default=None,max_length=200)


class Settings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    endpoint:str=Field(max_length=300)
    bucket:str=Field(max_length=63)
    public_base:str=Field(max_length=2000)
    website_url:str=Field(max_length=2000)
    access_key:str=Field(default='',max_length=500)
    secret_key:str=Field(default='',max_length=500)
    profile:str=Field(default='',max_length=100)


class RcloneSettings(BaseModel):
    model_config=ConfigDict(extra='forbid')
    remote:str=Field(default='r2',min_length=1,max_length=64)
    bucket:str|None=Field(default=None,max_length=63)
    public_base:str|None=Field(default=None,max_length=2000)
    website_url:str|None=Field(default=None,max_length=2000)


class LegacyPreview(BaseModel):
    model_config=ConfigDict(extra='forbid')
    directory:str=Field(max_length=2000)


class LibraryImportRequest(LegacyPreview):
    synchronise:StrictBool=True


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
    @routes.post('/settings/rclone')
    def use_rclone(body:RcloneSettings):return service.use_rclone(**body.model_dump())
    @routes.post('/check')
    def check():
        try:return service.check()
        except Exception as exc:
            from .publishing import error_text
            raise HTTPException(400,error_text(exc)) from exc
    @routes.get('/projects')
    def projects():return service.projects()
    @routes.get('/library')
    def library():return service.library.projects()
    @routes.get('/library/settings')
    def library_settings():return service.library.configuration()
    @routes.post('/library/settings')
    def save_library_settings(body:dict):return service.library.save_settings(body)
    @routes.post('/library/settings/sync')
    def sync_library_settings():return service.library.sync_settings()
    @routes.get('/library/import')
    def import_state():return service.library_import.state()
    @routes.post('/library/import')
    def import_showcase(body:LibraryImportRequest):return service.library_import.start(body.directory,body.synchronise)
    @routes.get('/library/recordings/{gid}')
    def library_recording(gid:str):return service.library.detail(gid)
    @routes.post('/library/recordings/{gid}')
    def save_library_recording(gid:str,body:dict):return service.library.save(gid,body)
    @routes.post('/library/recordings/{gid}/prepare')
    def prepare_revision(gid:str,body:dict):return service.library.prepare_revision(gid,body)
    @routes.get('/easter-egg')
    def easter_egg():return service.easter.configuration()
    @routes.post('/easter-egg')
    def save_easter_egg(body:CoverTrigger):return service.easter.save(body.track_id)
    @routes.get('/easter-egg/covers/{track_id}')
    def cover_preview(track_id:str):return Response(service.easter.cover(track_id),media_type='image/jpeg',headers={'Cache-Control':'private,max-age=300'})
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
    @routes.post('/projects/{gid}/finish')
    def finish(gid:str,body:Prepare):return service.finish_preparation(gid,body.model_dump())
    @routes.post('/projects/{gid}/reset-completion')
    def reset(gid:str):return service.showcase.reset(gid)
    @routes.post('/{identifier}/publish')
    def publish(identifier:str,body:Publish|None=None):return service.start_publish(identifier,body.visibility if body else None)
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
