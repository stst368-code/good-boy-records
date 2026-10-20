# GBR platform launcher

The unified application provides Review, Selection, Generation, Preparation and YAML editor
tabs, connected to `_98_database/gbr.sqlite3`. The shared platform belongs here;
reusable worker and editing tools belong in `_99_tools`.

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
environment; `--cpu` selects CPU packages. Models download on an explicit job.
Lighting/lyric annotations use the current working audio hash and never rewrite
source YAMLs. Artwork stays at brief preparation until the API workflow is supplied.

## Writing and workflow

The workflow tabs are Writing → YAML editor → Generation → Review → Selection → Preparation.
The desktop workspace is fluid up to 1920 px; the preparation page uses that width too.
The public GBR site now lives in `_96_website`; startup migrates local website files from
`_04_website` and `_05_website`, including untracked media, without rewriting their bytes.

Writing connects to a running llama.cpp server under Settings → Writing model settings
(or Worker settings → Writing model). Binaries remain in `_99_tools/_llama_cpp`; query the
server's loaded model before sending a brief. No model downloads occur on app startup.
New songs, lyric/caption rewrites, combined rewrites and translations produce editable
drafts. Lore and caption references are optional; an empty `_references/_lyrics` folder
is a valid fallback. Put tagged song examples there as UTF-8 TXT, Markdown or YAML files.

Only adding a reviewed draft to Built changes the active writing collection. New songs
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
