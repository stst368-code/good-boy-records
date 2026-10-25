# Generation controller

The controller now selects inputs, validates catalogue identity and YAML syntax,
resolves parameter provenance, and freezes requests in the shared database.
It includes exact source snapshots, narrow output patches and successful-output
timestamps. Local native MiniMax execution, compatible conditioning caches,
automatic hardware discovery and stage/memory telemetry are implemented.
Use the Generation tab in the unified app for local, ComfyUI, dedicated RunPod
and serverless workers. [The first local test](LOCAL_TEST.md) remains available.

## Production and experiments

**New batch → Run purpose** defaults to **Production**, which sends completed
recordings to the review inbox. Choose **Experiment** for encoding shortlists,
sampler/step sweeps and other trials. Give the batch an optional name, then use
the same worker selection, caption and parameter controls. The **Experiments**
tab has its own active/completed/failed sections; the production queue is separate.

Experiment bundles live at `_02_generation/_experiments/<batch-id>/`, with the
same FLAC, completed YAML, exact source YAML and generation report as production.
They do not enter review, quality analytics or production output counters. Broad
review scans and copied FLACs also respect their embedded experiment purpose.
Measurements stay in the existing shared database: all attempts, failures,
stage timings, hardware, reported peak VRAM and cache observations. Missing
measurements and unpriced cost remain null, never zero estimates. Stock ComfyUI
reports execution timing and available cache events; it cannot report per-song
peak VRAM without worker instrumentation.

Expand a batch's configurations to listen, inspect measurements, order by steps
or total time, and export **Performance CSV** (one row per attempt, including
stage/resource details). **Send to review** moves one existing bundle to the
production inbox and updates its ledger paths. Its original experiment purpose,
run/attempt IDs, timings and source bytes are retained; it then participates in
normal review/quality analytics. **Delete output files** permanently removes only
an unpromoted experiment's verified bundle, keeping measurements, source snapshots
and worker encoding caches. Interrupted moves/deletions resume with the same
button. Unexpected/changed files and shared ownership block removal. Clean any
pending temporary worker output first using the existing batch cleanup control.

**Publish comparison to website**, inside an experiment batch, publishes a selected
matched set using the existing **Settings → Publishing** R2 configuration. Choose
the varied parameter, one of the available matched sets, a public title and the
website position. Lossless FLAC is the default: its public copy has private tags,
embedded YAML and workflow blocks removed while preserving decoded PCM exactly.
MP3 is available for smaller downloads. Neither option alters the original bundle
or creates review/distribution entries. Other controls, source and model revision
must match; repetitions remain separate and missing/failed settings are excluded.

Public comparisons have their own R2 namespace:
`gbr/v2/experiments/catalogue.json`, immutable manifests under
`gbr/v2/experiments/sets/`, and content-addressed audio under
`gbr/v2/experiments/objects/`. They never enter `gbr/v2/catalogue.json`. How it’s
made reads its published sets directly from R2, including each clip’s available
timing/hardware/cache measurements. Publishing a 1–100 step set updates the player’s
range automatically, without editing guide YAML or rebuilding the website.

The app records publication progress/retries in `generation_artifacts`, using the
existing shared database. Verified public renders are removed from local scratch
after upload. Interrupted uploads retain their receipts and resume explicitly.
Uploaded sets keep playing when the studio is closed or local experiment output
files are deleted. **Remove from website index** removes the listing; immutable
objects remain available to existing direct links. Set `GBR_EXPERIMENT_CATALOGUE`
in `_96_website/showcase-host.js` to the matching public bucket if changing hosts.

For a controlled step sweep, keep the caption, lyrics, encoder settings and
duration fixed; set **Steps → Range**, start `1`, stop `100`, step `1`. Selecting
Experiment initially chooses **Same automatic seeds across batch**. Explicit
seed values/ranges still take precedence; use fixed seeds to reuse a known
encoding from an earlier batch. Native **Reuse when supported** creates/reuses
the matching cache on each worker; **Require cached encodings** refuses a missing
native cache. ComfyUI uses its graph cache. Cache availability is worker specific;
matching seeds alone do not copy caches between devices or guarantee a cache hit.

The CLI accepts `plan --purpose experiment --experiment-label "Step sweep"`;
the default output root then becomes `_02_generation/_experiments`. Purpose and
label are recorded in the existing frozen request and batch policy JSON. Output
actions use the existing audio artifact metadata as a durable journal. No extra
database or table migration is needed; historical requests without purpose remain
production. Original Writing YAMLs and their formatting are unchanged.

## Run the implemented planner

Use Python 3.10 or later and the verified shared database from `_98_database`:

```powershell
py -3 -m pip install -r _02_generation/requirements.txt
py -3 _02_generation/run_generation.py inspect --all
py -3 _02_generation/run_generation.py inspect --song moses-supposes --song new-mills-new-mills --parameters my-parameters.json
py -3 _02_generation/run_generation.py plan --album "Album name" --order album --parameters my-parameters.json --on-error cancel
py -3 _02_generation/run_generation.py plan --all --parameters my-parameters.json --on-error drop
py -3 _02_generation/run_generation.py verify _02_generation/_runtime/BATCH_ID/manifest.json
_99_tools/_workers_infdit/.venv/Scripts/python.exe _02_generation/run_generation.py execute _02_generation/_runtime/BATCH_ID/manifest.json --limit 1
```

`--song` accepts a song ID (including its variants) or exact filename stem.
Repeat it, `--file` or `--album` for multiple selections. `--all` includes missing
catalogue files and uncatalogued YAMLs so errors cannot disappear from the audit.
Ordering supports selection order, filename or validated album position. With
no selector in an interactive terminal, a numbered file picker is shown. The
interactive error policy offers drop/cancel; unattended runs default to cancel.
Inspection returns exit code 2 for invalid inputs and does not create a batch.

`--built`, `--styles`, `--styles-encoding`, `--template-root`, `--database` and
`--workspace` override locations. The default style CSV is
`_01_writing/_references/styles.csv`, whose columns are `cluster` and
`style_primary`. The older `references/styles.csv` has incompatible headings and
is not silently substituted. Caption paths resolve under the separate
`_01_writing/_references` root. CP1252 CSVs require explicit
`--styles-encoding cp1252`.

For review-derived selection, add `--below-rating 3`, optionally `--reviewer ID`,
`--rating-cluster NAME` and `--rating-style NAME`. This uses mean distinct latest
assessments, excludes unreviewed tracks, and records review IDs. Explicit numeric
song/variant IDs win; historical records without either ID may match a unique
catalogue title. Incomplete IDs and ambiguous titles are not guessed. Cluster/style
filters require the effective request to target the same combination; supply
explicit parameter overrides when the writing YAML targets something else.
Legacy `version` is never automatically interpreted as `style`.

## Parameter specification

`--parameters` reads JSON with `defaults`, `preset`, `batch` and `rerun` value
objects. Nonblank source YAML values take part automatically. Lowest-to-highest
default priority is defaults, preset, selected template, YAML, batch, rerun.
`precedence` changes the source list and `field_precedence` changes individual
fields. Specific rerun overrides always remain final. Unknown fields are errors.
Models and all required numeric/sampler controls must be chosen explicitly from
the input or specification; no worker/model fallback is invented. The requested
duration defaults to 360 seconds. Encoder and sampler seeds may be unsigned
64-bit decimal strings or `"auto"`; omitted seeds become automatic. They are
frozen once, stored as strings and never regenerated by `verify`.

Example override structure (the preset/input must supply the remaining controls):

```json
{
  "batch": {"sampler_cfg": 1.7, "sampler_steps": 30},
  "rerun": {"sampler_seed": "12345678901234567890"},
  "field_precedence": {"encoder_cfg": ["defaults", "preset", "batch", "yaml"]},
  "execution": {
    "encoding_cache": "prefer",
    "conditioning": {"backend": "runpod_serverless", "address": "ENDPOINT"},
    "diffusion": {"backend": "local"}
  }
}
```

Cache policies are `prefer`, `require`, and `disabled`. Conditioning and diffusion
can independently target `local`, `runpod_dedicated`, or `runpod_serverless`, with
an optional worker address. These choices are frozen requests. The local
executor currently accepts local stages with automatically discovered addresses
only; it refuses RunPod or explicit endpoint targets before starting inference.
Model selection belongs to the job, not to the worker configuration. Optional
root `model_revision` pins the native model to a full commit SHA. Otherwise it
is resolved at job start and retained for retries. Execution options also include
`memory_profile` (`low_vram` or `resident_stage`), `cuda_device`,
`vram_sample_interval_ms` (50–5,000), and `offline`. Offline execution requires
an explicit/resolved revision and already cached native model components.

A caption already in the source YAML is retained. With an empty caption, supply
a cluster/style pair; exactly one CSV template must match, or select a specific
`template_id` in the specification. The exact template ID and raw content hash
are recorded when that template actually supplies the effective caption. A
source caption may win the configured precedence, in which case it is not
misattributed to a template. `request_id` supports idempotency and
`parent_review_generation_id` records a rerun's parent; the review button and
immutable-source rerun endpoint remain subsequent interface work.

## Prepared artifacts and output rules

A successful plan creates `runtime/BATCH_ID/manifest.json`, `inputs/` with exact
original bytes, and `requests/` with explicit effective generation changes.
All per-input issues and exclusions are saved in the manifest and shared DB.
Inputs changed after preflight are rejected. Resume verifies the manifest
against its database hash and checks every snapshot/request hash; it uses frozen
copies even if the writing source later changes. Prepared requests do not start
inference. Interrupted filesystem publication can leave an unregistered batch,
which verification refuses; no prepared request is automatically dispatched.

`write_completed_yaml` produces a new sidecar only for a successful generation,
adding the quoted `generation_datetime` in Europe/London `yymmddhhmm` form. Full
UTC times belong in the database. Its publication is atomic and never overwrites
an existing file. Numeric seeds/controls that did not actually change retain
their original spelling and quoting. Lyrics and unrelated fields remain exact
byte slices, including mixed newlines and UTF-8 BOMs. Missing generation fields
use following template anchors; no generation block is appended to the end.
Explicitly changed captions use literal blocks with correct newline semantics.

Ambiguous syntax is rejected before planning: duplicate keys, aliases, anchors,
explicit tags, multiple documents and flow-style roots require deliberate repair.
The generation path never renames or deletes fields or edits writing originals.
Bulk source repairs belong to the separate YAML editor.

```powershell
_99_tools/_workers_infdit/.venv/Scripts/python.exe -m unittest discover -s _02_generation/_tests -v
```

The following sections describe the complete target design, including later
cloud transport and UI work. The supported native controls and current local
memory/measurement behaviour are documented under `_99_tools/_workers_infdit`.

## Import and preflight

Select individual songs, multiple explicit songs, album sets, a directory, or
review-derived combinations. Read `_01_writing/_built` and its `track_list.csv`
and `variant_list.csv`. Resolve song/variant identity before planning. Report
duplicate or missing IDs, missing files, duplicate YAML keys, invalid types,
catalogue disagreements and unsupported generation controls. Do not silently
repair source YAMLs or guess missing identities.

The app resolves a blank catalogue path, the repository folder or `_01_writing`
to `_01_writing/_built` when that container has no catalogue of its own. Relative
paths resolve from the repository, and legacy directory spellings remain usable.
Loading, saved preferences and batch checks use the same resolved directory;
Load displays that directory in the path field. Changing the folder clears the
previous track selection. Explicit custom catalogue folders stay supported;
an invalid custom folder reports its error instead of selecting another library.

Show errors per input before GPU work. The user can exclude specified invalid
inputs and run the remainder, or cancel the batch, fix inputs, and revalidate.
Record exclusions and their reasons with the frozen selection. Review-based
selection must make reviewer scope, score rule, track/variant/cluster/style
aggregation and reviewed/unreviewed handling explicit.

Preserve explicit selection order, support album track order and stable filename
order, and persist the final order. Any random order needs a stored shuffle seed.
Freeze effective parameters, resolved template IDs, model revisions and seeds
before dispatch so resume cannot silently produce a different experiment.

## YAML preservation

Generation never edits, moves or deletes an input file. Store an exact immutable
byte snapshot and its SHA-256. Produce the output sidecar from that snapshot.

Apply narrow syntax-aware edits only to fields actually changed by generation.
Keep comments, unknown fields, field order, multiline lyrics, quoting, indentation
and each existing newline intact. Creating missing generation fields is allowed,
but their insertion must follow declared template anchors. Do not append a
reordered generation block or serialize the whole document. If a construct
cannot be patched safely, stop and report the unsupported input.

Migrate templates to `cluster` and `style`, looking up CSV `cluster` and
`style_primary`. A legacy `version` field is not sufficient evidence of a style:
explicit migration rules must distinguish it from variant/version identifiers.
Where a style pair matches several caption templates, persist the selected
template's exact ID and content hash.

The proposed completion field is `generation_datetime: "yymmddhhmm"`, using
Europe/London time; full UTC timestamps belong in the database. Successful
completion adds it to the output copy only. Run IDs provide uniqueness.

## Parameters and reruns

Expose a per-field resolution policy. A proposed default, from lowest to highest
priority, is engine defaults, preset, selected template, source YAML, batch
overrides, specific rerun overrides. Show each effective value and its source.
Only generate a seed when missing or explicitly requested as automatic. A rerun
with a fixed seed must retain it. Never silently substitute unsupported sampler,
scheduler, model or dtype settings.

A review rerun request should contain an idempotency key, parent review generation
ID, immutable source hash, precise overrides and requested selection scope. Keep
parent/child run links; retries are attempts of the same run. Cache keys include
all conditioning inputs, encoder revision, relevant parameters and cache format
version. Record whether an encoding was generated or reused and its originating
artifact, without charging its creation cost again on every consumer.

## Execution and measurement

Workers live in `_99_tools`, with local, RunPod dedicated and RunPod serverless
adapters. Configure the address, enabled state and optional role preference.
Query capabilities, model inventory and hardware at job start. The request picks
models; the result records actual checkpoints and modes. Identify the endpoint
by its address and its discovered instance/GPU for that session.

Native MiniMax execution must be validated against real checkpoints before
declaring the old ComfyUI options compatible. Old conditioning caches and model
conversion formats need explicit compatibility checks, not a filename match.

Record conditioning, diffusion, decoding, transfer and total wall time, including
failures, cold loads, cache hits and retry attempts. For each participating GPU,
record allocated/reserved peaks and sampled device usage with baseline, sampling
interval and coverage. Keep remote encoding and local diffusion measurements
separate; simultaneous jobs and other GPU processes affect observed device usage.

Separate active-work cost estimates from provider billing and idle time. Keep
unknown prices null and currencies separate. Analysis should compare cost per
audio minute, successful take and review quality threshold alongside peak VRAM,
tested hardware capacity, OOMs, concurrency and execution mode.

## App experiments

In the Generation tab, an empty template selection uses random captions by
default. Choose YAML caption or catalogue style when appropriate, or provide an
explicit template ID. Random caption counts select unique templates per track;
cluster/style filters narrow that pool. The preview freezes the chosen captions.

Set generations per combination and optional parameter lists or inclusive
`start:stop:step` ranges. Grid mode runs every combination; sample mode draws the
specified number of combinations independently. Total jobs are tracks × captions
× combinations × repetitions, capped at 10,000. Invalid cases invalidate their
source input so it can be dropped explicitly or the batch cancelled. Sweep values
win over ordinary YAML/form priorities; review rerun overrides remain final.
Every run retains its effective controls, template identity and experiment
indices. Automatic seeds are frozen separately per run before dispatch.

The app groups controls into Models, Text conditioning, Diffusion and Queue/output.
Each numeric field selects YAML/worker, worker default, fixed value, list or range.
Encoder/DiT and sampler/scheduler choices use dropdown pairs; values within a row
stay linked. Separate groups combine with numeric ranges. An API experiment can
express these as `groups: [[{sampler: "euler", scheduler: "simple"}, ...]]`;
fields cannot appear in two groups or in both a group and a sweep.

All available workers share the checked queue. A frozen run lists compatible
worker alternatives, including each worker's model choices and execution settings.
The shared SQLite claim transaction selects one alternative and records its actual
controls. Workers take the next compatible job after finishing a song; faster
workers can take more jobs. Disconnected/uncertain attempts stay attached to their
original worker and require explicit reconnect/retry. No extra database is used.

Model and sampler selection initially uses each worker's defaults/available choices.
Choose YAML mode to require the source choices, or a listed pair to constrain the
experiment. Explicit missing checkpoints are errors; blank models resolve before
dispatch. The source YAML is never changed.

Controller events include UTC timestamps and each run's start/attempt identity.
Clear log view is a browser display action; Show full logs restores the retained
text, and new events remain visible after clearing.

Outputs live in `_02_generation/_generated`, as unique generation bundles,
optionally grouped by track or album. Review moves the full bundle into the
review archive and updates artifact locations; it leaves no second audio copy.
Temporary raw FLACs are removed only after final artifacts verify. Jobs offers a
cleanup-only action, which also covers leftover raw files from earlier versions.
