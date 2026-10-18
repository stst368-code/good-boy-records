"""Exercise the execution boundary with real FLACs and a deterministic test worker.

This is not a MiniMax inference test. No GPU/model is emulated as a successful native run.
"""
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
import venv
from unittest.mock import patch

import numpy as np
import soundfile as sf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_02_generation"))
sys.path.insert(0, str(ROOT / "_98_database"))
sys.path.insert(0, str(ROOT / "_99_tools" / "_workers_infdit"))
sys.path.insert(0, str(ROOT / "_03_review" / "_generation_review"))
from _gbr_database.initialize import initialize
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.executor import execute, recover, read_events
from _gbr_generation.planner import BatchRejected, json_text, preflight, prepare_batch, verify_plan
from _gbr_worker.contract import MODEL, SMOKE_MODEL_REVISION, encoding_identity, validate_job
from _gbr_worker.hardware import gpu_lock
from _review.config import Config
from _review.db import Database
from _review.indexer import Indexer, metadata_for


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.built = self.root / "inputs"
        shutil.copytree(ROOT / "_02_generation" / "_examples" / "_local_smoke", self.built)
        self.source = self.built / "_yaml" / "gbr-local-smoke.yaml"
        # Make preservation harder: unknown data, BOM and CRLF.
        self.source.write_bytes(b"\xef\xbb\xbf" + self.source.read_bytes().replace(b"\n", b"\r\n") + b"private_note: .nan # do not normalize\r\n")
        self.original = self.source.read_bytes()
        self.database = self.root / "test.sqlite3"
        initialize(self.database)
        self.spec = json.loads((self.built / "parameters.json").read_text())
        self.calls = []

    def prepare(self, spec=None):
        spec = spec or self.spec
        catalogue = Catalogue(self.built)
        checked = preflight(catalogue.select(all_inputs=True), spec, Styles(self.built / "styles.csv"))
        return prepare_batch(checked, spec, self.root / "runtime", self.database, input_root=self.built)

    def worker(self, python, request, directory, fail=False):
        job = json.loads(request.read_text())
        self.calls.append(job)
        revision = job.get("model_revision") or SMOKE_MODEL_REVISION
        hardware = {"address": "local://test-host/GPU-test", "provider": "local", "provider_instance_id": None,
                    "gpu_uuid": "GPU-test", "gpu_name": "test boundary", "usable_vram_bytes": 12 * 1024**3}
        models = {"model": MODEL, "revision": revision, "adapter": "test-double"}
        events = [
            {"event": "worker", "at": "2026-10-17T12:00:00+00:00", "hardware": hardware, "versions": {}},
            {"event": "model_resolved", "models": models},
            {"event": "stage_end", "id": job["attempt_id"] + "-conditioning", "stage": "conditioning",
             "started_at": "2026-10-17T12:00:00+00:00", "completed_at": "2026-10-17T12:00:01+00:00", "duration_seconds": 1,
             "resource": {"device_id": "GPU-test", "baseline_used_bytes": None, "peak_allocated_bytes": 1234,
                "peak_reserved_bytes": 2048, "peak_observed_device_bytes": None, "sampling_interval_ms": 100,
                "coverage": "allocator_only", "offload": {}, "details": {}}}]
        (directory / "events.ndjson").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        if fail:
            raise RuntimeError("injected worker failure")
        wave = np.sin(np.arange(4410) * 2 * np.pi * 440 / 44100).astype(np.float32) * 0.1
        sf.write(str(directory / "audio.flac"), np.stack((wave, wave), axis=1), 44100, subtype="PCM_24")
        result = {"protocol": 1, "run_id": job["run_id"], "attempt_id": job["attempt_id"], "status": "succeeded",
                  "completed_at": "2026-10-17T12:00:01+00:00", "actual_parameters": job["parameters"], "models": models,
                  "hardware": hardware, "encoding": {"key": "test-key", "status": "miss"}}
        (directory / "result.json").write_text(json.dumps(result))

    def rows(self, sql):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute(sql).fetchall()

    def run_plan(self, path, **kwargs):
        return execute(path, worker_python=sys.executable, launcher=self.worker, **kwargs)

    def test_success_publishes_real_flac_and_preserves_source_lyrics_order_comments(self):
        path = self.prepare()
        result = self.run_plan(path)
        self.assertEqual(result["status"], "succeeded", result)
        output = Path(result["attempts"][0]["output"])
        yaml = next(p for p in output.glob("*.yaml") if p.name != "source.yaml")
        completed = yaml.read_bytes()
        self.assertEqual((output / "source.yaml").read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertTrue(completed.startswith(b"\xef\xbb\xbf"))
        self.assertIn(self.original.split(b"lyrics: |", 1)[1], completed)
        self.assertIn(b'generation_datetime: "2610171300"\r\ncaption:', completed)
        info = sf.info(str(next(output.glob("*.flac"))))
        self.assertEqual((info.samplerate, info.channels, info.subtype), (44100, 2, "PCM_24"))
        self.assertEqual(self.rows("SELECT status FROM generation_attempts"), [("succeeded",)])
        self.assertEqual(self.rows("SELECT estimated_cost,currency FROM generation_timing_spans WHERE parent_id IS NULL"), [(None, None)])
        self.assertEqual(self.rows("SELECT baseline_used_bytes,peak_allocated_bytes,peak_observed_device_bytes FROM generation_resource_measurements"), [(None, 1234, None)])
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])

    def test_completed_run_is_not_launched_twice(self):
        path = self.prepare()
        self.run_plan(path)
        self.assertEqual(self.run_plan(path)["attempts"], [])
        self.assertEqual(len(self.calls), 1)

    def test_failure_retains_metrics_without_completed_outputs_and_retry_keeps_seeds(self):
        path = self.prepare()
        result = execute(path, worker_python=sys.executable, launcher=lambda *args: self.worker(*args, fail=True))
        self.assertEqual(result["status"], "failed")
        self.assertFalse((path.parent / "outputs").exists())
        self.assertEqual(len(self.rows("SELECT * FROM generation_resource_measurements")), 1)
        self.assertEqual(self.run_plan(path)["attempts"], [])
        retried = self.run_plan(path, retry_failed=True)
        self.assertEqual(retried["status"], "succeeded", retried)
        self.assertEqual(self.calls[0]["parameters"], self.calls[1]["parameters"])
        self.assertNotEqual(self.calls[0]["attempt_id"], self.calls[1]["attempt_id"])
        self.assertEqual(self.rows("SELECT status FROM generation_attempts ORDER BY rowid"), [("failed",), ("succeeded",)])

    def test_model_revision_resolved_before_failure_is_frozen_for_retry(self):
        spec = deepcopy(self.spec)
        del spec["model_revision"]
        path = self.prepare(spec)
        execute(path, worker_python=sys.executable, launcher=lambda *args: self.worker(*args, fail=True))
        self.run_plan(path, retry_failed=True)
        self.assertIsNone(self.calls[0]["model_revision"])
        self.assertEqual(self.calls[1]["model_revision"], SMOKE_MODEL_REVISION)

    def test_unsupported_native_controls_rejected_before_claim(self):
        self.spec["batch"] = {"sampler": "unsupported-sampler"}
        with self.assertRaises(BatchRejected) as caught:
            self.prepare()
        self.assertIn("Unsupported native flow", str(caught.exception.report))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rows("SELECT COUNT(*) FROM generation_attempts"), [(0,)])

    def test_failed_tag_write_leaves_one_raw_and_retry_publishes_without_inference(self):
        path = self.prepare()
        with patch('mutagen.flac.FLAC.save', side_effect=OSError('injected tag failure')):
            failed = self.run_plan(path)
        self.assertEqual(failed['status'], 'failed')
        attempt = failed['attempts'][0]
        work = Path(attempt['diagnostics'])
        self.assertTrue((work / 'audio.flac').is_file())
        self.assertFalse(list(self.root.rglob('.gbr-*')))
        self.assertEqual(len(list(self.root.rglob('*.flac'))), 1)
        timings = self.rows("SELECT duration_seconds FROM generation_timing_spans WHERE stage='total'")
        retried = self.run_plan(path, retry_failed=True)
        self.assertEqual(retried['status'], 'succeeded', retried)
        self.assertEqual(retried['attempts'][0]['attempt_id'], attempt['attempt_id'])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.rows("SELECT duration_seconds FROM generation_timing_spans WHERE stage='total'"), timings)
        self.assertEqual(len(list(self.root.rglob('*.flac'))), 1)
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_cleanup_backfills_an_old_success_without_another_generation(self):
        from _gbr_generation.executor import cleanup_outputs
        raw = []
        def worker(*args):
            self.worker(*args)
            raw.append((args[2] / 'audio.flac').read_bytes())
        path = self.prepare()
        result = execute(path, worker_python=sys.executable, launcher=worker)
        work = Path(result['attempts'][0]['diagnostics'])
        (work / 'audio.flac').write_bytes(raw[0])
        self.assertFalse((work / 'cleanup-pending.json').exists())
        cleaned = cleanup_outputs(path)
        self.assertEqual(cleaned['cleaned_attempts'], [result['attempts'][0]['attempt_id']])
        self.assertFalse((work / 'audio.flac').exists())
        self.assertEqual(len(self.calls), 1)

    def test_inline_lyric_tags_are_rejected_without_changing_lyrics(self):
        self.source.write_bytes(self.original.replace(b"[verse]\r\n  A little", b"[verse] A little"))
        raw = self.source.read_bytes()
        with self.assertRaises(BatchRejected) as caught:
            self.prepare()
        self.assertIn("section tags", str(caught.exception.report))
        self.assertEqual(self.source.read_bytes(), raw)
        self.assertEqual(self.calls, [])

    def test_runpod_does_not_fall_back_to_local_execution(self):
        self.spec["execution"]["conditioning"] = {"backend": "runpod_serverless", "address": "test"}
        path = self.prepare()
        with self.assertRaisesRegex(ValueError, "RunPod transport"):
            self.run_plan(path)

    def test_running_job_requires_explicit_recovery(self):
        path = self.prepare()
        run = verify_plan(path)["runs"][0]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("UPDATE generation_runs SET status='running'")
        with self.assertRaisesRegex(ValueError, "interrupted"):
            self.run_plan(path)
        self.assertEqual(recover(path, run["id"])["status"], "failed")
        self.assertEqual(self.run_plan(path, retry_failed=True)["status"], "succeeded")

    def test_published_bundle_recovers_without_another_generation(self):
        path = self.prepare()
        result = self.run_plan(path)
        run_id = result["attempts"][0]["run_id"]
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("DELETE FROM generation_artifacts")
            conn.execute("UPDATE generation_runs SET status='failed'")
        with self.assertRaisesRegex(ValueError, "published bundle"):
            self.run_plan(path, retry_failed=True)
        self.assertEqual(recover(path, run_id)["status"], "succeeded")
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(len(self.rows("SELECT * FROM generation_artifacts")), 4)

    def test_changed_output_bundle_cannot_be_recovered(self):
        path = self.prepare()
        result = self.run_plan(path)
        output = Path(result["attempts"][0]["output"])
        (output / "source.yaml").write_bytes(b"tampered")
        with self.assertRaisesRegex(ValueError, "bundle changed"):
            recover(path, result["attempts"][0]["run_id"])

    def test_real_flac_is_indexed_and_linked_to_native_run_by_hash(self):
        path = self.prepare()
        result = self.run_plan(path)
        output = Path(result["attempts"][0]["output"])
        audio = next(output.glob("*.flac"))
        config = Config(self.root / "review-config.json")
        config.values["input_directories"] = [str(output)]
        db = Database(self.database)
        Indexer(config, db).index(audio)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM generation_output_links"), [(1,)])
        self.assertEqual(db.one("SELECT genre FROM analytics_dataset")["genre"], "Choral")
        self.assertEqual(metadata_for(audio)[3]["variant_id"], 1)
        # File content, rather than tags alone, must prove the link.
        with closing(sqlite3.connect(self.database)) as conn, conn:
            conn.execute("DELETE FROM generation_output_links")
            conn.execute("UPDATE generation_artifacts SET sha256='wrong' WHERE kind='audio'")
        Indexer(config, db).index(audio)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM generation_output_links"), [(0,)])

    def test_truncated_event_log_keeps_complete_measurements(self):
        directory = self.root / "events"
        directory.mkdir()
        (directory / "events.ndjson").write_text('{"event":"good"}\n{"event":')
        self.assertEqual(read_events(directory), [{"event": "good"}])

    def test_subprocess_preserves_the_selected_virtual_environment(self):
        environment = self.root / "worker-env"
        venv.EnvBuilder(with_pip=False, symlinks=os.name != "nt").create(environment)
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        worker = self.root / "subprocess-worker"
        worker.mkdir()
        (worker / "worker.py").write_text("import sys\nprint('selected-prefix=' + sys.prefix, flush=True)\nraise SystemExit(1)\n")
        path = self.prepare()
        with patch("_gbr_generation.executor.WORKER", worker):
            result = execute(path, worker_python=python)
        self.assertEqual(result["status"], "failed")
        log = (Path(result["attempts"][0]["diagnostics"]) / "stderr.log").read_text()
        self.assertIn("selected-prefix=" + str(environment), log)

    def test_cache_key_covers_conditioning_but_not_diffusion_seed(self):
        path = self.prepare()
        run = verify_plan(path)["runs"][0]
        job = {"parameters": run["effective"], "lyrics": "Test"}
        original = encoding_identity(job, SMOKE_MODEL_REVISION, {})[0]
        job["parameters"]["sampler_seed"] = "123"
        self.assertEqual(original, encoding_identity(job, SMOKE_MODEL_REVISION, {})[0])
        for field, value in [("encoder_seed", "123"), ("caption", "other"), ("duration", 31), ("text_encoder", "fp32")]:
            modified = deepcopy(job)
            modified["parameters"][field] = value
            self.assertNotEqual(original, encoding_identity(modified, SMOKE_MODEL_REVISION, {})[0])
        self.assertNotEqual(original, encoding_identity(job, "a" * 40, {})[0])

    def test_gpu_lock_rejects_another_process_and_releases_after_exit(self):
        code = "from _gbr_worker.hardware import gpu_lock\nwith gpu_lock('test-GPU-lock'):\n print('acquired')\n"
        environment = dict(__import__("os").environ, PYTHONPATH=str(ROOT / "_99_tools" / "_workers_infdit"))
        with gpu_lock("test-GPU-lock"):
            child = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True)
            self.assertNotEqual(child.returncode, 0)
            self.assertIn("already using", child.stderr)
        child = subprocess.run([sys.executable, "-c", code], env=environment, capture_output=True, text=True)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
