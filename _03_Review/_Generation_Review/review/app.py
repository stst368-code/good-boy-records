import csv
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, StrictInt, StrictBool, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .config import Config, password_ok
from .exports import dataset, publishing_manifest, validation
from .service import Service, Conflict
from .util import atomic_text, now, dumps, inside

STATIC = Path(__file__).parent / "static"


class StrictBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewBody(StrictBody):
    audio_quality: StrictInt = Field(ge=0, le=5)
    composition: StrictInt = Field(ge=0, le=5)
    consistency: StrictInt = Field(ge=0, le=5)
    realism: StrictInt = Field(ge=0, le=5)
    mode: Literal["full", "fast", "quick", "standard", "range", "mixed"]
    expected_review_id: str | None = None
    listening: dict = Field(default_factory=dict)
    reassessment: bool = False
    reason: str | None = Field(default=None, max_length=20000)
    distribution: StrictBool | None = None
    bookmarked: StrictBool | None = None
    expected_publishing_status: str | None = None
    submission_id: str | None = Field(default=None, min_length=1, max_length=100)


class FlagsBody(StrictBody):
    distribution: StrictBool | None = None
    bookmarked: StrictBool | None = None
    expected_publishing_status: str | None = None


class OpenBody(StrictBody):
    generation_id: str


class QueueBody(StrictBody):
    order: Literal["random", "alphabetical", "date"] = "random"
    direction: Literal["asc", "desc"] = "asc"
    song: str = ""
    genre: str = ""
    directory: str = ""
    return_skipped: bool = False


class SettingsBody(StrictBody):
    input_directories: list[str]
    archive_directory: str
    quick_seconds: float = Field(ge=5, le=120)
    standard_seconds: float = Field(ge=10, le=300)
    scan_interval_seconds: int = Field(ge=5, le=3600)
    fade_ms: int = Field(ge=0, le=150)
    fast_seconds: float = Field(default=10, ge=5, le=30)
    transition_sensitivity: Literal["sensitive", "balanced", "conservative"] = "balanced"
    reservation_queue_ahead: StrictInt = Field(default=2, ge=0, le=10)
    reservation_seconds: StrictInt = Field(default=180, ge=30, le=3600)


class CacheBody(StrictBody):
    older_than_days: StrictInt = Field(default=7, ge=1, le=3650)
    dry_run: StrictBool = True


class ReviewerBody(StrictBody):
    name: str = Field(max_length=100)


class HeartbeatBody(StrictBody):
    current_id: str | None = None


class PrefetchBody(StrictBody):
    current_id: str
    mode: Literal["fast", "quick", "standard", "full"] = "quick"
    format: Literal["flac", "wav"] = "flac"


class LoginBody(StrictBody):
    password: str = Field(max_length=256)


class PublishingBody(StrictBody):
    status: Literal["not_selected", "selected", "published", "passed", "withdrawn"]
    external_reference: str | None = Field(default=None, max_length=2000)


class ComparisonBody(StrictBody):
    generation_a: str
    generation_b: str


class PreferenceBody(StrictBody):
    preferred: Literal["a", "b", "tie"]


def create_app(config_path, start_worker=True, database_path=None):
    config = Config(config_path)
    if database_path is not None:
        config.values["database_path"] = str(Path(database_path).resolve())
        config.validate()
    service = Service(config)
    secret_path = config.data / "session.secret"
    if not secret_path.exists():
        atomic_text(secret_path, secrets.token_hex(32))
        try:
            secret_path.chmod(0o600)
        except OSError:
            pass
    secret = secret_path.read_text().encode()
    attempts = {}

    @asynccontextmanager
    async def lifespan(app):
        if start_worker:
            service.start()
        yield
        service.close()

    app = FastAPI(title="GBR Review", version="1.3.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    hosts = {"127.0.0.1", "localhost", "::1", socket.gethostname(), *config.values["allowed_hosts"]}
    host_addresses = {'127.0.0.1', '::1', 'testclient'}
    try:
        discovered = socket.gethostbyname_ex(socket.gethostname())[2]
        hosts.update(discovered)
        host_addresses.update(discovered)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("192.0.2.1", 9))
            own_address = sock.getsockname()[0]
            hosts.add(own_address)
            host_addresses.add(own_address)
    except OSError:
        pass
    if config.values["host"] not in ("0.0.0.0", "::"):
        hosts.add(config.values["host"])
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=sorted(hosts))

    def local(request):
        if request.client is None:
            return False
        address = request.client.host
        try:
            ip = ipaddress.ip_address(address)
            if getattr(ip, 'ipv4_mapped', None):
                address = str(ip.ipv4_mapped)
            if ip.is_loopback:
                return True
        except ValueError:
            pass
        return address in host_addresses

    def authenticated(request):
        if config.local_only and not config.values["password_hash"] and local(request):
            return True
        token = request.cookies.get("gbr_session", "")
        if token:
            try:
                expires, signature = token.split(".", 1)
                message = (expires + "|" + config.values["password_hash"]).encode()
                return int(expires) > time.time() and hmac.compare_digest(signature, hmac.new(secret, message, hashlib.sha256).hexdigest())
            except ValueError:
                return False
        return False

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.path.startswith("/api/"):
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin")
                if request.headers.get("x-gbr-request") != "1" or request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlparse(origin).netloc != request.url.netloc):
                    return JSONResponse({"detail": "Same-origin request header required"}, status_code=403)
                if int(request.headers.get("content-length", "0")) > 100000:
                    return JSONResponse({"detail": "Request too large"}, status_code=413)
            if request.url.path not in ("/api/bootstrap", "/api/login") and not authenticated(request):
                return JSONResponse({"detail": "Sign in required"}, status_code=401)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; frame-ancestors 'none'; object-src 'none'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(Conflict)
    async def conflict_handler(request, exc):
        return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=409)

    @app.exception_handler(ValueError)
    async def value_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(FileNotFoundError)
    async def missing_handler(request, exc):
        return JSONResponse({"detail": "Audio or cached file unavailable. Rescan or retry from Settings."}, status_code=404)

    @app.exception_handler(KeyError)
    async def key_handler(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        reviewer = service.queues.identity(request.headers.get("x-gbr-reviewer")) if authenticated(request) else None
        session = service.db.one("SELECT order_by,direction,filters_json FROM sessions WHERE reviewer_id=?", (reviewer["id"],)) if reviewer else None
        directories = [str(p) for p in config.inputs]
        queue_directories = directories + [r['original_directory'] for r in service.db.all('SELECT DISTINCT original_directory FROM generations ORDER BY original_directory')
                                           if r['original_directory'] not in directories and any(inside(r['original_directory'], p) for p in config.inputs)] if authenticated(request) else []
        return {"authenticated": authenticated(request), "can_configure": local(request), "queue": session,
                "reviewer": reviewer, "reviewers": service.queues.names() if authenticated(request) else [],
                "heartbeat_seconds": min(10, config.values["reservation_seconds"] // 3),
                "input_directories": [str(p) for p in config.inputs] if authenticated(request) else [],
                "queue_directories": queue_directories,
                "durations": {m: config.values[m + "_seconds"] for m in ("fast", "quick", "standard")}}

    def reviewer_id(request):
        reviewer = service.queues.identity(request.headers.get("x-gbr-reviewer"))
        if not reviewer:
            raise HTTPException(428, "Choose your reviewer name before continuing")
        return reviewer["id"]

    @app.post("/api/reviewer/select")
    def select_reviewer(body: ReviewerBody, request: Request):
        return {"reviewer": service.queues.select(body.name, request.headers.get("x-gbr-reviewer"))}

    @app.post("/api/reviewer/heartbeat")
    def heartbeat(body: HeartbeatBody, request: Request):
        return service.queues.heartbeat(reviewer_id(request), body.current_id)

    @app.post("/api/login")
    def login(body: LoginBody, request: Request):
        ip = request.client.host
        history = [x for x in attempts.get(ip, []) if x > time.monotonic() - 300]
        if len(history) >= 10:
            raise HTTPException(429, "Too many attempts; wait five minutes")
        history.append(time.monotonic())
        attempts[ip] = history
        if not config.values["password_hash"] or not password_ok(body.password, config.values["password_hash"]):
            raise HTTPException(401, "Incorrect password")
        attempts.pop(ip, None)
        expiry = str(int(time.time()) + 7 * 86400)
        signature = hmac.new(secret, (expiry + "|" + config.values["password_hash"]).encode(), hashlib.sha256).hexdigest()
        response = JSONResponse({"authenticated": True})
        response.set_cookie("gbr_session", expiry + "." + signature, max_age=7 * 86400, httponly=True, secure=request.url.scheme == "https", samesite="strict")
        return response

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"signed_out": True})
        response.delete_cookie("gbr_session")
        return response

    @app.get("/api/queue/next")
    def next_track(request: Request, include_preview: bool = False):
        result = service.next(reviewer_id=reviewer_id(request))
        if include_preview:
            result["preview"] = track_preview(result["track"]["id"]) if result["track"] else None
        return result

    @app.post("/api/queue/prefetch")
    def prefetch(body: PrefetchBody, request: Request):
        result = service.next(peek=True, reviewer_id=reviewer_id(request), expected_current=body.current_id)
        if not result["track"]:
            return {"prepared": False}
        gid = result["track"]["id"]
        try:
            service.playback.render(gid, body.mode, body.format)
        except (ValueError, FileNotFoundError, KeyError):
            return {"prepared": False, "track_id": gid}
        return {"prepared": True, "track_id": gid}

    @app.post("/api/queue")
    def set_queue(body: QueueBody, request: Request):
        if body.directory and not any(inside(body.directory, p) for p in config.inputs):
            raise ValueError("Directory must be within a configured input directory")
        service.queue_settings(body.order, body.direction, {"song": body.song, "genre": body.genre, "directory": body.directory}, body.return_skipped, reviewer_id=reviewer_id(request))
        return {"saved": True}

    @app.get("/api/queue/back")
    def previous_track(request: Request):
        rid = reviewer_id(request)
        session = service.db.one("SELECT previous_generation_id FROM sessions WHERE reviewer_id=?", (rid,))
        return {"track": service.public_track(session["previous_generation_id"], correction=True, reviewer_id=rid) if session and session["previous_generation_id"] else None}

    @app.get("/api/tracks/{gid}")
    def track(gid: str, request: Request, reassess: bool = False):
        result = service.public_track(gid, correction=reassess, reviewer_id=reviewer_id(request))
        if not result:
            raise HTTPException(404, "Recording not found")
        if reassess:
            result["reassessment"] = True
        return result

    @app.post("/api/tracks/{gid}/review")
    def review(gid: str, body: ReviewBody, request: Request):
        return service.review(gid, {k: getattr(body, k) for k in ("audio_quality", "composition", "consistency", "realism")}, body.mode, body.expected_review_id, body.listening, body.reassessment, reviewer_id=reviewer_id(request), reason=body.reason, distribution=body.distribution, bookmarked=body.bookmarked, expected_publishing_status=body.expected_publishing_status, submission_id=body.submission_id)

    @app.get('/api/tracks/{gid}/flags')
    def flags(gid: str, request: Request):
        return service.track_flags(gid, reviewer_id(request))

    @app.post('/api/tracks/{gid}/flags')
    def save_flags(gid: str, body: FlagsBody, request: Request):
        return service.set_flags(gid, reviewer_id(request), body.distribution, body.bookmarked, body.expected_publishing_status)

    @app.post('/api/queue/open')
    def open_track(body: OpenBody, request: Request):
        return service.queues.open(reviewer_id(request), body.generation_id)

    @app.get('/api/tracks/{gid}/reviews')
    def review_details(gid: str, request: Request):
        reviewer_id(request)
        g = service.db.one('SELECT audio_id FROM generations WHERE id=?', (gid,))
        if not g:
            raise KeyError('Recording not found')
        return {'reviews': service.db.all('SELECT * FROM review_dataset WHERE audio_id=? ORDER BY revision DESC', (g['audio_id'],))}

    @app.post("/api/tracks/{gid}/skip")
    def skip(gid: str, request: Request):
        service.skip(gid, reviewer_id=reviewer_id(request))
        return {"skipped": True}

    def track_preview(gid):
        g = service.db.one("SELECT audio_id FROM generations WHERE id=?", (gid,))
        if not g:
            raise KeyError("Recording not found")
        analysis = service.db.one("SELECT * FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (g["audio_id"],))
        if not analysis:
            return {"ready": False}
        if not Path(analysis["preview_path"]).is_file() or not Path(analysis["spectrogram_path"]).is_file():
            service.db.execute("UPDATE audio SET analysis_state='pending' WHERE id=?", (g["audio_id"],))
            return {"ready": False}
        data = json.loads(Path(analysis["preview_path"]).read_text())
        markers = service.db.all("SELECT id,start,end FROM audio_events WHERE analysis_id=? ORDER BY start", (analysis["id"],))
        # No type, severity, confidence or measurements are exposed during scoring.
        segments = service.db.all("SELECT id,mode,ordinal,start,end FROM segments WHERE analysis_id=? ORDER BY mode,ordinal", (analysis["id"],))
        points = service.db.all("SELECT id,start,end FROM audio_events WHERE analysis_id=? AND type='structural_transition' ORDER BY start", (analysis["id"],))
        return {"ready": True, "duration": data["duration"], "waveform": data["waveform"], "channels": data["channels"],
                "markers": markers, "points": points, "segments": segments, "analysis_id": analysis["id"], "frequency_axis": data["frequency_axis"]}

    @app.get("/api/tracks/{gid}/preview")
    def preview(gid: str):
        return track_preview(gid)

    @app.get("/api/tracks/{gid}/analysis-state")
    def analysis_state(gid: str):
        g = service.db.one("SELECT a.id,a.analysis_state FROM generations g JOIN audio a ON a.id=g.audio_id WHERE g.id=?", (gid,))
        if not g:
            raise KeyError("Recording not found")
        latest = service.db.one("SELECT id FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (g["id"],))
        return {"state": g["analysis_state"], "analysis_id": latest["id"] if latest else None}

    @app.get("/api/tracks/{gid}/triage")
    def triage(gid: str):
        from .triage import technical_triage
        g = service.db.one("SELECT audio_id FROM generations WHERE id=?", (gid,))
        if not g:
            raise KeyError("Recording not found")
        latest = service.db.one("SELECT id,metrics_json FROM analyses WHERE audio_id=? ORDER BY created_at DESC,rowid DESC LIMIT 1", (g["audio_id"],))
        if not latest:
            return {"analysis_id": None, **technical_triage({})}
        events = service.db.all("SELECT type,start,end FROM audio_events WHERE analysis_id=? ORDER BY start", (latest["id"],))
        return {"analysis_id": latest["id"], **technical_triage(json.loads(latest["metrics_json"]), events)}

    @app.get("/api/tracks/{gid}/spectrogram")
    def spectrogram(gid: str):
        row = service.db.one("SELECT an.spectrogram_path FROM generations g JOIN analyses an ON an.audio_id=g.audio_id WHERE g.id=? ORDER BY an.created_at DESC,an.rowid DESC LIMIT 1", (gid,))
        if not row:
            raise KeyError("Spectrogram still being prepared")
        return FileResponse(row["spectrogram_path"], media_type="image/png")

    @app.get("/api/playback/{gid}")
    def audio(gid: str, mode: Literal["full", "fast", "quick", "standard", "range"] = "full", format: Literal["flac", "wav"] = "flac",
              start: float | None = None, end: float | None = None, segment_id: str | None = None, analysis_id: str | None = None):
        path = service.playback.render(gid, mode, format, start, end, segment_id, analysis_id=analysis_id)
        return service.playback.response(path, media_type="audio/flac" if format == "flac" else "audio/wav")

    @app.get("/api/publishing")
    def candidates(request: Request, bucket: str = "5", status: str = "", song: str = "", limit: int = 100, offset: int = 0, bookmarked: bool = False):
        rid = reviewer_id(request)
        bucket = None if bucket in ("", "all") else int(bucket)
        if bucket is not None and not 0 <= bucket <= 5:
            raise ValueError("Invalid bucket")
        limit, offset = max(1, min(limit, 300)), max(offset, 0)
        where = "(review_id IS NOT NULL OR publishing_status='selected' OR EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=analytics_dataset.generation_id AND b.reviewer_id=?))"
        args = [rid]
        if bookmarked:
            where += ' AND EXISTS(SELECT 1 FROM bookmarks b WHERE b.generation_id=analytics_dataset.generation_id AND b.reviewer_id=?)'
            args.append(rid)
        for key, value in [("bucket", bucket), ("publishing_status", status or None)]:
            if value is not None:
                where += " AND " + key + "=?"
                args.append(value)
        if song:
            where += " AND title LIKE ?"
            args.append("%" + song + "%")
        # Selection view contains statuses and archive categories, not model details.
        items = service.db.all("SELECT generation_id,title,genre,song_id,publishing_status,external_reference,bucket,reviewer_id,review_id FROM analytics_dataset WHERE " + where + " ORDER BY title,generation_id LIMIT ? OFFSET ?", (*args, limit, offset))
        for item in items:
            own = service.db.one('SELECT r.id FROM latest_reviewer_reviews r JOIN generations g ON g.audio_id=r.audio_id WHERE g.id=? AND r.reviewer_id=?', (item['generation_id'], rid))
            item["can_reassess"] = bool(own)
            item.pop('reviewer_id')
            item['can_review'] = not item['review_id']
            item['flags'] = service.track_flags(item['generation_id'], rid)
        return {"items": items, "offset": offset, "has_more": len(items) == limit}

    @app.post("/api/publishing/{gid}")
    def publish_status(gid: str, body: PublishingBody):
        service.publishing(gid, body.status, body.external_reference)
        return {"saved": True}

    @app.get("/api/publishing-manifest")
    def manifest():
        return publishing_manifest(service.db)

    @app.post("/api/comparisons")
    def compare(body: ComparisonBody):
        return service.comparison(body.generation_a, body.generation_b)

    @app.get("/api/comparisons/{cid}/{slot}/audio")
    def compare_audio(cid: str, slot: Literal["a", "b"], format: Literal["flac", "wav"] = "flac"):
        c = service.comparisons.get(cid)
        if not c:
            raise KeyError("Comparison expired; start again")
        return service.playback.response(service.playback.render(c[slot], "full", format, match_lufs=c["target"]), media_type="audio/" + format)

    @app.post("/api/comparisons/{cid}/preference")
    def preference(cid: str, body: PreferenceBody):
        c = service.comparisons.get(cid)
        if not c:
            raise KeyError("Comparison expired")
        preferred = c[body.preferred] if body.preferred != "tie" else None
        service.db.execute("INSERT INTO preferences VALUES(?,?,?,?,?,?)", (uuid.uuid4().hex, c["a"], c["b"], preferred, c["target"], now()))
        return {"saved": True, "preferred_generation_id": preferred}

    def require_host(request):
        if not local(request):
            raise HTTPException(403, "Open Settings on the host computer to change directories")

    @app.get("/api/settings")
    def settings(request: Request):
        require_host(request)
        keys = ("input_directories", "archive_directory", "fast_seconds", "quick_seconds", "standard_seconds", "transition_sensitivity", "fade_ms", "scan_interval_seconds", "reservation_queue_ahead", "reservation_seconds")
        return {**{k: config.values[k] for k in keys}, "data_directory": str(config.data), "archive_resolved": str(config.archive),
                'archive_locked': bool(service.db.one('SELECT 1 FROM audio WHERE archive_path IS NOT NULL LIMIT 1'))}

    @app.get('/api/directories')
    def directories(request: Request, path: str | None = None):
        require_host(request)
        from .directories import listing
        return listing(config, path)

    @app.get('/api/playback-cache')
    def cache_report(request: Request):
        require_host(request)
        return service.playback.cache_report()

    @app.post('/api/playback-cache/cleanup')
    def cache_cleanup(body: CacheBody, request: Request):
        require_host(request)
        return service.playback.cleanup(body.older_than_days, body.dry_run)

    @app.post("/api/settings")
    def save_settings(body: SettingsBody, request: Request):
        require_host(request)
        with service.config_lock:
            proposed = {**config.values, **body.model_dump()}
            archive = config.resolve(proposed["archive_directory"])
            if inside(archive, config.data) or inside(config.data, archive):
                raise ValueError("Archive and application data must be separate directories")
            if any(archive == config.resolve(p) for p in body.input_directories):
                raise ValueError("Archive must differ from every input directory")
            if archive != config.archive and service.db.one("SELECT id FROM audio WHERE archive_path IS NOT NULL LIMIT 1"):
                raise ValueError("Archive already contains reviewed audio. Keep this location; see the migration instructions.")
            previous = config.values
            try:
                config.values = proposed
                config.validate()
                config.save()
            except Exception:
                config.values = previous
                raise
            if any(previous[k] != config.values[k] for k in ("fast_seconds", "quick_seconds", "standard_seconds", "transition_sensitivity")):
                service.analysis_generation += 1
                service.db.execute("UPDATE audio SET analysis_state='retune_pending' WHERE analysis_state='ready'")
        service.scan_event.set()
        return {"saved": True}

    @app.get("/api/mobile")
    def mobile_info(request: Request):
        require_host(request)
        from .mobile import connection_info
        return connection_info(config)

    @app.get("/api/status")
    def status():
        return {"worker": service.worker_state, "scan_errors": service.db.one("SELECT COUNT(*) AS n FROM scan_errors")["n"],
                "archive_failures": service.db.one("SELECT COUNT(*) AS n FROM archive_operations WHERE status='failed'")["n"],
                "analysis_pending": service.db.one("SELECT COUNT(*) AS n FROM audio WHERE analysis_state IN ('pending','analysing','retune_pending','retuning')")["n"]}

    @app.get("/api/diagnostics")
    def diagnostics(request: Request):
        require_host(request)
        return {"scan_errors": service.db.all("SELECT * FROM scan_errors ORDER BY created_at DESC LIMIT 200"),
                "archive_errors": service.db.all("SELECT id,audio_id,error,attempts FROM archive_operations WHERE status='failed'")}

    @app.post("/api/scan")
    def scan():
        service.scan_event.set()
        return {"scheduled": True}

    @app.post("/api/archive/retry")
    def retry():
        service.archiver.retry()
        return {"scheduled": True}

    @app.post("/api/reconcile")
    def reconcile(request: Request):
        require_host(request)
        with service.config_lock:
            service.archiver.reconcile()
        return {"scheduled": True}

    @app.post("/api/reanalyse")
    def reanalyse():
        service.db.execute("UPDATE audio SET analysis_state='pending'")
        return {"scheduled": True}

    @app.post("/api/retune")
    def retune(request: Request):
        require_host(request)
        service.analysis_generation += 1
        service.db.execute("UPDATE audio SET analysis_state='retune_pending' WHERE analysis_state NOT IN ('analysing','retuning')")
        return {"scheduled": True}

    @app.post("/api/backup")
    def backup():
        path = config.data / "backups" / ("gbr-review-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6] + ".sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        service.db.backup(path)
        return FileResponse(path, filename=path.name, media_type="application/vnd.sqlite3")

    @app.get("/api/export/{format}")
    def export(format: Literal["csv", "json", "parquet"], kind: str = "generations"):
        path = config.data / "exports" / ("gbr-review-" + uuid.uuid4().hex + "." + format)
        dataset(service.db, path, format, kind)
        return FileResponse(path, filename="gbr-review." + format, media_type="application/octet-stream")

    @app.get("/api/validation")
    def validate():
        return validation(service.db, service.archiver)

    @app.get("/")
    def home():
        return FileResponse(STATIC / "index.html")

    @app.get("/sw.js")
    def service_worker():
        return FileResponse(STATIC / "sw.js", media_type="application/javascript", headers={"Service-Worker-Allowed": "/", "Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


