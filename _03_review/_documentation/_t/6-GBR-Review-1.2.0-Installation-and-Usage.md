# GBR Review 1.2.0 — installation and usage

This installs the multi-reviewer and scoring-guidance specification into your existing application, including the renamed `review` package. Fast mode, autoplay, keyboard scoring, cached transition tuning, next-track preparation and mobile HTTP are preserved. Runtime requirements are unchanged; your working environment needs no dependency reinstall.

## Update the existing installation

1. Stop GBR Review with **Ctrl+C** and close its browser tabs.
2. Extract `GBR-Review-1.2.0-Multi-Reviewer-Update.zip`. Place its `GBR-Review-1.2.0-Multi-Reviewer-Update` folder inside `E:\good-boy-records\_03_review\_generation_review`, alongside `config.json`, `.venv` and `review`.
3. Double-click **Apply-Patch-Windows.bat** inside that update folder. It detects `review` or `gbr_review`, checks payload hashes and refuses an active application. It backs up replaced files and a consistent database snapshot under `patch-backups/1.2.0-<timestamp>-<id>/`.
4. Start **Start-Windows.bat** from the original application folder. On the first upgraded database startup, the app backs up the legacy database again under the configured data folder's `backups/before-reviewers-<id>.sqlite`, then transactionally attributes historical reviews and revisions to **Simon**. The console prints the backup path. Subsequent starts preserve later identities.
5. Open or refresh the browser. Use **Ctrl+F5** if old controls remain. Enter the existing shared access code/password if prompted, then choose **Simon** or another name. Each simultaneous reviewer should use their own name.

The installer leaves configuration, database and media in place. First startup migrates the schema; it preserves scores, YAML/audio links, archive state, publishing flags and history. Installation itself does not review or move originals. Startup archive recovery retains the application's existing behavior.

If the extracted update is elsewhere, run it with the application folder:

```powershell
Apply-Patch-Windows.bat "E:\good-boy-records\_03_review\_generation_review"
```

On Linux/macOS:

```bash
/path/to/app/.venv/bin/python /path/to/update/apply_gbr_patch.py /path/to/app
```

An installation failure restores earlier code automatically. After the first database migration, a deliberate full rollback also requires the matching earlier database and media state; see the installed `docs/MULTI_REVIEWER.md`. Keep backups rather than restoring an old database over new reviews.

## Daily use

- **Choose a name.** Type a name or tap an existing one. Whitespace is trimmed; case-insensitive matches reuse the identity. The device remembers it. **Change user** releases unused reservations for the old identity.
- **Use independent queues.** Order, filters, Skip and Back belong to the selected reviewer. The same name on two devices shares that person's queue. Default reservations are the current recording plus **two ahead**, expiring after **180 seconds** without renewal. Host Settings can change these values.
- **Listen and rate.** Fast / Quick / Standard / Full and autoplay work as before. The fourth 0–5 score saves automatically. Desktop number keys score the highlighted row; `F/Q/S/U` changes mode, `N` skips and `B` opens your previous review. Back and initial/filter loads remain paused. Corrections append attributed revisions.
- **Follow the guidance.** Audio Quality, Composition, Consistency and Realism show their question and 0/3/5 captions. Composition shows supplied cluster/genre, or **Genre not supplied**. Other generation settings stay hidden. Realism means believable human production, including electronic and chiptune music.
- **Resume after expiry.** Use **Resume queue** if your reservation expired or changed. Unsaved scores are cleared; a stale submission cannot overwrite another reviewer. Temporarily reserved work shows a waiting message. **First pass complete** stops assignment; no second pass is enabled.

Speed and zoom/pan controls are removed. Default playback stays at **1×**. Waveform, manual range selection, Spectrum viewing and seeking remain. Phone layouts may scroll vertically to keep captions readable and buttons wide.

If upgrading directly from 1.0.1, use **Settings → Retune zones** once to create Fast plans from existing caches. Existing 1.1.0 plans need no retune for this reviewer update.

## Phone access

Stop the app, run **Start-Mobile-Windows.bat**, open its printed `http://<computer-IP>:<port>` link on the same network, and enter the printed code or existing password. Then choose your reviewer name. No SSL certificate is required for the browser UI. Keep the host awake; allow Python on the Private network if Windows asks. Optional PWA installation depends on browser secure-context support.

## Data and delivery

Exports/analytics now contain reviewer ID/name and assessment identity. JSON dataset exports use **schema version 2**; custom queue/scoring API clients must select a name and send `X-GBR-Reviewer`. Archive scoring rules are unchanged. See `docs/ANALYTICS.md` and `docs/MULTI_REVIEWER.md`.

The ZIP contains complete application source/assets under `payload/package/`, and launchers, unchanged requirements, examples, documentation and tests under `payload/root/`. No user database or audio is shipped. For a fresh installation, copy `payload/root/` to a new folder and `payload/package/` to its `gbr_review/` folder, then use **Install-Windows.bat** as described in `README.md`.

Validation: **81 backend tests** and **three Chromium integration scripts**, including independent clients and 390/320 px layouts. The HTTP nonsecure-origin check uses a local forwarding harness. Native Windows launchers, actual LAN reachability and physical iOS/Android checks remain outstanding. See `REVIEW-AND-GAPS.md` and `docs/TESTING.md`.
