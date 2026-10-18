"""Journaled copy / verify / commit / remove. Never overwrite a destination."""
import hashlib
import json
import os
import shutil
import uuid
from pathlib import Path
from .indexer import audio_identity
from .util import digest, now, dumps, slug, inside
from .native_archive import extend_plan, commit_paths, remove_empty_bundles


def verified_copy(source, destination, expected, operation_id):
    source, destination = Path(source) if source else None, Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        if digest(destination) == expected:
            return
        raise FileExistsError(f"Destination conflict; preserved both files: {destination}")
    if not source or not source.is_file():
        raise FileNotFoundError(f"Source unavailable: {source}")
    if digest(source) != expected:
        raise ValueError(f"Source changed after indexing; rescan before retry: {source}")
    stage = destination.with_name(".gbr-" + operation_id + "-" + destination.name + ".part")
    # This exclusively named staging file is owned by this journal operation.
    if stage.exists() and digest(stage) != expected:
        stage.unlink()
    if not stage.exists():
        with source.open("rb") as src, stage.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
    if digest(stage) != expected:
        raise ValueError("Copy verification failed; source retained")
    try:
        # Atomic exclusive publication on NTFS/ext4/APFS; cannot overwrite.
        os.link(stage, destination)
    except FileExistsError:
        if digest(destination) != expected:
            raise
    except OSError:
        # FAT/exFAT fallback is still exclusive and verified. An interruption
        # can leave a conflict, which is retried under a new collision-safe name.
        with stage.open("rb") as src, destination.open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
            dst.flush()
            os.fsync(dst.fileno())
    if digest(destination) != expected:
        raise ValueError("Destination verification failed; source retained")
    stage.unlink(missing_ok=True)


class Archiver:
    def __init__(self, config, db):
        self.config, self.db = config, db

    def available(self, audio_id):
        a = self.db.one("SELECT * FROM audio WHERE id=?", (audio_id,))
        if a and a["archive_path"] and Path(a["archive_path"]).is_file():
            return Path(a["archive_path"])
        for m in self.db.all("SELECT * FROM media_files WHERE audio_id=? AND storage_state IN ('input','archived') ORDER BY id", (audio_id,)):
            if Path(m["path"]).is_file():
                return Path(m["path"])
        return None

    def plan(self, op):
        a = self.db.one("SELECT * FROM audio WHERE id=?", (op["audio_id"],))
        gs = self.db.all("""SELECT g.*,m.yaml_sha256,m.raw_yaml_bytes,m.yaml_archive_path,s.title
          FROM generations g JOIN generation_metadata m ON m.generation_id=g.id
          JOIN songs s ON s.id=g.song_id WHERE g.audio_id=? ORDER BY g.created_at,g.id""", (a["id"],))
        base = a["archive_stem"] or slug(gs[0]["title"]) + "__" + a["pcm_sha256"][:8]
        folder = self.config.archive / str(op["bucket"])
        source_audio = self.available(a["id"])
        if not source_audio:
            raise FileNotFoundError("Recording unavailable; review remains saved")
        expected_audio = digest(source_audio)
        # Extend identifiers instead of accepting or overwriting unrelated files.
        for length in [8, 12, 16, 32, 64, 80]:
            stem = base if length == 8 else slug(gs[0]["title"]) + "__" + a["pcm_sha256"][:length]
            if length == 80:
                stem += "_" + op["id"][:8]
            target = folder / (stem + ".flac")
            if not target.exists() or digest(target) == expected_audio:
                break
        else:
            raise FileExistsError("Could not reserve a collision-safe archive name")
        items = [{"kind": "audio", "source": str(source_audio), "destination": str(target), "sha256": expected_audio}]
        used, generation_yamls = {}, {}
        for g in gs:
            yh = g["yaml_sha256"]
            if yh is None:
                generation_yamls[g["id"]] = None
                continue
            if yh in used:
                generation_yamls[g["id"]] = used[yh]
                continue
            yaml_target = folder / (stem + ("" if not used else "__p" + yh[:12]) + ".yaml")
            if yaml_target.exists() and digest(yaml_target) != yh:
                yaml_target = folder / (stem + "__p" + yh + ".yaml")
            if yaml_target.exists() and digest(yaml_target) != yh:
                raise FileExistsError(f"YAML collision: {yaml_target}")
            yp = g["yaml_archive_path"]
            if not yp or not Path(yp).is_file():
                sources = self.db.all("SELECT yaml_path FROM generation_sources WHERE generation_id=?", (g["id"],))
                yp = next((r["yaml_path"] for r in sources if r["yaml_path"] and Path(r["yaml_path"]).is_file() and digest(r["yaml_path"]) == yh), None)
            if not yp:
                # Byte-identical YAML is retained in SQLite, including malformed YAML.
                recovered = self.config.data / "recovery" / (g["id"] + ".yaml")
                recovered.parent.mkdir(parents=True, exist_ok=True)
                if not recovered.exists():
                    with recovered.open("xb") as f:
                        f.write(g["raw_yaml_bytes"])
                        f.flush()
                        os.fsync(f.fileno())
                yp = str(recovered)
            items.append({"kind": "yaml", "source": yp, "destination": str(yaml_target), "sha256": yh})
            used[yh], generation_yamls[g["id"]] = str(yaml_target), str(yaml_target)
        originals = []
        for m in self.db.all("SELECT * FROM media_files WHERE audio_id=? AND storage_state IN ('input','archived')", (a["id"],)):
            originals.append({"kind": "audio", "source": m["path"], "sha256": m["sha256"], "media_id": m["id"]})
        for g in gs:
            for row in self.db.all("SELECT * FROM generation_sources WHERE generation_id=?", (g["id"],)):
                if row["yaml_path"]:
                    originals.append({"kind": "yaml", "source": row["yaml_path"], "sha256": g["yaml_sha256"]})
        return extend_plan(self.db, {"stem": stem, "audio_destination": str(target), "items": items,
                "generation_yamls": generation_yamls, "originals": originals}, gs, folder, stem)

    def process(self, op):
        latest = self.db.one("SELECT * FROM latest_reviews WHERE audio_id=?", (op["audio_id"],))
        # A superseded pending review must not undo a later correction.
        if not latest or latest["id"] != op["review_id"]:
            self.db.execute("UPDATE archive_operations SET status='superseded',updated_at=? WHERE id=?", (now(), op["id"]))
            return
        try:
            self.db.execute("UPDATE archive_operations SET status='running',attempts=attempts+1,error=NULL,updated_at=? WHERE id=?", (now(), op["id"]))
            plan = json.loads(op["plan_json"])
            if not plan:
                plan = self.plan(op)
                self.db.execute("UPDATE archive_operations SET plan_json=?,updated_at=? WHERE id=?", (dumps(plan), now(), op["id"]))
            for item in plan["items"]:
                verified_copy(item["source"], item["destination"], item["sha256"], op["id"])
            verification = {i["destination"]: {"sha256": i["sha256"], "verified": True} for i in plan["items"]}
            # Validate shared recording even for different FLAC-container encodings.
            if audio_identity(plan["audio_destination"])[0] != op["audio_id"]:
                raise ValueError("Decoded recording verification failed")
            with self.db.transaction() as c:
                c.execute("UPDATE audio SET archive_path=?,archive_stem=?,archive_bucket=? WHERE id=?",
                          (plan["audio_destination"], plan["stem"], op["bucket"], op["audio_id"]))
                for gid, yp in plan["generation_yamls"].items():
                    c.execute("UPDATE generation_metadata SET yaml_archive_path=? WHERE generation_id=?", (yp, gid))
                commit_paths(c, plan)
                c.execute("UPDATE archive_operations SET verification_json=?,updated_at=? WHERE id=?", (dumps(verification), now(), op["id"]))
            destinations = {i["destination"] for i in plan["items"]}
            # Only remove sources whose exact bytes still match the indexed version.
            for original in plan["originals"] + plan["items"]:
                source = Path(original["source"])
                if str(source) in destinations or not source.exists():
                    continue
                if original["sha256"] is None or digest(source) != original["sha256"]:
                    raise ValueError(f"Source changed; preserved it: {source}")
                # A shared YAML may be used by an unrelated recording. Leave it
                # until all referencing generations have an archived copy.
                if original["kind"] == "yaml":
                    shared = self.db.one("""SELECT 1 AS found FROM generation_sources s JOIN generation_metadata m ON m.generation_id=s.generation_id
                      WHERE s.yaml_path=? AND m.yaml_archive_path IS NULL LIMIT 1""", (str(source),))
                    if shared:
                        continue
                source.unlink()
            remove_empty_bundles(plan)
            with self.db.transaction() as c:
                for m in c.execute("SELECT id,path FROM media_files WHERE audio_id=? AND storage_state IN ('input','archived')", (op["audio_id"],)).fetchall():
                    if m["path"] != plan["audio_destination"]:
                        c.execute("UPDATE media_files SET storage_state='consolidated' WHERE id=?", (m["id"],))
                path = Path(plan["audio_destination"])
                st = path.stat()
                existing = c.execute("SELECT id FROM media_files WHERE path=? AND storage_state IN ('input','archived')", (str(path),)).fetchone()
                if existing:
                    c.execute("UPDATE media_files SET storage_state='archived' WHERE id=?", (existing[0],))
                else:
                    c.execute("""INSERT INTO media_files(audio_id,sha256,path,original_path,size,mtime_ns,storage_state,indexed_at)
                      VALUES(?,?,?,?,?,?,'archived',?)""", (op["audio_id"], digest(path), str(path), str(path), st.st_size, st.st_mtime_ns, now()))
                c.execute("UPDATE archive_operations SET status='done',error=NULL,updated_at=? WHERE id=?", (now(), op["id"]))
        except Exception as exc:
            self.db.execute("UPDATE archive_operations SET status='failed',error=?,updated_at=? WHERE id=?", (str(exc), now(), op["id"]))

    def retry(self):
        # Re-plan failed destinations to recover safely from external collisions.
        self.db.execute("""UPDATE archive_operations SET status='pending',
                        plan_json=CASE WHEN verification_json='{}' THEN '{}' ELSE plan_json END,
                        error=NULL,updated_at=? WHERE status='failed'""", (now(),))

    def reconcile(self):
        self.db.execute("UPDATE archive_operations SET status='pending' WHERE status='running'")
        for a in self.db.all("SELECT a.*,r.id AS review_id,r.generation_id,r.bucket FROM audio a JOIN latest_reviews r ON r.audio_id=a.id"):
            pending = self.db.one("SELECT id FROM archive_operations WHERE audio_id=? AND status IN ('pending','running','failed')", (a["id"],))
            if pending:
                continue
            yamls = self.db.all("SELECT yaml_sha256,yaml_archive_path FROM generation_metadata m JOIN generations g ON g.id=m.generation_id WHERE g.audio_id=?", (a["id"],))
            healthy = a["archive_path"] and Path(a["archive_path"]).is_file() and a["archive_bucket"] == a["bucket"]
            healthy = healthy and all(not y["yaml_sha256"] or (y["yaml_archive_path"] and Path(y["yaml_archive_path"]).is_file()) for y in yamls)
            if not healthy:
                self.db.execute("INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                                (uuid.uuid4().hex, a["id"], a["generation_id"], a["review_id"], a["bucket"], now(), now()))
        for m in self.db.all("SELECT id,path,storage_state FROM media_files WHERE storage_state IN ('input','archived')"):
            if not Path(m["path"]).is_file():
                self.db.execute("UPDATE media_files SET storage_state='missing' WHERE id=?", (m["id"],))

