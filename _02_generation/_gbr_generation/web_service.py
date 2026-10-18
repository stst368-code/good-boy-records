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
from _gbr_worker.contract import MODEL, SMOKE_MODEL_REVISION

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

    @property
    def runtime(self):
        return self.root / '_02_generation' / '_runtime'

    @property
    def generated(self):
        return self.root / '_02_generation' / '_generated'

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
        connections = saved.get('connections', [])
        if not connections:
            connections = [normalize({'backend': 'local'})]
            latest = self.runtime / 'local_test/latest.json'
            try:
                manifest = verify_plan(relocated_path(json.loads(latest.read_text())['manifest']))
                target = manifest['runs'][0]['execution']['diffusion']
                if target['backend'] != 'local':
                    connections.append(normalize({'backend': target['backend'], 'address': target['address'],
                                                   'identity_file': keys[0] if keys else ''}))
            except (OSError, ValueError, KeyError):
                pass
        return {'connections': connections, 'key_candidates': keys, 'parameters': saved.get('parameters', DEFAULT_PARAMETERS),
                'input_root': str(self.catalogue_root(saved.get('input_root'))),
                'output_root': str(self.generated), 'layout': saved.get('layout', 'single'),
                'storage_endpoint': saved.get('storage_endpoint', os.environ.get('GBR_S3_ENDPOINT', '')),
                'storage_bucket': saved.get('storage_bucket', os.environ.get('GBR_S3_BUCKET', '')),
                'storage_region': saved.get('storage_region', os.environ.get('AWS_DEFAULT_REGION', 'auto')),
                'has_api_key': bool(self.secrets.get('api_key') or os.environ.get('RUNPOD_API_KEY')),
                'has_storage_key': bool(self.secrets.get('s3_secret_key') or os.environ.get('AWS_SECRET_ACCESS_KEY')),
                'shared_database_ready': self.shared_ready()}

    def remember(self, data):
        connection = normalize(data['connection'])
        with self.lock, self.review.config_lock:
            saved = deepcopy(self.review.config.values.get('generation', {}))
            connections = [c for c in saved.get('connections', []) if connection_key(c) != connection_key(connection)]
            saved['connections'] = [*connections, connection]
            saved['input_root'] = str(self.catalogue_root(data.get('input_root', saved.get('input_root'))))
            for key in ('parameters', 'layout', 'storage_endpoint', 'storage_bucket', 'storage_region'):
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
                'albums': sorted({row['album'] for row in rows if row['album']})}

    def preview(self, data):
        if not self.shared_ready():
            raise ValueError('Open GBR through _97_app with the migrated shared database before generating')
        connection = normalize(data['connection'])
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
        parameters = {**DEFAULT_PARAMETERS, **data.get('parameters', {})}
        spec = {'defaults': parameters, 'execution': execution(connection, data.get('encoding_cache', 'prefer')),
                'model_revision': data.get('model_revision') or SMOKE_MODEL_REVISION}
        for key in ('rerun', 'parent_review_generation_id', 'request_id'):
            if data.get(key):
                spec[key] = data[key]
        if data.get('parameter_priority', 'yaml') == 'form':
            spec['batch'] = parameters
        if data.get('field_precedence'):
            spec['field_precedence'] = data['field_precedence']
        reference_root, styles = self.styles()
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
        checked = []
        for item in selected:
            planned = []
            random_ids = None
            if caption_mode == 'random' and not data.get('template_id'):
                caption_cluster = data.get('caption_cluster') or (rating_filter or {}).get('cluster')
                caption_style = data.get('caption_style') or (rating_filter or {}).get('style')
                pool = [key for key, row in styles.by_id.items()
                        if (not caption_cluster or row['cluster'].casefold() == caption_cluster.strip().casefold())
                        and (not caption_style or row['style_primary'].casefold() == caption_style.strip().casefold())]
                if caption_count > len(pool):
                    raise ValueError(f'Caption pool has {len(pool)} templates; choose fewer captions or broaden the filters')
                random_ids = secrets.SystemRandom().sample(pool, caption_count)
            for caption_index, chosen_template in enumerate(random_ids or [data.get('template_id')]):
                for overrides, combination_index, repeat_index in combinations:
                    planned.append(self._preview_case(item, spec, styles, reference_root, data,
                        chosen_template, overrides, caption_index, combination_index, repeat_index))
            record = planned[0]
            record.planned_runs = [{'effective': dict(case.effective), 'provenance': dict(case.provenance),
                'template': case.template, 'experiment': case._experiment} for case in planned]
            for case in planned[1:]:
                record.issues.extend(issue for issue in case.issues if issue not in record.issues)
            checked.append(record)
        return self._remember_preview(checked, spec, connection, catalogue, data, rating_filter, rating_evidence)

    def _preview_case(self, item, spec, styles, reference_root, data, template_id, overrides, caption_index, combination_index, repeat_index):
            request = deepcopy(spec)
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
            if (data.get('experiments') or {}).get('seed_policy') == 'randomize':
                for key in ('encoder_seed', 'sampler_seed'):
                    overrides.setdefault(key, 'auto')
            if overrides:
                request.setdefault('batch', {}).update(overrides)
                request.setdefault('field_precedence', {}).update({key: ['defaults', 'preset', 'yaml', 'template', 'batch', 'rerun'] for key in overrides})
            record = preflight([item], request, styles, reference_root)[0]
            if record.effective.get('model') != MODEL:
                record.issues.append(Issue('unsupported_model', 'This worker currently supports native ' + MODEL + ' only'))
            record._experiment = {'caption': caption_index, 'combination': combination_index, 'repetition': repeat_index, 'overrides': overrides}
            return record

    def _remember_preview(self, checked, spec, connection, catalogue, data, rating_filter, rating_evidence):
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
                'root': catalogue.root, 'layout': data.get('layout', 'single'),
                'selection': {key: data.get(key) for key in ('files', 'all_inputs', 'songs', 'albums', 'order', 'rating_filter', 'parameter_priority', 'template_id', 'catalogue_captions', 'caption_mode', 'caption_count', 'caption_cluster', 'caption_style', 'experiments')}}
            if rating_evidence is not None:
                self.previews[token]['selection']['review_filter'] = rating_evidence
        return {'token': token, 'inputs': [record.summary() for record in checked], 'ready': sum(record.ready for record in checked),
                'invalid': sum(not record.ready for record in checked), 'output_root': str(self.generated),
                'generation_count': sum(len(r.planned_runs or [{}]) for r in checked if r.ready),
                'execution': spec['execution'], 'parameters': spec['defaults']}

    def launch(self, task, credentials=None):
        folder = self.runtime / 'app_jobs' / uuid.uuid4().hex
        folder.mkdir(parents=True)
        path = folder / 'task.json'
        path.write_text(json_text(task), encoding='utf-8')
        env = os.environ.copy()
        for name, variable in SECRET_ENV.items():
            value = (credentials or {}).get(name) or self.secrets.get(name)
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
            (folder / 'state.json').write_text(json_text({'status': 'failed', 'error': str(exc)}))
            raise ValueError('The controller could not start. The frozen batch is retained in Jobs for retry: ' + str(exc)) from None
        self.processes[folder.name] = process
        import psutil
        try:
            (folder / 'process.json').write_text(json_text({'pid': process.pid, 'created': psutil.Process(process.pid).create_time()}))
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
            saved = json.loads(path.read_text())
            process = psutil.Process(saved['pid'])
            return process.is_running() and abs(process.create_time() - saved['created']) < .01
        except (psutil.Error, OSError, ValueError):
            return False

    def start(self, token, skip_errors=False):
        with self.lock:
            preview = self.previews.get(token)
            if not preview or time.monotonic() - preview['created'] >= 900:
                raise ValueError('Generation preview expired; preview the inputs again')
            manifest = prepare_batch(preview['checked'], preview['spec'], self.runtime / 'batches', self.review.config.database,
                input_root=preview['root'], error_policy='drop' if skip_errors else 'cancel', selection=preview['selection'],
                output_root=self.generated, output_layout=preview['layout'])
            del self.previews[token]
            batch_id = manifest.parent.name
            with self.review.config_lock:
                directories = self.review.config.values['input_directories']
                if str(self.generated.resolve()) not in {str(p) for p in self.review.config.inputs}:
                    self.review.config.values['input_directories'] = [*directories, str(self.generated.resolve())]
                    self.review.config.validate()
                    self.review.config.save()
            result = self.launch({'manifest': str(manifest), 'batch_id': batch_id, 'connection': preview['connection'],
                                  'install': preview['connection']['auto_setup']})
            self.review.scan_event.set()
            return result

    def tasks(self):
        items = []
        root = self.runtime / 'app_jobs'
        if not root.is_dir():
            return items
        for folder in sorted(root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            if not folder.is_dir() or not (folder / 'task.json').is_file():
                continue
            task = task_paths(json.loads((folder / 'task.json').read_text()))
            state = json.loads((folder / 'state.json').read_text()) if (folder / 'state.json').is_file() else {'status': 'starting'}
            active = self.active(folder.name)
            item = {'id': folder.name, 'connection': task['connection'], 'batch_id': task.get('batch_id'), **state,
                    'active': active, 'manifest': task.get('manifest')}
            log = folder / 'controller.log'
            if log.is_file():
                with log.open('rb') as stream:
                    stream.seek(max(0, log.stat().st_size - 24000))
                    item['log'] = stream.read().decode('utf-8', errors='replace')
            if task.get('batch_id') and self.shared_ready():
                item['runs'] = self.review.db.all('SELECT id,title,status,cluster,style FROM generation_runs WHERE batch_id=? ORDER BY rowid', (task['batch_id'],))
                item['outputs'] = self.review.db.all("SELECT a.run_id,a.kind,a.uri FROM generation_artifacts a JOIN generation_runs r ON r.id=a.run_id WHERE r.batch_id=?", (task['batch_id'],))
                attempts_root = Path(task['manifest']).parent / 'attempts'
                completed = self.review.db.all("SELECT a.id FROM generation_attempts a JOIN generation_runs r ON r.id=a.run_id WHERE r.batch_id=? AND r.status='succeeded'", (task['batch_id'],))
                item['cleanup_pending'] = any(attempts_root.glob('*/cleanup-pending.json')) or any((attempts_root / row['id'] / 'audio.flac').is_file() for row in completed)
                if not active and item['status'] in {'starting', 'running', 'checking', 'preparing'}:
                    statuses = {run['status'] for run in item['runs']}
                    item['status'] = 'interrupted' if 'running' in statuses else 'succeeded' if statuses == {'succeeded'} else 'stopped'
                    item['reconnect'] = 'running' in statuses and task['connection']['backend'] != 'local'
            report = folder / 'hardware.json'
            if report.is_file():
                item['report'] = json.loads(report.read_text())
            items.append(item)
        return items

    def task(self, task_id):
        if not re.fullmatch(r'[0-9a-f]{32}', task_id):
            raise ValueError('Invalid generation task')
        folder = self.runtime / 'app_jobs' / task_id
        return folder, task_paths(json.loads((folder / 'task.json').read_text()))

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
        return self.launch({**task, 'install': bool(task['connection'].get('auto_setup', task.get('install', False))) if action in {'continue', 'retry'} else False,
                            'resume': action == 'resume', 'retry_failed': action == 'retry',
                            'cleanup': action == 'cleanup'})
