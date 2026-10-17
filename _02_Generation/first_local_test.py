"""Prepare one native GPU test in the shared database; isolation is explicit."""
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


def test_database(runtime, *, database=None, isolated=False):
    if database is not None and isolated:
        raise ValueError("Choose --database or --isolated, not both")
    return Path(database or (runtime / "local-test.sqlite3" if isolated else ROOT / "_98_Database" / "gbr.sqlite3")).resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prepare-only", action="store_true", help="Create an inspectable test plan without invoking CUDA")
    parser.add_argument("--retry", action="store_true", help="Reuse the latest frozen test with the same seeds")
    parser.add_argument("--worker-python", type=Path, default=default_python())
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--database", type=Path, help="Use an existing shared database; default: _98_Database/gbr.sqlite3")
    group.add_argument("--isolated", action="store_true", help="Explicitly use one separate local-test.sqlite3 for diagnostics")
    args = parser.parse_args(argv)
    runtime = ROOT / "_02_Generation" / "runtime" / "local_test"
    runtime.mkdir(parents=True, exist_ok=True)
    database = test_database(runtime, database=args.database, isolated=args.isolated)
    latest = runtime / "latest.json"
    if not args.prepare_only:
        # Doctor does not download model weights. Show GPU and RAM before the real run.
        subprocess.run([str(args.worker_python), str(WORKER / "worker.py"), "doctor"], check=True)
    if args.retry:
        manifest = Path(json.loads(latest.read_text(encoding="utf-8"))["manifest"])
    else:
        if not database.exists():
            initialize(database)
        examples = ROOT / "_02_Generation" / "examples" / "local_smoke"
        spec = json.loads((examples / "parameters.json").read_text(encoding="utf-8"))
        catalogue = Catalogue(examples)
        checked = preflight(catalogue.select(all_inputs=True), spec, Styles(examples / "styles.csv"))
        manifest = prepare_batch(checked, spec, runtime / "batches", database, input_root=examples,
                                 selection={"purpose": "local_hardware_test"})
        latest.write_text(json_text({"manifest": str(manifest)}), encoding="utf-8")
    print("Frozen test manifest: " + str(manifest), flush=True)
    if args.prepare_only:
        return 0
    result = execute(manifest, worker_python=args.worker_python, limit=1, retry_failed=args.retry)
    print(json_text(result))
    return 1 if result["status"] == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())

