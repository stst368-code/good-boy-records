"""Run one native test locally or on a dedicated RunPod: a short example or full song."""
import argparse
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_98_Database"))
from gbr_database.initialize import initialize
from gbr_generation.catalogue import Catalogue, Styles
from gbr_generation.executor import WORKER, default_python, execute
from gbr_generation.planner import json_text, preflight, prepare_batch
from gbr_generation.yaml_document import YamlDocument
from gbr_worker.contract import execution_target, ssh_endpoint
from gbr_worker.runpod import RunpodClient

# These are the controls of the tested local example, not a worker's model list.
LOCAL_CONTROLS = ("model", "dit", "text_encoder", "encoder_cfg", "top_k",
                  "sampler", "scheduler", "sampler_cfg", "sampler_steps")


def test_database(runtime, *, database=None, isolated=False):
    if database is not None and isolated:
        raise ValueError("Choose --database or --isolated, not both")
    return Path(database or (runtime / "local-test.sqlite3" if isolated else ROOT / "_98_Database" / "gbr.sqlite3")).resolve()


def prepare_inputs(*, filename=None, built=None, template_id=None, duration=None, encoding_cache=None, runpod=None):
    """Read and preflight inputs without changing sources, databases or model state."""
    examples = ROOT / "_02_Generation" / "examples" / "local_smoke"
    specification = json.loads((examples / "parameters.json").read_text(encoding="utf-8"))
    selection = {"purpose": "local_hardware_test"}
    template_root = None
    if filename is None:
        if template_id is not None or built is not None:
            raise ValueError("--built and --template-id require --file")
        catalogue = Catalogue(examples)
        selected = catalogue.select(all_inputs=True)
        styles = Styles(examples / "styles.csv")
    else:
        catalogue = Catalogue(built or ROOT / "_01_Writing" / "_Built")
        selected = catalogue.select(files=[str(filename)])
        if len(selected) != 1:
            raise ValueError("Choose exactly one catalogue YAML; use a relative path if the filename is ambiguous")
        template_root = ROOT / "_01_Writing" / "_references"
        styles = Styles(template_root / "styles.csv")
        example = YamlDocument((examples / "_yaml" / "gbr-local-smoke.yaml").read_bytes())
        specification["batch"] = {key: example.values[key] for key in LOCAL_CONTROLS}
        specification["batch"]["duration"] = 360 if duration is None else duration
        selection = {"purpose": "local_full_track_test", "files": [str(filename)],
                     "controls": "local_smoke_profile", "template_selection": None}
        # A catalogue template is an explicit catalogue mapping, never an interpretation
        # of the legacy YAML version field. Invalid/missing IDs are still preflight errors.
        try:
            document = YamlDocument(selected[0].path.read_bytes())
        except (OSError, ValueError):
            document = None
        use_template = template_id
        if use_template is None and document is not None and not str(document.values.get("caption") or "").strip():
            use_template = selected[0].metadata.get("original_style") or None
            if use_template:
                selection["template_selection"] = "catalogue.original_style"
        elif use_template is not None:
            selection["template_selection"] = "explicit"
        if use_template is not None:
            row = styles.by_id.get(use_template)
            if row is None:
                raise ValueError(f"Caption template {use_template!r} is absent from the shared styles.csv")
            specification["template_id"] = use_template
            specification["batch"].update(cluster=row["cluster"], style=row["style_primary"])
            if template_id is not None:
                specification["field_precedence"] = {"caption": ["defaults", "preset", "yaml", "template", "batch", "rerun"]}
    if duration is not None:
        specification.setdefault("batch", {})["duration"] = duration
    if encoding_cache is not None:
        specification["execution"]["encoding_cache"] = encoding_cache
    if runpod is not None:
        ssh_endpoint(runpod)
        specification["execution"].update(memory_profile="resident_stage",
            conditioning={"backend": "runpod_dedicated", "address": runpod},
            diffusion={"backend": "runpod_dedicated", "address": runpod})
        selection["purpose"] = "runpod_full_track_test" if filename is not None else "runpod_hardware_test"
    checked = preflight(selected, specification, styles, template_root)
    return checked, specification, catalogue.root, selection


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Full track: --file moses-supposes-punk.yaml --duration 360. "
        "Add --runpod ssh://root@PUBLIC_IP:PORT for GPU-resident execution on a dedicated pod. "
        "Uses the same smoke controls; seeds come from the YAML or are frozen automatically. "
        "Blank captions use the catalogue's original_style template ID. "
        "Duration is an upper bound; the model may finish earlier."))
    parser.add_argument("--prepare-only", action="store_true", help="Freeze an inspectable plan without invoking CUDA")
    parser.add_argument("--retry", action="store_true", help="Reuse the latest frozen test with its original seeds and database")
    parser.add_argument("--resume-remote", action="store_true", help="Reconnect to the latest remote test and collect its existing attempt")
    parser.add_argument("--runpod", help="Dedicated pod's SSH-over-exposed-TCP address: ssh://root@PUBLIC_IP:PORT")
    parser.add_argument("--identity-file", type=Path, help="Local SSH private key path; default: SSH agent/config. Never uploaded")
    parser.add_argument("--file", type=Path, help="One full-track YAML from _01_Writing/_Built (filename or relative path)")
    parser.add_argument("--built", type=Path, help="Alternative writing catalogue root; requires --file")
    parser.add_argument("--template-id", help="Explicit shared caption template; requires --file")
    parser.add_argument("--duration", type=float, help="Audio duration upper bound, at most 360 seconds; full-track default: 360")
    parser.add_argument("--encoding-cache", choices=("prefer", "require", "disabled"), help="Default: prefer a compatible cache, otherwise encode")
    parser.add_argument("--worker-python", type=Path, default=default_python())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--database", type=Path, help="Shared database; default: _98_Database/gbr.sqlite3")
    group.add_argument("--isolated", action="store_true", help="Explicitly use the separate diagnostic database")
    args = parser.parse_args(argv)
    if args.retry and args.resume_remote:
        parser.error("Choose --retry for a failed generation or --resume-remote for an existing remote attempt")
    if (args.retry or args.resume_remote) and any((args.file is not None, args.built is not None, args.template_id is not None,
                           args.duration is not None, args.encoding_cache is not None,
                           args.database is not None, args.isolated, args.runpod is not None)):
        parser.error("Retry/resume reuses the frozen request; omit file, parameter, target and database overrides")
    runtime = ROOT / "_02_Generation" / "runtime" / "local_test"
    latest = runtime / "latest.json"
    try:
        if args.retry or args.resume_remote:
            manifest = Path(json.loads(latest.read_text(encoding="utf-8"))["manifest"])
            from gbr_generation.planner import verify_plan
            frozen = verify_plan(manifest)
            backend, address = execution_target(frozen["runs"][0]["execution"])
            if args.resume_remote and backend != "runpod_dedicated":
                raise ValueError("The latest test is local; there is no remote attempt to resume")
        else:
            checked, spec, input_root, selection = prepare_inputs(filename=args.file, built=args.built,
                template_id=args.template_id, duration=args.duration, encoding_cache=args.encoding_cache, runpod=args.runpod)
            backend, address = execution_target(spec["execution"])
            invalid = [record.summary() for record in checked if not record.ready]
            if invalid:
                parser.exit(2, json_text({"error": "Test cancelled: fix these inputs before running", "inputs": invalid}))
            print(json_text({"inputs": [record.summary() for record in checked]}), flush=True)
            if not args.prepare_only:
                if backend == "runpod_dedicated":
                    print(json_text(RunpodClient(address, args.identity_file).rpc("doctor")), flush=True)
                else:
                    subprocess.run([str(args.worker_python), str(WORKER / "worker.py"), "doctor"], check=True)
            database = test_database(runtime, database=args.database, isolated=args.isolated)
            if not database.exists():
                initialize(database)
            manifest = prepare_batch(checked, spec, runtime / "batches", database,
                                     input_root=input_root, selection=selection,
                                     output_root=ROOT / '_02_Generation' / 'generated', output_layout='single')
            latest.write_text(json_text({"manifest": str(manifest)}), encoding="utf-8")
        print("Frozen test manifest: " + str(manifest), flush=True)
        if args.prepare_only:
            return 0
        if args.retry:
            if backend == "runpod_dedicated":
                print(json_text(RunpodClient(address, args.identity_file).rpc("doctor")), flush=True)
            else:
                subprocess.run([str(args.worker_python), str(WORKER / "worker.py"), "doctor"], check=True)
        result = execute(manifest, worker_python=args.worker_python, limit=1, retry_failed=args.retry,
                         runpod_identity=args.identity_file, resume_remote=args.resume_remote)
        print(json_text(result))
        return 1 if result["status"] == "failed" else 3 if result["status"] == "running" else 0
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, str(exc) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
