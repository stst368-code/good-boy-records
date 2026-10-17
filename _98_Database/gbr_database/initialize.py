"""Create a new empty platform through the same verified migration path."""
from contextlib import closing
from pathlib import Path
import sqlite3
import sys
import tempfile

from .migration import migrate


def initialize(path):
    path = Path(path).resolve()
    if path.exists():
        raise FileExistsError(path)
    root = Path(__file__).resolve().parents[2]
    sys.path.insert(0, str(root / "_03_Review" / "_Generation_Review"))
    from review.db import Database
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gbr-empty-", dir=path.parent) as temporary:
        seed = Path(temporary)
        Database(seed / "review.sqlite")
        with closing(sqlite3.connect(seed / "generation.sqlite")) as conn:
            conn.executescript("""
                CREATE TABLE jobs(job_id TEXT PRIMARY KEY,output_stem TEXT,params_json TEXT,status TEXT);
                CREATE TABLE counters(title_key TEXT PRIMARY KEY,last_value INTEGER);
                CREATE TABLE timings(timing_id TEXT PRIMARY KEY,job_id TEXT,worker_id TEXT,gpu_name TEXT,
                    stage TEXT,success INTEGER,duration_seconds REAL,billable INTEGER,
                    estimated_cost REAL,cost_currency TEXT);
            """)
        return migrate(seed / "generation.sqlite", seed / "review.sqlite", path, source_id="empty-bootstrap")
