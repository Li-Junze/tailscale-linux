# -*- coding: utf-8 -*-
"""用本地源文件跑桥接(-Once), 快速迭代调试。PowerShell 输出是 GBK, 按 cp936 解码。"""
import os, subprocess, sys

PS = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
p = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "site", "pkg", "bridge.ps1"))
tok = sys.argv[1] if len(sys.argv) > 1 else "dbg1"
extra = sys.argv[2:]

r = subprocess.run(
    [PS, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", p,
     "-Token", tok, "-Relay", "https://ts-remote-web.pages.dev", "-Once"] + extra,
    capture_output=True, creationflags=0x08000000, timeout=240,
)
raw = r.stdout or b""
for enc in ("cp936", "utf-8", "gbk"):
    try:
        txt = raw.decode(enc)
        if "��" not in txt:
            break
    except Exception:
        continue
else:
    txt = raw.decode("utf-8", "replace")
print(txt[-2500:])
err = (r.stderr or b"").decode("cp936", "replace")
if err.strip():
    print("STDERR:", err[-600:])
