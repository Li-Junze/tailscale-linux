# -*- coding: utf-8 -*-
"""帮启动器挑一个【能 import PyQt5】的 python 解释器。

为什么需要它:
  Windows 上装 python 的方式五花八门, 有多个解释器并存很常见
  (Microsoft Store 版、官网安装版、conda、venv、便携版...)。
  只有一部分带 PyQt5。这个脚本挨个试, 把第一个可用的打印出来。

同时输出 pythonw.exe(无控制台版)的路径, 供 .vbs 静默启动器使用 ——
GUI 程序不该在背后杵一个黑色 cmd 窗口。

用法:
    python find_python.py            # 只打印可用的 python.exe 路径
    python find_python.py --w        # 只打印可用的 pythonw.exe 路径
    python find_python.py --all      # 两行都打印

退出码: 0=找到, 1=没找到
注意: 探测时会剔除 PYTHONPATH, 避免外部注入的 shim 干扰判断。
"""
import os
import subprocess
import sys

# 候选解释器, 按"最可能有 PyQt5"排序
CANDIDATES = [
    # 官网安装版(最常见带 PyQt5 的)
    r"C:/Python314/python.exe",
    r"C:/Python313/python.exe",
    r"C:/Python312/python.exe",
    r"C:/Python311/python.exe",
    r"C:/Python310/python.exe",
    # 用户级安装
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
    # conda / venv 常见位置
    os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
    os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
    os.path.expandvars(r"%USERPROFILE%\miniconda3\envs\default\python.exe"),
    os.path.expandvars(r"%CONDA_PREFIX%\python.exe"),
    os.path.expandvars(r"%VIRTUAL_ENV%\Scripts\python.exe"),
    # 系统自带(往往没有 PyQt5, 但便宜, 放最后)
    os.path.expandvars(r"%SYSTEMROOT%\System32\config\systemprofile\AppData\Local\Microsoft\WindowsApps\python.exe"),
]

# PATH 里的命令名
PATH_NAMES = ["python", "python3", "py"]


def _clean_env():
    """剔除 PYTHONPATH / PYTHONHOME —— 它们会指向别的环境的 lib,
    让本机明明装了 PyQt5 却 import 失败。"""
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
        env.pop(k, None)
    return env


def _try(path_or_cmd, is_cmd=False):
    """试一个解释器能不能 import PyQt5。返回规范化后的 exe 路径或 None。"""
    argv = ([path_or_cmd, "-c", "import PyQt5.QtWidgets"]
            if is_cmd else [path_or_cmd, "-c", "import PyQt5.QtWidgets"])
    try:
        r = subprocess.run(argv, capture_output=True, timeout=25, env=_clean_env())
    except Exception:      # noqa: BLE001  路径不存在 / 权限 / 超时
        return None
    if r.returncode != 0:
        return None
    # 转成绝对路径, 并确认那个目录里确实有对应的 .exe
    exe = path_or_cmd
    if not os.path.isabs(exe):
        try:
            exe = _which(exe)
        except Exception:  # noqa: BLE001
            exe = None
    if not exe or not os.path.isfile(exe):
        return None
    return os.path.abspath(exe)


def _which(name):
    from shutil import which
    return which(name)


def _to_pythonw(exe):
    """由 python.exe 推出同目录的 pythonw.exe(无控制台版)。"""
    if not exe:
        return None
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.isfile(cand) else None


def find(with_pyqt5=True, want_windows=False):
    """找解释器。

    with_pyqt5: 是否要求能 import PyQt5(True) 还是任意 python(False)
    want_windows: True 时返回 pythonw.exe(无控制台), False 返回 python.exe
    """
    seen = set()

    def _accept(exe):
        if not exe:
            return None
        key = os.path.normcase(exe)
        if key in seen:
            return None
        seen.add(key)
        return _to_pythonw(exe) if want_windows else exe

    # 1) 已知路径
    for c in CANDIDATES:
        if not c:
            continue
        r = _try(c) if with_pyqt5 else (
            c if os.path.isfile(c) else None)
        got = _accept(r)
        if got:
            return got

    # 2) PATH 里的命令
    for name in PATH_NAMES:
        # py 启动器需要 -3 / -c 的组合, 这里直接当解释器试, 失败就跳过
        r = _try(name, is_cmd=True)
        got = _accept(r)
        if got:
            return got

    # 3) 退化: 只要有 python 就给(至少 CLI 能跑)
    if not with_pyqt5:
        for name in PATH_NAMES:
            try:
                exe = _which(name)
            except Exception:  # noqa: BLE001
                exe = None
            got = _accept(exe)
            if got:
                return got
    return None


def main():
    want_w = "--w" in sys.argv or "--windows" in sys.argv
    all_m = "--all" in sys.argv
    exe = find(with_pyqt5=True, want_windows=want_w)
    if not exe:
        return 1
    print(exe)
    if all_m:
        other = _to_pythonw(exe) if not want_w else exe
        if other and os.path.isfile(other):
            print(other)
    return 0


if __name__ == "__main__":
    sys.exit(main())
