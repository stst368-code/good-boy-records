"""Preparation inside GBR, protected by the app's existing authentication."""
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from typing import Literal
from .service import Preparation


class Job(BaseModel):
    model_config=ConfigDict(extra='forbid')
    kind: Literal['mix_preview','mix_master','visuals','lyric_alignment']
    stem_task_id: str | None=None
    input_artifact_id: str | None=None
    language: str | None=None
    mix: dict=Field(default_factory=dict)
    start: float=Field(default=0,ge=0,allow_inf_nan=False)
    seconds: StrictInt=Field(default=30,ge=1,le=60)


class Artwork(BaseModel):
    model_config=ConfigDict(extra='forbid')
    family: Literal['comfyui','qwen-2.1','moss-image','flux']='comfyui'
    direction: str=Field(default='',max_length=5000)
    references: list[str]=Field(default_factory=list,max_length=10)
    seed: StrictInt=Field(default=0,ge=0,lt=2**63)


def router(root,review,local):
    service=Preparation(root,review)
    def host(request: Request):
        if not local(request):raise HTTPException(403,'Open Preparation on the host computer to process tracks')
    routes=APIRouter(prefix='/api/preparation',dependencies=[Depends(host)])

    @routes.get('/projects')
    def projects():return service.projects()

    @routes.post('/sync')
    def sync():
        service.sync_selection()
        return service.projects()

    @routes.get('/references')
    def references():return {'references':service.reference_list()}

    @routes.post('/projects/{gid}/jobs')
    def job(gid: str,body: Job):return service.submit(gid,body.kind,body.model_dump(exclude={'kind'}))

    @routes.post('/projects/{gid}/artwork')
    def artwork(gid: str,body: Artwork):return service.artwork(gid,body.model_dump())

    @routes.post('/projects/{gid}/cleanup-previews')
    def cleanup(gid: str):
        service.require()
        return service.clean_previews(gid)

    @routes.post('/tasks/{identifier}/retry')
    def retry(identifier: str):return service.retry(identifier)

    @routes.get('/r2-plan')
    def storage_plan():
        service.require()
        from .storage_plan import plan
        return plan(service.db)

    @routes.get('/projects/{gid}/editorial')
    def editorial(gid: str,artifact: str | None=None):return service.editorial(gid,artifact)

    @routes.post('/projects/{gid}/editorial/{kind}')
    def save_editorial(gid: str,kind: Literal['lyric_edit','visual_edit'],body: dict):return service.save_editorial(gid,kind,body)

    @routes.get('/audio/{gid}')
    def audio(gid: str,artifact: str | None=None):
        service.require();_,source=service.source(gid,artifact_id=artifact)
        return FileResponse(source,media_type='audio/flac')

    @routes.get('/artifacts/{identifier}')
    def artifact(identifier: str):
        service.require()
        return FileResponse(service.artifact(identifier))

    return routes,service
