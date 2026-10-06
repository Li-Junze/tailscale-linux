@echo off
REM ============================================================
REM  Tailscale-Remote deployment package generator
REM  Double-click to run.
REM
REM  NOTE: keep this file ASCII-only. Do NOT add non-ASCII characters
REM        or multi-line IF(...) blocks -- cmd.exe mis-parses them and
REM        the window closes instantly.
REM ============================================================
cd /d "%~dp0"

REM ---- bootstrap: locate a python that can import PyQt5 ----
set "BOOT="
python find_python.py > ".pypath.txt" 2>nul
if not errorlevel 1 set "BOOT=1"
if not defined BOOT goto TRYPY

:TRYPY
if defined BOOT goto HAVEBOOT
py find_python.py > ".pypath.txt" 2>nul
if not errorlevel 1 set "BOOT=1"
if not defined BOOT goto NOPY

:HAVEBOOT
set "PY="
for /f "usebackq delims=" %%p in (".pypath.txt") do set "PY=%%p"
del ".pypath.txt" >nul 2>&1
if not defined PY goto NOPY

echo Starting generator with: %PY%
"%PY%" build_gui.py
if errorlevel 1 goto FAILED
exit /b 0

:NOPY
echo.
echo [X] No Python with PyQt5 found.
echo.
echo     Fix it with either:
echo       A) install PyQt5 into your current python:
echo              python -m pip install PyQt5
echo       B) install Python 3.8+ and PyQt5, tick "Add python.exe to PATH"
echo.
echo     Then double-click this file again.
echo.
pause
exit /b 1

:FAILED
echo.
echo [X] Program exited with an error.
echo     If it says "No module named PyQt5", install it with:
echo         %PY% -m pip install PyQt5
echo     Then double-click this file again.
echo.
pause
exit /b 1
