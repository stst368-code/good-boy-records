# GBR website

The player, knowledge map and Signal Observatory originated in the public
`stst368-code/porto/good-boy-records` site at commit
`25bb37cacd292429972c8efa58fe8ab2407eea78`. This version connects them to the
shared GBR song/variant, review and worker schemas.

Run `py -3.12 _96_website/build.py --serve --open`, or `Preview-Windows.bat`.
Only the generated `_site` directory is served. Ordinary builds use committed
public metadata and need no database, worker or media credentials.

## Current research data

Refresh from the existing shared database and preview in one command:

```powershell
py -3.12 _96_website/build.py --export-platform --serve --open
```

The default source is `_98_database/gbr.sqlite3`; override with `--database PATH`.
The reusable exporter opens it read-only, takes a consistent snapshot and
atomically replaces `_data/_analytics/platform.json`. It performs no migration,
creates no database backups and never uploads media or changes review flags.

The Observatory switches between the new four-score projection and Porto's
historical single-grade archive. It never pools these two review models. Current
reviews use the latest revision per reviewer, one row per PCM recording, and
preserve positive/negative tag IDs, inactive vocabulary and missing tag capture.
Choose any of the four scores or their average; filter tags and compare settings,
cluster/style, encoder/DiT precision, GPU, execution mode, cache policy and VRAM.

Worker measurements require an exact generation/run link. Missing or ambiguous
links remain unknown. Inclusive parent timings/prices are not summed with their
children; complete prices include failed retries. Partial costs remain labelled
as partial. New public projections omit raw YAML, workstation paths, endpoint
addresses, credentials, reviewer names and private Reasons. YAML originals and
review history are never rewritten.

The original public `generations.json`, `summary.json` and `cost-analysis.json`
remain available together. Historical unmatched worker costs stay in their
separate ledger and are not assigned to new recordings.

## Curated player catalogue

Choose **Hidden room · Easter egg only** as the website destination in
Preparation or Release preview to publish a secret track through the same
MP3/FLAC, artwork and lyric pipeline. Its entry lives in
`gbr/v2/hidden/catalogue.json`; the main catalogue suppresses older copies.
Release history can move an existing publication between the two destinations,
reusing its stable ID and audio assets. Withdrawal works in either destination.
There is one local showcase bundle and one shared publishing ledger, with no
database migration. Hidden recordings are omitted from future public research
exports; local reviews and performance data remain intact.

In **Settings → Website · Easter egg**, select an available main-player cover,
preview it and save. Three clicks/taps on that recording's playing cover, with
no more than 1.4 seconds between taps, open `hidden.html`. Changing tracks,
panning or pinching resets the gesture. Disabled is the default. The public
configuration is `gbr/v2/easter-egg.json`, so changing it needs no site rebuild.
The unlisted, no-index page loads hidden releases directly from R2, supports
playback and share links, and has a Main player return button. Existing hidden
tracks remain supported. This is discovery by a secret gesture, not access
control: someone with the URL can visit or share it.

New recordings publish from **GBR → Publish**, after a reviewed distribution
selection reaches Preparation. Choose the prepared source or an exact master,
optional registered artwork (otherwise the GBR sleeve), lossless download and
approved word timings. **Prepare preview** creates MP3 plus optional sanitised
FLAC, artwork, lyrics and numeric light cues. Audition and inspect its public
metadata before **Publish this preview**. Source audio/YAML and reviews stay intact.

Configure **Settings → Publishing · R2 and website** once: the existing public
bucket's S3 API endpoint, bucket name, public media URL and website URL. Enter
the R2 access/secret keys locally; blank fields retain saved credentials. An AWS
profile or `GBR_R2_ACCESS_KEY_ID` / `GBR_R2_SECRET_ACCESS_KEY` also works. Manually saved
keys live only in the ignored device runtime and are never returned by the API,
included in a release or committed. Use a token scoped to the public bucket.
If the old uploader already works on this PC, choose **Use saved rclone r2
connection** instead. GBR reads that existing R2 connection locally at publication
time; no second key copy is created and rotating the keys in rclone takes effect
on the next job. You can also enter `r2` in the endpoint field and save. The
original rclone configuration is never changed. Manual keys and AWS profiles
remain available for devices that do not use rclone.
**Check public connection** verifies a small upload, exact readback, public URL
and CORS. Allow the website origin (normally `https://stst368-code.github.io`)
for GET/HEAD with Range in the bucket CORS settings. No bucket is created or synced.
For a different public media domain, update `GBR_RELEASE_CATALOGUE` and
`GBR_RELEASE_RESEARCH` in `showcase-host.js` and deploy the website with that change.

Objects live at `gbr/v2/objects/<hash-prefix>/<sha256>.<extension>`; a verified,
conditional update to `gbr/v2/catalogue.json` makes them visible. Immutable
catalogue snapshots preserve release history. Upload/check failures retain the
previous catalogue and local preview for explicit retry. An uncertain response
is reconciled by release ID on retry. Successful publication removes verified
local web derivatives; **Discard preview** removes unused previews. **Withdraw
from website** removes the entry without deleting historical objects. Legacy
`showcase/` objects and share identities remain unchanged.

The public player merges these releases with its saved catalogue and falls back
to that catalogue if R2 is unavailable. **Publish research snapshot** exports the
shared database through the existing public allow-list, uploads immutable research
JSON and updates `gbr/v2/research.json`. The Observatory loads this separately from
Porto's older single-grade data. Private Reasons, reviewer names, credentials,
workstation paths and raw YAML are excluded. This introduces no new DB or schema.

`_data/catalogue.json` retains 168 main and nine hidden recordings. Public R2
`showcase/` object keys, catalogue identities and share tokens stay stable.
The local source folders use the repository's lowercase naming convention:
`_templates`, `_analytics`, `_content/_folders`, `_data` and `_tools`.

Refresh only from a complete curated media collection:

```powershell
py -3.12 -m pip install -r _96_website/requirements.txt
py -3.12 _96_website/build.py --refresh-catalogue --showcase "E:\curated-showcase"
```

This reads legacy variant folders and new flat recording bundles. The shared
`_01_writing/_references/styles.csv` controls current cluster/style ordering;
the older colour taxonomy is a fallback for published historical tracks.
`source.yaml` is excluded as a playable entry. The completed YAML, language,
explicit flag, song/variant IDs and generation date stay associated with the
recording. Per-recording sidecars are kept distinct. No source YAML is rewritten.

To restrict a refresh to review selections, add `--publishing-manifest PATH`
using the existing review app manifest. It matches selected/published recordings
by completed-YAML SHA256, requires one matching recipe with audio per selection,
and stops before replacing the catalogue if selections are absent or ambiguous.
This is a complete catalogue refresh, so the manifest must include every main
recording intended for the refreshed player. A folder scan alone does not prove
audio identity; media preparation/upload remains an explicit publishing step.

`--refresh-easter` explicitly reads hidden-track metadata via rclone. Ordinary
builds never scan the inbox/review archive, duplicate audio or remove R2 objects.
Public routes remain `analytics/`, `data/analytics/` and `content-source/folders/`.

## Deployment and checks

`.github/workflows/website.yml` tests and builds the public artifact. A website
push to the repository's current default branch publishes that tested artifact;
manual **GBR website → Deploy to GitHub Pages** also works on that branch.
PR checks never deploy. The duplicate repository-root uploader was removed.
Serve only `_site`; database/configuration,
original inputs, review archives, environments and local media are excluded.
A failed build retains the last completed site and cleans temporary staging.

Run `python -m unittest discover -s _96_website/_tests` for schema/export/build
regressions. `_tests/ui_website.py --browser PATH` checks root/nested routes and
mobile/desktop playback, share links, knowledge and analytics using synthetic
media. `_tests/ui_platform.py --browser PATH` checks the shared-schema projection,
response/tag filters, telemetry and source switching on desktop and mobile.
