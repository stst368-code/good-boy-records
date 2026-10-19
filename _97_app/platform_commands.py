"""Permanent platform commands; help belongs in the app rather than setup notes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / '_99_tools' / '_workers_infdit'
COMMANDS = {'setup-generation', 'doctor-generation', 'test-generation', 'setup-runpod', 'doctor-runpod',
            'migrate', 'init-db', 'check-references', 'plan-r2', 'check-comfy', 'doctor-processing'}


def dispatch(arguments):
    command, *tail = arguments
    if command == 'doctor-processing':
        argparse.ArgumentParser(description='Query FFmpeg and installed preparation packages without loading model weights.').parse_args(tail)
        environment=ROOT/'_03_review/_generation_review/.venv'
        candidate=environment/('Scripts/python.exe' if sys.platform=='win32' else 'bin/python')
        return subprocess.call([str(candidate) if candidate.is_file() else sys.executable,str(ROOT/'_99_tools/_workers_processing/worker.py'),'--doctor'])
    if command == 'check-comfy':
        return subprocess.call([sys.executable,str(ROOT/'_99_tools/_workers_comfyui/check_worker.py'),*tail])
    if command == 'plan-r2':
        import sqlite3
        from contextlib import closing
        parser=argparse.ArgumentParser(description='Build a read-only, content-addressed R2 inventory plan; never uploads or deletes.')
        parser.add_argument('--database',type=Path,default=ROOT/'_98_database/gbr.sqlite3')
        args=parser.parse_args(tail)
        sys.path.insert(0,str(ROOT/'_04_preparation'))
        from _gbr_preparation.storage_plan import plan
        with closing(sqlite3.connect(args.database.resolve(strict=True).as_uri()+'?mode=ro',uri=True)) as conn:
            conn.row_factory=sqlite3.Row
            conn.execute('PRAGMA query_only=ON');conn.execute('BEGIN')
            class ReadOnly:
                def all(self,sql):return [dict(row) for row in conn.execute(sql)]
            print(json.dumps(plan(ReadOnly()),ensure_ascii=False,indent=2))
        return 0
    if command in {'setup-runpod', 'doctor-runpod'}:
        parser = argparse.ArgumentParser(description=('Install and verify' if command == 'setup-runpod' else 'Query') +
            ' the native worker on an existing dedicated RunPod. Does not rent or stop pods.')
        parser.add_argument('--address', required=True, help='SSH over exposed TCP: ssh://root@PUBLIC_IP:PORT')
        parser.add_argument('--identity-file', type=Path, help='Local SSH private key path; otherwise uses SSH agent/config')
        args = parser.parse_args(tail)
        sys.path.insert(0, str(WORKER))
        from _gbr_worker.runpod import RunpodClient
        client = RunpodClient(args.address, args.identity_file)
        print(json.dumps(client.setup() if command == 'setup-runpod' else client.rpc('doctor'), indent=2))
        return 0
    if command in {'setup-generation', 'doctor-generation', 'test-generation', 'migrate'}:
        if command == 'setup-generation':
            argparse.ArgumentParser(description='Install or repair the isolated native CUDA worker environment.').parse_args(tail)
            script, python, extra = WORKER / 'install_worker.py', sys.executable, []
        elif command == 'migrate':
            script, python, extra = ROOT / '_98_database' / 'migrate_databases.py', sys.executable, []
        elif command == 'test-generation':
            # The controller needs YAML/FLAC libraries, not CUDA. Reuse the review environment
            # on fresh devices so a cloud test does not install a second local GPU runtime.
            environment = ROOT / '_03_review' / '_generation_review' / '.venv'
            candidate = environment / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            installed_worker = WORKER / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            python = candidate if candidate.is_file() else installed_worker if installed_worker.is_file() else sys.executable
            script, extra = ROOT / '_02_generation' / 'first_local_test.py', []
        else:
            python = WORKER / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            if not python.is_file():
                raise ValueError('Install the native worker with: py -3.12 _97_app/run_gbr.py setup-generation')
            script, extra = WORKER / 'worker.py', ['doctor']
        # Preserve a venv interpreter symlink; resolving it would select its base environment.
        return subprocess.call([str(python), str(script), *extra, *tail])
    if command == 'init-db':
        parser = argparse.ArgumentParser(description='Explicitly create one empty platform DB on a fresh device; never overwrite.')
        parser.add_argument('--database', type=Path, default=ROOT / '_98_database' / 'gbr.sqlite3')
        args = parser.parse_args(tail)
        sys.path.insert(0, str(ROOT / '_98_database'))
        from _gbr_database.initialize import initialize
        initialize(args.database)
        print(json.dumps({'database': str(args.database.resolve()), 'status': 'created'}))
        return 0
    parser = argparse.ArgumentParser(description='Check shared style IDs, provenance and referenced caption files.')
    parser.add_argument('--root', type=Path, default=ROOT / '_01_writing' / '_references')
    args = parser.parse_args(tail)
    sys.path.insert(0, str(ROOT / '_02_generation'))
    from _gbr_generation.catalogue import Styles, validate_template_format
    root = args.root.resolve(strict=True)
    catalogue = Styles(root / 'styles.csv')
    errors, counts = [], {'minimax': 0, 'custom': 0}
    for row in catalogue.rows:
        if row.get('template_source') not in counts:
            errors.append({'id': row['ID'], 'error': 'Shared catalogue requires template_source=minimax or custom'})
            continue
        counts[row['template_source']] += 1
        path = (root / row['template']).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            errors.append({'id': row['ID'], 'error': 'Template missing or outside reference root', 'template': row['template']})
        else:
            caption = path.read_text(encoding='utf-8-sig')
            if not caption.strip():
                errors.append({'id': row['ID'], 'error': 'Template is empty'})
            try:
                validate_template_format(caption, row['template_source'])
            except ValueError as exc:
                errors.append({'id': row['ID'], 'error': str(exc)})
    print(json.dumps({'templates': counts, 'errors': errors}, ensure_ascii=False, indent=2))
    return 2 if errors else 0
