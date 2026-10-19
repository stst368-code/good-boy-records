-- One shared ledger for preparation, mastering, alignment, visuals and artwork.
-- Inputs reference existing generations. Outputs use generation_artifacts.
CREATE TABLE IF NOT EXISTS processing_tasks (
    id TEXT PRIMARY KEY,
    generation_id TEXT NOT NULL REFERENCES generations(id),
    kind TEXT NOT NULL, status TEXT NOT NULL,
    fingerprint TEXT NOT NULL, recipe_json TEXT NOT NULL,
    input_sha256 TEXT NOT NULL,
    worker_session_id TEXT REFERENCES worker_sessions(id),
    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    telemetry_json TEXT NOT NULL DEFAULT '{}', error TEXT
);
CREATE INDEX IF NOT EXISTS idx_processing_generation ON processing_tasks(generation_id,created_at);
CREATE INDEX IF NOT EXISTS idx_processing_cache ON processing_tasks(fingerprint,status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_processing_active ON processing_tasks(fingerprint) WHERE status IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_artifact_processing ON generation_artifacts(json_extract(metadata_json,'$.processing_task_id'));
