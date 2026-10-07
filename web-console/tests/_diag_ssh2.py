# -*- coding: utf-8 -*-
"""诊断 Start-Process 方式跑 ssh 并重定向到文件"""
import os, subprocess, tempfile

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
tmp = os.environ.get("TEMP", ".")
code = r'''
$SSH = (Get-Command ssh).Source
Write-Host "SSH = $SSH"
$tmp = $env:TEMP
$outF = Join-Path $tmp "diag_o.txt"
$errF = Join-Path $tmp "diag_e.txt"
Remove-Item $outF,$errF -Force -EA SilentlyContinue
$argList = @('-o','BatchMode=yes','-o','ConnectTimeout=6','-o','StrictHostKeyChecking=accept-new','-o','LogLevel=ERROR')
$argList += @('dream','echo __OK__; uname -s 2>/dev/null; hostname 2>/dev/null')
Write-Host ("args = " + ($argList -join " | "))
$p = Start-Process -FilePath $SSH -ArgumentList $argList -NoNewWindow -PassThru -RedirectStandardOutput $outF -RedirectStandardError $errF
Write-Host ("pid = " + $p.Id)
try { Wait-Process -Id $p.Id -Timeout 15 -ErrorAction Stop; Write-Host "exited normally" }
catch { Write-Host "TIMEOUT -> kill"; try { Stop-Process -Id $p.Id -Force } catch {} }
Start-Sleep -Seconds 1
Write-Host ("out exists = " + (Test-Path $outF))
if (Test-Path $outF) { Write-Host ("OUT = [" + ((Get-Content $outF -Raw) -replace '\s+',' ') + "]") }
if (Test-Path $errF) { Write-Host ("ERR = [" + ((Get-Content $errF -Raw) -replace '\s+',' ').Substring(0,200) + "]") }
'''
r = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True,
                   creationflags=0x08000000, timeout=120)
print((r.stdout or b"").decode("cp936", "replace"))
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("ERR:", err[-400:])
