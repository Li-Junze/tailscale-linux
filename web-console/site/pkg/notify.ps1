# notify.ps1 -- 被控端「右下角弹窗 + 双向传文件 + 远程指令」客户端
# ---------------------------------------------------------------------------
# 零依赖: Windows 自带 PowerShell 5.1 + .NET/WinForms。不需要 Python/Node。
#   · 网页发来的文字 -> 本机右下角弹窗, 可直接打字回复
#   · 文件双向: 网页发的自动落盘到收件箱; 丢进发件箱自动回传网页
#   · 远程指令: 只接受白名单(cleanup / info / restart), 不做通用 shell
# ---------------------------------------------------------------------------
param(
    [string]$Room  = '',
    [string]$Relay = 'https://ts-remote-web.pages.dev',
    [string]$Inbox  = '',
    [string]$Outbox = '',
    [switch]$Quiet,                 # 不弹托盘气泡, 只弹消息窗
    [switch]$Once,                  # 只跑一轮就退出(自检用)
    [string]$TestToast = ''         # 只弹一个测试弹窗, 6 秒后退出(自检用)
)

$ErrorActionPreference = 'Stop'
$AGENT_VER = '1.1-web'
$script:Quit = $false
$script:LastId = 0
$script:Presence = @{}
$script:Sent = @{}
$script:Toast = $null
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition

# ---- 没给房间号就从 agent.ini 读(开机自启时就是靠这个) ----
function Load-Ini {
    $ini = Join-Path $ScriptDir 'agent.ini'
    if (-not (Test-Path $ini)) { return @{} }
    $h = @{}
    foreach ($ln in @(Get-Content $ini -ErrorAction SilentlyContinue)) {
        if ($ln -match '^\s*([A-Za-z_]+)\s*=\s*(.*)$') { $h[$Matches[1].ToLower()] = $Matches[2].Trim() }
    }
    return $h
}
$iniCfg = Load-Ini
if (-not $Room)  { $Room  = [string]$iniCfg['room'] }
if ($Relay -eq 'https://ts-remote-web.pages.dev' -and $iniCfg['relay']) { $Relay = [string]$iniCfg['relay'] }
# 第三级回退: ini 丢了(比如被清理误删)就从 install.ps1 的烘焙参数里抠出房间号,
# 保证 agent 永远能自举, 不会因为缺一个配置文件就彻底失联。
if (-not $Room) {
    $ip = Join-Path $ScriptDir 'install.ps1'
    if (Test-Path $ip) {
        $t = Get-Content $ip -Raw -ErrorAction SilentlyContinue
        if ($t -and $t -match "\[string\]\`$Room\s*=\s*'([^']+)'") { $Room = $Matches[1] }
        if ($Relay -eq 'https://ts-remote-web.pages.dev' -and
            $t -and $t -match "\[string\]\`$Relay\s*=\s*'([^']+)'") { $Relay = $Matches[1] }
    }
}

try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}

# ---- 单例: 一台机器只允许一个 agent, 否则每条指令都会被重复执行 N 遍 ----
$created = $false
try {
    $script:Mutex = New-Object Threading.Mutex($true, 'Global\TailscaleRemoteAgent', [ref]$created)
    if (-not $created) { exit 0 }
} catch { $created = $true }

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# ---------------------------------------------------------------- 目录
$root = Join-Path $env:USERPROFILE 'TailscaleRemote'
if (-not $Inbox)  { $Inbox  = Join-Path $root 'Inbox' }
if (-not $Outbox) { $Outbox = Join-Path $root 'Outbox' }
$sentDir = Join-Path $Outbox 'sent'
foreach ($d in @($root, $Inbox, $Outbox, $sentDir)) {
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d -Force | Out-Null }
}
$Relay = $Relay.TrimEnd('/')

# ---------------------------------------------------------------- HTTP
function New-Req([string]$url, [string]$method) {
    $r = [Net.HttpWebRequest]::Create($url)
    $r.Method = $method; $r.Timeout = 20000; $r.ReadWriteTimeout = 60000
    $r.UserAgent = "ts-remote-agent/$AGENT_VER"
    return $r
}
function Http-Json([string]$url, [string]$method = 'GET', $obj = $null) {
    $req = New-Req $url $method
    if ($null -ne $obj) {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($obj | ConvertTo-Json -Compress -Depth 6))
        $req.ContentType = 'application/json; charset=utf-8'
        $req.ContentLength = $bytes.Length
        $s = $req.GetRequestStream(); $s.Write($bytes, 0, $bytes.Length); $s.Close()
    }
    $resp = $req.GetResponse()
    $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
    $txt = $sr.ReadToEnd(); $sr.Close(); $resp.Close()
    return ($txt | ConvertFrom-Json)
}
function Http-PutBytes([string]$url, [byte[]]$bytes) {
    $req = New-Req $url 'PUT'
    $req.ContentType = 'application/octet-stream'
    $req.ContentLength = $bytes.Length
    $s = $req.GetRequestStream(); $s.Write($bytes, 0, $bytes.Length); $s.Close()
    $resp = $req.GetResponse()
    $sr = New-Object IO.StreamReader($resp.GetResponseStream(), [Text.Encoding]::UTF8)
    $txt = $sr.ReadToEnd(); $sr.Close(); $resp.Close()
    return ($txt | ConvertFrom-Json)
}
function Http-GetBytes([string]$url) {
    $req = New-Req $url 'GET'
    $resp = $req.GetResponse()
    $ms = New-Object IO.MemoryStream
    $resp.GetResponseStream().CopyTo($ms)
    $resp.Close()
    return $ms.ToArray()
}

# ---------------------------------------------------------------- 消息
function Send-Text([string]$text, [string]$kind = 'text') {
    if (-not $text) { return }
    try { Http-Json "$Relay/api/send" 'POST' @{ room = $Room; side = 'pc'; kind = $kind; body = $text } | Out-Null }
    catch { }
}
function Send-Beat() {
    $ips = @()
    try {
        $ips = @(Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                 Where-Object { $_.IPAddress -like '100.*' } | ForEach-Object { $_.IPAddress })
    } catch { }
    $info = @{
        host = $env:COMPUTERNAME
        user = $env:USERNAME
        ip   = ($ips -join ', ')
        ver  = $AGENT_VER
        path = $ScriptDir
        room = $Room
    }
    try { Http-Json "$Relay/api/beat" 'POST' @{ room = $Room; side = 'pc'; info = $info } | Out-Null }
    catch { }
}
function Upload-File([string]$path) {
    $fi = Get-Item -LiteralPath $path
    $size = $fi.Length; $name = $fi.Name; $CH = 1180000
    $b = Http-Json "$Relay/api/upload/begin" 'POST' @{ room = $Room; side = 'pc'; name = $name; size = $size }
    if (-not $b.ok) { throw "begin failed: $($b.error)" }
    $fs = [IO.File]::OpenRead($path)
    try {
        $buf = New-Object byte[] $CH; $i = 0
        while ($true) {
            $read = $fs.Read($buf, 0, $CH)
            if ($read -le 0) { break }
            $part = New-Object byte[] $read
            [Array]::Copy($buf, 0, $part, 0, $read)
            $r = Http-PutBytes "$Relay/api/upload/chunk?fileId=$($b.fileId)&i=$i" $part
            if (-not $r.ok) { throw "chunk$i failed" }
            $i++
        }
    } finally { $fs.Close() }
    $payload = @{ fileId = $b.fileId; name = $name; size = $size } | ConvertTo-Json -Compress
    Http-Json "$Relay/api/send" 'POST' @{ room = $Room; side = 'pc'; kind = 'file'; body = $payload } | Out-Null
    return @{ ok = $true; name = $name; size = $size }
}
function Download-File([string]$fileId, [string]$name, [int]$size, [int]$chunks) {
    $safe = $name -replace '[\\/:*?"<>|]', '_'
    $dest = Join-Path $Inbox $safe
    $tmp  = $dest + '.part'
    $fs = [IO.File]::Create($tmp)
    try {
        for ($i = 0; $i -lt $chunks; $i++) {
            $bytes = Http-GetBytes "$Relay/api/download?fileId=$fileId&i=$i"
            $fs.Write($bytes, 0, $bytes.Length)
        }
    } finally { $fs.Close() }
    if (Test-Path $dest) {
        $dest = Join-Path $Inbox ("{0}_{1}{2}" -f [IO.Path]::GetFileNameWithoutExtension($safe),
                                  (Get-Date -Format 'HHmmss'), [IO.Path]::GetExtension($safe))
    }
    Move-Item -LiteralPath $tmp -Destination $dest -Force
    return $dest
}

# ---------------------------------------------------------------- 远程指令(白名单)
function Invoke-RemoteCmd([string]$body) {
    $o = $null
    try { $o = ($body | ConvertFrom-Json) } catch { return }
    $c = [string]$o.cmd
    switch ($c) {
        'info' {
            $ips = @()
            try { $ips = @(Get-NetIPAddress -AddressFamily IPv4 -EA SilentlyContinue |
                     Where-Object { $_.IPAddress -like '100.*' } | ForEach-Object { $_.IPAddress }) } catch { }
            $t = Get-ScheduledTask -TaskName 'TailscaleRemoteAgent' -ErrorAction SilentlyContinue
            $tstr = if ($t) { 'yes' } else { 'no' }
            Send-Text ("[info] host=$env:COMPUTERNAME user=$env:USERNAME ip=" + ($ips -join ',') +
                       " path=$ScriptDir autostart=$tstr") 'sys'
        }
        'restart' {
            Send-Text '[sys] agent 即将重启' 'sys'
            $script:Quit = 'restart'
        }
        'cleanup' { Do-Cleanup ([bool]($o.keepTailscale)) }
        default   { Send-Text "[sys] 忽略未知指令: $c" 'sys' }
    }
}

# ---------------------------------------------------------------- 卸载(网页一键清除)
function Do-Cleanup([bool]$keepTs = $false) {
    Send-Text '[sys] 收到清除指令, 开始卸载 ...' 'sys'
    $log = @()
    try {
        Unregister-ScheduledTask -TaskName 'TailscaleRemoteAgent' -Confirm:$false -EA SilentlyContinue
        $log += '[OK] 已移除开机任务 TailscaleRemoteAgent'
    } catch { $log += '[!] 移除开机任务失败' }
    Get-Process powershell -EA SilentlyContinue | Where-Object {
        $_.Path -like '*powershell*' -and $_.Id -ne $PID
    } | Out-Null
    try {
        $ak = 'C:\ProgramData\ssh\administrators_authorized_keys'
        if (Test-Path $ak) {
            $pub = ''
            $pf = Join-Path $ScriptDir 'keys\control.pub'
            if (Test-Path $pf) { $pub = ((Get-Content $pf -Raw) -as [string]).Trim() }
            if ($pub) {
                $keep = @(Get-Content $ak | Where-Object { $_.Trim() -ne $pub })
                Set-Content -Path $ak -Value $keep -Encoding UTF8
            }
            $log += '[OK] 已撤销控制端公钥'
        }
    } catch { $log += '[!] 撤销公钥失败' }
    if ($keepTs) {
        $log += '[--] 按指令保留 Tailscale 入网(只是不再被本工具管理)'
    } else {
        try {
            $ts = 'C:\Program Files\Tailscale\tailscale.exe'
            if (Test-Path $ts) { & $ts logout 2>&1 | Out-Null; $log += '[OK] 已退出 Tailscale 网络' }
        } catch { $log += '[!] 退出 tailnet 失败' }
    }
    # 自己删不掉自己所在的目录, 交给一个 detached 的 powershell 延迟删
    try {
        $ps = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
        $cmd = "Start-Sleep -Seconds 6; Remove-Item -LiteralPath '$ScriptDir' -Recurse -Force -ErrorAction SilentlyContinue; Remove-Item -LiteralPath '$root' -Recurse -Force -ErrorAction SilentlyContinue"
        Start-Process $ps -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass',
                                         '-WindowStyle', 'Hidden', '-Command', $cmd) -WindowStyle Hidden
        $log += '[OK] 已安排删除安装目录(6 秒后生效)'
    } catch { $log += '[!] 安排删除失败, 需手动删 ' + $ScriptDir }
    Send-Text ("[sys] 清除完成:`r`n" + ($log -join "`r`n")) 'sys'
    $script:Quit = $true
}

# ---------------------------------------------------------------- 右下角弹窗
function New-Toast([string]$text, [string]$from) {
    if ($script:Toast -and -not $script:Toast.IsDisposed) {
        $script:Toast.Controls['body'].Text = $script:Toast.Controls['body'].Text + "`r`n—————`r`n" + $text
        $script:Toast.BringToFront(); $script:Toast.Tag = [DateTime]::Now
        return
    }
    $wa = [Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $f = New-Object Windows.Forms.Form
    $f.FormBorderStyle = 'None'; $f.StartPosition = 'Manual'
    $f.ShowInTaskbar = $false; $f.TopMost = $true
    $f.BackColor = [Drawing.Color]::FromArgb(11, 18, 32)
    $f.Size = New-Object Drawing.Size(430, 240)

    $title = New-Object Windows.Forms.Label
    $title.Text = "new message - $from"
    $title.ForeColor = [Drawing.Color]::FromArgb(147, 197, 253)
    $title.Font = New-Object Drawing.Font('Microsoft YaHei', 10, [Drawing.FontStyle]::Bold)
    $title.Location = New-Object Drawing.Point(18, 14); $title.Size = New-Object Drawing.Size(394, 26)
    $f.Controls.Add($title)

    $body = New-Object Windows.Forms.Label
    $body.Name = 'body'; $body.Text = $text
    $body.ForeColor = [Drawing.Color]::White
    $body.Font = New-Object Drawing.Font('Microsoft YaHei', 12)
    $body.Location = New-Object Drawing.Point(18, 46); $body.Size = New-Object Drawing.Size(394, 96)
    $f.Controls.Add($body)

    $tb = New-Object Windows.Forms.TextBox
    $tb.Name = 'reply'
    $tb.Font = New-Object Drawing.Font('Microsoft YaHei', 11)
    $tb.Location = New-Object Drawing.Point(18, 150); $tb.Size = New-Object Drawing.Size(394, 30)
    $tb.BackColor = [Drawing.Color]::FromArgb(30, 41, 59)
    $tb.ForeColor = [Drawing.Color]::White; $tb.BorderStyle = 'FixedSingle'
    $f.Controls.Add($tb)

    $ok = New-Object Windows.Forms.Button
    $ok.Text = 'reply'
    $ok.Font = New-Object Drawing.Font('Microsoft YaHei', 10, [Drawing.FontStyle]::Bold)
    $ok.FlatStyle = 'Flat'
    $ok.BackColor = [Drawing.Color]::FromArgb(37, 99, 235)
    $ok.ForeColor = [Drawing.Color]::White
    $ok.Location = New-Object Drawing.Point(18, 188); $ok.Size = New-Object Drawing.Size(190, 36)
    $ok.Add_Click({
        $t = $f.Controls['reply'].Text.Trim()
        if ($t) { Send-Text $t 'text' }
        $f.Close()
    })
    $f.Controls.Add($ok)

    $no = New-Object Windows.Forms.Button
    $no.Text = 'OK'
    $no.Font = New-Object Drawing.Font('Microsoft YaHei', 10)
    $no.FlatStyle = 'Flat'
    $no.BackColor = [Drawing.Color]::FromArgb(51, 65, 85)
    $no.ForeColor = [Drawing.Color]::White
    $no.Location = New-Object Drawing.Point(222, 188); $no.Size = New-Object Drawing.Size(190, 36)
    $no.Add_Click({ $f.Close() })
    $f.Controls.Add($no)

    $f.Location = New-Object Drawing.Point(($wa.Right - 450), ($wa.Bottom - 262))
    $f.Tag = [DateTime]::Now
    $f.Add_Shown({ $f.Controls['reply'].Focus() | Out-Null })
    $script:Toast = $f
    $f.Show()
    $tb.Add_KeyDown({ if ($_.KeyCode -eq 'Enter') { $ok.PerformClick() } })
}
function Close-StaleToast() {
    if ($script:Toast -and -not $script:Toast.IsDisposed) {
        if ((([DateTime]::Now) - [DateTime]$script:Toast.Tag).TotalSeconds -gt 25) { $script:Toast.Close() }
    }
}

# ---------------------------------------------------------------- 发件箱
function Scan-Outbox() {
    $files = @(Get-ChildItem -LiteralPath $Outbox -File -ErrorAction SilentlyContinue |
               Where-Object { $_.Extension -ne '.part' })
    foreach ($f in $files) {
        if ($script:Sent.ContainsKey($f.FullName) -and $script:Sent[$f.FullName] -eq $f.LastWriteTimeUtc) { continue }
        try {
            Upload-File $f.FullName | Out-Null
            $script:Sent[$f.FullName] = $f.LastWriteTimeUtc
            Send-Text ("sent to web: " + $f.Name) 'sys'
            Move-Item -LiteralPath $f.FullName -Destination (Join-Path $sentDir $f.Name) -Force
        } catch { Send-Text ("send failed " + $f.Name + ": " + $_.Exception.Message) 'sys' }
    }
}

# ---------------------------------------------------------------- 处理消息
function Handle-Pull($d) {
    foreach ($m in $d.msgs) {
        if ($m.id -le $script:LastId) { continue }
        $script:LastId = [int]$m.id
        if ($m.side -eq 'pc') { continue }
        if ($m.kind -eq 'text') {
            if (-not $Quiet) { New-Toast $m.body 'web console' }
            [Windows.Forms.Application]::DoEvents()
        }
        elseif ($m.kind -eq 'file') {
            try {
                $meta = $m.body | ConvertFrom-Json
                $fi = Http-Json "$Relay/api/fileinfo?fileId=$($meta.fileId)" 'GET'
                if ($fi.ok) {
                    $p = Download-File $meta.fileId $fi.file.name ([int]$fi.file.size) ([int]$fi.file.chunks)
                    Send-Text ("saved to inbox: " + (Split-Path $p -Leaf)) 'sys'
                    if (-not $Quiet) { New-Toast ("file saved`r`n" + (Split-Path $p -Leaf)) 'web console' }
                }
            } catch { Send-Text ("recv failed: " + $_.Exception.Message) 'sys' }
        }
        elseif ($m.kind -eq 'cmd') { Invoke-RemoteCmd $m.body }
    }
}

# ---------------------------------------------------------------- 托盘
function New-Tray() {
    $ni = New-Object Windows.Forms.NotifyIcon
    $ni.Icon = [Drawing.SystemIcons]::Information
    $ni.Text = "Tailscale remote (room $Room)"
    $ni.Visible = $true
    $menu = New-Object Windows.Forms.ContextMenuStrip
    $mIn = $menu.Items.Add("open inbox")
    $mIn.Add_Click({ Start-Process explorer.exe $Inbox })
    $mOut = $menu.Items.Add("open outbox")
    $mOut.Add_Click({ Start-Process explorer.exe $Outbox })
    $mSend = $menu.Items.Add("send file to web...")
    $mSend.Add_Click({
        $dlg = New-Object Windows.Forms.OpenFileDialog
        if ($dlg.ShowDialog() -eq 'OK') {
            try { Upload-File $dlg.FileName | Out-Null; Send-Text ("sent: " + (Split-Path $dlg.FileName -Leaf)) 'sys' }
            catch { Send-Text ("send failed: " + $_.Exception.Message) 'sys' }
        }
    })
    [void]$menu.Items.Add('-')
    $mQuit = $menu.Items.Add("quit")
    $mQuit.Add_Click({ $script:Quit = $true })
    $ni.ContextMenuStrip = $menu
    $ni.Add_MouseDoubleClick({ Start-Process explorer.exe $Inbox })
    return $ni
}

# ---------------------------------------------------------------- 主流程
if (-not $Room) { exit 1 }
$Room = ($Room -as [string]).Trim()
if (-not $Room) { exit 1 }

if ($TestToast) {
    New-Toast $TestToast 'self test'
    $t0 = Get-Date
    while (((Get-Date) - $t0).TotalSeconds -lt 6) {
        [Windows.Forms.Application]::DoEvents(); Start-Sleep -Milliseconds 120
    }
    if ($script:Toast -and -not $script:Toast.IsDisposed) { $script:Toast.Close() }
    exit 0
}

Send-Beat
if ($Once) {
    $d = Http-Json "$Relay/api/pull?room=$Room&after=0" 'GET'
    Write-Host "room=$Room  msgs=$($d.msgs.Count)"
    exit 0
}

# ★ 先把游标对齐到最新: 否则每次重启都会把历史指令重放一遍
#   (比如上次下发过的 cleanup, 会让 agent 刚起来就把自己卸载掉)
try {
    $h = Http-Json "$Relay/api/pull?room=$Room&after=0" 'GET'
    foreach ($m in @($h.msgs)) { if ([int]$m.id -gt $script:LastId) { $script:LastId = [int]$m.id } }
} catch { }

$tray = New-Tray
$nextBeat = [DateTime]::MinValue
$nextScan = [DateTime]::MinValue
$nextPull = [DateTime]::MinValue

while (-not $script:Quit) {
    [Windows.Forms.Application]::DoEvents()
    $now = [DateTime]::Now
    if ($now -ge $nextPull) {
        $nextPull = $now.AddSeconds(2)
        try { $d = Http-Json "$Relay/api/pull?room=$Room&after=$script:LastId" 'GET'; if ($d.ok) { Handle-Pull $d } } catch { }
    }
    if ($now -ge $nextBeat) { $nextBeat = $now.AddSeconds(15); Send-Beat }
    if ($now -ge $nextScan) { $nextScan = $now.AddSeconds(4); Scan-Outbox }
    Close-StaleToast
    Start-Sleep -Milliseconds 150
}
try { $tray.Visible = $false; $tray.Dispose() } catch {}
if ($script:Quit -eq 'restart') {
    Start-Process (Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe') `
        -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-WindowStyle', 'Hidden',
                        '-File', $MyInvocation.MyCommand.Definition) -WindowStyle Hidden
}
