# Validation and repeatable checks — version 1.3.0

Completed on Python 3.12/Linux: **97 backend tests**, **four Chromium integration scripts**, Python/JavaScript/shell syntax checks, and the in-place updater checks for both supported package names. No application JavaScript errors were observed. The supplied database copy preserves all original values across 21 tables, including 493 review revisions and 494 history entries, with integrity/foreign keys clean and migration reruns verified.

Browser checks use temporary synthetic media at desktop 1280 px and simulated phones 390/320 px. They cover flags, bookmark finding/reopening, reasons, automatic/manual submission, failure recovery, field reset, typing guards, directory editing, existing playback/selection and reviewer isolation. The nonsecure HTTP check uses a local forwarding harness. Physical Windows, phones/LAN, real media and the omitted cache remain unverified.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe -m playwright install chromium
.venv\Scripts\python.exe tests\ui_smoke.py
.venv\Scripts\python.exe tests\ui_efficiency.py
.venv\Scripts\python.exe tests\ui_reviewers.py
.venv\Scripts\python.exe tests\ui_review_context.py
```

On Linux/macOS use `.venv/bin/python`. Browser scripts use temporary ports 18765–18768; `--browser <path>` selects an existing Chromium executable. Optional `--screenshots <folder>` captures internal layout checks in supported scripts. Production needs neither development dependencies nor browser automation.

Tests never point at the user's real input/archive folders. Cross-filesystem checks use two writable devices when available; otherwise that test skips. A Starlette/httpx test-client deprecation notice may appear without affecting runtime.

See [REFRESH_REVIEW_AND_CHECKS.md](REFRESH_REVIEW_AND_CHECKS.md) for detailed findings, preservation evidence and physical acceptance checks still needed.
