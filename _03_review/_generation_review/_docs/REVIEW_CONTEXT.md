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
