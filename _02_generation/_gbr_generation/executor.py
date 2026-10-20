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
import threading
import time
import uuid

from .planner import json_text, open_database, sha, verify_plan, write_completed_yaml, write_new
from .yaml_document import YamlDocument
from .cancellation import discard as discard_cancelled,verify as verify_cancelled
from _gbr_layout import request_paths

ROOT = Path(__file__).resolve().parents[2]
WORKER = ROOT / "_99_tools" / "_workers_infdit"
sys.path.insert(0, str(WORKER))
sys.path.insert(0, str(ROOT / "_98_database"))
from _gbr_worker.contract import PROTOCOL, execution_target, file_sha, validate_job
from _gbr_worker.control import Cancelled,check_cancel
from _gbr_worker.runpod import RemotePending, RunpodClient
from _gbr_worker.serverless import ServerlessClient
sys.path.insert(0,str(ROOT/"_99_tools/_workers_comfyui"))
from client import Client as ComfyClient


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def default_python():
    return WORKER / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def upgrade_database(conn):
    from _gbr_database.migration import install_schema
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


def reserve_output(conn, run, output_root, layout='legacy'):
    row = conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run["id"],)).fetchone()
    if row:
        if not Path(row["output_directory"]).resolve().is_relative_to(output_root.resolve()):
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
    directory = output_root / run['id']
    if layout != 'legacy':
        group = run['title'] if layout == 'track' else run.get('album') or 'no-album' if layout == 'album' else None
        if group:
            group = re.sub(r'[^a-z0-9]+', '-', group.casefold()).strip('-')[:100] or 'untitled'
            output_root = output_root / group
        directory = output_root / (stem + '--' + run['id'][:8])
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
    final.parent.mkdir(parents=True, exist_ok=True)
    bundle = final.parent / ('.gbr-' + attempt_id)
    if bundle.exists():
        # An interrupted tag write is disposable only while the verified raw audio is retained.
        allowed = {state['output_stem'] + '.flac', state['output_stem'] + '.yaml', 'source.yaml', 'generation.json'}
        if bundle.is_symlink() or any(p.name not in allowed or not p.is_file() or p.is_symlink() for p in bundle.iterdir()):
            raise ValueError('Unrecognised staging files were retained for inspection: ' + str(bundle))
        for path in bundle.iterdir():
            path.unlink()
        bundle.rmdir()
    stem = state['output_stem']
    try:
        bundle.mkdir()
        completed_at = datetime.fromisoformat(result["completed_at"])
        write_completed_yaml(source, result["actual_parameters"], bundle / (stem + ".yaml"), succeeded=True,
                             completed_at=completed_at, timezone_name=timezone_name)
        write_new(bundle / "source.yaml", source)
        shutil.copyfile(audio, bundle / (stem + ".flac"))
        metadata = {**run["effective"], "song_id": run["song_id"], "variant_id": run["variant_id"], "title": run["title"],
                    "gbr_run_id": run["id"], "gbr_attempt_id": attempt_id, "gbr_source_sha256": run["source_sha256"],
                    "generation_timestamp": result["completed_at"],
                    "generation_source": ("gbr_" if execution_target(run["execution"])[0]=="comfyui" else "gbr_native_") + execution_target(run["execution"])[0],
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
        marker['worker_audio_sha256'] = file_sha(audio)
        write_new(bundle / "generation.json", json_text(marker).encode("utf-8"))
        final.parent.mkdir(parents=True, exist_ok=True)
        # Stage in the destination parent so publication is atomic across output-drive choices.
        # Windows/POSIX rename refuses replacement of a nonempty published bundle.
        bundle.rename(final)
    finally:
        # Failed publication keeps one raw FLAC for recovery, never an extra untagged stage copy.
        if bundle.exists() and not bundle.is_symlink():
            allowed = {stem + '.flac', stem + '.yaml', 'source.yaml', 'generation.json'}
            if all(p.name in allowed and p.is_file() and not p.is_symlink() for p in bundle.iterdir()):
                for path in bundle.iterdir():
                    path.unlink()
                bundle.rmdir()
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
        if conn.execute('SELECT status FROM generation_runs WHERE id=?', (run_id,)).fetchone()[0] == 'succeeded':
            artifacts = conn.execute('SELECT uri,sha256 FROM generation_artifacts WHERE run_id=?', (run_id,)).fetchall()
            if artifacts and all(Path(a['uri']).is_file() and file_sha(a['uri']) == a['sha256'] for a in artifacts):
                return {'run_id': run_id, 'status': 'succeeded', 'output': str(Path(artifacts[0]['uri']).parent)}
        state = conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run_id,)).fetchone()
        active = conn.execute("SELECT * FROM generation_attempts WHERE run_id=? AND status='running'", (run_id,)).fetchall()
        if any(a["execution_mode"] in {'runpod_dedicated', 'runpod_serverless', 'comfyui'} for a in active) and not remote_stopped:
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
        if conn.execute("SELECT status FROM generation_runs WHERE id=?", (run_id,)).fetchone()[0] not in {"running","publishing","cancel_requested"}:
            raise ValueError("Only an interrupted running job requires recovery")
        conn.execute("UPDATE generation_runs SET status='failed' WHERE id=?", (run_id,))
        return {"run_id": run_id, "status": "failed", "retry": "execute --retry-failed"}


def cleanup_outputs(manifest_path, *, runpod_identity=None, worker_id=None):
    """Retry acknowledgement only after all published artifacts still verify, including after archive."""
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = verify_plan(manifest_path)
    runs = {run['id']: run for run in manifest['runs']}
    cleaned = []
    with closing(open_database(manifest['database'])) as conn:
        for directory in manifest_path.parent.glob('attempts/*'):
            if not directory.is_dir() or not (directory / 'request.json').is_file():
                continue
            pending = directory / 'cleanup-pending.json'
            job = json.loads((directory / 'request.json').read_text())
            if worker_id and '|'.join(str(v or '') for v in execution_target(job['execution']))!=worker_id:continue
            run = runs.get(job['run_id'])
            if not run or job['attempt_id'] != directory.name:
                raise ValueError('Cleanup reference differs from the frozen attempt')
            state = conn.execute('SELECT status FROM generation_runs WHERE id=?', (run['id'],)).fetchone()
            artifacts = conn.execute('SELECT uri,sha256 FROM generation_artifacts WHERE run_id=?', (run['id'],)).fetchall()
            if state and state['status']=='cancelled' and (directory/'cancelled-output.json').is_file():
                verify_cancelled(directory)
                if pending.is_file():
                    backend,address=execution_target(job['execution'])
                    if backend=='comfyui':client=ComfyClient(address)
                    elif backend=='runpod_serverless':client=ServerlessClient(address)
                    elif backend=='runpod_dedicated':client=RunpodClient(address,runpod_identity,release=json.loads((directory/'remote.json').read_text()).get('release'))
                    else:client=None
                    if client:client.acknowledge(job,directory)
                    pending.unlink()
                cleaned.append(job['attempt_id']);continue
            if not state or state['status'] != 'succeeded':
                continue
            if len(artifacts) < 4 or not all(
                    Path(a['uri']).is_file() and file_sha(a['uri']) == a['sha256'] for a in artifacts):
                raise ValueError('Published files do not verify; remote output retained')
            report = conn.execute("SELECT uri FROM generation_artifacts WHERE run_id=? AND kind='generation_report'", (run['id'],)).fetchone()
            marker = json.loads(Path(report['uri']).read_text())
            audio = directory / 'audio.flac'
            if audio.is_file():
                if marker.get('attempt_id') != job['attempt_id'] or file_sha(audio) != marker.get('worker_audio_sha256'):
                    raise ValueError('Temporary audio differs from its published report; retained for inspection')
                audio.unlink()
                cleaned.append(job['attempt_id'])
            if not pending.is_file():
                continue
            backend, address = execution_target(job['execution'])
            if backend == 'runpod_dedicated':
                reference = json.loads((directory / 'remote.json').read_text())
                client = RunpodClient(address, runpod_identity, release=reference.get('release'))
            elif backend == 'runpod_serverless':
                client = ServerlessClient(address)
            elif backend == 'comfyui':
                client = ComfyClient(address)
            else:
                pending.unlink()
                if job['attempt_id'] not in cleaned:
                    cleaned.append(job['attempt_id'])
                continue
            client.acknowledge(job, directory)
            pending.unlink()
            if job['attempt_id'] not in cleaned:
                cleaned.append(job['attempt_id'])
    return {'cleaned_attempts': cleaned}


def execute(manifest_path, *, worker_python=None, limit=1, retry_failed=False, timezone_name="Europe/London",
            launcher=None, runpod_identity=None, resume_remote=False, worker_id=None,progress=None):
    from zoneinfo import ZoneInfo
    ZoneInfo(timezone_name)
    if type(limit) is not int or limit < 1:
        raise ValueError("limit must be a positive integer")
    manifest_path = Path(manifest_path).resolve(strict=True)
    manifest = verify_plan(manifest_path)
    if any(r.get('worker_options') for r in manifest['runs']):
        if not worker_id:raise ValueError('This shared batch requires a worker identity')
        runs=[]
        for run in manifest['runs']:
            option=next((o for o in run.get('worker_options',[]) if o['worker_id']==worker_id),None)
            if option:runs.append({**run,**{k:option[k] for k in ('effective','provenance','execution','model_revision')}})
        manifest={**manifest,'runs':runs}
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
        elif backend == 'runpod_serverless':
            ServerlessClient(address)
    attempts_root = manifest_path.parent / "attempts"
    output_root = Path(manifest.get('output_root') or manifest_path.parent / 'outputs')
    results = []
    with closing(open_database(manifest["database"])) as conn:
        upgrade_database(conn)
        for run in manifest["runs"]:
            if len(results) >= limit:
                break
            conn.execute("BEGIN IMMEDIATE")
            status = conn.execute("SELECT status FROM generation_runs WHERE id=?", (run["id"],)).fetchone()[0]
            backend, address = execution_target(run["execution"])
            if status in {'succeeded','cancelled'} or status == "failed" and not retry_failed:
                conn.rollback()
                continue
            if worker_id:
                previous=conn.execute('SELECT id FROM generation_attempts WHERE run_id=? ORDER BY started_at DESC,rowid DESC LIMIT 1',(run['id'],)).fetchone()
                if previous:
                    saved=json.loads((attempts_root/previous['id']/'request.json').read_text())
                    if saved['execution']!=run['execution'] or saved['parameters']!=run['effective']:
                        conn.rollback();continue
                if status in {'running','publishing','cancel_requested'} and not resume_remote:
                    conn.rollback();continue
            resuming = status in {'running','cancel_requested'} and resume_remote and backend in {'runpod_dedicated', 'runpod_serverless', 'comfyui'}
            if resume_remote and not resuming:
                conn.rollback()
                continue
            if status not in {"prepared", "failed"} and not resuming:
                conn.rollback()
                raise ValueError(f"Run {run['id']} is {status}; use --resume-remote for ComfyUI/RunPod or recover for an interrupted local job")
            state = reserve_output(conn, run, output_root, manifest.get('output_layout', 'legacy'))
            if Path(state["output_directory"]).exists():
                conn.rollback()
                raise ValueError("A published bundle exists for this run; use recover to reconcile it without generating again")
            publication = None
            if status == 'failed' and retry_failed:
                previous = conn.execute("SELECT * FROM generation_attempts WHERE run_id=? ORDER BY started_at DESC,rowid DESC LIMIT 1", (run['id'],)).fetchone()
                if previous:
                    candidate = attempts_root / previous['id']
                    receipt = candidate / 'publication-input.json'
                    if receipt.is_file() and (candidate / 'audio.flac').is_file():
                        saved = json.loads(receipt.read_text())
                        if saved.get('run_id') != run['id'] or saved.get('attempt_id') != previous['id'] or any(
                                file_sha(candidate / name) != saved.get(name) for name in ('audio.flac', 'result.json', 'request.json')):
                            raise ValueError('Saved publication inputs changed; retained for inspection without another generation')
                        publication = previous
            if publication is not None:
                attempt_id, started = publication['id'], publication['started_at']
                directory = attempts_root / attempt_id
                job = json.loads((directory / 'request.json').read_text())
                expected = make_job(run, manifest_path.parent, attempt_id, directory, job.get('model_revision'))
                if request_paths(job) != request_paths(expected):
                    raise ValueError('Saved publication request changed')
                with (directory / 'publication-recovery.ndjson').open('a', encoding='utf-8') as stream:
                    stream.write(json.dumps({'at': now(), 'previous_error': publication['error']}, ensure_ascii=False) + '\n')
                conn.execute("UPDATE generation_attempts SET status='running',error=NULL,completed_at=NULL WHERE id=?", (attempt_id,))
                conn.execute("UPDATE generation_runs SET status='running' WHERE id=?", (run['id'],))
            elif resuming:
                active = conn.execute("SELECT * FROM generation_attempts WHERE run_id=? AND status='running'", (run["id"],)).fetchall()
                if len(active) != 1:
                    raise ValueError("Expected exactly one active remote attempt")
                attempt_id, started = active[0]["id"], active[0]["started_at"]
                directory = attempts_root / attempt_id
                job = json.loads((directory / "request.json").read_text(encoding="utf-8"))
                expected = make_job(run, manifest_path.parent, attempt_id, directory, job.get("model_revision"))
                if request_paths(job) != request_paths(expected):
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
                conn.execute("UPDATE generation_runs SET status='running',started_at=?,effective_json=? WHERE id=?", (started,json_text(run['effective']), run["id"]))
            conn.commit()
            print(f'[{now()}] Starting {run["title"]} · run {run["id"][:8]} · attempt {attempt_id[:8]} · {backend} {address or "local"}',flush=True)
            clock, error, marker, pending, was_cancelled = time.perf_counter(), None, None, False, False
            client=None
            def cancelled():
                with closing(open_database(manifest['database'])) as reader:
                    return reader.execute('SELECT status FROM generation_runs WHERE id=?',(run['id'],)).fetchone()[0] in {'cancel_requested','cancelled'}
            def notify(stage,**data):
                update={'run_id':run['id'],'attempt_id':attempt_id,'stage':stage,**data}
                previous=getattr(notify,'last',{})
                if progress and update!=previous:
                    if stage==previous.get('stage') and time.monotonic()-getattr(notify,'at',0)<.5:return
                    progress(**update);notify.last=update;notify.at=time.monotonic()
            def remote_client(client):
                client.cancelled=cancelled;client.progress=notify
                return client
            try:
                if not resuming:check_cancel(cancelled)
                notify('starting_worker')
                if publication is not None:
                    print('Reusing verified worker audio; retrying metadata/publication without inference.', file=sys.stderr)
                    if not launcher and backend == 'runpod_dedicated':
                        reference = json.loads((directory / 'remote.json').read_text())
                        client = RunpodClient(address, runpod_identity, release=reference.get('release'))
                    elif not launcher and backend == 'runpod_serverless':
                        client = ServerlessClient(address)
                    elif not launcher and backend=='comfyui':
                        client=ComfyClient(address)
                elif launcher:
                    launcher(python, directory / "request.json", directory)
                elif backend == "runpod_dedicated":
                    reference = json.loads((directory / "remote.json").read_text()) if resuming else {}
                    client = remote_client(RunpodClient(address, runpod_identity, release=reference.get("release")))
                    client.run(job, directory, resume=resuming)
                elif backend == 'runpod_serverless':
                    client = remote_client(ServerlessClient(address))
                    client.run(job, directory, resume=resuming)
                elif backend=='comfyui':
                    client=remote_client(ComfyClient(address))
                    client.run(job,directory,resume=resuming)
                else:
                    with (directory / "stderr.log").open("x", encoding="utf-8") as log:
                        process = subprocess.Popen([str(python), "-u", str(WORKER / "worker.py"), "run", str(directory / "request.json")],
                                                   stdout=log, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace")
                        stopped=threading.Event()
                        def watch_cancel():
                            while not stopped.wait(.25) and process.poll() is None:
                                if cancelled():
                                    process.terminate()
                                    try:process.wait(timeout=3)
                                    except subprocess.TimeoutExpired:process.kill()
                                    return
                        watcher=threading.Thread(target=watch_cancel,daemon=True);watcher.start()
                        try:
                            stage='starting_worker'
                            for line in process.stderr:
                                log.write(line)
                                log.flush()
                                print(line, end="", file=sys.stderr, flush=True)
                                event=re.search(r'\] stage_start (\w+)',line)
                                if event:stage=event[1];notify(stage)
                                step=re.search(r'(\d+)%.*?(\d+)/(\d+)',line)
                                if step:notify(stage,step=int(step[2]),steps=int(step[3]))
                            return_code = process.wait()
                            check_cancel(cancelled)
                        except BaseException:
                            if process.poll() is None:process.terminate()
                            try:
                                process.wait(timeout=15)
                            except subprocess.TimeoutExpired:
                                process.kill()
                                process.wait()
                            raise
                        finally:
                            stopped.set();watcher.join(timeout=4)
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
                check_cancel(cancelled)
                result = json.loads((directory / "result.json").read_text(encoding="utf-8"))
                if result.get("status") != "succeeded" or result.get("run_id") != run["id"] or result.get("attempt_id") != attempt_id or result.get("protocol") != PROTOCOL:
                    raise ValueError(result.get("error", "Invalid/failed worker result"))
                receipt = directory / 'publication-input.json'
                evidence = {'run_id': run['id'], 'attempt_id': attempt_id,
                            **{name: file_sha(directory / name) for name in ('audio.flac', 'result.json', 'request.json')}}
                if receipt.is_file():
                    if json.loads(receipt.read_text()) != evidence:
                        raise ValueError('Publication input checksums changed')
                else:
                    write_new(receipt, json_text(evidence).encode('utf-8'))
                conn.execute('BEGIN IMMEDIATE')
                if conn.execute('SELECT status FROM generation_runs WHERE id=?',(run['id'],)).fetchone()[0]=='cancel_requested':
                    conn.rollback();raise Cancelled('Cancelled before publication')
                conn.execute("UPDATE generation_runs SET status='publishing' WHERE id=?",(run['id'],));conn.commit()
                notify('publishing')
                with conn:
                    store_observations(conn, attempt_id, run["id"], read_events(directory))
                state = dict(conn.execute("SELECT * FROM generation_execution_state WHERE run_id=?", (run["id"],)).fetchone())
                marker = publish_bundle(directory, run, state, result, (manifest_path.parent / run["snapshot"]).read_bytes(), attempt_id, timezone_name)
                with conn:
                    record_output(conn, run, state, marker)
                try:
                    audio = directory / 'audio.flac'
                    if audio.is_file():
                        if file_sha(audio) != marker.get('worker_audio_sha256'):
                            raise ValueError('Temporary audio changed; retained for inspection')
                        audio.unlink()
                    if not launcher and backend != 'local':
                        client.acknowledge(job, directory)
                    (directory / 'cleanup-pending.json').unlink(missing_ok=True)
                except Exception as exc:
                    (directory / 'cleanup-pending.json').write_text(json_text({'run_id': run['id'], 'attempt_id': attempt_id, 'error': str(exc)}))
                    print('Output saved. Temporary artifact cleanup needs a retry from Generation.', file=sys.stderr)
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"
                marker = None
                pending = backend in {'runpod_dedicated', 'runpod_serverless', 'comfyui'} and isinstance(exc, (RemotePending, KeyboardInterrupt))
                was_cancelled=isinstance(exc,Cancelled)
                if was_cancelled:
                    # Explicit cancellation authorises discarding this owned result;
                    # keep a checksum receipt so deferred remote cleanup is verifiable.
                    try:
                        if discard_cancelled(directory,job) and client:client.acknowledge(job,directory)
                    except Exception as cleanup_error:
                        (directory/'cleanup-pending.json').write_text(json_text({'error':str(cleanup_error),'cancelled':True}))
                    (directory/'download.part').unlink(missing_ok=True)
                if isinstance(exc, KeyboardInterrupt):
                    print("Controller interrupted; reconnect with --resume-remote. The pod continues working." if pending
                          else "Local test interrupted; saving available telemetry.", file=sys.stderr)
            finally:
                ended = now()
                with conn:
                    store_observations(conn, attempt_id, run["id"], read_events(directory))
                    duration = (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds() if resuming else time.perf_counter() - clock
                    if publication is None:
                        conn.execute("UPDATE generation_timing_spans SET completed_at=?,duration_seconds=? WHERE id=?",
                                     (None if pending else ended, None if pending else duration, attempt_id + "-total"))
                    conn.execute("UPDATE generation_attempts SET status=?,completed_at=?,error=? WHERE id=?", (
                        "running" if pending else "cancelled" if was_cancelled else "succeeded" if marker else "failed", None if pending else ended, error, attempt_id))
                    if not marker and not pending:
                        conn.execute("UPDATE generation_runs SET status=?,completed_at=? WHERE id=?", ('cancelled' if was_cancelled else 'failed',ended,run["id"]))
                results.append({"run_id": run["id"], "attempt_id": attempt_id, "status": "running" if pending else "cancelled" if was_cancelled else "succeeded" if marker else "failed",
                                "output": state["output_directory"] if marker else None, "error": error,
                                "diagnostics": str(directory), **({"resume": "execute --resume-remote"} if pending else {})})
            if error and not was_cancelled:
                break
        with conn:
            statuses = [r[0] for r in conn.execute("SELECT status FROM generation_runs WHERE batch_id=?", (manifest["batch_id"],))]
            batch_status = "succeeded" if all(s == "succeeded" for s in statuses) else "running" if any(s in {'running','publishing','cancel_requested'} for s in statuses) else "failed" if 'failed' in statuses else "prepared" if 'prepared' in statuses else 'cancelled'
            conn.execute("UPDATE generation_batches SET status=? WHERE id=?", (batch_status, manifest["batch_id"]))
    return {"batch_id": manifest["batch_id"], "status": batch_status, "attempts": results}

