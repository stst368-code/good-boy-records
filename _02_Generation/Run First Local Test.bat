@echo off
setlocal
cd /d "%~dp0"
"..\_99_Tools\_workers_infdit\.venv\Scripts\python.exe" first_local_test.py
if errorlevel 1 (
  echo Test failed. Keep the diagnostics folder shown above.
  pause
  exit /b 1
)
pause
