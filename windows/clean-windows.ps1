# clean-windows.ps1 —— 清洗 Windows 被控端, 抹除所有连接痕迹
# 删除: sshd 服务/OpenSSH 功能/公钥 / Tailscale 服务与状态. 本机回到"从未装过"状态.
#
# 用法(二选一):
#   ① 双击/右键 程序\windows\clean-windows.bat  -> 自动弹 UAC 提权
#   ② 直接: powershell -ExecutionPolicy Bypass -File clean-windows.ps1
# 参数:  -DryRun   只列清单不真删（先看看会删什么, 不需要管理员）

param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
# 不写死版本号: 通配取 assets\ 下任意 tailscale-setup-*.msi
# ★ 先给空串再覆盖: 轻量包没有 MSI; 对 $null / '' 直接调 Test-Path 会抛异常并中断。
$MSI = ''
$hit = Get-ChildItem (Join-Path $ScriptDir 'assets') -Filter 'tailscale-setup-*.msi' -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($hit) { $MSI = $hit.FullName }
$HasMSI = [bool]($MSI -and (Test-Path $MSI))
$AdminKeys = 'C:\ProgramData\ssh\administrators_authorized_keys'

# ---- 0. 权限: 非管理员则自动 UAC 重启自己 ----
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $IsAdmin -and -not $DryRun) {
    Write-Host ''
    Write-Host '[!] 需要管理员权限, 正在弹出 UAC 提权窗口 ...' -ForegroundColor Yellow
    Write-Host '    请在弹窗里点【是】; 之后真正干活的是新弹出的那个窗口。' -ForegroundColor Gray
    $argList = @('-NoProfile', '-ExecutionPolicy', 'Bypass',
                 '-File', ('"' + $PSCommandPath + '"'))
    try {
        Start-Process powershell -Verb RunAs -ArgumentList $argList
    } catch {
        Write-Host '[X] 提权被拒绝或失败。请右键 程序\windows\clean-windows.bat -> 以管理员身份运行。' -ForegroundColor Red
        Read-Host '按 Enter 退出'
    }
    exit 0
}

function Do-Clean {
    Write-Host '==== Windows 被控端清洗 ====' -ForegroundColor Cyan
    if ($DryRun) { Write-Host '[i] -DryRun 模式: 只列清单, 不会真的删除任何东西。' -ForegroundColor Cyan }

    # 1. 停止服务
    Write-Host '[1/4] 停止服务...'
    if ($DryRun) {
        foreach ($svc in 'sshd', 'tailscale') {
            Write-Host "      [DryRun] 会停止服务: $svc"
        }
    } else {
        Stop-Service sshd -Force -ErrorAction SilentlyContinue
        Stop-Service tailscale -Force -ErrorAction SilentlyContinue
    }

    # 2. 卸载 Tailscale (用同一 MSI 最稳)
    Write-Host '[2/4] 卸载 Tailscale...'
    if ($DryRun) {
        if ($HasMSI) { Write-Host "      [DryRun] 会用包内 MSI 卸载: $MSI" }
        else { Write-Host '      [DryRun] 包内无 MSI, 会按注册表里的 Tailscale 条目卸载' }
    } elseif ($HasMSI) {
        Start-Process msiexec.exe -ArgumentList "/x `"$MSI`" /quiet /norestart" -Wait
        Write-Host '      已用预置 MSI 卸载.'
    } else {
        $p = Get-ItemProperty 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*','HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -like 'Tailscale*' }
        if ($p) { Start-Process msiexec.exe -ArgumentList "/x $($p.PSChildName) /quiet /norestart" -Wait; Write-Host '      已卸载.' }
        else { Write-Host '      [!] 未找到 Tailscale 安装, 跳过.' -ForegroundColor Yellow }
    }

    # 3. 移除 OpenSSH Server 功能
    Write-Host '[3/4] 移除 OpenSSH Server 功能...'
    if ($DryRun) {
        Write-Host '      [DryRun] 会移除功能: OpenSSH.Server'
    } else {
        try { Remove-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null; Write-Host '      已移除.' }
        catch { Write-Host '      [!] 移除失败或本就未安装 (可忽略).' -ForegroundColor Yellow }
    }

    # 4. 删除公钥与状态
    Write-Host '[4/4] 删除公钥与 Tailscale 状态...'
    $targets = @(
        $AdminKeys,
        'C:\ProgramData\ssh',
        'C:\ProgramData\Tailscale',
        (Join-Path $ScriptDir 'keys\id_ed25519'),
        (Join-Path $ScriptDir 'keys\id_ed25519.pub')
    )
    if ($DryRun) {
        foreach ($t in $targets) {
            $mark = '本就不存在'
            if (Test-Path $t) { $mark = '存在, 会删除' }
            Write-Host "      [DryRun] $t  ($mark)"
        }
    } else {
        foreach ($t in $targets) {
            Remove-Item $t -Recurse -Force -ErrorAction SilentlyContinue
        }
    }

    Write-Host ''
    if ($DryRun) {
        Write-Host '[DryRun] 预演结束 —— 以上是"会删掉的东西", 系统未被动过。' -ForegroundColor Cyan
        Write-Host '真正清洗请双击 程序\windows\clean-windows.bat (会弹 UAC)。' -ForegroundColor Cyan
    } else {
        Write-Host '本机连接痕迹已清除 (服务/功能/公钥/状态目录).' -ForegroundColor Green
        Write-Host '[!] 服务器侧设备仍会显示: 需联网后到 https://login.tailscale.com/admin/machines 删除该节点.' -ForegroundColor Yellow
    }
}

# ---- 主流程: 包一层 try/catch, 保证无论成功/报错窗口都不秒关 ----
try {
    Do-Clean
} catch {
    Write-Host ''
    Write-Host '==================== [!] 执行中断 (发生错误) ====================' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host '可重跑本脚本; 或截图以上报错反馈.' -ForegroundColor Yellow
}

Read-Host '回车退出'
