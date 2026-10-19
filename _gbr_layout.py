"""Permanent compatibility for the repository's lowercase, underscore directory convention.

Only filesystem references are translated. Frozen manifests, request bytes, lyrics and source YAMLs
are never rewritten. Existing datasets move in place on app startup; no second database is created.
"""
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import uuid

DIRECTORIES = {
    "_04_website": "_05_website",
    "_01_Writing": "_01_writing",
    "_01_Writing/_Built": "_01_writing/_built",
    "_01_Writing/_Built/_yaml": "_01_writing/_built/_yaml",
    "_01_Writing/_Caption_Rewriter": "_01_writing/_caption_rewriter",
    "_01_Writing/_Lore": "_01_writing/_lore",
    "_01_Writing/_references": "_01_writing/_references",
    "_01_Writing/_references/_custom_templates": "_01_writing/_references/_custom_templates",
    "_01_Writing/_references/_minimax_templates": "_01_writing/_references/_minimax_templates",
    "_02_Generation": "_02_generation",
    "_02_Generation/examples": "_02_generation/_examples",
    "_02_Generation/examples/local_smoke": "_02_generation/_examples/_local_smoke",
    "_02_Generation/examples/local_smoke/_yaml": "_02_generation/_examples/_local_smoke/_yaml",
    "_02_Generation/gbr_generation": "_02_generation/_gbr_generation",
    "_02_Generation/static": "_02_generation/_static",
    "_02_Generation/tests": "_02_generation/_tests",
    "_03_Review": "_03_review",
    "_03_Review/_Documentation": "_03_review/_documentation",
    "_03_Review/_Documentation/_I": "_03_review/_documentation/_i",
    "_03_Review/_Documentation/_L": "_03_review/_documentation/_l",
    "_03_Review/_Documentation/_T": "_03_review/_documentation/_t",
    "_03_Review/_Generation_Review": "_03_review/_generation_review",
    "_03_Review/_Generation_Review/data": "_03_review/_generation_review/_data",
    "_03_Review/_Reviewed": "_03_review/_reviewed",
    "_03_Review/_Generation_Review/GBR_Reviewed": "_03_review/_generation_review/_reviewed",
    "_03_Review/_Generation_Review/docs": "_03_review/_generation_review/_docs",
    "_03_Review/_Generation_Review/docs/previews": "_03_review/_generation_review/_docs/_previews",
    "_03_Review/_Generation_Review/review": "_03_review/_generation_review/_review",
    "_03_Review/_Generation_Review/review/static": "_03_review/_generation_review/_review/_static",
    "_03_Review/_Generation_Review/tests": "_03_review/_generation_review/_tests",
    "_97_App": "_97_app",
    "_98_Database": "_98_database",
    "_98_Database/gbr_database": "_98_database/_gbr_database",
    "_98_Database/tests": "_98_database/_tests",
    "_99_Tools": "_99_tools",
    "_99_Tools/_YAML_Editor": "_99_tools/_yaml_editor",
    "_99_Tools/_YAML_Editor/gbr_yaml_editor": "_99_tools/_yaml_editor/_gbr_yaml_editor",
    "_99_Tools/_YAML_Editor/static": "_99_tools/_yaml_editor/_static",
    "_99_Tools/_YAML_Editor/tests": "_99_tools/_yaml_editor/_tests",
    "_99_Tools/_workers_infdit": "_99_tools/_workers_infdit",
    "_99_Tools/_workers_infdit/gbr_worker": "_99_tools/_workers_infdit/_gbr_worker",
    "_02_Generation/runtime": "_02_generation/_runtime",
    "_97_App/runtime": "_97_app/_runtime",
    "_02_Generation/generated": "_02_generation/_generated"
}
ROOT = Path(__file__).resolve().parent
ALIASES = dict(DIRECTORIES)
for _old, _new in DIRECTORIES.items():
    ALIASES[str(Path(_new).parent / Path(_old).name).replace('\\', '/')] = _new
ORDERED = sorted(ALIASES.items(), key=lambda item: len(item[0]), reverse=True)


def relative_path(value):
    for old, new in ORDERED:
        if value.casefold() == old.casefold() or value.casefold().startswith(old.casefold() + '/'):
            return new + value[len(old):]
    return value


def relocated_path(value, root=None):
    if value is None or not isinstance(value, (str, os.PathLike)):
        return value
    root = str(Path(root or ROOT).absolute()).replace('\\', '/').rstrip('/')
    original = str(value)
    normalized = original.replace('\\', '/')
    if normalized.casefold().startswith(root.casefold() + '/'):
        result = root + '/' + relative_path(normalized[len(root)+1:])
        return result if os.sep == '/' else result.replace('/', os.sep)
    if not Path(normalized).is_absolute() and ':' not in normalized:
        return relative_path(normalized)
    return original


def manifest_paths(manifest):
    result = dict(manifest)
    for key in ('database', 'output_root'):
        if result.get(key):
            result[key] = relocated_path(result[key])
    return result


def task_paths(task):
    task = dict(task)
    if task.get('manifest'):
        task['manifest'] = relocated_path(task['manifest'])
    if task.get('connection', {}).get('worker_python'):
        task['connection'] = dict(task['connection'], worker_python=relocated_path(task['connection']['worker_python']))
    return task


def request_paths(request):
    # Comparison only: keep the original request when reconnecting to an immutable remote attempt.
    return {**request, **{key: relocated_path(request[key]) for key in ('result_directory', 'cache_root') if key in request}}


def _sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024**2), b''):
            h.update(block)
    return h.digest()


def _native_created(pid):
    """Check a saved controller identity before psutil has been installed.

    PID existence alone is insufficient: Windows regularly reuses old PIDs.
    """
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.restype = wintypes.HANDLE
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 5:
                raise RuntimeError('Cannot inspect saved controller process ' + str(pid) + '; close that GBR controller before migrating directories.')
            return None
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        times = [wintypes.FILETIME() for _ in range(4)]
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                raise RuntimeError('Cannot read controller process state: ' + str(pid))
            if exit_code.value != 259:  # STILL_ACTIVE
                return None
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
                raise RuntimeError('Cannot read controller process creation time: ' + str(pid))
            return ((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime) / 10_000_000 - 11644473600
        finally:
            kernel.CloseHandle(handle)
    try:
        stat = Path('/proc', str(pid), 'stat').read_text().rpartition(')')[2].split()
        if stat[0] == 'Z':
            return None
        boot = int(next(line.split()[1] for line in Path('/proc/stat').read_text().splitlines() if line.startswith('btime ')))
        return boot + int(stat[19]) / os.sysconf('SC_CLK_TCK')
    except (FileNotFoundError, ProcessLookupError):
        return None
    except (OSError, StopIteration, ValueError, IndexError) as exc:
        raise RuntimeError('Cannot inspect saved controller process ' + str(pid)) from exc


def _running(root):
    try:
        import psutil
    except ImportError:
        psutil = None
    scripts = {'run_gbr.py', 'run_review.py', 'run_generation.py', 'first_local_test.py', 'app_jobs.py', 'worker.py', 'install_worker.py'}
    prefix = str(root).replace('\\', '/').rstrip('/').casefold() + '/'
    commands = []
    ancestors = set()
    if psutil is not None:
        try:
            ancestors = {p.pid for p in psutil.Process().parents()}
        except psutil.Error:
            pass
        for process in psutil.process_iter(['pid', 'cmdline', 'cwd']):
            try:
                commands.append((process.info['pid'], process.info['cmdline'] or [], process.info.get('cwd')))
            except psutil.Error:
                continue
    elif os.name != 'nt':
        for process in Path('/proc').iterdir():
            if process.name.isdigit():
                try:
                    commands.append((int(process.name), (process / 'cmdline').read_text().split('\0'), str((process / 'cwd').resolve())))
                except OSError:
                    pass
        parent = os.getppid()
        while parent > 1 and parent not in ancestors:
            ancestors.add(parent)
            try:
                parent = int(next(line.split()[1] for line in Path('/proc', str(parent), 'status').read_text().splitlines() if line.startswith('PPid:')))
            except (OSError, StopIteration, ValueError):
                break
    for pid, args, cwd in commands:
        if pid == os.getpid():
            continue
        matched = False
        for index, arg in enumerate(args):
            normalized = arg.replace('\\', '/')
            if not normalized.startswith('/') and ':' not in normalized and cwd:
                normalized = str(Path(cwd) / normalized).replace('\\', '/')
            if normalized.casefold().startswith(prefix) and Path(normalized).name.casefold() in scripts:
                # py.exe/base Python wait for the venv child. That *same invocation*
                # is part of this startup, not a second application. Controllers
                # and unrelated launcher processes still block directory moves.
                same_startup = (pid in ancestors and Path(normalized).name.casefold() == 'run_gbr.py'
                                and Path(sys.argv[0].replace('\\', '/')).name.casefold() == 'run_gbr.py'
                                and args[index+1:] == sys.argv[1:])
                matched = not same_startup
                break
        modules = {'_review', '_review.__main__', 'review', 'review.__main__', '_gbr_generation.app_jobs', 'gbr_generation.app_jobs'}
        if '-m' in args:
            index = args.index('-m')
            in_repo = bool(cwd and (str(cwd).replace('\\', '/').rstrip('/') + '/').casefold().startswith(prefix))
            matched |= in_repo and index + 1 < len(args) and args[index + 1] in modules
        if matched:
            raise RuntimeError('Close the running GBR application or generation controller before updating directory names. Process: ' + str(pid))
    locations = {root / p for p in ALIASES if p.endswith('/runtime') or p.endswith('/_runtime')}
    locations |= {root / DIRECTORIES[p] for p in DIRECTORIES if p.endswith('/runtime')}
    for runtime in locations:
        for path in runtime.glob('app_jobs/*/process.json'):
            try:
                saved = json.loads(path.read_text())
                if psutil is None:
                    created = _native_created(saved['pid'])
                    active = created is not None and abs(created - saved['created']) < .02
                else:
                    process = psutil.Process(saved['pid'])
                    active = process.is_running() and process.status() != psutil.STATUS_ZOMBIE and abs(process.create_time() - saved['created']) < .01
                if active:
                    raise RuntimeError('Finish or pause generation and close the app before updating directory names. Active controller: ' + str(path.parent))
            except (OSError, ValueError, KeyError, TypeError):
                continue
            except Exception as exc:
                if psutil is not None and isinstance(exc, psutil.Error):
                    continue
                raise


def _paths_in_plan(value, root):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if key in {'source', 'destination', 'audio_destination', 'uri'} and isinstance(item, str):
                result[key] = relocated_path(item, root)
            elif key == 'generation_yamls' and isinstance(item, dict):
                result[key] = {k: relocated_path(v, root) for k, v in item.items()}
            else:
                result[key] = _paths_in_plan(item, root)
        return result
    if isinstance(value, list):
        return [_paths_in_plan(v, root) for v in value]
    return value


def _database_paths(database, root):
    if not database.is_file():
        return
    columns = {
        'audio': ('archive_path',), 'media_files': ('path',), 'generation_sources': ('yaml_path',),
        'generations': ('original_directory',),
        'generation_metadata': ('yaml_archive_path',), 'analyses': ('preview_path', 'spectrogram_path'),
        'scan_errors': ('path',), 'generation_artifacts': ('uri',),
        'generation_execution_state': ('output_directory',),
    }
    with closing(sqlite3.connect(database, timeout=30)) as conn, conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, fields in columns.items():
            if table not in tables:
                continue
            available = {r[1] for r in conn.execute('PRAGMA table_info(' + table + ')')}
            for field in set(fields) & available:
                for rowid, old in conn.execute('SELECT rowid,' + field + ' FROM ' + table).fetchall():
                    new = relocated_path(old, root)
                    if new != old:
                        conn.execute('UPDATE ' + table + ' SET ' + field + '=? WHERE rowid=?', (new, rowid))
        if 'archive_operations' in tables:
            for rowid, raw in conn.execute('SELECT rowid,plan_json FROM archive_operations').fetchall():
                plan = json.loads(raw)
                updated = _paths_in_plan(plan, root)
                if updated != plan:
                    conn.execute('UPDATE archive_operations SET plan_json=? WHERE rowid=?', (json.dumps(updated, ensure_ascii=False), rowid))
        if 'sessions' in tables and 'filters_json' in {r[1] for r in conn.execute('PRAGMA table_info(sessions)')}:
            for rowid, raw in conn.execute('SELECT rowid,filters_json FROM sessions').fetchall():
                filters = json.loads(raw)
                if filters.get('directory'):
                    directory = relocated_path(filters['directory'], root)
                    if directory != filters['directory']:
                        filters['directory'] = directory
                        conn.execute('UPDATE sessions SET filters_json=? WHERE rowid=?', (json.dumps(filters, ensure_ascii=False), rowid))


def _rename_case(source, target):
    """Rename a verified directory alias, or defer a locked cosmetic Windows change.

    migrate() has already established that both spellings refer to one folder.
    Windows continues to resolve the canonical spelling if a launcher, current
    directory or loaded interpreter holds the existing spelling open.
    """
    windows_alias = sys.platform == 'win32' and str(source).casefold() == str(target).casefold()
    if windows_alias:
        current = os.getcwd().replace('\\', '/').rstrip('/').casefold()
        original = str(source).replace('\\', '/').rstrip('/').casefold()
        if current == original or current.startswith(original + '/'):
            return False
    temporary = source.with_name('.gbr-directory-' + uuid.uuid4().hex)
    try:
        source.rename(temporary)
    except OSError as exc:
        if windows_alias and getattr(exc, 'winerror', None) in {32, 33}:
            return False
        raise
    try:
        temporary.rename(target)
    except OSError as exc:
        try:
            temporary.rename(source)
        except OSError as restore_error:
            raise RuntimeError('Directory rename could not restore ' + str(source) + '; files remain at ' + str(temporary)) from restore_error
        if windows_alias and getattr(exc, 'winerror', None) in {32, 33}:
            return False
        raise
    return True


def migrate(root=None):
    root = Path(root or ROOT).absolute()
    candidates = {root / p for p in ALIASES}
    files, old_dirs, case_dirs = set(), set(), []
    for old in sorted(candidates, key=lambda p: len(p.parts)):
        new = Path(relocated_path(old, root))
        if not old.exists() or str(old) == str(new):
            continue
        if old.is_symlink():
            raise ValueError('Directory migration retained a symbolic link for inspection: ' + str(old))
        if new.exists() and os.path.samefile(old, new):
            # Git on Windows may preserve a directory's old case.
            actual = next((p for p in old.parent.iterdir() if p.name.casefold() == old.name.casefold()), old)
            if actual.name != new.name:
                case_dirs.append((actual, new))
            continue
        for parent, dirs, names in os.walk(old, followlinks=False):
            folder = Path(parent)
            old_dirs.add(folder)
            if any((folder / name).is_symlink() for name in dirs + names):
                raise ValueError('Directory migration retained symbolic links for inspection: ' + str(folder))
            files.update(folder / name for name in names)
    moves, duplicates, obsolete_cache = [], [], []
    for source in sorted(files):
        target = Path(relocated_path(source, root))
        if source == target:
            continue
        if target.exists():
            if '__pycache__' in source.parts and source.suffix in {'.pyc', '.pyo'} and target.is_file() and not target.is_symlink():
                obsolete_cache.append(source)
                continue
            if target.is_symlink() or not target.is_file() or _sha(source) != _sha(target):
                raise ValueError('Directory rename has conflicting files; neither was changed: ' + str(source) + ' / ' + str(target))
            duplicates.append(source)
        else:
            moves.append((source, target))
    # Every collision is checked before the first data move. A failed DB update rolls the moves back.
    if moves or duplicates or case_dirs or obsolete_cache:
        _running(root)
    moved, changed_case, deferred_case = [], [], []
    try:
        for source, target in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
            moved.append((source, target))
        for source, target in sorted(set(case_dirs), key=lambda item: len(item[0].parts), reverse=True):
            if _rename_case(source, target):
                changed_case.append((source, target))
            else:
                deferred_case.append((source, target))
        _database_paths(root / '_98_database/gbr.sqlite3', root)
    except Exception:
        for source, target in reversed(changed_case):
            temporary = target.with_name('.gbr-directory-' + uuid.uuid4().hex)
            target.rename(temporary)
            temporary.rename(source)
        for source, target in reversed(moved):
            source.parent.mkdir(parents=True, exist_ok=True)
            target.rename(source)
        raise
    for source in duplicates + obsolete_cache:
        source.unlink()
    for folder in sorted(old_dirs, key=lambda p: len(p.parts), reverse=True):
        try:
            folder.rmdir()
        except OSError:
            pass
    return {'moved_files': len(moves), 'merged_identical_files': len(duplicates),
            'discarded_old_bytecode': len(obsolete_cache), 'case_renames': len(changed_case),
            'deferred_case_renames': len(deferred_case)}

