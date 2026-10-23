"""Linux process evidence for dedicated attempts, using stdlib only before worker installation."""
import json
import os
from pathlib import Path


def process_record(pid, proc=Path('/proc')):
    text = (proc / str(pid) / 'stat').read_text()
    fields = text.rsplit(')', 1)[1].split()
    return {'pid': int(pid), 'group': int(fields[2]), 'start_ticks': fields[19], 'state': fields[0]}


def identity(pid):
    try:
        return {**process_record(pid), 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()}
    except (OSError, ValueError, IndexError):
        # Restricted /proc views must not turn a successfully spawned worker into a submission error.
        return {'pid': pid}


def inspect_attempt(directory, root, proc=Path('/proc')):
    """Never infer liveness from PID alone; include orphan children in the process group.

    A complete scan must succeed before an unrecorded exit can be classified as stopped.
    An unrelated live worker is conservatively a blocker for legacy attempts lacking identity.
    """
    directory, root = Path(directory), Path(root)
    if (directory / 'exit.json').is_file():
        return {'state': 'finished'}
    if not proc.is_dir():
        return {'state': 'unknown', 'reason': 'Process inventory is unavailable'}
    try:
        saved = json.loads((directory / 'pid.json').read_text()) if (directory / 'pid.json').exists() else {}
        boot = (proc / 'sys/kernel/random/boot_id').read_text().strip()
        old_boot = saved.get('boot_id') and saved['boot_id'] != boot
        if saved.get('pid') and not old_boot:
            try:
                record = process_record(saved['pid'], proc)
                same = not saved.get('start_ticks') or record['start_ticks'] == saved['start_ticks']
                if same and record['state'] != 'Z':
                    return {'state': 'running', 'pids': [saved['pid']], 'reason': 'Recorded supervisor is active'}
            except (FileNotFoundError, ProcessLookupError):
                try:
                    os.kill(saved['pid'], 0)
                    return {'state': 'running', 'pids': [saved['pid']], 'reason': 'Supervisor is alive in a restricted process inventory'}
                except ProcessLookupError:
                    pass
                except PermissionError:
                    return {'state': 'unknown', 'reason': 'Supervisor process access was denied'}
        processes, uncertain = [], False
        for path in proc.iterdir():
            if not path.name.isdigit():
                continue
            try:
                record = process_record(path.name, proc)
                args = (path / 'cmdline').read_bytes().split(b'\0')
                command = [a.decode('utf-8', errors='replace') for a in args if a]
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, ValueError, IndexError):
                uncertain = True
                continue
            if record['state'] == 'Z' or record['pid'] == os.getpid():
                continue
            attempt_match = any(directory.name == arg or str(directory / 'request.json') == arg for arg in command)
            group_match = not old_boot and saved.get('pid') and record['group'] == saved.get('group', saved['pid'])
            legacy_worker = not saved.get('boot_id') and any(
                arg.startswith(str(root) + '/') and Path(arg).name in {'worker.py', 'pod_agent.py'} for arg in command)
            if attempt_match or group_match or legacy_worker:
                processes.append(record['pid'])
        if processes:
            return {'state': 'running', 'pids': processes, 'reason': 'Supervisor, worker or process group is active'}
        if uncertain:
            return {'state': 'unknown', 'reason': 'Could not inspect every process; files retained'}
        return {'state': 'stopped', 'pids': [], 'boot_id': boot,
                'reason': 'Complete process inventory found no supervisor, worker or child process'}
    except (OSError, ValueError, TypeError):
        return {'state': 'unknown', 'reason': 'Attempt process evidence is unreadable'}
