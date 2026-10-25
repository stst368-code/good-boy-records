"""SSH transport for a dedicated RunPod worker, callable by the platform controller."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import time

from .contract import canonical, execution_target, file_sha, ssh_endpoint

WORKER = Path(__file__).resolve().parents[1]
REMOTE_ROOT = "/workspace/good-boy-records/_99_Tools/_workers_infdit"

# Only our small worker source bundle crosses SSH; no DB, environment, keys or repo archive.
BOOTSTRAP = r'''
import hashlib,json,os,pathlib,shutil,subprocess,sys
bundle=json.load(sys.stdin)
root=pathlib.Path("/workspace/good-boy-records/_99_Tools/_workers_infdit")
files=bundle["files"]
digest=hashlib.sha256(json.dumps(files,sort_keys=True,ensure_ascii=False,allow_nan=False,separators=(",",":")).encode()).hexdigest()
if digest != bundle["release"]: raise ValueError("Worker bundle checksum mismatch")
release=root/"releases"/digest
release.mkdir(parents=True,exist_ok=True)
for name,content in files.items():
    relative=pathlib.PurePosixPath(name)
    if relative.is_absolute() or ".." in relative.parts: raise ValueError("Invalid worker path")
    path=release/relative
    path.parent.mkdir(parents=True,exist_ok=True)
    data=content.encode("utf-8")
    if path.exists():
        if path.read_bytes()!=data: raise ValueError("Installed worker release was modified")
    else:
        with path.open("xb") as stream: stream.write(data)
sys.path.insert(0,str(release))
from _gbr_worker.process_state import inspect_attempt
import fcntl
runtime=root/"runtime"
runtime.mkdir(parents=True,exist_ok=True)
lock=(runtime/"launch.lock").open("a+b")
fcntl.flock(lock,fcntl.LOCK_EX)
for attempt in (runtime/"attempts").glob("*"):
    if not attempt.is_dir() or (attempt/"exit.json").exists(): continue
    state=inspect_attempt(attempt,root)
    if state['state'] != 'stopped':
        raise RuntimeError("Worker attempt "+attempt.name+" is "+state['state']+"; environment kept unchanged. "+state.get('reason','')+". Resume its queue to collect it; do not submit a replacement.")
    recovered={'returncode':None,'recovered':True,'process_evidence':state}
    temporary=attempt/"exit.recovered.tmp"
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(recovered,stream);stream.flush();os.fsync(stream.fileno())
    temporary.replace(attempt/"exit.json")
    print("Recovered stopped attempt "+attempt.name+"; diagnostics and results retained.",file=sys.stderr)
from _gbr_worker.hardware import restore_pod_environment
restore_pod_environment()
if not os.environ.get("RUNPOD_POD_ID"): raise RuntimeError("This SSH address is not a RunPod pod")
python=None
for name in ("python3.12","python3.11","python3"):
    candidate=shutil.which(name)
    if candidate and subprocess.call([candidate,"-c","import sys; sys.exit(sys.version_info[:2] not in ((3,11),(3,12)))"],stdout=sys.stderr,stderr=sys.stderr)==0:
        python=candidate
        break
if python is None: raise RuntimeError("Choose a pod template with Python 3.11 or 3.12 installed")
env=dict(os.environ,HF_HOME=str(root/"runtime"/"huggingface"),PYTHONUTF8='1',PYTHONIOENCODING='utf-8')
profile=hashlib.sha256((files["requirements.txt"]+"torch==2.10.0+cu128").encode()).hexdigest()
marker=root/"runtime"/"dependency-profile.sha256"
installed=root/".venv"/"bin"/"python"
reuse=installed.is_file() and marker.is_file() and marker.read_text(encoding='utf-8').strip()==profile
if reuse:
    reuse=subprocess.call([str(installed),str(release/"worker.py"),"doctor"],env=env,stdout=sys.stderr,stderr=sys.stderr)==0
if not reuse:
    subprocess.run([python,str(release/"install_worker.py"),"--environment",str(root/".venv"),"--runtime",str(root/"runtime")],env=env,stdout=sys.stderr,check=True)
    marker.parent.mkdir(parents=True,exist_ok=True)
    marker.write_text(profile,encoding='utf-8')
else: print("Existing worker dependencies and GPU check passed; reusing environment.",file=sys.stderr)
print(json.dumps({"release":digest,"remote_root":str(root)}))
'''


class RemotePending(RuntimeError):
    """The pod may still be working. Keep the same attempt and reconnect; never retry it."""


def source_bundle():
    names = ["worker.py", "pod_agent.py", "install_worker.py", "requirements.txt"]
    names += [str(path.relative_to(WORKER)).replace("\\", "/") for path in sorted((WORKER / "_gbr_worker").glob("*.py"))]
    files = {name: (WORKER / name).read_text(encoding="utf-8") for name in names}
    return {"files": files, "release": hashlib.sha256(canonical(files).encode()).hexdigest()}


class RunpodClient:
    def __init__(self, address, identity_file=None, *, release=None):
        user, host, port = ssh_endpoint(address)
        ssh = shutil.which("ssh")
        if not ssh:
            raise ValueError("OpenSSH client is required on the controller (Windows Optional Features: OpenSSH Client)")
        self.address = address
        self.release = release or source_bundle()["release"]
        if not re.fullmatch(r"[0-9a-f]{64}", self.release):
            raise ValueError("Invalid worker release")
        self.command = [ssh, "-T", "-p", str(port), "-o", "BatchMode=yes", "-o", "ConnectTimeout=20",
                        "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=3",
                        "-o", "StrictHostKeyChecking=accept-new"]
        if identity_file is not None:
            identity = Path(identity_file).expanduser().absolute()
            if not identity.is_file():
                raise ValueError("SSH identity file was not found on this device")
            self.command += ["-i", str(identity), "-o", "IdentitiesOnly=yes"]
        self.command += [f"{user}@{host}"]

    def invoke(self, arguments, body, *, output=None, timeout=60, progress=False):
        command = self.command + [shlex.join(str(value) for value in arguments)]
        try:
            # stdout is always machine-readable JSON or a single requested binary file.
            result = subprocess.run(command, input=canonical(body).encode("utf-8"),
                                    stdout=output or subprocess.PIPE,
                                    stderr=None if progress else subprocess.PIPE, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RemotePending("SSH disconnected; reconnect to this same attempt") from exc
        if result.returncode:
            message = (result.stderr or b"").decode("utf-8", errors="replace")[-8000:]
            if result.returncode == 255:
                if 'Permission denied' in message:
                    raise RemotePending('SSH authentication failed. Select the private key registered with RunPod; encrypted keys also need an unlocked SSH agent.')
                raise RemotePending("SSH connection failed; the pod may still be running. " + message)
            raise RuntimeError(f"RunPod worker command failed (exit {result.returncode}): " + (message or "see installer output above"))
        if output is None:
            try:
                return json.loads(result.stdout)
            except ValueError as exc:
                raise RemotePending("Incomplete SSH response; reconnect to this same attempt") from exc

    def rpc(self, operation, body=None, **kwargs):
        return self.invoke(["python3", f"{REMOTE_ROOT}/releases/{self.release}/pod_agent.py", operation], body or {}, **kwargs)

    def check_connection(self):
        # Works on a fresh pod without loading CUDA or requiring our worker release.
        script = ('import json,pathlib,platform,shutil\n'
            + f'root=pathlib.Path({REMOTE_ROOT!r})\n'
            + f'release=root/"releases"/{self.release!r}\n'
            + 'ready=(root/".venv/bin/python").is_file() and any((root/"releases").glob("*/worker.py"))\n'
            + 'scope={}\nexec(' + repr((WORKER / '_gbr_worker/process_state.py').read_text(encoding="utf-8")) + ',scope)\n'
            + 'attempts=[dict(attempt_id=p.name,**scope["inspect_attempt"](p,root)) for p in (root/"runtime/attempts").glob("*") if p.is_dir() and not (p/"exit.json").exists()]\n'
            + 'print(json.dumps({"connected":True,"python":platform.python_version(),"worker_installed":ready,"attempts":attempts,'
              '"message":"SSH connected. Worker is installed." if ready else '
              '"SSH connected. Generate with automatic setup enabled to prepare this worker."}))\n')
        return self.invoke(['python3', '-c', script], {}, timeout=30)

    def setup(self):
        bundle = source_bundle()
        if bundle["release"] != self.release:
            raise ValueError("Cannot install an older worker release from this checkout")
        self.check_connection()
        self.invoke(["python3", "-c", BOOTSTRAP], bundle, timeout=3600, progress=True)
        return self.rpc("doctor")

    def collect(self, attempt_id, directory):
        outputs = self.rpc("outputs", {"attempt_id": attempt_id})
        allowed = {"audio.flac", "result.json", "events.ndjson", "startup.ndjson", "stderr.log", "exit.json"}
        if not {"exit.json", "stderr.log"} <= set(outputs) or not set(outputs) <= allowed:
            raise ValueError("Invalid remote result manifest")
        # Verify size and SHA before any file replaces local diagnostics or enters publishing.
        for name, expected in outputs.items():
            maximum = 512 * 1024**2 if name == "audio.flac" else 32 * 1024**2
            if type(expected.get("size_bytes")) is not int or not 0 <= expected["size_bytes"] <= maximum:
                raise ValueError("Remote output exceeds the transfer limit: " + name)
            path = directory / (name + ".download")
            try:
                with path.open("wb") as stream:
                    self.rpc("download", {"attempt_id": attempt_id, "name": name}, output=stream, timeout=600)
                if path.stat().st_size != expected["size_bytes"] or file_sha(path) != expected["sha256"]:
                    raise RemotePending("Remote output checksum mismatch; reconnect to download it again")
                path.replace(directory / name)
            finally:
                path.unlink(missing_ok=True)
        code = json.loads((directory / "exit.json").read_text(encoding="utf-8"))["returncode"]
        if not (directory / "result.json").is_file():
            raise RuntimeError(f"Remote worker exited {code} without a result; see {directory / 'stderr.log'}")
        (directory / 'remote-output.json').write_text(canonical(outputs), encoding='utf-8')

    def acknowledge(self, job, directory):
        outputs = json.loads((directory / 'remote-output.json').read_text(encoding="utf-8"))
        # Works for attempts made by older immutable releases without installing code or packages.
        script = r'''
import hashlib,json,os,pathlib,re,sys
body=json.load(sys.stdin)
if not re.fullmatch('[0-9a-f]{32}',body['attempt_id']): raise ValueError('Invalid attempt')
root=pathlib.Path('/workspace/good-boy-records/_99_Tools/_workers_infdit/runtime/attempts')
directory=root/body['attempt_id']
def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''): h.update(block)
    return h.hexdigest()
if directory.is_symlink() or not (directory/'exit.json').is_file() or digest(directory/'result.json')!=body['result_sha256']:
    raise ValueError('Remote result changed or is unfinished; retained on the pod')
audio=directory/'audio.flac'
if audio.exists():
    if audio.is_symlink() or digest(audio)!=body['audio_sha256']: raise ValueError('Remote audio changed; retained on the pod')
    audio.unlink()
temporary=directory/'collected.tmp'
with temporary.open('w',encoding='utf-8') as stream:
    json.dump(body,stream);stream.flush();os.fsync(stream.fileno())
temporary.replace(directory/'collected.json')
print(json.dumps({'collected':True}))
'''
        self.invoke(['python3', '-c', script], {'attempt_id': job['attempt_id'],
            'audio_sha256': outputs.get('audio.flac', {}).get('sha256'), 'result_sha256': outputs['result.json']['sha256']})

    def run(self, job, directory, *, resume=False, poll_seconds=10):
        backend, address = execution_target(job["execution"])
        if backend != "runpod_dedicated" or address != self.address:
            raise ValueError("Remote dispatch target changed")
        reference = {"address": self.address, "release": self.release, "attempt_id": job["attempt_id"]}
        reference_path = directory / "remote.json"
        if resume:
            if json.loads(reference_path.read_text(encoding="utf-8")) != reference:
                raise ValueError("Remote attempt reference changed")
            operation, body = "status", {"attempt_id": job["attempt_id"]}
        else:
            with reference_path.open("x", encoding="utf-8") as stream:
                stream.write(canonical(reference) + "\n")
            operation, body = "start", {"job": deepcopy(job)}
        try:
            state = self.rpc(operation, body)
        except RuntimeError as exc:
            # An SSH command can fail after spawning a detached process but before its reply.
            raise RemotePending("Remote submission/status is uncertain; reconnect to this attempt. " + str(exc)) from exc
        last_tail = None
        while True:
            if getattr(self,'progress',None):
                if getattr(self,'cancelled',None) and self.cancelled():self.progress('cancel_after_generation',message='This dedicated worker release finishes its owned attempt before discarding the output.')
                else:
                    phases=re.findall(r'\] stage_start (\w+)',state.get('log_tail',''))
                    self.progress(phases[-1] if phases else 'dedicated_generating')
            tail = state.get("log_tail")
            if tail and tail != last_tail:
                fresh = tail[len(last_tail):] if last_tail and tail.startswith(last_tail) else tail[-2048:]
                print(fresh, end="", file=sys.stderr, flush=True)
                last_tail = tail
            if state["state"] == "finished":
                self.collect(job["attempt_id"], directory)
                return
            if state["state"] != "running":
                raise RemotePending(f"Remote attempt is {state['state']}; no replacement job was started. Inspect the pod and reconnect")
            time.sleep(poll_seconds)
            try:
                state = self.rpc("status", {"attempt_id": job["attempt_id"]})
            except RuntimeError as exc:
                raise RemotePending("Remote status is unavailable; reconnect to this attempt. " + str(exc)) from exc
