@echo off
setlocal
chcp 65001 >nul
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0安装.ps1"
if errorlevel 1 (
  echo.
  echo 安装未完成。请检查网络后重试，或查看上方提示。
  pause
  exit /b 1
)
