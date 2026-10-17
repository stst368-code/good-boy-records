# GBR platform launcher

The first platform increment reuses the established review interface, connected
to `_98_Database/gbr.sqlite3`. It does not yet add generation or bulk YAML screens.
The shared platform belongs here; reusable worker and editing tools belong in
`_99_Tools`.

After the verified migration, run `Start-Windows.bat`. It reuses the existing
review virtual environment and `config.json`, including input/archive paths,
authentication, TLS, browser opening and logging. Start only after closing the
old review app. Database migration does not relocate audio, caches or archives;
check the existing review configuration against the actual folders first.

Equivalent command from the repository root:

```powershell
_03_Review/_Generation_Review/.venv/Scripts/python.exe _97_App/run_gbr.py --open
```

`--config PATH` and `--database PATH` select alternatives. Startup requires an
existing database containing a completed migration report; it cannot silently
create an empty shared database. The review application's caches, secrets and
logs stay in its configured data directory. Startup locks both that data directory
and the resolved database path, so configurations cannot concurrently share a
database or overwrite each other's caches and archive work.

The review CLI also accepts an explicit database before its subcommand:

```powershell
_03_Review/_Generation_Review/.venv/Scripts/python.exe _03_Review/_Generation_Review/run_review.py --database _98_Database/gbr.sqlite3 backup
```

Without an override or `database_path` configuration, the review CLI keeps using
its existing `data_directory/review.sqlite` location. Backups and exports use the
selected database. A configuration's optional `database_path` resolves relative
to that configuration file; command-line overrides resolve from the current
working directory.
