"""Pinned stock Diffusers blocks, with separate encoder and diffusion RNGs."""
import gc
from datetime import datetime, timezone
from pathlib import Path

from . import cache
from .contract import ADAPTER, DIFFUSERS_REVISION, encoding_identity


def resolve_model(job):
    from huggingface_hub import HfApi, hf_hub_download
    model = job["parameters"]["model"]
    revision = job.get("model_revision")
    offline = job["execution"].get("offline", False)
    if not revision:
        if offline:
            raise ValueError("Offline jobs require an explicit model revision")
        revision = HfApi().model_info(model).sha
    index = Path(hf_hub_download(model, "modular_model_index.json", revision=revision, local_files_only=offline))
    return revision, index.parent


def run(job, torch, device, journal, package_versions, hardware, output):
    from diffusers import ClassifierFreeGuidance
    from diffusers.hooks.group_offloading import apply_group_offloading
    from diffusers.modular_pipelines.minimax_music3.modular_blocks_minimax_music3 import (
        MiniMaxMusic3SemanticGenerationStep, MiniMaxMusic3CoreDenoiseStep)
    from diffusers.modular_pipelines.minimax_music3.decoders import MiniMaxMusic3VocoderDecodeStep
    import numpy as np
    import soundfile as sf

    p, execution = job["parameters"], job["execution"]
    dtype = {"bf16": torch.bfloat16, "fp32": torch.float32}
    low = execution.get("memory_profile", "low_vram") == "low_vram"
    policy = execution["encoding_cache"]
    with journal.span("resolve_model", gpu=False):
        revision, snapshot_root = resolve_model(job)
        actual = {"model": p["model"], "revision": revision, "adapter": ADAPTER,
                  "diffusers_revision": DIFFUSERS_REVISION, "text_encoder_dtype": p["text_encoder"],
                  "dit_dtype": p["dit"], "vocoder_dtype": "fp32", "memory_profile": execution["memory_profile"]}
        journal.emit("model_resolved", models=actual)
    cache_versions = {name: package_versions[name] for name in ("torch", "diffusers", "transformers", "safetensors", "tokenizers", "regex", "accelerate")}
    key, identity = encoding_identity(job, revision, cache_versions)
    hiddens, cached, cache_error = None, None, None
    if policy != "disabled":
        with journal.span("cache_lookup", gpu=False):
            try:
                hiddens, cached = cache.load(job["cache_root"], key, identity, torch)
            except (OSError, ValueError, KeyError) as exc:
                cache_error = str(exc)
                if policy == "require":
                    raise ValueError("Required conditioning cache is invalid: " + str(exc)) from exc
                journal.emit("cache_invalid", key=key, error=cache_error)
                # Retain evidence and allow a new valid entry at this key.
                import uuid
                directory = Path(job["cache_root"]) / key
                if directory.exists():
                    directory.rename(directory.with_name(key + ".invalid-" + uuid.uuid4().hex))
        if policy == "require" and hiddens is None:
            raise ValueError("Required conditioning cache is missing; no GPU inference was started")
    cache_status = "hit" if hiddens is not None else "disabled" if policy == "disabled" else "miss"
    journal.emit("cache", key=key if policy != "disabled" else None, status=cache_status,
                 origin=cached.get("origin") if cached else None, invalid_reason=cache_error)

    def load_components(block, names, precision):
        pipe = block.init_pipeline(pretrained_model_name_or_path=str(snapshot_root))
        for name in names:
            spec = pipe._component_specs.get(name)
            if spec is None or spec.subfolder != name:
                raise RuntimeError(f"Unexpected native component layout for {name}; refusing alternate weights")
        pipe.load_components(names=names, pretrained_model_name_or_path=p["model"], revision=revision,
                             dtype=precision, low_cpu_mem_usage=True, local_files_only=execution.get("offline", False))
        missing = [name for name in names if getattr(pipe, name, None) is None]
        if missing:
            raise RuntimeError("Native component loading failed: " + ", ".join(missing) + "; inspect worker stderr")
        return pipe

    def load(block, names, precision):
        with journal.span("load_" + names[0], gpu=False):
            return load_components(block, names, precision)

    def release(pipe):
        pipe.unload_components(list(pipe.components))
        gc.collect()
        torch.cuda.empty_cache()

    def semantic():
        pipe = load(MiniMaxMusic3SemanticGenerationStep(), ["tokenizer", "language_model", "rvq_depth_decoder"], dtype[p["text_encoder"]])
        try:
            if low:
                pipe.rvq_depth_decoder.to(device)
                apply_group_offloading(pipe.language_model, onload_device=device, offload_device=torch.device("cpu"),
                                       offload_type="leaf_level", use_stream=False)
            else:
                pipe.to(device)
            with torch.inference_mode():
                tensor = pipe(prompt=p["caption"], lyrics=job["lyrics"], audio_duration=p["duration"],
                              generator=torch.Generator(device="cpu").manual_seed(int(p["encoder_seed"])), output="frame_hiddens")
            return tensor.detach().cpu().contiguous()
        finally:
            release(pipe)

    def diffuse(tensor):
        pipe = load(MiniMaxMusic3CoreDenoiseStep(), ["condition_encoder", "transformer", "scheduler"], dtype[p["dit"]])
        try:
            if type(pipe.scheduler).__name__ != "FlowMatchEulerDiscreteScheduler":
                raise RuntimeError("Loaded scheduler does not match flow_match_euler")
            pipe.update_components(guider=ClassifierFreeGuidance(guidance_scale=p["sampler_cfg"]))
            if low and p["dit"] == "fp32":
                pipe.condition_encoder.to(device)
                apply_group_offloading(pipe.transformer, onload_device=device, offload_device=torch.device("cpu"),
                                       offload_type="leaf_level", use_stream=False)
            else:
                pipe.to(device)
            with torch.inference_mode():
                chunks = pipe(frame_hiddens=tensor.to(device=device, dtype=dtype[p["dit"]]),
                              num_inference_steps=p["sampler_steps"],
                              generator=torch.Generator(device="cpu").manual_seed(int(p["sampler_seed"])), output="latent_chunks")
            return [value.detach().cpu() for value in chunks]
        finally:
            release(pipe)

    def decode(chunks):
        pipe = load(MiniMaxMusic3VocoderDecodeStep(), ["vocoder"], torch.float32)
        try:
            pipe.to(device)
            with torch.inference_mode():
                audios = pipe(latent_chunks=[value.to(device) for value in chunks], output_type="np", output="audios")
            return audios
        finally:
            release(pipe)

    if hiddens is None:
        # Conservative host-memory reservation; fail before allocating the language model.
        import psutil
        required = (20 if p["text_encoder"] == "bf16" else 38) * 1024**3
        if psutil.virtual_memory().available < required:
            raise RuntimeError(f"Conditioning needs at least {required // 1024**3} GiB currently available system RAM in this profile")
        with journal.span("conditioning", offload={"language_model": "leaf_cpu" if low else "resident_gpu", "streams": False}):
            hiddens = semantic()
            cache.validate_tensor(hiddens, torch)
        if policy != "disabled":
            with journal.span("cache_write", gpu=False):
                cache.save(job["cache_root"], key, identity, hiddens,
                           {"run_id": job["run_id"], "attempt_id": job["attempt_id"], "hardware": hardware, "created_at": datetime.now(timezone.utc).isoformat()})
    with journal.span("diffusion", offload={"transformer": "leaf_cpu" if low and p["dit"] == "fp32" else "resident_gpu"}):
        chunks = diffuse(hiddens)
    del hiddens
    with journal.span("decode", offload={"vocoder": "resident_gpu"}):
        audios = decode(chunks)
    del chunks
    with journal.span("write_flac", gpu=False):
        if audios.ndim != 3 or audios.shape[0] != 1 or audios.shape[1] != 2 or audios.shape[2] == 0 or not np.isfinite(audios).all():
            raise RuntimeError("Native decoder returned invalid stereo audio")
        sf.write(str(output), audios[0].T, 44100, format="FLAC", subtype="PCM_24")
        info = sf.info(str(output))
        if info.samplerate != 44100 or info.channels != 2 or info.subtype != "PCM_24" or info.frames != audios.shape[2]:
            raise RuntimeError("FLAC verification failed")
    return {"models": actual, "audio": {"sample_rate": info.samplerate, "channels": info.channels,
                "frames": info.frames, "duration_seconds": info.duration, "subtype": info.subtype},
            "encoding": {"key": key if policy != "disabled" else None, "status": cache_status,
                         "origin": cached.get("origin") if cached else None}, "actual_parameters": dict(p)}
