# install.ps1 -- Tailscale 远程工具箱 · 被控端一键部署（零交互）
# ---------------------------------------------------------------------------
# 由网页控制台现场生成，房间号 / 中继地址 / 控制端回信地址 已烘焙进本文件。
# 对方只要双击同目录的外层引导脚本，剩下的全自动，最多弹一次 UAC。
#
# 流程: 装 Tailscale -> 入网 -> 开 SSH(公钥免密) -> 搬迁到标准安装目录
#       -> 注册开机自启 -> 拉起消息 agent -> 回传本机信息 -> 清理安装残留
# ---------------------------------------------------------------------------
param(
    [string]$Room        = '__ROOM__',
    [string]$Relay       = '__RELAY__',
    [string]$ControlUser = '__CTRL_USER__',     # 控制端 ssh 用户名(用于 scp 回传)
    [string]$ControlHost = '__CTRL_HOST__',     # 控制端 tailscale IP
    [string]$ControlPath = '__CTRL_PATH__',
    [string]$PTok        = '__PTOK__',      # 设备令牌(控制台门禁用, 没它 agent 发不出心跳)
    [string]$InstallDir  = '',      # 标准安装目录(引导脚本传入, 避免提权后变量漂移)
    [string]$SourceDir   = '',      # 外层引导所在目录(部署完要清掉的临时物)
    [switch]$KeepSource,            # 保留下载目录里的 zip/引导脚本(排障用)
    [switch]$NoRelocate,            # 不搬迁, 就地跑(排障用)
    [switch]$DryRun,                # 只预演, 不改系统
    [switch]$Elevated               # 内部用: 已经是提权后的第二次运行
)

$ErrorActionPreference = 'Stop'
$VER = '1.1-web'
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$TSExe     = 'C:\Program Files\Tailscale\tailscale.exe'
$TSIpn     = 'C:\Program Files\Tailscale\tailscale-ipn.exe'
$KeysDir   = Join-Path $ScriptDir 'keys'
$AdminKeys = 'C:\ProgramData\ssh\administrators_authorized_keys'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA 'TailscaleRemote' }

$script:Lines = @()
function Say($m, $c = 'Gray') { Write-Host $m -ForegroundColor $c; $script:Lines += $m }
function Banner($m) { Write-Host ''; Write-Host "==== $m ====" -ForegroundColor Cyan; $script:Lines += "[==] $m" }
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# ---------------------------------------------------------------- HTTP(回传用)
function Add-Tok([string]$url) {
    if (-not $PTok) { return $url }
    if ($url -match '[?&]t=') { return $url }
    if ($url -match '\?') { return ($url + '&t=' + $PTok) }
    return ($url + '?t=' + $PTok)
}
function Http-Json([string]$url, [string]$method = 'GET', $obj = $null) {
    try {
        $r = [Net.HttpWebRequest]::Create((Add-Tok $url))
        $r.Method = $method; $r.Timeout = 20000; $r.ReadWriteTimeout = 60000
        $r.UserAgent = "ts-remote-install/$VER"
        if ($null -ne $obj) {
            $b = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 6))
            $r.ContentType = 'application/json; charset=utf-8'; $r.ContentLength = $b.Length
            $s = $r.GetRequestStream(); $s.Write($b, 0, $b.Length); $s.Close()
        }
        $resp = $r.GetResponse()
        $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
        $t = $sr.ReadToEnd(); $sr.Close(); $resp.Close()
        return ($t | ConvertFrom-Json)
    } catch { return $null }
}

# ============================================================ 0. 提权(只弹一次 UAC)
$IsAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
           ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)

if (-not $IsAdmin -and -not $DryRun -and -not $Elevated) {
    Write-Host ''
    Write-Host '[!] 需要管理员权限, 正在弹 UAC ... 请点【是】, 之后看新弹出的窗口。' -ForegroundColor Yellow
    $a = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"' + $PSCommandPath + '"'),
           '-Elevated', '-Room', $Room, '-Relay', $Relay)
    if ($ControlUser) { $a += @('-ControlUser', $ControlUser) }
    if ($ControlHost) { $a += @('-ControlHost', $ControlHost) }
    if ($ControlPath) { $a += @('-ControlPath', ('"' + $ControlPath + '"')) }
    if ($InstallDir)  { $a += @('-InstallDir',  ('"' + $InstallDir  + '"')) }
    if ($SourceDir)   { $a += @('-SourceDir',   ('"' + $SourceDir   + '"')) }
    if ($KeepSource)  { $a += '-KeepSource' }
    if ($NoRelocate)  { $a += '-NoRelocate' }
    try { Start-Process powershell -Verb RunAs -ArgumentList $a }
    catch { Write-Host '[X] 提权失败, 请右键引导脚本 -> 以管理员身份运行。' -ForegroundColor Red; Start-Sleep 8 }
    exit 0
}

Banner "Tailscale 远程工具箱 · 一键部署 $VER"
Say "  本机: $env:COMPUTERNAME / $env:USERNAME     房间: $Room"
Say "  管理员: $IsAdmin     安装目录: $InstallDir"
if ($DryRun) { Say '  [DryRun] 只预演, 不会改动系统' 'Cyan' }

# ============================================================ 1. Tailscale
function Install-Tailscale {
    Banner '1/7 安装 Tailscale'
    if ((Test-Path $TSExe) -or (Get-Command tailscale -ErrorAction SilentlyContinue)) {
        Say '  [OK] 已安装, 跳过' 'Green'; return
    }
    $msi = ''
    $hit = Get-ChildItem (Join-Path $ScriptDir 'assets') -Filter 'tailscale-setup-*.msi' -ErrorAction SilentlyContinue |
           Sort-Object LastWriteTime -Descending | Select-Object -First 1
    if ($hit) { $msi = $hit.FullName }
    if ($DryRun) { Say "  [DryRun] 将安装 Tailscale (离线MSI=$([bool]$msi))"; return }
    if ($msi) {
        Say "  .. 离线安装: $msi"
        Start-Process msiexec.exe -ArgumentList @('/i', $msi, '/quiet', '/norestart') -Wait
    } else {
        $tmp = Join-Path $env:TEMP 'tailscale-setup-latest.msi'
        Say '  .. 联网下载安装包 (pkgs.tailscale.com) ...'
        (New-Object Net.WebClient).DownloadFile('https://pkgs.tailscale.com/stable/tailscale-setup-latest.msi', $tmp)
        Start-Process msiexec.exe -ArgumentList @('/i', $tmp, '/quiet', '/norestart') -Wait
    }
    Start-Sleep -Seconds 3
    if (Test-Path $TSExe) { Say '  [OK] Tailscale 装好了' 'Green' }
    else { Say '  [!] 未检测到 tailscale.exe, 后面的入网可能失败' 'Yellow' }
}

# ============================================================ 2. 入网
function Join-Tailnet {
    Banner '2/7 加入 Tailscale 网络'
    if (-not (Test-Path $TSExe)) { Say '  [!] 没有 tailscale.exe, 跳过' 'Yellow'; return }
    $key = ''
    $kf = Join-Path $KeysDir 'authkey.local.txt'
    if (Test-Path $kf) { $key = ((Get-Content $kf -Raw) -as [string]).Trim() }
    if ($DryRun) { Say "  [DryRun] 将入网 (authkey=$([bool]$key))"; return }
    Set-Service -Name tailscale -StartupType Automatic -ErrorAction SilentlyContinue
    Start-Service -Name tailscale -ErrorAction SilentlyContinue
    if ($key) { & $TSExe up --authkey=$key --accept-routes 2>&1 | Out-Host }
    else      { & $TSExe up --accept-routes 2>&1 | Out-Host }
    # 自愈: 服务在跑但托盘程序没起 -> BackendState=NoState -> 拿不到 100.x
    $ip = ''
    for ($i = 0; $i -lt 8; $i++) {
        Start-Sleep -Seconds 2
        $ip = (& $TSExe ip -4 2>$null | Select-Object -First 1)
        if ($ip -like '100.*') { break }
        if ($i -eq 2 -and (Test-Path $TSIpn)) {
            Say '  .. 未拿到 100.x, 拉起托盘程序自愈 ...' 'Yellow'
            Start-Process $TSIpn -ErrorAction SilentlyContinue
        }
    }
    if ($ip -like '100.*') { Say "  [OK] 已入网: $ip" 'Green' }
    else { Say '  [!] 还没拿到 100.x 地址, 请在托盘里确认已登录(不影响后续步骤)' 'Yellow' }
}

# ============================================================ 3. OpenSSH Server
function Enable-SSHServer {
    Banner '3/7 开启 OpenSSH Server'
    # ★ Get-WindowsCapability 本身就要管理员, DryRun 且未提权时别去查, 否则整条链断在这
    if ($DryRun -and -not $IsAdmin) { Say '  [DryRun] 将开启 OpenSSH.Server 并设为开机自启'; return }
    $cap = $null
    try {
        $cap = Get-WindowsCapability -Online -ErrorAction Stop |
               Where-Object { $_.Name -like 'OpenSSH.Server*' } | Select-Object -First 1
    } catch { }
    if (-not $cap) { Say '  [!] 查不到 OpenSSH.Server 功能(通常是没提权), 后面的 ssh 免密可能不可用' 'Yellow'; return }
    if ($DryRun) { Say "  [DryRun] 将开启 OpenSSH.Server (当前=$($cap.State))"; return }
    if ($cap.State -ne 'Installed') {
        Say '  .. 安装 OpenSSH.Server ...'
        Add-WindowsCapability -Online -Name $cap.Name | Out-Null
    }
    foreach ($s in @('sshd', 'ssh-agent')) {
        if (Get-Service $s -ErrorAction SilentlyContinue) {
            Set-Service $s -StartupType Automatic
            Start-Service $s -ErrorAction SilentlyContinue
            Say "  [OK] $s -> Automatic" 'Green'
        }
    }
}

# ============================================================ 4. 公钥免密
function Install-PubKeys {
    Banner '4/7 部署控制端公钥(免密登录)'
    $pub = ''
    $pf = Join-Path $KeysDir 'control.pub'
    if (Test-Path $pf) { $pub = ((Get-Content $pf -Raw) -as [string]).Trim() }
    if (-not $pub) { Say '  [!] 包里没有 control.pub, 跳过(控制端将需要密码)' 'Yellow'; return }
    if ($DryRun) { Say '  [DryRun] 将写入 administrators_authorized_keys'; return }
    New-Item -ItemType Directory -Path 'C:\ProgramData\ssh' -Force -ErrorAction SilentlyContinue | Out-Null
    $old = ''
    if (Test-Path $AdminKeys) { $old = ((Get-Content $AdminKeys -Raw) -as [string]) }
    if ($old -notmatch [regex]::Escape($pub)) {
        Add-Content -Path $AdminKeys -Value $pub -Encoding UTF8
    }
    & icacls.exe $AdminKeys /inheritance:r /grant 'SYSTEM:(R)' /grant 'BUILTIN\Administrators:(R)' 2>&1 | Out-Null
    Say '  [OK] 公钥已授权, 控制端可免密 ssh 进来' 'Green'
}

# ============================================================ 5. 搬迁到标准安装目录
function Move-ToInstallDir {
    Banner '5/7 安装到标准目录'
    if ($NoRelocate) { Say '  [skip] -NoRelocate'; return $ScriptDir }
    if ($DryRun) { Say "  [DryRun] 将 $ScriptDir -> $InstallDir"; return $InstallDir }
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    # robocopy 返回码 <8 都算成功
    $rc = (Start-Process robocopy -ArgumentList @('"' + $ScriptDir + '"', '"' + $InstallDir + '"',
           '/E', '/R:1', '/W:1', '/NFL', '/NDL', '/NJH', '/NJS', '/NP') -Wait -PassThru).ExitCode
    if ($rc -ge 8) { Say "  [!] 复制返回 $rc, 但通常仍可用" 'Yellow' }
    $ini = Join-Path $InstallDir 'agent.ini'
    @("room=$Room", "relay=$Relay", "installdir=$InstallDir",
      "host=$env:COMPUTERNAME", "user=$env:USERNAME", "token=$PTok",
      "control=$ControlUser@$ControlHost", "installed=$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')") |
        Set-Content -Path $ini -Encoding UTF8
    Say "  [OK] 已安装到: $InstallDir" 'Green'
    return $InstallDir
}

# ============================================================ 6. 开机自启 + 拉起 agent
function Enable-AutoStart([string]$dir) {
    Banner '6/7 开机自启 + 启动消息通道'
    $agent = Join-Path $dir 'notify.ps1'
    if (-not (Test-Path $agent)) { Say '  [!] 找不到 notify.ps1, 跳过' 'Yellow'; return }
    if ($DryRun) { Say "  [DryRun] 将注册开机任务并启动 $agent"; return }
    $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
    $arg = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File ""$agent"""
    try {
        $act = New-ScheduledTaskAction -Execute $ps -Argument $arg
        $trg = New-ScheduledTaskTrigger -AtLogOn
        $set = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
                 -StartWhenAvailable -DontStopOnIdleEnd -ExecutionTimeLimit (New-TimeSpan -Hours 8760)
        Register-ScheduledTask -TaskName 'TailscaleRemoteAgent' -Action $act -Trigger $trg `
                               -Settings $set -RunLevel Highest -Force | Out-Null
        Say '  [OK] 开机任务 TailscaleRemoteAgent 已注册' 'Green'
    } catch {
        Say "  [!] 计划任务注册失败: $($_.Exception.Message)" 'Yellow'
    }
    Get-Process powershell -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $ps } | Out-Null
    Start-Process $ps -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
        '-WindowStyle', 'Hidden', '-File', $agent) -WindowStyle Hidden
    Start-Sleep -Seconds 3
    Say '  [OK] 消息通道已启动(右下角弹窗 + 双向传文件)' 'Green'
}

# ============================================================ 7. 回传本机信息
function Report([string]$dir) {
    Banner '7/7 回传本机信息到控制端'
    $ips = @()
    try {
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                 Where-Object { $_.IPAddress -like '100.*' } | ForEach-Object { $_.IPAddress })
    } catch { }
    if (-not $ips) { try { $ips = @((& $TSExe ip -4 2>$null | Select-Object -First 1)) } catch { } }

    $L = @()
    $L += '=========== Tailscale 远程工具箱 · 被控端部署回执 ==========='
    $L += "主机名        : $env:COMPUTERNAME"
    $L += "用户名        : $env:USERNAME"
    $L += "Tailscale IP  : $($ips -join ', ')"
    $L += "SSH 连接命令  : ssh $env:USERNAME@$($ips | Select-Object -First 1)"
    $L += "房间号        : $Room"
    $L += "中继地址      : $Relay"
    $L += ''
    $L += "安装目录(隐)  : $dir"
    $L += "  脚本        : $(Join-Path $dir 'notify.ps1')"
    $L += "  配置        : $(Join-Path $dir 'agent.ini')"
    $L += "  卸载        : $(Join-Path $dir 'clean.ps1')"
    $L += "开机任务      : 计划任务 TailscaleRemoteAgent (登录时启动)"
    $L += ''
    if ($SourceDir) { $L += "引导脚本来源  : $SourceDir (已清理)" }
    $L += '部署时间      : ' + (Get-Date -Format 'yyyy-MM-dd HH:mm:ss')
    $L += "版本          : $VER"
    $L += '============================================================'
    $txt = ($L -join "`r`n")

    $file = Join-Path $dir ("info-" + $env:COMPUTERNAME + ".txt")
    try { [IO.File]::WriteAllText($file, $txt, [Text.Encoding]::UTF8) } catch { }

    # ① 走中继(永远可用, 网页控制台立即能看到)
    $ok1 = $false
    if (-not $DryRun) {
        $r = Http-Json "$Relay/api/send" 'POST' @{ room = $Room; side = 'pc'; kind = 'sys'; body = $txt }
        if ($r -and $r.ok) { $ok1 = $true }
    }
    if ($ok1) { Say '  [OK] 中继回传成功: 网页控制台立刻可见' 'Green' }
    else      { Say '  [!] 中继回传未成功(网络不通也不影响 ssh 远程连接)' 'Yellow' }

    # ② 走 scp 到控制端桌面(需要控制端开着 sshd)
    $ok2 = $false
    if ($ControlHost -and $ControlUser -and -not $DryRun) {
        $scp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'
        if (-not (Test-Path $scp)) { $scp = 'scp' }
        try {
            $dst = "$ControlUser@${ControlHost}:$ControlPath"
            & $scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=NUL -o BatchMode=yes `
                   -o ConnectTimeout=15 $file $dst 2>&1 | Out-Host
            if ($LASTEXITCODE -eq 0) { $ok2 = $true }
        } catch { }
    }
    if ($ok2) { Say "  [OK] scp 回传到 ${ControlUser}@${ControlHost}:$ControlPath" 'Green' }
    elseif ($ControlHost) { Say '  [!] scp 回传失败(控制端需开着 sshd), 信息已走中继' 'Yellow' }
    Say "  [OK] 回执已存: $file" 'Green'
}

# ============================================================ 8. 清理安装残留
function Clean-Source {
    if ($KeepSource) { Say '  [skip] -KeepSource, 保留下载目录的文件' 'Gray'; return }
    Banner '清理安装残留'
    if ($DryRun) { Say '  [DryRun] 将删除下载目录里的 zip 与引导脚本'; return }
    if ($SourceDir -and (Test-Path $SourceDir)) {
        foreach ($n in @('setup.zip', 'launcher.bat', 'launcher.cmd')) {
            $f = Join-Path $SourceDir $n
            if (Test-Path $f) { Remove-Item -LiteralPath $f -Force -ErrorAction SilentlyContinue }
        }
        Say "  [OK] 已清理下载目录: $SourceDir" 'Green'
    }
    if (-not $NoRelocate -and $ScriptDir -ne $InstallDir -and (Test-Path $ScriptDir)) {
        Start-Sleep -Seconds 1
        Remove-Item -LiteralPath $ScriptDir -Recurse -Force -ErrorAction SilentlyContinue
        Say '  [OK] 已清理解压临时目录' 'Green'
    }
}

# ============================================================ 主流程
# ★ 每一步独立容错: 对方只点一次, 任何单步小失败都不能把整条链打断。
#   失败的步会列在最后, 控制端看回执就知道还差什么。
$finalDir = $ScriptDir
$script:Failed = @()
function Step([string]$name, [scriptblock]$work) {
    try { & $work }
    catch {
        $script:Failed += $name
        Write-Host "  [X] $name 失败: $($_.Exception.Message)" -ForegroundColor Red
    }
}
Step '安装Tailscale'   { Install-Tailscale }
Step '加入网络'        { Join-Tailnet }
Step '开启SSH'         { Enable-SSHServer }
Step '部署公钥'        { Install-PubKeys }
Step '搬迁安装目录'    { $script:finalDir = Move-ToInstallDir }
Step '开机自启'        { Enable-AutoStart $script:finalDir }
Step '回传信息'        { Report $script:finalDir }
Step '清理残留'        { Clean-Source }
if ($script:Failed.Count) {
    Write-Host ''
    Write-Host ('  未完成的步骤: ' + ($script:Failed -join ' / ')) -ForegroundColor Yellow
    Write-Host '  可重跑引导脚本, 已完成的步骤会自动跳过。' -ForegroundColor Yellow
}

$ip = ''
try { $ip = (& $TSExe ip -4 2>$null | Select-Object -First 1) } catch { }
if (-not $ip) { $ip = '100.x.x.x' }
Write-Host ''
Write-Host '================================================================' -ForegroundColor Green
Write-Host '  部署完成。把这【最后一行】复制发给控制端:' -ForegroundColor White
Write-Host ''
Write-Host ("    ssh " + $env:USERNAME + "@" + $ip) -ForegroundColor Green
Write-Host ''
Write-Host "  安装目录: $finalDir" -ForegroundColor Gray
Write-Host '================================================================' -ForegroundColor Green
Write-Host ''
Start-Sleep -Seconds 12
