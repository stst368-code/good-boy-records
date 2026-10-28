"""Selection, immutable batch previews and supervised app jobs on the existing shared DB."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import sys
import threading
import time
import uuid

from .catalogue import Catalogue, Issue, Styles
from .planner import json_text, preflight, prepare_batch, verify_plan
from .connections import connection_key, execution, key_candidates, normalize
from .review_selection import select_by_rating
from .yaml_document import YamlDocument
from .experiments import expand, MAX_RUNS
from _gbr_layout import task_paths, relocated_path
from _gbr_worker.contract import MODEL, SMOKE_MODEL_REVISION, python_environment

DEFAULT_PARAMETERS = {'model': MODEL, 'dit': 'bf16', 'text_encoder': 'bf16', 'encoder_cfg': 1.5, 'top_k': 50,
    'sampler': 'euler', 'scheduler': 'flow_match_euler', 'sampler_cfg': 1.7, 'sampler_steps': 30,
    'duration': 360, 'encoder_seed': 'auto', 'sampler_seed': 'auto'}
SECRET_ENV = {'api_key': 'RUNPOD_API_KEY', 's3_access_key': 'AWS_ACCESS_KEY_ID',
              's3_secret_key': 'AWS_SECRET_ACCESS_KEY', 's3_session_token': 'AWS_SESSION_TOKEN', 'hf_token': 'HF_TOKEN'}


class GenerationService:
    def __init__(self, root, review_service):
        self.root, self.review = Path(root), review_service
        self.previews, self.processes, self.secrets = {}, {}, {}
        self.lock = threading.RLock()
        from .workers import Workers
        self.workers = Workers(self)
        from .dispatch import Dispatcher
        self.dispatcher=Dispatcher(self)
        from .runpod_account import RunpodDiscovery
        self.runpod=RunpodDiscovery(self)

    @property
    def runtime(self):
        return self.root / '_02_generation' / '_runtime'

    @property
    def generated(self):
        return self.root / '_02_generation' / '_generated'

    @property
    def experimental(self):
        return self.root / '_02_generation' / '_experiments'

    def register_inbox(self):
        with self.review.config_lock:
            if self.generated.resolve() not in self.review.config.inputs:
                directories = self.review.config.values['input_directories']
                self.review.config.values['input_directories'] = [*directories, str(self.generated.resolve())]
                self.review.config.validate()
                self.review.config.save()

    def shared_ready(self):
        return bool(self.review.db.one("SELECT 1 FROM sqlite_master WHERE name='platform_migration_report'")
                    and self.review.db.one('SELECT 1 FROM platform_migration_report WHERE id=1'))

    def catalogue_root(self, value=None):
        """Use the same writing location for preferences, loading and previewing.

        Blank paths and the repository/writing containers mean the standard
        catalogue. Explicit custom catalogues stay explicit, including errors.
        """
        repository = self.root.resolve()
        default = repository / '_01_writing' / '_built'
        text = str(value or '').strip()
        if not text:
            return default.resolve()
        candidate = Path(relocated_path(text, root=repository)).expanduser()
        if not candidate.is_absolute():
            candidate = repository / candidate
        candidate = candidate.resolve()
        has_catalogue = any((candidate / name).is_file() for name in ('track_list.csv', 'variant_list.csv'))
        if candidate in {repository, default.parent} and not has_catalogue:
            return default.resolve()
        return candidate

    def preferences(self):
        saved = deepcopy(self.review.config.values.get('generation', {}))
        keys = key_candidates()
        connections = [e['connection'] for e in saved['workers'] if e.get('connection')] if 'workers' in saved else saved.get('connections', [])
        if not connections and 'workers' not in saved:
            connections = [normalize({'backend': 'local'})]
            latest = self.runtime / 'local_test/latest.json'
            try:
                manifest = verify_plan(relocated_path(json.loads(latest.read_text(encoding="utf-8"))['manifest']))
                target = manifest['runs'][0]['execution']['diffusion']
                if target['backend'] != 'local':
                    connections.append(normalize({'backend': target['backend'], 'address': target['address'],
                                                   'identity_file': keys[0] if keys else ''}))
            except (OSError, ValueError, KeyError):
                pass
        return {'connections': connections, 'worker_id': saved.get('worker_id', 'auto'), 'key_candidates': keys, 'parameters': saved.get('parameters', DEFAULT_PARAMETERS),
                'controls':saved.get('controls',{}), 'input_root': str(self.catalogue_root(saved.get('input_root'))),
                'output_root': str(self.generated), 'experiment_root': str(self.experimental), 'layout': saved.get('layout', 'single'),
                'storage_endpoint': saved.get('storage_endpoint', os.environ.get('GBR_S3_ENDPOINT', '')),
                'storage_bucket': saved.get('storage_bucket', os.environ.get('GBR_S3_BUCKET', '')),
                'storage_region': saved.get('storage_region', os.environ.get('AWS_DEFAULT_REGION', 'auto')),
                'has_api_key': bool(self.runpod.credential.get()),
                'has_storage_key': bool(self.secrets.get('s3_secret_key') or os.environ.get('AWS_SECRET_ACCESS_KEY')),
                'shared_database_ready': self.shared_ready()}

    def remember(self, data):
        connection = normalize(data['connection']) if data.get('connection') is not None else None
        with self.lock, self.review.config_lock:
            saved = deepcopy(self.review.config.values.get('generation', {}))
            if connection is not None:
                if 'workers' in saved:
                    existing = next((e for e in saved['workers'] if e['id'] == connection_key(connection)), None)
                    if existing is None:
                        saved['workers'].append(self.workers.normalize({'connection': connection}))
                    else:
                        existing['connection'] = connection
                else:
                    connections = [c for c in saved.get('connections', []) if connection_key(c) != connection_key(connection)]
                    saved['connections'] = [*connections, connection]
            saved['input_root'] = str(self.catalogue_root(data.get('input_root', saved.get('input_root'))))
            for key in ('parameters', 'controls', 'layout', 'storage_endpoint', 'storage_bucket', 'storage_region', 'worker_id'):
                if key in data:
                    saved[key] = data[key]
            for key, value in data.get('secrets', {}).items():
                if key not in SECRET_ENV:
                    raise ValueError('Unknown credential field')
                if value:
                    self.secrets[key] = value
            self.review.config.values['generation'] = saved
            self.review.config.save()
        return connection

    def styles(self):
        root = self.root / '_01_writing/_references'
        return root, Styles(root / 'styles.csv')

    def catalogue(self, root=None):
        catalogue = Catalogue(self.catalogue_root(root))
        rows = []
        for item in catalogue.inputs:
            try:
                document = YamlDocument(item.path.read_bytes())
                title = document.values.get('title')
                title = title if isinstance(title, str) else item.path.stem
            except (OSError, ValueError):
                title = item.path.stem
            rows.append({**item.summary(), 'relative': str(item.path.relative_to(catalogue.root)), 'title': title,
                         'album': item.metadata.get('album_name') or '', 'exists': item.path.is_file()})
        return {'root': str(catalogue.root), 'inputs': rows,
                'albums': sorted({row['album'] for row in rows if row['album'] and row['exists']})}

    def preview(self, data):
        if not self.shared_ready():
            raise ValueError('Open GBR through _97_app with the migrated shared database before generating')
        data=deepcopy(data)
        from .purpose import purpose, label
        run_purpose, experiment_label = purpose(data), label(data)
        pool=self.workers.pool() if data.get('worker_id') in {'auto','all'} else None
        worker = None
        if data.get('worker_id'):
            worker, report = pool[0] if pool else self.workers.select(data['worker_id'])
            data['_worker'] = worker
            connection = worker['connection']
            data['connection'] = connection
            if report.get('_inventory'):
                data['_comfy_inventory'] = report['_inventory']
        elif data.get('connection'):
            connection = normalize(data['connection'])
            data['connection'] = connection
        else:
            raise ValueError('Select a saved worker or Automatic')
        catalogue = Catalogue(self.catalogue_root(data.get('input_root')))
        selected = catalogue.select(all_inputs=data.get('all_inputs', False), songs=data.get('songs', []),
            albums=data.get('albums', []), files=data.get('files', []), order=data.get('order', 'selection'))
        rating_filter = data.get('rating_filter')
        rating_evidence = None
        if rating_filter:
            selected, rating_evidence = select_by_rating(selected, self.review.config.database, rating_filter['threshold'],
                reviewer_id=rating_filter.get('reviewer_id'), cluster=rating_filter.get('cluster'), style=rating_filter.get('style'))
            if not selected:
                raise ValueError('No selected inputs meet the saved-rating filter')
        if connection['backend']=='comfyui' and not data.get('_comfy_inventory'):
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_workers_comfyui'))
            from client import Client
            data['_comfy_inventory']=Client(connection['address']).inventory()
        parameters = self.worker_parameters(data,worker,data.get('_comfy_inventory'))
        spec = {'defaults': parameters, 'execution': execution(connection, data.get('encoding_cache', 'prefer')),
                'purpose': run_purpose, 'experiment_label': experiment_label,
                'model_revision': None if connection['backend']=='comfyui' else data.get('model_revision') or SMOKE_MODEL_REVISION}
        for key in ('rerun', 'parent_review_generation_id', 'request_id'):
            if data.get(key):
                spec[key] = data[key]
        if data.get('parameter_priority', 'yaml') == 'form':
            spec['batch'] = parameters
        if data.get('field_precedence'):
            spec['field_precedence'] = data['field_precedence']
        self.parameter_sources(spec,data)
        reference_root, styles = self.styles()
        caption_templates=data.get('caption_templates')
        if caption_templates is not None and data.get('caption_mode')=='random' and not data.get('template_id'):
            if not caption_templates:raise ValueError('Turn on at least one caption cluster or template')
            if set(caption_templates)-set(styles.by_id):raise ValueError('Selected caption templates are no longer in styles.csv; refresh the caption list')
        experiment = data.get('experiments') or {}
        combinations = expand(experiment)
        caption_count = data.get('caption_count', 1)
        if type(caption_count) is not int or not 1 <= caption_count <= 1000:
            raise ValueError('Choose 1 to 1000 caption templates per track')
        caption_mode = data.get('caption_mode')
        if caption_mode not in {None, 'random', 'yaml', 'catalogue'}:
            raise ValueError('Unknown caption selection mode')
        if data.get('template_id') and caption_count != 1 or caption_mode in {'yaml', 'catalogue', None} and caption_count != 1:
            raise ValueError('Several captions require random caption selection')
        if len(selected) * len(combinations) * caption_count > MAX_RUNS:
            raise ValueError('Batch exceeds 10000 generations; select fewer tracks, captions or combinations')
        spec['experiments'] = experiment
        if connection['backend']=='comfyui':
            sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_workers_comfyui'))
            from client import Client
            try:
                if not data.get('_comfy_inventory'):data['_comfy_inventory']=Client(connection['address']).inventory()
            except OSError as error:raise ValueError('Cannot query ComfyUI pod URL: '+str(error)) from error
        checked = []
        for item in selected:
            planned = []
            random_ids = None
            if caption_mode == 'random' and not data.get('template_id'):
                caption_cluster = data.get('caption_cluster') or (rating_filter or {}).get('cluster')
                caption_style = data.get('caption_style') or (rating_filter or {}).get('style')
                caption_pool = [key for key, row in styles.by_id.items()
                        if (caption_templates is None or key in caption_templates)
                        and (not row.get('song_id') or str(row['song_id'])==str(item.song_id))
                        and (not caption_cluster or row['cluster'].casefold() == caption_cluster.strip().casefold())
                        and (not caption_style or row['style_primary'].casefold() == caption_style.strip().casefold())]
                if caption_count > len(caption_pool):
                    raise ValueError(f'Caption pool has {len(caption_pool)} templates; choose fewer captions or broaden the filters')
                random_ids = secrets.SystemRandom().sample(caption_pool, caption_count)
            for caption_index, chosen_template in enumerate(random_ids or [data.get('template_id')]):
                for overrides, combination_index, repeat_index in combinations:
                    if not pool:
                        case=self._preview_case(item, spec, styles, reference_root, data,chosen_template,overrides,caption_index,combination_index,repeat_index)
                    else:
                        candidates=[];errors=[];case=None
                        for entry,report in pool:
                            candidate_data={**data,'connection':entry['connection'],'_worker':entry,'_comfy_inventory':report.get('_inventory')}
                            candidate_spec={**deepcopy(spec),'defaults':self.worker_parameters(candidate_data,entry,report.get('_inventory')),
                                'execution':execution(entry['connection'],data.get('encoding_cache','prefer')),
                                'model_revision':None if entry['connection']['backend']=='comfyui' else data.get('model_revision') or SMOKE_MODEL_REVISION}
                            if data.get('parameter_priority')=='form':candidate_spec['batch']=dict(candidate_spec['defaults'])
                            trial=self._preview_case(item,candidate_spec,styles,reference_root,candidate_data,chosen_template,overrides,caption_index,combination_index,repeat_index)
                            if trial.ready:
                                if not candidates:case=trial
                                candidates.append({'worker_id':entry['id'],'effective':dict(trial.effective),'provenance':dict(trial.provenance),
                                    'execution':candidate_spec['execution'],'model_revision':candidate_spec['model_revision']})
                            else:errors.append(entry['id']+': '+'; '.join(i.message for i in trial.issues if i.severity=='error'));case=case or trial
                        if not candidates:case.issues.append(Issue('no_compatible_worker','No worker can run this combination: '+' | '.join(errors)))
                        case._worker_options=candidates
                    planned.append(case)
            record = planned[0]
            record.planned_runs = [{'effective': dict(case.effective), 'provenance': dict(case.provenance),
                'template': case.template, 'experiment': case._experiment, 'worker_options':getattr(case,'_worker_options',None),
                'worker_default_fields':getattr(case,'_worker_defaults',[])} for case in planned]
            for case in planned[1:]:
                record.issues.extend(issue for issue in case.issues if issue not in record.issues)
            checked.append(record)
        data['_pool']=[e for e,r in pool] if pool else None
        result=self._remember_preview(checked, spec, connection, catalogue, data, rating_filter, rating_evidence)
        result['workers']=[{'id':e['id'],'busy':r['busy']} for e,r in pool] if pool else [{'id':connection_key(connection)}]
        return result

    def worker_parameters(self,data,worker,inventory):
        parameters=dict(DEFAULT_PARAMETERS)
        if data['connection']['backend']=='comfyui' and inventory:
            from client import resolve
            nodes=inventory['nodes'];graph=json.loads((Path(__file__).resolve().parents[2]/'_99_tools/_workers_comfyui/_workflows/music-full.json').read_text(encoding="utf-8"))
            for field,node,key,graph_id in [('text_encoder','CLIPLoader','clip_name','1'),('dit','UNETLoader','unet_name','4'),('sampler','KSampler','sampler_name','6'),('scheduler','KSampler','scheduler','6')]:
                declaration=nodes.get(node,{}).get('input',{}).get('required',{}).get(key,[[]]);values=declaration[0]
                if not isinstance(values,list) or not values:continue
                advertised=declaration[1].get('default') if len(declaration)>1 and isinstance(declaration[1],dict) else None
                preferred=advertised or graph[graph_id]['inputs'][key]
                parameters[field]=resolve(values,None,field,preferred) if field in {'dit','text_encoder'} else preferred if preferred in values else values[0]
        parameters.update({k:v for k,v in (worker or {}).get('defaults',{}).items() if v not in ('',None)})
        parameters.update({k:v for k,v in data.get('parameters',{}).items() if v not in ('',None) and data.get('parameter_sources',{}).get(k)!='worker'})
        return parameters

    @staticmethod
    def parameter_sources(spec,data):
        from .parameters import PARAMETERS
        for field,source in data.get('parameter_sources',{}).items():
            if field not in PARAMETERS:raise ValueError('Unknown parameter source: '+field)
            if source=='control':order=['defaults','preset','template','yaml','batch','rerun']
            elif source=='worker':order=['preset','template','defaults','rerun']
            elif source=='yaml':continue
            else:raise ValueError('Unknown parameter priority')
            spec.setdefault('field_precedence',{})[field]=order
            if source=='control' and field in data.get('parameters',{}):spec.setdefault('batch',{})[field]=data['parameters'][field]

    def _preview_case(self, item, spec, styles, reference_root, data, template_id, overrides, caption_index, combination_index, repeat_index):
            request = deepcopy(spec)
            auto_fields={k for k,v in overrides.items() if v=='auto'}
            explicit = bool(template_id)
            if not template_id and (data.get('caption_mode') == 'catalogue' or data.get('caption_mode') is None and data.get('catalogue_captions', True)):
                try:
                    source = YamlDocument(item.path.read_bytes()).values
                    if not str(source.get('caption') or '').strip() and not source.get('cluster') and not source.get('style'):
                        template_id = item.metadata.get('original_style') or None
                except (OSError, ValueError):
                    pass
            if template_id:
                row = styles.by_id.get(template_id)
                if not row:
                    record = preflight([item], request, styles, reference_root)[0]
                    record.issues.append(Issue('unknown_catalogue_template', f'Caption template {template_id!r} is absent from styles.csv'))
                    record._experiment = {'caption': caption_index, 'combination': combination_index, 'repetition': repeat_index}
                    return record
                request['template_id'] = template_id
                request.setdefault('batch', {}).update(cluster=row['cluster'], style=row['style_primary'])
                if explicit:
                    request.setdefault('field_precedence', {}).update({key: ['defaults', 'preset', 'yaml', 'template', 'batch', 'rerun'] for key in ('caption', 'cluster', 'style')})
            overrides = dict(overrides)
            for key,value in list(overrides.items()):
                if value=='auto' and key not in {'encoder_seed','sampler_seed'}:overrides[key]=spec['defaults'][key]
            if (data.get('experiments') or {}).get('seed_policy') == 'randomize':
                for key in ('encoder_seed', 'sampler_seed'):
                    overrides.setdefault(key, 'auto')
            if overrides:
                request.setdefault('batch', {}).update(overrides)
                request.setdefault('field_precedence', {}).update({key: ['defaults', 'preset', 'yaml', 'template', 'batch', 'rerun'] for key in overrides})
            record = preflight([item], request, styles, reference_root)[0]
            if record.effective.get('model') != MODEL:
                record.issues.append(Issue('unsupported_model', 'This worker currently supports native ' + MODEL + ' only'))
            if data.get('_comfy_inventory') and record.ready:
                from client import Client
                parameters=dict(record.effective)
                for key in ('encoder_seed','sampler_seed'):
                    if parameters.get(key,'auto')=='auto':parameters[key]='0'
                try:Client(data['connection']['address']).prepare({'parameters':parameters,'lyrics':record.document.values.get('lyrics',''),'attempt_id':'preflight'},data['_comfy_inventory'])
                except (ValueError,KeyError) as error:record.issues.append(Issue('comfy_preflight',str(error)))
            record._experiment = {'caption': caption_index, 'combination': combination_index, 'repetition': repeat_index, 'overrides': overrides}
            record._worker_defaults=sorted(k for k,v in record.provenance.items() if k in self.workers.default_fields and (v=='defaults' or k in auto_fields))
            return record

    def _remember_preview(self, checked, spec, connection, catalogue, data, rating_filter, rating_evidence):
        pending_writing = self.writing_publications()
        for record in checked:
            if record.item.path.resolve() in pending_writing:
                record.issues.append(Issue('writing_publication_pending', 'Finish this recipe publication in Writing before generating it'))
        identities = {}
        for record in checked:
            for field in ('cluster', 'style'):
                requested = (rating_filter or {}).get(field)
                values = [case['effective'] for case in record.planned_runs] if record.planned_runs is not None else [record.effective]
                if requested and any(str(value.get(field, '')).strip().casefold() != requested.strip().casefold() for value in values):
                    record.issues.append(Issue('review_target_mismatch', f'Effective {field} differs from the requested review target; choose the matching caption template'))
            identities.setdefault((record.item.song_id, record.item.variant_id), []).append(record)
        for identity, records in identities.items():
            if None not in identity and len(records) > 1:
                for record in records:
                    record.issues.append(Issue('duplicate_selected_id', 'Several selected YAMLs have the same song/variant IDs'))
        now = time.monotonic()
        with self.lock:
            self.previews = {key: value for key, value in self.previews.items() if now - value['created'] < 900}
            if len(self.previews) >= 16:
                del self.previews[next(iter(self.previews))]
            token = uuid.uuid4().hex
            self.previews[token] = {'created': now, 'checked': checked, 'spec': spec, 'connection': connection,
                'worker_id':(data.get('_worker') or {}).get('id',connection_key(connection)),
                'launcher': (data.get('_worker') or {}).get('launcher'),
                'workers':data.get('_pool'),
                'root': catalogue.root, 'layout': data.get('layout', 'single'),
                'selection': {key: data.get(key) for key in ('purpose','experiment_label','worker_id','files', 'all_inputs', 'songs', 'albums', 'order', 'rating_filter', 'parameter_priority', 'template_id', 'catalogue_captions', 'caption_mode', 'caption_count', 'caption_cluster', 'caption_style','caption_templates', 'experiments')}}
            self.previews[token]['selection']['resolved_worker_id']=(data.get('_worker') or {}).get('id',connection_key(connection))
            if rating_evidence is not None:
                self.previews[token]['selection']['review_filter'] = rating_evidence
        return {'token': token, 'inputs': [record.summary() for record in checked], 'ready': sum(record.ready for record in checked),
                'invalid': sum(not record.ready for record in checked),
                'purpose': spec['purpose'], 'experiment_label': spec['experiment_label'],
                'output_root': str(self.experimental if spec['purpose'] == 'experiment' else self.generated),
                'generation_count': sum(len(r.planned_runs or [{}]) for r in checked if r.ready),
                'execution': spec['execution'], 'parameters': spec['defaults'], 'worker_id': self.previews[token]['worker_id']}

    def launch(self, task, credentials=None):
        if str(task.get('worker_id','')).startswith('runpod|'):
            task={**task,'runpod_pod_id':task['worker_id'].split('|',1)[1]}
        folder = self.runtime / 'app_jobs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / 'task.json'
        path.write_text(json_text(task), encoding='utf-8')
        env = python_environment()
        for name, variable in SECRET_ENV.items():
            value = (credentials or {}).get(name) or (self.runpod.credential.get() if name=='api_key' else self.secrets.get(name))
            if value:
                env[variable] = value
        saved = self.review.config.values.get('generation', {})
        for name, variable in [('storage_endpoint', 'GBR_S3_ENDPOINT'), ('storage_bucket', 'GBR_S3_BUCKET'), ('storage_region', 'AWS_DEFAULT_REGION')]:
            if saved.get(name):
                env[variable] = saved[name]
        kwargs = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
        try:
            with (folder / 'controller.log').open('xb') as log:
                process = subprocess.Popen([sys.executable, '-u', str(Path(__file__).with_name('app_jobs.py')), str(path)],
                    stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, **kwargs)
        except OSError as exc:
            (folder / 'state.json').write_text(json_text({'status': 'failed', 'error': str(exc)}), encoding="utf-8")
            raise ValueError('The controller could not start. The frozen batch is retained in Jobs for retry: ' + str(exc)) from None
        self.processes[folder.name] = process
        import psutil
        try:
            (folder / 'process.json').write_text(json_text({'pid': process.pid, 'created': psutil.Process(process.pid).create_time()}), encoding="utf-8")
        except psutil.Error:
            pass
        return {'task_id': folder.name, 'batch_id': task.get('batch_id')}

    def active(self, task_id):
        process = self.processes.get(task_id)
        if process is not None:
            return process.poll() is None
        path = self.runtime / 'app_jobs' / task_id / 'process.json'
        if not path.is_file():
            return False
        import psutil
        try:
            saved = json.loads(path.read_text(encoding="utf-8"))
            process = psutil.Process(saved['pid'])
            return process.is_running() and abs(process.create_time() - saved['created']) < .01
        except (psutil.Error, OSError, ValueError):
            return False

    def start(self, token, skip_errors=False):
        with self.lock:
            preview = self.previews.get(token)
            if not preview or time.monotonic() - preview['created'] >= 900:
                raise ValueError('Generation preview expired; preview the inputs again')
            if any(r.ready and r.item.path.resolve() in self.writing_publications() for r in preview['checked']):
                raise ValueError('A selected recipe is being published in Writing; finish that publication and check the batch again')
            manifest = prepare_batch(preview['checked'], preview['spec'], self.runtime / 'batches', self.review.config.database,
                input_root=preview['root'], error_policy='drop' if skip_errors else 'cancel', selection=preview['selection'],
                output_root=self.experimental if preview['spec'].get('purpose') == 'experiment' else self.generated,
                output_layout=preview['layout'])
            del self.previews[token]
            batch_id = manifest.parent.name
            from .planner import open_database
            from contextlib import closing
            with closing(open_database(self.review.config.database)) as db,db:
                db.execute('BEGIN IMMEDIATE')
                policy=json.loads(db.execute('SELECT policy_json FROM generation_batches WHERE id=?',(batch_id,)).fetchone()[0])
                policy['automatic_workers']=bool(preview.get('workers'))
                db.execute('UPDATE generation_batches SET policy_json=? WHERE id=?',(json_text(policy),batch_id))
            if preview['spec'].get('purpose') != 'experiment':
                self.register_inbox()
            if preview.get('workers'):
                eligible={o['worker_id'] for r in preview['checked'] if r.ready for c in r.planned_runs or [] for o in c.get('worker_options') or []}
                results=[self.launch({'manifest':str(manifest),'batch_id':batch_id,'connection':w['connection'],
                    'launcher':w.get('launcher'),'install':w['connection']['auto_setup'],'worker_id':w['id']}) for w in preview['workers'] if w['id'] in eligible]
                result={'batch_id':batch_id,'tasks':results}
            else:
                result = self.launch({'manifest': str(manifest), 'batch_id': batch_id, 'connection': preview['connection'],
                                      'launcher': preview.get('launcher'),
                                      'install': preview['connection']['auto_setup'],'worker_id':preview['worker_id']})
            self.review.scan_event.set()
            return result

    def tasks(self,include_logs=True):
        from .job_state import read_state
        items = []
        root = self.runtime / 'app_jobs'
        if not root.is_dir():
            return items
        for index,folder in enumerate(sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)):
            if index>=50 and not self.active(folder.name):continue
            if not folder.is_dir() or not (folder / 'task.json').is_file():
                continue
            task = task_paths(json.loads((folder / 'task.json').read_text(encoding="utf-8")))
            state = read_state(folder/'state.json',{'status':'starting'})
            active = self.active(folder.name)
            item = {'id': folder.name, 'connection': task['connection'], 'batch_id': task.get('batch_id'), **state,
                    'active': active, 'manifest': task.get('manifest'),'worker_id':task.get('worker_id'),
                    'runpod_pod_id':task.get('runpod_pod_id') or (str(task['worker_id']).split('|',1)[1] if str(task.get('worker_id','')).startswith('runpod|') else None),
                    'created_at':folder.stat().st_ctime}
            log = folder / 'controller.log'
            if include_logs and log.is_file():
                with log.open('rb') as stream:
                    item['log_end']=log.stat().st_size;item['log_start']=max(0,item['log_end']-24000)
                    stream.seek(item['log_start'])
                    item['log'] = stream.read().decode('utf-8', errors='replace')
            if task.get('batch_id') and self.shared_ready():
                item['runs'] = self.review.db.all('SELECT id,title,status,cluster,style FROM generation_runs WHERE batch_id=? ORDER BY rowid', (task['batch_id'],))
                item['batch_progress']={'complete':sum(r['status']=='succeeded' for r in item['runs']),'total':len(item['runs'])}
                item['outputs'] = self.review.db.all("SELECT a.run_id,a.kind,a.uri FROM generation_artifacts a JOIN generation_runs r ON r.id=a.run_id WHERE r.batch_id=?", (task['batch_id'],))
                attempts_root = Path(task['manifest']).parent / 'attempts'
                completed = self.review.db.all("SELECT a.id FROM generation_attempts a JOIN generation_runs r ON r.id=a.run_id WHERE r.batch_id=? AND r.status='succeeded'", (task['batch_id'],))
                item['cleanup_pending'] = any(attempts_root.glob('*/cleanup-pending.json')) or any((attempts_root / row['id'] / 'audio.flac').is_file() for row in completed)
                if task.get('worker_id'):
                    def owns(attempt_id):
                        try:
                            target=json.loads((attempts_root/attempt_id/'request.json').read_text(encoding="utf-8"))['execution']['diffusion']
                            return target['backend']+'|'+(target.get('address') or '')==connection_key(task['connection'])
                        except (OSError,ValueError,KeyError):return False
                    attempts=self.review.db.all('SELECT a.id,a.run_id FROM generation_attempts a JOIN generation_runs r ON r.id=a.run_id WHERE r.batch_id=? ORDER BY a.started_at DESC,a.rowid DESC',(task['batch_id'],))
                    latest={}
                    for attempt in attempts:latest.setdefault(attempt['run_id'],attempt['id'])
                    frozen=json.loads(Path(task['manifest']).read_text(encoding="utf-8"))
                    from .assignments import runs_for
                    eligible={r['id'] for r in runs_for(task['manifest'],frozen,task['worker_id'])[0]}
                    item['runs']=[r for r in item['runs'] if (owns(latest[r['id']]) if r['id'] in latest else r['id'] in eligible)]
                    item['cleanup_pending']=any(owns(p.parent.name) for p in attempts_root.glob('*/cleanup-pending.json')) or any(owns(r['id']) and (attempts_root/r['id']/'audio.flac').is_file() for r in completed)
                if not active and item['status'] in {'starting', 'running', 'checking', 'preparing','waiting_worker'}:
                    statuses = {run['status'] for run in item['runs']}
                    item['status'] = 'interrupted' if statuses & {'running','publishing','cancel_requested'} else 'succeeded' if statuses == {'succeeded'} else 'stopped'
                    item['reconnect'] = bool(statuses & {'running','cancel_requested'}) and task['connection']['backend'] != 'local'
            report = folder / 'hardware.json'
            if report.is_file():
                item['report'] = json.loads(report.read_text(encoding="utf-8"))
            items.append(item)
        return items

    def task(self, task_id):
        if not re.fullmatch(r'[0-9a-f]{32}', task_id):
            raise ValueError('Invalid generation task')
        folder = self.runtime / 'app_jobs' / task_id
        return folder, task_paths(json.loads((folder / 'task.json').read_text(encoding="utf-8")))

    def action(self, task_id, action):
        if action not in {'pause', 'continue', 'resume', 'retry', 'cleanup'}:
            raise ValueError('Unknown generation action')
        folder, task = self.task(task_id)
        if action == 'pause':
            (folder / 'pause').touch()
            return {'pausing': True}
        if self.active(task_id):
            raise ValueError('This controller is still running; wait or pause after the current song')
        if not task.get('manifest'):
            return self.launch(task)
        (folder / 'pause').unlink(missing_ok=True)
        return self.launch({**task,'worker_id':task.get('worker_id') or connection_key(task['connection']), 'install': bool(task['connection'].get('auto_setup', task.get('install', False))) if action in {'continue', 'retry'} else False,
                            'resume': action == 'resume', 'retry_failed': action == 'retry',
                            'cleanup': action == 'cleanup'})


    def comfy_inventory(self,address):
        sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_99_tools/_workers_comfyui'))
        from client import Client
        try:inventory=Client(address).inventory()
        except OSError as error:raise ValueError('Cannot query ComfyUI pod URL: '+str(error)) from error
        return self.comfy_choices(inventory)

    def writing_publications(self):
        if not self.review.db.one("SELECT 1 FROM sqlite_master WHERE name='writing_tasks'"):return set()
        return {Path(json.loads(r['publication_json'])['target']).resolve() for r in self.review.db.all("SELECT publication_json FROM writing_tasks WHERE status='publishing'")}

    @staticmethod
    def native_choices():
        from _gbr_worker.contract import SAMPLERS, SCHEDULES
        return {'text_encoders': ['bf16', 'fp32'], 'diffusion_models': ['bf16', 'fp32'],
                'samplers': sorted(SAMPLERS), 'schedulers': sorted(SCHEDULES), 'vaes': []}

    @staticmethod
    def comfy_choices(inventory):
        nodes=inventory['nodes']
        def options(node,field):return nodes.get(node,{}).get('input',{}).get('required',{}).get(field,[[]])[0]
        def relevant(node, field):
            values = options(node, field)
            return [v for v in values if isinstance(v, str) and 'minimax' in re.sub(r'[^a-z0-9]', '', v.casefold())] if isinstance(values, list) else []
        models = {'text_encoders': relevant('CLIPLoader','clip_name'), 'diffusion_models': relevant('UNETLoader','unet_name'),
                  'vaes': relevant('VAELoader','vae_name'), 'samplers': options('KSampler','sampler_name'), 'schedulers': options('KSampler','scheduler')}
        graph = json.loads((Path(__file__).resolve().parents[2]/'_99_tools/_workers_comfyui/_workflows/music-full.json').read_text(encoding="utf-8"))
        missing = sorted({n['class_type'] for n in graph.values()} - set(nodes))
        devices = inventory['system'].get('devices', [])
        device = devices[0] if devices else {}
        return {'system': inventory['system'], **models, 'models': models, 'missing_nodes': missing,
                'capabilities': inventory.get('capabilities', {}),
                'hardware': {'gpu_name': device.get('name'), 'usable_vram_bytes': device.get('vram_total'),
                             'free_vram_bytes_at_start': device.get('vram_free'), 'devices': devices}}
