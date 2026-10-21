import json
import logging
from contextlib import closing
import math
import random
import threading
import time
import uuid
from . import tags as review_tags
from datetime import datetime
from pathlib import Path
from .analysis import Analyzer
from .archive import Archiver
from .db import Database
from .indexer import Indexer, first
from .metadata_identity import display_metadata
from .playback import Playback
from .util import now, dumps, inside
from .reviewers import ReviewerQueues, Conflict

LOG = logging.getLogger("gbr_review")


class Service:
    def __init__(self, config):
        self.config = config
        self.db = Database(config.database)
        self.archiver = Archiver(config, self.db)
        self.indexer = Indexer(config, self.db)
        self.analyzer = Analyzer(config, self.db, self.archiver)
        self.playback = Playback(config, self.db, self.archiver)
        self.stop_event, self.scan_event = threading.Event(), threading.Event()
        self.worker = None
        self.worker_state = "idle"
        self.queue_lock = threading.RLock()
        self.config_lock = threading.RLock()
        self.comparisons = {}
        self.prefetched_id = None
        self.analysis_generation = 0
        self.queues = ReviewerQueues(self)
        self.default_reviewer_id = self.db.one("SELECT id FROM reviewers WHERE name_key='simon'")["id"]
        if self.db.migration_backup:
            LOG.info("Database migration completed. Previous database: %s", self.db.migration_backup)

    def start(self):
        try:
            for bucket in range(6):
                (self.config.archive / str(bucket)).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            self.indexer.error(str(self.config.archive), "Archive unavailable: " + str(exc))
        self.archiver.reconcile()
        if getattr(self, 'preparation', None):
            self.preparation.sync_selection()
        self.db.execute("UPDATE audio SET analysis_state='pending' WHERE analysis_state='analysing'")
        self.db.execute("UPDATE audio SET analysis_state='retune_pending' WHERE analysis_state='retuning'")
        for a in self.db.all("SELECT id FROM audio WHERE analysis_state='ready'"):
            analysis = self.db.one("SELECT preview_path,spectrogram_path FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (a["id"],))
            if not analysis or not Path(analysis["preview_path"]).is_file() or not Path(analysis["spectrogram_path"]).is_file():
                self.db.execute("UPDATE audio SET analysis_state='pending' WHERE id=?", (a["id"],))
        self.scan_event.set()
        self.worker = threading.Thread(target=self.work, name="gbr-background", daemon=True)
        self.worker.start()

    def close(self):
        self.stop_event.set()
        if getattr(self, 'writing', None):
            self.writing.close()
        if getattr(self, 'preparation', None):
            self.preparation.close()
        if self.worker:
            self.worker.join(timeout=8)

    def work(self):
        next_scan = 0
        while not self.stop_event.is_set():
            try:
                op = self.db.one("SELECT * FROM archive_operations WHERE status='pending' ORDER BY created_at,rowid LIMIT 1")
                if op:
                    self.worker_state = "archiving"
                    with self.config_lock:
                        self.archiver.process(op)
                    continue
                if self.scan_event.is_set() or time.monotonic() >= next_scan:
                    self.scan_event.clear()
                    self.worker_state = "scanning"
                    with self.config_lock:
                        self.indexer.scan()
                    next_scan = time.monotonic() + self.config.values["scan_interval_seconds"]
                    continue
                a = self.db.one("""SELECT id,analysis_state FROM audio WHERE analysis_state IN ('pending','retune_pending')
                  ORDER BY CASE WHEN id IN (SELECT audio_id FROM queue_reservations WHERE ordinal=0 AND expires_at>?) THEN 0
                                WHEN id IN (SELECT audio_id FROM queue_reservations WHERE expires_at>?) THEN 1 ELSE 2 END,created_at LIMIT 1""", (self.queues.clock(), self.queues.clock()))
                if a:
                    self.worker_state = "analysing"
                    generation = self.analysis_generation
                    cached = a["analysis_state"] == "retune_pending"
                    self.db.execute("UPDATE audio SET analysis_state=? WHERE id=?", ("retuning" if cached else "analysing", a["id"]))
                    try:
                        if cached:
                            self.analyzer.retune(a["id"])
                        else:
                            self.analyzer.run(a["id"])
                        if generation != self.analysis_generation:
                            self.db.execute("UPDATE audio SET analysis_state='retune_pending' WHERE id=?", (a["id"],))
                        self.db.execute("DELETE FROM scan_errors WHERE path=?", ("analysis:" + a["id"],))
                    except Exception as exc:
                        self.db.execute("UPDATE audio SET analysis_state='failed' WHERE id=?", (a["id"],))
                        self.indexer.error("analysis:" + a["id"], str(exc))
                        LOG.exception("Analysis failed")
                    continue
                self.worker_state = "idle"
                self.stop_event.wait(.5)
            except Exception:
                LOG.exception("Background task failed")
                self.stop_event.wait(2)

    def public_track(self, gid, correction=False, reviewer_id=None):
        g = self.db.one("""SELECT g.*,s.title,s.genre,a.duration,a.channels,a.analysis_state,m.parsed_json
          FROM generations g JOIN songs s ON s.id=g.song_id JOIN audio a ON a.id=g.audio_id
          JOIN generation_metadata m ON m.generation_id=g.id WHERE g.id=?""", (gid,))
        if not g:
            return None
        meta = display_metadata(self.db,g)
        intended_genre = first(meta, "style_primary", "style", "genre", "intended_genre", "cluster")
        result = {"id": gid, "audio_id": g["audio_id"], "title": str(first(meta,'title','song','song_title') or g["title"]),
                  "genre": str(intended_genre).strip() if intended_genre else "Genre not supplied",
                  "cluster": str(first(meta, "cluster") or "").strip(),
                  "duration": g["duration"], "channels": g["channels"], "analysis_state": g["analysis_state"], "correction": correction}
        result['flags'] = self.track_flags(gid, reviewer_id or self.default_reviewer_id)
        if correction:
            r = self.db.one("SELECT * FROM latest_reviewer_reviews WHERE audio_id=? AND reviewer_id=?", (g["audio_id"], reviewer_id or self.default_reviewer_id))
            if not r:
                raise Conflict("You can only edit your own reviews", "other_reviewer")
            if r:
                with closing(self.db.connect()) as c:
                    tags = review_tags.selections(c, r['id'])
                result.update(review_id=r["id"], scores={k: r[k] for k in ("audio_quality", "composition", "consistency", "realism")}, previous_mode=r["mode"], reason=r['reason'], tags=tags, tags_recorded=bool(r['tags_recorded']))
        return result

    def next(self, peek=False, reviewer_id=None, expected_current=None):
        rid = reviewer_id or self.default_reviewer_id
        result = self.queues.next(rid, peek, expected_current)
        if rid == self.default_reviewer_id:
            self.prefetched_id = result["track"]["id"] if peek and result["track"] else None
        return result

    def review(self, gid, scores, mode, expected_review_id=None, listening=None, reassessment=False, reviewer_id=None, reason=None, distribution=None, bookmarked=None, expected_publishing_status=None, submission_id=None, rejected=None, tags=None):
        reviewer_id = reviewer_id or self.default_reviewer_id
        with self.db.transaction() as c:
            g = c.execute("SELECT * FROM generations WHERE id=?", (gid,)).fetchone()
            if not g:
                raise KeyError("Recording not found")
            if submission_id:
                saved = c.execute('SELECT * FROM reviews WHERE reviewer_id=? AND submission_id=?', (reviewer_id, submission_id)).fetchone()
                if saved:
                    flags = json.loads(saved['flags_json'])
                    same_tags = tags is None or review_tags.validate(c, tags, saved['id']) == review_tags.selections(c, saved['id'])
                    if not same_tags or saved['generation_id'] != gid or any(saved[k] != value for k, value in scores.items()) or (reason is not None and saved['reason'] != reason) or (distribution is not None and flags.get('distribution') != distribution) or (bookmarked is not None and flags.get('bookmarked') != bookmarked) or (rejected is not None and flags.get('requested_rejected', flags.get('rejected')) != rejected):
                        raise Conflict('The earlier submission was saved. Use Back to edit that review; your current entries have not been submitted.', 'submission_saved')
                    return {'saved': True, 'review_id': saved['id'], 'already_saved': True}
            session = self.queues.session(c, reviewer_id)
            previous = c.execute("SELECT * FROM latest_reviewer_reviews WHERE audio_id=? AND reviewer_id=?", (g["audio_id"], reviewer_id)).fetchone()
            any_review = c.execute("SELECT 1 FROM reviews WHERE audio_id=?", (g["audio_id"],)).fetchone()
            if previous:
                if previous["id"] != expected_review_id:
                    raise Conflict("Your previous review changed. Reload it before editing.")
                if not reassessment and session["previous_generation_id"] != gid:
                    raise Conflict("Back can only edit your own previous recording")
            else:
                if any_review:
                    raise Conflict("Someone has already reviewed this recording. The first pass does not assign it again.", "already_reviewed")
                if expected_review_id:
                    raise Conflict("There is no earlier review by you to correct", "other_reviewer")
                self.queues.require_current(c, reviewer_id, gid, g["audio_id"])
            reason = previous['reason'] if previous and reason is None else reason or ''
            previous_tags = review_tags.selections(c, previous['id']) if previous else []
            selected_tags = previous_tags if tags is None else review_tags.validate(c, tags, previous['id'] if previous else None)
            tags_recorded = int(tags is not None or bool(previous and previous['tags_recorded']))
            flags = self._apply_flags(c, gid, reviewer_id, distribution, bookmarked, expected_publishing_status, rejected)
            automatic_rejection = min(scores.values()) <= 3 and not (flags['distribution'] or flags['bookmarked'])
            if automatic_rejection:
                self._publishing(c, gid, 'rejected')
                flags = self._flags(c, gid, reviewer_id)
            flags.update(automatic_rejection=automatic_rejection, requested_rejected=rejected)
            average = sum(scores.values()) / 4
            bucket = math.floor(average + .5)
            maximum = c.execute("SELECT COALESCE(MAX(revision),0) FROM reviews WHERE audio_id=?", (g["audio_id"],)).fetchone()[0]
            revision = maximum + 1
            review_id, timestamp = uuid.uuid4().hex, now()
            assessment_id = previous["assessment_id"] if previous else uuid.uuid4().hex
            if not previous:
                c.execute("INSERT INTO assessments VALUES(?,?,?,?)", (assessment_id, g["audio_id"], reviewer_id, timestamp))
            c.execute("""INSERT INTO reviews(id,generation_id,audio_id,audio_quality,composition,consistency,realism,average,bucket,created_at,mode,revision,session_id,previous_review_id,listening_json,reviewer_id,assessment_id,assessment_revision,reason,flags_json,submission_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (review_id, gid, g["audio_id"], scores["audio_quality"], scores["composition"], scores["consistency"], scores["realism"],
                       average, bucket, timestamp, mode, revision, session["id"], previous["id"] if previous else None, dumps(listening or {}),
                       reviewer_id, assessment_id, previous["assessment_revision"] + 1 if previous else 1, reason, dumps(flags), submission_id))
            c.execute('UPDATE reviews SET tags_recorded=? WHERE id=?', (tags_recorded, review_id))
            c.executemany('INSERT INTO review_tags(review_id,tag_id,polarity) VALUES(?,?,?)',
                          [(review_id, tag['tag_id'], tag['polarity']) for tag in selected_tags])
            c.execute("""INSERT INTO review_history(audio_id,generation_id,action,previous_review_id,review_id,details_json,created_at,reviewer_id,assessment_id)
                       VALUES(?,?,?,?,?,?,?,?,?)""",
                      (g["audio_id"], gid, ("reassessment" if reassessment else "correction") if previous else "review", previous["id"] if previous else None, review_id,
                       dumps({"scores": scores, "mode": mode, "reason": reason, "flags": flags, "tags": selected_tags, "tags_recorded": bool(tags_recorded), "previous_tags": previous_tags if previous else None, "previous_scores": {k: previous[k] for k in scores} if previous else None}), timestamp, reviewer_id, assessment_id))
            c.execute("INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                      (uuid.uuid4().hex, g["audio_id"], gid, review_id, bucket, timestamp, timestamp))
            if getattr(self, 'preparation', None):
                self.preparation.queue_promotion(c, gid)
            c.execute("UPDATE sessions SET previous_generation_id=?,updated_at=? WHERE id=?", (gid, timestamp, session["id"]))
            if not previous:
                c.execute("DELETE FROM queue_reservations WHERE audio_id=? AND reviewer_id=?", (g["audio_id"], reviewer_id))
                c.execute("UPDATE sessions SET current_generation_id=NULL,opened_generation_id=NULL WHERE id=?", (session["id"],))
        return {"saved": True, "review_id": review_id}

    def skip(self, gid, reviewer_id=None):
        reviewer_id = reviewer_id or self.default_reviewer_id
        with self.db.transaction() as c:
            g = c.execute("SELECT audio_id FROM generations WHERE id=?", (gid,)).fetchone()
            if not g:
                raise KeyError("Recording not found")
            session = self.queues.require_current(c, reviewer_id, gid, g[0])
            c.execute("INSERT OR IGNORE INTO queue_skips VALUES(?,?)", (session["id"], g[0]))
            c.execute("INSERT INTO review_history(audio_id,generation_id,action,details_json,created_at,reviewer_id) VALUES(?,?,'skip','{}',?,?)", (g[0], gid, now(), reviewer_id))
            c.execute("DELETE FROM queue_reservations WHERE audio_id=? AND reviewer_id=?", (g[0], reviewer_id))
            c.execute("UPDATE sessions SET current_generation_id=NULL,opened_generation_id=NULL WHERE id=?", (session["id"],))

    def queue_settings(self, order, direction, filters, return_skipped=False, reviewer_id=None):
        reviewer_id = reviewer_id or self.default_reviewer_id
        with self.db.transaction() as c:
            session = self.queues.session(c, reviewer_id)
            self.queues.release(reviewer_id, c)
            c.execute("UPDATE sessions SET order_by=?,direction=?,filters_json=?,updated_at=? WHERE id=?",
                      (order, direction, dumps(filters), now(), session["id"]))
            if return_skipped:
                c.execute("DELETE FROM queue_skips WHERE session_id=?", (session["id"],))
        if reviewer_id == self.default_reviewer_id:
            self.prefetched_id = None

    def publishing(self, gid, status, reference=None):
        with self.db.transaction() as c:
            self._publishing(c, gid, status, reference)

    def _publishing(self, c, gid, status, reference=None):
        row = c.execute("SELECT g.id,r.id AS review_id FROM generations g LEFT JOIN latest_reviews r ON r.audio_id=g.audio_id WHERE g.id=?", (gid,)).fetchone()
        if not row:
            raise KeyError('Recording not found')
        if not row['review_id'] and status not in ('selected', 'not_selected', 'rejected'):
            raise ValueError("Complete a human review first")
        old = c.execute("SELECT * FROM publishing_selection WHERE generation_id=?", (gid,)).fetchone()
        if old and old['status'] == status and (reference is None or reference == old['external_reference']):
            return
        if status == "published" and (not old or old["status"] not in ("selected", "published")):
            raise ValueError("Select this generation before marking it published")
        timestamp = now()
        selected = old["selected_at"] if old else None
        published = old["published_at"] if old else None
        if status == "selected":
            selected = timestamp if not selected else selected
        if status == "published":
            published = timestamp if not published else published
        reference = reference if reference is not None else (old["external_reference"] if old else None)
        c.execute("INSERT INTO publishing_selection VALUES(?,?,?,?,?,?) ON CONFLICT(generation_id) DO UPDATE SET status=excluded.status,selected_at=excluded.selected_at,published_at=excluded.published_at,external_reference=excluded.external_reference,updated_at=excluded.updated_at",
                  (gid, status, selected, published, reference, timestamp))
        c.execute("INSERT INTO publishing_history(generation_id,old_status,new_status,external_reference,created_at) VALUES(?,?,?,?,?)",
                  (gid, old["status"] if old else "not_selected", status, reference, timestamp))
        if status == 'selected' and getattr(self, 'preparation', None):
            self.preparation.queue_promotion(c, gid)

    def _flags(self, c, gid, rid):
        status = c.execute('SELECT status FROM publishing_selection WHERE generation_id=?', (gid,)).fetchone()
        status = status[0] if status else 'not_selected'
        return {'distribution': status in ('selected', 'published'), 'publishing_status': status,
                'rejected': status == 'rejected',
                'bookmarked': bool(c.execute('SELECT 1 FROM bookmarks WHERE generation_id=? AND reviewer_id=?', (gid, rid)).fetchone())}

    def track_flags(self, gid, rid):
        with closing(self.db.connect()) as c:
            c.execute('BEGIN')
            if not c.execute('SELECT 1 FROM generations WHERE id=?', (gid,)).fetchone():
                raise KeyError('Recording not found')
            return self._flags(c, gid, rid)

    def _apply_flags(self, c, gid, rid, distribution=None, bookmarked=None, expected_status=None, rejected=None):
        before = self._flags(c, gid, rid)
        protective = (before['distribution'] if distribution is None else distribution) or (before['bookmarked'] if bookmarked is None else bookmarked)
        if rejected is True and protective:
            raise ValueError('Turn off Publish and Bookmark before marking Reject')
        if rejected is not None and rejected != before['rejected'] and expected_status is not None and before['publishing_status'] != expected_status:
            raise Conflict('Track status changed in another view. Refresh flags before trying again.', 'flags_changed')
        if distribution is not None and distribution != before['distribution']:
            if expected_status is not None and before['publishing_status'] != expected_status:
                raise Conflict('Distribution status changed in another view. Refresh track flags and try again.', 'flags_changed')
            if before['publishing_status'] == 'published':
                raise Conflict('This track is published. Change its publishing state in Selection.', 'flags_changed')
            self._publishing(c, gid, 'selected' if distribution else 'not_selected')
        if bookmarked is not None and bookmarked != before['bookmarked']:
            timestamp = now()
            if bookmarked:
                c.execute('INSERT INTO bookmarks VALUES(?,?,?,?)', (gid, rid, timestamp, timestamp))
            else:
                c.execute('DELETE FROM bookmarks WHERE generation_id=? AND reviewer_id=?', (gid, rid))
            c.execute('INSERT INTO bookmark_history(generation_id,reviewer_id,action,created_at) VALUES(?,?,?,?)', (gid, rid, 'bookmarked' if bookmarked else 'removed', timestamp))
        current = self._flags(c, gid, rid)
        if rejected is True and not current['rejected']:
            if current['publishing_status'] == 'published':
                raise Conflict('This track is published. Change its publishing state in Selection.', 'flags_changed')
            self._publishing(c, gid, 'rejected')
        elif current['rejected'] and (rejected is False or current['distribution'] or current['bookmarked']):
            self._publishing(c, gid, 'not_selected')
        return self._flags(c, gid, rid)

    def set_flags(self, gid, rid, distribution=None, bookmarked=None, expected_status=None, rejected=None):
        with self.db.transaction() as c:
            self.queues.session(c, rid)
            if not c.execute('SELECT 1 FROM generations WHERE id=?', (gid,)).fetchone():
                raise KeyError('Recording not found')
            flags = self._apply_flags(c, gid, rid, distribution, bookmarked, expected_status, rejected)
            reviewed = c.execute('''SELECT r.audio_quality,r.composition,r.consistency,r.realism
                FROM latest_reviews r JOIN generations g ON g.audio_id=r.audio_id WHERE g.id=?''', (gid,)).fetchone()
            if reviewed and min(reviewed) <= 3 and not (flags['distribution'] or flags['bookmarked']):
                self._publishing(c, gid, 'rejected')
                flags = self._flags(c, gid, rid)
            return flags

    def comparison(self, a, b):
        gs = [self.db.one("SELECT * FROM generations WHERE id=?", (gid,)) for gid in (a, b)]
        if not all(gs) or a == b or gs[0]["song_id"] != gs[1]["song_id"] or gs[0]["audio_id"] == gs[1]["audio_id"]:
            raise ValueError("Choose two different recordings of the same song")
        ms = [self.db.one("SELECT metrics_json FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (g["audio_id"],)) for g in gs]
        if not all(ms):
            raise ValueError("Wait for both recordings to finish analysis")
        metrics = [json.loads(m["metrics_json"]) for m in ms]
        if any(m.get("integrated_lufs") is None for m in metrics):
            raise ValueError("Silence cannot be loudness matched")
        # Reduce both to a shared target that also provides 1dB true-peak headroom.
        target = min(-18., *(m["integrated_lufs"] - m["true_peak_dbtp_4x"] - 1 for m in metrics))
        random.shuffle(gs)
        cid = uuid.uuid4().hex
        self.comparisons[cid] = {"a": gs[0]["id"], "b": gs[1]["id"], "target": target, "created": time.monotonic()}
        self.comparisons = {k: v for k, v in self.comparisons.items() if time.monotonic() - v["created"] < 86400}
        return {"id": cid, "title": self.public_track(a)["title"]}


