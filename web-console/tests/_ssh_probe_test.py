# -*- coding: utf-8 -*-
"""定位桥接探测失败原因: 逐个参数组合试 ssh"""
import subprocess

CASES = {
    "A 全套(含 NUL)": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o",
                       "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=NUL", "-o", "LogLevel=ERROR"],
    "B 去掉 NUL": ["-o", "BatchMode=yes", "-o", "ConnectTimeout=6", "-o",
                   "StrictHostKeyChecking=no", "-o", "LogLevel=ERROR"],
    "C 只 BatchMode": ["-o", "BatchMode=yes"],
}
for name, opts in CASES.items():
    r = subprocess.run(["ssh"] + opts + ["dream", "echo __OK__; hostname"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=40)
    print(f"{name}: rc={r.returncode} out={(r.stdout or '').strip()[:80]!r} err={(r.stderr or '').strip()[:120]!r}")

# 作业内复现
ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
code = r'''
$sb = {
    param($alias)
    $a = @("-o","BatchMode=yes","-o","ConnectTimeout=6","-o","StrictHostKeyChecking=no","-o","UserKnownHostsFile=NUL","-o","LogLevel=ERROR")
    $out = & ssh @a $alias "echo __OK__; hostname" 2>$null
    return ("rc=" + $LASTEXITCODE + " out=[" + ($out -join ",") + "]")
}
$j = Start-Job -ScriptBlock $sb -ArgumentList @("dream")
$ok = Wait-Job $j -Timeout 20
Write-Host ("job done=" + [bool]$ok)
Write-Host (Receive-Job $j)
Remove-Job $j -Force
'''
r = subprocess.run([ps, "-NoProfile", "-Command", code], capture_output=True,
                   text=True, encoding="utf-8", errors="replace", creationflags=0x08000000, timeout=120)
print("--- job ---")
print(r.stdout[-800:])
if r.stderr.strip():
    print("ERR:", r.stderr[-400:])
