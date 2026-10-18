import hashlib
import json
import re
import uuid
from pathlib import Path
import soundfile as sf
import yaml
from mutagen.flac import FLAC
from .util import digest, dumps, inside, now


def audio_identity(path):
    """Exact decoded PCM, independent of FLAC tags, compression, and filename."""
    h = hashlib.sha256()
    with sf.SoundFile(path) as f:
        h.update(f"PCM-int32-v1:{f.samplerate}:{f.channels}:".encode())
        for block in f.blocks(blocksize=65536, dtype="int32", always_2d=True):
            h.update(block.astype("<i4", copy=False).tobytes())
        return h.hexdigest(), dict(duration=f.frames / f.samplerate, sample_rate=f.samplerate,
                                   channels=f.channels, bit_depth=int(re.sub(r"\D", "", f.subtype) or 0))


def metadata_for(path, directory_entries=None):
    candidates = [path.with_suffix(".yaml"), path.with_suffix(".yml"), Path(str(path) + ".yaml"), Path(str(path) + ".yml")]
    if directory_entries is not None:
        sidecar = next((directory_entries[p.name.lower()] for p in candidates if p.name.lower() in directory_entries), None)
    else:
        sidecar = next((p for p in candidates if p.is_file()), None)
    if sidecar is None and directory_entries is None:
        # Case-insensitive fallback only when ordinary lookups fail.
        wanted = {p.name.lower() for p in candidates}
        sidecar = next((p for p in path.parent.iterdir() if p.name.lower() in wanted and p.is_file()), None)
    embedded, errors = {}, []
    try:
        tags = FLAC(path)
        for key, values in (tags.tags or {}).items():
            embedded[key.lower()] = values[0] if len(values) == 1 else values
    except Exception as exc:
        errors.append("Embedded metadata: " + str(exc))
    parsed_embedded, recovered = {}, None
    for key in ("yaml", "generation_yaml", "gbr_yaml", "prompt_yaml", "metadata", "comment", "description", "gbr_metadata", "generation_metadata"):
        value = embedded.get(key)
        if isinstance(value, str):
            try:
                obj = yaml.safe_load(value)
                if isinstance(obj, dict):
                    parsed_embedded.update(obj)
                    recovered = value
            except yaml.YAMLError:
                pass
    for key, value in embedded.items():
        if key not in ("comment", "description", "metadata", "yaml") and isinstance(value, str):
            parsed_embedded.setdefault(key, value)
    raw_bytes = sidecar.read_bytes() if sidecar else (recovered.encode("utf-8") if recovered else None)
    raw = raw_bytes.decode("utf-8-sig", errors="replace") if raw_bytes else None
    parsed = dict(parsed_embedded)
    status = "embedded" if recovered else "missing"
    if sidecar:
        if len(raw_bytes) > 8 * 1024 * 1024:
            raise ValueError("YAML exceeds 8 MiB metadata limit")
        try:
            obj = yaml.safe_load(raw)
            if not isinstance(obj, dict):
                raise ValueError("Generation YAML must contain a mapping")
            parsed.update(obj)
            status = "valid"
        except (yaml.YAMLError, ValueError) as exc:
            status = "invalid"
            errors.append(str(exc))
    try:
        dumps(parsed)
    except (ValueError, TypeError, RecursionError) as exc:
        # Non-finite values / recursive YAML must not hide otherwise playable audio.
        status = 'invalid'
        errors.append('Unrepresentable metadata: ' + str(exc))
        safe = {}
        for key,value in parsed.items():
            try:
                dumps({str(key):value})
                safe[str(key)] = value
            except (ValueError, TypeError, RecursionError):
                continue
        parsed = safe
    return sidecar, raw_bytes, raw, parsed, embedded, status, "; ".join(errors) or None


def first(meta, *keys):
    for key in keys:
        value = meta.get(key)
        if value is not None and value != "":
            return value
    return None


def number(value, integer=False):
    try:
        return int(value) if integer else float(value)
    except (TypeError, ValueError):
        return None


def as_text(value):
    return dumps(value) if isinstance(value, (dict, list)) else str(value) if value is not None else None


class Indexer:
    def __init__(self, config, db):
        self.config, self.db = config, db

    def index(self, path, directory_entries=None):
        path = Path(path).resolve()
        before = path.stat()
        sidecar, raw_bytes, raw, meta, embedded, status, error = metadata_for(path, directory_entries)
        yaml_hash = hashlib.sha256(raw_bytes).hexdigest() if raw_bytes is not None else None
        # Embedded values can distinguish provenance even if an external YAML is identical.
        key = hashlib.sha256(dumps({"yaml": yaml_hash, "metadata": meta}).encode()).hexdigest()
        existing = self.db.one("SELECT * FROM media_files WHERE path=? AND storage_state IN ('input','archived') ORDER BY id DESC LIMIT 1", (str(path),))
        if existing and existing["size"] == before.st_size and existing["mtime_ns"] == before.st_mtime_ns:
            audio_id, file_hash = existing["audio_id"], existing["sha256"]
            info = self.db.one("SELECT * FROM audio WHERE id=?", (audio_id,))
            pcm = info["pcm_sha256"]
        else:
            file_hash = digest(path)
            pcm, info = audio_identity(path)
            audio_id = pcm
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise ValueError("File is still being written; will retry on next scan")
        title = str(first(meta, "title", "song", "song_title") or re.sub(r"[-_]\d{4,}$", "", path.stem))
        genre = str(first(meta, "style", "genre", "intended_genre", "cluster") or "Genre unspecified")
        cluster = as_text(meta.get("cluster"))
        song_id = hashlib.sha256(str(first(meta, "song_id", "canonical_song_id") or title.casefold()).encode()).hexdigest()
        generation_id = hashlib.sha256((audio_id + key).encode()).hexdigest()
        timestamp = now()
        with self.db.transaction() as c:
            c.execute("INSERT OR IGNORE INTO songs VALUES(?,?,?,?)", (song_id, title, genre, cluster))
            c.execute("INSERT OR IGNORE INTO audio(id,pcm_sha256,duration,sample_rate,bit_depth,channels,created_at) VALUES(?,?,?,?,?,?,?)",
                      (audio_id, pcm, info["duration"], info["sample_rate"], info["bit_depth"], info["channels"], timestamp))
            # Title is canonical; each generation keeps its intended genre in parsed metadata.
            c.execute("INSERT OR IGNORE INTO generations VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (generation_id, audio_id, song_id, key, as_text(first(meta, "generation_timestamp", "timestamp", "created_at")) or
                       __import__("datetime").datetime.fromtimestamp(before.st_mtime, __import__("datetime").timezone.utc).isoformat(),
                       path.name, str(path.parent), as_text(first(meta, "source", "generation_source")),
                       as_text(first(meta, "workflow", "workflow_id")), timestamp))
            params = [as_text(meta.get(k)) for k in ("model", "dit", "text_encoder")]
            params += [number(meta.get("encoder_cfg")), as_text(meta.get("encoder_seed")), number(meta.get("top_k"), True),
                       as_text(meta.get("sampler")), as_text(meta.get("scheduler")), number(meta.get("sampler_cfg")),
                       as_text(meta.get("sampler_seed")), number(first(meta, "sampler_steps", "steps"), True),
                       number(first(meta, "generation_duration", "generation_seconds", "elapsed_seconds")),
                       as_text(first(meta, "hardware", "gpu", "compute_hardware"))]
            c.execute("""INSERT OR IGNORE INTO generation_metadata(generation_id,yaml_sha256,raw_yaml,raw_yaml_bytes,
              parsed_json,embedded_json,metadata_status,error,model,dit,text_encoder,encoder_cfg,encoder_seed,top_k,
              sampler,scheduler,sampler_cfg,sampler_seed,sampler_steps,generation_duration,hardware)
              VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      [generation_id, yaml_hash, raw, raw_bytes, dumps(meta), dumps(embedded), status, error, *params])
            if existing and existing["sha256"] == file_hash and existing["audio_id"] == audio_id:
                media_id = existing["id"]
            else:
                if existing:
                    c.execute("UPDATE media_files SET storage_state='superseded' WHERE id=?", (existing["id"],))
                media_id = c.execute("""INSERT INTO media_files(audio_id,sha256,path,original_path,size,mtime_ns,storage_state,indexed_at)
                  VALUES(?,?,?,?,?,?,'input',?)""", (audio_id, file_hash, str(path), str(path), before.st_size, before.st_mtime_ns, timestamp)).lastrowid
            if not c.execute("SELECT 1 FROM generation_sources WHERE generation_id=? AND media_id=? AND COALESCE(yaml_path,'')=?",
                             (generation_id, media_id, str(sidecar) if sidecar else "")).fetchone():
                c.execute("INSERT INTO generation_sources(generation_id,media_id,yaml_path,original_yaml_path) VALUES(?,?,?,?)",
                          (generation_id, media_id, str(sidecar) if sidecar else None, str(sidecar) if sidecar else None))
            c.execute("DELETE FROM scan_errors WHERE path=?", (str(path),))
            # Native provenance is joined only by run ID AND the final tagged-file hash.
            # Standalone review databases do not have platform tables.
            run_id = meta.get("gbr_run_id")
            if isinstance(run_id, str) and c.execute("SELECT 1 FROM sqlite_master WHERE name='generation_output_links'").fetchone():
                artifact = c.execute("SELECT id FROM generation_artifacts WHERE run_id=? AND kind='audio' AND sha256=?", (run_id, file_hash)).fetchone()
                if artifact:
                    c.execute("INSERT OR IGNORE INTO generation_output_links VALUES(?,?,?)", (run_id, generation_id, artifact["id"]))
            # A new duplicate of reviewed audio is automatically brought into its existing bucket.
            review = c.execute("SELECT * FROM latest_reviews WHERE audio_id=?", (audio_id,)).fetchone()
            pending = c.execute("SELECT 1 FROM archive_operations WHERE audio_id=? AND status IN ('pending','running','failed')", (audio_id,)).fetchone()
            if review and not pending:
                c.execute("INSERT INTO archive_operations(id,audio_id,generation_id,review_id,bucket,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                          (uuid.uuid4().hex, audio_id, generation_id, review["id"], review["bucket"], timestamp, timestamp))
        return audio_id

    def scan(self):
        seen, indexed, directory_cache = set(), 0, {}
        for root in self.config.inputs:
            if not root.is_dir():
                self.error(str(root), "Input directory unavailable")
                continue
            for path in root.rglob("*"):
                if any(part.startswith('.gbr-') for part in path.relative_to(root).parts):
                    continue
                if path.suffix.lower() != ".flac" or not path.is_file() or path.is_symlink():
                    continue
                resolved = path.resolve()
                if resolved in seen or inside(resolved, self.config.archive) or inside(resolved, self.config.data):
                    continue
                seen.add(resolved)
                try:
                    if resolved.parent not in directory_cache:
                        directory_cache[resolved.parent] = {p.name.lower(): p for p in resolved.parent.iterdir() if p.suffix.lower() in ('.yaml','.yml') and p.is_file()}
                    self.index(resolved, directory_cache[resolved.parent])
                    indexed += 1
                except Exception as exc:
                    self.error(str(resolved), str(exc))
        return indexed

    def error(self, path, error):
        self.db.execute("INSERT INTO scan_errors VALUES(?,?,?) ON CONFLICT(path) DO UPDATE SET error=excluded.error,created_at=excluded.created_at",
                        (path, error, now()))


