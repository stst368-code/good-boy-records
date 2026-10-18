"""Durable stage events, allocator peaks and sampled device-wide memory."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import sys
import threading
import time
import uuid


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Journal:
    def __init__(self, path, torch=None, device=None, hardware=None, interval_ms=100):
        self.stream = path.open("x", encoding="utf-8", newline="\n")
        self.torch, self.device, self.interval = torch, device, interval_ms
        self.hardware = hardware
        self.stack = []
        self.nvml = self.nvml_device = None
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            # Map by UUID, never by ordinal (CUDA_VISIBLE_DEVICES can reorder GPUs).
            gpu_uuid = hardware.get("gpu_uuid") if hardware else None
            if gpu_uuid:
                self.nvml_device = pynvml.nvmlDeviceGetHandleByUUID(gpu_uuid)
                driver = pynvml.nvmlSystemGetDriverVersion()
                hardware["nvml_driver"] = driver.decode() if isinstance(driver, bytes) else driver
        except Exception:
            self.nvml_device = None

    def emit(self, event, **values):
        row = {"event": event, "at": now(), **values}
        self.stream.write(json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n")
        self.stream.flush()
        print(f"[{row['at']}] {event} {values.get('stage', '')}", file=sys.stderr, flush=True)
        return row

    @contextmanager
    def span(self, stage, *, offload=None, gpu=True):
        import psutil
        identifier, started, clock = uuid.uuid4().hex, now(), time.perf_counter()
        parent_id = self.stack[-1]["id"] if self.stack else None
        if self.stack:
            self.stack[-1]["children"] = True
        entry = {"id": identifier, "children": False}
        self.stack.append(entry)
        stop = threading.Event()
        samples, rss_samples = [], []
        sample_errors = []
        try:
            process = psutil.Process()
        except (OSError, psutil.Error) as exc:
            process = None
            sample_errors.append(type(exc).__name__)
        def sample():
            if process is not None:
                try:
                    rss_samples.append(process.memory_info().rss)
                except Exception as exc:
                    sample_errors.append(type(exc).__name__)
            if self.nvml_device is not None:
                try:
                    samples.append(self.nvml.nvmlDeviceGetMemoryInfo(self.nvml_device).used)
                except Exception as exc:
                    sample_errors.append(type(exc).__name__)
        def loop():
            while not stop.wait(self.interval / 1000):
                sample()
        if gpu and self.torch is not None:
            self.torch.cuda.synchronize(self.device)
            self.torch.cuda.reset_peak_memory_stats(self.device)
        sample()
        self.emit("stage_start", id=identifier, parent_id=parent_id, stage=stage, started_at=started)
        thread = threading.Thread(target=loop, daemon=True)
        thread.start()
        error = None
        try:
            yield
        except BaseException as exc:
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            allocated = reserved = None
            if gpu and self.torch is not None:
                try:
                    self.torch.cuda.synchronize(self.device)
                    allocated = self.torch.cuda.max_memory_allocated(self.device)
                    reserved = self.torch.cuda.max_memory_reserved(self.device)
                except Exception as exc:
                    sample_errors.append(type(exc).__name__)
            stop.set()
            thread.join(timeout=5)
            sample()
            self.stack.pop()
            self.emit("stage_end", id=identifier, parent_id=parent_id, includes_children=entry["children"], stage=stage, started_at=started, completed_at=now(),
                      duration_seconds=time.perf_counter() - clock, error=error,
                      resource={"device_id": (self.hardware or {}).get("gpu_uuid") or str(self.device),
                          "baseline_used_bytes": samples[0] if samples else None,
                          "peak_allocated_bytes": allocated, "peak_reserved_bytes": reserved,
                          "peak_observed_device_bytes": max(samples) if samples else None,
                          "sampling_interval_ms": self.interval, "coverage": "sampled_device" if samples else "allocator_only" if allocated is not None else "unavailable",
                          "offload": offload or {}, "details": {"device_sample_count": len(samples),
                              "sample_errors": sample_errors, "process_peak_sampled_rss_bytes": max(rss_samples) if rss_samples else None,
                              "device_samples_include_other_processes": True}})

    def close(self):
        self.stream.close()
        if self.nvml is not None:
            self.nvml.nvmlShutdown()
