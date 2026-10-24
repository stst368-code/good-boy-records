"""Experiment playback, measurements and resumable output actions on the shared ledger.

Frozen requests retain their original purpose forever. The audio artifact's metadata
holds the one output-action journal; timings, caches and source documents are untouched.
"""
from contextlib import closing
import json
from pathlib import Path
import re

from .executor import now
from .planner import json_text, open_database
from .purpose import purpose


def maximum(rows, field):
    values = [r[field] for r in rows if r[field] is not None]
    return max(values) if values else None


def observations(db, run_id):
    """Keep every attempt and parent span; never sum overlapping timing/VRAM spans."""
    artifacts = db.all("SELECT metadata_json FROM generation_artifacts WHERE run_id=? AND kind='audio'", (run_id,))
    artifact = json.loads(artifacts[0]['metadata_json']) if artifacts else {}
    result = []
    for attempt in db.all('''SELECT a.*,w.gpu_name,w.usable_vram_bytes,w.hardware_json,w.environment_json
            FROM generation_attempts a LEFT JOIN worker_sessions w ON w.id=a.worker_session_id
            WHERE a.run_id=? ORDER BY a.rowid''', (run_id,)):
        spans = db.all('SELECT * FROM generation_timing_spans WHERE attempt_id=? ORDER BY rowid', (attempt['id'],))
        resources = db.all('SELECT * FROM generation_resource_measurements WHERE attempt_id=?', (attempt['id'],))
        wall = next((s for s in spans if s['stage'] == 'total' and s['parent_id'] is None), {})
        encoding = artifact.get('encoding') if artifact.get('attempt_id') == attempt['id'] else None
        cache = (encoding or {}).get('status')
        if cache is None and type((encoding or {}).get('cache_hit')) is bool:
            cache = 'hit' if encoding['cache_hit'] else 'miss'
        hardware = json.loads(attempt.pop('hardware_json') or '{}')
        environment = json.loads(attempt.pop('environment_json') or '{}')
        models = json.loads(attempt.pop('actual_models_json') or '{}')
        result.append({**attempt, 'hardware': hardware, 'environment': environment, 'actual_models': models,
            'wall_seconds': wall.get('duration_seconds'),
            'estimated_cost': wall.get('estimated_cost'), 'currency': wall.get('currency'),
            'cost_basis': wall.get('cost_basis'), 'encoding': encoding, 'cache_status': cache,
            'conditioning_seconds': next((s['duration_seconds'] for s in spans if s['stage'] == 'conditioning'), None),
            'diffusion_seconds': next((s['duration_seconds'] for s in spans if s['stage'] == 'diffusion'), None),
            'comfy_execution_seconds': next((s['duration_seconds'] for s in spans if s['stage'] == 'comfy_execution'), None),
            'peak_allocated_bytes': maximum(resources, 'peak_allocated_bytes'),
            'peak_reserved_bytes': maximum(resources, 'peak_reserved_bytes'),
            'peak_observed_device_bytes': maximum(resources, 'peak_observed_device_bytes'),
            'timings': spans, 'resources': resources})
    return result


class ExperimentOutputs:
    def __init__(self, generation):
        self.g = generation

    def run(self, run_id):
        if not re.fullmatch('[0-9a-f]{32}', run_id):
            raise ValueError('Invalid generation run ID')
        row = self.g.review.db.one('SELECT * FROM generation_runs WHERE id=?', (run_id,))
        if not row or purpose(json.loads(row['requested_json'])) != 'experiment':
            raise ValueError('Choose an experiment run')
        return row

    def output(self, run_id):
        row = self.g.review.db.one("SELECT * FROM generation_artifacts WHERE run_id=? AND kind='audio'", (run_id,))
        if not row:
            return {'state': 'pending', 'available': False}
        journal = json.loads(row['metadata_json']).get('experiment_output') or {}
        state = journal.get('state', 'saved')
        available = state in {'saved', 'promoted'} and Path(row['uri']).is_file()
        return {'state': state, 'available': available,
                'promoted_at': journal.get('completed_at') if state == 'promoted' else None}

    def audio(self, run_id):
        self.run(run_id)
        if not self.output(run_id)['available']:
            raise ValueError('This experiment has no available saved audio')
        row = self.g.review.db.one("SELECT uri FROM generation_artifacts WHERE run_id=? AND kind='audio'", (run_id,))
        path = Path(row['uri'])
        # Promoted tracks can later move into review; only ledger paths are served.
        if path.is_symlink() or not path.is_file():
            raise ValueError('Saved experiment audio is unavailable')
        return path

    @staticmethod
    def verify(directory, files, *, missing=False):
        from _gbr_worker.contract import file_sha
        if directory.is_symlink() or not directory.is_dir():
            raise ValueError('Experiment output folder is unavailable: ' + str(directory))
        expected = {f['filename'] for f in files}
        if any(p.name not in expected or p.is_symlink() or not p.is_file() for p in directory.iterdir()):
            raise ValueError('Unexpected files in experiment bundle; left untouched')
        for item in files:
            path = directory / item['filename']
            if missing and not path.exists():
                continue
            if not path.is_file() or path.stat().st_size != item['size_bytes'] or file_sha(path) != item['sha256']:
                raise ValueError('Experiment file changed; left untouched: ' + str(path))

    def action(self, run_id, action):
        if action not in {'promote', 'delete'}:
            raise ValueError('Unknown experiment output action')
        with self.g.lock:
            run = self.run(run_id)
            if run['status'] != 'succeeded':
                raise ValueError('Wait for the experiment to finish before changing its outputs')
            with closing(open_database(self.g.review.config.database)) as conn:
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute("SELECT 1 FROM generation_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchone():
                    raise ValueError('This generation is finishing worker cleanup; wait for its attempt to finish and retry')
                artifacts = [dict(r) for r in conn.execute('''SELECT * FROM generation_artifacts
                    WHERE run_id=? AND kind IN ('audio','completed_yaml','source_yaml','generation_report')''', (run_id,))]
                audio = next((a for a in artifacts if a['kind'] == 'audio'), None)
                if not audio or len(artifacts) != 4:
                    raise ValueError('A complete verified FLAC/YAML bundle is required')
                metadata = json.loads(audio['metadata_json'])
                journal = metadata.get('experiment_output')
                if journal and journal['state'] in {'promoted', 'deleted'}:
                    conn.rollback()
                    if (action, journal['state']) not in {('promote', 'promoted'), ('delete', 'deleted')}:
                        raise ValueError('This experiment output has already been ' + journal['state'])
                    if action == 'promote':
                        self.after_promote(run_id)
                    self.remove_empty_parents(Path(journal['source']))
                    return {'run_id': run_id, **self.output(run_id)}
                if journal and journal['action'] != action:
                    raise ValueError('Finish the pending experiment ' + journal['action'] + ' action first')
                if conn.execute('SELECT 1 FROM generation_output_links WHERE run_id=?', (run_id,)).fetchone():
                    raise ValueError('This output already belongs to review; manage it there')
                # Recheck ownership on retries too, including after a Windows file lock.
                for item in artifacts:
                    if conn.execute('SELECT 1 FROM generation_artifacts WHERE uri=? AND run_id IS NOT ?', (item['uri'], run_id)).fetchone():
                        raise ValueError('Another run owns this file; left untouched')
                    if conn.execute("SELECT 1 FROM media_files WHERE path=? AND storage_state IN ('input','archived')", (item['uri'],)).fetchone():
                        raise ValueError('This file is already in review; left untouched')
                if not journal:
                    source = Path(audio['uri']).parent
                    if source.is_symlink() or not source.resolve().is_relative_to(self.g.experimental.resolve()):
                        raise ValueError('Only managed experiment bundles can be moved or deleted')
                    files = []
                    for item in artifacts:
                        path = Path(item['uri'])
                        if path.parent != source or path.is_symlink():
                            raise ValueError('Experiment artifacts do not form one owned bundle')
                        files.append({k: item[k] for k in ('id', 'sha256', 'size_bytes')} | {'filename': path.name})
                    # Deletion must not leave a temporary worker FLAC/remote acknowledgement behind.
                    attempt = metadata.get('attempt_id')
                    temporary = self.g.runtime / 'batches' / run['batch_id'] / 'attempts' / str(attempt)
                    if action == 'delete' and any((temporary / n).exists() for n in ('audio.flac', 'cleanup-pending.json')):
                        raise ValueError('Clean temporary worker files in this batch before deleting its saved output')
                    self.verify(source, files)
                    journal = {'action': action, 'state': 'promoting' if action == 'promote' else 'deleting',
                        'source': str(source), 'files': files, 'started_at': now()}
                    if action == 'promote':
                        journal['destination'] = str(self.g.generated / source.name)
                    metadata['experiment_output'] = journal
                    conn.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?', (json_text(metadata), audio['id']))
                # Persist intent before touching files. Retrying the same button resumes it.
                conn.commit()
                source = Path(journal['source'])
                if source.is_symlink() or not source.resolve().is_relative_to(self.g.experimental.resolve()):
                    raise ValueError('Experiment source moved outside the managed folder; left untouched')
                if action == 'promote':
                    destination = Path(journal['destination'])
                    if source.exists():
                        self.verify(source, journal['files'])
                        if destination.exists():
                            raise ValueError('Promotion destination already exists; nothing overwritten')
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        source.rename(destination)
                    self.verify(destination, journal['files'])
                else:
                    if source.exists():
                        self.verify(source, journal['files'], missing=True)
                        for item in journal['files']:
                            (source / item['filename']).unlink(missing_ok=True)
                        source.rmdir()
                conn.execute('BEGIN IMMEDIATE')
                latest = json.loads(conn.execute('SELECT metadata_json FROM generation_artifacts WHERE id=?', (audio['id'],)).fetchone()[0])
                if latest.get('experiment_output') != journal:
                    raise ValueError('Experiment action changed; retry to reconcile it')
                if action == 'promote':
                    for item in journal['files']:
                        conn.execute('UPDATE generation_artifacts SET uri=? WHERE id=?', (str(destination / item['filename']), item['id']))
                    conn.execute('UPDATE generation_execution_state SET output_directory=? WHERE run_id=?', (str(destination), run_id))
                latest['experiment_output'] = {**journal, 'state': 'promoted' if action == 'promote' else 'deleted', 'completed_at': now()}
                conn.execute('UPDATE generation_artifacts SET metadata_json=? WHERE id=?', (json_text(latest), audio['id']))
                conn.commit()
            if action == 'promote':
                self.after_promote(run_id)
            self.remove_empty_parents(source)
            return {'run_id': run_id, **self.output(run_id)}

    def remove_empty_parents(self, source):
        # Never remove caches, worker scratch, unknown files or the experiment root.
        parent = source.parent
        while parent != self.g.experimental and parent.is_relative_to(self.g.experimental):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent

    def after_promote(self, run_id):
        self.g.register_inbox()
        if not self.g.review.db.one('SELECT 1 FROM generation_output_links WHERE run_id=?', (run_id,)):
            self.g.review.indexer.index(self.audio(run_id))
        self.g.review.scan_event.set()
