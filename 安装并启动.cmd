@echo off
setlocal
chcp 65001 >nul
title BiliDownload Setup
if not exist "%~dp0setup.ps1" goto missing
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1" %*
set "install_exit=%errorlevel%"
echo.
if "%install_exit%"=="0" (echo Setup finished.) else (echo Setup failed. See the error and log path above.)
pause
exit /b %install_exit%
:missing
echo Please extract the ENTIRE ZIP first, then run this file from the extracted folder.
echo Required setup files are missing. Do not run inside the ZIP viewer.
pause
exit /b 2
