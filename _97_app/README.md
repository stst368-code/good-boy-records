# GBR platform launcher

The unified application provides Review, Selection, Generation and YAML editor
tabs, connected to `_98_database/gbr.sqlite3`. The shared platform belongs here;
reusable worker and editing tools belong in `_99_tools`.

The native generation controller is available separately for the
[first local PC test](../_02_generation/LOCAL_TEST.md). Successful FLACs carry
native run provenance and link back to generation telemetry when the existing
review app indexes them against the same database.

After the verified migration, run `Start-Windows.bat`. It reuses the existing
review virtual environment and `config.json`, including input/archive paths,
authentication, TLS, browser opening and logging. Start only after closing the
old review app. Database migration does not relocate audio, caches or archives;
check the existing review configuration against the actual folders first.

Equivalent command from the repository root:

```powershell
_03_review/_generation_review/.venv/Scripts/python.exe _97_app/run_gbr.py --open
```

`--config PATH` and `--database PATH` select alternatives. Startup requires an
existing database containing a completed migration report; it cannot silently
create an empty shared database. The review application's caches, secrets and
logs stay in its configured data directory. Startup locks both that data directory
and the resolved database path, so configurations cannot concurrently share a
database or overwrite each other's caches and archive work.

The review CLI also accepts an explicit database before its subcommand:

```powershell
_03_review/_generation_review/.venv/Scripts/python.exe _03_review/_generation_review/run_review.py --database _98_database/gbr.sqlite3 backup
```

Without an override or `database_path` configuration, the review CLI keeps using
its existing `data_directory/review.sqlite` location. Backups and exports use the
selected database. A configuration's optional `database_path` resolves relative
to that configuration file; command-line overrides resolve from the current
working directory.

## Directory convention and existing devices

Repository directories use lowercase names starting with `_`, including source
packages and static assets. Use `_97_app/Start-Windows.bat` or
`python _97_app/run_gbr.py`. The generated inbox is `_02_generation/_generated`.
Machine-managed virtual environments, caches, worker deployment storage and
runtime identifier folders keep their required formats.

Finish generation and close the app before pulling this directory update.
Startup moves legacy local data into the canonical folders, reuses the existing
database/environment and updates storage references. It does not import history
again or rewrite frozen manifests/YAML. Conflicting files stop the move without
overwriting either file. Existing cloud worker storage stays in its original
location so old immutable releases remain collectable. No new setup script or
extra database is needed. Fresh devices use the same launcher and normal init-db
or historical import commands.

Startup checks distinguish the launcher's own Python/venv startup chain from
another running GBR app or controller. Other programs using the same virtual
environment do not block directory migration. Saved controller PIDs are checked
against their creation time so a recycled PID does not prevent startup. An
actual running app/controller still blocks moving its directories; nothing is
terminated automatically.
