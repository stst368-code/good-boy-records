# Generation controller contract

This is the agreed direction for the new runner, not an executable inference
implementation. The first increment provides a shared database migration in
`_98_Database` and a review launcher in `_97_App`.

## Import and preflight

Select individual songs, multiple explicit songs, album sets, a directory, or
review-derived combinations. Read `_01_Writing/_Built` and its `track_list.csv`
and `variant_list.csv`. Resolve song/variant identity before planning. Report
duplicate or missing IDs, missing files, duplicate YAML keys, invalid types,
catalogue disagreements and unsupported generation controls. Do not silently
repair source YAMLs or guess missing identities.

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

Workers live in `_99_Tools`, with local, RunPod dedicated and RunPod serverless
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
