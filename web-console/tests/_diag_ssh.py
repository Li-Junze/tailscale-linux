# -*- coding: utf-8 -*-
"""诊断: $SSH 解析、HOME/USERPROFILE 环境、Process 方式启动 ssh"""
import os, subprocess

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
code = r'''
$c = Get-Command ssh -ErrorAction SilentlyContinue
Write-Host ("Get-Command ssh = " + $(if($c){$c.Source}else{'(null)'}))
Write-Host ("System32\OpenSSH\ssh.exe exists = " + (Test-Path "$env:SystemRoot\System32\OpenSSH\ssh.exe"))
Write-Host ("USERPROFILE = " + $env:USERPROFILE)
Write-Host ("HOME = " + $env:HOME)
Write-Host ("id_ed25519 exists = " + (Test-Path "$env:USERPROFILE\.ssh\id_ed25519"))
Write-Host ("config exists = " + (Test-Path "$env:USERPROFILE\.ssh\config"))

$psi = New-Object Diagnostics.ProcessStartInfo
$psi.FileName = (Get-Command ssh).Source
$psi.Arguments = '-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "dream" "echo __OK__; hostname"'
$psi.UseShellExecute = $false
$psi.CreateNoWindow = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
Write-Host ("FileName = " + $psi.FileName)
$p = [Diagnostics.Process]::Start($psi)
Write-Host ("started = " + [bool]$p)
if (-not $p.WaitForExit(15000)) { $p.Kill(); Write-Host "TIMEOUT" }
else {
  Write-Host ("exit = " + $p.ExitCode)
  Write-Host ("out = [" + $p.StandardOutput.ReadToEnd().Trim() + "]")
  Write-Host ("err = [" + $p.StandardError.ReadToEnd().Trim() + "]")
}
'''
r = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True,
                   creationflags=0x08000000, timeout=120)
print((r.stdout or b"").decode("cp936", "replace"))
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("ERR:", err[-400:])
