@echo off
REM ============================================================
REM  dragdrop-send.bat
REM  DRAG files/folders ONTO this .bat to send them to the peer.
REM
REM  How it works:
REM    1) The remote (controlled) machine runs:  python p2p.py serve --gui
REM       A window shows a 6-digit passcode and a 100.x.x.x IP.
REM    2) Drag the files/folders you want to send onto this .bat icon.
REM    3) Type the remote IP and the passcode shown on their screen.
REM
REM  You can also just double-click and type paths manually.
REM
REM  NOTE: keep this file ASCII-only. Non-ASCII characters get
REM        garbled in cmd.exe and may break the IF/SET parsing.
REM ============================================================
setlocal
cd /d "%~dp0"

set "DROPPED=%*"
if not "%DROPPED%"=="" goto hasfiles

echo ============================================================
echo   DRAG-DROP SEND
echo.
echo   Drag files/folders onto this file to send them.
echo   Or type paths now (separate multiple paths with spaces):
echo ============================================================
set /p "MANUAL=Path> "
if "%MANUAL%"=="" exit /b 0
set "DROPPED=%MANUAL%"

:hasfiles
echo.
echo Sending: %DROPPED%
echo.

set "HOST="
set /p "HOST=Remote Tailscale IP (100.x.x.x): "
if "%HOST%"=="" exit /b 0

set "AUTHARG="
set /p "PASS=6-digit passcode shown on their screen (blank if none): "
if not "%PASS%"=="" set "AUTHARG=--auth %PASS%"

set "PORTARG="
set /p "PORT=Port [8765]: "
if not "%PORT%"=="" set "PORTARG=-p %PORT%"

set "PY="
python --version >nul 2>&1 && set "PY=python"
if not defined PY py --version >nul 2>&1 && set "PY=py"
if not defined PY (
    echo [X] Python not found. Please install Python 3.8+ first.
    pause & exit /b 1
)

set "ARGS=send %HOST% %PORTARG% %AUTHARG%"
for %%F in (%DROPPED%) do set "ARGS=%ARGS% "%%~fF""

echo.
%PY% p2p.py %ARGS%
echo.
pause
endlocal
