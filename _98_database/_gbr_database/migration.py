"""Stage a shared database from consistent SQLite snapshots, preserving review identity.

Only a new destination is published. Sources are opened read-only and copied with
SQLite's backup API, including committed WAL contents. Legacy queue rows remain
historical rows: importing never schedules inference or performs archive actions.
"""
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import ntpath
import os
from pathlib import Path
import sqlite3
import tempfile

PARAMETERS = ("encoder_cfg", "encoder_seed", "top_k", "sampler", "scheduler",
              "sampler_cfg", "sampler_seed", "sampler_steps")
NUMERIC = {"encoder_cfg", "top_k", "sampler_cfg", "sampler_steps"}
LEGACY = {"jobs": "job_id", "timings": "timing_id", "counters": "title_key"}
REVIEW_REQUIRED = {"audio", "generations", "generation_metadata", "reviews",
                   "review_history", "reviewers", "assessments", "bookmarks",
                   "publishing_selection", "schema_version"}
SCHEMA = Path(__file__).with_name("schema.sql")


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def quoted(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def connect_readonly(path):
    path = Path(path).resolve(strict=True)
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def tables(conn):
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]


def counts(conn):
    return {name: conn.execute("SELECT COUNT(*) FROM " + quoted(name)).fetchone()[0]
            for name in tables(conn)}


def check_integrity(conn):
    result = [r[0] for r in conn.execute("PRAGMA integrity_check")]
    if result != ["ok"]:
        raise ValueError("SQLite integrity check failed: " + repr(result[:10]))
    errors = [tuple(r) for r in conn.execute("PRAGMA foreign_key_check").fetchmany(10)]
    if errors:
        raise ValueError("SQLite foreign-key check failed: " + repr(errors))


def table_fingerprint(conn, name):
    """Hash typed cell values and row order without normalizing text or BLOBs."""
    return _projected_fingerprint(conn, "SELECT * FROM " + quoted(name) + " ORDER BY rowid")


def _projected_fingerprint(conn, sql):
    h = hashlib.sha256()
    for row in conn.execute(sql):
        for value in row:
            if value is None:
                tag, payload = b"n", b""
            elif isinstance(value, bytes):
                tag, payload = b"b", value
            elif isinstance(value, str):
                tag, payload = b"s", value.encode("utf-8")
            elif isinstance(value, int):
                tag, payload = b"i", str(value).encode("ascii")
            else:
                tag, payload = b"f", repr(value).encode("ascii")
            h.update(tag + str(len(payload)).encode("ascii") + b":" + payload)
        h.update(b";")
    return h.hexdigest()


def snapshot(source, destination):
    with closing(connect_readonly(source)) as src, closing(sqlite3.connect(destination)) as dst:
        src.backup(dst)


def validate_sources(generation, review):
    if not set(LEGACY).issubset(tables(generation)):
        raise ValueError("Generation database must contain jobs, timings and counters")
    if not REVIEW_REQUIRED.issubset(tables(review)):
        raise ValueError("Review database must be migrated to the reviewer/bookmark schema first")
    if "platform_schema_version" in tables(review):
        raise ValueError("Review source is already a shared platform database; select the original review snapshot")
    check_integrity(generation)
    check_integrity(review)
    if generation.execute("SELECT COUNT(*) FROM timings t LEFT JOIN jobs j ON j.job_id=t.job_id WHERE j.job_id IS NULL").fetchone()[0]:
        raise ValueError("Generation timing records reference missing jobs")
def validate_selected_parameters(generation):
    selected = "job_id IN (SELECT job_id FROM temp.gbr_selected_jobs)"
    if generation.execute("SELECT COUNT(*) FROM jobs WHERE (params_json IS NULL OR NOT json_valid(params_json)) AND " + selected).fetchone()[0]:
        raise ValueError("Generation database contains invalid params_json; repair before importing")
    if generation.execute("SELECT COUNT(*) FROM jobs WHERE json_type(CASE WHEN json_valid(params_json) THEN params_json ELSE '{}' END)!='object' AND " + selected).fetchone()[0]:
        raise ValueError("Generation params_json must contain objects; repair before importing")


def _json_object(text):
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def output_candidates(generation, review):
    """Return evidence, never assert audio identity using filenames alone."""
    by_stem = defaultdict(list)
    for row in generation.execute("SELECT job_id,output_stem,params_json,status FROM jobs WHERE output_stem IS NOT NULL AND COALESCE(status,'')!='withdrawn'"):
        by_stem[row["output_stem"].casefold()].append(row)
    for record in review.execute("""SELECT g.id,g.original_filename,m.parsed_json
        FROM generations g JOIN generation_metadata m ON m.generation_id=g.id"""):
        stem = ntpath.splitext(ntpath.basename(record["original_filename"]))[0].casefold()
        candidates = by_stem.get(stem, ())
        for job in candidates:
            effective, meta = _json_object(job["params_json"]), _json_object(record["parsed_json"])
            matched, conflicts, missing = [], [], []
            for field in PARAMETERS:
                a, b = effective.get(field), meta.get(field)
                if a is None or b is None or a == "" or b == "":
                    missing.append(field)
                    continue
                same = str(a).casefold() == str(b).casefold()
                if field in NUMERIC:
                    try:
                        same = float(a) == float(b)
                    except (ValueError, TypeError):
                        pass
                (matched if same else conflicts).append(field)
            level = ("ambiguous" if len(candidates) != 1 else "conflict" if conflicts
                     else "filename_only" if missing else "parameters_match")
            yield job["job_id"], record["id"], level, {
                "output_stem": stem, "matched_parameters": matched,
                "conflicting_parameters": conflicts, "missing_parameters": missing,
                "audio_hash_verified": False,
            }


def select_history(generation, review):
    """Keep unambiguous filename/parameter matches; never import withdrawn jobs."""
    links = [row for row in output_candidates(generation, review)
             if row[2] in {"parameters_match", "filename_only"}]
    generation.execute("CREATE TEMP TABLE IF NOT EXISTS gbr_selected_jobs(job_id TEXT PRIMARY KEY)")
    generation.execute("DELETE FROM temp.gbr_selected_jobs")
    generation.executemany("INSERT OR IGNORE INTO temp.gbr_selected_jobs VALUES(?)", ((r[0],) for r in links))
    return links


def legacy_query(name):
    where = " WHERE job_id IN (SELECT job_id FROM temp.gbr_selected_jobs)" if name in {"jobs", "timings"} else ""
    return "SELECT * FROM " + quoted(name) + where + " ORDER BY rowid"


def inspect_connections(generation, review):
    links = list(output_candidates(generation, review))
    levels = Counter(item[2] for item in links)
    linked_review_ids = {item[1] for item in links}
    gc, rc = counts(generation), counts(review)
    kept = {name: generation.execute("SELECT COUNT(*) FROM (" + legacy_query(name) + ")").fetchone()[0] for name in LEGACY}
    withdrawn = generation.execute("SELECT COUNT(*) FROM jobs WHERE status='withdrawn'").fetchone()[0]
    return {
        "generation_tables": gc, "review_tables": rc,
        "history_policy": "drop_withdrawn_and_unmatched_legacy_jobs; preserve_review_records",
        "imported_legacy_tables": kept,
        "excluded_legacy_jobs": {"withdrawn": withdrawn, "without_usable_review_match": gc["jobs"] - kept["jobs"] - withdrawn},
        "excluded_legacy_timing_events": gc["timings"] - kept["timings"],
        "legacy_statuses": dict(generation.execute("SELECT status,COUNT(*) FROM jobs GROUP BY status")),
        "timing_stages": dict(generation.execute("SELECT stage,COUNT(*) FROM timings GROUP BY stage")),
        "review_link_evidence": dict(levels),
        "review_generations_without_filename_candidate": rc["generations"] - len(linked_review_ids),
        "new_runnable_jobs_created": 0,
        "historical_timezone": "unspecified; original timestamp strings preserved",
        "historical_vram": "unknown; no measured values fabricated",
    }


def inspect_sources(generation_path, review_path):
    with closing(connect_readonly(generation_path)) as generation, closing(connect_readonly(review_path)) as review:
        # A read transaction gives each report a stable view, including live WAL data.
        generation.execute("BEGIN")
        review.execute("BEGIN")
        validate_sources(generation, review)
        select_history(generation, review)
        validate_selected_parameters(generation)
        return inspect_connections(generation, review)


def install_schema(conn):
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    conn.executescript(SCHEMA.with_name('processing.sql').read_text(encoding='utf-8'))
    conn.executescript(SCHEMA.with_name('writing.sql').read_text(encoding='utf-8'))
    conn.execute("INSERT OR IGNORE INTO platform_schema_version VALUES(1,?)", (utc_now(),))
    conn.execute("INSERT OR IGNORE INTO platform_schema_version VALUES(2,?)", (utc_now(),))
    conn.commit()


def copy_legacy(conn, source, source_id, original_path, snapshot_sha):
    source_schema = {r["name"]: r["sql"] for r in source.execute(
        "SELECT name,sql FROM sqlite_master WHERE type='table' AND name IN ('jobs','timings','counters')")}
    conn.execute("INSERT INTO legacy_import_sources VALUES(?,?,?,?,?)", (
        source_id, snapshot_sha, str(Path(original_path).resolve()), utc_now(), json.dumps(source_schema)))
    for name, key in LEGACY.items():
        info = source.execute("PRAGMA table_info(" + quoted(name) + ")").fetchall()
        columns = [row["name"] for row in info]
        if "source_id" in columns or key not in columns:
            raise ValueError("Unsupported legacy table structure: " + name)
        # Keep every source column, including additions unknown to this version.
        definitions = []
        for row in info:
            kind = row["type"].upper()
            if kind not in {"TEXT", "INTEGER", "REAL", "BLOB", "NUMERIC", ""}:
                raise ValueError("Unsupported SQLite declared type: " + kind)
            definitions.append(quoted(row["name"]) + " " + kind)
        destination = "legacy_generation_" + name
        ddl = ("CREATE TABLE " + quoted(destination) + "(source_id TEXT NOT NULL REFERENCES legacy_import_sources(source_id),"
               + ",".join(definitions) + ",PRIMARY KEY(source_id," + quoted(key) + "))")
        conn.execute(ddl)
        placeholders = ",".join("?" for _ in range(len(columns) + 1))
        sql = "INSERT INTO " + quoted(destination) + " VALUES(" + placeholders + ")"
        cursor = source.execute(legacy_query(name))
        while rows := cursor.fetchmany(500):
            conn.executemany(sql, ((source_id, *tuple(row)) for row in rows))
    conn.execute("CREATE INDEX idx_legacy_job_status ON legacy_generation_jobs(source_id,status)")
    conn.execute("CREATE INDEX idx_legacy_job_stem ON legacy_generation_jobs(output_stem)")
    conn.execute("CREATE INDEX idx_legacy_timing_job ON legacy_generation_timings(source_id,job_id)")


def install_views(conn):
    conn.executescript("""
        CREATE VIEW legacy_generation_costs AS
        SELECT source_id,job_id,cost_currency,
          SUM(CASE WHEN billable=1 THEN estimated_cost END) AS known_estimated_cost,
          SUM(CASE WHEN billable=1 AND estimated_cost IS NULL THEN 1 ELSE 0 END) AS missing_cost_events,
          SUM(CASE WHEN billable=1 THEN 1 ELSE 0 END) AS billable_events,
          SUM(CASE WHEN billable=1 AND success=0 THEN estimated_cost END) AS known_failed_cost
        FROM legacy_generation_timings GROUP BY source_id,job_id,cost_currency;
        CREATE VIEW legacy_stage_performance AS
        SELECT source_id,worker_id,gpu_name,stage,cost_currency,
          COUNT(*) AS total_events,SUM(success) AS successful_events,
          SUM(CASE WHEN success=0 THEN 1 ELSE 0 END) AS failed_events,
          AVG(CASE WHEN success=1 THEN duration_seconds END) AS mean_success_seconds,
          SUM(CASE WHEN billable=1 THEN estimated_cost END) AS known_estimated_cost,
          SUM(CASE WHEN billable=1 AND estimated_cost IS NULL THEN 1 ELSE 0 END) AS missing_cost_events
        FROM legacy_generation_timings
        WHERE stage IN ('conditioning_total','sampling_total','full_generation_total')
        GROUP BY source_id,worker_id,gpu_name,stage,cost_currency;
    """)


def migrate(generation_path, review_path, output_path, source_id="minimax-main", progress=None):
    """Publish a new database only after full reconciliation. Never overwrite one.

    An existing destination is always refused. A fresh import requires a new
    destination; incremental cutover is intentionally explicit.
    """
    generation_path, review_path = Path(generation_path).resolve(strict=True), Path(review_path).resolve(strict=True)
    output_path = Path(output_path).resolve()
    if output_path in {generation_path, review_path} or generation_path == review_path:
        raise ValueError("Generation, review and destination paths must be different")
    if not source_id.strip():
        raise ValueError("source_id cannot be blank")
    if output_path.exists():
        raise FileExistsError("Destination already exists; select a new path. It has not been modified.")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    notify = progress or (lambda _: None)
    with tempfile.TemporaryDirectory(prefix="gbr-migration-", dir=output_path.parent) as temp:
        temp = Path(temp)
        generation_snapshot, staging = temp / "generation.sqlite3", temp / "shared.sqlite3"
        notify("Creating consistent source snapshots")
        snapshot(generation_path, generation_snapshot)
        snapshot(review_path, staging)
        source_hash = file_hash(generation_snapshot)
        review_hash = file_hash(staging)
        with closing(connect_readonly(generation_snapshot)) as generation, closing(sqlite3.connect(staging)) as target:
            generation.row_factory = sqlite3.Row
            target.row_factory = sqlite3.Row
            target.execute("PRAGMA foreign_keys=ON")
            # Stage with a single file so publication does not strand a WAL.
            target.execute("PRAGMA journal_mode=DELETE")
            notify("Validating snapshots and fingerprinting original review tables")
            validate_sources(generation, target)
            selected_links = select_history(generation, target)
            validate_selected_parameters(generation)
            baseline_counts = counts(target)
            baseline = {name: table_fingerprint(target, name) for name in baseline_counts}
            report = inspect_connections(generation, target)
            install_schema(target)
            notify("Importing matched legacy history; excluding withdrawn and unmatched jobs")
            with target:
                copy_legacy(target, generation, source_id, generation_path, source_hash)
                target.executemany("INSERT INTO legacy_output_links VALUES(?,?,?,?,?)", (
                    (source_id, job, review_id, level, json.dumps(evidence, ensure_ascii=False))
                    for job, review_id, level, evidence in selected_links))
            install_views(target)
            notify("Reconciling imported rows and unchanged review data")
            for name in baseline_counts:
                if target.execute("SELECT COUNT(*) FROM " + quoted(name)).fetchone()[0] != baseline_counts[name]:
                    raise ValueError("Review row count changed: " + name)
                if table_fingerprint(target, name) != baseline[name]:
                    raise ValueError("Review data changed: " + name)
            legacy_fingerprints = {}
            for name in LEGACY:
                imported = "legacy_generation_" + name
                source_rows = report["imported_legacy_tables"][name]
                if target.execute("SELECT COUNT(*) FROM " + quoted(imported)).fetchone()[0] != source_rows:
                    raise ValueError("Legacy row count mismatch: " + name)
                # The imported table adds one source_id column. Compare typed values
                # via a temporary view projection using the same fingerprint routine.
                source_columns = [r["name"] for r in generation.execute("PRAGMA table_info(" + quoted(name) + ")")]
                projection = ",".join(quoted(x) for x in source_columns)
                target.execute("CREATE TEMP VIEW imported_values AS SELECT rowid AS import_rowid," + projection + " FROM " + quoted(imported))
                original_hash = _projected_fingerprint(generation, legacy_query(name))
                imported_hash = _projected_fingerprint(target, "SELECT " + projection + " FROM imported_values ORDER BY import_rowid")
                target.execute("DROP VIEW imported_values")
                if imported_hash != original_hash:
                    raise ValueError("Legacy values changed: " + name)
                legacy_fingerprints[name] = original_hash
            check_integrity(target)
            report.update({"format_version": 2, "created_at": utc_now(), "source_id": source_id,
                           "generation_snapshot_sha256": source_hash, "review_snapshot_sha256": review_hash,
                           "preserved_review_fingerprints": baseline, "legacy_fingerprints": legacy_fingerprints,
                           "database_integrity": "ok", "foreign_key_errors": 0,
                           "output_path": str(output_path)})
            target.execute("CREATE TABLE platform_migration_report(id INTEGER PRIMARY KEY CHECK(id=1),report_json TEXT NOT NULL)")
            target.execute("INSERT INTO platform_migration_report VALUES(1,?)", (json.dumps(report, ensure_ascii=False),))
            target.commit()
        # Same-filesystem hard-link publication is atomic and refuses replacement.
        # No destination is visible until its complete, verified contents exist.
        os.link(staging, output_path)
        notify("Validated shared database published")
        return report
