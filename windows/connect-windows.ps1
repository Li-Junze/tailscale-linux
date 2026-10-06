# connect-windows.ps1 —— 纯 Tailscale 离线方案 · Windows 被控端
# 角色: 被控端 (被连入). 控制端无需本脚本, 用 Tailscale 客户端 + ssh 直连即可.
#
# 关键约束: Tailscale SSH 在 Windows 不支持服务端, 故改用
#           Windows 自带 OpenSSH Server + 公钥免密 (同样不需要密码).
# 依赖: PowerShell (Win10/11 自带). 不需要 curl/wget/python.
#       Tailscale MSI 已预置 assets/. 需管理员身份 (装服务/功能/写 ProgramData).
#
# 用法(二选一, 都不需要你自己去想"管理员"):
#   ① 双击/右键 程序\windows\connect-windows.bat   -> 会自动弹 UAC 提权
#   ② 本脚本会自己检查权限, 不是管理员就自动 UAC 重启自己
# 参数:  -DryRun   只做检查与打印, 不改动系统（排障用, 不需要管理员）

param([switch]$DryRun)

$ErrorActionPreference = 'Stop'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TSExe    = 'C:\Program Files\Tailscale\tailscale.exe'
# 不写死版本号: 通配取 assets\ 下任意 tailscale-setup-*.msi (换版本无需改脚本)
# ★ 必须先给空串再覆盖: 轻量包没有 MSI, 直接对 $null 调 Test-Path 会抛
#   "Cannot bind argument to parameter 'Path' because it is null" 并让脚本中断。
$MSI = ''
$hit = Get-ChildItem (Join-Path $ScriptDir 'assets') -Filter 'tailscale-setup-*.msi' -ErrorAction SilentlyContinue |
       Sort-Object LastWriteTime -Descending | Select-Object -First 1
if ($hit) { $MSI = $hit.FullName }
$KeysDir  = Join-Path $ScriptDir 'keys'
$AdminKeys= 'C:\ProgramData\ssh\administrators_authorized_keys'
$script:GenKeyPath = $null   # 若本脚本代生成密钥对, 记录私钥路径, Finish 时打印 -i 连接命令
$SCRIPT_ID = 'v0.7-win-20261006'

function Banner($m){ Write-Host ''; Write-Host "==== $m ====" -ForegroundColor Cyan }

# ---- 0. 权限: 不是管理员就自动弹 UAC 用管理员身份重启自己 ----
#  ★ 提权逻辑放在 ps1 里(而不是 bat 里拼三层引号), 彻底避免路径含空格/中文时的引号地狱
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
        Write-Host '[X] 提权被拒绝或失败。请右键 程序\windows\connect-windows.bat -> 以管理员身份运行。' -ForegroundColor Red
        Read-Host '按 Enter 退出'
    }
    exit 0
}
if ($DryRun) {
    Write-Host '[i] -DryRun 模式: 只做检查, 不会安装/修改任何东西。' -ForegroundColor Cyan
    Write-Host "    当前是否管理员: $IsAdmin"
}

Banner "纯 Tailscale 离线版 · Windows 被控端 $SCRIPT_ID"
Write-Host "本机用户: $env:USERNAME   主机名: $env:COMPUTERNAME"

# ---- 1. 安装 Tailscale (优先预置 MSI 离线; 轻量包则联网下载 EXE) ----
function Install-Tailscale {
    if ($DryRun) {
        Write-Host '[1/5] [DryRun] Tailscale 安装: ' -NoNewline
        if (Test-Path $TSExe) { Write-Host '已安装, 无需动作' }
        elseif ($MSI -and (Test-Path $MSI)) { Write-Host "会用包内 MSI 离线安装 -> $MSI" }
        else { Write-Host '包内无 MSI(轻量包), 需要联网下载安装器' }
        return
    }
    if (Test-Path $TSExe) { Write-Host '[1/5] Tailscale 已安装, 跳过安装.'; return }

    if (-not ($MSI -and (Test-Path $MSI))) {
        # 轻量模式: 包内无 MSI, 联网下载官方安装器
        Write-Host '[1/5] 本包为【轻量模式】(未内置 MSI), 联网下载 Tailscale 安装器 ...' -ForegroundColor Cyan
        $tmpExe = Join-Path $env:TEMP 'tailscale-setup.exe'
        $url = 'https://pkgs.tailscale.com/stable/tailscale-setup-latest.exe'
        try {
            Write-Host "      下载: $url"
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            Invoke-WebRequest -Uri $url -OutFile $tmpExe -UseBasicParsing -TimeoutSec 180
        } catch {
            Write-Host "[X] 下载失败: $($_.Exception.Message)" -ForegroundColor Red
            Write-Host '    两条路任选其一:' -ForegroundColor Yellow
            Write-Host '      A) 让这台机器联网后重跑本脚本' -ForegroundColor Yellow
            Write-Host '      B) 换一台有网机器下载 MSI, 拷到 程序\windows\assets\ 后重跑' -ForegroundColor Yellow
            return
        }
        if (-not (Test-Path $tmpExe)) { Write-Host '[X] 下载器未生成' -ForegroundColor Red; return }
        Write-Host '      运行安装器 ...'
        $p = Start-Process $tmpExe -ArgumentList '/quiet' -Wait -PassThru
        Remove-Item $tmpExe -Force -ErrorAction SilentlyContinue
        if (-not (Test-Path $TSExe)) { Write-Host "[X] 安装失败 exit=$($p.ExitCode)" -ForegroundColor Red; return }
    } else {
        Write-Host '[1/5] 安装 Tailscale (预置 MSI, 离线零下载)...'
        $p = Start-Process msiexec.exe -ArgumentList "/i `"$MSI`" /quiet /norestart" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Write-Host "[X] MSI 安装失败 exit=$($p.ExitCode)" -ForegroundColor Red; return }
    }
    $t=0; while ($t -lt 30) { if (Get-Service tailscale -ErrorAction SilentlyContinue) { break }; Start-Sleep 1; $t++ }
    Write-Host '      Tailscale 安装完成, 服务已注册.'
}

# ---- 2. 入网 (authkey) ----
function Join-Tailnet {
    if ($DryRun) {
        $hasKey = [bool]$env:TS_AUTHKEY -or (Test-Path (Join-Path $KeysDir 'authkey.local.txt'))
        $k = '未预置, 运行时提示粘贴'
        if ($hasKey) { $k = '已预置, 自动入网' }
        Write-Host "[2/5] [DryRun] 入网 authkey: $k"
        return
    }
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
    if ($DryRun) {
        $state = '查询不到(需管理员权限, 或系统较旧)'
        try {
            $cap = Get-WindowsCapability -Online -ErrorAction Stop |
                   Where-Object { $_.Name -like 'OpenSSH.Server*' }
            if ($cap) { $state = $cap.State }
        } catch { }
        Write-Host "[3/5] [DryRun] OpenSSH Server: $state"
        return
    }
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
    Write-Host '      ① 控制端是 Windows:' -ForegroundColor White
    Write-Host '         - CMD (命令提示符):' -ForegroundColor Gray
    Write-Host '             type %USERPROFILE%\.ssh\id_ed25519.pub' -ForegroundColor Green
    Write-Host '             若提示找不到文件, 先生成:  ssh-keygen -t ed25519 -N "" -f %USERPROFILE%\.ssh\id_ed25519' -ForegroundColor Green
    Write-Host '         - PowerShell (注意: 路径用 $env:, 不要用 %VAR%):' -ForegroundColor Gray
    Write-Host '             type "$env:USERPROFILE\.ssh\id_ed25519.pub"' -ForegroundColor Green
    Write-Host '             若没有, 生成密钥对 (提示 passphrase 时直接回车两次):' -ForegroundColor Gray
    Write-Host '             ssh-keygen -t ed25519 -f "$env:USERPROFILE\.ssh\id_ed25519"' -ForegroundColor Green
    Write-Host '             (PowerShell 里切勿写 -N "": 空字符串会被丢弃而报 Too many arguments)' -ForegroundColor DarkYellow
    Write-Host '      ② 控制端是 Linux / macOS / WSL:' -ForegroundColor White
    Write-Host '          cat ~/.ssh/id_ed25519.pub' -ForegroundColor Green
    Write-Host '          若没有:  ssh-keygen -t ed25519 -N "" -f ~/.ssh/id_ed25519' -ForegroundColor Green
    Write-Host '      ③ 已有 keys/ 目录里的 *.pub 文件则无需粘贴, 脚本自动读取.' -ForegroundColor Gray
        Write-Host '      (复制 ssh-ed25519 AAAA... 开头的那整行, 回到下面粘贴)' -ForegroundColor DarkCyan
        Write-Host '      ※ 若控制端完全没密钥, 本脚本第4步可帮你在本机直接生成一对 (选 y 即可).' -ForegroundColor Magenta
}

function Install-PubKeys {
    if ($DryRun) {
        $pf = @(Get-ChildItem $KeysDir -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '\.pub(\.local)?$' })
        Write-Host "[4/5] [DryRun] keys/ 下可用公钥文件: $($pf.Count) 个"
        return
    }
    Write-Host '[4/5] 部署控制端公钥 (免密登录)...'
    Print-KeyHelp
    $pubs = @()
    Get-ChildItem $KeysDir -ErrorAction SilentlyContinue | Where-Object { $_.Name -match '\.pub(\.local)?$' } | ForEach-Object { (Get-Content $_.FullName) | ForEach-Object { if ($_.Trim()) { $pubs += $_.Trim() } } }
    $extra = Read-Host '粘贴控制端公钥 (直接粘一行 ssh-ed25519 ..., 或回车用 keys/ 内文件/自动生成)'
    if ($extra.Trim()) { $pubs += $extra.Trim() }

    if ($pubs.Count -eq 0) {
        Write-Host '      [!] 未检测到任何公钥, 控制端将无法免密登录.' -ForegroundColor Red
        $gen = Read-Host '      是否在本机生成一对新密钥供控制端使用? (y/N, 默认 N)'
        if ($gen -match '^[Yy]') {
            $genKey = Join-Path $KeysDir 'id_ed25519'
            if (Test-Path "$genKey.pub") {
                Write-Host "      已存在 $genKey.pub, 直接复用." -ForegroundColor Gray
            } else {
                Write-Host '      生成密钥对 (ed25519, 无口令)...'
                # 关键: PowerShell 会把空字符串参数 -N "" / -N '' 直接丢弃, 导致 ssh-keygen 报
                #   "Too many arguments" (-N 吃掉后面的 -f)。因此【不传 -N】, 改为用管道喂入
                #   两个空行作为空口令, 跨 PowerShell 版本都稳定, 且不碰空参数 bug.
                "`r`n`r`n" | & ssh-keygen -t ed25519 -f "$genKey" 2>&1 | Out-Host
                if ($LASTEXITCODE -ne 0 -or -not (Test-Path "$genKey.pub")) {
                    Write-Host '      [X] ssh-keygen 失败 (可能未装 OpenSSH 客户端). 请按上方指示在控制端手动生成.' -ForegroundColor Red
                    Print-KeyHelp
                    return
                }
            }
            $pubs += (Get-Content "$genKey.pub").Trim()
            $script:GenKeyPath = $genKey
            Write-Host "      [OK] 已生成私钥: $genKey" -ForegroundColor Green
            Write-Host '      → 请把这个私钥文件复制到你的【控制端电脑】(私钥必须放在连出那台机器上):' -ForegroundColor Yellow
            Write-Host '          Windows 控制端: 复制到 用户目录\.ssh\id_ed25519  (CMD: %USERPROFILE%\.ssh\id_ed25519)' -ForegroundColor Green
            Write-Host '          Linux/macOS 控制端: 复制到 ~/.ssh/id_ed25519 并执行 chmod 600' -ForegroundColor Green
            Write-Host '      → 之后即可用该私钥免密连入本机 (连接命令见末尾).' -ForegroundColor Yellow
            Write-Host '      [!] 拷贝到控制端后, 建议删掉本机这份私钥 (clean-windows.bat 会自动清除).' -ForegroundColor Gray
        } else {
            Write-Host '      请按上方指示, 在控制端电脑生成公钥, 把那整行粘回来.' -ForegroundColor Yellow
            Print-KeyHelp
            return
        }
    }
    if (-not (Test-Path 'C:\ProgramData\ssh')) { New-Item -ItemType Directory -Path 'C:\ProgramData\ssh' -Force | Out-Null }
    try {
        $pubs | Set-Content -Path $AdminKeys -Encoding ASCII
        # Windows 对管理员组用户: 公钥必须放 administrators_authorized_keys, 且 ACL 严格, 否则 sshd 拒绝
        icacls $AdminKeys /inheritance:r /grant 'SYSTEM:F' /grant 'BUILTIN\Administrators:F' | Out-Null
        Restart-Service sshd -Force
        Write-Host "      已写入 $($pubs.Count) 个公钥 -> $AdminKeys"
    } catch {
        Write-Host "      [!] 写公钥/设权限/重启 sshd 出错: $_" -ForegroundColor Red
        Write-Host '      公钥文件可能已写入, 但请确认 sshd 服务正在运行 (Get-Service sshd).' -ForegroundColor Yellow
    }
}

# ---- 5. 完成 ----
function Finish {
    Write-Host '[5/5] 完成.'
    if ($DryRun) { $ip = '100.x.x.x' } else { $ip = (& $TSExe ip -4 2>$null) -join ',' }
    if ($DryRun) { Banner '[DryRun] 流程预演结束 —— 真正部署后这里会显示:' }
    else { Banner '本机(被控端)已就绪' }
    Write-Host "  Tailscale IP : $ip"
    if ($script:GenKeyPath) {
        Write-Host "  连接命令    : ssh -i `"$script:GenKeyPath`" $env:USERNAME@$ip"
        Write-Host '                (用本脚本生成的私钥; 已拷到控制端则把路径换成控制端那份)'
    } else {
        Write-Host "  连接命令    : ssh $env:USERNAME@$ip"
    }
    Write-Host '  (控制端需已装 Tailscale 客户端并在同一 tailnet)'
    Write-Host ''
    Write-Host '  开机自启: Tailscale 服务 + sshd 均 Automatic (无需额外配置)。'
    Write-Host '  [!] 建议到 login.tailscale.com 把本机 Key expiry 设为 Disable, 否则过期需重跑.'
    Write-Host '  用完清洗: 以管理员运行 clean-windows.bat'
    Write-Host ''
    # ---------- 传文件: 只提示, 不引入任何新依赖 ----------
    # ★ 被控端不装任何东西。前面已装好 OpenSSH Server, 控制端直接用
    #   系统自带的 sftp / scp 往这台机器传文件即可。
    Write-Host '  ── 想把文件发到这台机器? ──'
    Write-Host '     在你自己的(控制端)电脑上执行, 把下面的 IP 换成本机的:'
    Write-Host ''
    Write-Host "       scp -r 文件或目录 $env:USERNAME@$($ip):./"
    Write-Host ''
    Write-Host "     文件会落在本机 C:\Users\$env:USERNAME\ 下。"
    Write-Host '     (控制端若提示输密码, 本方案用的是公钥免密, 直接回车即可)'
}

# ---- 主流程: 包一层 try/catch, 保证无论成功/报错窗口都不秒关 ----
try {
    Install-Tailscale
    Join-Tailnet
    Enable-SSHServer
    Install-PubKeys
    Finish
} catch {
    Write-Host ''
    Write-Host '==================== [!] 执行中断 (发生错误) ====================' -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    if ($_.ScriptStackTrace) { Write-Host $_.ScriptStackTrace -ForegroundColor DarkGray }
    Write-Host '可重跑本脚本 (已完成的步骤会自动跳过); 或截图以上报错反馈.' -ForegroundColor Yellow
}

if ($DryRun) {
    Write-Host ''
    Write-Host '==================== [DryRun] 预演结束 ====================' -ForegroundColor Cyan
    Write-Host '  上面只是"将要做什么", 系统未做任何改动。' -ForegroundColor Cyan
    Write-Host '  真正部署请双击: 程序\windows\connect-windows.bat  (会自动弹 UAC)' -ForegroundColor Cyan
}

Write-Host ''
Read-Host '按 Enter 键关闭本窗口'




