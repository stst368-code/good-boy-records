"""Resolve each requested value with explicit provenance, then freeze seeds."""
import math
import re
import secrets

from .yaml_document import GENERATION_FIELDS

SOURCES = ("defaults", "preset", "template", "yaml", "batch", "rerun")
PARAMETERS = GENERATION_FIELDS - {"generation_datetime"}
SEEDS = {"encoder_seed", "sampler_seed"}
INTEGERS = {"top_k", "sampler_steps"}
NUMBERS = {"encoder_cfg", "sampler_cfg", "duration"}
REQUIRED = {"model", "dit", "text_encoder", "encoder_cfg", "top_k", "sampler", "scheduler",
            "sampler_cfg", "sampler_steps", "duration"}


def execution_options(specification):
    options = specification.get("execution", {})
    if not isinstance(options, dict) or set(options) - {"encoding_cache", "conditioning", "diffusion", "memory_profile", "cuda_device", "vram_sample_interval_ms", "offline"}:
        raise ValueError("execution requires encoding_cache and optional conditioning/diffusion targets")
    cache = options.get("encoding_cache", "prefer")
    if cache not in {"prefer", "require", "disabled"}:
        raise ValueError("encoding_cache must be prefer, require or disabled")
    profile = options.get("memory_profile", "low_vram")
    device = options.get("cuda_device", 0)
    interval = options.get("vram_sample_interval_ms", 100)
    offline = options.get("offline", False)
    if profile not in {"low_vram", "resident_stage", "server_managed"}:
        raise ValueError("memory_profile must be low_vram or resident_stage")
    if type(device) is not int or device < 0:
        raise ValueError("cuda_device must be a nonnegative integer")
    if type(interval) is not int or not 50 <= interval <= 5000:
        raise ValueError("vram_sample_interval_ms must be an integer from 50 to 5000")
    if type(offline) is not bool:
        raise ValueError("offline must be true or false")
    result = {"encoding_cache": cache, "memory_profile": profile, "cuda_device": device,
              "vram_sample_interval_ms": interval, "offline": offline}
    for stage in ("conditioning", "diffusion"):
        target = options.get(stage, {})
        if not isinstance(target, dict) or set(target) - {"backend", "address"}:
            raise ValueError(f"execution.{stage} accepts only backend and address; models belong to the job")
        backend, address = target.get("backend", "local"), target.get("address")
        if backend not in {"local", "runpod_dedicated", "runpod_serverless", "comfyui"}:
            raise ValueError(f"Unsupported {stage} backend")
        if address is not None and (not isinstance(address, str) or not address.strip()):
            raise ValueError("Worker address must be nonempty text when supplied")
        result[stage] = {"backend": backend, "address": address}
    if profile=='server_managed' and any(result[stage]['backend']!='comfyui' for stage in ('conditioning','diffusion')):
        raise ValueError('server_managed memory is for ComfyUI only')
    return result


def resolve(document, specification, template=None, freeze=False):
    allowed = set(SOURCES) | {"precedence", "field_precedence", "template_id", "parent_review_generation_id", "request_id", "execution", "model_revision", "experiments"}
    unknown = set(specification) - allowed
    if unknown or "yaml" in specification or "template" in specification:
        raise ValueError(f"Unsupported parameter specification keys: {sorted(unknown | (set(specification) & {'yaml','template'}))}")
    execution_options(specification)
    revision = specification.get("model_revision")
    if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
        raise ValueError("model_revision must be a full 40-character commit SHA")
    sources = {name: specification.get(name, {}) for name in SOURCES}
    if any(not isinstance(value, dict) for value in sources.values()):
        raise ValueError("Parameter sources must be JSON objects")
    sources["defaults"] = {"duration": 360, **sources["defaults"]}
    sources["yaml"] = {k: v for k, v in document.values.items() if k in PARAMETERS}
    sources["template"] = template or {}
    for name, values in sources.items():
        if not isinstance(values, dict) or set(values) - PARAMETERS:
            raise ValueError(f"{name} must contain only supported generation fields")
    precedence = specification.get("precedence", list(SOURCES))
    field_precedence = specification.get("field_precedence", {})
    if not isinstance(field_precedence, dict) or set(field_precedence) - PARAMETERS:
        raise ValueError("field_precedence contains unknown fields")
    def validate_order(order):
        if not isinstance(order, list) or not order or any(not isinstance(s, str) or s not in SOURCES for s in order) or len(set(order)) != len(order):
            raise ValueError("Precedence must be a nonempty list of unique source names, lowest priority first")
        return order
    validate_order(precedence)
    for order in field_precedence.values():
        validate_order(order)
    values, provenance = {}, {}
    for key in sorted(PARAMETERS):
        order = [name for name in field_precedence.get(key, precedence) if name != "rerun"] + ["rerun"]
        for source in order:
            value = sources[source].get(key)
            if value is not None and value != "":
                values[key], provenance[key] = value, source
    for key in SEEDS:
        value = values.get(key, "auto")
        if value == "auto":
            if freeze:
                values[key] = str(secrets.randbits(64))
                provenance[key] = provenance.get(key, "automatic") + ":frozen"
            continue
        if isinstance(value, bool) or not str(value).isdigit() or not 0 <= int(value) < 2**64:
            raise ValueError(f"{key} must be auto or an unsigned 64-bit integer")
        values[key] = str(int(value))
    for key in INTEGERS | NUMBERS:
        if key not in values:
            continue
        raw = values[key]
        if isinstance(raw, bool):
            raise ValueError(f"{key} must be numeric, not boolean")
        if key in INTEGERS:
            if isinstance(raw, int):
                value = raw
            elif isinstance(raw, str) and re.fullmatch(r"[0-9]+", raw):
                value = int(raw)
            elif isinstance(raw, float) and math.isfinite(raw) and raw.is_integer():
                value = int(raw)
            else:
                raise ValueError(f"{key} must be a positive integer")
            if value <= 0:
                raise ValueError(f"{key} must be a positive integer")
            values[key] = value
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be numeric") from exc
        if not math.isfinite(value) or value < 0 or value == 0 and key == "duration":
            raise ValueError(f"{key} must be a finite number in its valid range")
        values[key] = value
    for key in PARAMETERS - SEEDS - INTEGERS - NUMBERS:
        if key in values and (not isinstance(values[key], str) or not values[key].strip()):
            raise ValueError(f"{key} must be text")
    return values, provenance


def missing_parameters(values):
    return sorted(REQUIRED - values.keys())


def output_changes(document, effective):
    """Numerically equal seeds/controls keep their original spelling and quoting."""
    if set(effective) - PARAMETERS:
        raise ValueError("Unknown generation fields: " + ", ".join(sorted(set(effective) - PARAMETERS)))
    changes = {}
    for key, value in effective.items():
        if key not in PARAMETERS:
            continue
        current = document.values.get(key)
        if current == value and type(current) is type(value):
            continue
        if key in SEEDS | INTEGERS | NUMBERS and current is not None and not isinstance(current, bool):
            try:
                equal = str(current).isdigit() and int(current) == int(value) if key in SEEDS else float(current) == float(value)
            except (ValueError, TypeError, OverflowError):
                equal = False
            if equal:
                continue
        changes[key] = value
    return changes
