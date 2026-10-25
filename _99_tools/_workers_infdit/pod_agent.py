"""Dedicated-pod process supervision and result transfer. No platform database on the pod."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from _gbr_worker.contract import canonical, execution_target, file_sha, validate_job, python_environment
from _gbr_worker.hardware import restore_pod_environment
from _gbr_worker.process_state import identity, inspect_attempt

RELEASE = Path(__file__).resolve().parent
ROOT = RELEASE.parent.parent
RUNTIME = ROOT / "runtime"
PYTHON = ROOT / ".venv" / "bin" / "python"
OUTPUTS = {"audio.flac", "result.json", "events.ndjson", "startup.ndjson", "stderr.log", "exit.json"}


def environment():
    return python_environment(HF_HOME=str(RUNTIME / "huggingface"), PYTHONUNBUFFERED="1")


def attempt_directory(attempt_id):
    if not re.fullmatch(r"[0-9a-f]{32}", attempt_id):
        raise ValueError("Invalid remote attempt ID")
    return RUNTIME / "attempts" / attempt_id


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(canonical(value) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


@contextmanager
def launch_lock():
    RUNTIME.mkdir(parents=True, exist_ok=True)
    with (RUNTIME / "launch.lock").open("a+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        yield


def status(directory):
    if not directory.is_dir():
        return {"state": "missing"}
    result = inspect_attempt(directory, ROOT)
    if (directory / "exit.json").is_file():
        result.update(json.loads((directory / "exit.json").read_text(encoding="utf-8")))
        result["state"] = "finished"
    log = directory / "stderr.log"
    if log.is_file():
        with log.open("rb") as stream:
            stream.seek(max(0, log.stat().st_size - 8192))
            result["log_tail"] = stream.read(8192).decode("utf-8", errors="replace")
    return result


def start(original):
    validate_job(original, require_local=False)
    backend, _ = execution_target(original["execution"])
    if backend != "runpod_dedicated" or not os.environ.get("RUNPOD_POD_ID"):
        raise ValueError("Dedicated execution requires a RunPod pod and a dedicated target")
    directory = attempt_directory(original["attempt_id"])
    digest = hashlib.sha256(canonical(original).encode()).hexdigest()
    with launch_lock():
        if directory.exists():
            saved = json.loads((directory / "identity.json").read_text(encoding="utf-8"))
            if saved["sha256"] != digest or saved["release"] != str(RELEASE):
                raise ValueError("Attempt already exists with different input or worker code")
            return status(directory)
        directory.mkdir(parents=True)
        write_json(directory / "identity.json", {"sha256": digest, "release": str(RELEASE)})
        job = dict(original, result_directory=str(directory), cache_root=str(RUNTIME / "encodings"))
        write_json(directory / "request.json", job)
        with (directory / "stderr.log").open("xb") as log:
            process = subprocess.Popen([str(PYTHON), str(RELEASE / "pod_agent.py"), "work", original["attempt_id"]],
                stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True, env=environment())
        write_json(directory / "pid.json", identity(process.pid))
    return status(directory)


def work(attempt_id):
    directory = attempt_directory(attempt_id)
    # A second supervisor must never execute an existing request again.
    with (directory / "started").open("x"):
        pass
    code = subprocess.call([str(PYTHON), "-u", str(RELEASE / "worker.py"), "run", str(directory / "request.json")],
                           env=environment())
    write_json(directory / "exit.json", {"returncode": code})
    return code


def main():
    restore_pod_environment()
    operation = sys.argv[1]
    if operation == "work":
        return work(sys.argv[2])
    body = json.load(sys.stdin)
    if operation == "doctor":
        subprocess.run([str(PYTHON), str(RELEASE / "worker.py"), "doctor"], env=environment(), check=True)
        return 0
    if operation == "start":
        result = start(body["job"])
    else:
        directory = attempt_directory(body["attempt_id"])
        if operation == "status":
            result = status(directory)
        elif operation == "outputs":
            if status(directory)["state"] != "finished":
                raise ValueError("The remote worker has not finished; outputs remain on the pod")
            result = {name: {"sha256": file_sha(directory / name), "size_bytes": (directory / name).stat().st_size}
                      for name in sorted(OUTPUTS) if (directory / name).is_file()}
        elif operation == "download":
            if body["name"] not in OUTPUTS or status(directory)["state"] != "finished":
                raise ValueError("Invalid or unfinished output")
            with (directory / body["name"]).open("rb") as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b""):
                    sys.stdout.buffer.write(block)
            return 0
        elif operation == 'acknowledge':
            if status(directory)['state'] != 'finished' or file_sha(directory / 'result.json') != body['result_sha256']:
                raise ValueError('Remote result changed; retained on the pod')
            audio = directory / 'audio.flac'
            if audio.is_file():
                if file_sha(audio) != body['audio_sha256']:
                    raise ValueError('Remote audio changed; retained on the pod')
                audio.unlink()
            write_json(directory / 'collected.json', {'audio_sha256': body['audio_sha256'], 'result_sha256': body['result_sha256']})
            result = {'collected': True}
        else:
            raise ValueError("Unknown pod operation")
    print(canonical(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
