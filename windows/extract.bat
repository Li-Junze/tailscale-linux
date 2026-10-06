@echo off
REM extract.bat -- 解压同目录下最新的 .tar.gz (Windows 侧辅助小工具)
REM 逻辑全 ASCII; 中文只出现在提示文字里。
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
set "PKG="
for /f "delims=" %%f in ('dir /b /o-d *.tar.gz 2^>nul') do (
  set "PKG=%%f"
  goto :found
)
:found
if not defined PKG (
  echo [X] 当前目录下没找到 .tar.gz 包。
  pause
  exit /b 1
)
echo 正在解压 %PKG% ...
tar -xzf "%PKG%"
if errorlevel 1 (
  echo.
  echo [X] 解压失败: Windows 需自带 tar 命令, Win10 1803 及以上版本都有。
  echo.
  pause
  exit /b 1
)
echo.
echo === 解压完成 ===
echo 下一步: 右键 deploy.bat 选 "以管理员身份运行"
pause
