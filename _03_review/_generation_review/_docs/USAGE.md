# Installation, configuration and daily use

## First run and folder selection

Use `Install-Windows.bat` / `bash install.sh`, then `Start-Windows.bat` / `bash start.sh` as described in the README. All paths are interpreted on the **host computer**, even when you review from a phone.

Input folders may be on different local drives. Discovery is recursive, excludes the archive and application-data tree, and ignores symlinked FLAC files. Do not choose the archive itself as an input folder. Prefer a local archive on NTFS, ext4 or APFS; cross-drive transfers always copy and verify first.

Associated YAML discovery checks these adjacent names, with case-insensitive fallback:

1. `track.yaml`
2. `track.yml`
3. `track.flac.yaml`
4. `track.flac.yml`

Matching is based on the complete FLAC stem, not a guessed filename-number pattern. Filenames are retained for traceability, but audio and generation relationships use hashes and identifiers.

For missing YAMLs, the app tries structured YAML/JSON in FLAC tags including `yaml`, `generation_yaml`, `gbr_yaml`, `prompt_yaml`, `metadata`, `generation_metadata`, `gbr_metadata`, `comment` and `description`. Sidecar values take precedence over recovered embedded values. All embedded tags are retained in the database. If neither source supplies a title/genre, the filename supplies a readable title and the UI explicitly says **Genre not supplied**.

Original YAML is never annotated with review scores or publishing flags. A malformed YAML is preserved byte-for-byte, but its unavailable parameters cannot become reliable analytics values. If metadata is missing completely, audio can be archived without a fabricated YAML.

## Configuration reference

`config.example.json` is a reference. Setup creates the real `config.json`. Relative paths resolve against the folder containing that configuration file.

| Key | Default / meaning |
| --- | --- |
| `input_directories` | List of recursive scan roots. |
| `archive_directory` | `GBR_Reviewed`, containing `0`–`5`. |
| `data_directory` | `data`; keep SQLite on a local drive. |
| `host` | `127.0.0.1`; `0.0.0.0` permits password-protected LAN access. |
| `port` | `8765`. |
| `scan_interval_seconds` | `30`, valid 5–3600. |
| `fast_seconds` | `10`, valid 5–30; short screening overview. |
| `transition_sensitivity` | `balanced`; alternatives `sensitive` or `conservative`. |
| `quick_seconds` | `20`, valid 5–120. |
| `standard_seconds` | `50`, valid 10–300. |
| `reservation_queue_ahead` | `2`, integer 0–10; current recording plus this many upcoming recordings per reviewer. |
| `reservation_seconds` | `180`, integer 30–3600; renewals are every ten seconds while connected. |
| `fade_ms` | `35`, valid 0–150; applies to rendered snippet/range boundaries. |
| `password_hash` | Empty for local-only access; set using setup or the password command. |
| `allowed_hosts` | Optional exact hostnames / LAN addresses, in addition to detected local addresses. |
| `ssl_certfile`, `ssl_keyfile` | Optional PEM certificate and private-key paths. Both are required together. |

Changing snippet durations or sensitivity automatically schedules **cached retuning** for ready recordings. Choose **Retune zones** once after installing an update to refresh existing plans. Retuning reuses stored features and measurements; missing caches fall back to full analysis. **Reanalyse audio** deliberately decodes originals and rebuilds richer features, and takes longer. Old analysis records are preserved. Cache keys include analysis revision, rendering format, fades and playback ranges.

Set or change a password with the server stopped:

```powershell
.venv\Scripts\python.exe run_review.py password
```

Password hashes use scrypt with a random salt. The server persists its session secret under `data/`. Sessions are signed, use an HttpOnly SameSite cookie, expire after seven days, and are invalidated when the password hash changes. LAN users must sign in before accessing APIs or media. Directory configuration remains restricted to the host.

## Simple phone access

Stop the app, run **Start-Mobile-Windows.bat**, open the printed phone URL on the same network and enter the printed access code (or your existing password). No certificate is required. Host Settings also displays the phone connection information. Subsequent Start-Windows launches retain the LAN configuration. Run Configure-Windows and choose local-only access to revert. A password can remain configured in local-only mode.

The generated code is stored on the host as `data/mobile-code.txt`, protected by the same local filesystem access as the application data. Remote visitors cannot fetch it through the API. The old configuration is retained at `data/backups/config-before-mobile.json`. HTTP traffic is unencrypted; this launcher is intended for a trusted local network.

## Optional HTTPS and PWA use

For a phone-installable PWA on the LAN, obtain a certificate trusted by the phone for the hostname/IP you will use. Set `ssl_certfile` and `ssl_keyfile`, add the name to `allowed_hosts` if required, restart, and open `https://<that-name>:8765`. Certificate creation and device trust setup are outside this application.

On localhost, modern browsers can offer PWA installation without TLS. The manifest and service worker cache the interface shell only. Audio, reviews, credentials and database responses are not cached by the service worker. **There is no offline review queue**: the host must be reachable to load audio and save ratings.

Background and lock-screen playback are best-effort browser features. Keeping the phone screen awake cannot make a sleeping host computer serve audio. The app uses continuous audio files and Media Session, but does not bypass operating-system restrictions.

## Queue and corrections

The default queue excludes any recording that already has a human review. Random, alphabetical and date order are supported. Date order uses the provided generation timestamp where parseable, otherwise index/file timestamps. Direction changes oldest/newest or A–Z/Z–A.

Queue options can filter by partial song title, partial intended genre and a configured input root. Current queue position, filters, previous reviewed generation and skips are stored in SQLite. Each reviewer has a separate persistent session. The existing `main` session is retained for Simon, preserving its previous item, filters and skipped tracks. Different names have independent queues. Two devices selecting the same name share that reviewer’s position; use distinct names for simultaneous reviewers.

Back preloads the previous four individual scores because it is an explicit correction. Changing one of those values immediately saves the revised four-score assessment. Initial reviews never show earlier scores, averages, archive buckets, model settings, or defect details. A stale revision is rejected with a reload message.

Selection → Reassess deliberately revisits an older recording reviewed by the selected reviewer; it cannot edit someone else’s assessment. Its history action is recorded as `reassessment`, separate from `correction`. A/B relative preferences do not alter absolute ratings.

Review mode records what played: `fast`, `quick`, `standard`, `full`, `range` or `mixed`. Listening metadata also records modes played, approximate played seconds, playback speed, selected range, analysis ID, IDs of analyses actually played, and whether observed full-playback intervals covered at least 95% of the recording. Browser suspension can undercount this coverage. A correction without another listen retains the earlier review mode and records no new played modes. Selecting “Full” is not a claim that the reviewer heard every second. Ratings are not gated on a mandatory listening duration.

## Efficient review

Use Fast for a first screening, Quick/Standard when a decision needs more context, and Full or Next point for uncertain passages. Fast normally contains five two-second passages, including the opening, ending and strongest available section changes. It can omit a defect or an important musical moment, and does not substitute for a complete listen.

Enable Autoplay after the first user-initiated playback. Four number keys enter the four human scores and advance; no score is generated automatically. The next recording is prepared in the background without changing the queue or submitting a rating. Back pauses for corrections. Skip uses **N**, and **S** selects Standard. A browser may still require a tap on Play before automatic playback is permitted.

Balanced sensitivity is the starting point. Sensitive admits weaker section changes and more possible beat-level false positives; Conservative reduces such points. Saving a change queues cached retuning. Original 1.0.1 caches support the new detector through their existing descriptors; full reanalysis adds finer spectral bands and chroma.

## Archive recovery

Every completed review commits an archive operation to SQLite. A single background worker:

1. Saves a deterministic operation plan.
2. Copies media and all distinct YAML snapshots to staging files on the destination filesystem.
3. Verifies SHA-256 and publishes destination files without replacing existing files.
4. Verifies the decoded audio identity and commits current archive paths.
5. Deletes only source files whose bytes still match the indexed version.
6. Records success or a recoverable error.

On ordinary local filesystems, exclusive hard-link publication makes the staging-to-destination step atomic. FAT/exFAT uses an exclusive-copy fallback; interruption can leave a partial destination, which is retained and bypassed under a collision-safe name on retry. Reviews are never rolled back because copying, verification or deletion failed.

Identical recordings share one archived FLAC. Each distinct YAML remains intact; additional provenance sidecars use the audio stem followed by `__p<YAML-hash>`. Separate generation rows retain all parameters. The `review_inherited` analytics column identifies provenance records using a shared audio assessment. Multiple different recordings can share the same quality score.

Automatic startup recovery resumes pending/running operations. Failed operations remain visible for manual retry after the cause is fixed. If a source changes after indexing, it is retained and the operation fails safely; rescan and inspect the reported issue before retrying. If the only original audio was externally deleted or overwritten before copying, the database cannot reconstruct it.

## Backup and restore

Use Settings → Download database backup, or the `backup` command. This produces a consistent SQLite snapshot while preserving concurrent readers. Also back up the reviewed archive and `config.json`. Raw metadata snapshots are in the database, but FLAC data is not.

To restore a database snapshot:

1. Stop GBR Review.
2. Copy the current `data/` directory elsewhere as a rollback copy.
3. Restore the snapshot as `data/review.sqlite`. Move the old `review.sqlite-wal` and `review.sqlite-shm` files into the rollback copy; do not apply a stale WAL to a restored snapshot.
4. Restore or mount the archive at the same paths retained in the snapshot.
5. Restart and run Settings → Reconcile archive, then Validate dataset.

Archive and source locations are stored as absolute paths. Moving an existing archive or changing drive letters requires a controlled path migration; the app does not silently rewrite provenance paths. Keep drive letters stable. For a planned relocation, first stop the app and take database/archive backups, then migrate `audio.archive_path`, `generation_metadata.yaml_archive_path`, active `media_files.path`, and any pending operation plans together. Preserve original-path fields as historical evidence. Do not edit the live WAL database through an unrelated file-sync utility.

## CLI reference

Use `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux/macOS before `run_review.py`:

| Command | Purpose |
| --- | --- |
| `setup` | Folder and optional LAN/password prompts. |
| `mobile --open` | Enable password-protected LAN HTTP and start; show phone links/code. |
| `retune` | Queue cached zone retuning with the server stopped. |
| `serve --open` | Start one server and open a browser. |
| `password` | Set/change the access password. |
| `scan` | Index once with the server stopped. |
| `backup` | Write a consistent SQLite snapshot. |
| `validate` | Database/foreign-key, media-path and revision checks; server stopped. |
| `reconcile` | Schedule recovery/retry with the server stopped; start server to process it. |
| `export --format csv\|json\|parquet` | Export the latest generation-level analytics dataset. |
| `export --kind events --format csv --output events.csv` | Export individual events; other datasets are listed in ANALYTICS.md. |

The optional global `--config <path>` argument comes before the command, e.g. `python run_review.py --config D:/GBR/review-config.json serve`.

## Identity and first-pass reservations

Choose or enter a name after signing in. Whitespace is trimmed; case-insensitive names resolve to the existing stable ID. Clearing browser storage requires choosing the name again. Changing identity releases the old identity’s unused reservations. Changing queue options also releases only your reservations and applies your own filters.

Current and ahead reservations use decoded audio identity, so duplicate FLAC tags or different YAML cannot create a second assignment. Active devices renew reservations. Closing or losing the connection makes unused reservations available after the inactivity timeout; a suspended phone can also lose its lease. Reconnecting keeps the current recording only when it remains available. **Resume queue** clears stale ratings and obtains a valid assignment; stale submissions never overwrite another review. Temporarily reserved work and completion of the first pass have different messages.

The first upgraded database startup creates `data/backups/before-reviewers-<id>.sqlite` (or the configured data folder) before the transactional migration. Old reviews and historical revisions become Simon’s, without changing score values, provenance, archive or publishing state. Reruns keep later reviewer identities. Schema version 2 supports assessment IDs and per-assessment corrections; assigning another person’s already reviewed audio and aggregation are deferred. See [MULTI_REVIEWER.md](MULTI_REVIEWER.md).
