@echo off
setlocal
set "GBR_PYTHON=%~dp0..\_03_Review\_Generation_Review\.venv\Scripts\python.exe"
if not exist "%GBR_PYTHON%" (
  echo Run the existing review tool's Install-Windows.bat first.
  pause
  exit /b 1
)
"%GBR_PYTHON%" "%~dp0run_gbr.py" --open
if errorlevel 1 (
  pause
  exit /b 1
)
