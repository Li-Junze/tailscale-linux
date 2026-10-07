#Requires -Version 5.1
<#
  ts-remote bridge —— 控制端本机桥接

  浏览器自己不能 SSH, 所以这个脚本跑在【你自己电脑】上:
    1) 读 ~/.ssh/config, 列出你已经配好的主机
    2) 并发探测现在连不连得上(免密, 每台最多 14 秒)
    3) 把清单上报到网页控制台
    4) 网页下发任务(装 agent / 探测 / 卸载) -> 这里用 ssh/scp 去干 -> 结果回传网页

  用法:
    powershell -NoProfile -ExecutionPolicy Bypass -File bridge.ps1 -Token <网页给的>
    -Once   只跑一轮就退出(自检用)

  它不写注册表、不改系统设置、不开机自启。关掉窗口即停。

  ★ 两个大坑(都踩过, 别改回去):
    1) 远程命令里不能出现 >  |  & 这类字符, Windows 的 ssh 会直接报
       "系统找不到指定的路径" 且 rc=1。所以部署一律: 先 scp 上传脚本,
       再 ssh 执行一句没有元字符的命令(如: sh tsr-boot.sh)。
    2) ssh 要用 Windows 自带 OpenSSH(C:\Windows\System32\OpenSSH\ssh.exe)。
       Get-Command 常常先命中 PortableGit 的 ssh, 非交互启动时不输出任何东西。
#>
param(
    [string]$Token    = '',
    [string]$Relay    = 'https://ts-remote-web.pages.dev',
    [int]   $Interval = 5,
    [switch]$Once,
    [switch]$Debug
)

$ErrorActionPreference = 'Continue'
try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}
$Relay = $Relay.TrimEnd('/')
$tmp   = Join-Path $env:TEMP 'tsr-bridge'
if (-not (Test-Path $tmp)) { New-Item -ItemType Directory -Path $tmp -Force | Out-Null }

function Say($s, $c = 'Gray') { Write-Host $s -ForegroundColor $c }

# ---- ssh / scp 可执行文件 ----
$winSsh = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$winScp = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'
if (Test-Path $winSsh) { $SSH = $winSsh; $SCP = $winScp }
else {
    try { $c = Get-Command ssh -ErrorAction SilentlyContinue; if ($c) { $SSH = $c.Source } } catch {}
    try { $c2 = Get-Command scp -ErrorAction SilentlyContinue; if ($c2) { $SCP = $c2.Source } } catch {}
}
if (-not $SSH) { Say '[X] 没找到 ssh（Windows 可选功能里装一下 OpenSSH 客户端）' 'Red'; exit 1 }

function Http($url, $method = 'GET', $body = $null) {
    try {
        if ($method -eq 'GET') { return Invoke-RestMethod -Uri $url -Method GET -TimeoutSec 20 }
        $bytes = [Text.Encoding]::UTF8.GetBytes($body)
        return Invoke-RestMethod -Uri $url -Method POST -ContentType 'application/json' -Body $bytes -TimeoutSec 25
    } catch { return $null }
}

function SshArgs($h) {
    $a = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
           '-o', 'StrictHostKeyChecking=accept-new', '-o', 'LogLevel=ERROR')
    if ($h.port) { $a += @('-p', [string]$h.port) }
    return $a
}

# 同步跑一条【不带 shell 元字符】的命令
function SshRun($h, $cmd) {
    $a = SshArgs $h
    $out = & $SSH @a $h.alias $cmd 2>$null
    if ($LASTEXITCODE -ne 0) { return '' }
    return ($out -join "`n")
}

function ScpUp($h, $local, $remote) {
    $a = SshArgs $h
    & $SCP @a $local "$($h.alias):$remote" 2>$null | Out-Null
    return ($LASTEXITCODE -eq 0)
}

# ---------------------------------------------------------------- ssh 配置解析
function Get-SshHosts {
    $cfg = Join-Path $env:USERPROFILE '.ssh\config'
    if (-not (Test-Path $cfg)) { return @() }
    $out = @()
    $cur = $null
    foreach ($line in [IO.File]::ReadAllLines($cfg)) {
        $raw = [string]$line
        if ($raw -match '^\s*#') { continue }
        if ($raw -match '^\s*$') { continue }
        if ($raw -match '^\s') {                       # 缩进 = 属性行(必须用原始行判断)
            if (-not $cur) { continue }
            if     ($raw -match '^\s*HostName\s+(\S+)') { $cur.host = $matches[1] }
            elseif ($raw -match '^\s*User\s+(\S+)')     { $cur.user = $matches[1] }
            elseif ($raw -match '^\s*Port\s+(\S+)')     { $cur.port = $matches[1] }
            continue
        }
        if ($raw -match '^Host\s+(.+)$') {
            if ($cur) { $out += $cur }
            $names = $matches[1].Trim()
            if ($names -match '[*?]') { $cur = $null; continue }
            $first = ($names -split '\s+')[0]
            $cur = @{ alias = $first; host = $first; user = ''; port = '' }
        }
    }
    if ($cur) { $out += $cur }
    $seen = @{}; $res = @()
    foreach ($h in $out) { if (-not $seen.ContainsKey($h.alias)) { $seen[$h.alias] = 1; $res += $h } }
    return $res
}

# ---------------------------------------------------------------- 探测(并发 + 硬超时)
function SshProbeProc([string]$alias, [string]$port) {
    $tag = [guid]::NewGuid().ToString('N')
    $outF = Join-Path $tmp ('o_' + $tag + '.txt')
    $errF = Join-Path $tmp ('e_' + $tag + '.txt')
    $argList = @('-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
                 '-o', 'StrictHostKeyChecking=accept-new', '-o', 'LogLevel=ERROR')
    if ($port) { $argList += @('-p', [string]$port) }
    # 远程命令保持极简: 不能带 > | & 否则 Windows ssh 直接失败
    $argList += @($alias, 'echo __OK__; uname -s; hostname')
    $p = Start-Process -FilePath $SSH -ArgumentList $argList -NoNewWindow -PassThru `
                       -RedirectStandardOutput $outF -RedirectStandardError $errF
    return @{ p = $p; out = $outF; err = $errF }
}

function Probe-All($list) {
    if (-not $list -or $list.Count -eq 0) { return @() }
    $procs = @()
    foreach ($h in $list) {
        try { $procs += ,@($h, (SshProbeProc $h.alias $h.port)) }
        catch { $procs += ,@($h, $null) }
    }
    $res = @()
    foreach ($pair in $procs) {
        $h = $pair[0]; $pr = $pair[1]; $r = ''
        if ($pr) {
            try { Wait-Process -Id $pr.p.Id -Timeout 14 -ErrorAction Stop } catch {
                try { Stop-Process -Id $pr.p.Id -Force -ErrorAction SilentlyContinue } catch {}
            }
            if (Test-Path $pr.out) { $r = [string](Get-Content $pr.out -Raw -ErrorAction SilentlyContinue) }
            Remove-Item $pr.out, $pr.err -Force -ErrorAction SilentlyContinue
        }
        if ($r -and $r -match '__OK__') {
            $os = ''
            if ($r -match 'Linux') { $os = 'linux' } elseif ($r -match 'Darwin') { $os = 'mac' } else { $os = 'win' }
            $hn = ($r -split "`n" | Where-Object { $_ -and $_ -notmatch '__OK__' -and $_ -notmatch 'Linux|Darwin' } | Select-Object -First 1)
            $h.ok = $true; $h.os = $os; $h.note = if ($hn) { [string]$hn } else { '' }
        } else {
            $h.ok = $false; $h.os = ''
            $h.note = '连不上(要密码/没开机/没配置)'
            if ($r) {
                $s = ($r -replace '\s+', ' ').Trim()
                $h.note += ' [' + $s.Substring(0, [Math]::Min(70, $s.Length)) + ']'
            }
            if ($Debug) { Say ('     debug ' + $h.alias + ' -> ' + $h.note) 'DarkGray' }
        }
        $res += $h
    }
    return $res
}

# ---------------------------------------------------------------- 部署
function Get-Pkg($name) {
    $dest = Join-Path $tmp $name
    try {
        Invoke-WebRequest -Uri "$Relay/pkg/$name?t=$Token" -OutFile $dest -TimeoutSec 30 -UseBasicParsing
        if (Test-Path $dest) { return $dest }
    } catch {}
    return $null
}

function Write-Text($path, $text) {          # UTF-8 无 BOM, LF 换行
    $enc = New-Object Text.UTF8Encoding($false)
    [IO.File]::WriteAllText($path, $text.Replace("`r`n", "`n"), $enc)
}

function Do-Deploy($h, $args) {
    $room = [string]$args.room; if (-not $room) { $room = 'lobby' }
    $pub  = [string]$args.pub
    $os   = [string]$h.os; if (-not $os) { $os = 'win' }
    $log  = New-Object System.Collections.ArrayList

    $dt = Http "$Relay/api/tok?t=$Token" 'POST' '{}'
    $tok = ''; if ($dt -and $dt.tok) { $tok = [string]$dt.tok }
    if (-not $tok) { [void]$log.Add('[!] 没拿到设备令牌, 装完可能连不上控制台') }

    if ($os -eq 'linux' -or $os -eq 'mac') {
        $sh = Get-Pkg 'notify.sh'
        if (-not $sh) { return @{ ok = $false; out = '下载 notify.sh 失败' } }
        $boot = Join-Path $tmp 'tsr-boot.sh'
        Write-Text $boot @"
set -e
mkdir -p ~/.local/share/tailscale-remote
mv ~/tsr-notify.sh ~/.local/share/tailscale-remote/notify.sh
chmod +x ~/.local/share/tailscale-remote/notify.sh
printf 'room=$room\nrelay=$Relay\ntoken=$tok\n' > ~/.local/share/tailscale-remote/agent.ini
(crontab -l 2>/dev/null | grep -v tsr-notify; echo '@reboot sleep 20 && sh ~/.local/share/tailscale-remote/notify.sh >/dev/null 2>&1 &') | crontab -
if pgrep -f tailscale-remote/notify.sh > /dev/null 2>&1
then echo ALREADY_RUNNING
else
  nohup sh ~/.local/share/tailscale-remote/notify.sh > /dev/null 2>&1 &
  echo STARTED
fi
sleep 2
echo DEPLOY_DONE
"@
        if (-not (ScpUp $h $sh 'tsr-notify.sh')) { return @{ ok = $false; out = 'scp 上传 notify.sh 失败' } }
        if (-not (ScpUp $h $boot 'tsr-boot.sh')) { return @{ ok = $false; out = 'scp 上传启动脚本失败' } }
        $r = SshRun $h 'sh tsr-boot.sh'
        [void]$log.Add('已上传 notify.sh -> ~/.local/share/tailscale-remote/')
        [void]$log.Add('房间=' + $room + ' 中继=' + $Relay + ' 令牌=' + $(if ($tok) { '已签发' } else { '无' }))
        [void]$log.Add('开机自启: crontab @reboot')
        if ($r -and $r -match 'DEPLOY_DONE') { [void]$log.Add('agent 已在后台跑起来') }
        else { [void]$log.Add('[!] 远端返回: ' + [string]$r) }
        return @{ ok = $true; out = ($log -join "`n") }
    }

    # ---- Windows 目标
    $ps1 = Get-Pkg 'notify.ps1'
    if (-not $ps1) { return @{ ok = $false; out = '下载 notify.ps1 失败' } }
    $boot = Join-Path $tmp 'tsr-boot.ps1'
    Write-Text $boot @"
`$d = Join-Path `$env:LOCALAPPDATA 'TailscaleRemote'
New-Item -ItemType Directory -Force -Path `$d | Out-Null
Move-Item -Force (Join-Path `$HOME 'tsr-notify.ps1') (Join-Path `$d 'notify.ps1') -ErrorAction SilentlyContinue
if (-not (Test-Path (Join-Path `$d 'notify.ps1'))) { Move-Item -Force 'tsr-notify.ps1' (Join-Path `$d 'notify.ps1') -ErrorAction SilentlyContinue }
Set-Content -Path (Join-Path `$d 'agent.ini') -Value "room=$room`r`nrelay=$Relay`r`ntoken=$tok`r`n" -Encoding UTF8
$(if ($pub) { "New-Item -ItemType Directory -Force -Path (Join-Path `$d 'keys') | Out-Null; Set-Content -Path (Join-Path `$d 'keys\control.pub') -Value '$pub' -Encoding ASCII" })
`$ps = Join-Path `$env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
Start-Process `$ps -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',(Join-Path `$d 'notify.ps1')) -WindowStyle Hidden
schtasks /Create /TN 'TailscaleRemoteAgent' /TR "powershell -NoProfile -WindowStyle Hidden -File \`"`$d\notify.ps1\`"" /SC ONLOGON /RL LIMITED /F | Out-Null
Write-Output 'DEPLOY_DONE'
"@
    if (-not (ScpUp $h $ps1 'tsr-notify.ps1')) { return @{ ok = $false; out = 'scp 上传 notify.ps1 失败' } }
    if (-not (ScpUp $h $boot 'tsr-boot.ps1'))  { return @{ ok = $false; out = 'scp 上传启动脚本失败' } }
    $r = SshRun $h 'powershell -NoProfile -ExecutionPolicy Bypass -File tsr-boot.ps1'
    [void]$log.Add('已上传 notify.ps1 -> %LOCALAPPDATA%\TailscaleRemote')
    [void]$log.Add('房间=' + $room + ' 中继=' + $Relay + ' 令牌=' + $(if ($tok) { '已签发' } else { '无' }))
    [void]$log.Add('开机自启: 计划任务 TailscaleRemoteAgent')
    if ($r -and $r -match 'DEPLOY_DONE') { [void]$log.Add('agent 已在后台跑起来') }
    else { [void]$log.Add('[!] 远端返回: ' + [string]$r) }
    return @{ ok = $true; out = ($log -join "`n") }
}

function Do-Remove($h) {
    $os = [string]$h.os; if (-not $os) { $os = 'win' }
    if ($os -eq 'linux' -or $os -eq 'mac') {
        $f = Join-Path $tmp 'tsr-remove.sh'
        Write-Text $f @"
pkill -f tailscale-remote/notify.sh 2>/dev/null
(crontab -l 2>/dev/null | grep -v tsr-notify) | crontab -
rm -rf ~/.local/share/tailscale-remote
rm -f ~/tsr-notify.sh ~/tsr-boot.sh
echo REMOVE_DONE
"@
        ScpUp $h $f 'tsr-remove.sh' | Out-Null
        $r = SshRun $h 'sh tsr-remove.sh'
        return @{ ok = $true; out = ('已停止 agent、清掉 crontab、删除安装目录。' + "`n" + [string]$r) }
    }
    $f = Join-Path $tmp 'tsr-remove.ps1'
    Write-Text $f @"
`$d = Join-Path `$env:LOCALAPPDATA 'TailscaleRemote'
schtasks /Delete /TN 'TailscaleRemoteAgent' /F 2>&1 | Out-Null
Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue |
  Where-Object { `$_.CommandLine -like '*notify.ps1*' } |
  ForEach-Object { Stop-Process -Id `$_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Seconds 2
Remove-Item -LiteralPath `$d -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path `$HOME 'tsr-notify.ps1') -Force -ErrorAction SilentlyContinue
Remove-Item -LiteralPath (Join-Path `$HOME 'tsr-boot.ps1') -Force -ErrorAction SilentlyContinue
Write-Output 'REMOVE_DONE'
"@
    ScpUp $h $f 'tsr-remove.ps1' | Out-Null
    $r = SshRun $h 'powershell -NoProfile -ExecutionPolicy Bypass -File tsr-remove.ps1'
    return @{ ok = $true; out = ('已移除开机任务、停掉进程、删除安装目录。' + "`n" + [string]$r) }
}

# ================================================================ 主循环
if (-not $Token) { Say '[X] 缺少 -Token（网页「已有 SSH 连接」里复制的命令自带）' 'Red'; exit 1 }

Say '============================================' 'Cyan'
Say ' ts-remote 控制端桥接' 'Cyan'
Say (' 中继 : ' + $Relay) 'DarkGray'
Say (' ssh  : ' + $SSH) 'DarkGray'
Say (' token: ' + $Token) 'DarkGray'
Say '============================================' 'Cyan'

$hosts = Get-SshHosts
Say ('ssh 配置里发现 ' + $hosts.Count + ' 台主机，开始并发探测 …') 'Yellow'
$probed = @(Probe-All $hosts)
foreach ($p in $probed) {
    if ($p.ok) { Say ('  [OK] ' + $p.alias + '  ' + $p.user + '@' + $p.host + '  (' + $p.os + ')  ' + $p.note) 'Green' }
    else       { Say ('  [--] ' + $p.alias + '  ' + $p.note) 'DarkGray' }
}

$payload = @{ token = $Token; hosts = @($probed | ForEach-Object {
    @{ alias = $_.alias; user = $_.user; host = $_.host; os = $_.os; ok = [bool]$_.ok; note = $_.note }
}) } | ConvertTo-Json -Depth 6
$r = Http "$Relay/api/bridge?t=$Token" 'POST' $payload
if ($r -and $r.ok) { Say ('上报成功：' + $probed.Count + ' 台') 'Green' }
else { Say '[X] 上报失败，检查网络/token' 'Red' }

if ($Once) { Say '（-Once 模式，退出）' 'DarkGray'; exit 0 }

Say ''; Say '开始处理网页下发的任务（Ctrl+C 停止）…' 'Yellow'
$beat = 0
while ($true) {
    Start-Sleep -Seconds $Interval
    $beat++
    if ($beat % 12 -eq 0) {
        $fresh = @(Probe-All $hosts)
        $pl = @{ token = $Token; hosts = @($fresh | ForEach-Object {
            @{ alias = $_.alias; user = $_.user; host = $_.host; os = $_.os; ok = [bool]$_.ok; note = $_.note }
        }) } | ConvertTo-Json -Depth 6
        Http "$Relay/api/bridge?t=$Token" 'POST' $pl | Out-Null
    }
    $d = Http "$Relay/api/bridge/tasks?token=$Token&t=$Token" 'GET'
    if (-not $d -or -not $d.tasks) { continue }
    foreach ($t in $d.tasks) {
        $h = $null
        foreach ($x in $hosts) { if ($x.alias -eq $t.host) { $h = $x } }
        if (-not $h) {
            Http "$Relay/api/bridge/result?t=$Token" 'POST' (@{ id = $t.id; ok = $false; out = '未知主机 ' + $t.host } | ConvertTo-Json) | Out-Null
            continue
        }
        Say ('执行 ' + $t.type + ' -> ' + $h.alias) 'Cyan'
        if (-not $h.os) {
            $one = @(Probe-All @($h))
            if ($one.Count -gt 0) { $h = $one[0] }
        }
        $res = $null
        switch ($t.type) {
            'deploy' { $res = Do-Deploy $h $t.args }
            'remove' { $res = Do-Remove $h }
            'probe'  { $p = @(Probe-All @($h))[0]; $res = @{ ok = $p.ok; out = ($p.note + '  os=' + $p.os) } }
            default  { $res = @{ ok = $false; out = '不支持的任务 ' + $t.type } }
        }
        $flag = if ($res.ok) { '[OK]' } else { '[!]' }
        Say ('   ' + $flag + ' ' + [string]$res.out) $(if ($res.ok) { 'Green' } else { 'Yellow' })
        Http "$Relay/api/bridge/result?t=$Token" 'POST' (@{ id = $t.id; ok = [bool]$res.ok; out = [string]$res.out } | ConvertTo-Json) | Out-Null
    }
}
