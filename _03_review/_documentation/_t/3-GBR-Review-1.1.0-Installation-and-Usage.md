# GBR Review 1.1.0 — efficiency update

This update preserves the autoplay changes you supplied and supports your renamed `review` package folder. It includes the complete application source and static assets, updated launchers, documentation and tests. No dependency reinstall is required for your working 1.0.1 installation.

## Install into the existing application

1. Stop GBR Review with **Ctrl+C** and close its browser tabs.
2. Extract this ZIP. Place the extracted `GBR-Review-1.1.0-Efficiency-Update` folder directly inside your existing application folder, alongside `config.json`, `.venv` and `review`.
3. Double-click **Apply-Patch-Windows.bat** inside that extracted folder. It detects `review` or `gbr_review`, verifies payload hashes and refuses to run while the application is active.
4. Start **Start-Windows.bat** in the original application folder. Refresh the browser; use Ctrl+F5 if the old controls remain visible.
5. Open host **Settings → Retune zones** once. This adds Fast sample plans and the revised transition detector to existing analyses. The current and prepared next recording receive priority. You can continue reviewing while the remaining queue is retuned.

Before replacing code, the installer saves the earlier files and a consistent SQLite snapshot under `patch-backups/1.1.0-<timestamp>-<id>/`. Your `config.json`, reviews, archive and original media are not replaced. The installer restores earlier code if installation fails. An existing configuration receives new default settings when loaded.

If the update folder is elsewhere, pass the application folder explicitly:

```powershell
Apply-Patch-Windows.bat "E:\good-boy-records\_03_review\_generation_review"
```

On Linux/macOS:

```bash
/path/to/app/.venv/bin/python /path/to/update/apply_gbr_patch.py /path/to/app
```

Use the existing installation and database. Do not create a fresh installation for your current review queue.

## Faster daily review

Choose **Fast** for a ten-second screening overview, **Quick** for twenty seconds, **Standard** for fifty, or **Full**. Durations are configurable. Fast normally includes five two-second passages, prioritising the opening, ending and strongest available section changes. Use **Next point** to hear five seconds around a detected transition, or switch to a longer mode when uncertain. Short sampling can omit relevant material.

Enable **Autoplay** and start the first track yourself. Following a fourth rating or Skip, the next track starts in your remembered playback mode. Initial opening, Back and queue/filter changes stay paused. Custom ranges do not carry to the next recording. The browser may still ask for a tap on Play.

The next recording is prepared in the background. Preparing it does not advance, skip, rate or archive it. The fourth rating still saves your four individual human scores and triggers the existing archive operation. A temporary next-track connection failure retries loading without submitting that review again.

| Key | Action |
| --- | --- |
| `0`–`5` | Score the highlighted dimension; move to the next. Fourth score saves. |
| Up / Down | Choose a rating dimension. |
| Space | Play / pause. A focused button keeps its normal Space action. |
| `N` / `B` | Skip / Back. **N replaces the former S skip shortcut.** |
| `T` | Hear the next detected point. |
| `F` / `Q` / `S` / `U` | Fast / Quick / Standard / Full. |
| Left / Right / `R` | Seek five seconds / restart. |

Shortcuts pause while typing, saving, using a dialog or browsing Selection. No ratings are generated automatically.

## Tune the transitions

Start with **Balanced** sensitivity in host Settings. Choose **Sensitive** when quieter arrangement changes remain absent, or **Conservative** when too many rhythmic fluctuations become points. Saving sensitivity or duration changes automatically schedules cached retuning.

**Retune zones** reuses existing features and measured loudness/peaks. Original 1.0.1 caches are supported. **Reanalyse audio** is the slower option that decodes the originals again and adds the new finer spectral bands and pitch-class features. Missing preview assets also fall back to full analysis. Analysis and review history remain retained.

The new detector is tested against subtle synthetic changes, not calibrated against your actual songs. Gradual builds, short sections and purely melodic changes may still need a manual listen.

## Phone access without SSL certificates

1. Stop a running copy with Ctrl+C.
2. Double-click **Start-Mobile-Windows.bat** in the original application folder.
3. On a phone using the same network, open the printed `http://<computer-IP>:<port>` link and enter the printed access code. If you already configured a password, use it instead.

No certificate creation or installation is required. Host Settings also shows the phone URLs/code. If Windows asks, allow Python on the **Private** network. Keep the computer awake. Subsequent Start-Windows launches retain LAN access. On Linux/macOS use `.venv/bin/python run_review.py mobile --open`.

The previous configuration is backed up before mobile setup. The code is retained on the host and is not available through remote APIs. This is ordinary unencrypted HTTP for a trusted local network. Optional phone PWA installation/service workers may require HTTPS; opening the responsive browser UI, playing audio and saving ratings do not. Phone lock-screen and Bluetooth behavior still depend on the device/browser. Failed native FLAC playback automatically retries lossless WAV.

To return to local-only access, run Configure-Windows with the app stopped and choose local-only. An existing password can remain configured.

## Contents and validation

`payload/package/` contains the complete Python package and browser assets; `payload/root/` contains launchers, requirements, example configuration, documentation and tests. The installer maps these to the existing package name. No user database or audio is shipped. Fresh installations can be assembled by copying `payload/root/` to a new app folder and `payload/package/` to its `gbr_review/` folder, then using Install-Windows as described in the application README.

Validation: **61 backend tests**, desktop/mobile Chromium integration, Fast/autoplay, keyboard scoring, WAV fallback, next-request recovery and nonsecure HTTP browser login/playback passed. The nonsecure-origin check used a local forwarding harness; native Windows, real LAN reachability and physical phones were not tested here. See `REVIEW-AND-GAPS.md`, `BENCHMARKS.json` and the installed `docs/TESTING.md`.

SQL consumers that enumerate review modes must accept the new `fast` value. Existing score, provenance and analytics views remain available.
