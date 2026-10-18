"""Checked, bounded transfers of worker result files; never extract arbitrary archive paths."""
from pathlib import Path
import tarfile

from .contract import PROTOCOL, file_sha

OUTPUTS = {'audio.flac', 'result.json', 'events.ndjson', 'startup.ndjson', 'stderr.log', 'exit.json'}
MAX_ARCHIVE = 768 * 1024**2


def pack(directory, archive, job):
    files = {p.name: {'sha256': file_sha(p), 'size_bytes': p.stat().st_size}
             for p in directory.iterdir() if p.name in OUTPUTS and p.is_file()}
    if 'result.json' not in files:
        raise ValueError('Worker did not write a result')
    with tarfile.open(archive, 'w') as tar:
        for name in sorted(files):
            tar.add(directory / name, arcname=name, recursive=False)
    return {'protocol': PROTOCOL, 'run_id': job['run_id'], 'attempt_id': job['attempt_id'],
            'sha256': file_sha(archive), 'size_bytes': archive.stat().st_size, 'files': files}


def unpack(archive, directory, receipt, job):
    if (receipt.get('protocol') != PROTOCOL or receipt.get('run_id') != job['run_id']
            or receipt.get('attempt_id') != job['attempt_id']):
        raise ValueError('Serverless result belongs to a different frozen attempt')
    if not 0 < receipt.get('size_bytes', 0) <= MAX_ARCHIVE or archive.stat().st_size != receipt['size_bytes'] or file_sha(archive) != receipt['sha256']:
        raise ValueError('Serverless archive size/checksum mismatch')
    files = receipt['files']
    if not {'result.json', 'exit.json'} <= set(files) or not set(files) <= OUTPUTS:
        raise ValueError('Invalid serverless result file list')
    staged = []
    try:
        with tarfile.open(archive, 'r:') as tar:
            seen = set()
            for member in tar:
                if member.name not in files or member.name in seen or not member.isfile():
                    raise ValueError('Unexpected, duplicate or non-file archive member')
                seen.add(member.name)
                expected = files[member.name]
                maximum = 512 * 1024**2 if member.name == 'audio.flac' else 32 * 1024**2
                if type(expected.get('size_bytes')) is not int or not 0 <= member.size == expected['size_bytes'] <= maximum:
                    raise ValueError('Serverless file exceeds its declared transfer limit')
                path = directory / (member.name + '.download')
                staged.append((path, directory / member.name))
                with tar.extractfile(member) as source, path.open('wb') as destination:
                    remaining = member.size
                    while remaining:
                        block = source.read(min(1024**2, remaining))
                        if not block:
                            raise ValueError('Truncated serverless file')
                        destination.write(block)
                        remaining -= len(block)
                if file_sha(path) != expected['sha256']:
                    raise ValueError('Serverless file checksum mismatch')
            if seen != set(files):
                raise ValueError('Missing serverless result files')
        for path, destination in staged:
            path.replace(destination)
    finally:
        for path, _ in staged:
            path.unlink(missing_ok=True)
