"""Launch the existing review interface against the verified shared database."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
REVIEW_ROOT = ROOT / "_03_Review" / "_Generation_Review"


def validate_database(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT report_json FROM platform_migration_report WHERE id=1").fetchone()
        if not row:
            raise ValueError("Shared database has no completed migration report")
    return path


def main(argv=None):
    from platform_commands import COMMANDS, dispatch
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in COMMANDS:
        try:
            return dispatch(argv)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            raise SystemExit(str(exc)) from exc
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Device commands: setup-generation, doctor-generation, setup-runpod, doctor-runpod, test-generation, init-db, migrate, check-references. "
        "Use COMMAND --help for options. init-db creates an empty DB; migrate imports existing history."))
    parser.add_argument("--config", type=Path, default=REVIEW_ROOT / "config.json")
    parser.add_argument("--database", type=Path, default=ROOT / "_98_Database" / "gbr.sqlite3")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    try:
        database = validate_database(args.database)
        if not args.config.is_file():
            raise ValueError("Review configuration was not found. Run the existing review setup first, or pass --config.")
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Cannot start GBR: {exc}\nRun _98_Database/migrate_databases.py before shared-platform startup.\n")
    sys.path.insert(0, str(REVIEW_ROOT))
    from review.__main__ import main as review_main
    arguments = ["--config", str(args.config.resolve()), "--database", str(database), "serve"]
    if args.open:
        arguments.append("--open")
    review_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

