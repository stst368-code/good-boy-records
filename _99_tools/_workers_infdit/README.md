# Native local MiniMax worker

Install 64-bit Python 3.12 (3.11 is also supported), then double-click
`Install Local Worker.bat`. This creates this tool's own `.venv`, installs
PyTorch 2.10.0 with CUDA 12.8 and the pinned native Diffusers source, checks
dependencies and executes a small CUDA calculation. It does not install into
ComfyUI or download model weights. A working NVIDIA driver is required.

From the repository root on Windows, the first real test is:

```powershell
& "_99_tools/_workers_infdit/.venv/Scripts/python.exe" _02_generation/first_local_test.py
```

Or double-click `_02_generation/Run First Local Test.bat`. See
`_02_generation/LOCAL_TEST.md` for output locations and recovery.

Models are selected by jobs. A worker has no configured display name, model
list or assumed hardware. Each job discovers GPU identity, compute capability,
VRAM, host RAM, CPU, operating system, CUDA and installed package versions.
Its address is `local://hostname/GPU-UUID`; UUID discovery failures retain a
device address and null unavailable details. An OS lock prevents two GBR
workers in the same local user environment from using the same GPU together.
Other programs are not controlled by that lock.

The adapter uses stock MiniMax Music 3 modular blocks from Diffusers revision
`b800f828f3951c4d206e154411bcbfa693cd322e`, with Transformers 5.20.1. Models
come from `MiniMaxAI/MiniMax-Music3`, using only the required native component
subfolders. The job supplies a full model revision or resolves it at job start;
the controller records it for retries. The test pins revision
`fbdf52fbaaca799592917417eb05f1899f1255ec`. Legacy single-file ComfyUI
checkpoints and conditioning caches are not compatible with this adapter.

Supported controls are BF16/FP32 encoder and DiT precision, independent unsigned
64-bit encoder/sampler seeds, encoder CFG >= 0, top-k 1–16,384, diffusion CFG >= 0,
1–1,000 diffusion steps and a duration upper bound up to 360 seconds.

The pinned upstream semantic block fixes CFG at 1.5 and top-k at 50. The GBR v2
adapter binds its upstream autoregressive functions to a job-local namespace to
expose both controls, without mutating installed library globals. Cache identity
includes the adapter version and the effective encoder settings.

Euler with `flow_match_euler` remains the upstream default. Experimental native
flow solvers are Heun, midpoint, RK4 and DPM++ 2M, with `linear`, `karras`
(rho 7), `exponential` and `cosine` schedules. These use MiniMax time from noise
(0) to audio (1), preserve chunk overlaps, and apply CFG even below 1. Heun and
midpoint take extra transformer evaluations; RK4 takes four per step. Unsupported
ComfyUI sampler names are rejected before GPU work; they are not silently
mapped. CPU tests exercise the actual pinned blocks and known ODE solutions;
GPU audio comparisons are still required for the experimental solvers.

Lyrics following a leading section tag on the same line are refused because
upstream discards that inline text. No source repair occurs automatically.

`low_vram` runs conditioning, diffusion and FP32 vocoding separately, unloading
each model set before the next. The language model uses leaf CPU offloading,
without stream prefetch or pinned-memory duplication. FP32 DiT also offloads
its transformer; BF16 DiT stays resident for that stage. `resident_stage`
keeps the current stage's components on the GPU and needs more VRAM. These
are explicit job modes, recorded with the result. The local profile refuses
less than 8 GiB free VRAM and reserves at least 20 GiB available host RAM for
BF16 conditioning (38 GiB for FP32). These are admission estimates, not a
guarantee that every song fits; actual OOMs and memory peaks are recorded.

Encoding caching supports `prefer`, `require` and `disabled`. Cache identity
includes exact caption/lyrics, encoder seed/CFG/top-k/precision, duration,
model revision, adapter source revision and relevant installed library versions.
Files are checksummed safetensors, with tensor shape/dtype/finite-value checks.
`require` fails on a miss or corruption before inference; `prefer` quarantines
corrupt entries and regenerates. `disabled` never reads or writes this cache.
Cache hits retain their original run/attempt provenance; no conditioning
creation time or cost is charged to a consumer as new work.

The result records stage wall times including nested model loads, PyTorch
allocated/reserved memory peaks, and NVML device memory sampled every 100 ms
by default. GPU mapping uses UUIDs, so remapped CUDA ordinals cannot silently
measure the wrong card. Missing NVML coverage is explicit. Device samples
include other applications and can miss brief peaks; allocator peaks concern
this PyTorch process. Host RSS is also sampled. Parent timings include their
children: do not add total, conditioning and conditioning-load time together.
Local price, cost and unavailable historic/hardware data remain null.

Local subprocess, dedicated RunPod SSH and RunPod Serverless transports are
available through GBR's Generation tab. Dedicated setup reuses healthy pinned
dependencies. A missing exit record triggers process inspection under the launch
lock: live/uncertain attempts block environment changes; verified stopped
attempts gain a recovery record with an unknown exit code and retained evidence.
Connection checks also report unresolved attempts without installing packages.

After a complete bundle is verified and recorded locally, temporary raw FLACs
are deleted and remote transfers acknowledged. Cleanup can be retried separately
from inference, including for older worker releases. Failed metadata publication
retains one checksummed raw audio file and removes its incomplete staging copy.
Retry reuses those verified worker outputs without another inference attempt;
original GPU timing measurements are preserved.

Verified source references:

- [Native MiniMax Music 3 documentation](https://huggingface.co/docs/diffusers/main/en/modular_pipelines/minimax_music3)
- [Pinned native blocks](https://github.com/huggingface/diffusers/blob/b800f828f3951c4d206e154411bcbfa693cd322e/src/diffusers/modular_pipelines/minimax_music3/modular_blocks_minimax_music3.py)
- [Model revision](https://huggingface.co/MiniMaxAI/MiniMax-Music3/commit/fbdf52fbaaca799592917417eb05f1899f1255ec)
