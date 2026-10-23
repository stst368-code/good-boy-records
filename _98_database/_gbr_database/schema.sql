-- Additive platform schema. Existing review tables, IDs and schema_version stay intact.
CREATE TABLE IF NOT EXISTS platform_schema_version (
    version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS legacy_import_sources (
    source_id TEXT PRIMARY KEY, snapshot_sha256 TEXT NOT NULL,
    original_path TEXT NOT NULL, imported_at TEXT NOT NULL, schema_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalogue_songs (
    song_id TEXT PRIMARY KEY, title TEXT NOT NULL, metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS catalogue_variants (
    song_id TEXT NOT NULL REFERENCES catalogue_songs(song_id), variant_id TEXT NOT NULL,
    filename TEXT NOT NULL, metadata_json TEXT NOT NULL, PRIMARY KEY(song_id,variant_id)
);
CREATE TABLE IF NOT EXISTS source_documents (
    sha256 TEXT PRIMARY KEY, raw_bytes BLOB NOT NULL, source_path TEXT,
    imported_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_batches (
    id TEXT PRIMARY KEY, created_at TEXT NOT NULL, status TEXT NOT NULL,
    selection_json TEXT NOT NULL, policy_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_batch_inputs (
    batch_id TEXT NOT NULL REFERENCES generation_batches(id), input_id TEXT NOT NULL,
    source_sha256 TEXT REFERENCES source_documents(sha256), source_path TEXT NOT NULL,
    status TEXT NOT NULL, issues_json TEXT NOT NULL, PRIMARY KEY(batch_id,input_id)
);
CREATE TABLE IF NOT EXISTS generation_runs (
    id TEXT PRIMARY KEY, batch_id TEXT REFERENCES generation_batches(id),
    source_sha256 TEXT NOT NULL REFERENCES source_documents(sha256),
    song_id TEXT, variant_id TEXT, title TEXT NOT NULL, cluster TEXT, style TEXT,
    template_id TEXT, template_sha256 TEXT, status TEXT NOT NULL,
    requested_json TEXT NOT NULL, effective_json TEXT NOT NULL,
    created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT,
    parent_review_generation_id TEXT REFERENCES generations(id),
    request_id TEXT UNIQUE
);
CREATE TABLE IF NOT EXISTS worker_sessions (
    id TEXT PRIMARY KEY, address TEXT NOT NULL, provider TEXT,
    provider_instance_id TEXT, observed_at TEXT NOT NULL,
    gpu_uuid TEXT, gpu_name TEXT, usable_vram_bytes INTEGER,
    hardware_json TEXT NOT NULL, environment_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_attempts (
    id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES generation_runs(id),
    worker_session_id TEXT REFERENCES worker_sessions(id), stage TEXT NOT NULL,
    status TEXT NOT NULL, execution_mode TEXT NOT NULL, actual_models_json TEXT NOT NULL,
    started_at TEXT, completed_at TEXT, error TEXT
);
CREATE TABLE IF NOT EXISTS generation_timing_spans (
    id TEXT PRIMARY KEY, attempt_id TEXT NOT NULL REFERENCES generation_attempts(id),
    parent_id TEXT REFERENCES generation_timing_spans(id), stage TEXT NOT NULL,
    started_at TEXT NOT NULL, completed_at TEXT, duration_seconds REAL,
    estimated_cost REAL, currency TEXT, cost_basis TEXT, includes_children INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_resource_measurements (
    attempt_id TEXT NOT NULL REFERENCES generation_attempts(id),
    stage TEXT NOT NULL, device_id TEXT NOT NULL,
    baseline_used_bytes INTEGER, peak_allocated_bytes INTEGER,
    peak_reserved_bytes INTEGER, peak_observed_device_bytes INTEGER,
    sampling_interval_ms REAL, coverage TEXT NOT NULL,
    offload_json TEXT NOT NULL, details_json TEXT NOT NULL,
    PRIMARY KEY(attempt_id,stage,device_id)
);
CREATE TABLE IF NOT EXISTS generation_artifacts (
    id TEXT PRIMARY KEY, run_id TEXT REFERENCES generation_runs(id), kind TEXT NOT NULL,
    sha256 TEXT, uri TEXT NOT NULL, size_bytes INTEGER, encoding_key TEXT,
    metadata_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_output_links (
    run_id TEXT NOT NULL REFERENCES generation_runs(id),
    review_generation_id TEXT NOT NULL REFERENCES generations(id),
    artifact_id TEXT REFERENCES generation_artifacts(id),
    PRIMARY KEY(run_id,review_generation_id)
);
CREATE TABLE IF NOT EXISTS legacy_output_links (
    source_id TEXT NOT NULL, job_id TEXT NOT NULL,
    review_generation_id TEXT NOT NULL REFERENCES generations(id),
    evidence_level TEXT NOT NULL CHECK(evidence_level IN ('parameters_match','filename_only','conflict','ambiguous')),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(source_id,job_id,review_generation_id),
    FOREIGN KEY(source_id,job_id) REFERENCES legacy_generation_jobs(source_id,job_id)
);
CREATE INDEX IF NOT EXISTS idx_runs_status ON generation_runs(status,created_at);
CREATE INDEX IF NOT EXISTS idx_runs_catalogue ON generation_runs(song_id,variant_id,cluster,style);
CREATE INDEX IF NOT EXISTS idx_attempts_run ON generation_attempts(run_id);
CREATE INDEX IF NOT EXISTS idx_resources_attempt ON generation_resource_measurements(attempt_id);
CREATE INDEX IF NOT EXISTS idx_legacy_links_review ON legacy_output_links(review_generation_id);
-- Native execution state is separate from frozen requests and existing review identities.
CREATE TABLE IF NOT EXISTS generation_execution_state (
    run_id TEXT PRIMARY KEY REFERENCES generation_runs(id),
    model_revision TEXT, output_stem TEXT NOT NULL UNIQUE,
    output_directory TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS generation_output_counters (
    title_key TEXT PRIMARY KEY, last_value INTEGER NOT NULL
);
