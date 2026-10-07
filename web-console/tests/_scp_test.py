# -*- coding: utf-8 -*-
"""验证桥接的 scp 上传 + ssh 执行链路(不装东西, 传一个临时文件再删掉)"""
import os, subprocess, tempfile

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
code = r'''
$SSH = Join-Path $env:SystemRoot 'System32\OpenSSH\ssh.exe'
$SCP = Join-Path $env:SystemRoot 'System32\OpenSSH\scp.exe'
$f = Join-Path $env:TEMP 'tsr-scp-probe.txt'
Set-Content -Path $f -Value 'tsr-scp-ok' -Encoding ASCII
$a = @('-o','BatchMode=yes','-o','ConnectTimeout=6','-o','StrictHostKeyChecking=accept-new','-o','LogLevel=ERROR')
& $SCP @a $f 'dream:tsr-scp-probe.txt' 2>$null
Write-Host ("scp rc = " + $LASTEXITCODE)
$out = & $SSH @a dream 'type tsr-scp-probe.txt' 2>$null
Write-Host ("read back = [" + ($out -join ' ') + "]")
& $SSH @a dream 'del tsr-scp-probe.txt' 2>$null
Write-Host ("cleanup rc = " + $LASTEXITCODE)
Remove-Item $f -Force -EA SilentlyContinue
'''
r = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True,
                   creationflags=0x08000000, timeout=120)
print((r.stdout or b"").decode("cp936", "replace"))
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("ERR:", err[-400:])
