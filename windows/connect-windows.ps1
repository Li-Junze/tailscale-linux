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
$script:ConnCmd    = ''      # 最终连接命令, 脚本【最后一行】会重复一次方便直接抄
$SCRIPT_ID = 'v0.8-win-20261006'

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
        Start-Sleep -Seconds 8      # 不按 Enter: 全程零交互(窗口自己留 8 秒给人看清)
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
    # ★ 零交互: 包里没烘焙 authkey 也【不提问】。宁可后面 INDICATE 提示, 也不卡住对方。
    if (-not $key) {
        Write-Host '      [i] 包内未自带 authkey，跳过自动入网（不打扰你）。' -ForegroundColor Yellow
        Write-Host "      请让对方把 authkey 发来，或直接双击桌面的『重新入网』。" -ForegroundColor Yellow
        Write-Host "      手动也可以在助手指导下敲: tailscale up --authkey=你的KEY" -ForegroundColor Gray
        return
    }
    & $TSExe up --authkey=$key --accept-dns=true 2>&1 | Out-Host
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
        Write-Host '      [DryRun] 开机自启: sshd / ssh-agent / tailscale 均设 Automatic, 并建 Tailscale-AutoUp 开机任务'
        return
    }
    Write-Host '[3/5] 配置 Windows OpenSSH Server + 开机自启 (公钥免密)...'
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

    # ---- 开机自启总保障: 服务 Automatic + 开机自动 tailscale up ----
    # 目标: 对方重启电脑后什么都不用点, Tailscale 自动入网、sshd 自动监听。
    Write-Host '      [自启] 固化开机自启动 ...'
    foreach ($svc in @('sshd', 'ssh-agent', 'tailscale')) {
        if (-not (Get-Service $svc -ErrorAction SilentlyContinue)) { continue }
        # ★ Set / Start 分成两次 try: 之前合成一个 try, 只要 Start 抛一次异常,
        #   哪怕启动类型已经设好了也会被打成 [!], 看起来像没配上。
        $setErr = ''
        try { Set-Service $svc -StartupType Automatic -ErrorAction Stop }
        catch { $setErr = $_.Exception.Message }
        try { Start-Service $svc -ErrorAction SilentlyContinue } catch { }
        if ($setErr) {
            Write-Host "      [!] 服务 $svc 自启设置失败: $setErr" -ForegroundColor DarkYellow
        } else {
            Write-Host "      [OK] 服务 $svc -> Automatic (开机即运行)"
        }
    }
    # ---- 开机自动 tailscale up (延迟 30s, 等网络就绪) ----
    # ★ 血泪教训: schtasks /TR 的值里如果带空格路径, 必须把内层引号写成 \" ,
    #   写成 "C:\Program Files\...\tailscale.exe" up 时 schtasks 会把 " up"
    #   当成又一个参数 -> 报 "无效参数/选项 - 'Files\Tailscale\tailscale.exe up'"
    #   (2026-10-06 真机截图复现)。
    #   这里优先用 ScheduledTasks 模块(完全没有引号地狱), 失败再退回 schtasks。
    $autoUpOk = $false
    $autoUpMsg = ''
    try {
        $act = New-ScheduledTaskAction -Execute $TSExe -Argument 'up'
        $trg = New-ScheduledTaskTrigger -AtStartup
        $trg.Delay = 'PT30S'
        $pri = New-ScheduledTaskPrincipal -UserId 'SYSTEM' `
                   -LogonType ServiceAccount -RunLevel Highest
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries `
                   -DontStopIfGoingOnBatteries -StartWhenAvailable
        Register-ScheduledTask -TaskName 'Tailscale-AutoUp' -Action $act `
            -Trigger $trg -Principal $pri -Settings $set -Force `
            -ErrorAction Stop | Out-Null
        $autoUpOk = $true
    } catch {
        $autoUpMsg = $_.Exception.Message
        try {
            # 兜底: 老写法, 但把内层引号正确转义为 \"
            $tr = '\"' + $TSExe + '\" up'
            schtasks /Create /TN 'Tailscale-AutoUp' /TR "$tr" /SC ONSTART `
                /DELAY 0000:30 /RL HIGHEST /F 2>&1 | Out-Null
            if ($LASTEXITCODE -eq 0) { $autoUpOk = $true }
        } catch { $autoUpMsg = "$autoUpMsg / $($_.Exception.Message)" }
    }
    if ($autoUpOk) {
        Write-Host '      [OK] 开机任务 Tailscale-AutoUp 已建 (重启后自动入网)'
    } else {
        Write-Host "      [!] 建开机任务失败(不影响本次使用): $autoUpMsg" -ForegroundColor DarkYellow
    }
}

# ---- 4. 部署控制端公钥 (免密, 全程不提问) ----
# ★ 硬性要求: 对方必须是 0 交互。所以这里【没有任何 Read-Host】。
#   包里 keys/ 已烘焙好控制端公钥(打包时自动生成), 落盘即可; 万一缺失,
#   就静默在本机再生成一对并把公钥装上 —— 宁可多一步自动操作, 也不问对方。
function Install-PubKeys {
    if ($DryRun) {
        $pf = @(Get-ChildItem $KeysDir -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match '\.pub(\.local)?$' })
        $n = $pf.Count
        Write-Host "[4/5] [DryRun] keys/ 下可用公钥文件: $n 个"
        Write-Host '      [DryRun] 真实运行全程无提问, 公钥静默写入 administrators_authorized_keys'
        return
    }
    Write-Host '[4/5] 部署控制端公钥 (免密登录, 全程自动)...'
    $pubs = @()
    Get-ChildItem $KeysDir -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '\.pub(\.local)?$' } |
        ForEach-Object {
            (Get-Content $_.FullName) | ForEach-Object {
                $t = $_.Trim()
                if ($t) { $pubs += $t }
            }
        }

    if ($pubs.Count -eq 0) {
        # 极端情况(包里竟然没公钥): 静默生成一对, 不打扰对方
        Write-Host '      [i] 包内未带公钥 —— 自动在本机补生成一对 (ed25519, 无口令)...'
        $genKey = Join-Path $KeysDir 'id_ed25519'
        # 不传 -N: PowerShell 会把空字符串参数丢掉导致 ssh-keygen 报 Too many arguments,
        #          改用管道喂两个空行作为空口令, 跨版本稳定。
        "`r`n`r`n" | & ssh-keygen -t ed25519 -f "$genKey" 2>&1 | Out-Host
        if (Test-Path "$genKey.pub") {
            $pubs += (Get-Content "$genKey.pub").Trim()
            $script:GenKeyPath = $genKey
            Write-Host "      [OK] 已补生成: $genKey" -ForegroundColor Green
        } else {
            Write-Host '      [!] 本机也没有 ssh-keygen, 无法免密。对方仍可用密码登录。' -ForegroundColor Red
            return
        }
    } else {
        Write-Host "      [OK] 读到 $($pubs.Count) 个公钥, 静默写入, 无需你输入任何东西。"
    }

    if (-not (Test-Path 'C:\ProgramData\ssh')) {
        New-Item -ItemType Directory -Path 'C:\ProgramData\ssh' -Force | Out-Null
    }
    try {
        $pubs | Set-Content -Path $AdminKeys -Encoding ASCII
        # Windows 对管理员组用户: 公钥必须放 administrators_authorized_keys, 且 ACL 严格, 否则 sshd 拒绝
        icacls $AdminKeys /inheritance:r /grant 'SYSTEM:F' /grant 'BUILTIN\Administrators:F' | Out-Null
        # 顺手写一份到自己用户目录(非管理员组账号也能用)
        $userSsh = Join-Path $env:USERPROFILE '.ssh'
        if (-not (Test-Path $userSsh)) { New-Item -ItemType Directory -Path $userSsh -Force | Out-Null }
        $pubs | Set-Content -Path (Join-Path $userSsh 'authorized_keys') -Encoding ASCII
        Restart-Service sshd -Force
        Write-Host "      [OK] 已写入 $($pubs.Count) 个公钥 -> $AdminKeys"
        Write-Host '      [OK] sshd 已重载, 控制端可以免密连入。'
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
    $u = $env:USERNAME
    Write-Host ''
    Write-Host '  ============ 控制端要用的三样东西 ============' -ForegroundColor Cyan
    Write-Host '    (1) Tailscale IP : ' -NoNewline -ForegroundColor Gray
    Write-Host "$ip" -ForegroundColor White
    Write-Host '    (2) 本机用户名   : ' -NoNewline -ForegroundColor Gray
    Write-Host "$u" -ForegroundColor Yellow
    Write-Host '    (3) 连接命令     : ' -NoNewline -ForegroundColor Gray
    if ($script:GenKeyPath) {
        Write-Host "ssh -i `"$script:GenKeyPath`" $u@$ip" -ForegroundColor Green
    } else {
        Write-Host "ssh $u@$ip" -ForegroundColor Green
    }
    Write-Host '  ============================================' -ForegroundColor Cyan
    Write-Host ''
    # 记住连接命令, 脚本最后一行会再重复一次(方便直接抄最后一行)
    if ($script:GenKeyPath) {
        $script:ConnCmd = "ssh -i `"$script:GenKeyPath`" $u@$ip"
    } else {
        $script:ConnCmd = "ssh $u@$ip"
    }
    Write-Host '  ** (2) 这个用户名必须一字不差地抄到控制端 **' -ForegroundColor Yellow
    Write-Host '     少打或多打一个字母 => 这台机器上没这个账号 => 公钥压根不参与验证' -ForegroundColor Yellow
    Write-Host '     => 控制端只会被反复要密码 => Permission denied (公钥其实已装好)。' -ForegroundColor Yellow
    Write-Host '     [真实案例] lllxx 被抄成 lllxxx, 排查了两轮才发现是多了个字母。' -ForegroundColor DarkYellow
    if ($script:GenKeyPath) {
        Write-Host '  (连接命令里的私钥是本脚本代为生成的; 已拷到控制端就换成控制端那份路径)' -ForegroundColor Gray
    }
    Write-Host '  (控制端需已装 Tailscale 客户端并在同一 tailnet)'
    Write-Host ''
    # ---- 把上面三样写进包根目录, 便于直接复制粘贴, 免得手抄出错 ----
    if ($DryRun) {
        Write-Host '  [DryRun] 真正部署时会写出: 包根目录\连接信息.txt' -ForegroundColor Gray
    } else {
        $root = Split-Path -Parent (Split-Path -Parent $ScriptDir)
        try {
            $rows = @(
                '============ 控制端连接信息 ============',
                '',
                "Tailscale IP : $ip",
                "本机用户名   : $u",
                '',
                '★ 用户名必须一字不差。打错一个字母 => 反复要密码 + Permission denied,',
                '  而被控端其实早就装好了, 极易误判成"包坏了"。',
                '★ 控制端是 Windows: 打开 PowerShell, 直接粘贴下面那一行。',
                '★ 控制端是 Linux/macOS: 确保控制端私钥已生成, 其 .pub 已放进本机 keys/。',
                '',
                '=========== 复制下面这一行给控制端 ===========',
                "ssh $u@$ip",
                '============================================='
            ) -join "`r`n"
            Set-Content -Path (Join-Path $root '连接信息.txt') -Value $rows -Encoding UTF8
            Write-Host '  已写出 包根目录\连接信息.txt  (可直接抄给控制端)' -ForegroundColor Gray
        } catch {
            Write-Host "  [!] 写 连接信息.txt 失败, 不影响使用: $_" -ForegroundColor DarkYellow
        }
    }
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
    Write-Host "       scp -r 文件或目录 $u@$($ip):./"
    Write-Host ''
    Write-Host "     文件会落在本机 C:\Users\$u\ 下。"
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

# ==================== 最后一行: 连接命令 ====================
# 需求: 让对方能把【最后一行】直接复制过来, 不用在一堆输出里找。
if (-not $script:ConnCmd) { $script:ConnCmd = "ssh $env:USERNAME@100.x.x.x" }
Write-Host ''
Write-Host ''
Write-Host '==============================================================' -ForegroundColor Green
Write-Host '  请把【下面这一行】复制发给对方（就是最后这行）:' -ForegroundColor White
Write-Host ''
Write-Host ("    " + $script:ConnCmd) -ForegroundColor Green
Write-Host ''
Write-Host '  对方在自己电脑上粘贴执行即可免密连入本机，不需要输密码。' -ForegroundColor Yellow
Write-Host '==============================================================' -ForegroundColor Green
Write-Host ''
Read-Host '按 Enter 键关闭本窗口'




