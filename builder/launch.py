# -*- coding: utf-8 -*-
"""工具箱启动器 —— 被桌面/仓库里的 .vbs 调用。

为什么要单独一个文件:
    VBScript 按系统 ANSI 码页读源码, 任何非 ASCII 字节(尤其是路径里的
    中文)都可能被误解码成乱码 -> FileExists 判定失败 -> 报"找不到文件"。
    与其在 .vbs 里拼 Unicode 转义(极易写错), 不如把这活交给 Python:
    本文件全 UTF-8, 中文路径随便写。

职责:
    1. 定位项目根(找 builder 目录的父目录, 里面要有 build_gui.py)
    2. 定位带 PyQt5 的解释器
    3. 放哨兵 -> 静默拉起 GUI -> 校验哨兵 -> 失败写日志并弹窗

用法:
    python launch.py            # 由 .vbs 用 pythonw 调用(无控制台)
    python launch.py --probe    # 只打印定位结果, 不启动(诊断用)
"""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# 哨兵和日志必须放在【项目的 builder 目录】里, 因为 build_gui.py 只会清
# 自己所在目录的 .toolbox-alive。若本脚本被复制到桌面单独运行, 这里会先
# 指向桌面, 定位到项目后再改写成项目路径。
LOG_PATH = os.path.join(HERE, "toolbox-launch-failed.log")
SENTINEL = os.path.join(HERE, ".toolbox-alive")


def retarget(root):
    """把哨兵/日志路径改到项目的 builder 目录。"""
    global LOG_PATH, SENTINEL
    b = os.path.join(root, "builder")
    LOG_PATH = os.path.join(b, "toolbox-launch-failed.log")
    SENTINEL = os.path.join(b, ".toolbox-alive")

# 中文路径写成 Python 字符串是安全的(本文件是 UTF-8)
SEARCH_ROOTS = [
    r"C:/Users/39969/WPSDrive/1732568967/WPS企业云盘/清华大学/团队文档"
    r"\威仙城\mess\show\tool\P2P\tailscale-remote",
    r"C:/Users/39969/WorkBuddy/2026-10-06-11-03-09/tailscale-remote",
]

PY_CANDIDATES = [
    r"C:/Python314/python.exe", r"C:/Python313/python.exe",
    r"C:/Python312/python.exe", r"C:/Python311/python.exe",
    r"C:/Python310/python.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python314\python.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python313\python.exe"),
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
    os.path.expandvars(r"%USERPROFILE%\anaconda3\python.exe"),
    os.path.expandvars(r"%USERPROFILE%\miniconda3\python.exe"),
]


def log(msg):
    """追加一行到失败日志。失败也绝不影响主流程。"""
    import datetime
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:      # noqa: BLE001
        pass


def pop(title, body):
    """弹一个消息框。pythonw 下没有 stderr, 这是唯一的反馈渠道。"""
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, body, title, 0x10)
    except Exception:      # noqa: BLE001
        try:
            sys.stderr.write(f"{title}\n{body}\n")
        except Exception:  # noqa: BLE001
            pass


def has_gui(root):
    return os.path.isfile(os.path.join(root, "builder", "build_gui.py"))


def find_project():
    """定位项目根(里面有 builder/build_gui.py)。"""
    # 1) 本文件所在位置向上找(项目自带的启动器一定对)
    d = HERE
    for _ in range(4):
        if has_gui(d):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 2) 已知位置
    for r in SEARCH_ROOTS:
        if has_gui(r):
            return r
    # 3) 扫描 WPS 云盘(深度受限, 只找含 builder 的目录)
    cloud = r"C:/Users/39969/WPSDrive"
    if os.path.isdir(cloud):
        hit = _scan(cloud, 7)
        if hit:
            return hit
    return None


def _scan(base, depth):
    """递归找 builder\\build_gui.py。深度受限以控制耗时。"""
    if depth <= 0:
        return None
    try:
        entries = list(os.scandir(base))
    except OSError:
        return None
    dirs = []
    for e in entries:
        try:
            if not e.is_dir():
                continue
        except OSError:
            continue
        if e.name == "builder" and os.path.isfile(
                os.path.join(e.path, "build_gui.py")):
            return base
        dirs.append(e.path)
    for p in dirs:
        r = _scan(p, depth - 1)
        if r:
            return r
    return None


PROBE_CACHE = os.path.join(HERE, ".pyqt5-probe.json")


def _probe_cache_read():
    try:
        import json
        with open(PROBE_CACHE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except Exception:      # noqa: BLE001
        return {}


def _probe_cache_write(d):
    try:
        import json
        with open(PROBE_CACHE, "w", encoding="utf-8") as f:
            json.dump(dict(list(d.items())[-20:]), f)
    except Exception:      # noqa: BLE001
        pass


def has_pyqt5(exe):
    """带缓存的探测 —— 每次启动少等约 1 秒。

    缓存键含解释器的 mtime+size, 所以换了 Python 版本会自动重新探测;
    PyQt5 本身装/卸载不会改 exe 的 mtime, 用 30 天过期兜底。
    """
    import time as _t
    key = None
    try:
        st = os.stat(exe)
        key = f"{os.path.normcase(exe)}|{int(st.st_mtime)}|{st.st_size}"
        cache = _probe_cache_read()
        hit = cache.get(key)
        # ★ 只认「成功」的缓存: 失败不缓存, 免得用户补装 PyQt5 后还被挡 30 天
        if (isinstance(hit, dict) and hit.get("ok")
                and _t.time() - hit.get("t", 0) < 30 * 86400):
            return True
    except Exception:      # noqa: BLE001
        cache = {}
    try:
        env = dict(os.environ)
        for k in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP"):
            env.pop(k, None)
        r = subprocess.run([exe, "-c", "import PyQt5.QtWidgets"],
                           capture_output=True, timeout=25, env=env)
        ok = r.returncode == 0
    except Exception:      # noqa: BLE001
        ok = False
    if key and ok:
        cache[key] = {"ok": True, "t": _t.time()}
        _probe_cache_write(cache)
    return ok


def to_pythonw(exe):
    p = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return p if os.path.isfile(p) else None


def find_python():
    """返回一个带 PyQt5 的 pythonw.exe(没有控制台)。"""
    from shutil import which
    cands = list(PY_CANDIDATES) + [
        which("pythonw"), which("python"), which("python3")]
    seen = set()
    for c in cands:
        if not c:
            continue
        exe = c if os.path.isabs(c) else (which(c) or "")
        if not exe or not os.path.isfile(exe):
            continue
        key = os.path.normcase(exe)
        if key in seen:
            continue
        seen.add(key)
        if not has_pyqt5(exe):
            continue
        return to_pythonw(exe) or exe
    return None


def main():
    probe = "--probe" in sys.argv
    root = find_project()
    pyw = find_python()

    if probe:
        print(f"project : {root or '(NOT FOUND)'}")
        print(f"pythonw : {pyw or '(NOT FOUND)'}")
        return 0 if (root and pyw) else 1

    if not root:
        msg = (f"找不到工具箱(build_gui.py)。\n\n已尝试:\n"
               f"1) {HERE} 向上 4 层\n"
               f"2) WPS 云盘 tool\\P2P\\tailscale-remote\n"
               f"3) WorkBuddy\\2026-10-06-11-03-09\\tailscale-remote\n"
               f"4) 扫描 C://Users//39969//WPSDrive (7 层)\n\n"
               f"如果你挪动了项目, 可以直接在命令行跑:\n"
               f"    python <项目路径>\\builder\\build_gui.py")
        log(msg)
        pop("Tailscale 工具箱 - 找不到项目", msg)
        return 1

    retarget(root)          # 哨兵/日志 落到项目里, 与 GUI 约定一致

    if not pyw:
        msg = (f"找不到带图形界面支持的 Python(pythonw.exe)。\n\n"
               f"请安装 Python 后执行:\n"
               f"    python -m pip install PyQt5")
        log(msg)
        pop("Tailscale 工具箱 - 缺少 PyQt5", msg)
        return 1

    # 哨兵: GUI 起来后会删掉它
    try:
        if os.path.isfile(SENTINEL):
            os.remove(SENTINEL)
        open(SENTINEL, "w").close()
    except OSError:
        pass

    gui = os.path.join(root, "builder", "build_gui.py")
    env = dict(os.environ)
    for k in ("PYTHONPATH", "PYTHONHOME"):
        env.pop(k, None)          # 外部注入的 shim 会让 PyQt5 import 失败
    env["PYTHONIOENCODING"] = "utf-8"

    log(f"launching: {pyw} {gui}")
    import time
    try:
        subprocess.Popen([pyw, gui], cwd=os.path.dirname(gui), env=env,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as e:     # noqa: BLE001
        log(f"spawn failed: {e}")
        pop("Tailscale 工具箱 - 启动失败", f"无法启动图形界面:\n{e}")
        return 1

    # 等 GUI 删哨兵
    for _ in range(25):        # 25 * 0.2s = 5s
        time.sleep(0.2)
        if not os.path.isfile(SENTINEL):
            log("GUI up (sentinel cleared)")
            return 0

    msg = (f"图形界面没能启动。\n\n"
           f"项目: {root}\n"
           f"解释器: {pyw}\n\n"
           f"该解释器可能没装 PyQt5, 请执行:\n"
           f"    {pyw.replace('pythonw', 'python')} -m pip install PyQt5\n\n"
           f"装完再双击桌面图标即可。\n"
           f"(详细日志: {os.path.join(root, 'builder', 'debug.log')})")
    log("sentinel still present -> GUI failed")
    pop("Tailscale 工具箱 - 启动失败", msg)
    return 1


if __name__ == "__main__":
    sys.exit(main())
