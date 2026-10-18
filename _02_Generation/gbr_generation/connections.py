"""Connection preferences contain addresses and local key paths, never model inventories."""
from pathlib import Path
import re
import shlex
from urllib.parse import urlsplit

from gbr_worker.contract import execution_target, ssh_endpoint


def key_candidates():
    root = Path.home() / '.ssh'
    if not root.is_dir():
        return []
    preferred = ['runpod_gbr', 'id_ed25519', 'id_rsa']
    files = [p for p in root.iterdir() if p.is_file() and not p.name.endswith('.pub')
             and p.name not in {'config', 'known_hosts', 'known_hosts.old', 'authorized_keys'}]
    files.sort(key=lambda p: (preferred.index(p.name) if p.name in preferred else 99, p.name))
    return [str(p) for p in files]


def normalize(connection):
    allowed = {'backend', 'address', 'identity_file', 'worker_python', 'memory_profile', 'auto_setup'}
    if set(connection) - allowed:
        raise ValueError('Connections accept an execution mode, address, key path and memory profile; models belong to jobs')
    result = dict(backend='local', address='', identity_file='', worker_python='', memory_profile='auto', auto_setup=True)
    result.update(connection)
    if result['memory_profile'] not in {'auto', 'low_vram', 'resident_stage'} or type(result['auto_setup']) is not bool:
        raise ValueError('Invalid connection memory profile or automatic setup option')
    address = result['address'].strip()
    if result['backend'] == 'runpod_dedicated':
        if address.startswith('ssh '):
            # Parse only these arguments; never execute a pasted shell command.
            lexer = shlex.shlex(address, posix=True)
            lexer.whitespace_split, lexer.escape = True, ''
            tokens = list(lexer)
            host, port, identity = None, 22, None
            index = 1
            while index < len(tokens):
                token = tokens[index]
                if token in {'-p', '-i'} and index + 1 < len(tokens):
                    if token == '-p':
                        port = int(tokens[index + 1])
                    else:
                        identity = tokens[index + 1]
                    index += 2
                elif token.startswith('-') or host is not None:
                    raise ValueError('Paste the direct SSH command with host, -p and optional -i only')
                else:
                    host = token
                    index += 1
            address = f'ssh://{host}:{port}'
            if identity and not result['identity_file']:
                result['identity_file'] = identity
        ssh_endpoint(address)
    elif result['backend'] == 'runpod_serverless':
        if address.startswith('https://'):
            parsed = urlsplit(address)
            if parsed.hostname != 'api.runpod.ai' or parsed.query or parsed.fragment:
                raise ValueError('Use a RunPod endpoint ID or its api.runpod.ai URL')
            parts = parsed.path.strip('/').split('/')
            if len(parts) < 2 or parts[0] != 'v2':
                raise ValueError('Use a RunPod endpoint ID')
            address = parts[1]
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,100}', address):
            raise ValueError('Enter the RunPod Serverless endpoint ID')
    elif result['backend'] != 'local' or address:
        raise ValueError('Local execution has no remote address')
    result['address'] = address
    if result['identity_file'].casefold().endswith('.pub'):
        raise ValueError('Choose the private RunPod SSH key, without the .pub suffix')
    for key in ('identity_file', 'worker_python'):
        if result[key]:
            result[key] = str(Path(result[key]).expanduser().absolute())
    execution_target(execution(result))
    return result


def connection_key(connection):
    return connection['backend'] + '|' + connection.get('address', '')


def execution(connection, cache='prefer'):
    profile = connection.get('memory_profile', 'auto')
    if profile == 'auto':
        profile = 'low_vram' if connection['backend'] == 'local' else 'resident_stage'
    target = {'backend': connection['backend'], 'address': connection.get('address') or None}
    return {'encoding_cache': cache, 'memory_profile': profile, 'cuda_device': 0, 'vram_sample_interval_ms': 100,
            'conditioning': dict(target), 'diffusion': dict(target)}
