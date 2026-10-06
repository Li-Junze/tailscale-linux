@echo off
REM 一键启动「Tailscale-Remote 离线包配置生成器」
cd /d "%~dp0"
python build_gui.py
if errorlevel 1 (
    echo.
    echo [!] 启动失败。若提示找不到 PyQt5，请先安装：
    echo     pip install PyQt5
    echo 然后用同样命令重新运行本脚本。
    pause
)
