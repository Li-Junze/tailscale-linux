@echo off
REM connect-windows.bat —— Windows 被控端部署入口
REM 自动以管理员身份运行 connect-windows.ps1 (绕过脚本执行策略).
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -ArgumentList '-ExecutionPolicy Bypass -File \"%CD%\connect-windows.ps1\"' -Verb RunAs"
