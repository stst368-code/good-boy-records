# GBR website

The galaxy player, knowledge map and Signal Observatory were ported from
`stst368-code/porto/good-boy-records` at commit
`25bb37cacd292429972c8efa58fe8ab2407eea78`.

Run `py -3.12 _04_website/build.py --serve --open` from the repository root,
or use `Preview-Windows.bat`. Ordinary builds need no worker, database or media
credentials. Only the generated `_site` directory is served.

## Source

- `_templates`: the player and legacy genre colours.
- `_analytics`: Signal Observatory interface.
- `_content/_folders`: authored knowledge documents.
- `_data/catalogue.json`: 168 main and nine hidden recordings, with stable IDs.
- `_data/_analytics/generations.json`: the public historical dataset of 1,181
  recordings, dated 10 October 2026, on its original 0–5 rating scale. It is not
  a live view of the shared review database. New four-metric reviews need an
  explicit export mapping that retains their review model.
- `showcase-host.js`: existing public R2 location; `showcase/` object keys are
  retained for audio, artwork, YAML provenance and timed lyrics.
- `_tools`: reusable catalogue and knowledge builders.
- `_site`: ignored public build output, replaced after a successful build.

The optional historical cost ledger and standalone summary are excluded pending
approval. The charts read the generation export directly. Local configuration,
SQLite files, original writing YAMLs, audio, review archives and environments are
never staged. A failed build retains the last complete site.

Public routes remain `analytics/`, `data/analytics/` and
`content-source/folders/`. Relative links work at the domain root and under a
repository URL. The knowledge map describes the current native MiniMax runner,
while metadata for earlier ComfyUI recordings remains historical.

## Intentional catalogue refresh

Use a complete curated collection, not the generation inbox or whole review archive:

```powershell
py -3.12 -m pip install -r _04_website/requirements.txt
py -3.12 _04_website/build.py --refresh-catalogue --showcase "E:\curated-showcase"
```

The builder reads YAMLs without rewriting their bytes. It supports legacy
composition/variant folders and flat recording bundles, current `cluster` and
`style`/`style_primary` fields, per-recording sidecars and existing share tokens.
Ordinary builds use the committed catalogue and never scan a partial checkout.
Optional `--refresh-easter` reads hidden-track metadata through rclone; builds
never upload or remove R2 media.

## Deployment and checks

`.github/workflows/website.yml` tests and builds the public artifact on pull
requests and pushes to `main`. It does not include Porto's CV or the local app.
After merging, set the new repository's Pages source to **GitHub Actions**, then
run **GBR website** on `main` with **Deploy to GitHub Pages** selected. Normal
builds do not deploy or alter production settings.

Run `python -m unittest discover -s _04_website/_tests` for build regressions.
`_tests/ui_website.py --browser PATH` checks desktop/mobile player controls,
root/nested routes, filters, main/hidden share links, knowledge and analytics.
Browser tests use synthetic media; live R2 probes were rejected by Cloudflare
1010 from this environment, so live playback is not claimed as verified here.
