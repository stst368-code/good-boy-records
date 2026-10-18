@echo off
setlocal
pushd "%~dp0.."
if errorlevel 1 (
  echo Cannot open the GBR repository folder.
  pause
  exit /b 1
)
set "GBR_ROOT=%CD%"
set "GBR_PYTHON=%GBR_ROOT%\_03_review\_generation_review\.venv\Scripts\python.exe"
if not exist "%GBR_PYTHON%" (
  py -3.12 "%GBR_ROOT%\_97_app\run_gbr.py" --open
) else (
  "%GBR_PYTHON%" "%GBR_ROOT%\_97_app\run_gbr.py" --open
)
set "GBR_EXIT=%ERRORLEVEL%"
popd
if not "%GBR_EXIT%"=="0" pause
exit /b %GBR_EXIT%

