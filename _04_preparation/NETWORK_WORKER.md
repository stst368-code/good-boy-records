# Use another PC for lyric alignment

The main GBR app keeps the queue, songs, versions and results. A preparation
worker on another PC downloads one recording, separates vocals, aligns the
lyrics, and returns the timings and separated stems. It does not need your
database or a shared drive. Generation can keep running on the main PC.

## On the main PC

1. Pull `main` and restart GBR.
2. Open **Preparation → Processing → Pair another PC**.
3. Copy the pairing key. Use the main PC's LAN URL printed at startup, such as
   `http://192.168.1.10:8765`. `localhost` refers to whichever PC runs the worker.
4. In **Generation → Workers**, disable this second PC's diffusion worker if it
   cannot generate songs. Its preparation worker runs independently.

## On the preparation PC

Pull `main` in its GBR checkout. Install the isolated processing environment
once; FFmpeg must be available on PATH:

```powershell
py -3.12 _97_app/run_gbr.py setup-processing --cuda cu126
```

`cu126` supports the GTX 1060/Pascal generation. PyTorch 2.8's `cu128` wheels
omit Pascal kernels. This setup is separate from ComfyUI and the diffusion
environment. See [PyTorch's compatibility notice](https://dev-discuss.pytorch.org/t/cuda-toolkit-version-and-architecture-support-update-maxwell-and-pascal-architecture-support-removed-in-cuda-12-8-and-12-9-builds/3128).

Start the worker using the **main PC's** address:

```powershell
py -3.12 _97_app/run_gbr.py processing-worker --server http://192.168.1.10:8765 --comfy-url http://127.0.0.1:8189
```

Paste the pairing key at the hidden prompt. On Windows the key is remembered
using your Windows account's encryption. To replace a rejected key, pass
`--token` or set `GBR_PROCESSING_TOKEN` for that launch.

The optional `--comfy-url` points to ComfyUI on this preparation PC. Pickup
pauses while ComfyUI runs, the GPU is busy, or RAM is almost full. When the GPU
is idle but its memory is occupied, automatic mode can use CPU. Only connected
workers appear in GBR. Leave this terminal running while the PC is available;
Ctrl+C stops it. Model downloads happen on the first alignment.

Automatic device selection uses supported quantized computation on older or
smaller GPUs, and falls back to CPU when the installed CUDA build cannot run.
Use `--device cpu` to choose CPU explicitly, or `--device cuda` to require GPU.
CPU processing takes longer; this is background work, not real-time playback.

## Waiting and interrupted tasks

The timeline now shows the waiting reason. **Run here on CPU** bypasses a busy
main GPU if the local processing environment is installed. **Processing** shows
the worker, stage, elapsed time and log, with retry for failed/interrupted work.
An offline worker's lease expires visibly; a stale result cannot overwrite a
retried task. A missing source recording fails that row instead of blocking
the following recordings.

Audio and results are checked against the claimed recording. Pairing authorizes
only preparation-worker endpoints; it does not sign the worker into the rest
of the app. **Disconnect workers** revokes their pairing key.
