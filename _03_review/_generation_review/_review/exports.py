import csv
import json
from pathlib import Path
from .util import atomic_text, dumps, now


def rows(db):
    result = db.all("SELECT * FROM analytics_dataset ORDER BY generation_id")
    for row in result:
        metrics = json.loads(row["audio_metrics_json"]) if row["audio_metrics_json"] else {}
        for key in ("sample_peak_dbfs", "true_peak_dbtp_4x", "clipped_samples", "rms_dbfs", "integrated_lufs", "loudness_range_lu",
                    "spectral_centroid_hz", "spectral_flatness", "spectral_flux", "stereo_correlation", "event_count"):
            row[key] = metrics.get(key)
        row["reviewed_outputs_per_compute_hour"] = (3600 / row["generation_duration"]) if row["generation_duration"] and row["generation_duration"] > 0 and row["review_id"] else None
    return result


EXPORT_TABLES = {"generations": "analytics_dataset", "reviews": "review_dataset", "events": "audio_events", "segments": "segments",
                 "analyses": "analyses", "review_history": "review_history_dataset", "reviewers": "reviewers", "publishing_history": "publishing_history", "preferences": "preferences", "bookmarks": "bookmarks", "bookmark_history": "bookmark_history"}


def dataset(db, path, format, kind="generations"):
    if kind not in EXPORT_TABLES:
        raise ValueError("Unknown dataset")
    table = EXPORT_TABLES[kind]
    data = rows(db) if kind == "generations" else db.all("SELECT * FROM " + table)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if format == "json":
        atomic_text(path, dumps({"schema_version": 3, "exported_at": now(), kind: data}))
    elif format == "csv":
        with db.connect() as c:
            columns = list(data[0]) if data else [d[0] for d in c.execute("SELECT * FROM " + table + " LIMIT 0").description]
        with path.open("w", newline="", encoding="utf-8-sig") as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            writer.writerows(data)
    elif format == "parquet":
        import pyarrow as pa
        import pyarrow.parquet as pq
        if data:
            table = pa.Table.from_pylist(data)
        else:
            table = pa.table({"generation_id": pa.array([], type=pa.string())})
        pq.write_table(table, path)
    else:
        raise ValueError("Unsupported export format")
    return path


def publishing_manifest(db):
    tracks = db.all("""SELECT generation_id,audio_id,pcm_sha256,title,genre,
      yaml_sha256,archive_path AS audio_path,yaml_archive_path AS yaml_path,publishing_status,selected_at,published_at,external_reference,reviewer_id,reviewer_name
      FROM analytics_dataset WHERE publishing_status IN ('selected','published') AND review_id IS NOT NULL ORDER BY selected_at""")
    for track in tracks:
        track['archive_ready'] = bool(track['audio_path'] and Path(track['audio_path']).is_file() and (not track['yaml_sha256'] or (track['yaml_path'] and Path(track['yaml_path']).is_file())))
        if not track['audio_path']:
            sources = db.all("SELECT path FROM media_files WHERE audio_id=? AND storage_state IN ('input','archived')", (track['audio_id'],))
            track['audio_path'] = next((s['path'] for s in sources if Path(s['path']).is_file()), None)
        if not track['yaml_path']:
            sources = db.all("SELECT yaml_path FROM generation_sources WHERE generation_id=?", (track['generation_id'],))
            track['yaml_path'] = next((s['yaml_path'] for s in sources if s['yaml_path'] and Path(s['yaml_path']).is_file()), None)
    return {"schema_version": 1, "exported_at": now(), "tracks": tracks}


def validation(db, archiver):
    checks = {"database_integrity": db.one("PRAGMA integrity_check"), "foreign_key_errors": db.all("PRAGMA foreign_key_check"),
              "missing_audio": [], "missing_yaml": [], "archive_failures": db.all("SELECT id,error FROM archive_operations WHERE status='failed'")}
    for row in db.all("SELECT id FROM audio"):
        if not archiver.available(row["id"]):
            checks["missing_audio"].append(row["id"])
    for row in db.all("SELECT generation_id,yaml_archive_path FROM generation_metadata WHERE yaml_archive_path IS NOT NULL"):
        if not Path(row["yaml_archive_path"]).is_file():
            checks["missing_yaml"].append(row["generation_id"])
    checks["review_revisions"] = db.one("SELECT COUNT(*) AS count FROM reviews")["count"]
    checks["review_consistency"] = db.all("""SELECT audio_id,COUNT(*) AS assessments,
      MAX(audio_quality)-MIN(audio_quality) AS audio_quality_span,
      MAX(composition)-MIN(composition) AS composition_span,
      MAX(consistency)-MIN(consistency) AS consistency_span,
      MAX(realism)-MIN(realism) AS realism_span FROM reviews GROUP BY audio_id HAVING COUNT(*)>1""")
    return checks
