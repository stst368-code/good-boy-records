"""Launch the existing review interface against the verified shared database."""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REVIEW_ROOT = ROOT / "_03_review" / "_generation_review"


def validate_database(path):
    path = Path(path).resolve(strict=True)
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        row = conn.execute("SELECT report_json FROM platform_migration_report WHERE id=1").fetchone()
        if not row:
            raise ValueError("Shared database has no completed migration report")
    return path


def prepare_configuration(root, path):
    from _review.config import Config
    config = Config(path)
    generated = (Path(root) / '_02_generation/_generated').resolve()
    generated.mkdir(parents=True, exist_ok=True)
    if generated not in config.inputs:
        config.values['input_directories'].append(str(generated))
        config.validate()
        config.save()
    return config


def main(argv=None):
    from platform_commands import COMMANDS, dispatch
    argv = list(sys.argv[1:] if argv is None else argv)
    if '--help' not in argv and '-h' not in argv:
        from _gbr_layout import migrate
        try:
            migrate(ROOT)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            raise SystemExit(str(exc)) from exc
    if (not argv or argv[0] not in COMMANDS or argv[0] == 'test-generation') and '--help' not in argv and '-h' not in argv:
        from dependencies import bootstrap
        delegated = bootstrap(ROOT, argv)
        if delegated is not None:
            return delegated
    if argv and argv[0] in COMMANDS:
        try:
            return dispatch(argv)
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            raise SystemExit(str(exc)) from exc
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Device commands: setup-generation, doctor-generation, setup-runpod, doctor-runpod, test-generation, init-db, migrate, check-references, plan-r2, check-comfy, doctor-processing, setup-processing. "
        "Use COMMAND --help for options. init-db creates an empty DB; migrate imports existing history."))
    parser.add_argument("--config", type=Path, default=REVIEW_ROOT / "config.json")
    parser.add_argument("--database", type=Path, default=ROOT / "_98_database" / "gbr.sqlite3")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args(argv)
    from _gbr_layout import migrate, relocated_path
    args.database, args.config = Path(relocated_path(args.database)), Path(relocated_path(args.config))
    sys.path.insert(0, str(REVIEW_ROOT))
    try:
        database = validate_database(args.database)
        prepare_configuration(ROOT, args.config)
    except (OSError, ValueError, sqlite3.Error) as exc:
        parser.exit(1, f"Cannot start GBR: {exc}\nFor a new installation use run_gbr.py init-db; to import existing history use run_gbr.py migrate --help.\n")
    from _review.__main__ import main as review_main
    arguments = ["--config", str(args.config.resolve()), "--database", str(database), "serve"]
    if args.open:
        arguments.append("--open")
    review_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

