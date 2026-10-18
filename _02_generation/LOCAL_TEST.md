# First test on the local PC

Use the `codex/shared-platform-database-foundation` branch from PR #1.
The scripts must run from the complete repository checkout, preserving the
`_02_generation`, `_03_review`, `_98_database` and `_99_tools` directories.

1. Install 64-bit Python 3.12, if needed. Close other GPU workloads and free
   system RAM. The default test uses BF16 with CPU offloading; it discovers
   the hardware instead of assuming a particular GPU or RAM size.
2. Double-click `_99_tools/_workers_infdit/Install Local Worker.bat` and let
   its CUDA check finish. `Check Local Hardware.bat` repeats that check later.
3. Double-click `_02_generation/Run First Local Test.bat`.

The first run downloads the native components it needs from Hugging Face.
Allow tens of GB of free disk space for weights, installation and working
files. A cold download/load can take considerably longer than generation.
The installer records `runtime/installed-packages.txt` and
`runtime/hardware-check.json` under the worker tool. Hugging Face uses its
standard model cache; set `HF_HOME` before launching if another drive is needed.

The test uses `examples/local_smoke`, not `_01_writing/_built`. Its short,
original test lyric, caption, fixed seeds, 30 diffusion steps and 30-second
upper bound are reviewable before execution. Upstream may stop audio earlier.
It creates an isolated database at
`_02_generation/_runtime/local_test/local-test.sqlite3`. This database uses the
shared platform schema but does not replace the production or historical DB.

The terminal prints the frozen manifest and the final output directory:

```text
_02_generation/_runtime/local_test/batches/BATCH_ID/
    manifest.json
    inputs/                 exact imported source bytes
    requests/               frozen effective request YAML
    attempts/ATTEMPT_ID/     request, events, result and stderr log
    outputs/RUN_ID/          complete output bundle
```

A successful bundle contains `gbr-local-smoke-choral-NNNN.flac`, the matching
completed `.yaml`, an exact `source.yaml` copy and `generation.json` with hashes,
worker details and measured results. Audio is 44.1 kHz stereo PCM_24 FLAC.
The completed sidecar adds only needed generation changes and the quoted
`generation_datetime` in Europe/London `yymmddhhmm` form. Database/report times
retain full UTC timestamps. Comments, unknown fields, order, lyrics and
newlines remain intact. Source files are never modified, moved or deleted.

Failed jobs retain diagnostics and available performance data. They do not
publish a completed bundle. To retry the latest failed test with its frozen
seeds and resolved model revision, run from the repository root:

```powershell
& "_99_tools/_workers_infdit/.venv/Scripts/python.exe" _02_generation/first_local_test.py --retry
```

Running the normal test launcher again creates a new test run; it may reuse a
compatible conditioning cache. A completed run in a frozen batch is skipped.
The default executor stops at the first failure and runs at most one job per
invocation. No infinite automatic retries or paid worker dispatch occur.

After a terminal/process crash, stop the old controller and worker before
recovering a run marked `running`:

```powershell
& "_99_tools/_workers_infdit/.venv/Scripts/python.exe" _02_generation/run_generation.py recover "PATH/manifest.json" --run-id RUN_ID
& "_99_tools/_workers_infdit/.venv/Scripts/python.exe" _02_generation/run_generation.py execute "PATH/manifest.json" --retry-failed
```

Recovery imports complete durable telemetry events, marks interrupted attempts,
and reconciles a fully published checksummed bundle without generating again.
It refuses altered bundles. Recovery cannot determine remotely whether an old
process is still active: stopping that process is an explicit operator step.

For the first hardware result, keep the attempt directory and
`generation.json` (if successful), especially `stderr.log`, `events.ndjson`
and `result.json`. These show download/load time, inference stages, cache
status, actual model revision, CUDA/hardware, memory coverage and any OOM.
They will establish whether this mode fits the PC and how long a real run takes.

The existing review app can scan the successful batch's `outputs` directory.
Use this test database as its explicit `--database` when testing the automatic
run-to-review link. The link is accepted only when both run ID and final FLAC
hash match. Do not point review scanning at `attempts`, which contains staging
audio. Production migration/cutover remains separate from this hardware test.

Validation in development covers protocol/DB execution using a deterministic
test worker, real FLAC encoding/decoding and review indexing, source-byte
preservation, failure/retry handling and recovery. It does not certify a full
MiniMax generation or VRAM fit on the user's GPU; the launch above is that test.
