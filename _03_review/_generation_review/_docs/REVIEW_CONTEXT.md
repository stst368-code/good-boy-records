# Review context, bookmarks and submission — 1.3.0

Distribution intent keeps using `publishing_selection` and `publishing_history`. The `selected` flag can now be set before first review; `published`, `passed` and `withdrawn` still require a review. The distribution checkbox never starts publishing or an archive operation. Unreviewed intent is omitted from the existing publishing manifest until a human review exists.

`bookmarks` is independent, keyed by generation and reviewer. `bookmark_history` records additions/removals. Selection's bookmark filter exposes the selected reviewer's saved tracks across archive buckets and includes unreviewed bookmarks. Opening an unreviewed bookmark for Review checks exclusive audio ownership before changing that reviewer's queue; it retains their ordinary queue filters and other reviewers' work.

Schema version 3 adds `reviews.reason`, `flags_json` and an optional `submission_id`. The reason and flags snapshot belong to each immutable review revision. Corrections share the existing assessment/reviewer IDs and append a revision. Legacy reasons default to empty; legacy flag snapshots are empty objects rather than invented historical states. Existing values and timestamps are not rewritten. `review_history.details_json` includes reason/flags on new writes.

Reasons are plain text, including line breaks, capped at 20,000 characters. They are displayed with text nodes, without executing HTML. Back/Reassess loads the saved reason; explicit review details show prior revisions. Reasons are saved at review submission, not merely by toggling a marker or skipping.

Auto-submit is a remembered device preference, initially enabled. Enabled preserves fourth-choice submission; disabled exposes a button requiring all four values. Keyboard scoring ignores textarea focus. Failed saves retain the entered form, while submitted requests use a stable retry ID to avoid duplicate writes. Conflicting retries with edited values return an actionable message and cannot overwrite the already saved revision.

Review/flags are committed together when submitting. Marker changes can also persist immediately through the flag endpoint. Shared distribution conflicts are revalidated at save; personal bookmarks cannot clear another reviewer's bookmark. Queue expiry/stale-review checks remain in effect.

## API and exports

Existing authentication and `X-GBR-Reviewer`/`X-GBR-Request` requirements remain. Additive review fields are `reason`, `distribution`, `bookmarked`, `expected_publishing_status` and `submission_id`. Old clients omitting reason preserve it on corrections; absent flag fields preserve current state.

- `GET/POST /api/tracks/{id}/flags` reads or changes markers.
- `GET /api/tracks/{id}/reviews` exposes saved reviews/reasons with attribution.
- `GET /api/publishing?bucket=&bookmarked=true` finds the reviewer's bookmarks.
- `POST /api/queue/open` with `generation_id` obtains an exclusive unreviewed bookmark assignment.
- `GET /api/directories?path=...` supports the host-side picker; remote access remains blocked.
- `GET /api/playback-cache` reports usage; `POST /api/playback-cache/cleanup` accepts `older_than_days` and `dry_run` (default true).

JSON datasets now use schema version 3. Review exports include reason and snapshot fields; generation analytics expose `review_reason`, `review_flags_json` and `reviewer_bookmarked`. Bookmark/history export kinds are added. Numerical scoring and archive rules are unchanged.

## Positive/negative observations — 1.6.0

Review has 20 optional observations below the numerical ratings, grouped into
Vocals, Instruments, Production, Musical Style, Arrangement and Overall. Each
has separate minus/plus buttons: the same polarity clears, the opposite switches.
Clear Tags affects only the current draft. Both buttons stay visible. Tags do
not submit, change a score, change playback or modify the optional Reason.
Choose them while listening or after the first three scores; the fourth score
still submits immediately with the existing autoplay behaviour. Back/Reassess
restores saved observations. Adjust tags/Reason, then choose a rating to save a
new correction revision. The existing optional manual-mode preference remains.

The initial vocabulary was consolidated from 39 nonempty reasons among 2,020
supplied reviews. Fuzz/distortion/glitches share Cleanliness (positive means
clean, negative means unwanted artifacts); flat or robotic phrasing belongs to
Delivery. Timbre and Interest describe instrumental sound and engagement;
Variety explains repetitive arrangements. Energy replaces an overall Realism
tag because Realism already has a numerical metric. Historical reasons are
preserved as written and are not automatically converted into judgments.

Schema **4** adds `review_tag_definitions`, `review_tags` and
`reviews.tags_recorded`. Migration is transactional and idempotent. Before
upgrading an existing database, the normal backup mechanism creates one verified
snapshot beside that database (`backups/before-review-tags-*.sqlite` when schema
3 is already installed). Successful restarts do not create further migration
backups. A fresh database needs no migration backup. No separate tag database,
history reimport or one-time script is required.

`review_tag_definitions` is the authoritative configuration after initial setup:
edit category, label, description, category_order, sort_order or active there.
Keep `id` stable. Add a row to introduce a tag; set `active=0` to disable one.
Restart/reload the app to refresh controls. `review_tags.json` supplies the
initial seed only; updating the application never overwrites vocabulary edits.
Disabled tags remain readable on old reviews and may be retained or removed
when correcting those reviews. New observations cannot select a disabled tag.

Each selection references the immutable review ID and inherits its reviewer and
assessment identity; `(review_id,tag_id)` is unique. Scores, reason, flags, tags
and the history entry commit in the same transaction. Corrections append a new
review and selections without deleting previous revisions. Older clients that
omit `tags` preserve existing selections on corrections; `tags: []` explicitly
clears the new revision. Untagged historical reviews stay valid.

Settings provides Review history JSON, Tag observations CSV and Tag counts CSV.
The host's Import review JSON control restores schema-4 review JSON exports
(up to 64 MiB) into a catalogue that already contains the same generation/audio
IDs. It imports all review revisions, reviewer/assessment attribution, tags and
review history atomically. Identical records are skipped. Missing recordings or
conflicting existing reviews abort the entire import; local reasons and
vocabulary edits are never overwritten. It does not copy media or schedule
archive moves. CSV/Parquet remain analytics formats; JSON provides lossless
round trips. Regular database backups still include the entire catalogue.
