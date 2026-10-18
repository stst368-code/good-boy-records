# GBR Review 1.3.0 — upgrade and usage

## Upgrade the existing installation

1. Stop GBR Review with **Ctrl+C** and close its browser tabs.
2. Extract `GBR-Review-1.3.0-Review-Context-Update.zip`. Place its update folder inside `E:\good-boy-records\_03_review\_generation_review`, alongside your existing `config.json`, `.venv` and `review` package.
3. Double-click **Apply-Patch-Windows.bat** in that update folder. It detects `review` or `gbr_review`, verifies the payload and backs up replaced code plus a consistent database snapshot. The installer refuses a running application.
4. Restart **Start-Windows.bat** in the original application folder. First startup creates another verified database snapshot before the transactional schema-3 migration. Existing scores, identities, timestamps, history, settings and flags remain in place.
5. Refresh with **Ctrl+F5**. Use your existing access code/password and reviewer name.

Runtime requirements are unchanged; the working environment needs no reinstall. No database, config, credentials, originals or cache files are bundled or replaced. Use the live database already in your application, rather than copying the uploaded database back over newer reviews. The supplied database is about 704 MiB; each automatic snapshot needs similar free space.

If the extracted update is elsewhere:

```powershell
Apply-Patch-Windows.bat "E:\good-boy-records\_03_review\_generation_review"
```

On Linux/macOS:

```bash
/path/to/app/.venv/bin/python /path/to/update/apply_gbr_patch.py /path/to/app
```

Code backups are under `patch-backups/1.3.0-<timestamp>-<id>/`. The runtime migration snapshot is under the configured data folder's `backups/before-review-features-<id>.sqlite`. Restore matching code/database/media state for a deliberate rollback; restoring an old database discards newer reviews and does not undo archive moves.

## Review controls

- **Track flags** contains independent checkboxes for **Flag for distribution** and **Bookmark**. Both can be selected. Flags save immediately; they do not submit a review, publish, upload or move audio. Distribution reuses the existing shared `selected` status. Bookmarks belong to the selected reviewer.
- **Reason** is optional multiline text, saved with the exact review revision and reviewer. Enter it before the last score when auto-submit is on. Back/Reassess loads that revision's reason. Selection → **Reviews / Reason** shows saved revisions, attribution and reasons. Skipping an unfinished review does not save its Reason.
- **Auto-submit** is **on by default** and remembered on the device. The fourth outstanding rating saves immediately and advances with existing autoplay. Turn it **off** to show **Submit review**; the button becomes enabled when all four values are supplied. This also allows text-only corrections without changing scores.
- If a save fails, scores, Reason and flag choices stay in the form. Use **Retry save**, or **Submit review** in manual mode. A committed request can be retried without creating another review. Expired/stale assignments still require the existing queue recovery flow.
- A different track loads its own flags and starts with an empty Reason; Back/Reassess loads that review's saved Reason. All rating definitions, captions, keyboard shortcuts, Fast modes and queues remain. Rating shortcuts are suppressed while typing in Reason.

## Bookmarks in Selection

Choose **Bookmarks → Bookmarked by me**. This clears the default bucket/status restriction so lower-scoring and not-yet-reviewed bookmarks are visible. Use **Listen**, **Reassess** for your own reviewed recordings, or **Review** for an unreviewed bookmark. Review obtains an exclusive reservation and does not steal another reviewer's track. Existing queue order/filters resume after that recording. Distribution changes in either tab share the same status.

## Directory settings

On the host computer open Settings using `http://127.0.0.1:8765` or its own LAN address. **Add folder…** opens a server-side folder picker; existing input paths can also be edited in the textbox. Save settings to rescan. Optional diagnostics no longer block the settings dialog.

An established reviewed archive stays locked to avoid breaking stored media paths; this does not block input-folder changes. Remote phones retain the existing restriction on host filesystem configuration. They receive the same review, Reason, flags and submission controls. Input changes do not delete previously indexed records.

## Playback cache

Existing WAVs were **not deleted or rewritten**. They are derived lossless playback files used for browser compatibility, samples/ranges and matched A/B audio. Full FLAC plays directly where supported.

Full-track cache keys no longer vary with analysis retuning or snippet fades. Identifiable old full-track files are reused unchanged. **Automatic cleanup is off.** Host Settings → **Playback cache** reports actual usage and offers a preview of optional safe cleanup. It protects active/queued audio, untracked legacy files, originals and files whose originals cannot be hash-verified. Eligible tracked derivatives regenerate when needed. Most legacy files remain protected until the application identifies them; this update does not promise immediate removal of the reported 13 GB.

The actual cache was not attached, so its individual files and total size could not be inspected. See `REVIEW-AND-CHECKS.md` for measured database findings, validation and physical checks still outstanding.
