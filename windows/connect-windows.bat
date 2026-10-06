@echo off
REM connect-windows.bat -- Windows 被控端入口
REM 本体脚本是同目录的 connect-windows.ps1 (UTF-8 BOM, 中文显示正常)。
REM 本 bat 只负责"找到并启动它"; 提权由 ps1 自己弹 UAC, 避免在 bat 里拼三层引号。
REM 逻辑全 ASCII: cmd 按 OEM 码页(936)解码 bat, 中文字节只允许出现在提示文字里。
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
if not exist "%~dp0connect-windows.ps1" (
  echo.
  echo [X] 缺少 connect-windows.ps1 —— 它必须和本文件在同一个目录里。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0connect-windows.ps1" %*
