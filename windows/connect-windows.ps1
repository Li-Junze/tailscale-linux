# connect-windows.ps1 —— 纯 Tailscale 离线方案 · Windows 被控端
# 角色: 被控端 (被连入). 控制端无需本脚本, 用 Tailscale 客户端 + ssh 直连即可.
#
# 关键约束: Tailscale SSH 在 Windows 不支持服务端, 故改用
#           Windows 自带 OpenSSH Server + 公钥免密 (同样不需要密码).
# 依赖: PowerShell (Win10/11 自带). 不需要 curl/wget/python.
#       Tailscale MSI 已预置 assets/. 需管理员身份 (装服务/功能/写 ProgramData).
#
# 用法: 右键 connect-windows.bat -> 以管理员身份运行 (会自动提权并执行本脚本)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TSExe    = 'C:\Program Files\Tailscale\tailscale.exe'
$MSI      = Join-Path $ScriptDir 'assets\tailscale-setup-1.102.4-amd64.msi'
$KeysDir  = Join-Path $ScriptDir 'keys'
$AdminKeys= 'C:\ProgramData\ssh\administrators_authorized_keys'
$SCRIPT_ID = 'v0.2-win-20261006'

function Banner($m){ Write-Host ''; Write-Host "==== $m ====" -ForegroundColor Cyan }

# ---- 0. 管理员检查 ----
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host '[X] 需要管理员权限. 请右键 connect-windows.bat -> 以管理员身份运行.' -ForegroundColor Red
    Read-Host '回车退出'; exit 1
}

Banner "纯 Tailscale 离线版 · Windows 被控端 $SCRIPT_ID"
Write-Host "本机用户: $env:USERNAME   主机名: $env:COMPUTERNAME"

# ---- 1. 安装 Tailscale (预置 MSI, 离线) ----
function Install-Tailscale {
    if (Test-Path $TSExe) { Write-Host '[1/5] Tailscale 已安装, 跳过 MSI.'; return }
    if (-not (Test-Path $MSI)) {
        Write-Host "[X] 未找到预置 MSI: $MSI" -ForegroundColor Red
        Write-Host '      请使用 Release 完整包 (含 assets), 或用 stage 脚本预置.' -ForegroundColor Yellow
        Read-Host '回车退出'; exit 1
    }
    Write-Host '[1/5] 安装 Tailscale (预置 MSI, 离线零下载)...'
    $p = Start-Process msiexec.exe -ArgumentList "/i `"$MSI`" /quiet /norestart" -Wait -PassThru
    if ($p.ExitCode -ne 0) { Write-Host "[X] MSI 安装失败 exit=$($p.ExitCode)" -ForegroundColor Red; Read-Host '回车退出'; exit 1 }
    $t=0; while ($t -lt 30) { if (Get-Service tailscale -ErrorAction SilentlyContinue) { break }; Start-Sleep 1; $t++ }
    Write-Host '      Tailscale 安装完成, 服务已注册.'
}

# ---- 2. 入网 (authkey) ----
function Join-Tailnet {
    Write-Host '[2/5] 启动 Tailscale 并入网...'
    $key = $env:TS_AUTHKEY
    $localKey = Join-Path $KeysDir 'authkey.local.txt'
    if (-not $key -and (Test-Path $localKey)) {
        $m = Select-String -Path $localKey -Pattern 'TS_AUTHKEY=([^\s]+)' | Select-Object -First 1
        if ($m) { $key = $m.Matches.Groups[1].Value }
    }
    if (-not $key) {
        $key = Read-Host '粘贴 Tailscale authkey (留空跳过, 之后手动 tailscale up)'
    }
    if ($key) {
        & $TSExe up --authkey=$key --accept-dns=true 2>&1 | Out-Host
    } else {
        Write-Host '      [!] 未提供 key, 请稍后手动: ' -ForegroundColor Yellow
        Write-Host "      & '$TSExe' up" -ForegroundColor Yellow
    }
}

# ---- 3. 启用 OpenSSH Server ----
function Enable-SSHServer {
    Write-Host '[3/5] 配置 Windows OpenSSH Server (公钥免密)...'
    $cap = Get-WindowsCapability -Online | Where-Object { $_.Name -like 'OpenSSH.Server*' }
    if ($cap.State -ne 'Installed') {
        Write-Host '      安装 OpenSSH Server 功能 (系统自带 payload, 通常离线可用)...'
        try { Add-WindowsCapability -Online -Name 'OpenSSH.Server~~~~0.0.1.0' | Out-Null }
        catch { Write-Host "[!] 离线安装失败: $_" -ForegroundColor Red
                Write-Host '      请联网后重试, 或手动: 设置 -> 可选功能 -> 添加 OpenSSH 服务器.' -ForegroundColor Yellow; return }
    }
    Set-Service sshd -StartupType Automatic
    Start-Service sshd
    try { Set-Service ssh-agent -StartupType Automatic; Start-Service ssh-agent } catch {}
    Write-Host '      sshd 已启动并设为开机自启.'
}

# ---- 4. 部署控制端公钥 (免密) ----
function Print-KeyHelp {
    Write-Host '      ── 如何在【控制端电脑】上拿到你的公钥 (复制输出整行) ──' -ForegroundColor DarkCyan
    Write-Host '      ① 控制端是 Windows (PowerShell / CMD):' -ForegroundColor White
    Write-Host '          type %USERPROFILE%\.ssh\id_ed25519.pub' -ForegroundColor Green
    Write-Host '          若提示"找不到文件", 先生成密钥对 (一路回车即可):' -ForegroundColor Gray
    Write-Host '          ssh-keygen -t ed25519 -N "" -f %USERPROFILE%\.ssh\id_ed25519' -ForegroundColor Green
    Write-Host '      ② 控制端是 Linux / macOS / WSL:' -ForegroundColor White
    Write-Host '          cat ~/.ssh/id_ed25519.pub' -ForegroundColor Green
    Write-Host '          若没有:  ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519' -ForegroundColor Green
    Write-Host '      ③ 已有 keys/ 目录里的 *.pub 文件则无需粘贴, 脚本自动读取.' -ForegroundColor Gray
    Write-Host '      (复制 ssh-ed25519 AAAA... 开头的那整行, 回到下面粘贴)' -ForegroundColor DarkCyan
}

function Install-PubKeys {
    Write-Host '[4/5] 部署控制端公钥 (免密登录)...'
    Print-KeyHelp
    $pubs = @()
    Get-ChildItem $KeysDir -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.pub(\.local)?$' } | ForEach-Object { (Get-Content $_.FullName) | ForEach-Object { if ($_.Trim()) { $pubs += $_.Trim() } } }
    $extra = Read-Host '粘贴控制端公钥 (直接粘一行 ssh-ed25519 ..., 或回车用 keys/ 内文件)'
    if ($extra.Trim()) { $pubs += $extra.Trim() }
    if ($pubs.Count -eq 0) {
        Write-Host '      [!] 还未检测到任何公钥, 控制端将无法免密登录.' -ForegroundColor Red
        Write-Host '      请按上方指示, 在控制端电脑执行取钥命令, 然后把那整行粘回来.' -ForegroundColor Yellow
        Print-KeyHelp
        return
    }
    if (-not (Test-Path 'C:\ProgramData\ssh')) { New-Item -ItemType Directory -Path 'C:\ProgramData\ssh' -Force | Out-Null }
    $pubs | Set-Content -Path $AdminKeys -Encoding ASCII
    # Windows 对管理员组用户: 公钥必须放 administrators_authorized_keys, 且 ACL 严格, 否则 sshd 拒绝
    icacls $AdminKeys /inheritance:r /grant 'SYSTEM:F' /grant 'BUILTIN\Administrators:F' | Out-Null
    Restart-Service sshd -Force
    Write-Host "      已写入 $($pubs.Count) 个公钥 -> $AdminKeys"
}

# ---- 5. 完成 ----
function Finish {
    Write-Host '[5/5] 完成.'
    $ip = (& $TSExe ip -4 2>$null) -join ','
    Banner '本机(被控端)已就绪'
    Write-Host "  Tailscale IP : $ip"
    Write-Host "  连接命令    : ssh $env:USERNAME@$ip"
    Write-Host '  (控制端需已装 Tailscale 客户端并在同一 tailnet)'
    Write-Host ''
    Write-Host '  开机自启: Tailscale 服务 + sshd 均 Automatic (无需额外配置).'
    Write-Host '  ⚠ 建议到 login.tailscale.com 把本机 Key expiry 设为 Disable, 否则过期需重跑.'
    Write-Host '  用完清洗: 以管理员运行 clean-windows.bat'
    Read-Host '回车退出'
}

Install-Tailscale
Join-Tailnet
Enable-SSHServer
Install-PubKeys
Finish


