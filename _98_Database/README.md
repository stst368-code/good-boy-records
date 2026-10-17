# Shared GBR database

This directory owns the shared SQLite database. `_97_App` launches the platform;
`_03_Review` retains its existing review interface and services. The generation
controller lives in `_02_Generation`, with execution workers planned in `_99_Tools`.

Migration, input preflight and frozen batch preparation are implemented. Native
MiniMax execution, worker telemetry collection and the YAML editor remain
subsequent work. Prepared requests are not an enabled inference queue.

## Inspect and migrate

Python 3.10 or later is sufficient for the migration; no packages are required.
Keep both original databases and their archives. Extract each archive to a
different folder. Run from the repository root, replacing the source paths:

```powershell
py -3 _98_Database/migrate_databases.py inspect --generation "E:/Backups/generation/.minimax_pipeline.sqlite3" --review "E:/Backups/review/review.sqlite"
py -3 _98_Database/migrate_databases.py migrate --generation "E:/Backups/generation/.minimax_pipeline.sqlite3" --review "E:/Backups/review/review.sqlite" --output "_98_Database/gbr.sqlite3" --report "_98_Database/backups/initial-migration.json"
```

An existing output database or report is refused. Rerunning to a fresh destination
imports a new independent snapshot; it does not merge later changes into an
already active shared database. Use local storage supporting hard links, such as
NTFS. The migration stages files beside the destination and publishes the
verified database atomically without replacing anything.

SQLite's backup API includes committed WAL data when sources are live, but the
two source databases cannot be snapshotted as a single transaction. Stop the old
runner and review app before the final cutover. Inspection and trial imports may
use supplied snapshots while the old tools continue running. Take fresh snapshots
at cutover so reviews made since these attachments are included. Keep the old
tools stopped once the shared app starts: their old databases are independent.

Allow temporary free disk space for two source snapshots and the final database.
Run locally rather than on an actively synchronised or network-mounted database.
Use consistent SQLite backups for copying to cloud storage.

## Preservation and reconciliation

- Review starts as an SQLite backup. Existing IDs, tables, relationships, raw
  YAML bytes, reasons, flags, bookmarks and analysis data remain intact.
- Withdrawn jobs and legacy jobs without a usable review match are excluded,
  together with their timing events. A usable association requires an unambiguous
  filename match without conflicting parameters; incomplete parameter evidence
  stays labelled. Every column of the retained jobs/events is preserved in
  `legacy_generation_jobs` and `legacy_generation_timings`. Naming counters remain
  intact to avoid reusing old output numbers. Import `source_id` namespaces them.
- Native `generation_runs`, batches and attempts start empty. No import starts
  inference, retries a job, scans audio or moves files.
- Counts and typed-value SHA-256 fingerprints of every review table and all
  retained legacy rows are reconciled before publication. Integrity and foreign-key checks must pass.
  The result is also stored in `platform_migration_report`.
- Original Windows paths and timestamp strings remain unchanged. A historical
  path on another computer is not treated as proof that a file is missing.
- Output associations record filename and parameter evidence. `parameters_match`
  means the filename and all eight recorded parameters agree; it does not prove
  PCM identity. Incomplete evidence stays labelled; conflicting and ambiguous
  candidates are reported and their jobs excluded from the import.

Unmatched review records are retained: 439 lack a legacy filename candidate, and
160 saved reviews depend on that group. Excluding unlinked runner history does
not silently erase those ratings. The report records the cleanup policy and
counts of omitted jobs and timing events. Original source databases are untouched.

The initial importer accepts the current reviewer/bookmark review schema and the
attached runner's jobs/timings/counters schema. Older review databases should be
upgraded by the existing review tool, with its normal migration backup, first.
Already shared databases are not accepted as a fresh review source.

## Analysis and new records

`legacy_generation_costs` groups billable estimates by job **and currency**.
`missing_cost_events` marks incomplete totals; a missing price is not zero.
Nonbillable aggregate timings do not inflate totals. `legacy_stage_performance`
includes conditioning, sampling and `full_generation_total` records, separating
worker address, GPU model, stage and currency. Stage durations are not additive
when spans overlap; successful duration averages exclude failed attempts.

Future records distinguish source documents, batches, runs, attempts, timing
spans, artifacts and review links. Worker sessions record a discovered hardware
snapshot for an address, rather than a manually maintained worker name or model
list. Resource measurements distinguish PyTorch allocated/reserved peaks from a
sampled device usage peak, with sampling coverage and execution mode recorded.
Missing historical hardware, VRAM and prices remain null. No telemetry values
are inferred from a GPU's advertised capacity.

## Verification

```powershell
py -3 -m unittest discover -s _98_Database/tests -v
```

Tests exercise the existing review schema, review attribution, exact YAML bytes,
future legacy columns, large seeds, evidence levels, cleanup exclusions, live WAL snapshots, invalid
inputs, source/output protection, separated currencies and null costs.

See [AUDIT.md](AUDIT.md) for results from the supplied populated databases.
