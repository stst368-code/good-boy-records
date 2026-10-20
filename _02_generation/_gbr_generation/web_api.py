"""Generation routes mounted in the review app with the same host and request protection."""
import json
from typing import Literal
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from .web_service import GenerationService
from .planner import BatchRejected


class Body(BaseModel):
    model_config = ConfigDict(extra='forbid')

    @model_validator(mode='after')
    def finite_json(self):
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class Connection(Body):
    backend: Literal['local', 'runpod_dedicated', 'runpod_serverless', 'comfyui'] = 'local'
    address: str = Field(default='', max_length=2000)
    identity_file: str = Field(default='', max_length=4000)
    worker_python: str = Field(default='', max_length=4000)
    memory_profile: Literal['auto', 'low_vram', 'resident_stage'] = 'auto'
    auto_setup: StrictBool = True


class Preferences(Body):
    connection: Connection | None = None
    worker_id: str | None = Field(default=None, max_length=2100)
    input_root: str | None = None
    parameters: dict | None = None
    layout: Literal['single', 'track', 'album'] = 'single'
    storage_endpoint: str = Field(default='', max_length=2000)
    storage_bucket: str = Field(default='', max_length=2000)
    storage_region: str = Field(default='auto', max_length=100)
    secrets: dict[str, str] = Field(default_factory=dict)


class Worker(Preferences):
    install: StrictBool = False


class WorkerSettings(Preferences):
    connection: Connection
    previous_id: str | None = Field(default=None, max_length=2100)
    enabled: StrictBool = True
    defaults: dict = Field(default_factory=dict)
    launcher: dict = Field(default_factory=dict)


class WorkerChoice(Body):
    worker_id: str = Field(max_length=2100)


class WorkerQuery(WorkerChoice):
    activate: StrictBool = False


class RatingFilter(Body):
    threshold: float = Field(ge=0, le=5, allow_inf_nan=False)
    reviewer_id: str | None = None
    cluster: str | None = Field(default=None, max_length=1000)
    style: str | None = Field(default=None, max_length=1000)


class Preview(Body):
    input_root: str
    connection: Connection | None = None
    worker_id: str | None = Field(default=None, max_length=2100)
    all_inputs: StrictBool = False
    files: list[str] = Field(default_factory=list, max_length=5000)
    songs: list[str] = Field(default_factory=list, max_length=5000)
    albums: list[str] = Field(default_factory=list, max_length=5000)
    order: Literal['selection', 'filename', 'album'] = 'selection'
    parameters: dict = Field(default_factory=dict)
    parameter_priority: Literal['yaml', 'form'] = 'yaml'
    parameter_sources: dict[str, Literal['yaml', 'worker', 'control']] = Field(default_factory=dict)
    field_precedence: dict = Field(default_factory=dict)
    template_id: str | None = None
    catalogue_captions: StrictBool = True
    caption_mode: Literal['random', 'yaml', 'catalogue'] | None = None
    caption_count: int = Field(default=1, ge=1, le=1000, strict=True)
    caption_cluster: str | None = Field(default=None, max_length=1000)
    caption_style: str | None = Field(default=None, max_length=1000)
    experiments: dict = Field(default_factory=dict)
    model_revision: str | None = None
    encoding_cache: Literal['prefer', 'require', 'disabled'] = 'prefer'
    layout: Literal['single', 'track', 'album'] = 'single'
    rating_filter: RatingFilter | None = None
    rerun: dict = Field(default_factory=dict)
    parent_review_generation_id: str | None = None
    request_id: str | None = Field(default=None, min_length=1, max_length=1000)


class Start(Body):
    token: str
    skip_errors: StrictBool = False


class Action(Body):
    action: Literal['pause', 'resume', 'continue', 'retry', 'cleanup']


def router(root, review_service, local):
    service = GenerationService(root, review_service)

    def host_only(request: Request):
        if not local(request):
            raise HTTPException(403, 'Open Generation on the host computer to configure workers and send jobs')

    routes = APIRouter(prefix='/api/generation', dependencies=[Depends(host_only)])

    @routes.get('/preferences')
    def preferences():
        return service.preferences()

    @routes.post('/preferences')
    def save_preferences(body: Preferences):
        service.remember(body.model_dump(exclude_none=True, exclude_unset=True))
        return service.preferences()

    @routes.get('/catalogue')
    def catalogue(root: str | None = None):
        try:
            return service.catalogue(root)
        except OSError as exc:
            raise ValueError('Choose a writing folder containing track_list.csv, variant_list.csv and the track YAMLs: ' + str(exc)) from None

    @routes.get('/templates')
    def templates():
        _, catalogue = service.styles()
        return {'templates': [{'id': row['ID'], 'cluster': row['cluster'], 'style': row['style_primary'],
                              'source': row.get('template_source')} for row in catalogue.rows]}

    @routes.get('/workers')
    def workers(refresh: bool = False):
        return {'workers': service.workers.list(refresh)}

    @routes.post('/workers')
    def save_worker(body: WorkerSettings):
        data = body.model_dump(exclude_none=True, exclude_unset=True)
        from .web_service import SECRET_ENV
        if set(data.get('secrets', {})) - set(SECRET_ENV):raise ValueError('Unknown credential field')
        entry = service.workers.save(data, body.previous_id)
        service.remember({k: v for k, v in data.items() if k in {'storage_endpoint', 'storage_bucket', 'storage_region', 'secrets'}})
        return entry

    @routes.post('/workers/remove')
    def remove_worker(body: WorkerChoice):
        service.workers.delete(body.worker_id)
        return {'workers': service.workers.list()}

    @routes.post('/workers/query')
    def query_worker(body: WorkerQuery):
        entry = service.workers.get(body.worker_id)
        service.workers.query(entry, body.activate)
        return next(e for e in service.workers.list() if e['id'] == entry['id'])

    @routes.post('/workers/select')
    def select_worker(body: WorkerChoice):
        entry, report = service.workers.select(body.worker_id, automatic=body.worker_id == 'auto')
        return {**entry, 'status': {k: v for k, v in report.items() if not k.startswith('_')}}

    @routes.post('/workers/prepare')
    def prepare_worker(body: WorkerChoice):
        entry = service.workers.get(body.worker_id)
        if entry['connection']['backend'] == 'comfyui':
            raise ValueError('Use Start / query for a configured local ComfyUI worker')
        return service.launch({'connection': entry['connection'], 'install': True})

    @routes.post('/comfy-inventory')
    def comfy_inventory(body: Connection):
        from .connections import normalize
        connection=normalize(body.model_dump())
        if connection['backend']!='comfyui':raise ValueError('Choose a ComfyUI URL')
        return service.comfy_inventory(connection['address'])

    @routes.post('/worker')
    def worker(body: Worker):
        data = body.model_dump(exclude_none=True, exclude_unset=True)
        connection = service.remember(data)
        if connection is None:raise ValueError('Choose a worker connection')
        return service.launch({'connection': connection, 'install': body.install})

    @routes.post('/preview')
    def preview(body: Preview):
        try:
            return service.preview(body.model_dump())
        except HTTPError as exc:
            raise ValueError(f'ComfyUI request failed: HTTP {exc.code} at {exc.url}. Check the worker URL and its API routes in Worker settings.') from None
        except URLError as exc:
            raise ValueError('Cannot connect to the generation worker: ' + str(exc.reason)) from None
        except OSError as exc:
            raise ValueError('Cannot read a writing input or caption reference: ' + str(exc)) from None

    @routes.post('/start')
    def start(body: Start):
        try:
            return service.start(body.token, body.skip_errors)
        except BatchRejected as exc:
            raise HTTPException(400, {'message': str(exc), 'inputs': exc.report}) from None
        except OSError as exc:
            raise ValueError('The batch could not be prepared: ' + str(exc)) from None

    @routes.get('/tasks')
    def tasks():
        return {'tasks': service.tasks()}

    @routes.post('/tasks/{task_id}')
    def task_action(task_id: str, body: Action):
        return service.action(task_id, body.action)

    return routes, service
