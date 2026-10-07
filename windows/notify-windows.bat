@echo off
REM ============================================================
REM  notify-windows.bat -- 打开「右下角消息弹窗」（被控端）
REM  启动后本机右下角会弹窗显示网页发来的消息, 可直接打字回复;
REM  文件双向自动收发（收件箱 / 发件箱）。全程无窗口常驻托盘。
REM  ASCII-only on purpose (see deploy.bat).
REM ============================================================
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
set "PS1="
for /d %%D in ("%~dp0*") do if exist "%%~fD\windows\notify-windows.ps1" set "PS1=%%~fD\windows\notify-windows.ps1"
if not defined PS1 if exist "%~dp0windows\notify-windows.ps1" set "PS1=%~dp0windows\notify-windows.ps1"
if not defined PS1 (
  echo.
  echo [X] 包不完整: 找不到 程序\windows\notify-windows.ps1
  echo     请把整个文件夹一起解压后再运行。
  echo.
  pause
  exit /b 1
)
set "ROOM=%~1"
set "PS1DIR="
for %%F in ("%PS1%") do set "PS1DIR=%%~dpF"
if not defined ROOM if exist "%PS1DIR%keys\notify.local.txt" set /p ROOM=<"%PS1DIR%keys\notify.local.txt"
if not defined ROOM (
  echo.
  echo [!] 没有房间号。请用:  消息弹窗.bat 你的房间号
  echo.
  pause
  exit /b 1
)
set "PS1F=%PS1%"
set "ROOMF=%ROOM%"
set "RELAYF=https://ts-remote-web.pages.dev"
REM 隐藏启动: 外层 powershell 也是 Hidden, 所以只会有一瞬间的 cmd 闪过
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$env:PS1F,'-Room',$env:ROOMF,'-Relay',$env:RELAYF) -WindowStyle Hidden"
exit /b 0
