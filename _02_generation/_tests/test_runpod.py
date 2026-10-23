"""Exercise SSH dispatch and real detached processes with a FLAC boundary test double.

No RunPod resource, CUDA runtime or MiniMax inference is used by these tests.
"""
from contextlib import closing, redirect_stderr
import io
import hashlib
import json
import os
from pathlib import Path
import shutil
import shlex
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "_02_generation"))
sys.path.insert(0, str(ROOT / "_98_database"))
sys.path.insert(0, str(ROOT / "_99_tools/_workers_infdit"))
from _gbr_database.initialize import initialize
from _gbr_generation.catalogue import Catalogue, Styles
from _gbr_generation.executor import execute, recover
from _gbr_generation.planner import preflight, prepare_batch, verify_plan
from _gbr_worker.contract import canonical, execution_target, ssh_endpoint
from _gbr_worker.hardware import discover, restore_pod_environment
from _gbr_worker.runpod import RemotePending, RunpodClient, source_bundle
from first_local_test import prepare_inputs

ADDRESS = "ssh://root@203.0.113.10:17445"


class RunpodContractTests(unittest.TestCase):
    def test_endpoint_rejects_passwords_commands_proxy_and_malformed_addresses(self):
        for address in (None, "", "root@203.0.113.10", "ssh://root:password@host:22", "ssh://root@host/path",
                        "ssh://root@host?x=y", "ssh://root@host#x", "ssh://root@host:0", "ssh://root@host:65536",
                        "ssh://root@host;touch", "ssh://-oProxyCommand@host", "ssh://user@ssh.runpod.io",
                        "ssh://root@host\n", "ssh://root@host:bad"):
            with self.subTest(address=address), self.assertRaises(ValueError):
                ssh_endpoint(address)
        self.assertEqual(ssh_endpoint(ADDRESS), ("root", "203.0.113.10", 17445))

    def test_split_and_serverless_never_fall_back_to_local(self):
        remote = {"backend": "runpod_dedicated", "address": ADDRESS}
        for target in ({"backend": "local"}, {"backend": "runpod_serverless", "address": "endpoint"},
                       {"backend": "runpod_dedicated", "address": "ssh://root@other:22"}):
            with self.assertRaisesRegex(ValueError, "RunPod transport"):
                execution_target({"conditioning": remote, "diffusion": target})

    def test_cloud_smoke_plan_uses_resident_stages_without_changing_controls(self):
        local, _, _, _ = prepare_inputs()
        cloud, spec, _, selection = prepare_inputs(runpod=ADDRESS, encoding_cache="disabled")
        self.assertTrue(cloud[0].ready, cloud[0].summary())
        self.assertEqual(local[0].effective, cloud[0].effective)
        self.assertEqual(spec["execution"]["memory_profile"], "resident_stage")
        self.assertEqual(selection["purpose"], "runpod_hardware_test")

    def test_bundle_contains_only_worker_sources_and_pins(self):
        bundle = source_bundle()
        self.assertIn("pod_agent.py", bundle["files"])
        self.assertIn("requirements.txt", bundle["files"])
        self.assertFalse(any("runtime" in path or ".sqlite" in path or ".venv" in path for path in bundle["files"]))
        self.assertEqual(len(bundle["release"]), 64)

    def test_worker_detects_pod_and_allowlists_metadata_without_secrets(self):
        props = SimpleNamespace(uuid="GPU-test", name="test GPU", major=8, minor=6)
        torch = SimpleNamespace(cuda=SimpleNamespace(get_device_properties=lambda _: props,
                                mem_get_info=lambda _: (40 * 1024**3, 48 * 1024**3)), version=SimpleNamespace(cuda="12.8"))
        with patch.dict(os.environ, {"RUNPOD_POD_ID": "test-pod", "RUNPOD_API_KEY": "do-not-record",
                                     "PUBLIC_KEY": "do-not-record", "RUNPOD_DC_ID": "test-dc"}, clear=True):
            hardware = discover(torch, SimpleNamespace(index=0))
        self.assertEqual(hardware["provider"], "runpod")
        self.assertEqual(hardware["address"], "runpod://test-pod/GPU-test")
        self.assertEqual(hardware["provider_instance_id"], "test-pod")
        self.assertEqual(hardware["provider_details"]["RUNPOD_DC_ID"], "test-dc")
        self.assertNotIn("do-not-record", canonical(hardware))

    def test_ssh_environment_restore_reads_only_known_values_without_executing_shell(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "environment"
            path.write_text('export RUNPOD_POD_ID="test-pod"\nexport RUNPOD_DC_ID="dc"\n'
                'export RUNPOD_API_KEY="do-not-import"\nexport PUBLIC_KEY="do-not-import"\n'
                'export CUDA_VERSION="12.8"\nexport RUNPOD_CPU_COUNT="$(do-not-execute)"\n')
            with patch.dict(os.environ, {"RUNPOD_DC_ID": "already-set"}, clear=True):
                restore_pod_environment(path)
                self.assertEqual(os.environ["RUNPOD_POD_ID"], "test-pod")
                self.assertEqual(os.environ["RUNPOD_DC_ID"], "already-set")
                self.assertEqual(os.environ["RUNPOD_CPU_COUNT"], "$(do-not-execute)")
                self.assertNotIn("RUNPOD_API_KEY", os.environ)
                self.assertNotIn("PUBLIC_KEY", os.environ)


@unittest.skipIf(os.name == "nt", "Detached Linux pod supervisor requires POSIX")
class RunpodTransportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.remote = self.root / "remote"
        bundle = source_bundle()
        self.release = bundle["release"]
        release = self.remote / "releases" / self.release
        for name, content in bundle["files"].items():
            path = release / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        python = self.remote / ".venv/bin/python"
        python.parent.mkdir(parents=True)
        # A symlink under another .venv loses the controller's installed test dependencies.
        python.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
        python.chmod(0o700)
        # This fixture exercises the process/output contract, not native generation quality.
        (release / "worker.py").write_text('''import json,sys,time
from pathlib import Path
import numpy as np
import soundfile as sf
job=json.loads(Path(sys.argv[2]).read_text())
out=Path(job["result_directory"])
with (out.parent.parent/"launches").open("a") as f: f.write(job["attempt_id"]+"\\n")
time.sleep(0.1)
hardware={"address":"runpod://test-pod/GPU-test","provider":"runpod","provider_instance_id":"test-pod","gpu_uuid":"GPU-test","gpu_name":"test boundary","usable_vram_bytes":48*1024**3}
models={"model":job["parameters"]["model"],"revision":job["model_revision"],"adapter":"test-double"}
events=[{"event":"worker","at":"2026-10-17T12:00:00+00:00","hardware":hardware,"versions":{}},{"event":"model_resolved","models":models}]
(out/"events.ndjson").write_text("\\n".join(json.dumps(e) for e in events)+"\\n")
wave=np.zeros((4410,2),dtype=np.float32)
sf.write(out/"audio.flac",wave,44100,subtype="PCM_24")
result={"protocol":1,"run_id":job["run_id"],"attempt_id":job["attempt_id"],"status":"succeeded","completed_at":"2026-10-17T12:00:01+00:00","actual_parameters":job["parameters"],"models":models,"hardware":hardware}
(out/"result.json").write_text(json.dumps(result))
print("remote boundary fixture completed",flush=True)
''')
        self.ssh = self.root / "fake-ssh"
        self.ssh.write_text("#!" + sys.executable + "\nimport os,shlex,subprocess,sys\n"
            + "args=shlex.split(sys.argv[-1])\n"
            + f"args=[a.replace('/workspace/good-boy-records/_99_Tools/_workers_infdit',{str(self.remote)!r}) for a in args]\n"
            + "raise SystemExit(subprocess.call(args,env=dict(os.environ,RUNPOD_POD_ID='test-pod')))\n")
        self.ssh.chmod(0o700)
        self.built = self.root / "inputs"
        shutil.copytree(ROOT / "_02_generation/_examples/_local_smoke", self.built)
        self.source = self.built / "_yaml/gbr-local-smoke.yaml"
        self.source.write_bytes(b"\xef\xbb\xbf" + self.source.read_bytes().replace(b"\n", b"\r\n") +
                                b"private_note: .nan # retain exactly\r\n")
        self.original = self.source.read_bytes()
        self.database = self.root / "shared.sqlite3"
        initialize(self.database)
        spec = json.loads((self.built / "parameters.json").read_text())
        spec["execution"].update(memory_profile="resident_stage",
            conditioning={"backend": "runpod_dedicated", "address": ADDRESS},
            diffusion={"backend": "runpod_dedicated", "address": ADDRESS})
        catalogue = Catalogue(self.built)
        checked = preflight(catalogue.select(all_inputs=True), spec, Styles(self.built / "styles.csv"))
        self.manifest = prepare_batch(checked, spec, self.root / "local", self.database, input_root=self.built)

    def client(self, address=ADDRESS, identity_file=None, *, release=None):
        client = RunpodClient(address, identity_file, release=release)
        client.command[0] = str(self.ssh)
        original_run = client.run
        client.run = lambda job, directory, **kwargs: original_run(job, directory, poll_seconds=0.01, **kwargs)
        return client

    def execute(self, **kwargs):
        with patch("_gbr_generation.executor.RunpodClient", side_effect=self.client), redirect_stderr(io.StringIO()):
            return execute(self.manifest, worker_python=self.root / "no-local-cuda", **kwargs)

    def rows(self, sql):
        with closing(sqlite3.connect(self.database)) as conn:
            return conn.execute(sql).fetchall()

    def test_dedicated_execution_publishes_flac_locally_without_local_cuda_or_remote_db(self):
        result = self.execute()
        self.assertEqual(result["status"], "succeeded", result)
        output = Path(result["attempts"][0]["output"])
        self.assertEqual((output / "source.yaml").read_bytes(), self.original)
        self.assertEqual(self.source.read_bytes(), self.original)
        self.assertEqual(self.rows("SELECT execution_mode,status FROM generation_attempts"), [("runpod_dedicated", "succeeded")])
        self.assertEqual(self.rows("SELECT provider,provider_instance_id FROM worker_sessions"), [("runpod", "test-pod")])
        self.assertFalse(list(self.remote.rglob("*.sqlite3")))
        from mutagen.flac import FLAC
        tags = FLAC(next(output.glob("*.flac")))
        self.assertEqual(json.loads(tags["GBR_METADATA"][0])["generation_source"], "gbr_native_runpod_dedicated")
        self.assertEqual(self.rows("PRAGMA foreign_key_check"), [])
        directory = Path(result['attempts'][0]['diagnostics'])
        self.assertFalse((directory / 'audio.flac').exists())
        self.assertFalse(list((self.remote / 'runtime/attempts').glob('*/audio.flac')))
        self.assertTrue(list((self.remote / 'runtime/attempts').glob('*/collected.json')))

    def test_disconnect_keeps_attempt_running_and_resume_collects_without_relaunch(self):
        disconnected = False
        factory = self.client
        def disconnecting_client(*args, **kwargs):
            client = factory(*args, **kwargs)
            rpc = client.rpc
            def disconnected_rpc(operation, body=None, **options):
                nonlocal disconnected
                if operation == "status" and not disconnected:
                    disconnected = True
                    raise RemotePending("injected connection loss")
                return rpc(operation, body, **options)
            client.rpc = disconnected_rpc
            return client
        with patch("_gbr_generation.executor.RunpodClient", side_effect=disconnecting_client):
            first = execute(self.manifest, worker_python=self.root / "missing")
        self.assertEqual(first["status"], "running", first)
        self.assertEqual(self.rows("SELECT status,completed_at FROM generation_attempts"), [("running", None)])
        with self.assertRaisesRegex(ValueError, "--resume-remote"):
            recover(self.manifest, first["attempts"][0]["run_id"])
        with self.assertRaisesRegex(ValueError, "--resume-remote"):
            self.execute(retry_failed=True)
        result = self.execute(resume_remote=True)
        self.assertEqual(result["status"], "succeeded", result)
        self.assertEqual(first["attempts"][0]["attempt_id"], result["attempts"][0]["attempt_id"])
        self.assertEqual(len((self.remote / "runtime/launches").read_text().splitlines()), 1)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM generation_attempts"), [(1,)])

    def test_remote_start_is_idempotent_and_refuses_changed_request(self):
        result = self.execute()
        directory = Path(result["attempts"][0]["diagnostics"])
        job = json.loads((directory / "request.json").read_text())
        client = self.client()
        self.assertEqual(client.rpc("start", {"job": job})["state"], "finished")
        job["lyrics"] += "changed"
        with self.assertRaisesRegex(RuntimeError, "different input"):
            client.rpc("start", {"job": job})
        self.assertEqual(len((self.remote / "runtime/launches").read_text().splitlines()), 1)

    def test_corrupt_download_cannot_replace_a_verified_file(self):
        # A deferred acknowledgement leaves the remote result available for recollection.
        with patch.object(RunpodClient, 'acknowledge', side_effect=OSError('temporary SSH failure')):
            result = self.execute()
        directory = Path(result["attempts"][0]["diagnostics"])
        attempt = result["attempts"][0]["attempt_id"]
        shutil.copyfile(self.remote / 'runtime/attempts' / attempt / 'audio.flac', directory / 'audio.flac')
        original = (directory / "audio.flac").read_bytes()
        client = self.client()
        rpc = client.rpc
        def corrupt(operation, body=None, **kwargs):
            if operation == "download" and body["name"] == "audio.flac":
                kwargs["output"].write(b"truncated")
                return
            return rpc(operation, body, **kwargs)
        client.rpc = corrupt
        with self.assertRaisesRegex(RemotePending, "checksum"):
            client.collect(attempt, directory)
        self.assertEqual((directory / "audio.flac").read_bytes(), original)
        self.assertFalse(list(directory.glob("*.download")))

    def test_cleanup_retries_acknowledgement_without_generating_and_checks_final_files(self):
        from _gbr_generation.executor import cleanup_outputs
        with patch.object(RunpodClient, 'acknowledge', side_effect=OSError('temporary SSH failure')):
            result = self.execute()
        with patch('_gbr_generation.executor.RunpodClient', side_effect=self.client):
            cleaned = cleanup_outputs(self.manifest)
        self.assertEqual(cleaned['cleaned_attempts'], [result['attempts'][0]['attempt_id']])
        self.assertEqual(len((self.remote / 'runtime/launches').read_text().splitlines()), 1)
        self.assertFalse(list(self.remote.glob('runtime/attempts/*/audio.flac')))
        self.assertFalse(list(self.manifest.parent.glob('attempts/*/cleanup-pending.json')))

    def test_cleanup_keeps_remote_output_if_the_published_copy_changed(self):
        from _gbr_generation.executor import cleanup_outputs
        with patch.object(RunpodClient, 'acknowledge', side_effect=OSError('temporary SSH failure')):
            result = self.execute()
        (Path(result['attempts'][0]['output']) / 'source.yaml').write_text('changed')
        with patch('_gbr_generation.executor.RunpodClient', side_effect=self.client), self.assertRaisesRegex(ValueError, 'do not verify'):
            cleanup_outputs(self.manifest)
        self.assertTrue(list(self.remote.glob('runtime/attempts/*/audio.flac')))

    def test_resume_missing_remote_attempt_does_not_launch_it(self):
        result = self.execute()
        directory = Path(result["attempts"][0]["diagnostics"])
        job = json.loads((directory / "request.json").read_text())
        shutil.rmtree(self.remote / "runtime/attempts" / job["attempt_id"])
        with self.assertRaisesRegex(RemotePending, "missing"):
            self.client().run(job, directory, resume=True)
        self.assertEqual(len((self.remote / "runtime/launches").read_text().splitlines()), 1)

    def test_setup_uploads_verified_sources_and_queries_doctor_without_copying_database(self):
        # Replace only the installer and doctor for a CPU test of the real bootstrap protocol.
        bundle = source_bundle()
        bundle["files"]["install_worker.py"] = ('from pathlib import Path\n'
            'p=Path(__file__).resolve().parents[2]/"fixture-installs"\n'
            'p.write_text(str((int(p.read_text()) if p.exists() else 0)+1))\n')
        bundle["files"]["worker.py"] = 'import json\nprint(json.dumps({"protocol":1,"hardware":{"provider":"runpod"}}))\n'
        bundle["release"] = hashlib.sha256(canonical(bundle["files"]).encode()).hexdigest()
        with patch("_gbr_worker.runpod.source_bundle", return_value=bundle):
            client = self.client(release=bundle["release"])
            result = client.setup()
            client.setup()
        self.assertEqual(result["hardware"]["provider"], "runpod")
        path = self.remote / "releases" / bundle["release"] / "requirements.txt"
        self.assertEqual(path.read_text(), bundle["files"]["requirements.txt"])
        self.assertFalse(list(self.remote.rglob("*.sqlite3")))
        self.assertEqual((self.remote / 'fixture-installs').read_text(), '1')

    def test_ssh_check_works_before_a_worker_release_is_installed(self):
        shutil.rmtree(self.remote / 'releases')
        result = self.client(release='f' * 64).check_connection()
        self.assertTrue(result['connected'])
        self.assertFalse(result['worker_installed'])
        self.assertIn('automatic setup', result['message'])

    def test_setup_refuses_to_change_environment_of_an_unfinished_attempt(self):
        attempt = self.remote / "runtime/attempts" / ("a" * 32)
        attempt.mkdir(parents=True)
        from _gbr_worker.process_state import identity
        (attempt / 'pid.json').write_text(json.dumps(identity(os.getpid())))
        bundle = source_bundle()
        bundle['files']['worker.py'] += '\n# New immutable release for setup check\n'
        bundle['release'] = hashlib.sha256(canonical(bundle['files']).encode()).hexdigest()
        client = self.client(release=bundle['release'])
        invoke = client.invoke
        def capture_setup_output(arguments, body, **kwargs):
            return invoke(arguments, body, **dict(kwargs, progress=False))
        client.invoke = capture_setup_output
        with patch('_gbr_worker.runpod.source_bundle', return_value=bundle), self.assertRaisesRegex(RuntimeError, 'environment kept unchanged'):
            client.setup()
        self.assertFalse((attempt / 'exit.json').exists())

    def test_setup_recovers_a_dead_legacy_attempt_without_deleting_its_evidence(self):
        attempt = self.remote / 'runtime/attempts' / ('b' * 32)
        attempt.mkdir(parents=True)
        (attempt / 'stderr.log').write_text('old interrupted run')
        self.test_setup_uploads_verified_sources_and_queries_doctor_without_copying_database()
        recovered = json.loads((attempt / 'exit.json').read_text())
        self.assertTrue(recovered['recovered'])
        self.assertIsNone(recovered['returncode'])
        self.assertEqual((attempt / 'stderr.log').read_text(), 'old interrupted run')


if __name__ == "__main__":
    unittest.main()
