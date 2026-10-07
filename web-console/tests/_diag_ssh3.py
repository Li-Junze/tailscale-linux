# -*- coding: utf-8 -*-
"""诊断: .NET Process 方式 + Windows OpenSSH, 两种远程命令对比"""
import os, subprocess

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
code = r'''
function Try($sshPath, $cmd) {
    $psi = New-Object Diagnostics.ProcessStartInfo
    $psi.FileName = $sshPath
    $psi.Arguments = '-o BatchMode=yes -o ConnectTimeout=6 -o StrictHostKeyChecking=accept-new -o LogLevel=ERROR "dream" "' + $cmd + '"'
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $p = [Diagnostics.Process]::Start($psi)
    if (-not $p.WaitForExit(15000)) { $p.Kill(); return "TIMEOUT" }
    $o = $p.StandardOutput.ReadToEnd().Trim()
    $e = $p.StandardError.ReadToEnd().Trim()
    return ("rc=" + $p.ExitCode + " out=[" + $o + "] err=[" + $e.Substring(0,[Math]::Min(120,$e.Length)) + "]")
}
$win = "$env:SystemRoot\System32\OpenSSH\ssh.exe"
$git = (Get-Command ssh).Source
Write-Host ("win : " + (Try $win 'echo __OK__; uname -s 2>/dev/null; hostname 2>/dev/null'))
Write-Host ("git : " + (Try $git 'echo __OK__; uname -s 2>/dev/null; hostname 2>/dev/null'))
Write-Host ("win2: " + (Try $win 'echo __OK__'))
'''
r = subprocess.run([PS, "-NoProfile", "-Command", code], capture_output=True,
                   creationflags=0x08000000, timeout=150)
print((r.stdout or b"").decode("cp936", "replace"))
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("ERR:", err[-400:])
