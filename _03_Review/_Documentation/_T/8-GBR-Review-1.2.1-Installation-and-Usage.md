# GBR Review 1.2.1 — rating UI update

## Install

1. Stop GBR Review with **Ctrl+C** and close its browser tabs.
2. Extract `GBR-Review-1.2.1-UI-Update.zip`. Place its `GBR-Review-1.2.1-UI-Update` folder inside your existing application folder, alongside `config.json`, `.venv` and the renamed `review` package.
3. Double-click **Apply-Patch-Windows.bat** inside the update folder. It detects `review` or `gbr_review`, checks payload hashes, backs up replaced code and snapshots the database before installation.
4. Restart **Start-Windows.bat** from the original application folder. Refresh the browser with **Ctrl+F5** to load the revised interface.

If the update folder is elsewhere, pass the original application folder:

```powershell
Apply-Patch-Windows.bat "E:\good-boy-records\_03_Review\_Generation_Review"
```

On Linux/macOS:

```bash
/path/to/app/.venv/bin/python /path/to/update/apply_gbr_patch.py /path/to/app
```

This is a complete application update based on 1.2.0. Runtime requirements are unchanged. Existing configuration, reviews and media stay in place. An already migrated 1.2.0 database needs no further schema migration. Earlier releases receive the documented, backed-up Simon migration on first upgraded startup; see `docs/MULTI_REVIEWER.md`.

## Updated rating guidance

The hotkey instruction line is replaced with the centred notice:

> A 5 rating is truly exceptional, reserve for tracks which literally could not be improved

Each row has gold brackets beneath **1–2** and **3–4**, with a centred caption beneath each bracket. All previous score captions are replaced:

| Row | 0 | 1–2 | 3–4 | 5 |
| --- | --- | --- | --- | --- |
| Audio Quality | Pure noise | Rough | Clear | Perfect |
| Composition | Unrelated | Spin-off | Clearly inspired | Exact |
| Consistency | China shop bull | Inconsistent | Consistent | Unified Song |
| Realism | Slop | Could fool mum | Realistic | Hyper-realistic |

The individual six 0–5 buttons still save automatically after the fourth rating. The grouped captions describe the scores without merging the buttons. Autoplay, keyboard shortcuts, reviewer identity/reservations and intended-genre guidance continue to work. Phone layouts wrap longer captions and scroll vertically.

## Validation and limits

Desktop and simulated-phone browser checks cover bracket/label alignment, 24 wide rating targets, automatic submission, playback, correction and simultaneous-reviewer workflows at 1280, 390 and 320 px. No application JavaScript errors were observed. Native Windows and physical phones still need device acceptance checks. Existing corpus/detector and external-importer limits are documented in `docs/GAPS_AND_ISSUES.md`.

The ZIP includes full Python source/static assets in `payload/package/`, launchers/docs/tests in `payload/root/`, and the in-place installer. No user database or media is bundled.
