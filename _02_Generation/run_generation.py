"""Inspect writing inputs, freeze generation requests, or verify a saved batch."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from gbr_generation.catalogue import Catalogue, Issue, Styles
from gbr_generation.planner import BatchRejected, json_text, preflight, prepare_batch, verify_plan
from gbr_generation.review_selection import select_by_rating

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for command in ("inspect", "plan"):
        p = sub.add_parser(command)
        p.add_argument("--built", type=Path, default=ROOT / "_01_Writing" / "_Built")
        p.add_argument("--all", action="store_true")
        p.add_argument("--song", action="append", default=[], help="Repeat a catalogue song ID or exact filename stem")
        p.add_argument("--album", action="append", default=[])
        p.add_argument("--file", action="append", default=[])
        p.add_argument("--order", choices=("selection", "filename", "album"), default="selection")
        p.add_argument("--parameters", type=Path, help="JSON source values, overrides and precedence policy")
        p.add_argument("--styles", type=Path, default=ROOT / "_01_Writing" / "_references" / "styles.csv")
        p.add_argument("--styles-encoding", default="utf-8-sig")
        p.add_argument("--template-root", type=Path, default=ROOT / "_01_Writing" / "_references")
        p.add_argument("--database", type=Path, default=ROOT / "_98_Database" / "gbr.sqlite3")
        p.add_argument("--below-rating", type=float, help="Include only tracks with mean latest rating at or below this value")
        p.add_argument("--reviewer", help="Reviewer ID; default includes all reviewers")
        p.add_argument("--rating-cluster")
        p.add_argument("--rating-style", help="Uses explicit style metadata; legacy version is not reinterpreted")
        if command == "plan":
            p.add_argument("--workspace", type=Path, default=ROOT / "_02_Generation" / "runtime")
            p.add_argument("--on-error", choices=("cancel", "drop", "prompt"), default="prompt")
    p = sub.add_parser("verify")
    p.add_argument("manifest", type=Path)
    p = sub.add_parser("execute", help="Run frozen jobs locally or on a dedicated RunPod")
    p.add_argument("manifest", type=Path)
    p.add_argument("--worker-python", type=Path)
    p.add_argument("--limit", type=int, default=1, help="Maximum jobs in this invocation (default: 1)")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--resume-remote", action="store_true", help="Collect the existing remote attempt without starting a new job")
    p.add_argument("--identity-file", type=Path, help="Local SSH key path; default uses the SSH agent/config")
    p.add_argument("--timezone", default="Europe/London")
    p = sub.add_parser("recover", help="Recover an interrupted run after stopping its old processes")
    p.add_argument("manifest", type=Path)
    p.add_argument("--run-id", required=True)
    p.add_argument("--remote-stopped", action="store_true", help="Explicitly confirm that the old remote worker has stopped before abandoning its attempt")
    args = parser.parse_args(argv)
    try:
        if args.command in {"execute", "recover"}:
            from gbr_generation.executor import execute, recover
            if args.command == "recover":
                result = recover(args.manifest, args.run_id, remote_stopped=args.remote_stopped)
            else:
                result = execute(args.manifest, worker_python=args.worker_python, limit=args.limit,
                                 retry_failed=args.retry_failed, timezone_name=args.timezone,
                                 runpod_identity=args.identity_file, resume_remote=args.resume_remote)
            print(json_text(result))
            if result["status"] == "failed":
                raise SystemExit(1)
            return
        if args.command == "verify":
            manifest = verify_plan(args.manifest)
            print(json_text({"batch_id": manifest["batch_id"], "verified_runs": len(manifest["runs"]), "status": manifest["status"]}))
            return
        catalogue = Catalogue(args.built)
        if not (args.all or args.song or args.album or args.file) and sys.stdin.isatty():
            for index, item in enumerate(catalogue.inputs, 1):
                print(f"{index:3d}  {item.path.name}  song={item.song_id} variant={item.variant_id}", file=sys.stderr)
            answer = input("Select file numbers separated by commas, or all: ").strip()
            if answer.casefold() == "all":
                args.all = True
            else:
                try:
                    indices = [int(part.strip()) - 1 for part in answer.split(",")]
                    if any(n < 0 or n >= len(catalogue.inputs) for n in indices):
                        raise ValueError()
                    args.file = [str(catalogue.inputs[n].path) for n in indices]
                except ValueError as exc:
                    raise ValueError("Selection must contain listed file numbers") from exc
        selected = catalogue.select(all_inputs=args.all, songs=args.song, albums=args.album, files=args.file, order=args.order)
        selection = {"all": args.all, "songs": args.song, "albums": args.album, "files": args.file, "order": args.order}
        if args.below_rating is not None:
            selected, evidence = select_by_rating(selected, args.database, args.below_rating, args.reviewer, args.rating_cluster, args.rating_style)
            selection["review_filter"] = evidence
            if not selected:
                raise ValueError("No inputs meet the review filter")
        elif args.reviewer or args.rating_cluster or args.rating_style:
            raise ValueError("Reviewer/cluster/style rating filters require --below-rating")
        specification = json.loads(args.parameters.read_text(encoding="utf-8-sig")) if args.parameters else {}
        if not isinstance(specification, dict):
            raise ValueError("Parameter specification must be a JSON object")
        styles = Styles(args.styles, args.styles_encoding)
        checked = preflight(selected, specification, styles, args.template_root)
        for record in checked:
            for field in ("cluster", "style"):
                requested = getattr(args, "rating_" + field)
                if requested and str(record.effective.get(field, "")).strip().casefold() != requested.strip().casefold():
                    record.issues.append(Issue("review_target_mismatch", f"Effective {field} differs from the requested review target; supply an explicit override"))
        if args.command == "inspect":
            print(json_text({"selection": selection, "inputs": [i.summary() for i in checked]}))
            if any(not i.ready for i in checked):
                raise SystemExit(2)
            return
        policy = args.on_error
        if policy == "prompt":
            policy = "cancel"
            if any(not i.ready for i in checked) and sys.stdin.isatty():
                print(json_text([i.summary() for i in checked if not i.ready]), file=sys.stderr)
                answer = input("Drop invalid inputs and prepare the rest? Enter drop; anything else cancels: ").strip()
                policy = "drop" if answer.casefold() == "drop" else "cancel"
        path = prepare_batch(checked, specification, args.workspace, args.database, input_root=catalogue.root,
                             error_policy=policy, selection=selection)
        print(json_text({"manifest": str(path), "prepared": sum(i.ready for i in checked), "excluded": sum(not i.ready for i in checked)}))
    except BatchRejected as exc:
        print(json_text({"error": str(exc), "inputs": exc.report}), file=sys.stderr)
        raise SystemExit(2)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, str(exc) + "\n")


if __name__ == "__main__":
    main()

