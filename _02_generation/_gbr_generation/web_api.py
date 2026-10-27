"""Generation routes mounted in the review app with the same host and request protection."""
import csv
import io
import json
from typing import Literal
from urllib.error import HTTPError, URLError

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
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
    controls: dict | None = None
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


class AutoWorkers(Body):
    enabled:StrictBool


class WorkerQuery(WorkerChoice):
    activate: StrictBool = False


class RatingFilter(Body):
    threshold: float = Field(ge=0, le=5, allow_inf_nan=False)
    reviewer_id: str | None = None
    cluster: str | None = Field(default=None, max_length=1000)
    style: str | None = Field(default=None, max_length=1000)


class Preview(Body):
    input_root: str
    purpose: Literal['production','experiment'] = 'production'
    experiment_label: str = Field(default='',max_length=160)
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
    caption_templates: list[str] | None = Field(default=None,max_length=10000)
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


class PublishComparison(Body):
    parameter: Literal['sampler_steps','sampler_cfg','encoder_cfg','top_k','duration']
    group: str = Field(pattern='^[0-9a-f]{64}$')
    title: str = Field(min_length=1,max_length=160)
    slot: str = Field(default='',max_length=64)
    format: Literal['flac','mp3'] = 'flac'


class Action(Body):
    action: Literal['pause', 'resume', 'continue', 'retry', 'cleanup']


def router(root, review_service, local):
    service = GenerationService(root, review_service)
    from .jobs import Jobs
    jobs_service=Jobs(service)

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
        root, catalogue = service.styles()
        rows=[]
        for row in catalogue.rows:
            path=(root/row['template'].replace('\\','/')).resolve()
            if not path.is_relative_to(root.resolve()) or not row['cluster'] or not row['style_primary']:continue
            try:
                if not path.is_file() or not path.read_text(encoding='utf-8-sig').strip():continue
            except (OSError,UnicodeError):continue
            rows.append({'id':row['ID'],'cluster':row['cluster'],'style':row['style_primary'],
                         'source':row.get('template_source'),'version':row.get('version') or row.get('template_version'),
                         'song_id':row.get('song_id') or None})
        return {'templates':rows}

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
        service.dispatcher.wake.set()
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

    @routes.get('/jobs')
    def jobs(purpose:Literal['production','experiment','all']='production'):return jobs_service.listing(purpose)

    @routes.get('/jobs/{batch_id}')
    def job_details(batch_id:str,offset:int=0,limit:int=100,sort:Literal['created','steps','time']='created'):return jobs_service.details(batch_id,offset,limit,sort)

    @routes.get('/jobs/{batch_id}/performance.csv')
    def performance(batch_id:str):
        rows=jobs_service.performance(batch_id)
        stream=io.StringIO(newline='')
        columns=list(dict.fromkeys(key for row in rows for key in row)) or ['batch_id','run_id','attempt_id','purpose']
        writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader()
        for row in rows:
            writer.writerow({key:json.dumps(value,ensure_ascii=False) if isinstance(value,(list,dict)) else
                "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value for key,value in row.items()})
        return StreamingResponse(iter(['\ufeff'+stream.getvalue()]),media_type='text/csv',
            headers={'Content-Disposition':f'attachment; filename="gbr-{batch_id}-performance.csv"','Cache-Control':'no-store'})

    @routes.get('/jobs/{batch_id}/comparison-options')
    def comparison_options(batch_id:str):
        return service.experiment_publisher.options(batch_id)

    @routes.post('/jobs/{batch_id}/publish-comparison')
    def publish_comparison(batch_id:str,body:PublishComparison):
        try:return service.experiment_publisher.start(batch_id,body.model_dump())
        except Exception as error:
            from _gbr_preparation.publishing import error_text
            raise HTTPException(400,error_text(error)) from None

    @routes.post('/experiment-publications/{identifier}/retry')
    def retry_comparison(identifier:str):
        try:return service.experiment_publisher.retry(identifier)
        except Exception as error:
            from _gbr_preparation.publishing import error_text
            raise HTTPException(400,error_text(error)) from None

    @routes.post('/experiment-publications/{identifier}/withdraw')
    def withdraw_comparison(identifier:str):
        try:return service.experiment_publisher.withdraw(identifier)
        except Exception as error:
            from _gbr_preparation.publishing import error_text
            raise HTTPException(400,error_text(error)) from None

    @routes.get('/experiments/{run_id}/audio')
    def experiment_audio(run_id:str):
        from .experiment_outputs import ExperimentOutputs
        return FileResponse(ExperimentOutputs(service).audio(run_id),media_type='audio/flac',headers={'Cache-Control':'no-store'})

    @routes.post('/experiments/{run_id}/promote')
    def promote_experiment(run_id:str):
        from .experiment_outputs import ExperimentOutputs
        try:return ExperimentOutputs(service).action(run_id,'promote')
        except OSError as error:raise ValueError('Experiment move is pending. Close file handles and retry Send to review: '+str(error)) from None

    @routes.post('/experiments/{run_id}/delete-output')
    def delete_experiment_output(run_id:str):
        from .experiment_outputs import ExperimentOutputs
        try:return ExperimentOutputs(service).action(run_id,'delete')
        except OSError as error:raise ValueError('Experiment file deletion is pending. Close file handles and retry Delete output files: '+str(error)) from None

    @routes.get('/jobs/{batch_id}/runs/{run_id}/text')
    def job_text(batch_id:str,run_id:str):return jobs_service.text(batch_id,run_id)

    @routes.post('/jobs/{batch_id}/runs/{run_id}/cancel')
    def cancel_run(batch_id:str,run_id:str):return jobs_service.cancel(batch_id,run_id)

    @routes.post('/jobs/{batch_id}/workers')
    def add_batch_worker(batch_id:str,body:WorkerChoice):
        try:return service.dispatcher.add(batch_id,body.worker_id)
        except (OSError,RuntimeError) as error:raise ValueError('Cannot assign this worker: '+str(error)) from None

    @routes.post('/jobs/{batch_id}/auto-workers')
    def auto_batch_workers(batch_id:str,body:AutoWorkers):return service.dispatcher.set_automatic(batch_id,body.enabled)

    @routes.post('/tasks/{task_id}')
    def task_action(task_id: str, body: Action):
        return service.action(task_id, body.action)

    return routes, service
