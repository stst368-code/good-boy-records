@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 (
  set "GBR_PYTHON=python"
) else (
  set "GBR_PYTHON=py -3"
)
%GBR_PYTHON% -c "import sys; assert (3,11) <= sys.version_info[:2] <= (3,13), 'Install Python 3.11, 3.12 or 3.13 from python.org'"
if errorlevel 1 goto failed
%GBR_PYTHON% -m venv .venv
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto failed
.venv\Scripts\python.exe -m pip install -r requirements.txt
if errorlevel 1 goto failed
.venv\Scripts\python.exe run_review.py setup
if errorlevel 1 goto failed
echo Installed. Run Start-Windows.bat to open GBR Review.
pause
exit /b 0
:failed
echo Installation failed. The error above identifies the step to fix.
pause
exit /b 1
