# GBR Review 1.3.0 — changes, findings and checks

This update is built from the supplied `Configure-Windows.zip` current source, including its native Windows event-loop fix. The supplied `application.zip` contains the database and log; it does not include the actual media or playback cache. Neither uploaded archive nor its database was modified.

## Implemented changes

| Area | Result |
| --- | --- |
| Distribution in Review | Independent checkbox in Track flags; writes the same `selected` intent used by Selection. Available before scoring completes. It does not publish or move media. Unreviewed intentions wait for a human review before appearing in the publishing manifest. |
| Bookmark | Separate reviewer-specific bookmark, independent of distribution. Selection finds bookmarks across all buckets, including unreviewed ones, and offers Listen/Review/Reassess as appropriate. Review acquires a valid exclusive reservation without stealing another person's work. |
| Reason | Optional multiline plain text per review/reviewer/revision; loads in Back/Reassess and is visible in saved review details and relevant exports. Historical reasons remain blank. |
| Submission | Auto-submit initially on: fourth outstanding rating submits and uses existing autoplay. Off: a separate Submit review button requires all four values. The device remembers the choice. Typing in Reason cannot score ratings. |
| Failure recovery | Entered values stay in the form; an actionable message and retry are provided. Retry IDs prevent duplicate commits, and stale/conflicting requests cannot overwrite a saved review. Different tracks reset Reason and load their own markers. |
| Directories | Settings opens before optional diagnostics completes. Folder picker can add/edit server input paths. Same-host LAN access is recognised; remote filesystem configuration remains restricted. Existing archive location is explicitly locked once reviewed media exists, without locking input folders. |
| Cache | Full WAV identity no longer varies with retuning/fade settings. Identifiable old full WAVs are reused unchanged. Usage report and optional protected cleanup are added. Automatic cleanup is off, and untracked existing files are retained. |
| Logging | Existing Windows Proactor fix is preserved. New logs rotate at 5 MiB with three backups. An existing oversized application log is retained as a timestamped legacy log when it can be renamed; log handling does not block startup if a viewer holds it. |

All four rating definitions/questions/captions, keyboard scoring, Back/Skip, reviewer identities/queues, numerical averages/buckets, provenance, archive handling and local HTTP operation are retained. Distribution remains shared; bookmarks are personal. Reason saves with review submission, rather than merely setting a marker or skipping.

## Supplied database findings

The snapshot contains **1,352 distinct audio recordings**, **2,699 generation/provenance records**, **493 review revisions**, **494 review-history entries**, **two reviewers**, and **one existing distribution selection**. Before migration, SQLite integrity was `ok` and there were no foreign-key errors.

The migration was run against a disposable copy. Every original value across **21 existing tables** was compared using row/column fingerprints, including raw BLOB metadata, scores, reviewer/assessment IDs, timestamps, review history, archive journal, publishing state, analysis and queue data. All comparisons matched. The pre-migration backup matched the original data; integrity and foreign keys remained clean after migration. A rerun produced no second migration backup or attribution changes. The uploaded database's SHA-256 remained unchanged.

Schema 3 adds reason/flag snapshot/retry fields, bookmark tables, an optional bookmark-open queue pointer and a derived-playback ledger. Existing publishing rows and historical flag timestamps are not rewritten. Dataset JSON reports schema version 3; external consumers need to accept the additive fields.

## WAV cache findings

The supplied audio metadata is almost entirely **44.1 kHz, stereo, 16-bit** (one recording is 48 kHz). At that format, three minutes of lossless WAV is about **31.8 MB**. A full WAV copy of every indexed recording would be approximately **51.1 GB** before small headers. The reported 13 GB is therefore plausible, though unused and duplicate derivatives can waste space.

WAV supports browsers that cannot play the original FLAC, full playback fallback, rendered sample/range sessions and level-matched A/B playback. Removing it indiscriminately can interrupt active playback and force regeneration. The previous full-track cache key unnecessarily included analysis revision and snippet fade, allowing equivalent full copies to accumulate after retuning.

The update reuses identifiable legacy full copies and uses a stable key for new full WAVs. **No existing WAV cache was removed or rewritten.** Optional cleanup only removes tracked older derivatives when their original container hash still matches; active streams/queues, untracked files and unavailable/changed originals are protected. A removed eligible derivative is recreated on demand. No automated retention job is enabled.

Because the cache was omitted from the upload, individual WAV files, precise on-disk usage and actual reclaimable bytes cannot be verified here. Legacy untracked files will not all become eligible immediately. Use the new host cache report/preview to inspect actual usage.

## Checks performed

- **97 backend tests passed** on Python 3.12/Linux. Covers independent/together markers, pre-review intent, revision-specific blank/populated reasons, idempotent retry, stale rollback, bookmark ownership/reopening, exports, own-LAN host directory access, archive protection, WAV reuse/key stability, cleanup protection/regeneration, migration rollback, and existing review/archive/authentication/analysis behavior.
- **Four Chromium integration scripts passed**, with no application JavaScript errors. Covers default fourth-choice save/autoplay, manual submit, populated/blank correction reasons, textarea shortcut suppression, failed-save retention/retry, marker consistency in both tabs, bookmark discovery/reopening, field reset, directory picker even with failed diagnostics, plus earlier Fast/keyboard/WAV/A-B and simultaneous-reviewer checks.
- Desktop 1280 px and simulated-phone 390/320 px layouts were checked for visible guidance, grouped captions, no horizontal overflow and wide rating targets. Desktop/mobile share the same functionality and responsive assets.
- The supplied real-database copy passed transactional migration, backup verification, all-original-values preservation and rerun checks. The original snapshot remained untouched.
- Python compilation, JavaScript syntax, shell syntax and release archive/hash checks passed. The in-place updater is verified for both `review` and `gbr_review`, preserving configuration/data/media and refusing running/corrupt installs; injected-write rollback is checked.

The nonsecure-origin browser check uses a loopback request-forwarding harness. It tests browser login/playback behavior without claiming physical LAN reachability.

## Checks not run / remaining issues

1. **Native Windows execution and physical phones.** Actual `.bat` launch, Windows drive permissions/NTFS transfer, host LAN routing/firewall, real iOS/Android autoplay/background/Bluetooth and folder picker on the real host remain unverified. The supplied native event-loop implementation is preserved, rather than replaced by the older Selector workaround.
2. **Actual 13 GB cache and real audio.** Neither was uploaded. Source behavior and synthetic byte-level reuse/regeneration are tested; actual file inventory, reclaimable space, corpus transitions and real-media performance remain for host acceptance.
3. **Existing unavailable audio.** The snapshot reports 30 recordings in failed analysis state with unavailable audio, plus one separate missing-source scan error. Folder changes/rescan can help when the correct sources are restored; the update cannot reconstruct missing originals and does not erase these records.
4. **Large historical log.** The supplied log is about 637 MB and contains repeated historical Windows Selector empty-buffer assertions. It is preserved; the current native loop and bounded future logging reduce recurrence/growth but still need physical Windows verification.
5. **External analytics importer.** Not supplied. Check JSON schema 3 and additive reason/bookmark fields against that consumer. Existing generation/review identifiers and scoring rules are preserved.

After upgrading, verify a few known SQL reviews/reasons, add an input folder through Settings, and try one desktop plus one phone with different names. Use the cache preview before optional cleanup; retain the pre-migration snapshots.
