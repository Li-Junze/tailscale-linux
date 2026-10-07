# clean.ps1 -- 本地卸载（把这台电脑上的远程连接工具全部撤掉）
# 用法: 右键 -> 使用 PowerShell 运行; 或管理员 PowerShell 里 .\clean.ps1
# 网页控制台也可以远程下发清除指令, 效果与本脚本一致。
param([switch]$KeepTailscale)

$ErrorActionPreference = 'Continue'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$InstallDir = $ScriptDir
$iniCfg = @{}
$ini = Join-Path $ScriptDir 'agent.ini'
if (Test-Path $ini) {
    foreach ($ln in @(Get-Content $ini)) {
        if ($ln -match '^\s*([A-Za-z_]+)\s*=\s*(.*)$') { $iniCfg[$Matches[1].ToLower()] = $Matches[2].Trim() }
    }
}
if ($iniCfg['installdir']) { $InstallDir = $iniCfg['installdir'] }

$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin) {
    Write-Host '[!] 需要管理员权限, 正在弹 UAC ...' -ForegroundColor Yellow
    Start-Process powershell -Verb RunAs -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-File', ('"' + $PSCommandPath + '"'), $(if($KeepTailscale){'-KeepTailscale'})) 
    exit 0
}

Write-Host ''
Write-Host '==== 开始卸载 Tailscale 远程工具箱 ====' -ForegroundColor Cyan

Write-Host '[1/5] 停止消息 agent 进程 ...'
Get-CimInstance Win32_Process -Filter "Name='powershell.exe' or Name='pwsh.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -like '*notify.ps1*' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Write-Host '      [OK]' -ForegroundColor Green

Write-Host '[2/5] 移除开机任务 TailscaleRemoteAgent ...'
try { Unregister-ScheduledTask -TaskName 'TailscaleRemoteAgent' -Confirm:$false -ErrorAction Stop
      Write-Host '      [OK] 已移除' -ForegroundColor Green }
catch { Write-Host '      [--] 本来就没有' -ForegroundColor Gray }

Write-Host '[3/5] 撤销控制端公钥免密 ...'
$ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
if (Test-Path $ak) {
    $pub = ''
    $pf = Join-Path $InstallDir 'keys\control.pub'
    if (Test-Path $pf) { $pub = ((Get-Content $pf -Raw) -as [string]).Trim() }
    if ($pub) {
        $keep = @(Get-Content $ak | Where-Object { $_.Trim() -ne $pub })
        Set-Content -Path $ak -Value $keep -Encoding UTF8
        Write-Host '      [OK] 公钥已撤销' -ForegroundColor Green
    } else { Write-Host '      [--] 没有记录公钥' -ForegroundColor Gray }
} else { Write-Host '      [--] 无授权文件' -ForegroundColor Gray }

Write-Host '[4/5] 退出 Tailscale 网络 ...'
if (-not $KeepTailscale) {
    $ts = 'C:\Program Files\Tailscale\tailscale.exe'
    if (Test-Path $ts) {
        & $ts logout 2>&1 | Out-Null
        Write-Host '      [OK] 已 logout (加 -KeepTailscale 可保留入网)' -ForegroundColor Green
    } else { Write-Host '      [--] 没装 Tailscale' -ForegroundColor Gray }
} else { Write-Host '      [--] -KeepTailscale, 保留' -ForegroundColor Gray }

Write-Host '[5/5] 删除安装目录 ...'
$targets = @($InstallDir, (Join-Path $env:USERPROFILE 'TailscaleRemote'))
foreach ($d in ($targets | Select-Object -Unique)) {
    if ($d -and (Test-Path $d)) {
        Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path $d) { Write-Host "      [!] 删不掉(可能被占用): $d" -ForegroundColor Yellow }
        else { Write-Host "      [OK] 已删: $d" -ForegroundColor Green }
    }
}
Write-Host ''
Write-Host '==== 卸载完成。重启一次可确保彻底干净。 ====' -ForegroundColor Cyan
Start-Sleep -Seconds 8
