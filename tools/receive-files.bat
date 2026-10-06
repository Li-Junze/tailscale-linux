@echo off
REM ============================================================
REM  receive-files.bat
REM  Double-click on the CONTROLLED machine to start receiving files.
REM
REM  It will print a 6-digit passcode and this machine's Tailscale IP.
REM  Keep this window open. The peer then drags files onto
REM  dragdrop-send.bat (or runs p2p.py send) to deliver them here.
REM
REM  Files land in:  <this folder>\inbox
REM
REM  NOTE: keep this file ASCII-only (cmd.exe garbles non-ASCII).
REM ============================================================
cd /d "%~dp0"

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY py --version >nul 2>&1 && set "PY=py"
if not defined PY (
    echo [X] Python not found. Please install Python 3.8+ first.
    pause & exit /b 1
)

echo Starting receiver ...
echo.
%PY% p2p.py serve --dir "inbox" --gui
echo.
echo Receiver stopped.
pause
