# Coverage of the supplied tool document

| Specification area | Delivered implementation | Qualification / decision |
| --- | --- | --- |
| 1. Discovery | Recursive multi-root FLAC/YAML scan, periodic refresh, embedded tags, file hashes and unique generation records. | Metadata tag/name assumptions and duplicate rules are documented. |
| 2. Playback | FLAC streaming, range requests, lossless WAV fallback, seek/restart/volume at default 1× speed, continuous quick/standard sessions and individual snippet replay. | Browser decoding and mobile OS controls can differ. |
| 3. Ratings | Four strict 0–5 dimensions, fourth-choice submit, exact mean, half-up buckets, Back/corrections, skip and explicit reassessment. | No score-derived classification appears during reviewing. |
| 4. Interface | Minimal responsive review view, mandatory title/genre, 24 wide rating buttons, 50 px mobile height. | Long titles and smaller usable viewports may require scrolling. |
| 5. Visualisation | Cached left/right waveform, seek, markers, sample regions, manual ranges, cached spectrogram/cursor. | Stereo measurements remain stored/exportable; no biased technical-inspection panel is added to initial scoring. |
| 6. Automatic analysis | Peaks, loudness, spectrum, quiet/dropout, stereo and texture/consistency events with times, versions, confidence and measurements. | Heuristic event detection and engineering meter estimates; not calibrated/certified judgments. |
| 7. Queue | Random/alphabetical/date, direction, song/genre/directory filters, persisted current/previous items, skip-return pass. | Independent reviewer sessions and exclusive expiring reservations for current/ahead audio; first pass only. |
| 8. Archive | Six buckets, readable hash names, exact YAML preservation, copy/verify/commit/delete journal, cross-drive support, startup recovery/retry, correction relocation. | Unrelated files are never overwritten. Archive-root relocation is deliberately controlled. |
| 9. Duplicates / A/B | Container plus PCM hashes, separate provenance records, one review per identical audio, blind same-song loudness-matched preference. | Shared assessments are explicit in exports; one FLAC with extra provenance sidecars. |
| 10. Publishing intent | Bucket-5 pass, selected/published/passed/withdrawn states, dates/history/reference, selection manifest. | No publishing or generation workflow is executed. |
| 11. Database | SQLite WAL, indexed tables, strict score checks, full raw/parsed generation metadata, separate scores/events/history/journal/status. | Local disk recommended for SQLite. |
| 12. Analytics | Joined SQL view, CSV/JSON/Parquet exports, per-event/review/history datasets, controlled queries, validation and efficiency examples. | Existing GBR importer integration requires that repository/schema. |
| 13. Architecture | Modular Python/FastAPI, one worker, local caches, static dependency-free browser frontend. | No Node frontend build or cloud runtime dependency. |
| 14. Mobile/LAN | Responsive interface, signed-cookie password sessions, exact host checks, same-origin writes, host-only folder editing, PWA assets, Media Session. | HTTPS/trusted certificate needed for full LAN PWA support; physical lock-screen controls unverified. |
| 15. Reliability | Exclusive filenames, exact hashes, transaction-first review, recoverable operations, failed-move retention, OS worker lock, consistent backup, reconciliation and corruption handling. | An externally lost original cannot be reconstructed from metadata. |
| 16–17. Workflows | Initial blind review → archive → separate selection, plus explicit status updates and manifest consumption. | The review and publishing systems remain independent. |
| 18. Phases | All four phases implemented within this local package. | Optional comparison/API/Parquet/PWA assets are included. |
| 19. Core rules | Human scores primary; neutrality during review; publishing is metadata; generation provenance and history retained. | Detailed decisions and limits are listed in GAPS_AND_ISSUES.md. |

Implementation entry points: `gbr_review/indexer.py`, `analysis.py`, `playback.py`, `service.py`, `archive.py`, `db.py`, `app.py`, `exports.py`, and `static/app.js`.

## Version 1.2 specification

Reviewer identity and guidance requirements are mapped individually in [MULTI_REVIEWER_REVIEW_AND_GAPS.md](MULTI_REVIEWER_REVIEW_AND_GAPS.md). Playback-speed and zoom controls were removed as requested; spectrum viewing and seeking remain. All four rows now show guidance and the 0/3/5 anchor captions.

## Version 1.2.1 rating UI addendum

The grouped 1–2 and 3–4 captions/brackets supersede the version-1.2.0 score-anchor layout. The centred exceptional-rating reminder replaces the hotkey instruction, while shortcuts remain functional. See [UI_BRACKETS_UPDATE.md](UI_BRACKETS_UPDATE.md).
