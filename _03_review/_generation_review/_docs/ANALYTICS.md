# Data access and GBR analytics integration

The platform launcher uses the shared `_98_database/gbr.sqlite3`. Standalone
review can select another database through configuration or `--database`.
Analytics can read the selected database or consume its CSV, JSON and Parquet exports.

## Identifiers and units

| Field | Meaning |
| --- | --- |
| `media_files.sha256` | SHA-256 of exact FLAC-container bytes, including tags. |
| `audio.pcm_sha256` / `audio.id` | SHA-256 over a versioned rate/channel header and decoded little-endian signed int32 PCM. Independent of FLAC tags/compression. |
| `generation_id` | SHA-256 of audio identity plus a metadata-provenance fingerprint. |
| `yaml_sha256` | SHA-256 of original YAML bytes, including whitespace/line endings. |
| `review_inherited` | 1 when a generation uses an assessment entered for another provenance record of the identical recording. |
| Time fields | UTC ISO timestamps; event/range offsets and durations are seconds. |
| Scores | Original integer 0–5 ratings; averages are exact quarter-point means. |
| Peak / RMS | dBFS; estimated oversampled true peak is dBTP. |
| Loudness / LRA | LUFS / LU. |
| Spectral centroid | Hz; flatness and flux are unitless. |

Treat the PCM digest as an application-defined identifier. It is not the digest of a standalone WAV file. Native bit-depth variants with different decoded sample values are different recordings; compression/tag variants with the same samples share identity.

## SQL tables and views

`songs`, `audio`, `generations`, `media_files`, `generation_sources`, `generation_metadata`, `reviews`, `review_history`, `reviewers`, `assessments`, `queue_reservations`, `sessions`, `queue_skips`, `analyses`, `audio_events`, `segments`, `archive_operations`, `publishing_selection`, `publishing_history`, `preferences`, `scan_errors`, `schema_version`.

`latest_reviews` exposes the latest revision per recording. `analytics_dataset` joins generations, canonical song context, generation-specific genre/cluster, flattened common MiniMax settings, latest human ratings, latest machine metrics, current file paths and publishing state. Full generation metadata remains in `generation_metadata.parsed_json`, `embedded_json`, `raw_yaml` and the exact `raw_yaml_bytes` BLOB.

Analysis revisions remain separate; `audio_events` / `segments` reference a specific `analysis_id`. Review and publication histories are append-only. `archive_operations` retains plans, exact hashes, verification results, retry counts and errors.

## Read-only Python access

```python
import sqlite3
from pathlib import Path

path = Path(r"C:\GBR\ReviewTool\data\review.sqlite").resolve()
db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
db.row_factory = sqlite3.Row
rows = [dict(r) for r in db.execute("SELECT * FROM analytics_dataset")]
```

SQLite WAL supports simultaneous readers. Do not set `immutable=1` on the live database, because it can ignore uncheckpointed WAL data. Long-running readers can keep the WAL from shrinking; close read-only connections after use.

## CSV / Parquet / JSON

All three formats support these dataset kinds:

| `kind` | Data |
| --- | --- |
| `generations` | Latest generation-level joined dataset plus flattened audio metrics. Default. |
| `reviews` | All reviews/revisions, including stable reviewer ID, name, assessment ID and assessment revision. |
| `reviewers` | Stable IDs, display names, canonical matching keys and creation dates. |
| `events` | Detector type, times, severity, confidence, supporting measurements and algorithm/version. |
| `segments` | Persisted fast/quick/standard selection plans and selection rationale. |
| `analyses` | Every retained analysis revision and aggregate measurements. |
| `review_history` | Reviews, skips, corrections and reassessments. |
| `publishing_history` | Publishing-status changes. |
| `preferences` | Relative A/B choices and normalisation targets. |

Nested measurements remain JSON strings in tabular exports. CSV uses UTF-8 with a BOM for Excel compatibility. Empty Parquet exports still produce a valid file. Generation exports include both inherited and directly assessed provenance records; do not double-count identical audio when estimating sample sizes.

Polars example:

```python
import polars as pl
df = pl.read_parquet("reviews.parquet")
assessed = df.filter(pl.col("review_id").is_not_null() & (pl.col("review_inherited") == 0))
summary = assessed.group_by(["title", "genre", "sampler", "scheduler"]).agg(
    pl.len().alias("n"), pl.col("consistency").mean(),
    (pl.col("bucket") == 5).mean().alias("bucket5_fraction"),
)
```

Polars is an analytics consumer, not an application runtime dependency.

## Example controlled queries

Run the included `queries.sql` statements or adapt them to your own groups. Human scores are the outcome; automated markers are supplementary measurements.

- Group sampler/scheduler comparisons by song and genre before combining them.
- Count recordings, not inherited provenance rows, for overall bucket proportions.
- If one recording has conflicting generation provenance, do not infer which parameter set produced it without external evidence. All candidate provenance remains traceable.
- Compare generation duration only when available and measured consistently. `generation_duration` is imported from YAML, not inferred from the audio length.
- A retrospective association between a detector event and low human scores does not validate the detector’s confidence as a calibrated probability.
- Correction disagreement is not automatically independent inter-rater reliability. `review_history.action` distinguishes explicit reassessment from correction, while `reviewer_id` and `assessment_id` distinguish a person’s assessment from its corrections. The current first pass assigns only never-reviewed audio; second-pass aggregation remains deferred.

## Authenticated API

There is no unrestricted SQL or filesystem endpoint. APIs accept identifiers and predefined export kinds. For non-browser clients, send `X-GBR-Request: 1` on POST requests. If authentication is enabled, establish a session first and retain cookies:

```python
import requests
s = requests.Session()
base = "http://127.0.0.1:8765"
s.post(base + "/api/login", json={"password": "your password"},
       headers={"X-GBR-Request": "1"}).raise_for_status()
manifest = s.get(base + "/api/publishing-manifest").json()
with open("events.csv", "wb") as f:
    f.write(s.get(base + "/api/export/csv?kind=events").content)
```

`requests` is optional for this example. Read-only consumers can use SQLite without browser sessions.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/queue/next` | Current/next blind review track; no calculated score or model settings. |
| `GET /api/queue/back` | Selected reviewer’s previous review’s four scores for explicit correction. |
| `GET /api/tracks/{id}/preview` | Neutral waveform/marker/sample-region preview. |
| `GET /api/playback/{id}?mode=full\|fast\|quick\|standard&format=flac\|wav` | Cached or original audio, with range-request support. |
| `GET /api/export/csv\|parquet\|json?kind=...` | Predefined datasets. |
| `GET /api/publishing-manifest` | Selected/published generation locations, hashes, states and dates. |
| `POST /api/publishing/{id}` | `status` plus optional `external_reference`; changes only publishing metadata. |
| `GET /api/validation` | Integrity/path checks and within-recording review revision spans. |
| `POST /api/backup` | Consistent SQLite snapshot download. |

Browser scoring remains intentionally minimal. Detailed machine findings and parameters are available through the database and exports, not through initial-review cards.

## Version 1.1 additions

`structural_transition` events include boundary time, novelty, threshold, sensitivity and feature source in `measurements_json`. Cached retune analyses include `retuned_from_analysis_id` and `feature_source_version` in metrics. Reviews can use mode `fast`; listening JSON additionally retains `analyses_played`. Existing views/fields remain available; consumers that enumerate modes must accept `fast`.

`POST /api/queue/prefetch` prepares a next playback cache without advancing or writing a rating. `GET /api/queue/next?include_preview=true` includes the public preview. `GET /api/tracks/{id}/analysis-state` returns the latest analysis ID and state. Playback accepts optional `analysis_id` to pin a sample plan. `POST /api/retune` schedules cached retuning; `GET /api/mobile` returns host-only connection information.

## Version 1.2 reviewer attribution

`review_dataset` and `review_history_dataset` expose reviewer names alongside the underlying records. `latest_reviewer_reviews` keeps the latest revision per assessment. `analytics_dataset`, generation/review/history exports and the publishing manifest include `reviewer_id`, `reviewer_name` and `assessment_id`; reviews and generation analytics also contain `assessment_revision`. Export JSON has schema version **2**. The manifest keeps its existing top-level structure with additive attribution fields. The old per-audio `revision` and `latest_reviews` stay available for the unchanged archive rules.

`assessments` has a unique `(audio_id, reviewer_id)` pair. Corrections append rows sharing that assessment and increment `assessment_revision`. Another reviewer’s future independent assessment would have another ID; no assignment or consensus policy is enabled here. Group by recording/assessment and use the latest assessment revision to avoid counting corrections as independent votes.

Queue, Back, scoring, skip, reassessment and candidate APIs now require `X-GBR-Reviewer: <stable ID>`; a missing or unknown ID returns **428**. After existing shared-password login, call `POST /api/reviewer/select` with `{"name":"Simon"}` and `X-GBR-Request: 1`, then set `s.headers["X-GBR-Reviewer"] = response.json()["reviewer"]["id"]`. `GET /api/bootstrap` lists existing names for an authenticated session. `POST /api/reviewer/heartbeat` with `{"current_id":"..."}` renews owned leases atomically; clients should renew every ten seconds. The browser does this automatically. Export and media downloads retain the existing shared access controls. Names provide attribution, without individual account permissions.

## Version 1.3 review context

Reason and flag snapshots belong to individual review revisions; exports have JSON schema version 3. `bookmarks`/`bookmark_history` are reviewer-specific datasets. See [REVIEW_CONTEXT.md](REVIEW_CONTEXT.md) for fields/endpoints. Unreviewed distribution intentions are excluded from the publishing manifest until reviewed.

## Version 1.6 observations (JSON schema 4)

`review_dataset` and `review_history_dataset` add `tags_json` and
`tags_recorded`; generation analytics add `review_tags_json` and
`tags_recorded`. Tabular tag lists are JSON strings containing stable IDs,
polarity and current readable labels/categories, including disabled tags.
`tags_recorded=0` means an older client/review did not supply tag data; 1 with an
empty list means no observations were selected. Neither case expresses approval
or criticism. Existing reasons remain separate and unchanged.

Additional CSV/JSON/Parquet dataset kinds:

| Kind | Contents |
| --- | --- |
| `review_tags` | One row per selected tag/review revision, with reviewer, recording, generation settings, genre/cluster and four scores. |
| `tag_definitions` | Stable vocabulary IDs, current labels, ordering and active status. |
| `tag_summary` | Positive/negative counts over latest revisions per reviewer/recording, distinct recordings, denominator and score averages. |

`review_tag_dataset` retains every revision and identifies
`is_current_review` (latest per reviewer/assessment) and
`is_latest_recording_review` (latest across reviewers). It joins the generation
actually reviewed, so inherited YAML provenance rows cannot multiply counts.
For historical revision analyses select explicitly; ordinary frequency reports
use `is_current_review=1`.

`GET /api/review-tags/analytics?group_by=text_encoder,dit,sampler,scheduler`
returns separate population and observed-tag tables. Other grouping fields are
`reviewer_id`, `encoder_cfg`, `top_k`, `sampler_cfg`, `sampler_steps`, `song_id`,
`genre`, and `cluster`. An optional `reviewer=<id>` scopes both tables. Unknown
hardware/settings remain NULL groups. Population rows distinguish reviewed
recordings, current reviews, reviews supplying tag data and tagged recordings.
Observation rows include four score averages and
`recording_frequency=recordings/reviewed_recordings`. Missing selections do not
create positive/negative rows. Multiple reviewers may give opposite observations
for one recording: report reviewer counts alongside distinct recording counts;
do not sum polarity counts as though they were disjoint recordings.

For direct SQL, for example:

```sql
SELECT text_encoder,dit,sampler,scheduler,tag_id,polarity,
       COUNT(DISTINCT audio_id) AS recordings,
       COUNT(DISTINCT review_id) AS reviews,
       AVG(audio_quality),AVG(composition),AVG(consistency),AVG(realism)
FROM review_tag_dataset
WHERE is_current_review=1
GROUP BY text_encoder,dit,sampler,scheduler,tag_id,polarity;
```

Review JSON exports also include vocabulary, reviewer/assessment records and
review history for lossless reimport. See REVIEW_CONTEXT.md for the import
identity checks and migration behaviour. JSON's new schema version is 4; older
top-level dataset names and existing fields remain available.
