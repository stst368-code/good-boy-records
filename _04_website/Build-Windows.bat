@echo off
setlocal EnableExtensions
pushd "%~dp0.." || exit /b 1
py -3.12 _04_website\build.py %*
set "GBR_WEBSITE_EXIT=%ERRORLEVEL%"
popd
if not "%GBR_WEBSITE_EXIT%"=="0" pause
exit /b %GBR_WEBSITE_EXIT%
