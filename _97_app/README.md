# GBR platform launcher

The unified application provides Writing, YAML editor, Generation, Review, Selection,
Preparation and Publish tabs, connected to `_98_database/gbr.sqlite3`. The shared platform belongs here;
reusable worker and editing tools belong in `_99_tools`.

Generation has separate **Production queue** and **Experiments** views. Choose
Experiment under New batch for encoding trials and parameter sweeps: outputs go
to `_02_generation/_experiments/<batch-id>` and stay outside review and quality
stats. Listen/compare, export timings, move selected bundles into review, or delete
their output files while retaining measurements and worker caches. See the
[generation guide](../_02_generation/README.md#production-and-experiments).
**Publish comparison to website** uploads a matched experiment set through the
existing R2 settings. Its separate public catalogue supplies How it’s made and
shareable A/B comparison links, including when the local app is closed. Experiment
publication does not add tracks to the main player, review queue or quality stats.

The native generation controller is available separately for the
[first local PC test](../_02_generation/LOCAL_TEST.md). Successful FLACs carry
native run provenance and link back to generation telemetry when the existing
review app indexes them against the same database.

After the verified migration, run `Start-Windows.bat`. It reuses the existing
review virtual environment and `config.json`, including input/archive paths,
authentication, TLS, browser opening and logging. Start only after closing the
old review app. Database migration does not relocate audio, caches or archives;
check the existing review configuration against the actual folders first.

Equivalent command from the repository root:

```powershell
_03_review/_generation_review/.venv/Scripts/python.exe _97_app/run_gbr.py --open
```

`--config PATH` and `--database PATH` select alternatives. Startup requires an
existing database containing a completed migration report; it cannot silently
create an empty shared database. The review application's caches, secrets and
logs stay in its configured data directory. Startup locks both that data directory
and the resolved database path, so configurations cannot concurrently share a
database or overwrite each other's caches and archive work.

The review CLI also accepts an explicit database before its subcommand:

```powershell
_03_review/_generation_review/.venv/Scripts/python.exe _03_review/_generation_review/run_review.py --database _98_database/gbr.sqlite3 backup
```

Without an override or `database_path` configuration, the review CLI keeps using
its existing `data_directory/review.sqlite` location. Backups and exports use the
selected database. A configuration's optional `database_path` resolves relative
to that configuration file; command-line overrides resolve from the current
working directory.

## Historical generation repair

For automatic recovery followed by permanent removal of everything still
unrecoverable, close GBR and generation controllers, pull the current repo, and run:

```powershell
py -3.12 _97_app/run_gbr.py cleanup-generations auto --prune-unrecoverable
```

This rescans configured completed inputs, reconciles archived files using the
existing move journal, retries archive recovery, then builds and applies a fresh
cleanup plan. Exact database YAML snapshots are recovered; metadata-only history
does not block another usable YAML for the same PCM. Verified duplicate FLACs
are consolidated while keeping the recording and its review/generation IDs.
Remaining unrecoverable recordings, their reviews/metadata, and exclusively owned
files are permanently removed. No CSV edits or song-ID decisions are required.

Original Writing inputs, symlinks, staging files and files shared with surviving
recordings are protected. Active jobs and live published releases are retained.
Known terminal worker output copies can be removed by exact ledger identity;
worker/cache directories are never swept by filename patterns.
Every deletion is checksum/ownership checked, database relationships are checked
transactionally, and no filename-pattern or recursive-directory deletion is used.
A locked file leaves the printed journal resumable with `apply "PATH_TO_plan.json"`.
Permanent removal cannot be undone. Successful automatic runs remove their
one-time plan/CSV and retain an audit in the existing shared database, without
another database, trash copy or setup script.

For a read-only inspection instead, use `preview --prune-unrecoverable` and
apply the printed plan separately. Plain `preview` retains the manual repair
behaviour below and does not schedule permanent removal.

After pulling an update, start GBR and let its archiving work finish, then close
the app. Startup reconciles missing archive file-index records and verified
reviewed duplicates through the existing archive journal. It preserves reviews
and generation IDs; unrecoverable files remain flagged.

From the repository root, run `py -3.12 _97_app/run_gbr.py cleanup-generations preview`.
This reads the shared database and previews filename/schema changes. Missing
completed YAMLs can be recovered from exact, checksum-verified database bytes;
source Writing YAMLs and frozen source snapshots are never rewritten. Large
plans stream to disk and retain complete undo data. No additional database is created.

If recordings remain blocked, the printed plan directory contains `issues.csv`,
with track titles, audio/YAML paths and errors. Confirm an uncertain catalogue
identity by filling only its `song_id` cell, then run preview again with
`--identity-map "PATH_TO_issues.csv"`. Blank cells are ignored. A mapping is an
explicit correction, not a fuzzy match; malformed YAML and unavailable files
still require repair. Keep old input folders configured while they contain
recordings that have not been imported or archived.

With GBR closed, apply the saved plan using
`py -3.12 _97_app/run_gbr.py cleanup-generations apply "PATH_TO_plan.json"`.
`--skip-errors` explicitly excludes blocked recordings. `undo` with the same
plan restores files and database values, including removing only YAMLs created
by recovery. After accepting the result, `forget` removes that journal and its
unchanged issues report; an edited identity CSV is retained for reuse.

## Directory convention and existing devices

Repository directories use lowercase names starting with `_`, including source
packages and static assets. Use `_97_app/Start-Windows.bat` or
`python _97_app/run_gbr.py`. The generated inbox is `_02_generation/_generated`.
Machine-managed virtual environments, caches, worker deployment storage and
runtime identifier folders keep their required formats.

Finish generation and close the app before pulling this directory update.
Startup moves legacy local data into the canonical folders, reuses the existing
database/environment and updates storage references. It does not import history
again or rewrite frozen manifests/YAML. Conflicting files stop the move without
overwriting either file. Existing cloud worker storage stays in its original
location so old immutable releases remain collectable. No new setup script or
extra database is needed. Fresh devices use the same launcher and normal init-db
or historical import commands.

Startup checks distinguish the launcher's own Python/venv startup chain from
another running GBR app or controller. Other programs using the same virtual
environment do not block directory migration. Saved controller PIDs are checked
against their creation time so a recycled PID does not prevent startup. An
actual running app/controller still blocks moving its directories; nothing is
terminated automatically.

On Windows, the launcher starts from the repository root. If Windows holds a
directory open, a case-only change such as `_97_App` to `_97_app` is deferred;
both spellings already identify the same directory. Startup continues using
that directory, without a second copy or a temporary directory left behind.
Non-cosmetic moves and conflicting files retain the existing safety checks.
This happens before the generation interface loads; starting the app does not
automatically resume old pod jobs.

Preparation promotes reviewed distribution selections into `_04_preparation`,
with stereo/two-stem mastering trials, source-bound light/lyric timelines, artwork briefs and processing
history in the same database. FFmpeg is needed for audio renders;
`python _97_app/run_gbr.py doctor-processing` checks the host without loading models.
The [preparation, engine and storage plan](_plans/preparation-and-storage.md)
identifies the built features and pending GPU/release integrations.

**Preparation** contains reviewed publication approvals that have not yet been
completed or published. Lyric alignment queues automatically after the verified
promotion into Preparation, without opening its page. It waits for the processing
environment and a free local GPU; failures stay visible with a manual retry.
`setup-processing` installs that environment on a new device. The page also shows
the recording's original YAML, with its metadata, caption and lyrics available
without editing the source.

Preparation automatically reads edits to the recording's bound YAML file. Its
current path, current content and original imported YAML appear in YAML details.
Each accepted edit is a revision in the existing processing ledger; generation
provenance, identity and review history remain intact. Missing legacy IDs can be
assigned with **Master song → Variant → Save song / variant IDs**, using the
catalogues. Only those fields change in the Preparation copy, preserving its
format. ID-only changes retain lyric approval; changed lyrics or language require
fresh timing approval and queue alignment for the new text. Invalid or missing
YAML blocks publication with an error. If a package was already built, return it
from Publish to Preparation and finish it again to include subsequent edits.

**Preparation → Local processing queue** shows all active and waiting processing,
the running song, current stage, elapsed time, actual CPU/CUDA device and a live
worker log (including model downloads). Alignment runs one local task at a time;
queuing several tracks does not launch several model instances. Missing setup,
a live generation lock and a queue position have separate explanations. Historical
generation status rows alone cannot reserve the GPU. **Check / resume queue**
rechecks waiting tasks; stopped controllers are labelled interrupted and can be
retried. Run `py -3.12 _97_app/run_gbr.py setup-processing` if its environment is
missing. First use downloads the required models.

Split and roughly positioned words are labelled **Estimated positions only**
until real timing results arrive. Completed alignment reports the number of words
actually timed; unmatched words remain estimates. Zero matches is an explicit
alignment failure, not a successful result. Saved manual approval remains optional.

Choose **Finish preparation → Publish queue** after saving lyric/lighting edits
and choosing the source or master and artwork. This local step always makes an
MP3 of that exact audio and does not require R2. **Publish** lists only completed
preparations waiting to go live. Choose a room and **Publish recording** to move
the local bundle to
`_05_showcase/_song-<song_id>/_variant-<variant_id>/_recording-<full-audio-id>/`.
`_source` holds unchanged original audio/YAML and worker companions; `_derived`
holds processing outputs; `_release/<preview-id>` holds MP3, sanitised public
FLAC, cover and approved playback sidecars. Genre is metadata, not a directory
identity. Different recordings in the same genre cannot overwrite one another.
The existing shared `processing_tasks` ledger records and resumes verified moves;
no new database or migration script is required. Closed recordings leave the
Preparation queue. A successful publication enters **Library**.

Publication synchronises the approved public assets and conditionally updates the
R2 catalogue. Private source YAML, stems and operational data stay local. An R2
failure retains the completed bundle; **Publish → Retry publication**
resumes without re-encoding or another move. A locked source is reported as
cleanup pending; release the file and retry cleanup. Uncommitted failed moves can
be reset if both copies still verify. Existing legacy public URLs and share IDs
remain supported; re-releasing an indexed legacy recording through Preparation
uses the new layout. Existing Windows/R2 data is not renamed by pulling code.
Each pending recording shows its latest publication status, timestamp, progress
and error directly on its card; errors remain visible during automatic refresh.
Multiple Publish clicks for different recordings are queued. A recording waiting
for alignment or rendering releases the publication executor so other ready
recordings can publish, then resumes the same job when that processing finishes.
Closing GBR marks unfinished publications interrupted; retry them explicitly
after restarting to reconcile any remote writes.
**Library → Recordings** manages published titles, genre, language, explicit flags,
albums, covers, lyrics and room placement. Save a local draft or **Update website**;
the timing/light/audio editor reuses the Preparation tools. Revisions preserve
public IDs and share tokens, original YAML, reviews and history. Unchanged audio
assets are reused rather than copied or encoded again.

**Library → Rooms** creates multiple rooms, each with an optional unique main-player
cover entrance. Three clicks on that playing cover open its room. The original
Easter page remains separate. Room names/order and website lighting/defaults are
stored once in the shared database and published as `gbr/v2/library.json`.

For the old porto tree, use **Library → Import old showcase**. The Windows path
`C:\Users\Simon\OneDrive\porto\good-boy-records\showcase` is prefilled. The resumable
import recognises FLAC/MP3 pairs, MP3-only recordings and flat Easter folders with
shared sidecars. It keeps public IDs, tokens, covers and existing lyric/light data,
matches known tracks automatically and retains unmatched identities as legacy
Library records. Normalised YAML copies acquire the current explicit fields and
repair escaped newline markers; original YAML bytes are archived locally. The
import links existing reviews by audio identity without inventing new reviews.
Public audio strips private embedded tags without changing decoded audio; the
original container remains in the local bundle.
Verified files move into the canonical recording bundle, database paths update,
and only then are old copies removed. Uncertain or changed files stay in place
with a per-recording issue. MP3-only sources are labelled; no fake lossless version
is made. The optional R2 alignment reuses existing audio, creates missing MP3s and
updates room catalogues. Old bucket objects remain for link compatibility.
Resume the import after interruptions or locked-file cleanup; no one-time script,
additional database or manual SQL is required.

The public website consumes the R2 release catalogue directly; do not point the
legacy YAML catalogue builder at the private completed bundle.

Configure workers under **Settings → Worker settings** (also accessible from
Generation). Select a type and address, then **Query models & GPU** to retrieve
MiniMax encoder/DiT/VAE choices, samplers, queue state and available GPU memory.
Optional defaults fill untouched generation controls. Existing saved connections
migrate on the first settings save; measurements keep using the shared database.

Generation selects a saved worker or **All available workers**, which queries enabled
workers and runs a shared queue across compatible online endpoints. Each generation is
claimed once; faster workers can take more jobs. Busy endpoints join when their GBR
controller becomes available. Each worker uses its own defaults and available models.
The checked alternatives are frozen in the batch; settings changes cannot redirect it.

Controls follow Models, Text conditioning, Diffusion and Queue/output. Encoder/DiT and
sampler/scheduler rows keep each listed pair together. Numeric controls offer YAML/worker,
worker default, fixed value, list or range in one place. Explicit choices constrain
eligible workers; leaving models at their default uses each worker's available checkpoints.
Logs have UTC timestamps; Clear log view hides existing text without deleting history.

For local ComfyUI, use a loopback URL such as `http://127.0.0.1:8188` and optionally
set an absolute `.bat`, `.cmd`, `.ps1`, `.sh`, executable or Python start-file path.
Arguments are a JSON list; a Python entry point can specify its ComfyUI environment's
Python executable. Enable auto-start to launch only when needed and wait for the
API. **Start / query** tests it. Launch logs use one runtime folder per address;
GBR does not stop ComfyUI when the application closes. Pod ComfyUI uses its full HTTP
service URL, such as `https://POD_ID-8188.proxy.runpod.net`. Keep the exposed port in
the hostname. The client uses the legacy browser headers for API calls and FLAC
downloads, and discovers root or `/api` routes without retrying uncertain POSTs.

The recovered full ComfyUI workflow
uses Comfy graph caching; native persistent encoding-cache controls remain
native-only. The optional `_99_tools/_workers_comfyui/gbr_bridge.py` extension
provides checkpoint hashing and verified temporary-output cleanup through that URL.
Stock servers with missing extension routes (including HTTP 405) can generate;
cleanup remains pending until the bridge is installed and **Clean temporary files**
is retried. Required API failures identify the worker rather than the writing YAMLs.

`python _97_app/run_gbr.py setup-processing` installs the isolated Demucs/WhisperX
environment; `--cpu` selects CPU packages. Models download on the first alignment
or separation job, including automatically queued publication approvals.
Lighting/lyric annotations use the current working audio hash and never rewrite
source YAMLs. Artwork stays at brief preparation until the API workflow is supplied.

## Writing and workflow

Worker settings → **RunPod account & discovered pods** stores a single account key
in the OS keyring (Windows Credential Manager, macOS Keychain or a native Linux
secret store). The key is never written to config/database JSON, returned by API,
or sent to pod HTTP services. `RUNPOD_API_KEY` remains a supported alternative on
headless machines without an unlocked secret store. Existing session credentials
continue to work. Updating dependencies through the normal launcher installs keyring.

Automatic discovery defaults to 30 seconds once credentials are available; disable
it or adjust the interval in this panel. Refresh Pods, Test Account Connection and
per-pod Inspect run asynchronously. The expandable status panel reports account
errors, current pods, endpoint checks, timestamps, counts and the next discovery.
REST v2 cursor pagination includes cluster member pods; each running pod's detail
endpoint supplies live ports/utilisation that the list response omits. Rate limits
and account failures back off without changing healthy workers or running jobs.

Pods use stable `runpod|<pod-id>` identities in the existing saved worker list.
Discovery preserves defaults, enabled status and Auto Allocate preferences across
stops, restarts and address changes. Removing a discovered worker disables it so
rediscovery cannot silently reset its preferences. Unknown/incompatible pods remain
visible but cannot receive automatic work. Existing manual workers remain supported;
a matching manual endpoint is adopted into the same pod record with its preferences.
Generation's existing dispatcher admits compatible new pods to active automatic
batches. It never retries or transfers an unresolved in-progress attempt. Existing
task files and `worker_sessions.provider_instance_id` record pod ownership; no new
registry or database tables are introduced.

External RunPod requirements: the account key needs `pods:read`; the service must
already run and its HTTP port must be exposed. Advertised ports, configured candidate
ports and saved ComfyUI ports are probed. Proxy 403, 404 and 405 responses are service
errors, not evidence of a stopped pod. Direct TCP mappings can be used when public.
For native workers, expose SSH TCP port 22, register the matching public key with
RunPod once, and select the local private key (or use the SSH agent/default identity).
The existing GBR native worker must be prepared on the pod/template. Discovery is
read-only and never installs software, starts/stops paid pods, modifies access rules
or uploads SSH keys. A pod restart can change endpoints for future jobs; running
controllers retain their frozen endpoint and recovery/ownership rules.

Caption subsection reference choices show up to eight available recordings whose
latest overall review bucket is 4 or 5. They match the actual subsection text, including
mixed custom captions, rather than guessing from genre/model names. A missing caption
may use verified template ID plus unchanged template SHA evidence. Playback reuses the
existing review audio endpoint/cache; editing the copied text removes the examples
because they no longer describe that edited element. No matching recording is valid.
The lookup scans the 2000 highest/recently rated candidate generation rows and does not
generate audio or add reviews. No database migration or new audio copies are required.

The workflow tabs are Writing → YAML editor → Generation → Review → Selection → Preparation → Publish → Library.
The desktop workspace is fluid up to 1920 px; the preparation page uses that width too.
The public GBR site now lives in `_96_website`; startup migrates local website files from
`_04_website` and `_05_website`, including untracked media, without rewriting their bytes.

Writing opens on **Base**. Enter a song name and lyrics, with optional writing/author,
inspiration artist/song and album/position. Save lowercases these entries, removes blank
lyric lines and hyphenates the title. It creates a template-based YAML under `_built/_yaml`,
appends `track_list.csv` and registers the song and base variant in the existing shared
catalogue tables. IDs respect both CSV and database records. Choose an existing track or
variant under Start from to load its lyrics and metadata; saving creates the next variant
and appends `variant_list.csv`, preserving the original source and unrelated YAML fields.

Base and variant lyric saves also canonicalise bracketed sections to MiniMax's
`intro`, `verse`, `pre-chorus`, `chorus`, `post-chorus`, `bridge`, `instrumental`,
`solo` and `outro` tags. Descriptions and numbering are removed, e.g.
`[Intro: alalala]` → `[intro]`, `[Verse2]` → `[verse]`. Refrain/hook map to chorus;
gap/silence/pause/interlude/breakdown map to instrumental; named instrument solos
map to solo; middle eight maps to bridge and coda/fade-out to outro. Common chorus
misspellings and pre/post-chorus separator variants are accepted. Each tag gets its
own line without dropping sung words outside the brackets. Unknown or malformed
tags are reported before saving. This does not rewrite existing input files or
apply normalization during generation. The mappings live in `_gbr_writing/lyrics.py`.

**Captions** is independent of lyrics. Its three sections contain editable subsection
boxes, each with searchable reference examples. Loading an example copies only that
subsection. Cluster is required, version is optional; occupied cluster/version pairs
receive a numeric suffix. Custom captions use `_references/_custom_templates` and the
existing `styles.csv` index, extended with `version` and optional `song_id` columns when
needed. An unlinked caption can be used with any track; generation excludes captions
linked to other songs. Saves and retries use the existing writing ledger, with no new
database, one-time setup script or schema migration.

The **Model assistant** retains the existing draft workflow. It connects to a running llama.cpp server under Settings → Writing model settings
(or Worker settings → Writing model). Binaries remain in `_99_tools/_llama_cpp`; query the
server's loaded model before sending a brief. No model downloads occur on app startup.
New songs, lyric/caption rewrites, combined rewrites and translations produce editable
drafts. Lore and caption references are optional; an empty `_references/_lyrics` folder
is a valid fallback. Put tagged song examples there as UTF-8 TXT, Markdown or YAML files.

For model requests, only adding a reviewed draft to Built changes the active writing collection. New songs
receive the next song ID; keep-original rewrites receive a new variant ID. Archive-and-replace
moves the exact original YAML into `_01_writing/_archive/<draft-id>` and retains its IDs.
Catalogue publication is journalled and can finish after interruption. Unrequested YAML
fields, structure and line endings are preserved. Saved AI captions use `_llm_templates`
and `template_source=llm`; existing MiniMax samples and custom templates remain available.

An additive, idempotent `writing_tasks` table in the existing shared database stores drafts,
requests, reference hashes, revisions, model/token/timing data and publication state. It
reuses `worker_sessions`; unavailable GPU/VRAM measurements remain null. No legacy-data
re-import or second database is required. Text quality still needs evaluation with your model.

Generation has separate New batch and Queue views. The queue groups each batch once
under Active, Completed or Failed/cancelled. Expand Tracks & configurations for
individual runs, their selected controls, worker and live stage; caption and lyric
drawers load only when opened. Technical logs remain available in a separate drawer.
Queued configurations cancel immediately. An active native local run stops only its
own child process. New ComfyUI servers use the atomic ID-specific cancel API; old
servers can remove an owned queued prompt, or finish an active generation before
discarding its output. Older dedicated native releases also finish before discarding.
No global Comfy interrupt or queue clear is sent. Unknown remote state remains
reconnectable. Cancelled output cleanup retains checksum receipts and no review copy.
Status snapshots use unique temporary files and bounded retries for Windows sharing
locks. A temporary status-file lock does not fail inference; busy workers wait.

ComfyUI FLACs are converted during publication to 44.1 kHz stereo PCM_24. Bit-depth
promotion preserves existing samples; mono is duplicated without gain changes.
Other sample rates use FFmpeg. Original and final formats are recorded in the bundle
report, and original FLAC tags are preserved. A format/publication failure retains
the verified worker audio: Retry failed republishes that attempt without inference.

Review excludes generation runtime/staging audio even when a parent folder is scanned.
Use `_02_generation/_generated` as the input; earlier published runtime `outputs`
bundles remain readable. Current `style_primary`/`style` fields
drive the displayed genre and queue filters. Older scratch entries retain their IDs
and reviews, but display the completed recipe of the exact same decoded recording
when available. Existing `audio__...` archive names caused by such entries are repaired
on scan through the verified move journal; YAML bytes and review history stay intact.

Generation caption selection has prefilled cluster toggles, with an optional template
version selector per cluster. All enabled clusters form the random-caption pool;
All/Clear and individual selections persist with generation controls.

Queued batches expose Add worker and Automatically join new workers. All available
batches enable automatic joining by default. The app checks newly available saved,
enabled workers in the background while the batch has active controllers; it does
not require the Generation tab to stay open and does not resume stopped batches.
Manual assignment also works for paused/failed batches with untouched queued runs.
Running, completed, cancelled and previously attempted configurations are never
transferred or regenerated by admission. Worker-selected defaults can adapt to the
new endpoint; fixed controls, captions, experiment pairs and frozen seeds are retained.
Checked worker assignments are append-only files registered by checksum in the same
batch's existing database policy; source YAMLs and the base manifest are unchanged.
Before updating a batch managed by an older single-worker controller, Pause after
the current song and Continue from the updated app. That upgrades the controller
without rerunning completed songs; an active older controller is never disturbed.

Review Queue options and Publishing Selection use live track-name, cluster and style
buttons. Choices combine within a group and narrow across groups; an empty group
means Any. Queue choices persist per reviewer, and preview counts use the same exact
filters as playback. Song IDs and the master/variant catalogues group generation,
version, caption and filename suffixes under one song; legacy records fall back to
recognised run suffixes. Original titles, YAMLs, IDs and history are retained.
Lists are faceted: each choice has content matching the other active filters.
Review offers only unreviewed, available recordings, including its directory list;
completed songs/clusters disappear. Selection facets use reviewed/flagged/bookmarked
content and actual buckets/statuses; rerun facets use saved ratings. Exhausted active
selections remain clearable without widening a queue automatically. The open Review
chooser refreshes every five seconds. Old text selections resolve to the same songs.
Generation's rating filters, Writing's cluster/style/language and caption references,
and YAML editor metadata/field/template selectors use their existing catalogues.
Worker SSH keys use discovered file choices with an explicit Other key file option.
Text entry remains available for authored content, new field names/values and paths.
These controls add no tables or database migrations.
