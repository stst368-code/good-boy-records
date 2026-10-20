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

`.github/workflows/website.yml` tests and builds the public artifact. Pages
publishes only when **GBR website** is explicitly dispatched on `main` with
**Deploy to GitHub Pages** enabled. Serve only `_site`; database/configuration,
original inputs, review archives, environments and local media are excluded.
A failed build retains the last completed site and cleans temporary staging.

Run `python -m unittest discover -s _96_website/_tests` for schema/export/build
regressions. `_tests/ui_website.py --browser PATH` checks root/nested routes and
mobile/desktop playback, share links, knowledge and analytics using synthetic
media. `_tests/ui_platform.py --browser PATH` checks the shared-schema projection,
response/tag filters, telemetry and source switching on desktop and mobile.
