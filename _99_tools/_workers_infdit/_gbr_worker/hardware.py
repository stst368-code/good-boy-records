"""Discover rather than configure hardware; unavailable measurements stay null."""
from contextlib import contextmanager
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import shlex
import socket
import tempfile

PACKAGES = ("torch", "diffusers", "transformers", "accelerate", "safetensors", "huggingface-hub",
            "tokenizers", "regex", "numpy", "soundfile", "mutagen", "psutil", "nvidia-ml-py")


def restore_pod_environment(path=Path("/etc/rp_environment")):
    """RunPod's SSH daemon may omit container variables. Read known values without sourcing shell code."""
    allowed = {"RUNPOD_POD_ID", "RUNPOD_DC_ID", "RUNPOD_POD_HOSTNAME", "RUNPOD_GPU_COUNT", "RUNPOD_CPU_COUNT",
               "RUNPOD_PUBLIC_IP", "RUNPOD_TCP_PORT_22", "RUNPOD_VOLUME_ID", "CUDA_VERSION", "CUDA_HOME",
               "CUDA_PATH", "LD_LIBRARY_PATH", "PATH"}
    try:
        with Path(path).open(encoding="utf-8") as stream:
            lines = stream.read(1024 * 1024).splitlines()
    except FileNotFoundError:
        return
    for line in lines:
        try:
            parts = shlex.split(line, comments=True)
        except ValueError:
            continue
        if len(parts) == 2 and parts[0] == "export":
            key, separator, value = parts[1].partition("=")
            if separator and key in allowed:
                os.environ.setdefault(key, value)


def versions():
    result = {}
    for name in PACKAGES:
        try:
            result[name] = metadata.version(name)
        except metadata.PackageNotFoundError:
            result[name] = None
    return result


def discover(torch, device):
    import psutil
    restore_pod_environment()
    props = torch.cuda.get_device_properties(device)
    free, total = torch.cuda.mem_get_info(device)
    gpu_uuid = str(getattr(props, "uuid", "")) or None
    memory = psutil.virtual_memory()
    pod = os.environ.get("RUNPOD_POD_ID") or None
    serverless = os.environ.get('GBR_SERVERLESS_WORKER') == '1'
    identity = gpu_uuid or 'cuda-' + str(device.index)
    # Deliberately allowlist runtime metadata: never record the pod-scoped API key or keys.
    provider_details = {key: os.environ.get(key) or None for key in
                        ("RUNPOD_DC_ID", "RUNPOD_POD_HOSTNAME", "RUNPOD_GPU_COUNT", "RUNPOD_CPU_COUNT",
                         "RUNPOD_PUBLIC_IP", "RUNPOD_TCP_PORT_22", "RUNPOD_VOLUME_ID")} if pod else {}
    address = f"runpod-serverless://{os.environ.get('GBR_ENDPOINT_ID', 'unknown')}/{pod or socket.gethostname()}/{identity}" if serverless else f"runpod://{pod}/{identity}" if pod else f"local://{socket.gethostname()}/{identity}"
    return {"address": address, 'execution_mode': 'runpod_serverless' if serverless else 'runpod_dedicated' if pod else 'local',
            "provider": "runpod" if pod or serverless else "local", "provider_instance_id": pod,
            "provider_details": provider_details, "gpu_uuid": gpu_uuid,
            "gpu_name": props.name, "usable_vram_bytes": total, "free_vram_bytes_at_start": free,
            "compute_capability": [props.major, props.minor], "cuda_device": device.index,
            "cpu": platform.processor() or None, "cpu_physical_cores": psutil.cpu_count(logical=False),
            "cpu_logical_cores": psutil.cpu_count(), "ram_total_bytes": memory.total,
            "ram_available_bytes_at_start": memory.available, "os": platform.platform(),
            "python": platform.python_version(), "torch_cuda": torch.version.cuda,
            "nvml_driver": None}


@contextmanager
def gpu_lock(identity):
    """OS lock survives stale files and releases on a killed worker. No PID/name registry."""
    key = hashlib.sha256(identity.encode()).hexdigest()[:24]
    path = Path(tempfile.gettempdir()) / f"gbr-gpu-{key}.lock"
    with path.open("a+b") as handle:
        handle.seek(0)
        if path.stat().st_size == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeError("Another GBR worker is already using this GPU") from exc
        try:
            yield
        finally:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def check_install():
    from .contract import DIFFUSERS_REVISION
    dist = metadata.distribution("diffusers")
    direct = json.loads(dist.read_text("direct_url.json") or "{}")
    if DIFFUSERS_REVISION not in json.dumps(direct):
        raise RuntimeError("Diffusers differs from the verified source revision; rerun Install Local Worker.bat")
    # Import the exact blocks now, before any model download.
    from diffusers.modular_pipelines.minimax_music3.modular_blocks_minimax_music3 import (
        MiniMaxMusic3SemanticGenerationStep, MiniMaxMusic3CoreDenoiseStep)
    from diffusers.modular_pipelines.minimax_music3.decoders import MiniMaxMusic3VocoderDecodeStep
    from diffusers.hooks.group_offloading import apply_group_offloading
    return versions()

