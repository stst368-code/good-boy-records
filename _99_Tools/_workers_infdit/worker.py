"""One job per process. No configured worker names or model inventories."""
import argparse
import json
import os
from pathlib import Path
import sys
import traceback

from gbr_worker.contract import PROTOCOL, canonical, execution_target, validate_job
from gbr_worker.hardware import check_install, discover, gpu_lock, restore_pod_environment
from gbr_worker.telemetry import Journal, now


def probe(device_index):
    import torch
    installed = check_install()
    if not torch.cuda.is_available():
        raise RuntimeError("A working CUDA PyTorch installation and NVIDIA GPU are required; no CPU fallback")
    device = torch.device("cuda", device_index)
    torch.cuda.set_device(device)
    if not torch.cuda.is_bf16_supported():
        raise RuntimeError("The first local profile requires BF16 support")
    # Exercise CUDA, rather than relying only on an enumerated device name.
    value = torch.ones((16, 16), device=device, dtype=torch.bfloat16)
    (value @ value).sum().item()
    del value
    torch.cuda.empty_cache()
    return torch, device, installed, discover(torch, device)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.add_argument("--cuda-device", type=int, default=0)
    run = sub.add_parser("run")
    run.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    restore_pod_environment()
    if args.command == "doctor":
        _, _, installed, hardware = probe(args.cuda_device)
        print(canonical({"protocol": PROTOCOL, "hardware": hardware, "versions": installed}))
        return 0
    job = json.loads(args.request.read_text(encoding="utf-8"))
    target = Path(job["result_directory"]).resolve(strict=True)
    journal = Journal(target / "events.ndjson")
    result = {"protocol": PROTOCOL, "run_id": job.get("run_id"), "attempt_id": job.get("attempt_id"), "started_at": now(), "status": "failed"}
    try:
        backend, _ = execution_target(job["execution"])
        validate_job(job, require_local=backend == "local")
        if backend == "runpod_dedicated" and not os.environ.get("RUNPOD_POD_ID"):
            raise RuntimeError("A RunPod request requires a RunPod pod; refusing a local fallback")
        torch, device, installed, hardware = probe(job["execution"].get("cuda_device", 0))
        journal.close()
        # Continue the same journal after CUDA discovery, retaining early failure evidence.
        (target / "events.ndjson").rename(target / "startup.ndjson")
        journal = Journal(target / "events.ndjson", torch, device, hardware, job["execution"].get("vram_sample_interval_ms", 100))
        result.update(hardware=hardware, versions=installed)
        journal.emit("worker", hardware=hardware, versions=installed)
        with gpu_lock(hardware["gpu_uuid"] or hardware["address"]):
            with journal.span("hardware_check", gpu=False):
                minimum = 24 if job["execution"].get("memory_profile") == "resident_stage" else 8
                if hardware["free_vram_bytes_at_start"] < minimum * 1024**3:
                    raise RuntimeError(f"This memory profile requires at least {minimum} GiB free VRAM")
            from gbr_worker.native import run as generate
            result.update(generate(job, torch, device, journal, installed, hardware, target / "audio.flac"))
        result["status"] = "succeeded"
    except BaseException as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        journal.emit("failure", error=result["error"])
        traceback.print_exc(file=sys.stderr)
    finally:
        result["completed_at"] = now()
        (target / "result.json").write_text(canonical(result) + "\n", encoding="utf-8")
        journal.close()
    return 0 if result["status"] == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())

