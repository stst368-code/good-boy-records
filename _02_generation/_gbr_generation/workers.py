"""Machine worker settings and live discovery; generation measurements remain in the shared DB."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from types import SimpleNamespace

from .connections import connection_key, normalize
from _gbr_worker.hardware import gpu_lock
from _gbr_worker.contract import python_environment

DEFAULT_FIELDS = {'text_encoder', 'dit', 'sampler', 'scheduler', 'encoder_cfg', 'top_k',
                  'sampler_cfg', 'sampler_steps', 'duration'}


def is_local(address):
    host = urlsplit(address).hostname
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def launcher_settings(value, connection):
    value = dict(value or {})
    if set(value) - {'path', 'args', 'cwd', 'python', 'auto_start'}:
        raise ValueError('Launcher accepts a start file, argument list, working folder and optional Python executable')
    result = {'path': '', 'args': [], 'cwd': '', 'python': '', 'auto_start': False, **value}
    if type(result['auto_start']) is not bool or not isinstance(result['args'], list) or any(
            not isinstance(a, str) or '\x00' in a or len(a) > 4000 for a in result['args']) or len(result['args']) > 100:
        raise ValueError('Launcher arguments must be a list of separate text values')
    for key in ('path', 'cwd', 'python'):
        if not isinstance(result[key], str) or '\x00' in result[key] or len(result[key]) > 4000:
            raise ValueError('Invalid launcher path')
        result[key] = result[key].strip().strip('"')
        if result[key]:
            path = Path(result[key]).expanduser()
            if not path.is_absolute():
                raise ValueError('Use absolute paths for the local ComfyUI launcher')
            result[key] = str(path)
    if any(result[k] for k in ('path', 'args', 'cwd', 'python', 'auto_start')):
        if connection['backend'] != 'comfyui' or not is_local(connection['address']):
            raise ValueError('Auto-start is available only for a local ComfyUI loopback URL')
        if not result['path']:
            raise ValueError('Choose the local ComfyUI start file')
    return result


def launch_command(settings):
    path = Path(settings['path'])
    if not path.is_file():
        raise ValueError('ComfyUI start file does not exist: ' + str(path))
    args = settings['args']
    suffix = path.suffix.casefold()
    if suffix in {'.bat', '.cmd'}:
        if os.name != 'nt':
            raise ValueError('Windows ComfyUI start files must be launched on Windows')
        # cmd expands metacharacters even with shell=False. Accept file paths
        # and ordinary flags; shell syntax belongs inside the user's start file.
        if any(any(c in part for c in '&|<>^%!"\r\n') for part in [str(path), *args]):
            raise ValueError('Batch launcher paths/arguments cannot contain shell metacharacters')
        command = '""' + str(path) + '"' + ''.join(' "' + a + '"' for a in args) + '"'
        return [os.environ.get('COMSPEC', 'cmd.exe'), '/d', '/s', '/c', command]
    if suffix == '.py':
        return [settings.get('python') or sys.executable, str(path), *args]
    if suffix == '.ps1':
        return ['powershell.exe', '-NoProfile', '-File', str(path), *args]
    if suffix == '.sh':
        return ['bash', str(path), *args]
    return [str(path), *args]


def ensure_comfy(connection, launcher, runtime, timeout=60):
    """Start an explicitly configured local file once, then wait for its API.

    Never stop ComfyUI or assume a PID belongs to us after an application restart.
    One log/process record per endpoint replaces temporary launcher files.
    """
    from client import Client
    client = Client(connection['address'])
    def ready():
        try:
            result = client.request('/system_stats', timeout=2)
            return isinstance(result, dict) and 'system' in result
        except (OSError, ValueError):
            return False
    if ready():
        return
    settings = launcher_settings(launcher, connection)
    if not settings['auto_start']:
        raise ValueError('ComfyUI is offline. Start it or enable local auto-start in Worker settings.')
    folder = Path(runtime) / 'workers' / hashlib.sha256(connection_key(connection).encode()).hexdigest()[:24]
    folder.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout
    while True:
        try:
            with gpu_lock('gbr-launch:' + connection_key(connection)):
                if ready():
                    return
                import psutil
                running = False
                child = None
                record = folder / 'process.json'
                try:
                    old = json.loads(record.read_text(encoding="utf-8"))
                    process = psutil.Process(old['pid'])
                    running = process.is_running() and process.status() != psutil.STATUS_ZOMBIE and abs(process.create_time() - old['created']) < .01
                except (OSError, ValueError, TypeError, KeyError, psutil.Error):
                    pass
                # Some .bat launchers exit after handing off to Python. Keep a
                # short start window as well as PID identity to avoid double starts.
                if not running:
                    try:running = time.time() - json.loads(record.read_text(encoding="utf-8")).get('started_at', 0) < 120
                    except (OSError, ValueError, TypeError):pass
                if not running:
                    command = launch_command(settings)
                    cwd = Path(settings['cwd'] or Path(settings['path']).parent)
                    if not cwd.is_dir():
                        raise ValueError('ComfyUI working folder does not exist')
                    flags = {'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == 'nt' else {'start_new_session': True}
                    with (folder / 'launcher.log').open('ab') as log:
                        child = subprocess.Popen(command, cwd=cwd, stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=python_environment(), **flags)
                    try:
                        created = psutil.Process(child.pid).create_time()
                    except psutil.Error:
                        if child.poll() is not None:
                            raise ValueError('ComfyUI launcher exited immediately; check ' + str(folder / 'launcher.log')) from None
                        created = None  # PID inspection can be restricted; readiness still works.
                    record.write_text(json.dumps({'pid': child.pid, 'created': created, 'started_at': time.time()}), encoding="utf-8")
                # Keep the endpoint lock until ready: concurrent requests cannot
                # spawn another launcher while a batch file hands off to Python.
                while time.monotonic() < deadline:
                    if ready():
                        return
                    if child is not None and child.poll() not in (None, 0):
                        record.unlink(missing_ok=True)
                        raise ValueError('ComfyUI start file failed; check ' + str(folder / 'launcher.log'))
                    time.sleep(.25)
                raise ValueError('ComfyUI did not become ready within 60 seconds; check ' + str(folder / 'launcher.log'))
        except RuntimeError as error:
            if 'Another GBR worker' not in str(error):
                raise
            if ready():
                return
            if time.monotonic() >= deadline:
                raise ValueError('Another request is starting ComfyUI; wait and query the worker again') from None
            time.sleep(.25)


class Workers:
    default_fields=DEFAULT_FIELDS
    def __init__(self, generation):
        self.generation = generation
        self.reports = {}

    def entries(self):
        saved = self.generation.review.config.values.get('generation', {})
        entries = saved.get('workers')
        if entries is None:
            # Read old connections in place. A settings save performs the single,
            # idempotent migration, without a second registry or database.
            entries = [{'connection': c} for c in self.generation.preferences()['connections']]
        return [self.normalize(e) for e in entries]

    def normalize(self, value):
        connection = normalize(value['connection'])
        defaults = deepcopy(value.get('defaults', {}))
        if not isinstance(defaults, dict) or set(defaults) - DEFAULT_FIELDS:
            raise ValueError('Worker defaults accept generation controls only')
        json.dumps(defaults, allow_nan=False)
        from .parameters import resolve
        resolved, _ = resolve(SimpleNamespace(values={}), {'defaults': defaults})
        if any(k not in resolved for k in defaults):
            raise ValueError('Worker defaults cannot be blank')
        defaults = {k: resolved[k] for k in defaults}
        if type(value.get('enabled', True)) is not bool:
            raise ValueError('Invalid worker enabled setting')
        return {'id': connection_key(connection), 'connection': connection, 'enabled': value.get('enabled', True),
                'defaults': defaults, 'launcher': launcher_settings(value.get('launcher'), connection)}

    def get(self, identifier):
        for entry in self.entries():
            if entry['id'] == identifier:
                return entry
        raise ValueError('Worker was removed or changed; choose a saved worker again')

    def save(self, value, previous_id=None):
        entry = self.normalize(value)
        g = self.generation
        with g.lock, g.review.config_lock:
            entries = [e for e in self.entries() if e['id'] not in {entry['id'], previous_id}]
            entries.append(entry)
            saved = deepcopy(g.review.config.values.get('generation', {}))
            saved['workers'] = entries
            saved.pop('connections', None)
            g.review.config.values['generation'] = saved
            g.review.config.save()
        g.dispatcher.wake.set()
        return entry

    def delete(self, identifier):
        self.get(identifier)
        g = self.generation
        with g.lock, g.review.config_lock:
            saved = deepcopy(g.review.config.values.get('generation', {}))
            saved['workers'] = [e for e in self.entries() if e['id'] != identifier]
            saved.pop('connections', None)
            g.review.config.values['generation'] = saved
            g.review.config.save()
        self.reports.pop(identifier, None)

    def busy(self, connection):
        # An unresolved remote attempt is still busy, even after its controller
        # disconnects. The remote queue supplements this with non-GBR jobs.
        return any(connection_key(t['connection']) == connection_key(connection) and
                   (t.get('active') or any(r['status'] == 'running' for r in t.get('runs', [])))
                   for t in self.generation.tasks(include_logs=False))

    def inspect(self, entry):
        g = self.generation
        connection = entry['connection']; backend = connection['backend']
        if backend == 'comfyui':
            from client import Client
            client = Client(connection['address'])
            inventory = client.inventory(timeout=5)
            queue = client.request('/queue', timeout=5)
            if not isinstance(queue.get('queue_running'), list) or not isinstance(queue.get('queue_pending'), list):
                raise ValueError('ComfyUI queue status is unavailable')
            return {**g.comfy_choices(inventory), 'busy': bool(queue['queue_running'] or queue['queue_pending']),
                    'queue_running': len(queue['queue_running']), 'queue_pending': len(queue['queue_pending']),
                    '_inventory': inventory}
        if backend == 'runpod_serverless':
            key = g.secrets.get('api_key') or os.environ.get('RUNPOD_API_KEY')
            if not key:
                raise ValueError('Set the RunPod API key in Worker settings')
            request = Request('https://api.runpod.ai/v2/' + connection['address'] + '/health', headers={'Authorization': 'Bearer ' + key})
            with urlopen(request, timeout=5) as response:
                health = json.load(response)
            workers = health.get('workers', {})
            # A cold serverless endpoint can accept jobs and scale itself up.
            return {'busy': not bool(workers.get('idle', 0) or workers.get('ready', 0)) and bool(workers.get('running', 0)),
                    'health': health, 'hardware': None, 'models': g.native_choices(), 'hardware_pending': True}
        if backend == 'runpod_dedicated':
            from _gbr_worker.runpod import RunpodClient
            report = RunpodClient(connection['address'], connection.get('identity_file') or None).rpc('doctor', timeout=15)
        else:
            from .executor import default_python, WORKER
            python = Path(connection.get('worker_python') or default_python())
            if not python.is_file():
                raise ValueError('Native worker is not installed; use Prepare worker')
            report = json.loads(subprocess.check_output([str(python), str(WORKER / 'worker.py'), 'doctor'], timeout=15, stderr=subprocess.PIPE, env=python_environment()))
        return {'busy': False, 'hardware': report.get('hardware'), 'report': report, 'models': g.native_choices()}

    def query(self, entry, activate=False):
        connection = entry['connection']
        # Imports are rooted at the installed app, not a user-selected catalogue.
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / '_99_tools/_workers_comfyui'))
        try:
            if activate and connection['backend'] == 'comfyui':
                ensure_comfy(connection, {**entry['launcher'], 'auto_start': True}, self.generation.runtime)
            report = self.inspect(entry)
            if connection['backend'] == 'comfyui':
                models = report.get('models', {})
                report['compatible'] = not report.get('missing_nodes') and all(models.get(k) for k in ('text_encoders', 'diffusion_models', 'vaes'))
            report.update(online=True, busy=bool(report.get('busy') or self.busy(connection)), checked_at=time.time())
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            message = f'{connection["backend"]}: HTTP {error.code} at {error.url}' if isinstance(error, HTTPError) else str(error)
            if isinstance(error, subprocess.CalledProcessError) and error.stderr:
                message += '\n' + error.stderr.decode('utf-8', errors='replace')[-2000:]
            report = {'online': False, 'busy': None, 'error': message, 'checked_at': time.time()}
        self.reports[entry['id']] = report
        return report

    def list(self, refresh=False):
        entries = self.entries()
        if refresh and entries:
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(self.query, entries))
        return [{**e, 'status': {k: v for k, v in self.reports.get(e['id'], {}).items() if not k.startswith('_')}} for e in entries]

    def select(self, identifier, automatic=False):
        entries = [e for e in self.entries() if e['enabled']] if automatic else [self.get(identifier)]
        if not entries:
            raise ValueError('Add or enable a worker in Worker settings first')
        if automatic:
            with ThreadPoolExecutor(max_workers=4) as pool:
                reports = list(pool.map(self.query, entries))
            candidates = [(e, r) for e, r in zip(entries, reports) if r['online'] and r.get('compatible', True)]
            if candidates:
                entry, report = min(candidates, key=lambda pair: bool(pair[1]['busy']))
                return entry, report
            # Auto-start a configured local server only when no worker is online.
            entries = [e for e in entries if e['launcher']['auto_start']]
        for entry in entries:
            report = self.query(entry, activate=entry['launcher']['auto_start'])
            if report['online'] and (not automatic or report.get('compatible', True)):
                return entry, report
            if not automatic:
                # Native setup belongs to the background controller. Explicitly
                # selected fresh installs can still be prepared there.
                if entry['connection']['backend'] in {'local', 'runpod_dedicated'} and entry['connection']['auto_setup']:
                    return entry, report
                raise ValueError(report['error'])
        raise ValueError('No enabled worker is online. Query workers or enable local ComfyUI auto-start in Worker settings.')

    def pool(self):
        entries=[e for e in self.entries() if e['enabled']]
        with ThreadPoolExecutor(max_workers=4) as pool:
            reports=list(pool.map(self.query,entries))
        candidates=[(e,r) for e,r in zip(entries,reports) if r['online'] and r.get('compatible',True)]
        for e,r in zip(entries,reports):
            if not r['online'] and e['launcher']['auto_start']:
                r=self.query(e,activate=True)
                if r['online'] and r.get('compatible',True):candidates.append((e,r))
        if not candidates:raise ValueError('No compatible enabled workers are online. Query workers in Settings.')
        return sorted(candidates,key=lambda pair:bool(pair[1]['busy']))
