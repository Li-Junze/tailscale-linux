@echo off
chcp 65001 >nul
REM ============================================================
REM  ★ 双击我 —— Tailscale-Remote 部署包生成器
REM  勾选环境 -> 填密钥 -> 生成包, 对方解压后只跑 deploy 即可
REM ============================================================
cd /d "%~dp0"

REM 依次尝试本机可用的 python
set "PY="
where python >nul 2>&1 && set "PY=python"
if not defined PY ( where py >nul 2>&1 && set "PY=py" )
if not defined PY (
    echo [!] 没找到 Python。请先安装 Python 3.8+ 并勾选 "Add to PATH"。
    pause & exit /b 1
)

%PY% build_gui.py
if errorlevel 1 (
    echo.
    echo [!] 启动失败。若提示找不到 PyQt5, 请先安装:
    echo       %PY% -m pip install PyQt5
    echo     然后重新双击本文件。
    pause
)
