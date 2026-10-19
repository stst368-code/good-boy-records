# Stage 4, workers and R2: implementation and decisions

19 October 2026. Default direction: one GBR app, one shared database, interchangeable
workers, one canonical recording bundle. This document is the current plan.

| Area | Built now | Next integration |
| --- | --- | --- |
| Stage 4 | Preparation tab; reviewed distribution selections enter the existing verified move journal; audio/YAML/native companions move together | Separation/alignment worker environments and final release approval |
| Mixing | Real FFmpeg previews and FLAC master candidates; gain, three-band EQ, convolution room reverb, fades, optional sample-peak limiter; reusable recipes and processing history | Stem faders, compression, loudness-matched comparisons, two-pass loudness/true-peak finishing |
| Playback analysis | CPU energy/bass/brightness features and measured energy-rise events; output has `dance.events` understood by the player | Rich beat/downbeat/section analysis, manual corrections and release assembly |
| Artwork | YAML-derived editable direction, reproducible prompt, selected reference hashes and model-family choice | Actual image diffusion, candidate selection and artwork approval |
| ComfyUI | Read-only worker discovery and API-workflow compiler; explicit mappings and range/choice validation | A validated MiniMax workflow, dispatch, collection, cancellation and full local/RunPod test |
| R2 | Read-only inventory/key plan from the shared artifact registry | Remote inventory reconciliation, release manifests and staged cutover |

## Directory and promotion flow

Writing → generation → review → `_04_preparation` → `_05_website`.
The former `_04_website` source is relocated by the existing compatibility layer.
Public routes, catalogue identities, share tokens and existing R2 media URLs remain
stable. No cloud objects are moved or removed by this update.

A distribution flag alone does not bypass human review. After a complete review,
GBR journals promotion into `_04_preparation/_tracks/<audio_id>/_source`. Review's
existing copy/verify/commit/remove logic moves the FLAC, completed YAML and native
companions and updates all known locations in the same DB. Writing originals stay
protected. Multiple generation records of identical PCM share one physical source.

Existing selected/reviewed recordings are queued when the app starts or when
**Refresh selected tracks** is pressed. The background archive worker performs the
move. A withdrawn selection cancels a pending promotion. Rating corrections retain
the Stage 4 location. Already-promoted recordings remain there if selection later
changes, so work is not silently discarded or moved backwards.

`_derived` contains explicit processing outputs. Preview cache keeps the latest
clip per generation record and retains tiny DB history records after eviction. Full master
candidates remain intentional versions. Clearing previews preserves sources,
master candidates and shared outputs. Open-file failures are reported as in-use.
Crash leftovers are not blindly deleted. Stopped task recovery checks controller
host, PID and process creation time; live or unknown ownership blocks recovery.

## Central data: capture once, reference it

`_98_database/gbr.sqlite3` remains the sole operational database. CSVs remain the
editable master catalogue for song/variant definitions; their IDs link to DB
catalogue entries and frozen generation recipes. Historical output metadata is
not rewritten when catalogue metadata changes. A title or genre change never
requires moving an R2 object.

This update adds **one generic `processing_tasks` table**, shared by preparation,
mastering, alignment, visual analysis and artwork. It stores the immutable recipe,
input hash, state, worker-session reference and operation measurements. Outputs
reuse `generation_artifacts`; hardware/software reuse `worker_sessions`; promotion
reuses `archive_operations`. No per-song, per-model or per-worker databases/tables.
The additive DDL is idempotent. Existing reviews, tags, YAML bytes and IDs stay intact.

The future GPU adapters should extend the existing attempt/timing/resource ledger
through a compatibility migration, rather than introducing another family of
performance tables or recording the same VRAM/cost readings twice. Today's CPU
worker records elapsed time, software and output measurements. Processing recipes/history are available through the existing export system; downloaded export scratch files are removed after transmission. Explicit database backups retain their existing behaviour. The worker does not invent
GPU measurements or cloud prices. Public analytics remains an explicit export,
not another operational DB. The active SQLite file stays beside the app on local/persistent storage; R2 holds backups, not a live mounted database. Other devices use the app API rather than editing independent DB copies.

## Audio preparation and mastering

Recommended dependency graph: choose source/trim → optional stems and mixing →
choose master candidate → alignment + visual analysis → artwork/lyric approval →
release manifest. Keep an identity time map for purely tonal/dynamic processing;
trimming, rearranging or time-stretching invalidates old timing unless mapped.
Every sidecar must identify its audio source hash. Visuals should be recomputed
for the selected master. Never attach old timing/visuals merely by filename.

Use an isolated processing environment under `_99_tools/_workers_processing`.
Do not install Demucs/WhisperX into the native MiniMax CUDA environment. Demucs'
original repository is archived; pin a tested distribution/model and compare a
maintained separator alternative through the same stem contract. Start with
vocals + accompaniment for alignment; four stems add useful mix controls but
increase storage and can expose separation artefacts.

WhisperX should align the supplied YAML lyrics against isolated vocals, with
ASR used for anchors/discrepancy detection rather than replacing the lyrics.
Singing, repeated choruses, multilingual text, overlapping voices and long gaps
need explicit coverage/confidence checks and a correction UI. Unaligned words
remain missing. The current public player has a legacy fallback that evenly
spaces untimed words: release validation should block treating that as alignment,
and the fallback should be changed to visibly untimed lyrics in the release work.

For quality improvements, begin with level/true-peak measurement, small EQ changes,
click/edge fades and conservative dynamics. Convolution reverb is an aesthetic
choice and may reduce clarity. Stem balancing can help an exposed vocal but cannot
recover absent detail. Restoration/enhancement models should be optional A/B
candidates: they can alter singer identity, transients and lyrics. Keep the original
and measure/subjectively review the result. The built limiter controls sample
peaks; it is not sold as a true-peak mastering limiter. Full renders are candidates,
not automatically approved or published. An ingested derived master receives its own PCM identity and keeps its parent processing link; original generation ratings are not silently assigned to changed audio. Previews use the same chain and keep
fades on whole-track time before extracting an audition window.

## Two generation engines, independent of location

Expose `engine = native | comfyui` independently from `local | dedicated RunPod |
serverless`. Both consume frozen YAML copies, emit the same FLAC/YAML/provenance
bundle and report into the existing DB. Models are discovered at job start.
Retain native mode and its working cache identities.

ComfyUI needs an API-format workflow, explicit field-to-node mappings, required
node/version inventory and exact checkpoint identities. The compiler now refuses
unmapped requested parameters, unsupported nodes/choices, out-of-range values and
bindings that would destroy graph connections. It preserves 64-bit seeds and the
original workflow. It only preflights/compiles; it cannot launch a generation yet.

Native BF16/FP32 and a quantised Comfy checkpoint may not represent the same model
or numerical path. Compare checkpoint hashes/revisions, precision/quantisation,
caption, lyrics, duration, sampler implementation and offload strategy before
attributing a quality/memory difference to the UI engine. Comfy's streamed loading
or quantised weights may help the 12 GB desktop; measure peak GPU **and system RAM**,
load time and generation time. Do not promise a fit from DiT size alone.

Dedicated pods can expose Comfy through the existing SSH connection/tunnel. Build
one portable worker contract first; then wrap it in a persistent serverless handler
with pinned custom nodes, durable storage and cost accounting. Native cache blobs
and Comfy cache blobs must be distinct unless compatibility is proven. Use a
read-only check with `run_gbr.py check-comfy --address http://127.0.0.1:8188`.

## Artwork diffusion and reference media

The brief builder reads `title`, `cluster`, `style`, `story` and actual lyric lines
from canonical parsed YAML. It extracts a bounded narrative excerpt, not a claimed
AI interpretation. Visual direction is editable. The title is the sole printed
text by default. Original YAML content and structure remain untouched.

Put examples under `_04_preparation/_references/_artwork/_global`, `_clusters` or
`_songs`. The UI lists references explicitly; selection freezes content hashes,
so changing a file cannot silently alter an earlier recipe. Same-content examples
share a registry identity. For model execution, inputs should be staged by those
hashes and retained with their provenance. References and stems are working assets,
not automatic public releases.

**Reference-editing trial candidate: Qwen Image 2.1**, because its official interface combines
creation and reference-image editing. Its 7B visual component is not the complete
runtime VRAM requirement. Its published Qwen Research License restricts model use to non-commercial research/evaluation without a separate commercial licence, so production use needs that choice resolved. **MOSS Image SFT** is worth an independent text-to-image
trial with Apache-2.0 licensing; its official material distinguishes the SFT release from the base/training
model. Do not assume equivalent reference-image editing. **FLUX** is another
candidate; select the exact release/runtime and its terms rather than a generic
family name. Qwen Image Edit 2511 is another reference-editing baseline to compare if Qwen 2.1 terms do not fit. These are options, not preinstalled models.

Record prompt/negative prompt, model+revision, seeds, size, steps, guidance,
quantisation, workflow hash, reference hashes and hardware/timings. Generate several
candidates, review/select one, then attach it to a release. Keep album-wide palette
or artwork reuse as an explicit mode rather than an accidental shared image.

## Proposed R2 v2 layout and migration

Prefer a private working bucket plus a public release bucket. A prefix inside a
public bucket is not a privacy boundary. Only selected public derivatives/recipes
are copied to the public bucket. SQLite, configuration, credentials, working stems
and reference collections remain private.

Store immutable file objects by content hash, e.g.
`gbr/v2/objects/sha256/ab/<sha256>`. Put filenames, MIME types, song/variant/generation
IDs and roles in release manifests. This avoids title/style renames and deduplicates
identical bytes. Friendly public manifests can keep existing catalogue/share IDs.
Public mastered FLAC is optional; web audio, cover and playback sidecars are separate
roles. Identical bytes need one object within each bucket, regardless of aliases.

Migration order: inventory local DB + remote keys/checksums → resolve missing or
ambiguous legacy identities against track/variant CSVs → build a reviewable mapping
→ upload missing immutable objects → verify bytes and media/CORS/range playback →
build a versioned release manifest → switch one current-manifest pointer → verify
old/new links → consider removing unreachable legacy objects. No `rclone sync` or
delete pass before this comparison. Keep old URLs temporarily or explicitly map
redirects through a serving layer; R2 renames do not create redirects.

`run_gbr.py plan-r2 --database PATH` now produces the local half of that plan,
including deduplication and missing/conflicting bytes. It never uploads/deletes.
The remote inventory/cutover remains pending. Back up the shared DB through SQLite's
backup API, never by copying an active WAL file; retain a small defined rollback
window plus a current pointer rather than endless active database copies.

## Tidy-up review

Done: reserve stage numbering; move website source to stage 5; update paths and
build checks; ignore preparation/media/model runtime files; cap preview cache; retain one verified
source bundle; remove the tracked machine-specific review config (including its
password hash) and obsolete bundle checksum/build-report/update-note files from the repository. A fresh
config is created by the existing app defaults. Existing local user configuration
is not a cleanup target; keep it if Git reports local modifications during update.

Retain reusable installers, migrations, diagnostics and tests. Historical authored
documentation is left intact; duplicate build instructions and archived logs can
be consolidated after you confirm which document remains canonical. No user audio,
source writing YAMLs, cloud media or databases are deleted by repository tidying.

## Decisions for Simon

| Decision | Recommended starting point | Alternative |
| --- | --- | --- |
| Stem mixing | Vocals + accompaniment, then four-stem faders when useful | Four stems from every selected recording |
| Finishing target | Preserve dynamics; opt-in loudness presets and measured true-peak ceiling | Fixed target for every genre |
| Artwork text | Title only; curated references; Qwen editing trial, licence choice before production | Text-free cover or album-wide shared artwork |
| Public masters | Web encode public; full FLAC optional per release | Public FLAC for all releases |
| Comfy baseline | Supply one API-format MiniMax workflow that already works for you, including node/checkpoint versions | Build a new reference workflow and validate against your current outputs |
| R2 cutover | New hash objects + manifest, old URLs retained through verification | In-place reorganisation with more rollback/link risk |

Next implementation priority: validate your working Comfy workflow and isolated
Demucs/WhisperX environment, then finish the stem/alignment review and release
approval screens. The built stereo mixing station can be tested immediately. Use `run_gbr.py doctor-processing` to query FFmpeg and installed preparation packages without loading models.

## Validation and migration notes

The local regression run passed 363 tests and 127 parameterized subtests across
generation, review, the shared database, YAML maintenance, preparation and the
public site. Chromium checks exercised real FFmpeg previews and source/processed
playback at 1440/390/320 px, artwork-brief restoration, master candidates, visual
features and preview eviction. Existing review-tag/fourth-rating/autoplay and
generation-to-review flows also passed, as did the relocated public analytics.
A clean public build retained 168 main and nine hidden recordings.

The only schema addition is `processing_tasks` and its indexes. Existing shared
databases install it idempotently on app startup; fresh initialization installs
the same SQL. No re-import or replacement database is required. The existing
verified directory migration relocates website source into stage 5. Close active
GBR controllers before pulling structural changes, then launch normally.

GPU model inference, Demucs/WhisperX accuracy, an actual Comfy generation, physical
Windows file locking and R2 migration/playback have not been validated by this
update. Local browser media was synthetic. No pods or bucket contents were changed.

## Primary references checked

- [Qwen Image 2.1 model card](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/README.md)
- [Qwen 2.1 model licence](https://huggingface.co/Qwen/Qwen-Image-2.1/blob/main/LICENSE)
- [MOSS Image SFT](https://huggingface.co/OpenMOSS-Team/MOSS-Image-SFT)
- [ComfyUI API routes](https://docs.comfy.org/development/comfyui-server/comms_routes)
- [Demucs maintenance status and separation](https://github.com/facebookresearch/demucs)
- [WhisperX alignment API and limitations](https://github.com/m-bain/whisperX)
- [FFmpeg processing filters](https://ffmpeg.org/ffmpeg-filters.html)
- [SQLite network/storage guidance](https://sqlite.org/useovernet.html)
- [Cloudflare R2 public buckets](https://developers.cloudflare.com/r2/buckets/public-buckets/)
