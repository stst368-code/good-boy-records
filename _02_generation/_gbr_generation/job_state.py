"""Small replaceable controller snapshots; SQLite remains the job authority."""
import json
import os
from pathlib import Path
import tempfile
import time
_cleaned={}


def read_state(path, default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return dict(default or {})


def atomic_state(path, value, attempts=8):
    path=Path(path)
    if time.monotonic()-_cleaned.get(str(path),0)>60:
        _cleaned[str(path)]=time.monotonic()
        for old in path.parent.glob('.'+path.stem+'-*.tmp'):
            try:
                if time.time()-old.stat().st_mtime>60:old.unlink()
            except OSError:pass
    descriptor,name=tempfile.mkstemp(prefix='.'+path.stem+'-',suffix='.tmp',dir=path.parent)
    temporary=Path(name)
    try:
        with os.fdopen(descriptor,'w',encoding='utf-8') as stream:
            json.dump(value,stream,ensure_ascii=False,allow_nan=False);stream.flush();os.fsync(stream.fileno())
        for attempt in range(attempts):
            try:temporary.replace(path);return True
            except PermissionError:
                if attempt+1<attempts:time.sleep(min(.01*2**attempt,.15))
        # Readers/antivirus can deny rename on Windows. A stale snapshot must
        # never fail or restart an otherwise healthy generation.
        return False
    finally:
        try:temporary.unlink(missing_ok=True)
        except PermissionError:pass
