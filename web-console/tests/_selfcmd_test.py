# -*- coding: utf-8 -*-
"""验证网页里「自动填本机信息」那条命令真能跑通(与前端 selfCommand() 生成的一致)"""
import json, os, subprocess, sys, time

RELAY = "https://ts-remote-web.pages.dev"
TOK = sys.argv[1] if len(sys.argv) > 1 else "self" + str(int(time.time()) % 10000)
CODE_HASH = sys.argv[2] if len(sys.argv) > 2 else ""

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

# 先登记这个 token(前端 ensureTok 做的事)
subprocess.run(["curl", "-sS", "-m", "30", "-X", "POST", RELAY + "/api/acl",
                "-H", "Content-Type: application/json",
                "-H", "Cookie: tsr_auth=" + CODE_HASH,
                "-d", json.dumps({"addTok": TOK})], capture_output=True)
print("token 已登记:", TOK)

# 前端 selfCommand() 的输出(逐字对照)
inner = (
    "$t='" + TOK + "';$r='" + RELAY + "';"
    "$p=$env:USERPROFILE + '/.ssh/id_ed25519';"
    "if(-not(Test-Path ($p + '.pub'))){ssh-keygen -t ed25519 -N '' -f $p | Out-Null};"
    "$pub=(Get-Content ($p + '.pub') -Raw).Trim();"
    "$ts='C:/Program Files/Tailscale/tailscale.exe';$ip='';"
    "if(Test-Path $ts){try{$ip=@(&$ts ip -4)[0]}catch{}};"
    "$b=@{token=$t;info=@{host=$env:COMPUTERNAME;user=$env:USERNAME;pub=$pub;tsip=$ip}}|ConvertTo-Json -Depth 5;"
    "Invoke-RestMethod -Uri ($r + '/api/ctrl?t=' + $t) -Method POST -ContentType 'application/json' -Body ([Text.Encoding]::UTF8.GetBytes($b))|Out-Null;"
    "Write-Host 'OK 已上报'"
)
full = 'powershell -NoProfile -ExecutionPolicy Bypass -Command "' + inner + '"'
print("--- 命令预览 ---")
print(full[:160] + " ...")

r = subprocess.run([PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", inner],
                   capture_output=True, creationflags=0x08000000, timeout=180)
print("--- 执行结果 ---")
print((r.stdout or b"").decode("cp936", "replace"))
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("STDERR:", err[-400:])

# 拉回来看看
out = subprocess.run(["curl", "-sS", "-m", "30",
                      f"{RELAY}/api/ctrl?token={TOK}&t={TOK}"], capture_output=True)
print("--- 网页侧读回 ---")
print(out.stdout.decode("utf-8", "replace")[:600])
