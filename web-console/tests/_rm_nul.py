# -*- coding: utf-8 -*-
"""删掉 Windows 保留设备名文件 NUL（普通 API 拒绝访问, 要 \\?\ 前缀）"""
import os

base = r"C:\Users\39969\WorkBuddy\2026-10-06-11-03-09\tailscale-remote\web-console"
raw = os.path.join(base, "NUL")
p = "\\\\?\\" + raw
print("target:", p)
try:
    os.remove(p)
    print("已删除")
except Exception as e:
    print("删除失败:", e)
print("还在?", os.path.exists(raw))
