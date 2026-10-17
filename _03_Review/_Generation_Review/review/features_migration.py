"""Add review reasons, independent bookmarks and a derived-playback ledger."""
VERSION = 3


def required(conn):
    columns = {r[1] for r in conn.execute('PRAGMA table_info(reviews)')}
    return bool(columns) and not conn.execute('SELECT 1 FROM schema_version WHERE version=?', (VERSION,)).fetchone()


def migrate(conn, analytics_sql):
    if conn.execute('SELECT 1 FROM schema_version WHERE version=?', (VERSION,)).fetchone():
        return
    conn.execute('BEGIN IMMEDIATE')
    try:
        columns = {r[1] for r in conn.execute('PRAGMA table_info(reviews)')}
        for name, declaration in (('reason', "TEXT NOT NULL DEFAULT ''"), ('flags_json', "TEXT NOT NULL DEFAULT '{}'"), ('submission_id', 'TEXT')):
            if name not in columns:
                conn.execute('ALTER TABLE reviews ADD COLUMN '+name+' '+declaration)
        if 'opened_generation_id' not in {r[1] for r in conn.execute('PRAGMA table_info(sessions)')}:
            conn.execute('ALTER TABLE sessions ADD COLUMN opened_generation_id TEXT REFERENCES generations(id)')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_review_submission ON reviews(reviewer_id,submission_id) WHERE submission_id IS NOT NULL')
        conn.execute('''CREATE TABLE IF NOT EXISTS bookmarks(
            generation_id TEXT NOT NULL REFERENCES generations(id), reviewer_id TEXT NOT NULL REFERENCES reviewers(id),
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL, PRIMARY KEY(generation_id,reviewer_id))''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_bookmark_reviewer ON bookmarks(reviewer_id,generation_id)')
        conn.execute('''CREATE TABLE IF NOT EXISTS bookmark_history(
            id INTEGER PRIMARY KEY, generation_id TEXT NOT NULL REFERENCES generations(id),
            reviewer_id TEXT NOT NULL REFERENCES reviewers(id), action TEXT NOT NULL,
            created_at TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS playback_cache(
            filename TEXT PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id),
            size INTEGER NOT NULL, created_at REAL NOT NULL, last_used REAL NOT NULL)''')
        conn.execute('DROP VIEW IF EXISTS analytics_dataset')
        conn.execute(analytics_sql)
        conn.execute('INSERT INTO schema_version VALUES(?)', (VERSION,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
