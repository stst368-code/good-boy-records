"""Inspect source snapshots or build a verified shared database. Standard library only."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

from _gbr_database import inspect_sources, migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inspect", "migrate"))
    parser.add_argument("--generation", required=True, type=Path)
    parser.add_argument("--review", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--source-id", default="minimax-main")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    if args.command == "migrate" and args.output is None:
        parser.error("migrate requires --output; existing files are never overwritten")
    if args.report and args.report.resolve() in {args.generation.resolve(), args.review.resolve(), args.output.resolve() if args.output else None}:
        parser.error("Report path must be different from database paths")
    report_handle = None
    try:
        # Reserve the optional report before doing expensive migration work.
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            report_handle = args.report.open("x", encoding="utf-8")
        if args.command == "inspect":
            result = inspect_sources(args.generation, args.review)
        else:
            result = migrate(args.generation, args.review, args.output, args.source_id,
                             progress=lambda message: print(message, file=sys.stderr, flush=True))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        if report_handle:
            report_handle.write(rendered + "\n")
            report_handle.flush()
        print(rendered)
    except (ValueError, OSError, sqlite3.Error) as exc:
        if report_handle:
            report_handle.close()
            args.report.unlink(missing_ok=True)
        parser.exit(1, str(exc) + "\n")
    finally:
        if report_handle:
            report_handle.close()


if __name__ == "__main__":
    main()
