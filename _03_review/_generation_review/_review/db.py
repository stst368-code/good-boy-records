import sqlite3
import uuid
from contextlib import closing, contextmanager
from pathlib import Path
from . import migration, features_migration, tags

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO schema_version VALUES(1);
CREATE TABLE IF NOT EXISTS songs(
 id TEXT PRIMARY KEY, title TEXT NOT NULL, genre TEXT NOT NULL, cluster TEXT);
CREATE TABLE IF NOT EXISTS audio(
 id TEXT PRIMARY KEY, pcm_sha256 TEXT NOT NULL UNIQUE, duration REAL NOT NULL,
 sample_rate INTEGER NOT NULL, bit_depth INTEGER, channels INTEGER NOT NULL,
 archive_path TEXT, archive_stem TEXT, archive_bucket INTEGER,
 created_at TEXT NOT NULL, analysis_state TEXT NOT NULL DEFAULT 'pending');
CREATE TABLE IF NOT EXISTS generations(
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id),
 song_id TEXT NOT NULL REFERENCES songs(id), metadata_key TEXT NOT NULL,
 generation_timestamp TEXT, original_filename TEXT NOT NULL, original_directory TEXT NOT NULL,
 source TEXT, workflow TEXT, created_at TEXT NOT NULL,
 UNIQUE(audio_id, metadata_key));
CREATE TABLE IF NOT EXISTS generation_metadata(
 generation_id TEXT PRIMARY KEY REFERENCES generations(id), yaml_sha256 TEXT,
 raw_yaml TEXT, raw_yaml_bytes BLOB, parsed_json TEXT NOT NULL, embedded_json TEXT NOT NULL,
 metadata_status TEXT NOT NULL, error TEXT, yaml_archive_path TEXT,
 model TEXT, dit TEXT, text_encoder TEXT, encoder_cfg REAL, encoder_seed TEXT,
 top_k INTEGER, sampler TEXT, scheduler TEXT, sampler_cfg REAL, sampler_seed TEXT,
 sampler_steps INTEGER, generation_duration REAL, hardware TEXT);
CREATE TABLE IF NOT EXISTS media_files(
 id INTEGER PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id),
 sha256 TEXT NOT NULL, path TEXT NOT NULL, original_path TEXT NOT NULL,
 size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, storage_state TEXT NOT NULL,
 indexed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS generation_sources(
 id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generations(id),
 media_id INTEGER NOT NULL REFERENCES media_files(id), yaml_path TEXT, original_yaml_path TEXT,
 UNIQUE(generation_id,media_id,yaml_path));
CREATE TABLE IF NOT EXISTS reviews(
 id TEXT PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generations(id),
 audio_id TEXT NOT NULL REFERENCES audio(id), audio_quality INTEGER NOT NULL CHECK(audio_quality BETWEEN 0 AND 5),
 composition INTEGER NOT NULL CHECK(composition BETWEEN 0 AND 5),
 consistency INTEGER NOT NULL CHECK(consistency BETWEEN 0 AND 5),
 realism INTEGER NOT NULL CHECK(realism BETWEEN 0 AND 5),
 average REAL NOT NULL, bucket INTEGER NOT NULL CHECK(bucket BETWEEN 0 AND 5),
 created_at TEXT NOT NULL, mode TEXT NOT NULL, revision INTEGER NOT NULL,
 session_id TEXT NOT NULL, previous_review_id TEXT, listening_json TEXT NOT NULL,
 UNIQUE(audio_id,revision));
CREATE TABLE IF NOT EXISTS review_history(
 id INTEGER PRIMARY KEY, audio_id TEXT, generation_id TEXT, action TEXT NOT NULL,
 previous_review_id TEXT, review_id TEXT, details_json TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sessions(
 id TEXT PRIMARY KEY, current_generation_id TEXT, previous_generation_id TEXT,
 order_by TEXT NOT NULL DEFAULT 'random', direction TEXT NOT NULL DEFAULT 'asc',
 filters_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS queue_skips(
 session_id TEXT NOT NULL, audio_id TEXT NOT NULL, PRIMARY KEY(session_id,audio_id));
CREATE TABLE IF NOT EXISTS analyses(
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id), version TEXT NOT NULL,
 created_at TEXT NOT NULL, metrics_json TEXT NOT NULL, preview_path TEXT NOT NULL,
 spectrogram_path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS audio_events(
 id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES analyses(id), audio_id TEXT NOT NULL,
 type TEXT NOT NULL, start REAL NOT NULL, end REAL NOT NULL,
 severity REAL NOT NULL, confidence REAL NOT NULL, measurements_json TEXT NOT NULL, algorithm TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS segments(
 id TEXT PRIMARY KEY, analysis_id TEXT NOT NULL REFERENCES analyses(id), audio_id TEXT NOT NULL,
 mode TEXT NOT NULL, ordinal INTEGER NOT NULL, start REAL NOT NULL, end REAL NOT NULL, selection_json TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS archive_operations(
 id TEXT PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id), generation_id TEXT,
 review_id TEXT, bucket INTEGER NOT NULL, operation_type TEXT NOT NULL DEFAULT 'move',
 status TEXT NOT NULL DEFAULT 'pending', plan_json TEXT NOT NULL DEFAULT '{}',
 verification_json TEXT NOT NULL DEFAULT '{}', attempts INTEGER NOT NULL DEFAULT 0,
 error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publishing_selection(
 generation_id TEXT PRIMARY KEY REFERENCES generations(id), status TEXT NOT NULL DEFAULT 'not_selected',
 selected_at TEXT, published_at TEXT, external_reference TEXT, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publishing_history(
 id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL, old_status TEXT, new_status TEXT NOT NULL,
 external_reference TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS preferences(
 id TEXT PRIMARY KEY, generation_a TEXT NOT NULL, generation_b TEXT NOT NULL, preferred TEXT,
 target_lufs REAL NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS scan_errors(
 path TEXT PRIMARY KEY, error TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_generations_audio ON generations(audio_id);
CREATE INDEX IF NOT EXISTS idx_generations_song ON generations(song_id);
CREATE INDEX IF NOT EXISTS idx_reviews_audio ON reviews(audio_id,revision DESC);
CREATE INDEX IF NOT EXISTS idx_media_audio ON media_files(audio_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_media_active_path ON media_files(path) WHERE storage_state IN ('input','archived');
CREATE INDEX IF NOT EXISTS idx_analysis_audio ON analyses(audio_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_audio ON audio_events(audio_id);
CREATE INDEX IF NOT EXISTS idx_archive_status ON archive_operations(status,created_at);
CREATE INDEX IF NOT EXISTS idx_publishing_status ON publishing_selection(status);
CREATE INDEX IF NOT EXISTS idx_metadata_params ON generation_metadata(sampler,scheduler,sampler_cfg,sampler_steps);
CREATE VIEW IF NOT EXISTS latest_reviews AS
 SELECT r.* FROM reviews r WHERE r.revision=(SELECT MAX(revision) FROM reviews WHERE audio_id=r.audio_id);
CREATE VIEW IF NOT EXISTS analytics_dataset AS
 SELECT g.id AS generation_id,a.id AS audio_id,a.pcm_sha256,s.id AS song_id,s.title,
 COALESCE(NULLIF(json_extract(m.parsed_json,'$.genre'),''),NULLIF(json_extract(m.parsed_json,'$.intended_genre'),''),NULLIF(json_extract(m.parsed_json,'$.cluster'),''),s.genre) AS genre,
 COALESCE(json_extract(m.parsed_json,'$.cluster'),s.cluster) AS cluster,
 g.generation_timestamp,g.original_filename,g.original_directory,g.source,g.workflow,
 m.yaml_sha256,m.metadata_status,m.model,m.dit,m.text_encoder,m.encoder_cfg,m.encoder_seed,
 m.top_k,m.sampler,m.scheduler,m.sampler_cfg,m.sampler_seed,m.sampler_steps,m.generation_duration,m.hardware,
 m.parsed_json AS generation_metadata_json,a.duration AS audio_duration,a.sample_rate,a.bit_depth,a.channels,
 r.id AS review_id,r.audio_quality,r.composition,r.consistency,r.realism,r.average,r.bucket,
 r.mode AS review_mode,r.revision,r.created_at AS reviewed_at,
 CASE WHEN r.generation_id=g.id THEN 0 ELSE 1 END AS review_inherited,
 a.archive_path,m.yaml_archive_path,COALESCE(p.status,'not_selected') AS publishing_status,
 p.selected_at,p.published_at,p.external_reference,
 an.version AS analysis_version,an.metrics_json AS audio_metrics_json
 FROM generations g JOIN audio a ON a.id=g.audio_id JOIN songs s ON s.id=g.song_id
 JOIN generation_metadata m ON m.generation_id=g.id LEFT JOIN latest_reviews r ON r.audio_id=a.id
 LEFT JOIN publishing_selection p ON p.generation_id=g.id
 LEFT JOIN analyses an ON an.id=(SELECT id FROM analyses WHERE audio_id=a.id ORDER BY created_at DESC,rowid DESC LIMIT 1);
"""

ANALYTICS_SQL = "CREATE VIEW analytics_dataset AS " + SCHEMA.split("CREATE VIEW IF NOT EXISTS analytics_dataset AS", 1)[1].strip().rstrip(";")
ANALYTICS_SQL = ANALYTICS_SQL.replace("r.mode AS review_mode,r.revision,r.created_at AS reviewed_at,", "r.mode AS review_mode,r.revision,r.created_at AS reviewed_at,r.reviewer_id,v.display_name AS reviewer_name,r.assessment_id,r.assessment_revision,")
ANALYTICS_SQL = ANALYTICS_SQL.replace("LEFT JOIN latest_reviews r ON r.audio_id=a.id", "LEFT JOIN latest_reviews r ON r.audio_id=a.id LEFT JOIN reviewers v ON v.id=r.reviewer_id")
FEATURE_ANALYTICS_SQL = ANALYTICS_SQL.replace('r.assessment_revision,', 'r.assessment_revision,r.reason AS review_reason,r.flags_json AS review_flags_json,')
FEATURE_ANALYTICS_SQL = FEATURE_ANALYTICS_SQL.replace('p.selected_at,p.published_at,p.external_reference,', 'p.selected_at,p.published_at,p.external_reference,EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=g.id AND b.reviewer_id=r.reviewer_id) AS reviewer_bookmarked,')


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.migration_backup = None
        with closing(self.connect()) as conn:
            reviewers_needed = migration.required(conn)
            features_needed = features_migration.required(conn)
            tags_needed = tags.required(conn)
            if reviewers_needed or features_needed or tags_needed:
                folder = self.path.parent / "backups"
                folder.mkdir(parents=True, exist_ok=True)
                self.migration_backup = folder / (("before-reviewers-" if reviewers_needed else "before-review-features-" if features_needed else "before-review-tags-") + uuid.uuid4().hex + ".sqlite")
                with closing(sqlite3.connect(self.migration_backup)) as snapshot:
                    conn.backup(snapshot)
                    if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                        raise RuntimeError("Database backup failed verification; reviewer migration was not attempted")
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)
            migration.migrate(conn, ANALYTICS_SQL)
            features_migration.migrate(conn, FEATURE_ANALYTICS_SQL)
            tags.migrate(conn, FEATURE_ANALYTICS_SQL)

    def connect(self):
        c = sqlite3.connect(self.path, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        c.execute("PRAGMA busy_timeout=30000")
        return c

    @contextmanager
    def transaction(self):
        c = self.connect()
        try:
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def all(self, sql, args=()):
        with closing(self.connect()) as c:
            return [dict(r) for r in c.execute(sql, args)]

    def one(self, sql, args=()):
        rows = self.all(sql, args)
        return rows[0] if rows else None

    def execute(self, sql, args=()):
        with self.transaction() as c:
            return c.execute(sql, args).rowcount

    def backup(self, destination):
        with closing(self.connect()) as source, closing(sqlite3.connect(destination)) as target:
            source.backup(target)
