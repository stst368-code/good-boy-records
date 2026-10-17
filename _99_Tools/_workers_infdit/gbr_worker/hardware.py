"""Discover rather than configure hardware; unavailable measurements stay null."""
from contextlib import contextmanager
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import socket
import tempfile

PACKAGES = ("torch", "diffusers", "transformers", "accelerate", "safetensors", "huggingface-hub",
            "tokenizers", "regex", "numpy", "soundfile", "mutagen", "psutil", "nvidia-ml-py")


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
    props = torch.cuda.get_device_properties(device)
    free, total = torch.cuda.mem_get_info(device)
    gpu_uuid = str(getattr(props, "uuid", "")) or None
    memory = psutil.virtual_memory()
    return {"address": f"local://{socket.gethostname()}/{gpu_uuid or 'cuda-' + str(device.index)}",
            "provider": "local", "provider_instance_id": None, "gpu_uuid": gpu_uuid,
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
