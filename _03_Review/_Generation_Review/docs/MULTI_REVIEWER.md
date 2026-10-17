# Reviewer identity and first-pass queues — version 1.2.0

After the existing access-code/password sign-in, choose a known name or type a new one. Names are trimmed, blank names are rejected, and case-insensitive matches reuse the stable reviewer ID. The first spelling is retained for display. The browser remembers the ID and shows **Reviewing as …** with **Change user**.

Different names have separate queue order, filters, current recording, previous reviewed recording and skipped audio. The same name on two devices represents one reviewer and shares that queue. Identity provides attribution; existing shared access controls still govern the application. There are no individual passwords or permissions.

## Reservations and reconnecting

The server reserves the current recording and two ahead by default. Host Settings controls `reservation_queue_ahead` (0–10) and `reservation_seconds` (30–3600; default 180). Zero ahead reserves only the current recording and disables next-track preparation. Reducing the ahead limit releases excess reservations on the next queue request or heartbeat.

Allocation, renewal, release and submission checks use SQLite write transactions. An audio ID can belong to only one reviewer's reservation. The identity is decoded PCM, so different FLAC compression/tags or different YAML provenance do not produce another assignment. Next-track preparation uses that reviewer's reserved next item and never advances or writes a review.

Connected clients renew every ten seconds. Closing a tab or losing its connection lets its reservations expire; phone background suspension can have the same effect. Opening the name again resumes the current recording if it is still unreviewed and available. Another reviewer may have acquired it after expiry.

Changing identity releases the old reviewer's unused reservations. Changing queue settings releases only that reviewer's reservations before allocating matching work. Skips affect only that reviewer and leave the audio available to others. Back edits only that reviewer's previous review while retaining their pending first-pass recording.

If a visible assignment expires or changes, rating and Skip are disabled and **Resume queue** appears. The server rejects expired, conflicting or stale submissions without replacing a review. Resume obtains a valid assignment and clears old unsaved scores. Work temporarily held by other reviewers shows a waiting message. Completion shows **First pass complete** and stops assignment.

## Existing data

On the first upgraded database startup, the app creates a consistent SQLite snapshot under the configured data folder's `backups/before-reviewers-<id>.sqlite` before migration. The migration is transactional and rollback-tested. Every legacy review, correction and history entry is attributed to **Simon**. The old `main` session remains Simon's, preserving Back, filters and skips. Scores, raw YAML bytes, media links, archive journal and publishing state are retained. A successful rerun adds neither another Simon nor new attribution changes.

The update installer also backs up replaced code and snapshots the database in `patch-backups/1.2.0-<timestamp>-<id>/` before installing. It does not replace `config.json`, user data or media. Runtime migration happens when the app or a database-using command first opens the database, rather than during code installation.

After migration, restoring only old Python code is insufficient: the old writer is incompatible with the new schema. A full rollback needs matching pre-update code/database and corresponding media state. Retain current data first. Restoring an old database discards later reviews and does not move archived audio back automatically; do not use this as an ordinary troubleshooting step.

## Attribution and future compatibility

`reviewers` stores stable IDs/names. `assessments` has one `(audio_id, reviewer_id)` pair. A correction retains its assessment ID and appends an `assessment_revision`. Another person's independent assessment would have a different ID. The current application rejects that second-person assessment and does not assign already reviewed audio.

`review_dataset`, `review_history_dataset`, `latest_reviewer_reviews` and `analytics_dataset` expose attribution. Relevant CSV/Parquet/JSON exports and the publishing manifest include IDs and names. JSON dataset exports use schema version 2. The historical per-audio revision remains for existing archive behavior.

Second-pass assignment, aggregation and changed archive-scoring policies are deferred. Selection and publishing intent remain shared; Reassess is available only for your own reviewed recordings. See [ANALYTICS.md](ANALYTICS.md) for the API contract and [TESTING.md](TESTING.md) for validation.
