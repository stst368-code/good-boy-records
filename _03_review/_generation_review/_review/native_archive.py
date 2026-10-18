"""Move native generation companions with reviewed audio and keep artifact paths current."""
from pathlib import Path

KINDS = {'audio': '.flac', 'completed_yaml': '.yaml', 'source_yaml': '.source.yaml',
         'generation_report': '.generation.json'}


def extend_plan(db, plan, generations, folder, stem):
    if not db.one("SELECT 1 FROM sqlite_master WHERE type='table' AND name='generation_output_links'"):
        return plan
    moved, seen = [], set()
    for generation in generations:
        artifacts = db.all('''SELECT a.* FROM generation_artifacts a JOIN generation_output_links l ON l.run_id=a.run_id
                              WHERE l.review_generation_id=? ORDER BY a.run_id,a.kind''', (generation['id'],))
        for artifact in artifacts:
            if artifact['id'] in seen or artifact['kind'] not in KINDS:
                continue
            seen.add(artifact['id'])
            source = Path(artifact['uri'])
            protected = Path(__file__).resolve().parents[3] / '_01_writing' / '_built'
            if source.resolve().is_relative_to(protected.resolve()):
                raise ValueError('A generation artifact refers to an original writing YAML; source retained')
            # Existing audio/completed-YAML destinations are reused by exact content hash.
            match = next((item for item in plan['items'] if item['sha256'] == artifact['sha256']), None)
            destination = match['destination'] if match else str(folder / (stem + '__g' + artifact['run_id'] + KINDS[artifact['kind']]))
            if not match:
                plan['items'].append({'kind': artifact['kind'], 'source': str(source),
                                      'destination': destination, 'sha256': artifact['sha256']})
            plan['originals'].append({'kind': artifact['kind'], 'source': str(source), 'sha256': artifact['sha256']})
            moved.append({'id': artifact['id'], 'source': str(source), 'destination': destination, 'sha256': artifact['sha256']})
    plan['native_artifacts'] = moved
    return plan


def commit_paths(connection, plan):
    for item in plan.get('native_artifacts', []):
        connection.execute('UPDATE generation_artifacts SET uri=? WHERE id=? AND sha256=?',
                           (item['destination'], item['id'], item['sha256']))


def remove_empty_bundles(plan):
    for folder in {Path(item['source']).parent for item in plan.get('native_artifacts', [])}:
        try:
            folder.rmdir()
        except OSError:
            pass  # Unknown files and nonempty folders always remain in place.
