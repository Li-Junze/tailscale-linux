@echo off
REM clean-windows.bat -- Windows 被控端清洗入口
REM 本体脚本是同目录的 clean-windows.ps1 (UTF-8 BOM)。
REM 提权由 ps1 自己弹 UAC。逻辑全 ASCII, 中文只出现在提示文字里。
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
if not exist "%~dp0clean-windows.ps1" (
  echo.
  echo [X] 缺少 clean-windows.ps1 —— 它必须和本文件在同一个目录里。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0clean-windows.ps1" %*
