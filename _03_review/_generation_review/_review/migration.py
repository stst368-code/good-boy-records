"""Transactional reviewer migration. A consistent backup is made by Database first."""
import uuid
from .util import now

VERSION = 2


def required(conn):
    columns = {r[1] for r in conn.execute("PRAGMA table_info(reviews)")}
    return bool(columns) and "reviewer_id" not in columns


def migrate(conn, analytics_sql):
    if conn.execute("SELECT 1 FROM schema_version WHERE version=?", (VERSION,)).fetchone():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS reviewers(
            id TEXT PRIMARY KEY, display_name TEXT NOT NULL, name_key TEXT NOT NULL UNIQUE, created_at TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS assessments(
            id TEXT PRIMARY KEY, audio_id TEXT NOT NULL REFERENCES audio(id),
            reviewer_id TEXT NOT NULL REFERENCES reviewers(id), created_at TEXT NOT NULL,
            UNIQUE(audio_id,reviewer_id))""")
        for table, additions in {
            "reviews": (("reviewer_id", "TEXT REFERENCES reviewers(id)"),
                        ("assessment_id", "TEXT REFERENCES assessments(id)"),
                        ("assessment_revision", "INTEGER NOT NULL DEFAULT 1")),
            "review_history": (("reviewer_id", "TEXT REFERENCES reviewers(id)"),
                               ("assessment_id", "TEXT REFERENCES assessments(id)")),
            "sessions": (("reviewer_id", "TEXT REFERENCES reviewers(id)"),),
        }.items():
            columns = {r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")}
            for name, kind in additions:
                if name not in columns:
                    conn.execute("ALTER TABLE " + table + " ADD COLUMN " + name + " " + kind)
        conn.execute("INSERT OR IGNORE INTO reviewers VALUES(?,?,?,?)", (uuid.uuid4().hex, "Simon", "simon", now()))
        simon = conn.execute("SELECT id FROM reviewers WHERE name_key='simon'").fetchone()[0]
        # Never overwrite attribution already assigned to a later review.
        conn.execute("UPDATE reviews SET reviewer_id=? WHERE reviewer_id IS NULL", (simon,))
        pairs = conn.execute("SELECT DISTINCT audio_id,reviewer_id FROM reviews WHERE assessment_id IS NULL").fetchall()
        for audio_id, reviewer_id in pairs:
            aid = uuid.uuid5(uuid.NAMESPACE_URL, "gbr-review:" + audio_id + ":" + reviewer_id).hex
            conn.execute("INSERT OR IGNORE INTO assessments VALUES(?,?,?,?)", (aid, audio_id, reviewer_id, now()))
            aid = conn.execute("SELECT id FROM assessments WHERE audio_id=? AND reviewer_id=?", (audio_id, reviewer_id)).fetchone()[0]
            for ordinal, row in enumerate(conn.execute("SELECT id FROM reviews WHERE audio_id=? AND reviewer_id=? ORDER BY revision", (audio_id, reviewer_id)), 1):
                conn.execute("UPDATE reviews SET assessment_id=?,assessment_revision=? WHERE id=? AND assessment_id IS NULL", (aid, ordinal, row[0]))
        conn.execute("""UPDATE review_history SET reviewer_id=COALESCE(
            (SELECT reviewer_id FROM reviews WHERE id=review_history.review_id),
            (SELECT reviewer_id FROM reviews WHERE id=review_history.previous_review_id),?) WHERE reviewer_id IS NULL""", (simon,))
        conn.execute("""UPDATE review_history SET assessment_id=COALESCE(
            (SELECT assessment_id FROM reviews WHERE id=review_history.review_id),
            (SELECT assessment_id FROM reviews WHERE id=review_history.previous_review_id)) WHERE assessment_id IS NULL""")
        conn.execute("INSERT OR IGNORE INTO sessions(id,updated_at,reviewer_id) VALUES('main',?,?)", (now(), simon))
        conn.execute("UPDATE sessions SET reviewer_id=? WHERE id='main' AND reviewer_id IS NULL", (simon,))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_session_reviewer ON sessions(reviewer_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_reviewer ON reviews(reviewer_id,audio_id,assessment_revision DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_assessment_revision ON reviews(assessment_id,assessment_revision)")
        conn.execute("""CREATE TABLE IF NOT EXISTS queue_reservations(
            audio_id TEXT PRIMARY KEY REFERENCES audio(id), reviewer_id TEXT NOT NULL REFERENCES reviewers(id),
            generation_id TEXT NOT NULL REFERENCES generations(id), ordinal INTEGER NOT NULL,
            created_at REAL NOT NULL, expires_at REAL NOT NULL)""")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reservation_reviewer ON queue_reservations(reviewer_id,ordinal)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_reservation_expiry ON queue_reservations(expires_at)")
        conn.execute("""CREATE VIEW IF NOT EXISTS latest_reviewer_reviews AS SELECT r.* FROM reviews r
            WHERE NOT EXISTS(SELECT 1 FROM reviews newer WHERE newer.assessment_id=r.assessment_id AND newer.assessment_revision>r.assessment_revision)""")
        conn.execute("CREATE VIEW IF NOT EXISTS review_dataset AS SELECT r.*,v.display_name AS reviewer_name FROM reviews r JOIN reviewers v ON v.id=r.reviewer_id")
        conn.execute("CREATE VIEW IF NOT EXISTS review_history_dataset AS SELECT h.*,v.display_name AS reviewer_name FROM review_history h LEFT JOIN reviewers v ON v.id=h.reviewer_id")
        conn.execute("DROP VIEW IF EXISTS analytics_dataset")
        conn.execute(analytics_sql)
        conn.execute("""CREATE TRIGGER IF NOT EXISTS require_review_identity BEFORE INSERT ON reviews
            WHEN NEW.reviewer_id IS NULL OR NEW.assessment_id IS NULL OR NOT EXISTS(
                SELECT 1 FROM assessments a WHERE a.id=NEW.assessment_id AND a.audio_id=NEW.audio_id AND a.reviewer_id=NEW.reviewer_id)
            BEGIN SELECT RAISE(ABORT,'Reviewer and assessment identity required'); END""")
        conn.execute("INSERT OR IGNORE INTO schema_version VALUES(?)", (VERSION,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
