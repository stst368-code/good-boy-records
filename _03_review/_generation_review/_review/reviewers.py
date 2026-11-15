"""Reviewer identity and audio-exclusive, expiring server-side queue reservations."""
import json
import random
import time
import uuid
from contextlib import closing
from datetime import datetime
from .util import now
from .metadata_identity import worker_intermediate
from .filtering import PROJECTION, matches, prepare, song_names, available_audio, available_page


class Conflict(Exception):
    def __init__(self, message, code="stale_review"):
        super().__init__(message)
        self.code = code


class ReviewerQueues:
    def __init__(self, service):
        self.service = service
        self.db = service.db
        self.clock = time.time

    def names(self):
        return self.db.all("SELECT id,display_name FROM reviewers ORDER BY name_key")

    def identity(self, rid):
        return self.db.one("SELECT id,display_name FROM reviewers WHERE id=?", (rid,)) if rid else None

    def select(self, name, previous=None):
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError("Enter a name between 1 and 100 characters")
        with self.db.transaction() as c:
            c.execute("INSERT OR IGNORE INTO reviewers VALUES(?,?,?,?)", (uuid.uuid4().hex, name, name.casefold(), now()))
            row = dict(c.execute("SELECT id,display_name FROM reviewers WHERE name_key=?", (name.casefold(),)).fetchone())
            if previous and previous != row["id"]:
                self.release(previous, c)
            self.session(c, row["id"])
            return row

    def session(self, c, rid):
        if not c.execute("SELECT 1 FROM reviewers WHERE id=?", (rid,)).fetchone():
            raise ValueError("Choose a reviewer before continuing")
        c.execute("INSERT OR IGNORE INTO sessions(id,updated_at,reviewer_id) VALUES(?,?,?)", (rid, now(), rid))
        return dict(c.execute("SELECT * FROM sessions WHERE reviewer_id=?", (rid,)).fetchone())

    def release(self, rid, c=None):
        if c is None:
            with self.db.transaction() as connection:
                return self.release(rid, connection)
        c.execute("DELETE FROM queue_reservations WHERE reviewer_id=?", (rid,))
        c.execute("UPDATE sessions SET current_generation_id=NULL,opened_generation_id=NULL,updated_at=? WHERE reviewer_id=?", (now(), rid))

    def sweep(self, c):
        c.execute("DELETE FROM queue_reservations WHERE expires_at<=? OR EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=queue_reservations.audio_id)", (self.clock(),))

    def queue_filters(self, filters):
        names = song_names(self.service) if filters.get('titles') or filters.get('song') else None
        return prepare(filters, names), names

    def candidates(self, c, session, randomize=True):
        filters, names = self.queue_filters(json.loads(session["filters_json"]))
        rows = [dict(r) for r in c.execute(f"""SELECT g.id,g.audio_id,g.generation_timestamp,g.created_at,g.original_directory,a.duration,{PROJECTION}
            FROM generations g JOIN songs s ON s.id=g.song_id JOIN audio a ON a.id=g.audio_id
            LEFT JOIN generation_metadata m ON m.generation_id=g.id
            WHERE NOT EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id)
            AND NOT EXISTS(SELECT 1 FROM publishing_selection p WHERE p.generation_id=g.id AND p.status='published')
            AND NOT EXISTS(SELECT 1 FROM queue_skips k WHERE k.audio_id=g.audio_id AND k.session_id=?)
            ORDER BY g.rowid""", (session["id"],))]
        if names:
            rows = names.annotate(rows)
        rows=[r for r in rows if r['id']==session.get('opened_generation_id') or not worker_intermediate(r['original_directory'])]
        opened = next((r for r in rows if r['id'] == session.get('opened_generation_id')), None)
        rows = [r for r in rows if matches(r, filters)]
        if session["order_by"] == "random":
            if randomize:
                random.shuffle(rows)
        else:
            def date_key(r):
                try:
                    return datetime.fromisoformat(r["generation_timestamp"].replace("Z", "+00:00")).timestamp()
                except (ValueError, TypeError):
                    return datetime.fromisoformat(r["created_at"]).timestamp()
            rows.sort(key=(lambda r: r["title"].casefold()) if session["order_by"] == "alphabetical" else date_key,
                      reverse=session["direction"] == "desc")
        seen, result = set(), []
        if opened:
            result.append(opened)
            seen.add(opened['audio_id'])
        for r in rows:
            if r["audio_id"] not in seen:
                result.append(r)
                seen.add(r["audio_id"])
        return result

    def count_scope(self, c, session, overrides=None):
        filters, names = self.queue_filters({**json.loads(session['filters_json']), **(overrides or {})})
        rows = [dict(row) for row in c.execute(f'''SELECT g.id,g.audio_id,g.original_directory,{PROJECTION},
            EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id) AS reviewed,
            EXISTS(SELECT 1 FROM publishing_selection p WHERE p.generation_id=g.id AND p.status='published') AS published,
            EXISTS(SELECT 1 FROM queue_skips k WHERE k.audio_id=g.audio_id AND k.session_id=?) AS skipped
            FROM generations g JOIN songs s ON s.id=g.song_id LEFT JOIN generation_metadata m ON m.generation_id=g.id''', (session['id'],))]
        seen, reviewed, skipped = set(), set(), set()
        reserved = {r[0] for r in c.execute('SELECT audio_id FROM queue_reservations WHERE reviewer_id<>? AND expires_at>?', (session['reviewer_id'], self.clock()))}
        present = available_audio(self.db)
        for row in names.annotate(rows) if names else rows:
            if row['published'] and not row['reviewed']:continue
            if row['audio_id'] not in present or worker_intermediate(row['original_directory']):continue
            if not matches(row, filters):continue
            aid = row['audio_id']
            seen.add(aid)
            if row['reviewed']:
                reviewed.add(aid)
            elif row['skipped']:
                skipped.add(aid)
        remaining = seen - reviewed
        return {'total': len(seen), 'reviewed': len(reviewed), 'remaining': len(remaining),
                'skipped': len(skipped), 'reserved_elsewhere': len(remaining & reserved),
                'in_queue': len(remaining - skipped - reserved), 'directory': filters.get('directory', '')}

    def counts(self, rid, overrides=None):
        with closing(self.db.connect()) as c:
            session = c.execute("SELECT * FROM sessions WHERE reviewer_id=?", (rid,)).fetchone()
            if session is None:
                raise ValueError("Choose a reviewer before continuing")
            return self.count_scope(c, session, overrides)

    def browse(self, rid, offset, limit):
        with closing(self.db.connect()) as c:
            session = dict(c.execute("SELECT * FROM sessions WHERE reviewer_id=?", (rid,)).fetchone())
            rows = self.candidates(c, session, randomize=False)
            leases = c.execute("""SELECT audio_id,reviewer_id,ordinal FROM queue_reservations
                                  WHERE expires_at>? ORDER BY ordinal""", (self.clock(),)).fetchall()
        ranks = {row["audio_id"]: row["ordinal"] for row in leases if row["reviewer_id"] == rid}
        unavailable = {row["audio_id"] for row in leases if row["reviewer_id"] != rid}
        rows = [row for row in rows if row["audio_id"] not in unavailable]
        # Show the active reservation and reserved-ahead tracks first. The rest
        # keep a stable browse order even when assignment is set to Random.
        rows.sort(key=lambda row: (-1 if row["id"] == session["current_generation_id"]
                                   else ranks.get(row["audio_id"], float("inf"))))
        page, has_more = available_page(self.db, rows, offset, limit)
        columns = ("id", "title", "genre", "duration")
        return {"items": [{key: row[key] for key in columns} for row in page], "has_more": has_more}

    def open(self, rid, gid, include_counts=True):
        with self.db.transaction() as c:
            self.sweep(c)
            session = self.session(c, rid)
            row = c.execute('SELECT id,audio_id FROM generations WHERE id=?', (gid,)).fetchone()
            if not row:
                raise KeyError('Recording not found')
            if c.execute('SELECT 1 FROM reviews WHERE audio_id=?', (row['audio_id'],)).fetchone():
                raise Conflict('This recording has a review. Use Reassess for your own review or Listen.', 'already_reviewed')
            lease = c.execute('SELECT reviewer_id FROM queue_reservations WHERE audio_id=?', (row['audio_id'],)).fetchone()
            if lease and lease[0] != rid:
                raise Conflict('This recording is reserved by another reviewer. Try again after it is released.', 'reserved')
            if not self.service.archiver.available(row['audio_id']):
                raise FileNotFoundError('Recording unavailable')
            self.release(rid, c)
            c.execute('DELETE FROM queue_skips WHERE session_id=? AND audio_id=?', (session['id'], row['audio_id']))
            timestamp = self.clock()
            c.execute('INSERT INTO queue_reservations VALUES(?,?,?,?,?,?)', (row['audio_id'], rid, gid, 0, timestamp, timestamp+self.service.config.values['reservation_seconds']))
            c.execute('UPDATE sessions SET current_generation_id=?,opened_generation_id=?,updated_at=? WHERE id=?', (gid, gid, now(), session['id']))
        return self.next(rid, include_counts=include_counts)

    def next(self, rid, peek=False, expected_current=None, include_counts=True):
        with self.db.transaction() as c:
            self.sweep(c)
            session = self.session(c, rid)
            if expected_current is not None and session["current_generation_id"] != expected_current:
                return {"track": None, "reason": "stale_current", "can_back": bool(session["previous_generation_id"]), "has_skips": False}
            if expected_current is not None and not c.execute("SELECT 1 FROM queue_reservations WHERE reviewer_id=? AND generation_id=?", (rid, expected_current)).fetchone():
                return {"track": None, "reason": "stale_current", "can_back": bool(session["previous_generation_id"]), "has_skips": False}
            candidates = self.candidates(c, session)
            eligible = {r["audio_id"]: r for r in candidates}
            leases = []
            reservations = [dict(r) for r in c.execute("SELECT * FROM queue_reservations ORDER BY ordinal,created_at")]
            reserved_elsewhere = {r["audio_id"] for r in reservations if r["reviewer_id"] != rid}
            for lease in reservations:
                if lease["reviewer_id"] != rid:
                    continue
                if lease["audio_id"] in eligible and self.service.archiver.available(lease["audio_id"]):
                    leases.append(lease)
                else:
                    c.execute("DELETE FROM queue_reservations WHERE audio_id=? AND reviewer_id=?", (lease["audio_id"], rid))
            # Retain a disconnected device's current recording if its expired
            # lease is still available. Another reviewer may already own it.
            current = next((r for r in candidates if r["id"] == session["current_generation_id"]), None)
            held = {r["audio_id"] for r in leases}
            if current and current["audio_id"] not in held and current["audio_id"] not in reserved_elsewhere and self.service.archiver.available(current["audio_id"]):
                leases.insert(0, {"audio_id": current["audio_id"], "generation_id": current["id"]})
                held.add(current["audio_id"])
            limit = 1 + self.service.config.values["reservation_queue_ahead"]
            # Zero ahead means reserve only the current recording.
            for row in candidates:
                if len(leases) >= limit:
                    break
                if row["audio_id"] not in held and row["audio_id"] not in reserved_elsewhere and self.service.archiver.available(row["audio_id"]):
                    leases.append({"audio_id": row["audio_id"], "generation_id": row["id"]})
                    held.add(row["audio_id"])
            for extra in leases[limit:]:
                c.execute("DELETE FROM queue_reservations WHERE audio_id=? AND reviewer_id=?", (extra["audio_id"], rid))
            leases = leases[:limit]
            timestamp = self.clock()
            expiry = timestamp + self.service.config.values["reservation_seconds"]
            for ordinal, lease in enumerate(leases):
                c.execute("""INSERT INTO queue_reservations VALUES(?,?,?,?,?,?) ON CONFLICT(audio_id) DO UPDATE SET ordinal=excluded.ordinal,expires_at=excluded.expires_at
                    WHERE queue_reservations.reviewer_id=excluded.reviewer_id""", (lease["audio_id"], rid, lease["generation_id"], ordinal, timestamp, expiry))
            gid = leases[0]["generation_id"] if leases else None
            # Preparing a queue can reserve it, but never advances the current.
            if not peek:
                c.execute("UPDATE sessions SET current_generation_id=?,updated_at=? WHERE id=?", (gid, now(), session["id"]))
            chosen = leases[1]["generation_id"] if peek and len(leases) > 1 else gid if not peek else None
            skips = bool(c.execute("SELECT 1 FROM queue_skips WHERE session_id=? LIMIT 1", (session["id"],)).fetchone())
            if chosen:
                reason = "ready"
            elif c.execute("SELECT EXISTS(SELECT 1 FROM audio) AND NOT EXISTS(SELECT 1 FROM audio a WHERE NOT EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=a.id))").fetchone()[0]:
                reason = "first_pass_complete"
            elif reserved_elsewhere.intersection(eligible):
                reason = "reserved"
            elif skips and not candidates:
                reason = "all_skipped"
            elif not candidates and json.loads(session["filters_json"]):
                reason = "no_match"
            else:
                reason = "no_tracks"
            result = {"reason": reason, "can_back": bool(session["previous_generation_id"]), "has_skips": skips,
                      "reservation_expires_at": expiry if leases else None, "reserved_ahead": max(0, len(leases)-1)}
        result["track"] = self.service.public_track(chosen, reviewer_id=rid) if chosen else None
        if include_counts:
            result["counts"] = self.counts(rid)
        return result

    def prefetch_target(self, rid, current_id):
        if not self.service.config.values["reservation_queue_ahead"]:
            return None
        with closing(self.db.connect()) as c:
            timestamp = self.clock()
            current = c.execute("""SELECT q.expires_at FROM sessions s JOIN queue_reservations q
                ON q.reviewer_id=s.reviewer_id AND q.generation_id=s.current_generation_id
                WHERE s.reviewer_id=? AND s.current_generation_id=?""", (rid, current_id)).fetchone()
            if current and current["expires_at"] > timestamp:
                next_row = c.execute("""SELECT q.generation_id FROM queue_reservations q
                    WHERE q.reviewer_id=? AND q.ordinal>0 AND q.expires_at>?
                    AND NOT EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=q.audio_id)
                    AND NOT EXISTS(SELECT 1 FROM publishing_selection p WHERE p.generation_id=q.generation_id AND p.status='published')
                    ORDER BY q.ordinal LIMIT 1""", (rid, timestamp)).fetchone()
                return next_row["generation_id"] if next_row else None
        if current:
            with self.db.transaction() as c:
                self.sweep(c)
        return None

    def require_current(self, c, rid, gid, audio_id):
        session = self.session(c, rid)
        lease = c.execute("SELECT * FROM queue_reservations WHERE audio_id=? AND reviewer_id=? AND generation_id=? AND expires_at>?", (audio_id, rid, gid, self.clock())).fetchone()
        if not lease or session["current_generation_id"] != gid:
            raise Conflict("This recording's reservation expired or changed. Resume your queue before rating it.", "reservation_lost")
        return session

    def heartbeat(self, rid, current_id=None, include_counts=True):
        with self.db.transaction() as c:
            self.sweep(c)
            session = self.session(c, rid)
            limit = 1 + self.service.config.values["reservation_queue_ahead"]
            c.execute("""DELETE FROM queue_reservations WHERE reviewer_id=? AND audio_id NOT IN
                (SELECT audio_id FROM queue_reservations WHERE reviewer_id=? ORDER BY ordinal,created_at LIMIT ?)""", (rid, rid, limit))
            valid = not current_id or (session["current_generation_id"] == current_id and c.execute("SELECT 1 FROM queue_reservations WHERE reviewer_id=? AND generation_id=?", (rid, current_id)).fetchone() is not None)
            # An expired lease cannot be renewed over someone else's claim.
            expiry = self.clock() + self.service.config.values["reservation_seconds"]
            c.execute("UPDATE queue_reservations SET expires_at=? WHERE reviewer_id=?", (expiry, rid))
        result = {"valid_current": bool(valid), "reservation_expires_at": expiry}
        if include_counts:
            result["counts"] = self.counts(rid)
        return result

