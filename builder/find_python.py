#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
find_python.py —— 帮 ★运行生成器.bat 找一个「能 import PyQt5」的 Python
=====================================================================
为什么需要:  很多机器上 python / py 有多个, 有的装了 PyQt5 有的没装。
            bat 自己拼 `-c "import PyQt5"` 在 cmd 下引号转义极易出错,
            所以把探测逻辑放回 Python, bat 只负责读结果。

用法:  python find_python.py
输出:  打印第一个可用的解释器绝对路径到 stdout (探测成功时只输出这一行)
      全部不可用时输出 空的 100 错误码 (bat 据此显示提示)
"""
import os
import subprocess
import sys

# 常见安装位置, 按"最可能有 PyQt5"排序
CANDIDATES = [
    "python",                      # PATH 里的默认 python
    "py",                          # Windows Python Launcher
    r"C:\Python314\python.exe",
    r"C:\Python313\python.exe",
    r"C:\Python312\python.exe",
    r"C:\Python311\python.exe",
    r"C:\Python310\python.exe",
]
LOCAL = os.environ.get("LOCALAPPDATA", "")
if LOCAL:
    for v in ("313", "312", "311", "310", "39"):
        CANDIDATE = os.path.join(LOCAL, "Programs", "Python", "Python" + v, "python.exe")
        CANDIDATES.append(CANDIDATE)
    CANDIDATES.append(os.path.join(LOCAL, "Microsoft", "WindowsApps", "python.exe"))


def works(exe):
    """该解释器能否 import PyQt5.QtWidgets。"""
    try:
        r = subprocess.run(
            [exe, "-c", "import PyQt5.QtWidgets"],
            capture_output=True, timeout=60,
            # 清掉可能干扰的 PYTHONPATH(WorkBuddy 会注入 shim)
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def resolve(exe):
    """把 'python' / 'py' 解析成绝对路径, 便于 bat 可靠调用。"""
    try:
        r = subprocess.run(
            [exe, "-c", "import sys; print(sys.executable)"],
            capture_output=True, text=True, timeout=60,
            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"},
        )
        if r.returncode == 0:
            p = r.stdout.strip()
            if p and os.path.isfile(p):
                return p
    except Exception:  # noqa: BLE001
        pass
    return exe


def main():
    # 自身解释器优先(用户可能就是想用它)
    if works(sys.executable):
        print(os.path.abspath(sys.executable))
        return 0
    for exe in CANDIDATES:
        if works(exe):
            print(resolve(exe))
            return 0
    sys.exit(100)


if __name__ == "__main__":
    sys.exit(main())
