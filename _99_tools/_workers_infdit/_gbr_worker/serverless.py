"""Asynchronous RunPod requests with private S3/R2 result transfer and durable resume."""
import json
import os
from pathlib import Path
import re
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .artifacts import MAX_ARCHIVE, unpack
from .contract import canonical
from .runpod import RemotePending


class ArtifactStore:
    def __init__(self, endpoint=None, bucket=None):
        import boto3
        from botocore.config import Config
        self.bucket = bucket or os.environ.get('GBR_S3_BUCKET')
        endpoint = endpoint or os.environ.get('GBR_S3_ENDPOINT')
        access, secret = os.environ.get('AWS_ACCESS_KEY_ID'), os.environ.get('AWS_SECRET_ACCESS_KEY')
        if not self.bucket or not endpoint or not endpoint.startswith('https://') or not access or not secret:
            raise ValueError('Serverless FLAC transfer needs an S3/R2 endpoint, bucket and access credentials in Generation settings')
        self.client = boto3.client('s3', endpoint_url=endpoint, region_name=os.environ.get('AWS_DEFAULT_REGION', 'auto'),
            aws_access_key_id=access, aws_secret_access_key=secret, aws_session_token=os.environ.get('AWS_SESSION_TOKEN'),
            config=Config(signature_version='s3v4', retries={'max_attempts': 3}))

    def receipt(self, key):
        from botocore.exceptions import ClientError
        try:
            result = self.client.get_object(Bucket=self.bucket, Key=key + '.json')
        except ClientError as exc:
            if exc.response.get('Error', {}).get('Code') in {'NoSuchKey', '404'}:
                return None
            raise RuntimeError('Cannot read serverless artifact storage; check bucket access') from None
        with result['Body'] as body:
            raw = body.read(1024**2 + 1)
        if len(raw) > 1024**2:
            raise ValueError('Artifact receipt is too large')
        return json.loads(raw)

    def upload_urls(self, key):
        return {name: self.client.generate_presigned_url('put_object', Params={'Bucket': self.bucket, 'Key': key + suffix}, ExpiresIn=86400)
                for name, suffix in [('archive', '.tar'), ('receipt', '.json')]}

    def download(self, key, destination):
        result = self.client.get_object(Bucket=self.bucket, Key=key + '.tar')
        count = 0
        with result['Body'] as body, destination.open('wb') as stream:
            while block := body.read(1024**2):
                count += len(block)
                if count > MAX_ARCHIVE:
                    raise ValueError('Serverless result exceeds transfer limit')
                stream.write(block)

    def remove(self, key):
        result = self.client.delete_objects(Bucket=self.bucket, Delete={'Objects': [{'Key': key + suffix} for suffix in ('.tar', '.json')]})
        if result.get('Errors'):
            raise RuntimeError('Temporary serverless artifacts could not be removed; retry cleanup')


class ServerlessClient:
    def __init__(self, endpoint, *, store=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]{3,100}', endpoint):
            raise ValueError('Invalid RunPod endpoint ID')
        self.endpoint = endpoint
        self.key = os.environ.get('RUNPOD_API_KEY')
        if not self.key:
            raise ValueError('Provide a RunPod API key in Generation settings or RUNPOD_API_KEY')
        self.store = store

    def api(self, operation, body=None):
        request = Request(f'https://api.runpod.ai/v2/{self.endpoint}/{operation}',
            data=None if body is None else canonical(body).encode(),
            headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=60) as response:
                raw = response.read(4 * 1024**2 + 1)
            if len(raw) > 4 * 1024**2:
                raise ValueError('Unexpectedly large RunPod API response')
            return json.loads(raw)
        except HTTPError as exc:
            if exc.code in {401, 403}:
                raise RuntimeError('RunPod rejected the API key or endpoint permission') from None
            raise RemotePending(f'RunPod API returned HTTP {exc.code}; resume this attempt instead of submitting again') from None
        except (OSError, ValueError, URLError):
            raise RemotePending('RunPod response is unavailable; resume this attempt instead of submitting again') from None

    def health(self):
        return self.api('health')

    def run(self, job, directory, *, resume=False, poll_seconds=5):
        store = self.store or ArtifactStore()
        reference_path = directory / 'remote.json'
        object_key = f"gbr-generations/{job['run_id']}/{job['attempt_id']}"
        if resume:
            reference = json.loads(reference_path.read_text())
            if reference.get('endpoint') != self.endpoint or reference.get('object_key') != object_key:
                raise ValueError('Serverless attempt reference changed')
        else:
            reference = {'backend': 'runpod_serverless', 'endpoint': self.endpoint, 'object_key': object_key,
                         'attempt_id': job['attempt_id'], 'submission': 'uncertain'}
            with reference_path.open('x', encoding='utf-8') as stream:
                stream.write(canonical(reference))
            # Do not retry this POST: a lost reply can still represent an accepted GPU job.
            response = self.api('run', {'input': {'job': job, 'uploads': store.upload_urls(object_key)},
                                       'policy': {'executionTimeout': 3600000, 'ttl': 86400000}})
            if not isinstance(response.get('id'), str) or not re.fullmatch(r'[A-Za-z0-9_-]+', response['id']):
                raise RemotePending('No valid remote job ID returned; resume to look for its result')
            reference.update(job_id=response['id'], submission='accepted')
            temporary = reference_path.with_suffix('.tmp')
            temporary.write_text(canonical(reference))
            temporary.replace(reference_path)
        while True:
            if getattr(self,'cancelled',None) and self.cancelled() and reference.get('job_id'):
                from .control import Cancelled
                self.api('cancel/'+reference['job_id'],{})
                confirmed=self.api('status/'+reference['job_id'])
                if confirmed.get('status') in {'CANCELLED','FAILED','TIMED_OUT'}:raise Cancelled('RunPod confirmed this job stopped')
            try:
                receipt = store.receipt(object_key)
            except Exception:
                raise RemotePending('Artifact storage is unavailable; remote work may still be running') from None
            if receipt:
                if getattr(self,'progress',None):self.progress('downloading')
                archive = directory / 'worker.tar.download'
                try:
                    store.download(object_key, archive)
                    unpack(archive, directory, receipt, job)
                    (directory / 'remote-output.json').write_text(canonical(receipt))
                except Exception:
                    raise RemotePending('Result download/verification failed; resume to collect the existing result') from None
                finally:
                    archive.unlink(missing_ok=True)
                return
            job_id = reference.get('job_id')
            if not job_id:
                raise RemotePending('Submission response was lost. No replacement was sent; resume after the remote result arrives or inspect the endpoint')
            status = self.api('status/' + job_id)
            if getattr(self,'progress',None):self.progress('serverless_queued' if status.get('status')=='IN_QUEUE' else 'serverless_generating')
            if status.get('status') in {'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                raise RuntimeError('RunPod job ended with status ' + status['status'])
            if status.get('status') == 'COMPLETED':
                raise RemotePending('RunPod finished without a verified artifact receipt; inspect worker/storage logs before resuming')
            time.sleep(poll_seconds)

    def acknowledge(self, job, directory):
        reference = json.loads((directory / 'remote.json').read_text())
        receipt = json.loads((directory / 'remote-output.json').read_text())
        if (receipt['attempt_id'] != job['attempt_id'] or receipt['run_id'] != job['run_id']
                or reference.get('endpoint') != self.endpoint
                or reference.get('object_key') != f"gbr-generations/{job['run_id']}/{job['attempt_id']}"):
            raise ValueError('Cannot clean a different serverless attempt')
        store = self.store or ArtifactStore()
        saved = store.receipt(reference['object_key'])
        if saved and saved['sha256'] != receipt['sha256']:
            raise ValueError('Remote artifacts changed; retained in storage')
        store.remove(reference['object_key'])
