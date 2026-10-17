"""Permanent platform commands; help belongs in the app rather than setup notes."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / '_99_Tools' / '_workers_infdit'
COMMANDS = {'setup-generation', 'doctor-generation', 'test-generation', 'migrate', 'init-db', 'check-references'}


def dispatch(arguments):
    command, *tail = arguments
    if command in {'setup-generation', 'doctor-generation', 'test-generation', 'migrate'}:
        if command == 'setup-generation':
            argparse.ArgumentParser(description='Install or repair the isolated native CUDA worker environment.').parse_args(tail)
            script, python, extra = WORKER / 'install_worker.py', sys.executable, []
        elif command == 'migrate':
            script, python, extra = ROOT / '_98_Database' / 'migrate_databases.py', sys.executable, []
        else:
            python = WORKER / '.venv' / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
            if not python.is_file():
                raise ValueError('Install the native worker with: py -3.12 _97_App/run_gbr.py setup-generation')
            script, extra = (WORKER / 'worker.py', ['doctor']) if command == 'doctor-generation' else (ROOT / '_02_Generation' / 'first_local_test.py', [])
        # Preserve a venv interpreter symlink; resolving it would select its base environment.
        return subprocess.call([str(python), str(script), *extra, *tail])
    if command == 'init-db':
        parser = argparse.ArgumentParser(description='Explicitly create one empty platform DB on a fresh device; never overwrite.')
        parser.add_argument('--database', type=Path, default=ROOT / '_98_Database' / 'gbr.sqlite3')
        args = parser.parse_args(tail)
        sys.path.insert(0, str(ROOT / '_98_Database'))
        from gbr_database.initialize import initialize
        initialize(args.database)
        print(json.dumps({'database': str(args.database.resolve()), 'status': 'created'}))
        return 0
    parser = argparse.ArgumentParser(description='Check shared style IDs, provenance and referenced caption files.')
    parser.add_argument('--root', type=Path, default=ROOT / '_01_Writing' / '_references')
    args = parser.parse_args(tail)
    sys.path.insert(0, str(ROOT / '_02_Generation'))
    from gbr_generation.catalogue import Styles, validate_template_format
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
