-- Drafts and requests before a generated recording exists. Reuses worker_sessions.
CREATE TABLE IF NOT EXISTS writing_tasks (
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
 reviewer_id TEXT REFERENCES reviewers(id),
 source_path TEXT, source_sha256 TEXT NOT NULL, source_yaml BLOB NOT NULL,
 request_json TEXT NOT NULL, prompt_json TEXT NOT NULL,
 result_yaml BLOB, result_json TEXT NOT NULL DEFAULT '{}',
 worker_session_id TEXT REFERENCES worker_sessions(id),
 revision INTEGER NOT NULL DEFAULT 0,
 created_at TEXT NOT NULL, completed_at TEXT, error TEXT,
 publication_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_writing_created ON writing_tasks(created_at);
