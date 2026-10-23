# GBR Review

A standalone local application for listening to, rating and archiving generated FLAC/YAML pairs. Python runs on the computer holding the audio; the interface opens in a browser. No generation service, GBR website, cloud account or Node build is required.

## Windows installation

1. Install **64-bit Python 3.12** from [python.org](https://www.python.org/downloads/). Python 3.11–3.13 is supported. Enable the Python launcher or “Add Python to PATH”.
2. Extract the entire ZIP to a permanent folder, for example `C:\GBR\ReviewTool`. Keep the files together.
3. Double-click **Install-Windows.bat**. It creates an isolated `.venv`, installs the dependencies, and opens a setup prompt. Initial installation requires internet access to download Python packages.
4. Enter generation input folders, one per line. Press Enter on a blank line when finished. Choose a permanent reviewed archive folder, for example `D:\GBR\GBR_Reviewed`.
5. Accept local-only access for initial setup. For phone access, subsequently use **Start-Mobile-Windows.bat**; it generates an access code automatically when no password exists.
6. Double-click **Start-Windows.bat**. The browser opens at **http://127.0.0.1:8765**. Leave the console running. Stop the application with Ctrl+C.

**Completing the fourth rating moves original audio and its YAML into the chosen archive.** The review is committed to SQLite first, and destination copies are verified before source deletion. Skip leaves originals in place. The installer never moves or reviews your files.

Run `Configure-Windows.bat` with the server stopped to repeat setup. Folder and sampling settings are also available in the browser on the host computer. Once files have been archived, the browser prevents changing the archive root accidentally.

## Linux / macOS installation

With Python 3.11–3.13 installed:

```bash
cd gbr-review
bash install.sh
bash start.sh
```

The SoundFile wheels normally include libsndfile. If your platform uses a source build, install the platform’s libsndfile package. FFmpeg is not required: the application can serve lossless WAV as a browser playback fallback.

## Reviewing

If upgrading directly from 1.0.1, choose **Settings → Retune zones** once. Existing 1.1.0 plans can continue unchanged. It recalculates listening plans from cached features without decoding the originals again. New Fast plans become available as it completes. Changing sampling durations or transition sensitivity automatically schedules cached retuning. **Reanalyse audio** is the slower option that regenerates richer spectral/pitch features from the originals.

Tracks appear as scanning and analysis complete. The app automatically scans input folders every 30 seconds; Settings → Rescan starts a new scan without restarting.

1. Choose your name or tap an existing reviewer. The device remembers it; **Change user** switches identity. Every historical review is attributed to **Simon** on the first upgraded startup, after a database backup.
2. Choose **Fast**, **Quick**, **Standard**, or **Full**. Defaults are approximately 10 seconds, 20 seconds, 50 seconds, or the entire recording. Short recordings play once in their entirety.
3. Play, pause, restart, seek on the waveform, or replay a numbered snippet. Small waveform dots are neutral listening points, without a defect diagnosis or severity label.
4. Choose an integer **0–5** for **Audio Quality**, **Composition**, **Consistency**, and **Realism**.
5. The fourth choice saves automatically and advances. **Autoplay** starts the following track when enabled and remembers your preference. The chosen playback mode also carries forward; custom ranges do not. Initial opening, queue changes and Back remain paused. There is no submit button and no displayed average.
6. Use **Back** to change your own last review. The correction creates a new review revision and relocates the archive pair if the rounded bucket changes. **Return** leaves correction mode without saving another revision.
7. Use **Skip** to defer a recording without reviewing or moving it. Queue options → Return skipped tracks starts another pass through those recordings.

Realism means “would an average listener believe humans produced this?” It applies equally to electronic, synthetic and chiptune music. It does not require acoustic instruments or a literal human-sounding voice.

The four scores have equal weight. The exact mean is stored; the archive bucket is `floor(mean + 0.5)`. Each original score and every correction remain in the database. Automated analysis never changes the ratings.

## Player controls

| Control | Behaviour |
| --- | --- |
| Fast / Quick / Standard | Non-overlapping, audio-informed snippets rendered as one continuous listening file. |
| Full | Original FLAC, with native browser seeking and byte-range requests. |
| Waveform | Tap to seek. Tap a small dot to hear its passage with context. |
| Select range | Drag a passage, then choose Play selection. Shift-drag also works on desktop. |
| Spectrum | Show the cached spectrogram; tap to seek. |
| Next point | Play five seconds around the next detected section change; cycles through the points. |
| Playback compatibility | Automatic lossless WAV fallback when FLAC fails; desktop also offers a manual toggle. |
| Volume | Default 50%; mobile OS restrictions may require the device’s volume controls. Playback stays at the existing default 1×; the speed selector has been removed. |

Desktop shortcuts: **0–5** scores the highlighted dimension and moves to the next; the fourth score saves. **Up/Down** changes the highlighted dimension. **Space** play/pause, **Left/Right** seek five seconds, **R** restart, **N** skip, **B** Back, **T** next point, **F/Q/S/U** Fast/Quick/Standard/Full. Shortcuts are disabled while typing, during saving, when a dialog is open or in Selection. **S now selects Standard; N skips.**

Playback stays active when switching between Review and Selection or opening queue/settings dialogs. Loading a different recording stops the old one. A/B comparison uses the same persistent player. See [_docs/USAGE.md](_docs/USAGE.md) for details.

## Mobile / LAN

Stop a running copy with Ctrl+C and double-click **Start-Mobile-Windows.bat**. The console displays the actual `http://<computer-IP>:8765` URL and an access code. Open that URL on a phone on the same network and enter the code. If you already configured a password, use that password instead. No SSL certificates, device trust installation or command-line network configuration are required.

The launcher saves LAN access for future starts and backs up the earlier configuration. Phone URLs/code are also available in Settings on the host computer. If Windows prompts, allow Python on the **Private** network. The computer must remain awake and connected. On Linux/macOS, run `.venv/bin/python run_review.py mobile --open`.

All authenticated devices share the database. Different reviewer names have independent queues, filters, Back history and skips. Current audio plus two upcoming recordings are reserved exclusively for each active reviewer by default, with a 180-second inactivity timeout. Active clients renew every ten seconds. The same name on two devices represents the same reviewer and queue. Expired or stale ratings are rejected; use **Resume queue** to continue. The first pass stops once every recording has a review. Directory changes and detailed filesystem diagnostics are available only from the host computer.

Media Session integration supports play, pause, stop and seek from compatible headphones and lock-screen controls. External next/previous actions seek ten seconds; they do not submit ratings or archive tracks. Full tracks and generated snippet sessions use ordinary continuous audio playback, avoiding background JavaScript scheduling for snippets.

Background playback remains controlled by the browser and operating system. The responsive review interface, audio and scoring work over ordinary LAN HTTP. Only optional PWA installation/service workers require HTTPS or localhost. Optional TLS certificate/key configuration is described in [_docs/USAGE.md](_docs/USAGE.md). Use HTTP on a trusted local network; its access-code and session traffic are unencrypted. Optional HTTPS is still supported.

## Publishing selection

Open **Selection** for a separate pass through reviewed recordings. It defaults to archive bucket 5. Filter by song, publishing state or another reviewed bucket.

- **Listen** plays a candidate without rating controls.
- **Reassess** deliberately revisits one of your own reviewed recordings and appends a reassessment revision when a score changes.
- Choose **Selected**, **Not selected**, **Published**, **Passed**, or **Withdrawn**. A generation must first be selected before being marked published.
- The **Reference** action stores an optional external publishing reference.
- Check two different recordings of the same song and choose **Compare two versions**. A/B ordering is randomised; both versions are loudness matched with peak headroom. Relative preference is stored separately from numerical ratings.

“Published” is a status you set after a separate publishing system does its work. This app creates no artwork, processes no lyrics, uploads no media, and runs no publishing workflow.

The **Selection manifest** exports identifiers, file locations, YAML hashes, state dates and references. `archive_ready` tells an external system whether the archive files are actually present; a failed move does not erase selection intent.

## Files and data

| Location | Contents |
| --- | --- |
| `config.json` | Your folder/network/sampling settings and password hash; created by setup. |
| `data/review.sqlite` | WAL-mode database with generation provenance, individual scores, history, analysis, archive journal and publishing flags. |
| `data/cache/` | Waveforms, spectrograms and rendered snippet/WAV comparison files. |
| `data/backups/` | Consistent SQLite snapshots created by backup actions. |
| `data/application.log` | Server and background-worker errors. |
| Chosen archive / `0` … `5` | Reviewed originals and their associated YAMLs. |

Filenames look like `the-endless-game__a91f42b8.flac` / `.yaml`. Identifiers extend automatically when needed to avoid collisions. Identical decoded audio is archived once. Different YAML provenance is preserved as additional `__p<YAML-hash>` sidecars, all linked to their generation records. Original YAML bytes, including invalid YAML, are retained in SQLite.

Keep the application database on a local disk. Back up **both** the database and the archive. A database snapshot alone does not contain your audio.

## Analytics and exports

Settings provides CSV, Parquet, JSON and database-backup downloads. The database exposes a stable `analytics_dataset` SQL view. JSON fields preserve metadata beyond the common flattened parameters. No analytics dashboard is placed in the review interface.

Example Windows commands, from the extracted application folder:

```powershell
.venv\Scripts\python.exe run_review.py export --format parquet --output data\exports\reviews.parquet
.venv\Scripts\python.exe run_review.py export --kind events --format csv --output data\exports\events.csv
.venv\Scripts\python.exe run_review.py backup
.venv\Scripts\python.exe run_review.py validate
```

See [_docs/ANALYTICS.md](_docs/ANALYTICS.md) for read-only SQLite access, Polars examples, dataset definitions, API usage and song/genre-controlled queries.

## Recovery and troubleshooting

- If a move fails, the review remains saved. Correct the disk/path/permission problem, then choose **Retry archive moves** in Settings.
- **Reconcile archive** checks storage state and schedules incomplete work. Recovery also runs at startup.
- Corrupt or changing FLACs are logged and left untouched. Missing/invalid YAML does not prevent valid audio from being reviewed.
- Full playback can begin before analysis finishes. If analysis fails, see Settings → File and recovery issues; fix the file or dependencies and choose Reanalyse audio.
- If another app has the source open on Windows, deletion may fail after copying. Both copies remain safe, and retry completes the operation later.
- Stop the old server before running a second copy. An OS-level process lock prevents simultaneous archive workers for the same data directory.
- If a custom hostname/IP is rejected, add that exact hostname/IP to `allowed_hosts` in `config.json` and restart.

Detailed backup/restore and migration advice is in [_docs/USAGE.md](_docs/USAGE.md). Known limits and specification decisions are listed separately in [_docs/GAPS_AND_ISSUES.md](_docs/GAPS_AND_ISSUES.md).

## Included validation

The repository includes backend tests and Chromium integration scripts; current validation totals accompany the pull request. The following describes the review-specific checks. Testing covers byte-exact transfer between two Linux filesystems, move-journal recovery, provenance preservation, ratings, correction history, authentication, sampling and exports. Browser checks cover playback, touch-target dimensions, waveform/range/spectral controls, automatic submission, Back, publishing states and blind A/B preference. Additional checks cover Fast/autoplay, keyboard review, lossless fallback, save/next-request failure recovery, and nonsecure HTTP browser login/playback using a local request-forwarding harness.

Additional checks cover two independent browser identities, exclusive queues, user changes, isolated Back/Skip/filters, expired submissions, remembered identity and caption alignment at 390/320 px. Windows scripts and physical iOS/Android/Bluetooth behaviour still need validation on those devices. This package is a runnable Python application, not a precompiled Windows executable or an offline Python runtime bundle. See [_docs/TESTING.md](_docs/TESTING.md).

## Multi-reviewer update

Use the [platform launcher](../../_97_app/README.md) for current device setup and updates, and [_docs/MULTI_REVIEWER.md](_docs/MULTI_REVIEWER.md) for reviewer identity, reservation behavior, attribution fields and limitations. No second-pass assignment or aggregation is enabled.

## Rating captions in 1.2.1

The centred exceptional-rating reminder replaces the hotkey line. Captions now cover 0, 1–2, 3–4 and 5, with gold brackets under the two paired ranges. All six scoring buttons stay independent. See [_docs/UI_BRACKETS_UPDATE.md](_docs/UI_BRACKETS_UPDATE.md).

## Review context in 1.3.0

Track flags, personal bookmarks, optional Reason, and an Auto-submit switch are available in Review. Selection finds bookmarks and shows saved reasons. Settings has a host-side folder picker and optional protected cache cleanup. See [_docs/REVIEW_CONTEXT.md](_docs/REVIEW_CONTEXT.md) for the original interaction notes. Reviewed distribution selections now enter Stage 4 through the verified move journal; the [current preparation plan](../../_97_app/_plans/preparation-and-storage.md) describes this flow.
