"""Run the same native worker on RunPod Serverless; upload a private result bundle."""
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
from urllib.request import Request, urlopen

from _gbr_worker.artifacts import pack
from _gbr_worker.contract import PROTOCOL, canonical, execution_target, validate_job
from _gbr_worker.hardware import gpu_lock


def upload(url, source):
    if not isinstance(url, str) or not url.startswith('https://'):
        raise ValueError('An HTTPS artifact upload URL is required')
    with source.open('rb') as body:
        request = Request(url, data=body, method='PUT', headers={'Content-Length': str(source.stat().st_size)})
        try:
            with urlopen(request, timeout=600) as response:
                response.read(4096)
        except Exception:
            raise RuntimeError('Artifact upload failed; check configured S3/R2 access') from None


def workspace():
    return Path('/runpod-volume' if Path('/runpod-volume').is_dir() else '/workspace') / 'gbr'


def handler(event):
    body = event['input']
    job = body['job']
    if any(not re.fullmatch(r'[0-9a-f]{32}', str(job.get(key, ''))) for key in ('run_id', 'attempt_id')):
        raise ValueError('Invalid frozen run/attempt ID')
    validate_job(job, require_local=False)
    if execution_target(job['execution'])[0] != 'runpod_serverless':
        raise ValueError('This endpoint accepts serverless requests only')
    root = workspace()
    root.mkdir(parents=True, exist_ok=True)
    os.environ['GBR_SERVERLESS_WORKER'] = '1'
    os.environ['GBR_ENDPOINT_ID'] = job['execution']['diffusion']['address']
    os.environ['HF_HOME'] = str(root / 'huggingface')
    with gpu_lock('serverless-attempt-' + job['attempt_id']):
        target = root / 'attempts' / job['attempt_id']
        target.mkdir(parents=True, exist_ok=True)
        frozen = target / 'frozen-job.json'
        if frozen.is_file():
            if frozen.read_text() != canonical(job):
                raise ValueError('This attempt already has different frozen inputs')
        else:
            with frozen.open('x') as stream:
                stream.write(canonical(job))
        if (target / 'uploaded.json').is_file():
            return json.loads((target / 'uploaded.json').read_text())
        # An interrupted native attempt on a persistent volume is not silently regenerated.
        if (target / 'started').exists() and not (target / 'result.json').exists():
            raise RuntimeError('An unfinished attempt exists; inspect it before generating again')
        if not (target / 'result.json').exists():
            (target / 'started').touch(exist_ok=False)
            local = dict(job, result_directory=str(target), cache_root=str(root / 'encodings'))
            (target / 'request.json').write_text(canonical(local))
            with (target / 'stderr.log').open('wb') as log:
                code = subprocess.call([sys.executable, '-u', str(Path(__file__).with_name('worker.py')), 'run', str(target / 'request.json')],
                                       stdout=log, stderr=log, env=os.environ.copy())
            (target / 'exit.json').write_text(canonical({'returncode': code}))
            if not (target / 'result.json').exists():
                (target / 'result.json').write_text(canonical({'protocol': PROTOCOL, 'run_id': job['run_id'],
                    'attempt_id': job['attempt_id'], 'status': 'failed',
                    'completed_at': datetime.now(timezone.utc).isoformat(),
                    'error': f'Native worker exited {code} without a result. Its logs and available measurements were retained.'}))
        if not (target / 'exit.json').exists():
            # A handler restart can occur after the worker commits its result but before exit.json.
            (target / 'exit.json').write_text(canonical({'returncode': None, 'recovered_from_result': True}))
        archive = target / 'outputs.tar'
        receipt = pack(target, archive, job)
        receipt_file = target / 'receipt.json'
        receipt_file.write_text(canonical(receipt))
        upload(body['uploads']['archive'], archive)
        upload(body['uploads']['receipt'], receipt_file)
        (target / 'uploaded.json').write_text(canonical(receipt))
        archive.unlink()
        (target / 'audio.flac').unlink(missing_ok=True)
        return receipt


if __name__ == '__main__':
    import runpod
    runpod.serverless.start({'handler': handler})
