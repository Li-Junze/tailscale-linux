@echo off
REM clean-windows.bat —— Windows 被控端清洗入口
REM 自动以管理员身份运行 clean-windows.ps1 (绕过脚本执行策略).
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%CD%\clean-windows.ps1\"' -Verb RunAs"
