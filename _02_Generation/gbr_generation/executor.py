"""Execute frozen jobs, with explicit attempts and atomic output bundles."""
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from .planner import json_text, open_database, sha, verify_plan, write_completed_yaml, write_new
from .yaml_document import YamlDocument

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "_99_Tools" / "_workers_infdit"
sys.path.insert(0, str(WORKER))
sys.path.insert(0, str(ROOT / "_98_Database"))
from gbr_worker.contract import PROTOCOL, execution_target, file_sha, validate_job
from gbr_worker.runpod import RemotePending, RunpodClient


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def default_python():
    return WORKER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def upgrade_database(conn):
    from gbr_database.migration import install_schema
    version = conn.execute("SELECT MAX(version) FROM platform_schema_version").fetchone()[0]
    if version > 2:
        raise ValueError("This controller does not support the newer database schema")
    if version < 2:
        install_schema(conn)


def make_job(run, base, attempt_id, directory, revision=None):
    document = YamlDocument((base / run["snapshot"]).read_bytes())
    job = {"protocol": PROTOCOL, "run_id": run["id"], "attempt_id": attempt_id,
           "parameters": run["effective"], "execution": run["execution"],
           "model_revision": revision or run.get("model_revision"), "lyrics": document.values.get("lyrics", ""),
           "source_sha256": run["source_sha256"], "result_directory": str(directory),
           "cache_root": str(WORKER / "runtime" / "encodings")}
    execution_target(job["execution"])
    return validate_job(job, require_local=False)


def reserve_output(conn, run, output_root):
    row = conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run["id"],)).fetchone()
    if row:
        if Path(row["output_directory"]).parent != output_root:
            raise ValueError("This run already reserved a different output directory")
        return dict(row)
    prefix = re.sub(r"[^a-z0-9]+", "-", (run["title"] + "-" + run["effective"].get("style", "music")).casefold()).strip("-") or "track"
    prior = 0
    # Continue known historic output numbers when a matching filename prefix exists.
    if conn.execute("SELECT 1 FROM sqlite_master WHERE name='legacy_generation_jobs'").fetchone():
        pattern = re.compile(re.escape(prefix) + r"-(\d+)$", re.IGNORECASE)
        for old in conn.execute("SELECT output_stem FROM legacy_generation_jobs WHERE output_stem IS NOT NULL"):
            match = pattern.fullmatch(old[0])
            if match:
                prior = max(prior, int(match[1]))
    conn.execute("INSERT OR IGNORE INTO generation_output_counters VALUES(?,?)", (prefix, prior))
    conn.execute("UPDATE generation_output_counters SET last_value=MAX(last_value,?)+1 WHERE title_key=?", (prior, prefix))
    number = conn.execute("SELECT last_value FROM generation_output_counters WHERE title_key=?", (prefix,)).fetchone()[0]
    stem = f"{prefix}-{number:04d}"
    directory = output_root / run["id"]
    conn.execute("INSERT INTO generation_execution_state VALUES(?,?,?,?)", (run["id"], run.get("model_revision"), stem, str(directory)))
    return {"run_id": run["id"], "model_revision": run.get("model_revision"), "output_stem": stem, "output_directory": str(directory)}


def read_events(directory):
    events = []
    path = directory / "events.ndjson"
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                # A process killed while writing its last event keeps all complete earlier events.
                continue
    return events


def store_observations(conn, attempt_id, run_id, events):
    parent = attempt_id + "-total"
    hardware_event = next((e for e in events if e.get("event") == "worker"), None)
    if hardware_event:
        hardware = hardware_event["hardware"]
        session = attempt_id + "-worker"
        conn.execute("INSERT OR IGNORE INTO worker_sessions VALUES(?,?,?,?,?,?,?,?,?,?)", (
            session, hardware["address"], hardware.get("provider"), hardware.get("provider_instance_id"),
            hardware_event["at"], hardware.get("gpu_uuid"), hardware.get("gpu_name"), hardware.get("usable_vram_bytes"),
            json_text(hardware), json_text(hardware_event["versions"])))
        conn.execute("UPDATE generation_attempts SET worker_session_id=? WHERE id=?", (session, attempt_id))
    resolved = next((e["models"] for e in events if e.get("event") == "model_resolved"), None)
    if resolved:
        frozen = conn.execute("SELECT model_revision FROM generation_execution_state WHERE run_id=?", (run_id,)).fetchone()[0]
        if frozen and frozen != resolved["revision"]:
            raise ValueError("Worker used a different model revision from the reserved run")
        conn.execute("UPDATE generation_execution_state SET model_revision=? WHERE run_id=?", (resolved["revision"], run_id))
        conn.execute("UPDATE generation_attempts SET actual_models_json=? WHERE id=?", (json_text(resolved), attempt_id))
    ended = {e["id"] for e in events if e.get("event") == "stage_end"}
    child_parents = {e.get("parent_id") for e in events if e.get("event") == "stage_start"}
    # Parent rows must exist before their children even though end events arrive inside-out.
    for event in events:
        if event.get("event") == "stage_start":
            conn.execute("INSERT OR IGNORE INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                event["id"], attempt_id, event.get("parent_id") or parent, event["stage"], event["started_at"], None, None,
                None, None, None, int(event["id"] in child_parents)))
    for event in events:
        if event.get("event") == "stage_start" and event["id"] not in ended:
            conn.execute("INSERT OR IGNORE INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                event["id"], attempt_id, event.get("parent_id") or parent, event["stage"], event["started_at"], None, None,
                None, None, None, 0))
        if event.get("event") != "stage_end":
            continue
        conn.execute("INSERT INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET completed_at=excluded.completed_at,duration_seconds=excluded.duration_seconds,includes_children=excluded.includes_children", (
            event["id"], attempt_id, event.get("parent_id") or parent, event["stage"], event["started_at"], event["completed_at"],
            event["duration_seconds"], None, None, None, int(event.get("includes_children", False))))
        resource = event["resource"]
        conn.execute("INSERT OR IGNORE INTO generation_resource_measurements VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            attempt_id, event["stage"], resource["device_id"], resource.get("baseline_used_bytes"),
            resource.get("peak_allocated_bytes"), resource.get("peak_reserved_bytes"), resource.get("peak_observed_device_bytes"),
            resource["sampling_interval_ms"], resource["coverage"], json_text(resource["offload"]), json_text(resource["details"])))


def validate_bundle(directory, run, state):
    marker = json.loads((directory / "generation.json").read_text(encoding="utf-8"))
    if marker["run_id"] != run["id"] or marker["source_sha256"] != run["source_sha256"]:
        raise ValueError("Existing output bundle does not belong to this frozen run")
    expected = {state["output_stem"] + ".flac", state["output_stem"] + ".yaml", "source.yaml"}
    if set(marker["files"]) != expected:
        raise ValueError("Output bundle file list is invalid")
    for filename, digest in marker["files"].items():
        if file_sha(directory / filename) != digest:
            raise ValueError("Output bundle changed: " + filename)
    if marker["files"]["source.yaml"] != run["source_sha256"]:
        raise ValueError("Output source copy changed")
    return marker


def publish_bundle(directory, run, state, result, source, attempt_id, timezone_name):
    import soundfile as sf
    from mutagen.flac import FLAC
    final = Path(state["output_directory"])
    if final.exists():
        return validate_bundle(final, run, state)
    if result.get("actual_parameters") != run["effective"]:
        raise ValueError("Worker did not report the exact requested generation controls")
    if result.get("models", {}).get("revision") != state["model_revision"]:
        raise ValueError("Worker model revision mismatch")
    audio = directory / "audio.flac"
    info = sf.info(str(audio))
    if info.format != "FLAC" or info.subtype != "PCM_24" or info.channels != 2 or info.samplerate != 44100 or info.frames <= 0:
        raise ValueError("Worker audio is not a nonempty 44.1 kHz stereo PCM_24 FLAC")
    with sf.SoundFile(str(audio)) as handle:
        import numpy as np
        for block in handle.blocks(blocksize=65536, dtype="float32"):
            if not np.isfinite(block).all():
                raise ValueError("Worker FLAC failed decode verification")
    bundle = directory / "bundle"
    bundle.mkdir()
    stem = state["output_stem"]
    completed_at = datetime.fromisoformat(result["completed_at"])
    write_completed_yaml(source, result["actual_parameters"], bundle / (stem + ".yaml"), succeeded=True,
                         completed_at=completed_at, timezone_name=timezone_name)
    write_new(bundle / "source.yaml", source)
    shutil.copyfile(audio, bundle / (stem + ".flac"))
    metadata = {**run["effective"], "song_id": run["song_id"], "variant_id": run["variant_id"], "title": run["title"],
                "gbr_run_id": run["id"], "gbr_attempt_id": attempt_id, "gbr_source_sha256": run["source_sha256"],
                "generation_timestamp": result["completed_at"],
                "generation_source": "gbr_native_" + execution_target(run["execution"])[0],
                "genre": run["effective"].get("style"),
                "model_revision": result["models"]["revision"], "hardware": result.get("hardware"),
                "audio_duration_seconds": info.duration}
    tags = FLAC(str(bundle / (stem + ".flac")))
    tags["GBR_METADATA"] = json_text(metadata)
    tags["GBR_YAML"] = (bundle / (stem + ".yaml")).read_bytes().decode("utf-8-sig")
    tags.save()
    files = {p.name: file_sha(p) for p in bundle.iterdir()}
    marker = {"format_version": 1, "run_id": run["id"], "attempt_id": attempt_id,
              "source_sha256": run["source_sha256"], "completed_at": result["completed_at"],
              "files": files, "worker_result": result, "yaml_timezone": timezone_name}
    write_new(bundle / "generation.json", json_text(marker).encode("utf-8"))
    final.parent.mkdir(parents=True, exist_ok=True)
    # Bundle creation and destination share a filesystem (both below the batch workspace).
    # Windows/POSIX rename refuses replacement of a nonempty published bundle.
    bundle.rename(final)
    return validate_bundle(final, run, state)


def record_output(conn, run, state, marker):
    directory = Path(state["output_directory"])
    for filename, digest in {**marker["files"], "generation.json": file_sha(directory / "generation.json")}.items():
        kind = "audio" if filename.endswith(".flac") else "source_yaml" if filename == "source.yaml" else "completed_yaml" if filename.endswith(".yaml") else "generation_report"
        conn.execute("INSERT OR IGNORE INTO generation_artifacts VALUES(?,?,?,?,?,?,?,?)", (
            sha((run["id"] + "/" + filename).encode()), run["id"], kind, digest, str(directory / filename),
            (directory / filename).stat().st_size, marker["worker_result"].get("encoding", {}).get("key"),
            json_text({"attempt_id": marker["attempt_id"], "encoding": marker["worker_result"].get("encoding")})))
    conn.execute("UPDATE generation_runs SET status='succeeded',completed_at=? WHERE id=?", (marker["completed_at"], run["id"]))


def recover(manifest_path, run_id, *, remote_stopped=False):
    """Explicitly recover after the operator has stopped the old controller/worker."""
    manifest = verify_plan(manifest_path)
    run = next((r for r in manifest["runs"] if r["id"] == run_id), None)
    if run is None:
        raise ValueError("Run is not in this batch")
    with closing(open_database(manifest["database"])) as conn, conn:
        upgrade_database(conn)
        conn.execute("BEGIN IMMEDIATE")
        state = conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run_id,)).fetchone()
        active = conn.execute("SELECT * FROM generation_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchall()
        if any(a["execution_mode"] == "runpod_dedicated" for a in active) and not remote_stopped:
            raise ValueError("Remote work may still be running. Use execute --resume-remote to collect it, or recover --remote-stopped only after stopping the pod worker")
        for attempt in active:
            work = Path(manifest_path).resolve().parent / "attempts" / attempt["id"]
            store_observations(conn, attempt["id"], run_id, read_events(work))
            conn.execute("UPDATE generation_attempts SET status='interrupted',completed_at=?,error=? WHERE id=?", (now(), "Explicit interruption recovery", attempt["id"]))
        if state and Path(state["output_directory"]).exists():
            marker = validate_bundle(Path(state["output_directory"]), run, state)
            record_output(conn, run, state, marker)
            conn.execute("UPDATE generation_attempts SET status='succeeded',error=NULL WHERE id=?", (marker["attempt_id"],))
            return {"run_id": run_id, "status": "succeeded", "output": state["output_directory"]}
        if conn.execute("SELECT status FROM generation_runs WHERE id=?", (run_id,)).fetchone()[0] != "running":
            raise ValueError("Only an interrupted running job requires recovery")
        conn.execute("UPDATE generation_runs SET status='failed' WHERE id=?", (run_id,))
        return {"run_id": run_id, "status": "failed", "retry": "execute --retry-failed"}


def execute(manifest_path, *, worker_python=None, limit=1, retry_failed=False, timezone_name="Europe/London",
            launcher=None, runpod_identity=None, resume_remote=False):
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone_name)
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = verify_plan(manifest_path)
    # Resolving a venv's interpreter symlink would select its base environment on POSIX.
    python = Path(worker_python or default_python()).expanduser().absolute()
    # Validate every selected job before claiming any or starting inference.
    for run in manifest["runs"]:
        make_job(run, manifest_path.parent, "preflight", manifest_path.parent)
        backend, address = execution_target(run["execution"])
        if backend == "local" and not python.is_file():
            raise FileNotFoundError("Worker Python is missing; install the local worker: " + str(python))
        if backend == "runpod_dedicated":
            RunpodClient(address, runpod_identity)
    attempts_root = manifest_path.parent / "attempts"
    output_root = manifest_path.parent / "outputs"
    results = []
    with closing(open_database(manifest["database"])) as conn:
        upgrade_database(conn)
        for run in manifest["runs"]:
            if len(results) >= limit:
                break
            conn.execute("BEGIN IMMEDIATE")
            status = conn.execute("SELECT status FROM generation_runs WHERE id=?", (run["id"],)).fetchone()[0]
            backend, address = execution_target(run["execution"])
            if status == "succeeded" or status == "failed" and not retry_failed:
                conn.rollback()
                continue
            resuming = status == "running" and resume_remote and backend == "runpod_dedicated"
            if resume_remote and not resuming:
                conn.rollback()
                continue
            if status not in {"prepared", "failed"} and not resuming:
                conn.rollback()
                raise ValueError(f"Run {run['id']} is {status}; use --resume-remote for RunPod or recover for an interrupted local job")
            state = reserve_output(conn, run, output_root)
            if Path(state["output_directory"]).exists():
                conn.rollback()
                raise ValueError("A published bundle exists for this run; use recover to reconcile it without generating again")
            if resuming:
                active = conn.execute("SELECT * FROM generation_attempts WHERE run_id=? AND status='running'", (run["id"],)).fetchall()
                if len(active) != 1:
                    raise ValueError("Expected exactly one active remote attempt")
                attempt_id, started = active[0]["id"], active[0]["started_at"]
                directory = attempts_root / attempt_id
                job = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                expected = make_job(run, manifest_path.parent, attempt_id, directory, job.get("model_revision"))
                if job != expected:
                    raise ValueError("Saved remote request changed")
            else:
                attempt_id, started = uuid.uuid4().hex, now()
                directory = attempts_root / attempt_id
                directory.mkdir(parents=True)
                job = make_job(run, manifest_path.parent, attempt_id, directory, state["model_revision"])
                write_new(directory / "request.json", json_text(job).encode("utf-8"))
                conn.execute("INSERT INTO generation_attempts VALUES(?,?,?,?,?,?,?,?,?,?)", (
                    attempt_id, run["id"], None, "full_generation", "running", backend, "{}", started, None, None))
                conn.execute("INSERT INTO generation_timing_spans VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
                    attempt_id + "-total", attempt_id, None, "total", started, None, None, None, None, None, 1))
                conn.execute("UPDATE generation_runs SET status='running',started_at=? WHERE id=?", (started, run["id"]))
            conn.commit()
            clock, error, marker, pending = time.perf_counter(), None, None, False
            try:
                if launcher:
                    launcher(python, directory / "request.json", directory)
                elif backend == "runpod_dedicated":
                    reference = json.loads((directory / "remote.json").read_text()) if resuming else {}
                    client = RunpodClient(address, runpod_identity, release=reference.get("release"))
                    client.run(job, directory, resume=resuming)
                else:
                    with (directory / "stderr.log").open("x", encoding="utf-8") as log:
                        process = subprocess.Popen([str(python), "-u", str(WORKER / "worker.py"), "run", str(directory / "request.json")],
                                                   stdout=log, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
                        try:
                            for line in process.stderr:
                                log.write(line)
                                log.flush()
                                print(line, end="", file=sys.stderr, flush=True)
                            return_code = process.wait()
                        except BaseException:
                            process.terminate()
                            try:
                                process.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise
                        finally:
                            process.stderr.close()
                        if return_code:
                            detail = ""
                            try:
                                failed = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                                if failed.get("run_id") == run["id"] and failed.get("attempt_id") == attempt_id:
                                    detail = str(failed.get("error", ""))
                            except (OSError, ValueError, AttributeError):
                                pass
                            raise RuntimeError(f"Worker exited {return_code}: {detail}; see {directory / 'stderr.log'}")
                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                if result.get("status") != "succeeded" or result.get("run_id") != run["id"] or result.get("attempt_id") != attempt_id or result.get("protocol") != PROTOCOL:
                    raise ValueError(result.get("error", "Invalid/failed worker result"))
                with conn:
                    store_observations(conn, attempt_id, run["id"], read_events(directory))
                state = dict(conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run["id"],)).fetchone())
                marker = publish_bundle(directory, run, state, result, (manifest_path.parent / run["snapshot"]).read_bytes(), attempt_id, timezone_name)
                with conn:
                    record_output(conn, run, state, marker)
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"
                marker = None
                pending = backend == "runpod_dedicated" and isinstance(exc, (RemotePending, KeyboardInterrupt))
                if isinstance(exc, KeyboardInterrupt):
                    print("Controller interrupted; reconnect with --resume-remote. The pod continues working." if pending
                          else "Local test interrupted; saving available telemetry.", file=sys.stderr)
            finally:
                ended = now()
                with conn:
                    store_observations(conn, attempt_id, run["id"], read_events(directory))
                    duration = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() if resuming else time.perf_counter() - clock
                    conn.execute("UPDATE generation_timing_spans SET completed_at=?,duration_seconds=? WHERE id=?",
                                 (None if pending else ended, None if pending else duration, attempt_id + "-total"))
                    conn.execute("UPDATE generation_attempts SET status=?,completed_at=?,error=? WHERE id=?", (
                        "running" if pending else "succeeded" if marker else "failed", None if pending else ended, error, attempt_id))
                    if not marker and not pending:
                        conn.execute("UPDATE generation_runs SET status='failed' WHERE id=?", (run["id"],))
                results.append({"run_id": run["id"], "attempt_id": attempt_id, "status": "running" if pending else "succeeded" if marker else "failed",
                                "output": state["output_directory"] if marker else None, "error": error,
                                "diagnostics": str(directory), **({"resume": "execute --resume-remote"} if pending else {})})
            if error:
                break
        with conn:
            statuses = [r[0] for r in conn.execute("SELECT status FROM generation_runs WHERE batch_id=?", (manifest["batch_id"],))]
            batch_status = "succeeded" if all(s == "succeeded" for s in statuses) else "failed" if "failed" in statuses else "running" if "running" in statuses else "prepared"
            conn.execute("UPDATE generation_batches SET status=? WHERE id=?", (batch_status, manifest["batch_id"]))
    return {"batch_id": manifest["batch_id"], "status": batch_status, "attempts": results}

