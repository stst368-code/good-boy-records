"""Permanent background controller used by the Generation tab; survives browser/app closure."""
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / '_02_generation'))
sys.path.insert(0, str(ROOT / '_99_tools/_workers_infdit'))
sys.path.insert(0, str(ROOT / '_98_database'))
from _gbr_worker.contract import canonical
from _gbr_worker.hardware import gpu_lock
from _gbr_worker.runpod import RunpodClient
from _gbr_worker.serverless import ServerlessClient
from _gbr_generation.executor import WORKER, cleanup_outputs, default_python, execute
from _gbr_generation.planner import verify_plan


def write_state(folder, status, **extra):
    path = folder / 'state.json'
    temporary = folder / 'state.tmp'
    temporary.write_text(canonical({'status': status, **extra}), encoding='utf-8')
    temporary.replace(path)


def prepare_worker(connection, *, install=False):
    backend = connection['backend']
    if backend=='comfyui':
        sys.path.insert(0,str(ROOT/'_99_tools/_workers_comfyui'))
        from client import Client
        client=Client(connection['address']);return {'system':client.inventory()['system'],'engine':'comfyui','encoding_cache':'Comfy graph cache only'}
    if backend == 'runpod_dedicated':
        client = RunpodClient(connection['address'], connection.get('identity_file') or None)
        return client.setup() if install else client.rpc('doctor')
    if backend == 'runpod_serverless':
        return ServerlessClient(connection['address']).health()
    python = Path(connection.get('worker_python') or default_python())
    if install:
        # A healthy environment is reused; a new device installs in the existing worker directory.
        if python.is_file():
            checked = subprocess.run([str(python), str(WORKER / 'worker.py'), 'doctor'], capture_output=True)
            if checked.returncode == 0:
                return json.loads(checked.stdout)
        if connection.get('worker_python'):
            raise ValueError('Repair a custom worker environment explicitly, or use the standard local environment')
        subprocess.run([sys.executable, str(WORKER / 'install_worker.py')], check=True)
    if not python.is_file():
        raise ValueError('Local worker is not installed. Enable automatic setup or use Prepare worker')
    return json.loads(subprocess.check_output([str(python), str(WORKER / 'worker.py'), 'doctor']))


def run_task(path):
    from _gbr_layout import task_paths
    task = task_paths(json.loads(Path(path).read_text(encoding='utf-8')))
    folder = Path(path).parent
    connection = task['connection']
    try:
        # Multiple controller processes cannot run overlapping batches on the same target.
        with gpu_lock('gbr-controller:' + connection['backend'] + ':' + connection.get('address', 'local')):
            if task.get('cleanup'):
                write_state(folder, 'cleaning')
                result = cleanup_outputs(task['manifest'], runpod_identity=connection.get('identity_file') or None)
                print(canonical(result), flush=True)
                write_state(folder, 'cleanup_complete', batch_id=task['batch_id'])
                return 0
            write_state(folder, 'preparing' if task.get('install') else 'checking')
            if not task.get('manifest') and not task.get('install') and connection['backend'] == 'runpod_dedicated':
                report = RunpodClient(connection['address'], connection.get('identity_file') or None).check_connection()
            else:
                report = prepare_worker(connection, install=task.get('install', False)) if not task.get('resume') else None
            if report is not None:
                (folder / 'hardware.json').write_text(canonical(report), encoding='utf-8')
                print(canonical(report), flush=True)
            if not task.get('manifest'):
                write_state(folder, 'succeeded')
                return 0
            manifest = verify_plan(task['manifest'])
            try:
                cleanup_outputs(task['manifest'], runpod_identity=connection.get('identity_file') or None)
            except (OSError, ValueError, RuntimeError) as exc:
                print('Earlier temporary files retained for cleanup: ' + str(exc), flush=True)
            attempted = set()
            while not (folder / 'pause').exists():
                write_state(folder, 'running', batch_id=manifest['batch_id'])
                result = execute(task['manifest'], worker_python=connection.get('worker_python') or None,
                    runpod_identity=connection.get('identity_file') or None, limit=1,
                    retry_failed=task.get('retry_failed', False), resume_remote=task.get('resume', False))
                print(canonical(result), flush=True)
                if not result['attempts']:
                    write_state(folder, result['status'], batch_id=manifest['batch_id'])
                    return 1 if result['status'] == 'failed' else 0
                # A failed or disconnected attempt stops the queue; only explicit actions retry it.
                if result['attempts'][0]['status'] != 'succeeded':
                    write_state(folder, result['status'], batch_id=manifest['batch_id'], reconnect=result['status'] == 'running')
                    return 3 if result['status'] == 'running' else 1
                if result['attempts'][0]['run_id'] in attempted:
                    raise RuntimeError('A batch run was unexpectedly dispatched twice')
                attempted.add(result['attempts'][0]['run_id'])
                task['resume'] = False
            write_state(folder, 'paused' if (folder / 'pause').exists() else 'succeeded', batch_id=manifest['batch_id'])
            return 0
    except Exception as exc:
        write_state(folder, 'failed', error=str(exc))
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr, flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(run_task(sys.argv[1]))
