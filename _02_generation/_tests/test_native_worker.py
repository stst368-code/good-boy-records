"""CPU checks for native dependencies/cache. Never downloads model weights."""
from contextlib import contextmanager
from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_99_tools" / "_workers_infdit"))
from _gbr_worker import cache
from _gbr_worker.contract import MODEL, SMOKE_MODEL_REVISION, encoding_identity
from _gbr_worker.telemetry import Journal

HAS_NATIVE = all(importlib.util.find_spec(name) is not None for name in ("torch", "diffusers", "transformers", "safetensors"))


@unittest.skipUnless(HAS_NATIVE, "Install the native worker environment for CPU adapter/cache checks")
class NativeWorkerTests(unittest.TestCase):
    def setUp(self):
        import torch
        self.torch = torch
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.job = {"protocol": 1, "run_id": "cache-test", "attempt_id": "cache-attempt",
                    "model_revision": SMOKE_MODEL_REVISION, "cache_root": str(self.root / "cache"),
                    "lyrics": "[verse]\nA short test\n", "execution": {"encoding_cache": "require", "memory_profile": "low_vram"},
                    "parameters": {"model": MODEL, "caption": "A test", "text_encoder": "bf16", "dit": "bf16",
                                   "encoder_cfg": 1.5, "encoder_seed": "123", "top_k": 50, "duration": 30}}
        self.key, self.identity = encoding_identity(self.job, SMOKE_MODEL_REVISION, {"torch": torch.__version__})
        self.tensor = torch.zeros((1, 2, 32768), dtype=torch.bfloat16)

    def test_pinned_api_imports_and_native_control_constants(self):
        from _gbr_worker.hardware import check_install
        from diffusers.modular_pipelines.minimax_music3 import encoders
        self.assertEqual(check_install()["transformers"], "5.20.1")
        self.assertEqual((encoders._AR_CFG_SCALE, encoders._AR_CFG_TOP_K, encoders._AR_SAMPLING_TOP_K), (1.5, 50, 50))

    def test_safetensors_round_trip_and_checksum_corruption(self):
        cache.save(self.job["cache_root"], self.key, self.identity, self.tensor, {"run_id": "origin"})
        tensor, record = cache.load(self.job["cache_root"], self.key, self.identity, self.torch)
        self.assertTrue(self.torch.equal(self.tensor, tensor))
        self.assertEqual(record["origin"]["run_id"], "origin")
        path = Path(self.job["cache_root"]) / self.key / "conditioning.safetensors"
        path.write_bytes(path.read_bytes() + b"wrong")
        with self.assertRaisesRegex(ValueError, "checksum"):
            cache.load(self.job["cache_root"], self.key, self.identity, self.torch)

    def test_cache_wrong_identity_dtype_shape_and_nonfinite_rejected(self):
        cache.save(self.job["cache_root"], self.key, self.identity, self.tensor, {})
        identity = deepcopy(self.identity)
        identity["lyrics"] = "changed"
        with self.assertRaisesRegex(ValueError, "identity"):
            cache.load(self.job["cache_root"], self.key, identity, self.torch)
        for tensor in [self.torch.zeros((1, 2, 8)), self.torch.zeros((1, 2, 32768), dtype=self.torch.float16),
                       self.torch.full((1, 2, 32768), float("nan"))]:
            with self.assertRaises(ValueError):
                cache.validate_tensor(tensor, self.torch)

    def test_required_cache_miss_stops_before_loading_components(self):
        from _gbr_worker.native import run
        from diffusers.modular_pipelines.minimax_music3.modular_blocks_minimax_music3 import MiniMaxMusic3SemanticGenerationStep
        journal = Journal(self.root / "events.ndjson")
        versions = {name: "test" for name in ("torch", "diffusers", "transformers", "safetensors", "tokenizers", "regex", "accelerate")}
        try:
            with patch("_gbr_worker.native.resolve_model", return_value=(SMOKE_MODEL_REVISION, self.root)), patch.object(MiniMaxMusic3SemanticGenerationStep, "init_pipeline", side_effect=AssertionError("Weights must not load")):
                with self.assertRaisesRegex(ValueError, "cache is missing"):
                    run(self.job, self.torch, self.torch.device("cpu"), journal, versions, {}, self.root / "audio.flac")
        finally:
            journal.close()
        self.assertFalse((self.root / "audio.flac").exists())

    def test_failed_nested_spans_keep_parentage_and_null_gpu_coverage(self):
        journal = Journal(self.root / "events.ndjson", interval_ms=50)
        try:
            with self.assertRaisesRegex(RuntimeError, "test failure"):
                with journal.span("conditioning", gpu=False):
                    with journal.span("load_language_model", gpu=False):
                        raise RuntimeError("test failure")
        finally:
            journal.close()
        events = [json.loads(line) for line in (self.root / "events.ndjson").read_text().splitlines()]
        ends = [e for e in events if e["event"] == "stage_end"]
        self.assertEqual(ends[0]["parent_id"], ends[1]["id"])
        self.assertTrue(ends[1]["includes_children"])
        self.assertIn("test failure", ends[0]["error"])
        self.assertEqual(ends[0]["resource"]["coverage"], "unavailable")
        self.assertIsNone(ends[0]["resource"]["peak_allocated_bytes"])


if __name__ == "__main__":
    unittest.main()
