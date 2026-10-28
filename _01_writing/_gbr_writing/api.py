from typing import Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from .service import WritingService
from .authoring import Authoring


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Settings(Body):
    endpoint: str = Field(max_length=2000)
    model: str = Field(default='',max_length=1000)
    temperature: float = Field(default=0.8,ge=0,le=2,allow_inf_nan=False)
    top_p: float = Field(default=0.95,gt=0,le=1,allow_inf_nan=False)
    max_tokens: int = Field(default=4096,ge=256,le=32768,strict=True)

class Submit(Body):
    kind: Literal['new','lyrics','caption','both','translate']
    brief: str = Field(min_length=1,max_length=16000)
    files: list[str] = Field(default_factory=list,max_length=100)
    language: str = Field(default='',max_length=100)
    cluster: str = Field(default='',max_length=1000)
    style: str = Field(default='',max_length=1000)
    use_lore: StrictBool = True
    use_styles: StrictBool = True
    use_lyrics: StrictBool = True
    template_ids: list[str] = Field(default_factory=list,max_length=3)

class Edit(Body):
    changes: dict[str,str]
    revision: int = Field(ge=0,strict=True)

class Publish(Body):
    mode: Literal['keep','archive_replace'] = 'keep'
    revision: int = Field(ge=0,strict=True)

class Template(Body):
    template_id: str = Field(min_length=2,max_length=120)

class Action(Body):
    action: Literal['retry','cancel']

class BaseSave(Body):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    file: str = Field(default='',max_length=2000)
    source_sha256: str = Field(default='',max_length=64)
    title: str = Field(min_length=1,max_length=500)
    lyrics: str = Field(min_length=1,max_length=200000)
    author: str = Field(default='gbr',max_length=1000)
    inspiration_author: str = Field(default='',max_length=1000)
    inspiration_track: str = Field(default='',max_length=1000)
    album_name: str = Field(default='',max_length=1000)
    album_position: int | None = Field(default=None,ge=1,strict=True)

class CaptionSave(Body):
    request_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    cluster: str = Field(min_length=1,max_length=500)
    version: str = Field(default='',max_length=500)
    song_id: str = Field(default='',max_length=30)
    fields: dict[str,str] = Field(max_length=12)


def router(root, review, local, reviewer_id):
    service = WritingService(root,review)
    authoring = Authoring(service)
    def host_only(request: Request):
        if not local(request):raise HTTPException(403,'Open Writing on the host computer to edit recipes or send model requests')
    routes = APIRouter(prefix='/api/writing',dependencies=[Depends(host_only)])

    @routes.get('/authoring')
    def options():return authoring.options()
    @routes.get('/base')
    def source(file: str):return authoring.source(file)
    @routes.post('/base')
    def save_base(body: BaseSave,request: Request):return authoring.save_base(body.model_dump(),reviewer_id(request))
    @routes.get('/caption-examples')
    def examples(field: str,q: str='',cluster: str=''):return authoring.examples(field,q,cluster)
    @routes.get('/caption-auditions')
    def auditions(field:str,template_id:str):return authoring.auditions(field,template_id)
    @routes.post('/captions')
    def save_caption(body: CaptionSave,request: Request):return authoring.save_caption(body.model_dump(),reviewer_id(request))

    @routes.get('/settings')
    def settings():return service.settings()
    @routes.post('/settings')
    def save_settings(body: Settings):return service.save_settings(body.model_dump())
    @routes.post('/query')
    def query():return service.query()
    @routes.get('/catalogue')
    def catalogue():return service.catalogue()
    @routes.post('/tasks')
    def submit(body: Submit,request: Request):return service.submit(body.model_dump(),reviewer_id(request))
    @routes.get('/tasks')
    def tasks():return {'tasks':service.tasks()}
    @routes.get('/tasks/{identifier}')
    def task(identifier: str):return service.public(identifier)
    @routes.post('/tasks/{identifier}/edit')
    def update(identifier: str,body: Edit,request: Request):return service.update(identifier,body.changes,body.revision,reviewer_id(request))
    @routes.post('/tasks/{identifier}/publish')
    def publish(identifier: str,body: Publish):return service.publish(identifier,body.mode,body.revision)
    @routes.post('/tasks/{identifier}/template')
    def template(identifier: str,body: Template):return service.save_caption_template(identifier,body.template_id)
    @routes.post('/tasks/{identifier}/action')
    def action(identifier: str,body: Action,request: Request):
        return service.retry(identifier,reviewer_id(request)) if body.action == 'retry' else service.cancel(identifier)
    return routes,service
