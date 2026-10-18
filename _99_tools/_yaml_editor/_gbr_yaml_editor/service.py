"""Preview tokens, bounded backups and recoverable source edits. No additional database."""
from collections import OrderedDict
from datetime import datetime, timezone
import difflib
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from _gbr_generation.catalogue import Catalogue
from _gbr_generation.yaml_document import YamlDocument
from .document import Source, edit, repair_text, key_name
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from _gbr_layout import relocated_path

MAX_FILE = 2 * 1024**2
MAX_BATCH = 64 * 1024**2
MAX_FILES = 2000
RETENTION = 20
SKIP = {'.git', '.venv', 'venv', '__pycache__', 'node_modules', '_Archive', 'runtime'}


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def atomic(path, raw, mode=0o600):
    path = Path(path)
    fd, temp = tempfile.mkstemp(prefix='.gbr-edit-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


class Editor:
    def __init__(self, repo_root, backup_root=None):
        self.repo = Path(repo_root).resolve()
        self.backups = Path(backup_root or self.repo / '_97_app/_runtime/yaml_edits').resolve()
        self.default_order_template = self.repo / '_01_writing/_built/track-template.yaml'
        self.previews = OrderedDict()
        self.lock = threading.RLock()

    def root(self, directory):
        try:
            root = Path(relocated_path(directory, self.repo)).expanduser().resolve(strict=True)
        except OSError as exc:
            raise ValueError('Directory not found or unavailable on this computer') from exc
        if not root.is_dir():
            raise ValueError('Choose an existing directory on this computer')
        if root == self.backups or root.is_relative_to(self.backups):
            raise ValueError('Editor backups cannot be edited')
        return root

    def path(self, root, relative):
        if not isinstance(relative, str) or '\\' in relative:
            raise ValueError('Choose a YAML from the scanned directory')
        parts = PurePosixPath(relative)
        if parts.is_absolute() or '..' in parts.parts or not parts.parts or parts.suffix.lower() not in {'.yaml', '.yml'}:
            raise ValueError('Choose a relative .yaml or .yml file')
        path = Path(relocated_path(root.joinpath(*parts.parts), self.repo))
        for parent in (path, *path.parents):
            if parent == root:
                break
            if parent.is_symlink():
                raise ValueError('Symbolic links are not editable')
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root) or resolved.is_relative_to(self.backups):
            raise ValueError('File is outside the selected directory')
        if not path.is_file() or path.stat().st_size > MAX_FILE:
            raise ValueError('File is unavailable or exceeds the 2 MiB editor limit')
        return path

    def catalogue(self, root):
        folder = next((p for p in (root, *root.parents) if (p / 'track_list.csv').is_file()), None)
        if folder is None:
            return {}, None
        try:
            catalogue = Catalogue(folder)
        except (ValueError, OSError) as exc:
            return {}, f'Catalogue unavailable: {exc}'
        result = {}
        for item in catalogue.inputs:
            result.setdefault(item.path, []).append(item)
        return result, None

    def scan(self, directory=None, recursive=True):
        root = self.root(directory or self.repo / '_01_writing/_built')
        catalogue, warning = self.catalogue(root)
        files, visited, truncated = [], 0, False
        for parent, dirs, names in os.walk(root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in SKIP and not (Path(parent)/d).is_symlink()) if recursive else []
            for name in sorted(names):
                visited += 1
                if visited > 100000 or len(files) >= MAX_FILES:
                    truncated = True
                    break
                if Path(name).suffix.lower() not in {'.yaml', '.yml'}:
                    continue
                relative = (Path(parent)/name).relative_to(root).as_posix()
                row = {'path': relative, 'sha256': None, 'fields': [], 'metadata': {}, 'issues': []}
                try:
                    path = self.path(root, relative)
                    raw = path.read_bytes()
                    row['sha256'] = digest(raw)
                    # Partial inspection still exposes duplicate root keys for explicit repair.
                    source = Source(raw)
                    row['fields'] = [e.name for e in source.entries]
                    values = YamlDocument(raw).values
                    row['metadata'] = {k: values.get(k) for k in ('title', 'song_id', 'variant_id', 'cluster', 'style')
                                       if (isinstance(values.get(k), (str, int, float, bool)) and not (isinstance(values.get(k), float) and not math.isfinite(values[k]))) or values.get(k) is None}
                    matches = catalogue.get(path.resolve(), [])
                    if len(matches) == 1:
                        item = matches[0]
                        row['issues'].extend(i.message for i in item.issues)
                        for key in ('song_id', 'variant_id'):
                            expected = getattr(item, key)
                            if expected and str(values.get(key)) != expected:
                                row['issues'].append(f'{key}: YAML has {values.get(key)!r}; catalogue has {expected}')
                    elif catalogue:
                        row['issues'].append('No unique catalogue match')
                except (ValueError, OSError) as exc:
                    row['issues'].append(str(exc))
                files.append(row)
            if truncated:
                break
        return {'root': str(root), 'files': files, 'truncated': truncated, 'warning': warning,
                'max_files': MAX_FILES, 'backup_retention': RETENTION,
                'default_order_template': str(self.default_order_template)}

    def read(self, directory, relative):
        root = self.root(directory)
        raw = self.path(root, relative).read_bytes()
        return {'path': relative, 'sha256': digest(raw), 'text': raw.decode('utf-8-sig')}

    def order_template(self, filename=None):
        path = Path(filename).expanduser() if filename else self.default_order_template
        if not path.is_absolute():
            path = self.repo / path
        if path.suffix.lower() not in {'.yaml', '.yml'}:
            raise ValueError('Choose a .yaml or .yml order template')
        if path.is_symlink():
            raise ValueError('Symbolic links cannot be used as order templates')
        try:
            path = path.resolve(strict=True)
            if not path.is_file() or path.stat().st_size > MAX_FILE:
                raise ValueError('Order template must be a YAML file smaller than 2 MiB')
            raw = path.read_bytes()
        except OSError as exc:
            raise ValueError(f'Order template is unavailable: {path}. Choose another template YAML.') from exc
        document = YamlDocument(raw)
        # Read only the root key order. Template values and comments are never copied.
        source = Source(raw)
        return {'path': str(path), 'sha256': digest(raw), 'keys': list(document.fields),
                'fields': [{'key': entry.name, 'block': source.text[entry.start:entry.end]}
                           for entry in source.entries], 'indent': source.indent}

    def resolve_operations(self, operations):
        resolved, templates, loaded = [], [], {}
        for original in operations:
            if not isinstance(original, dict):
                raise ValueError('Each operation must be an object')
            operation = dict(original)
            if operation.get('kind') in {'reorder', 'add_template'}:
                # Keep previously saved explicit key-list operations compatible.
                mode = 'template' if operation.get('kind') == 'add_template' else operation.get('order_source', 'manual' if 'keys' in operation else 'template')
                if mode not in {'template', 'manual'}:
                    raise ValueError('Order source must be template or manual')
                operation['order_source'] = mode
                if mode == 'template':
                    requested = operation.get('template_path') or None
                    if requested is not None and not isinstance(requested, str):
                        raise ValueError('Template path must be text')
                    if requested not in loaded:
                        loaded[requested] = self.order_template(requested)
                        templates.append(loaded[requested])
                    template = loaded[requested]
                    operation.update(keys=template['keys'], template_path=template['path'],
                                     template_sha256=template['sha256'])
                    if operation['kind'] == 'add_template':
                        fields = operation.get('fields', template['keys'])
                        if not isinstance(fields, list) or not fields or any(not isinstance(key, str) for key in fields) or len(set(fields)) != len(fields) or set(fields) - set(template['keys']):
                            raise ValueError('Choose unique fields from the selected template')
                        defaults = operation.get('defaults', {})
                        if not isinstance(defaults, dict) or set(defaults) - set(fields):
                            raise ValueError('Defaults must name selected template fields')
                        for key in fields:
                            key_name(key)
                        if type(operation.get('reorder', True)) is not bool:
                            raise ValueError('Template reordering must be true or false')
                        operation.update(template_fields=[f for f in template['fields'] if f['key'] in fields],
                                         template_indent=template['indent'], defaults=defaults)
            resolved.append(operation)
        return resolved, templates

    def preview(self, directory, files, operations=None, text=None, expected=None):
        root = self.root(directory)
        if not files or len(files) > MAX_FILES or len(set(files)) != len(files):
            raise ValueError('Select between 1 and 2000 unique YAML files')
        if text is not None and len(files) != 1:
            raise ValueError('Text repair edits one file at a time')
        if text is None and (not operations or len(operations) > 100):
            raise ValueError('Add between 1 and 100 edit operations')
        if text is not None and not isinstance(text, str):
            raise ValueError('Text repair requires text')
        operations, templates = self.resolve_operations(operations or [])
        catalogue, warning = self.catalogue(root) if any(o.get('kind') == 'catalogue_ids' for o in operations or []) else ({}, None)
        records, public, size = [], [], 0
        for relative in files:
            row = {'path': relative, 'state': 'error', 'error': None, 'diff': ''}
            try:
                path = self.path(root, relative)
                before = path.read_bytes()
                if expected and expected.get(relative) != digest(before):
                    raise ValueError('File changed since scan/read; rescan before editing')
                ids = None
                matches = catalogue.get(path.resolve(), [])
                if len(matches) == 1 and not matches[0].issues and matches[0].song_id and matches[0].variant_id:
                    ids = {'song_id': matches[0].song_id, 'variant_id': matches[0].variant_id}
                if warning:
                    raise ValueError(warning)
                after = repair_text(before, text) if text is not None else edit(before, operations, ids)
                size += len(before) + len(after)
                if size > MAX_BATCH:
                    raise ValueError('Batch exceeds 64 MiB; select fewer files')
                row['state'] = 'unchanged' if before == after else 'ready'
                if before != after:
                    diff = ''.join(difflib.unified_diff(before.decode('utf-8-sig').splitlines(keepends=True), after.decode('utf-8-sig').splitlines(keepends=True), fromfile=relative+' (before)', tofile=relative+' (after)'))
                    row['diff'] = diff[:100000]
                    row['diff_truncated'] = len(diff) > 100000
                    records.append({'path': relative, 'before': before, 'after': after})
            except (ValueError, OSError) as exc:
                row['error'] = str(exc)
            public.append(row)
        result = self._remember(root, records, public, operations or [{'kind': 'text_repair'}])
        result['order_templates'] = templates
        return result

    def _remember(self, root, records, public, operations):
        with self.lock:
            now = time.time()
            for token in list(self.previews):
                if now - self.previews[token]['created'] > 1800:
                    self.previews.pop(token)
            # Bound total retained source bytes, not just the token count.
            while self.previews and (len(self.previews) >= 10 or sum(p['bytes'] for p in self.previews.values()) + sum(len(r['before'])+len(r['after']) for r in records) > MAX_BATCH):
                self.previews.popitem(last=False)
            token = uuid.uuid4().hex
            self.previews[token] = {'root': root, 'records': records, 'public': public, 'operations': operations,
                                    'created': now, 'bytes': sum(len(r['before'])+len(r['after']) for r in records)}
            return {'token': token, 'root': str(root), 'files': public, 'ready': len(records), 'errors': sum(r['state']=='error' for r in public),
                    'unchanged': sum(r['state']=='unchanged' for r in public), 'expires_seconds': 1800}

    def _journal(self, folder, journal):
        atomic(folder/'journal.json', json.dumps(journal, ensure_ascii=False, indent=2).encode('utf-8'))

    def apply(self, token, skip_errors=False):
        with self.lock:
            preview = self.previews.get(token)
            if not preview or time.time()-preview['created'] > 1800:
                raise ValueError('Preview expired; preview the changes again')
            if any(r['state']=='error' for r in preview['public']) and not skip_errors:
                raise ValueError('Some inputs have errors. Fix them or explicitly choose to skip errored files')
            records, root = preview['records'], preview['root']
            if not records:
                raise ValueError('No changed, valid files to save')
            for record in records:
                if self.path(root, record['path']).read_bytes() != record['before']:
                    raise ValueError(f"{record['path']}: changed after preview; no files were saved")
            batch = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:12]
            self.backups.mkdir(parents=True, exist_ok=True)
            folder = self.backups/batch
            folder.mkdir(mode=0o700)
            journal = {'version': 1, 'batch': batch, 'root': str(root), 'state': 'prepared', 'created_at': datetime.now(timezone.utc).isoformat(),
                       'operations': preview['operations'], 'files': [{'path': r['path'], 'before': digest(r['before']), 'after': digest(r['after'])} for r in records]}
            staged, changed = [], []
            try:
                with zipfile.ZipFile(folder/'before.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                    for record in records:
                        archive.writestr(record['path'], record['before'])
                with (folder/'before.zip').open('r+b') as backup:
                    os.fsync(backup.fileno())
                self._journal(folder, journal)
                for record in records:
                    path = self.path(root, record['path'])
                    fd, temp = tempfile.mkstemp(prefix='.gbr-edit-', suffix='.tmp', dir=path.parent)
                    staged.append(Path(temp))
                    with os.fdopen(fd, 'wb') as handle:
                        handle.write(record['after'])
                        handle.flush()
                        os.fsync(handle.fileno())
                    os.chmod(temp, stat.S_IMODE(path.stat().st_mode))
                journal['state'] = 'applying'
                self._journal(folder, journal)
                for record, temp in zip(records, staged):
                    path = self.path(root, record['path'])
                    if path.read_bytes() != record['before']:
                        raise ValueError(f"{record['path']}: source changed while saving")
                    os.replace(temp, path)
                    changed.append(record)
                journal['state'] = 'committed'
                self._journal(folder, journal)
            except Exception:
                conflicts = []
                for record in reversed(changed):
                    try:
                        path = self.path(root, record['path'])
                        if digest(path.read_bytes()) != digest(record['after']):
                            raise ValueError('External edit detected')
                        atomic(path, record['before'], stat.S_IMODE(path.stat().st_mode))
                    except (OSError, ValueError):
                        conflicts.append(record['path'])
                journal['state'] = 'recovery_needed' if conflicts else 'rolled_back'
                journal['conflicts'] = conflicts
                self._journal(folder, journal)
                raise
            finally:
                for temp in staged:
                    temp.unlink(missing_ok=True)
            self.previews.pop(token, None)
            try:
                self._prune()
            except OSError:
                pass  # A cleanup failure must not turn a completed save into a reported failure.
            return {'batch': batch, 'saved': len(records), 'skipped': sum(r['state']=='error' for r in preview['public'])}

    def history(self):
        result = []
        if self.backups.is_dir():
            for folder in sorted(self.backups.iterdir(), reverse=True):
                if not folder.is_dir() or folder.is_symlink() or not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', folder.name):
                    continue
                try:
                    journal = json.loads((folder/'journal.json').read_text('utf-8'))
                    if journal.get('version') != 1 or journal.get('batch') != folder.name:
                        continue
                    result.append({k: journal[k] for k in ('batch', 'root', 'state', 'created_at')} | {'files': len(journal['files'])})
                except (OSError, ValueError, KeyError):
                    continue
        return result

    def restore_preview(self, batch):
        with self.lock:
            if not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[a-f0-9]{12}', batch):
                raise ValueError('Invalid backup selection')
            folder = self.backups/batch
            if folder.is_symlink():
                raise ValueError('Invalid backup selection')
            journal = json.loads((folder/'journal.json').read_text('utf-8'))
            root = self.root(journal['root'])
            records, public = [], []
            with zipfile.ZipFile(folder/'before.zip') as archive:
                for entry in journal['files']:
                    relative = entry['path']
                    row = {'path': relative, 'state': 'error', 'error': None, 'diff': ''}
                    try:
                        before = self.path(root, relative).read_bytes()
                        after = archive.read(relative)
                        if digest(after) != entry['before']:
                            raise ValueError('Backup checksum does not match its journal')
                        if digest(before) not in {entry['before'], entry['after']}:
                            raise ValueError('File was edited subsequently; restore would overwrite newer changes')
                        row['state'] = 'unchanged' if before == after else 'ready'
                        if before != after:
                            row['diff'] = ''.join(difflib.unified_diff(before.decode('utf-8-sig').splitlines(keepends=True), after.decode('utf-8-sig').splitlines(keepends=True), fromfile=relative+' (current)', tofile=relative+' (restore)'))[:100000]
                            records.append({'path': relative, 'before': before, 'after': after})
                    except (ValueError, OSError, KeyError) as exc:
                        row['error'] = str(exc)
                    public.append(row)
            return self._remember(root, records, public, [{'kind': 'restore', 'batch': batch}])

    def _prune(self):
        completed = [r for r in self.history() if r['state'] == 'committed']
        for row in completed[RETENTION:]:
            folder = self.backups/row['batch']
            # Remove only files this editor owns. Interrupted or unexpected content is retained.
            if {p.name for p in folder.iterdir()} <= {'journal.json', 'before.zip'}:
                for p in folder.iterdir():
                    p.unlink()
                folder.rmdir()
