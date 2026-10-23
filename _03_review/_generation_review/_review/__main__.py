import argparse
import getpass
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import sys
import webbrowser
from contextlib import ExitStack
from pathlib import Path
from .config import Config, password_hash
from .db import Database
from .util import dumps, now


class ProcessLock:
    """OS-owned lock is released on crashes; a second worker cannot move files."""
    def __init__(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.file = path.open("a+b")
        if path.stat().st_size == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise SystemExit("GBR Review is already using this database or data directory. Close it before starting another copy.")

    def close(self):
        self.file.close()


def main(argv=None):
    p = argparse.ArgumentParser(description="Local GBR music review application")
    p.add_argument("--config", default="config.json", help="Configuration file (paths resolve relative to it)")
    p.add_argument("--database", type=Path, help="Override the database path for this run")
    sub = p.add_subparsers(dest="command")
    serve = sub.add_parser("serve", help="Run the local application")
    serve.add_argument("--open", action="store_true", help="Open the browser")
    mobile = sub.add_parser("mobile", help="Start password-protected local-network HTTP review; no certificates needed")
    mobile.add_argument("--open", action="store_true", help="Open the host browser")
    sub.add_parser("setup", help="Configure folders and optional LAN access")
    sub.add_parser("password", help="Set or change the access password")
    sub.add_parser("scan", help="Index files once while the application is stopped")
    sub.add_parser("backup", help="Create a consistent SQLite backup")
    sub.add_parser("validate", help="Check database and archive integrity")
    sub.add_parser("reconcile", help="Schedule recovery of incomplete archives")
    sub.add_parser("retune", help="Queue updated zones using cached features, without remeasuring audio")
    export = sub.add_parser("export", help="Export the analytics dataset")
    export.add_argument("--format", choices=("csv", "json", "parquet"), default="csv")
    export.add_argument("--output", default="")
    from .exports import EXPORT_TABLES
    export.add_argument("--kind", choices=tuple(EXPORT_TABLES), default="generations")
    args = p.parse_args(argv)
    config = Config(args.config, allow_insecure_lan=args.command in ('setup','password','mobile'))
    if args.database is not None:
        config.values["database_path"] = str(args.database.resolve())
        config.validate(allow_insecure_lan=args.command in ('setup','password','mobile'))
    if args.command == "setup":
        print("GBR Review setup. Enter input folders one per line; leave a line blank to finish.")
        directories = []
        while True:
            value = input("Input folder: ").strip().strip('"')
            if not value:
                break
            directories.append(str(config.resolve(value)))
        if directories:
            config.values["input_directories"] = directories
        value = input(f"Archive folder [{config.values['archive_directory']}]: ").strip().strip('"')
        if value:
            config.values["archive_directory"] = value
        lan = input("Enable password-protected access on the same LAN? [y/N]: ").strip().lower() == "y"
        if lan:
            password = getpass.getpass("LAN password (at least 10 characters): ")
            if len(password) < 10 or password != getpass.getpass("Repeat password: "):
                raise SystemExit("Passwords must match and contain at least 10 characters. Setup was not saved.")
            config.values["password_hash"] = password_hash(password)
            config.values["host"] = "0.0.0.0"
        else:
            config.values["host"] = "127.0.0.1"
        config.validate()
        config.save()
        print("Configuration saved. Reviewed originals will move to the archive after four ratings are entered.")
        return
    if args.command == "password":
        password = getpass.getpass("New password (at least 10 characters): ")
        if len(password) < 10 or password != getpass.getpass("Repeat password: "):
            raise SystemExit("Passwords must match and contain at least 10 characters")
        config.values["password_hash"] = password_hash(password)
        config.save()
        print("Password saved. Restart the application; previous sessions will be invalidated.")
        return
    if args.command == "backup":
        db = Database(config.database)
        path = config.data / "backups" / ("review-" + now().replace(":", "-") + ".sqlite")
        path.parent.mkdir(parents=True, exist_ok=True)
        db.backup(path)
        print(path)
        return
    if args.command == "export":
        from .exports import dataset
        path = Path(args.output) if args.output else config.data / "exports" / ("review." + args.format)
        print(dataset(Database(config.database), path, args.format, args.kind))
        return
    with ExitStack() as locks:
        # Protect shared caches/archive work as well as the database, even when
        # two configuration files select different data directories or databases.
        for path in (config.data / "application.lock", config.database_lock):
            lock = ProcessLock(path)
            locks.callback(lock.close)
        if args.command in (None, "serve", "mobile"):
            if args.command == "mobile":
                from .mobile import enable_http
                enable_http(config)
            import uvicorn
            from .app import create_app
            app = create_app(config.path, database_path=config.database)
            if app.state.service.db.migration_backup:
                print("Database migration completed. Previous database: " + str(app.state.service.db.migration_backup))
            url = ("https" if config.values["ssl_certfile"] else "http") + f"://127.0.0.1:{config.values['port']}"
            print(f"GBR Review: {url}\nStop with Ctrl+C. Your reviews and archive remain saved.")
            if not config.local_only:
                from .mobile import connection_info
                mobile_info = connection_info(config)
                for link in mobile_info["urls"]:
                    print("Phone/tablet: " + link)
                print("Access code: " + mobile_info["access_code"] if mobile_info["access_code"] else "Phone access: use your existing password.")
            if args.command in ("serve", "mobile") and args.open:
                import threading
                timer = threading.Timer(1.5, lambda: webbrowser.open(url))
                timer.daemon = True
                timer.start()
            config.data.mkdir(parents=True, exist_ok=True)
            log_path = config.data / "application.log"
            if log_path.exists() and log_path.stat().st_size > 5*1024*1024:
                import datetime
                legacy_log = config.data / ("application-legacy-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S") + ".log")
                if not legacy_log.exists():
                    try:
                        log_path.replace(legacy_log)
                    except OSError:
                        pass  # Logging must not stop startup if a viewer holds the old log.
            logging.basicConfig(level=logging.INFO, handlers=[logging.StreamHandler(), RotatingFileHandler(config.data / "application.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8")])
            uvicorn.run(app, host=config.values["host"], port=config.values["port"], proxy_headers=False, workers=1,
                        loop=f"{__package__}.server_loop:create_loop",
                        ssl_certfile=str(config.resolve(config.values["ssl_certfile"])) if config.values["ssl_certfile"] else None,
                        ssl_keyfile=str(config.resolve(config.values["ssl_keyfile"])) if config.values["ssl_keyfile"] else None)
        else:
            from .service import Service
            from .exports import validation
            service = Service(config)
            if args.command == "scan":
                print(dumps({"indexed": service.indexer.scan()}))
            elif args.command == "validate":
                print(json.dumps(validation(service.db, service.archiver), indent=2))
            elif args.command == "reconcile":
                service.archiver.reconcile()
                service.archiver.retry()
                print("Recovery scheduled. Start the application to process pending operations.")
            elif args.command == "retune":
                service.db.execute("UPDATE audio SET analysis_state='retune_pending'")
                print("Zone retuning queued. Start the application to process cached features.")


if __name__ == "__main__":
    main()
