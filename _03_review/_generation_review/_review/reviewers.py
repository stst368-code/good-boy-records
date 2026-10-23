"""Reviewer identity and audio-exclusive, expiring server-side queue reservations."""
import json
import random
import time
import uuid
from contextlib import closing
from datetime import datetime
from .util import now, dumps, inside
from .metadata_identity import worker_intermediate
from .filtering import PROJECTION, matches, prepare, song_names, available_audio


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

    def candidates(self, c, session):
        names = song_names(self.service)
        filters = prepare(json.loads(session["filters_json"]), names)
        rows = [dict(r) for r in c.execute(f"""SELECT g.id,g.audio_id,g.generation_timestamp,g.created_at,g.original_directory,{PROJECTION}
            FROM generations g JOIN songs s ON s.id=g.song_id LEFT JOIN generation_metadata m ON m.generation_id=g.id
            WHERE NOT EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id)
            AND NOT EXISTS(SELECT 1 FROM queue_skips k WHERE k.audio_id=g.audio_id AND k.session_id=?)
            ORDER BY g.rowid""", (session["id"],))]
        rows=names.annotate(rows)
        rows=[r for r in rows if r['id']==session.get('opened_generation_id') or not worker_intermediate(r['original_directory'])]
        opened = next((r for r in rows if r['id'] == session.get('opened_generation_id')), None)
        rows = [r for r in rows if matches(r, filters)]
        if session["order_by"] == "random":
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
        names = song_names(self.service)
        filters = prepare({**json.loads(session['filters_json']), **(overrides or {})}, names)
        rows = c.execute(f'''SELECT g.id,g.audio_id,g.original_directory,{PROJECTION},
            EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=g.audio_id) AS reviewed,
            EXISTS(SELECT 1 FROM queue_skips k WHERE k.audio_id=g.audio_id AND k.session_id=?) AS skipped
            FROM generations g JOIN songs s ON s.id=g.song_id LEFT JOIN generation_metadata m ON m.generation_id=g.id''', (session['id'],))
        seen, reviewed, skipped = set(), set(), set()
        present = available_audio(self.db)
        for row in names.annotate(rows):
            if row['audio_id'] not in present or worker_intermediate(row['original_directory']):continue
            if not matches(row, filters):continue
            aid = row['audio_id']
            seen.add(aid)
            if row['reviewed']:
                reviewed.add(aid)
            elif row['skipped']:
                skipped.add(aid)
        remaining = seen - reviewed
        reserved = {r[0] for r in c.execute('SELECT audio_id FROM queue_reservations WHERE reviewer_id<>? AND expires_at>?', (session['reviewer_id'], self.clock()))}
        return {'total': len(seen), 'reviewed': len(reviewed), 'remaining': len(remaining),
                'skipped': len(skipped), 'reserved_elsewhere': len(remaining & reserved),
                'in_queue': len(remaining - skipped - reserved), 'directory': filters.get('directory', '')}

    def counts(self, rid, overrides=None):
        with self.db.transaction() as c:
            return self.count_scope(c, self.session(c, rid), overrides)

    def open(self, rid, gid):
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
        return self.next(rid)

    def next(self, rid, peek=False, expected_current=None):
        with self.db.transaction() as c:
            self.sweep(c)
            session = self.session(c, rid)
            if expected_current is not None and session["current_generation_id"] != expected_current:
                return {"track": None, "reason": "stale_current", "can_back": bool(session["previous_generation_id"]), "has_skips": False}
            if expected_current is not None and not c.execute("SELECT 1 FROM queue_reservations WHERE reviewer_id=? AND generation_id=?", (rid, expected_current)).fetchone():
                return {"track": None, "reason": "stale_current", "can_back": bool(session["previous_generation_id"]), "has_skips": False}
            candidates = self.candidates(c, session)
            eligible = {r["audio_id"]: r for r in candidates}
            leases = [dict(r) for r in c.execute("SELECT * FROM queue_reservations WHERE reviewer_id=? ORDER BY ordinal,created_at", (rid,))]
            for lease in leases:
                if lease["audio_id"] not in eligible or not self.service.archiver.available(lease["audio_id"]):
                    c.execute("DELETE FROM queue_reservations WHERE audio_id=? AND reviewer_id=?", (lease["audio_id"], rid))
            leases = [r for r in leases if r["audio_id"] in eligible and self.service.archiver.available(r["audio_id"])]
            # Retain a disconnected device's current recording if its expired
            # lease is still available. Another reviewer may already own it.
            current = next((r for r in candidates if r["id"] == session["current_generation_id"]), None)
            held = {r["audio_id"] for r in leases}
            if current and current["audio_id"] not in held and not c.execute("SELECT 1 FROM queue_reservations WHERE audio_id=?", (current["audio_id"],)).fetchone() and self.service.archiver.available(current["audio_id"]):
                leases.insert(0, {"audio_id": current["audio_id"], "generation_id": current["id"]})
                held.add(current["audio_id"])
            limit = 1 + self.service.config.values["reservation_queue_ahead"]
            # Zero ahead means reserve only the current recording.
            for row in candidates:
                if len(leases) >= limit:
                    break
                if row["audio_id"] not in held and not c.execute("SELECT 1 FROM queue_reservations WHERE audio_id=?", (row["audio_id"],)).fetchone() and self.service.archiver.available(row["audio_id"]):
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
            remaining = c.execute("SELECT COUNT(*) FROM audio a WHERE NOT EXISTS(SELECT 1 FROM reviews r WHERE r.audio_id=a.id)").fetchone()[0]
            total = c.execute("SELECT COUNT(*) FROM audio").fetchone()[0]
            reserved_elsewhere = any(c.execute("SELECT 1 FROM queue_reservations WHERE audio_id=? AND reviewer_id<>?", (r["audio_id"], rid)).fetchone() for r in candidates)
            skips = bool(c.execute("SELECT 1 FROM queue_skips WHERE session_id=? LIMIT 1", (session["id"],)).fetchone())
            reason = "ready" if chosen else "first_pass_complete" if total and not remaining else "reserved" if reserved_elsewhere else "all_skipped" if skips and not candidates else "no_match" if json.loads(session["filters_json"]) and not candidates else "no_tracks"
            result = {"track_id": chosen, "reason": reason, "can_back": bool(session["previous_generation_id"]), "has_skips": skips,
                      "reservation_expires_at": expiry if leases else None, "reserved_ahead": max(0, len(leases)-1),
                      'counts': self.count_scope(c, session)}
        result["track"] = self.service.public_track(chosen, reviewer_id=rid) if chosen else None
        result.pop("track_id")
        return result

    def require_current(self, c, rid, gid, audio_id):
        session = self.session(c, rid)
        lease = c.execute("SELECT * FROM queue_reservations WHERE audio_id=? AND reviewer_id=? AND generation_id=? AND expires_at>?", (audio_id, rid, gid, self.clock())).fetchone()
        if not lease or session["current_generation_id"] != gid:
            raise Conflict("This recording's reservation expired or changed. Resume your queue before rating it.", "reservation_lost")
        return session

    def heartbeat(self, rid, current_id=None):
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
            return {"valid_current": bool(valid), "reservation_expires_at": expiry, 'counts': self.count_scope(c, session)}

