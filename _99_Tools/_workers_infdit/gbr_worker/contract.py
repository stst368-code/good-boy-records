"""Small, dependency-free boundary shared by the controller and worker."""
import hashlib
import json
import math
from pathlib import Path
import re

PROTOCOL = 1
DIFFUSERS_REVISION = "b800f828f3951c4d206e154411bcbfa693cd322e"
MODEL = "MiniMaxAI/MiniMax-Music3"
SMOKE_MODEL_REVISION = "fbdf52fbaaca799592917417eb05f1899f1255ec"
ADAPTER = "gbr-minimax-music3-v1"


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_job(job, *, allow_unfrozen_seeds=False, require_local=True):
    if job.get("protocol") != PROTOCOL or not isinstance(job.get("run_id"), str):
        raise ValueError("Unsupported worker request")
    p, execution = job["parameters"], job["execution"]
    if p.get("model") != MODEL:
        raise ValueError(f"This adapter supports the native {MODEL} repository only; no ComfyUI checkpoints")
    for stage in ("conditioning", "diffusion"):
        target = execution[stage]
        if require_local and (target["backend"] != "local" or target.get("address")):
            raise ValueError("This execution adapter supports local stages only; RunPod transport is not installed")
    for field in ("dit", "text_encoder"):
        if p.get(field) not in {"bf16", "fp32"}:
            raise ValueError(f"{field}: native worker supports explicit bf16 or fp32 precision")
    if p.get("encoder_cfg") != 1.5 or p.get("top_k") != 50:
        raise ValueError("Pinned native MiniMax semantic generation fixes encoder_cfg=1.5 and top_k=50")
    if p.get("sampler") != "euler" or p.get("scheduler") != "flow_match_euler":
        raise ValueError("Pinned native MiniMax supports sampler=euler, scheduler=flow_match_euler")
    for field in ("encoder_seed", "sampler_seed"):
        value = p.get(field)
        if allow_unfrozen_seeds and value in (None, "auto"):
            continue
        if isinstance(value, bool) or not str(value).isdigit() or not 0 <= int(value) < 2**64:
            raise ValueError(f"{field} must be a frozen unsigned 64-bit integer")
    for field in ("sampler_cfg", "duration"):
        value = p.get(field)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {field}")
    if not 0.04 <= p["duration"] <= 360:
        raise ValueError("duration must be between 0.04 and 360 seconds; native duration is an upper bound")
    if type(p.get("sampler_steps")) is not int or not 1 <= p["sampler_steps"] <= 1000:
        raise ValueError("sampler_steps must be an integer between 1 and 1000")
    if not isinstance(p.get("caption"), str) or not p["caption"].strip():
        raise ValueError("A nonempty caption is required")
    if not isinstance(job.get("lyrics"), str):
        raise ValueError("lyrics must be text")
    for number, line in enumerate(job["lyrics"].splitlines(), 1):
        if re.match(r"^\s*\[[^\]]+\]\s*\S", line):
            raise ValueError(f"Lyrics line {number}: put section tags on their own line; the native model discards inline text")
    revision = job.get("model_revision")
    if revision is not None and not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("model_revision must be a full 40-character commit SHA")
    if execution.get("encoding_cache", "prefer") not in {"prefer", "require", "disabled"}:
        raise ValueError("Invalid encoding cache policy")
    if execution.get("memory_profile", "low_vram") not in {"low_vram", "resident_stage"}:
        raise ValueError("Unknown memory profile")
    if type(execution.get("cuda_device", 0)) is not int or execution.get("cuda_device", 0) < 0:
        raise ValueError("cuda_device must be a nonnegative integer")
    if not 50 <= execution.get("vram_sample_interval_ms", 100) <= 5000:
        raise ValueError("VRAM sample interval must be between 50 and 5000 ms")
    return job


def encoding_identity(job, revision, versions):
    p = job["parameters"]
    identity = {"adapter": ADAPTER, "diffusers_revision": DIFFUSERS_REVISION, "model": p["model"],
                "model_revision": revision, "versions": versions, "lyrics": job["lyrics"],
                "parameters": {key: p[key] for key in ("caption", "text_encoder", "encoder_cfg", "encoder_seed", "top_k", "duration")}}
    return hashlib.sha256(canonical(identity).encode("utf-8")).hexdigest(), identity
