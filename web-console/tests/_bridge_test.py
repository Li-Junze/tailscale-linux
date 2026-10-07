# -*- coding: utf-8 -*-
"""本机桥接自检: 注册 token -> 下载 bridge.ps1 -> -Once 跑一轮 -> 看网页是否收到"""
import os, sys, json, subprocess, time, urllib.request

RELAY = "https://ts-remote-web.pages.dev"
TOK = sys.argv[1] if len(sys.argv) > 1 else "testtok" + str(int(time.time()) % 10000)
CODE = sys.argv[2] if len(sys.argv) > 2 else ""

tmp = os.environ.get("TEMP", "/tmp")


def curl(args, out=None):
    cmd = ["curl", "-sS", "-m", "60"] + args
    if CODE:
        cmd += ["-H", "Cookie: tsr_auth=" + CODE]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def api(path, method="GET", body=None):
    args = ["-X", method, RELAY + path]
    if body is not None:
        args += ["-H", "Content-Type: application/json", "-d", json.dumps(body, ensure_ascii=False)]
    rc, out = curl(args)
    return out


print("=== 1) 注册本机令牌 ===")
print(api("/api/acl", "POST", {"addTok": TOK})[:200])

print("=== 2) 下载 bridge.ps1 (带令牌) ===")
ps1 = os.path.join(tmp, "tsr-bridge-test.ps1")
rc, out = curl(["-L", f"{RELAY}/pkg/bridge.ps1?t={TOK}", "-o", ps1])
print("rc =", rc, "size =", os.path.getsize(ps1) if os.path.exists(ps1) else "下载失败")
if not os.path.exists(ps1) or os.path.getsize(ps1) < 1000:
    print(out[:300]); sys.exit(1)

print("=== 3) 跑一轮 (-Once) ===")
ps = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
r = subprocess.run(
    [ps, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", ps1,
     "-Token", TOK, "-Relay", RELAY, "-Once"],
    capture_output=True, text=True, encoding="utf-8", errors="replace",
    creationflags=0x08000000, timeout=180,
)
print((r.stdout or "")[-2500:])
if r.stderr.strip():
    print("STDERR:", r.stderr.strip()[:500])

print("=== 4) 网页侧是否收到 ===")
print(api(f"/api/bridge?token={TOK}&t={TOK}")[:900])
