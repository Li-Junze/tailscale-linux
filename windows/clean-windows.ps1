# clean-windows.ps1 —— 清洗 Windows 被控端, 抹除所有连接痕迹 (v0.1-win-clean)
# 删除: sshd 服务/OpenSSH 功能/公钥 / Tailscale 服务与状态. 本机回到"从未装过"状态.
# 需管理员身份. 用法: 右键 clean-windows.bat -> 以管理员身份运行.

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
# 不写死版本号: 通配取 assets\ 下任意 tailscale-setup-*.msi
$MSI       = (Get-ChildItem (Join-Path $ScriptDir 'assets') -Filter 'tailscale-setup-*.msi' -ErrorAction SilentlyContinue |
              Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
$AdminKeys = 'C:\ProgramData\ssh\administrators_authorized_keys'

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '[X] 需要管理员权限. 请右键 clean-windows.bat -> 以管理员身份运行.' -ForegroundColor Red
    Read-Host '回车退出'; exit 1
}

Write-Host '==== Windows 被控端清洗 ====' -ForegroundColor Cyan

# 1. 停止服务
Write-Host '[1/4] 停止服务...'
Stop-Service sshd -Force -ErrorAction SilentlyContinue
Stop-Service tailscale -Force -ErrorAction SilentlyContinue

# 2. 卸载 Tailscale (用同一 MSI 最稳)
Write-Host '[2/4] 卸载 Tailscale...'
if (Test-Path $MSI) {
    Start-Process msiexec.exe -ArgumentList "/x `"$MSI`" /quiet /norestart" -Wait
    Write-Host '      已用预置 MSI 卸载.'
} else {
    $p = Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'Tailscale*' }
    if ($p) { Start-Process msiexec.exe -ArgumentList "/x $($p.PSChildName) /quiet /norestart" -Wait; Write-Host '      已卸载.' }
    else { Write-Host '      [!] 未找到 Tailscale 安装, 跳过.' -ForegroundColor Yellow }
}

# 3. 移除 OpenSSH Server 功能
Write-Host '[3/4] 移除 OpenSSH Server 功能...'
try { Remove-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null; Write-Host '      已移除.' }
catch { Write-Host '      [!] 移除失败或本就未安装 (可忽略).' -ForegroundColor Yellow }

                     # 4. 删除公钥与状态
                     Write-Host '[4/4] 删除公钥与 Tailscale 状态...'
                     Remove-Item $AdminKeys -Force -ErrorAction SilentlyContinue
                     Remove-Item 'C:\ProgramData\ssh' -Recurse -Force -ErrorAction SilentlyContinue
                     Remove-Item 'C:\ProgramData\Tailscale' -Recurse -Force -ErrorAction SilentlyContinue
                     # 清除本脚本代生成的私钥 (keys/id_ed25519[.pub]), 避免私钥残留在被控端
                     Remove-Item (Join-Path $ScriptDir 'keys\id_ed25519') -Force -ErrorAction SilentlyContinue
                     Remove-Item (Join-Path $ScriptDir 'keys\id_ed25519.pub') -Force -ErrorAction SilentlyContinue

Write-Host ''
Write-Host '本机连接痕迹已清除 (服务/功能/公钥/状态目录).' -ForegroundColor Green
Write-Host '⚠ 服务器侧设备仍会显示: 需联网后到 https://login.tailscale.com/admin/machines 删除该节点.' -ForegroundColor Yellow
Read-Host '回车退出'




