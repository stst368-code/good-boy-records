@echo off
setlocal
cd /d "%~dp0"
py -3.12 -c "import sys" >nul 2>&1
if not errorlevel 1 (
  py -3.12 install_worker.py
) else (
  py -3.11 install_worker.py
)
if errorlevel 1 (
  echo Install or CUDA check failed. Read the error above.
  pause
  exit /b 1
)
pause
