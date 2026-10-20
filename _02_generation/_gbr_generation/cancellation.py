"""Verify explicitly cancelled temporary output before deleting or acknowledging it."""
import json
from pathlib import Path
from _gbr_worker.contract import file_sha
from .planner import write_new,json_text


def discard(directory,job):
    directory=Path(directory);audio=directory/'audio.flac';result=directory/'result.json'
    if not audio.is_file():return False
    if directory.name!=job['attempt_id']:raise ValueError('Cancelled attempt directory changed')
    if result.is_file():
        saved=json.loads(result.read_text())
        if saved.get('run_id')!=job['run_id'] or saved.get('attempt_id')!=job['attempt_id'] or saved.get('status') not in {'succeeded','failed'}:
            raise ValueError('Cancelled worker output does not match its attempt; retained for inspection')
    receipt=directory/'cancelled-output.json'
    evidence={k:file_sha(directory/k) if (directory/k).is_file() else None for k in ('request.json','result.json','audio.flac')}
    if receipt.is_file():
        if json.loads(receipt.read_text())!=evidence:raise ValueError('Cancelled output checksums changed')
    else:write_new(receipt,json_text(evidence).encode())
    audio.unlink()
    return True


def verify(directory):
    directory=Path(directory);receipt=json.loads((directory/'cancelled-output.json').read_text())
    for name in ('request.json','result.json'):
        if receipt[name] and file_sha(directory/name)!=receipt[name]:raise ValueError('Cancelled output receipt changed; retained for inspection')
    audio=directory/'audio.flac'
    if audio.is_file():
        if file_sha(audio)!=receipt['audio.flac']:raise ValueError('Cancelled temporary audio changed')
        audio.unlink()
