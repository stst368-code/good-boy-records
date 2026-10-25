"""Publish controlled listening sets through the existing R2 configuration/ledger.

These are public teaching examples, never distribution selections or review rows.
One generation_artifacts row journals each publication; input runs and telemetry
remain the canonical records. Immutable public objects survive local cleanup.
"""
from fractions import Fraction
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
from urllib.parse import urlparse

from .experiment_outputs import observations
from .executor import now
from .planner import json_text
from .purpose import purpose

POINTER = 'gbr/v2/experiments/catalogue.json'
KIND = 'experiment_publication'
PARAMETERS = {'sampler_steps': 'Sampling steps', 'sampler_cfg': 'Diffusion CFG',
              'encoder_cfg': 'Encoder CFG', 'top_k': 'Top-k', 'duration': 'Maximum seconds'}
PUBLIC_CONTROLS = ('model', 'text_encoder', 'dit', 'encoder_cfg', 'encoder_seed', 'top_k',
                   'sampler', 'scheduler', 'sampler_cfg', 'sampler_seed', 'sampler_steps', 'duration')


def encoded(value):
    return (json_text(value)).encode('utf-8')


def digest(path):
    from _gbr_worker.contract import file_sha
    return file_sha(path)


def grid(values):
    values = sorted(set(values))
    if len(values) < 2 or any(type(v) not in {int, float} or not math.isfinite(v) or abs(v) > 2**53-1 for v in values):
        raise ValueError('A comparison needs at least two distinct numeric settings')
    fractions = [Fraction(str(v)) for v in values]
    denominator = math.lcm(*(f.denominator for f in fractions))
    numbers = [int(f * denominator) for f in fractions]
    step = math.gcd(*(b-a for a, b in zip(numbers, numbers[1:])))
    if (numbers[-1]-numbers[0]) // step > 5000:
        raise ValueError('Comparison values do not form a usable parameter grid')
    return {'min': values[0], 'max': values[-1], 'step': float(Fraction(step, denominator))}


def public_controls(parameters):
    controls = {k: parameters[k] for k in PUBLIC_CONTROLS if k in parameters}
    for key in ('model', 'text_encoder', 'dit'):
        if key in controls:
            controls[key] = str(controls[key]).replace('\\', '/').rsplit('/', 1)[-1]
    return controls


class ExperimentPublisher:
    def __init__(self, generation, publisher):
        self.g, self.publisher, self.db = generation, publisher, generation.review.db
        self.lock = threading.RLock()
        if self.g.shared_ready():
            for row in self.db.all('SELECT id,metadata_json FROM generation_artifacts WHERE kind=?', (KIND,)):
                data = json.loads(row['metadata_json'])
                if data['status'] in {'queued', 'running'}:
                    data.update(status='interrupted', error='Application restarted; retry to reconcile R2 publication')
                    self.save(row['id'], data)

    @property
    def runtime(self):
        return self.g.runtime / 'experiment_publications'

    def save(self, identifier, data):
        self.db.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=? AND kind=?', (json_text(data), identifier, KIND))

    def row(self, identifier):
        if not re.fullmatch('[0-9a-f]{32}', identifier):
            raise ValueError('Invalid experiment publication ID')
        row = self.db.one('SELECT * FROM generation_artifacts WHERE id=? AND kind=?', (identifier, KIND))
        if not row:
            raise ValueError('Unknown experiment publication')
        return row, json.loads(row['metadata_json'])

    def listing(self, batch_id):
        if not self.g.shared_ready():
            return []
        rows = self.db.all('''SELECT id,metadata_json FROM generation_artifacts
            WHERE kind=? AND json_extract(metadata_json,'$.batch_id')=? ORDER BY rowid DESC LIMIT 30''', (KIND, batch_id))
        return [{'id': row['id'], **{k: data.get(k) for k in ('title', 'status', 'error', 'progress', 'url', 'sample_count', 'created_at', 'public_base')}}
                for row in rows for data in [json.loads(row['metadata_json'])]]

    def targets(self):
        import yaml
        source = Path(__file__).resolve().parents[2] / '_96_website/_content/_how_its_made/tour.yaml'
        payload = yaml.safe_load(source.read_text(encoding='utf-8-sig'))
        return [{'id': b['published_slot'], 'title': b['title'], 'parameter': b['parameter_field'], 'stage': s['id'], 'node': n['id']}
                for s in payload['stages'] for n in s['nodes'] for b in n.get('body', [])
                if b.get('type') == 'experiment' and b.get('published_slot') and b.get('parameter_field') in PARAMETERS]

    def groups(self, batch_id):
        from .jobs import Jobs
        _, manifest = Jobs(self.g).batch(batch_id)
        if manifest.get('purpose') != 'experiment':
            raise ValueError('Choose an experiment batch')
        repetitions = {r['id']: (r.get('experiment') or {}).get('repetition', 0) for r in manifest['runs']}
        runs = self.db.all('''SELECT r.*,a.id AS audio_artifact,a.uri,a.sha256,a.size_bytes,a.metadata_json,
            e.model_revision FROM generation_runs r JOIN generation_artifacts a ON a.run_id=r.id AND a.kind='audio'
            JOIN generation_execution_state e ON e.run_id=r.id WHERE r.batch_id=? AND r.status='succeeded'
            AND NOT EXISTS(SELECT 1 FROM generation_attempts x WHERE x.run_id=r.id AND x.status='running') ORDER BY r.rowid''', (batch_id,))
        candidates = []
        for run in runs:
            state = json.loads(run['metadata_json']).get('experiment_output', {}).get('state', 'saved')
            if state not in {'saved', 'promoted'} or not Path(run['uri']).is_file():
                continue
            if purpose(json.loads(run['requested_json'])) != 'experiment':
                continue
            candidates.append(run)
        axes = []
        for parameter, label in PARAMETERS.items():
            groups = {}
            for run in candidates:
                parameters = json.loads(run['effective_json'])
                value = parameters.pop(parameter, None)
                if type(value) not in {int, float} or not math.isfinite(value):
                    continue
                key = hashlib.sha256(encoded({'source': run['source_sha256'], 'controls': parameters,
                    'revision': run['model_revision'], 'repetition': repetitions.get(run['id'], 0)})).hexdigest()
                group = groups.setdefault(key, {'id': key, 'controls': public_controls(parameters), 'runs': [], 'values': [],
                    'model_revision': run['model_revision']})
                group['runs'].append(run); group['values'].append(value)
            offered = []
            for group in groups.values():
                # Never silently choose between repeated recordings at the same setting.
                if len(group['values']) != len(set(group['values'])):
                    continue
                try:
                    bounds = grid(group['values'])
                except ValueError:
                    continue
                offered.append({**group, **bounds})
            if offered:
                axes.append({'id': parameter, 'label': label, 'groups': offered})
        return axes

    def options(self, batch_id):
        axes = self.groups(batch_id)
        for axis in axes:
            axis['groups'] = [{k: v for k, v in group.items() if k not in {'runs', 'values'}} |
                {'count': len(group['runs']), 'bytes': sum(r['size_bytes'] or 0 for r in group['runs'])} for group in axis['groups']]
        return {'parameters': axes, 'targets': self.targets(), 'r2': self.publisher.configuration()['configured']}

    def start(self, batch_id, options):
        parameter, group_id = options['parameter'], options['group']
        axis = next((a for a in self.groups(batch_id) if a['id'] == parameter), None)
        group = next((g for g in (axis or {}).get('groups', []) if g['id'] == group_id), None)
        if not group:
            raise ValueError('Select a matched set with at least two completed settings. Keep other controls and seeds fixed.')
        title = options['title'].strip()
        if not title or len(title) > 160 or options.get('format', 'flac') not in {'flac', 'mp3'}:
            raise ValueError('Enter a public title and choose FLAC or MP3')
        slot = options.get('slot') or ''
        target = next((t for t in self.targets() if t['id'] == slot and t['parameter'] == parameter), None)
        if slot and not target:
            raise ValueError('This website position does not match the varied parameter')
        self.publisher.store()  # Validate the existing R2 configuration before queuing anything.
        settings = self.publisher.settings()
        set_id = 'experiment-' + batch_id + '-' + parameter.replace('_', '-') + '-' + group_id[:12]
        with self.g.lock, self.lock:
            for row in self.db.all("SELECT id,metadata_json FROM generation_artifacts WHERE kind=? AND json_extract(metadata_json,'$.set_id')=?", (KIND, set_id)):
                previous = json.loads(row['metadata_json'])
                if previous['status'] in {'queued', 'running'}:
                    return {'id': row['id']}
            identifier = uuid.uuid4().hex
            bounds = {k: group[k] for k in ('min', 'max', 'step')}
            inputs = [{'run_id': r['id'], 'artifact_id': r['audio_artifact'], 'sha256': r['sha256'],
                'value': json.loads(r['effective_json'])[parameter]} for r in group['runs']]
            data = {'batch_id': batch_id, 'set_id': set_id, 'title': title, 'status': 'queued', 'created_at': now(),
                'sample_count': len(inputs), 'options': {**options, 'slot': slot, 'format': options.get('format', 'flac')},
                'public_base': settings['public_base'], 'destination': {k: settings[k] for k in ('bucket', 'endpoint', 'public_base', 'website_url')},
                'recipe': {'inputs': inputs, 'controls': group['controls'], 'bounds': bounds, 'revision': group['model_revision'],
                    'route': '#' + (target or {}).get('stage', 'generation') + '/' + (target or {}).get('node', 'sampling')},
                'files': [], 'progress': {'phase': 'queued', 'completed': 0, 'total': len(inputs)}, 'error': None}
            self.db.execute('INSERT INTO generation_artifacts(id,run_id,kind,uri,metadata_json) VALUES(?,NULL,?,?,?)',
                (identifier, KIND, 'r2://' + settings['bucket'] + '/' + POINTER, json_text(data)))
            self.publisher.pool.submit(self.publish, identifier)
            return {'id': identifier}

    def retry(self, identifier):
        with self.lock:
            _, data = self.row(identifier)
            if data['status'] in {'queued', 'running'}:
                return {'id': identifier}
            if data['status'] not in {'failed', 'interrupted'}:
                raise ValueError('Only a failed or interrupted comparison needs a retry')
            self.publisher.store()
            data.update(status='queued', error=None)
            self.save(identifier, data)
            self.publisher.pool.submit(self.publish, identifier)
        return {'id': identifier}

    def withdraw(self, identifier):
        with self.lock:
            _, data = self.row(identifier)
            if data['status'] not in {'published', 'withdrawn'}:
                raise ValueError('Only a published comparison can be removed from the website')
            settings = self.publisher.settings()
            if any(settings[k] != v for k, v in data['destination'].items()):
                raise ValueError('R2 destination changed; restore it before withdrawing this comparison')
            store = self.publisher.store()
            raw, etag = store.read(POINTER)
            if raw:
                catalogue = json.loads(raw)
                if catalogue.get('format') != 'gbr-public-experiments-v1' or not isinstance(catalogue.get('sets'), list):
                    raise ValueError('Unknown experiment catalogue format; nothing replaced')
                current = next((s for s in catalogue['sets'] if s.get('id') == data['set_id']), None)
                if current and current.get('url') != data.get('descriptor_url'):
                    raise ValueError('A newer edition is live; remove that edition instead')
                if current:
                    catalogue['sets'] = [s for s in catalogue['sets'] if s.get('id') != data['set_id']]
                    catalogue['updated_at'] = now()
                    store.compare_put(POINTER, encoded(catalogue), etag=etag)
            data.update(status='withdrawn', withdrawn_at=now(), progress={'phase': 'withdrawn'})
            self.save(identifier, data)
        return {'id': identifier, 'status': 'withdrawn'}

    def prepare_audio(self, source, destination, format):
        if format == 'flac':
            from _gbr_preparation.publishing import public_flac
            public_flac(source, destination)
        else:
            ffmpeg = shutil.which('ffmpeg')
            if not ffmpeg:
                raise ValueError('FFmpeg is required for MP3 comparisons; lossless FLAC publication needs no conversion')
            result = subprocess.run([ffmpeg, '-nostdin', '-v', 'error', '-y', '-i', str(source), '-map', '0:a:0',
                '-map_metadata', '-1', '-map_chapters', '-1', '-c:a', 'libmp3lame', '-q:a', '2', '-ar', '44100', '-ac', '2',
                '-f', 'mp3', str(destination)], capture_output=True, text=True, timeout=900, encoding="utf-8", errors="replace")
            if result.returncode:
                raise ValueError('Comparison audio encoding failed: ' + result.stderr[-1200:])

    def public_sample(self, item, public_base):
        attempts = observations(self.db, item['run_id'])
        actual = next((a for a in reversed(attempts) if a['status'] == 'succeeded'), {})
        measurements = {k: actual.get(k) for k in ('wall_seconds', 'conditioning_seconds', 'diffusion_seconds',
            'comfy_execution_seconds', 'peak_allocated_bytes', 'peak_reserved_bytes', 'peak_observed_device_bytes',
            'gpu_name', 'usable_vram_bytes', 'execution_mode', 'cache_status', 'estimated_cost', 'currency')}
        return {'value': item['value'], 'audio': public_base + '/' + item['key'],
                'run_id': item['run_id'], 'measurements': measurements}

    def publish(self, identifier):
        from _gbr_preparation.publishing import error_text
        _, data = self.row(identifier)
        directory = self.runtime / identifier
        try:
            settings = self.publisher.settings()
            if any(settings[k] != v for k, v in data['destination'].items()):
                raise ValueError('R2 destination changed; restore it before retrying this publication')
            store = self.publisher.store()
            data.update(status='running', error=None)
            self.save(identifier, data)
            directory.mkdir(parents=True, exist_ok=True)
            for index, source in enumerate(data['recipe']['inputs']):
                item = next((f for f in data['files'] if f['run_id'] == source['run_id']), None)
                if item is None:
                    row = self.db.one('SELECT uri,sha256 FROM generation_artifacts WHERE id=? AND run_id=? AND kind=\'audio\'',
                                      (source['artifact_id'], source['run_id']))
                    if not row or row['sha256'] != source['sha256']:
                        raise ValueError('Selected source recording changed; publish a new comparison')
                    path = Path(row['uri'])
                    if path.is_symlink() or digest(path) != source['sha256']:
                        raise ValueError('Selected source file changed; original retained')
                    format = data['options']['format']
                    output = directory / (source['run_id'] + '.' + format)
                    part = output.with_suffix(output.suffix + '.part')
                    if part.is_symlink() or output.is_symlink():
                        raise ValueError('Unexpected symlink in publication scratch')
                    part.unlink(missing_ok=True)
                    self.prepare_audio(path, part, format)
                    if digest(path) != source['sha256']:
                        raise ValueError('Source audio changed while preparing the public copy; nothing uploaded')
                    part.replace(output)
                    sha = digest(output)
                    item = {**source, 'path': str(output), 'sha256': sha, 'size': output.stat().st_size,
                        'key': 'gbr/v2/experiments/objects/' + sha[:2] + '/' + sha + '.' + format,
                        'content_type': 'audio/flac' if format == 'flac' else 'audio/mpeg'}
                    data['files'].append(item)
                    self.save(identifier, data)
                if not item.get('uploaded'):
                    data['progress'] = {'phase': 'uploading', 'completed': index, 'total': data['sample_count']}
                    self.save(identifier, data)
                    store.upload(item)
                    origin = urlparse(settings['website_url']); origin = origin.scheme + '://' + origin.netloc
                    store.public_check(settings['public_base'] + '/' + item['key'], origin)
                    item['uploaded'] = True
                    self.save(identifier, data)
                else:
                    store.verify(item)
                # A verified public object replaces this disposable render immediately.
                temporary = Path(item['path'])
                if temporary.exists():
                    if temporary.is_symlink() or digest(temporary) != item['sha256']:
                        raise ValueError('Temporary comparison file changed; retained')
                    temporary.unlink()
            controls = data['recipe']['controls']
            conditions = 'Caption and lyrics fixed. ' + ' · '.join(k.replace('_', ' ') + ': ' + str(v) for k, v in controls.items())
            descriptor = data.get('descriptor') or {'format': 'gbr-public-experiment-v1', 'id': data['set_id'], 'title': data['title'],
                'slot': data['options']['slot'], 'parameter_field': data['options']['parameter'],
                'parameter': PARAMETERS[data['options']['parameter']], **data['recipe']['bounds'], 'conditions': conditions,
                'controls': controls, 'model_revision': data['recipe']['revision'], 'created_at': data['created_at'],
                'audio_format': data['options']['format'],
                'samples': sorted([self.public_sample(item, settings['public_base']) for item in data['files']], key=lambda x: x['value'])}
            data['descriptor'] = descriptor
            self.save(identifier, data)
            raw = encoded(descriptor); sha = hashlib.sha256(raw).hexdigest()
            key = 'gbr/v2/experiments/sets/' + data['set_id'] + '/' + sha + '.json'
            existing, _ = store.read(key)
            if existing is None:
                try:
                    store.compare_put(key, raw, immutable=True)
                except Exception:
                    if store.read(key)[0] != raw:
                        raise
            elif existing != raw:
                raise ValueError('Immutable experiment manifest conflicts with existing data')
            origin = urlparse(settings['website_url']); origin = origin.scheme + '://' + origin.netloc
            store.public_check(settings['public_base'] + '/' + key, origin)
            raw_catalogue, etag = store.read(POINTER)
            catalogue = json.loads(raw_catalogue) if raw_catalogue else {'format': 'gbr-public-experiments-v1', 'sets': []}
            if catalogue.get('format') != 'gbr-public-experiments-v1' or not isinstance(catalogue.get('sets'), list):
                raise ValueError('Unknown experiment catalogue format; nothing replaced')
            entry = {'id': data['set_id'], 'slot': data['options']['slot'], 'parameter_field': data['options']['parameter'],
                     'title': data['title'], 'sample_count': data['sample_count'], 'url': settings['public_base'] + '/' + key, 'sha256': sha}
            if next((s for s in catalogue['sets'] if s.get('id') == data['set_id']), None) != entry:
                catalogue['sets'] = [s for s in catalogue['sets'] if s.get('id') != data['set_id']] + [entry]
                catalogue['updated_at'] = now()
                payload = encoded(catalogue)
                if len(payload) > 8 * 1024**2:
                    raise ValueError('Public experiment catalogue exceeds 8 MiB')
                try:
                    store.compare_put(POINTER, payload, etag=etag)
                except Exception:
                    # A lost response after a successful write is safe to reconcile.
                    current, _ = store.read(POINTER)
                    if not current or next((s for s in json.loads(current).get('sets', []) if s.get('id') == data['set_id']), None) != entry:
                        raise
            data.update(status='published', completed_at=now(), error=None,
                url=settings['website_url'].rstrip('/') + '/how-its-made/?experiment=' + data['set_id'] + data['recipe']['route'],
                progress={'phase': 'published', 'completed': data['sample_count'], 'total': data['sample_count']},
                descriptor_url=entry['url'], descriptor_sha256=sha)
            self.db.execute('UPDATE generation_artifacts SET sha256=?,size_bytes=?,uri=?,metadata_json=? WHERE id=?',
                (sha, len(raw), 'r2://' + settings['bucket'] + '/' + key, json_text(data), identifier))
            try:
                directory.rmdir()
            except OSError:
                data['cleanup_pending'] = True
                self.save(identifier, data)
        except Exception as error:
            data.update(status='failed', error=error_text(error))
            self.save(identifier, data)
