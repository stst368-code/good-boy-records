@echo off
setlocal
set "GBR_PYTHON=%~dp0..\_03_review\_generation_review\.venv\Scripts\python.exe"
if not exist "%GBR_PYTHON%" (
  py -3.12 "%~dp0run_gbr.py" --open
) else (
  "%GBR_PYTHON%" "%~dp0run_gbr.py" --open
)
if errorlevel 1 (
  pause
  exit /b 1
)

