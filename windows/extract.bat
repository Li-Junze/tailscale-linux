@echo off
REM extract.bat —— Windows 解压助手 (一键解包 .tar.gz)
REM 自动选取当前目录最新的 .tar.gz 并解压.
cd /d "%~dp0"
set "PKG="
for /f "delims=" %%f in ('dir /b /o-d *.tar.gz 2^>nul') do ( set "PKG=%%f" & goto :found )
:found
if not defined PKG ( echo [X] 未找到 .tar.gz 包 & pause & exit /b 1 )
echo 解压 %PKG% ...
tar -xzf "%PKG%"
echo.
echo === 解压完成 ===
echo 下一步（只需这一个）: 右键 deploy.bat 选择"以管理员身份运行"
pause
