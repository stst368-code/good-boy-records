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

| Data | Count |
| --- | ---: |
| Legacy jobs | 121,475 |
| Legacy timing events | 17,219 |
| Naming counters | 1,970 |
| Review audio identities | 2,005 |
| Review generation/metadata records | 3,398 each |
| Reviewers | 2 |
| Assessments | 1,287 |
| Reviews | 1,295 |
| Review history events | 1,316 |
| Bookmarks | 3 |
| Publishing selections | 35 |
| Analysis records | 3,300 |
| Audio events | 178,919 |
| Segments | 46,365 |

All 25 original review tables were reconciled using row counts and typed-value
fingerprints, including unchanged raw YAML byte fields. Every original column
and value in the three legacy generation tables was reconciled as well. The
shared output passed integrity and foreign-key checks. No native runs were
created and no source YAML or audio was changed.

The existing review `Database` class also opened the shared result without
requiring another migration. Its analytics view returned all 3,398 generation
records, and all 25 original review table fingerprints still matched afterward.

## Issues and interpretation

The legacy queue contains 3,507 completed, 18 planned, 2 sampling and 117,948
withdrawn jobs. All are history in the new database. In particular, importing the
two sampling rows does not authorize replaying them on a different backend.
The large number of withdrawn records and repeated prompt content explains much
of the generation database size; this migration deliberately does no cleanup.

There are 2,952 review-generation records with a unique legacy filename match and
agreement on all eight recorded generation parameters. Another seven match the
filename but have incomplete parameter evidence. The remaining 439 have no
filename candidate. No conflicting or ambiguous candidates were found in these
snapshots. These are metadata associations, not audio hash verification, and do
not collapse distinct review or audio identities.

The populated timings contain 2,566 `full_generation_total` events, in addition
to 1,019 conditioning totals and 1,019 sampling totals. The old summary omitted
the full-generation stage. The new performance view includes it; billable flags
govern cost aggregation rather than adding nested parent/child timings together.
Worker labels alone do not identify hardware reliably, so GPU model remains a
separate grouping field. Historical VRAM measurements are unavailable and remain
unknown. Historical timestamp strings are retained without assigning a guessed
timezone.

Existing stored Windows paths include an older `C:` location, while the supplied
directory listing ends at an `E:` repository location. That is a relocation issue
to verify on the user's machine, not proof that historical files are missing.
The migration does not rewrite paths. The directory listing also includes local
generated/cache material absent from GitHub; the Git repository alone is not a
complete backup of working data.

This trial validates the database migration. It does not validate HTTP/browser
flows, access to historical audio, native MiniMax inference or RunPod execution.
Those require their respective runtime dependencies, files and hardware.
