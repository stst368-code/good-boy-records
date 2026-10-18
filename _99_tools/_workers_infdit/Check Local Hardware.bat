@echo off
setlocal
cd /d "%~dp0"
".venv\Scripts\python.exe" worker.py doctor
if errorlevel 1 (
  echo Hardware check failed. Read the error above.
  pause
  exit /b 1
)
pause
