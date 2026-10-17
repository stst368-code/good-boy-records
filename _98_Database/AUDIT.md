# Supplied database audit — 17 October 2026

A trial migration was completed using the two supplied archives. Both sources
passed full SQLite integrity and foreign-key checks. The originals were opened
read-only and their file hashes were checked again after the migration:

| Extracted source | Bytes | SHA-256 |
| --- | ---: | --- |
| `.minimax_pipeline.sqlite3` | 1,158,651,904 | `c41b73f2c516a3190976ded75d710fafadcd1c78d6a40e114d54dc4c27ebab57` |
| `review.sqlite` | 823,468,032 | `8ccd8879e56b386ff964a66c576ab4cf04c3e69baf83af3aac50b15f625fcf11` |

The generated report stores hashes of SQLite backup snapshots. Those may differ
from the source file hashes because the backup API produces a consistent SQLite
image rather than a raw filesystem copy.

## Records preserved

| Data | Source | Imported |
| --- | ---: | ---: |
| Legacy jobs | 121,475 | 1,860 |
| Legacy timing events | 17,219 | 5,788 |
| Naming counters | 1,970 | 1,970 |
| Review audio identities | 2,005 | 2,005 |
| Review generation/metadata records | 3,398 each | 3,398 each |
| Reviewers | 2 | 2 |
| Assessments | 1,287 | 1,287 |
| Reviews | 1,295 | 1,295 |
| Review history events | 1,316 | 1,316 |
| Bookmarks | 3 | 3 |
| Publishing selections | 35 | 35 |
| Analysis records | 3,300 | 3,300 |
| Audio events | 178,919 | 178,919 |
| Segments | 46,365 | 46,365 |

All 25 original review tables were reconciled using row counts and typed-value
fingerprints, including unchanged raw YAML byte fields. Every original column
and value of the retained legacy rows was reconciled as well. The
shared output passed integrity and foreign-key checks. No native runs were
created and no source YAML or audio was changed.

The existing review `Database` class also opened the shared result without
requiring another migration. Its analytics view returned all 3,398 generation
records, and all 25 original review table fingerprints still matched afterward.

## Issues and interpretation

The source queue contains 3,507 completed, 18 planned, 2 sampling and 117,948
withdrawn jobs. The revised import excludes all 117,948 withdrawn jobs and 1,667
other jobs without a usable review association, along with 11,431 timing events.
All 1,860 retained jobs are completed history, not runnable requests. The resulting
database is 848,007,168 bytes, down from 1,926,299,648 bytes in the original full
history trial. Both source files remain unchanged.

There are 2,952 review-generation records with a unique legacy filename match and
agreement on all eight recorded generation parameters. Another seven match the
filename but have incomplete parameter evidence. The remaining 439 have no
filename candidate. No conflicting or ambiguous candidates were found in these
snapshots. These are metadata associations, not audio hash verification, and do
not collapse distinct review or audio identities.

The 439 unmatched review generations include 160 saved reviews covering 159
audio identities. They are retained under the selected cleanup policy; deleting
unlinked runner jobs is distinct from deleting saved review data.

The populated timings contain 2,566 `full_generation_total` events, in addition
to 1,019 conditioning totals and 1,019 sampling totals. The old summary omitted
the full-generation stage. The new performance view includes it; billable flags
govern cost aggregation rather than adding nested parent/child timings together.
Worker labels alone do not identify hardware reliably, so GPU model remains a
separate grouping field. Historical VRAM measurements are unavailable and remain
unknown. Historical timestamp strings are retained without assigning a guessed
timezone.

## Writing input trial

The new catalogue preflight selected 84 catalogue entries: 83 master tracks and
one variant. It found the missing `cheerleader.yaml`, a duplicate `title` in
`incredible.yaml`, a master title/filename disagreement in `sixteen-balls.yaml`,
162 missing/invalid identity fields, 79 missing captions and 82 inputs with
unresolved generation settings. Existing legacy `version` values were reported
without converting them to styles. The variant may legitimately retain its
master song title despite a different filename.

A separate sandbox copy of a song, with explicit test IDs and parameters, was
prepared against the populated shared trial database using a real caption
template from the repository. Snapshot and request hashes verified; lyric bytes
were identical. This created one prepared test request and no inference attempt.
It did not edit the writing catalogue or its source YAMLs.

Existing stored Windows paths include an older `C:` location, while the supplied
directory listing ends at an `E:` repository location. That is a relocation issue
to verify on the user's machine, not proof that historical files are missing.
The migration does not rewrite paths. The directory listing also includes local
generated/cache material absent from GitHub; the Git repository alone is not a
complete backup of working data.

This trial validates the database migration. It does not validate HTTP/browser
flows, access to historical audio, native MiniMax inference or RunPod execution.
Those require their respective runtime dependencies, files and hardware.
