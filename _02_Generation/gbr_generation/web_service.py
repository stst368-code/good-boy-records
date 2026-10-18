"""Selection, immutable batch previews and supervised app jobs on the existing shared DB."""
from copy import deepcopy
import json
import os
from pathlib import Path
import re
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
from gbr_worker.contract import MODEL, SMOKE_MODEL_REVISION

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
        return self.root / '_02_Generation' / 'runtime'

    @property
    def generated(self):
        return self.root / '_02_Generation' / 'generated'

    def shared_ready(self):
        return bool(self.review.db.one("SELECT 1 FROM sqlite_master WHERE name='platform_migration_report'")
                    and self.review.db.one('SELECT 1 FROM platform_migration_report WHERE id=1'))

    def preferences(self):
        saved = deepcopy(self.review.config.values.get('generation', {}))
        keys = key_candidates()
        connections = saved.get('connections', [])
        if not connections:
            connections = [normalize({'backend': 'local'})]
            latest = self.runtime / 'local_test/latest.json'
            try:
                manifest = verify_plan(json.loads(latest.read_text())['manifest'])
                target = manifest['runs'][0]['execution']['diffusion']
                if target['backend'] != 'local':
                    connections.append(normalize({'backend': target['backend'], 'address': target['address'],
                                                   'identity_file': keys[0] if keys else ''}))
            except (OSError, ValueError, KeyError):
                pass
        return {'connections': connections, 'key_candidates': keys, 'parameters': saved.get('parameters', DEFAULT_PARAMETERS),
                'input_root': saved.get('input_root', str(self.root / '_01_Writing/_Built')),
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
            for key in ('input_root', 'parameters', 'layout', 'storage_endpoint', 'storage_bucket', 'storage_region'):
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
        root = self.root / '_01_Writing/_references'
        return root, Styles(root / 'styles.csv')

    def catalogue(self, root=None):
        catalogue = Catalogue(root or self.root / '_01_Writing/_Built')
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
            raise ValueError('Open GBR through _97_App with the migrated shared database before generating')
        connection = normalize(data['connection'])
        catalogue = Catalogue(data['input_root'])
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
        checked = []
        for item in selected:
            request = deepcopy(spec)
            template_id = data.get('template_id')
            explicit = bool(template_id)
            if not template_id and data.get('catalogue_captions', True):
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
                    checked.append(record)
                    continue
                request['template_id'] = template_id
                request.setdefault('batch', {}).update(cluster=row['cluster'], style=row['style_primary'])
                if explicit:
                    request.setdefault('field_precedence', {}).update({key: ['defaults', 'preset', 'yaml', 'template', 'batch', 'rerun'] for key in ('caption', 'cluster', 'style')})
            checked.extend(preflight([item], request, styles, reference_root))
        identities = {}
        for record in checked:
            for field in ('cluster', 'style'):
                requested = (rating_filter or {}).get(field)
                if requested and str(record.effective.get(field, '')).strip().casefold() != requested.strip().casefold():
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
                'selection': {key: data.get(key) for key in ('files', 'all_inputs', 'songs', 'albums', 'order', 'rating_filter', 'parameter_priority', 'template_id', 'catalogue_captions')}}
            if rating_evidence is not None:
                self.previews[token]['selection']['review_filter'] = rating_evidence
        return {'token': token, 'inputs': [record.summary() for record in checked], 'ready': sum(record.ready for record in checked),
                'invalid': sum(not record.ready for record in checked), 'output_root': str(self.generated),
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
            task = json.loads((folder / 'task.json').read_text())
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
                item['cleanup_pending'] = any(Path(task['manifest']).parent.glob('attempts/*/cleanup-pending.json'))
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
        return folder, json.loads((folder / 'task.json').read_text())

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
        return self.launch({**task, 'install': False, 'resume': action == 'resume', 'retry_failed': action == 'retry',
                            'cleanup': action == 'cleanup'})
