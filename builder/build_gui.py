#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_gui.py —— Tailscale-Remote 配置生成器
=============================================
操作员（你）在本机运行, 按"目标机环境"勾选 / 填写, 一键生成
「配置已烘焙、目标机解压后只看得见两个要跑的文件」的部署包。

★ 目标包结构（刻意做得很干净）:
    包根/
    ├── deploy.sh      ← Linux: 只需跑这个
    ├── clean.sh       ← Linux: 用完跑这个
    ├── deploy.bat     ← Windows: 只需右键跑这个
    ├── clean.bat      ← Windows: 用完跑这个
    ├── README.md      ← 先看我
    ├── 使用说明.txt     ← 极简三步
    └── 程序/           ← 所有实现细节收纳于此, 不干扰使用
        ├── linux/     (脚本 + assets 预置二进制 + keys 密钥)
        └── windows/

依赖: PyQt5   (pip install PyQt5)   —— 仅图形界面需要;
      核心打包逻辑不依赖 PyQt5, 可单独调用做自动化 / 测试。
运行: python build_gui.py
"""
import os
import sys
import json
import shutil
import tarfile
import zipfile
import datetime
import subprocess


def _hide_console():
    """隐藏 Windows 控制台窗口(GUI 背后不该有个黑框)。
    ★ 必须在 QApplication 创建之前调用, 且要在 import PyQt5 之前尝试。
    ★ .bat 启动时会带一个 cmd 窗口; 用 pythonw 或 DETACHED_PROCESS 都能去掉。"""
    if os.name != "nt":
        return
    try:
        import ctypes
        # 6 = SW_HIDE: 让 console 窗口隐藏而不是关掉(关掉会连带影响 GUI)
        ctypes.windll.user32.ShowWindow(
            ctypes.windll.kernel32.GetConsoleWindow(), 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        # 让任务栏/Alt+Tab 里也不再显示这个 python 窗口
        ctypes.windll.user32.SetWindowPos(
            ctypes.windll.kernel32.GetConsoleWindow(), -1, 0, 0, 0, 0x0001)
    except Exception:  # noqa: BLE001
        pass


_hide_console()

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QComboBox, QLineEdit,
        QPlainTextEdit, QPushButton, QFileDialog, QMessageBox, QLabel,
        QCheckBox, QFrame, QScrollArea, QButtonGroup,
        # 设备管理表格
        QAbstractItemView, QTableWidget, QTableWidgetItem, QHeaderView,
    )
    # ★ pyqtSignal / QTimer 是"子线程回主线程"的必需品, 漏了会在
    #   import 之后的类定义处直接 NameError -> 表现为"双击就闪退"。
    from PyQt5.QtCore import Qt, QUrl, QTimer, pyqtSignal
    from PyQt5.QtGui import QFont, QDesktopServices, QGuiApplication, QColor
    HAS_QT = True
except Exception:  # noqa: BLE001
    HAS_QT = False

# 脚本位于 builder/ ，仓库根 = builder 的上级
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TS_KEYS_URL = "https://login.tailscale.com/admin/settings/keys"


# ============================================================ 纯逻辑（无 UI 依赖）
def _exe_filter(ti):
    """包内所有可执行物都要执行位。"""
    name = os.path.basename(ti.name)
    ext = os.path.splitext(name)[1].lower()
    if ti.isdir() or ext in (".sh", ".exe", ".bat", ".py") or name in (
        "tailscale", "tailscaled", "7zz", "7za"
    ):
        ti.mode = 0o755
    return ti


def make_targz(pkg_dir, out_dir, pkg_name, log=print):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, pkg_name + ".tar.gz")
    arcname = os.path.basename(pkg_dir)
    with tarfile.open(out_path, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar.add(pkg_dir, arcname=arcname, filter=_exe_filter)
    log(f"  ✓ tar.gz : {out_path}  ({os.path.getsize(out_path)//1024} KB)")
    return out_path


def make_zip(pkg_dir, out_dir, pkg_name, log=print):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, pkg_name + ".zip")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(pkg_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, pkg_dir).replace(os.sep, "/")
                zi = zipfile.ZipInfo(rel)
                base = os.path.basename(f)
                ext = os.path.splitext(base)[1].lower()
                is_exec = ext in (".sh", ".exe", ".bat", ".py") or base in (
                    "tailscale", "tailscaled", "7zz", "7za"
                )
                zi.external_attr = ((0o755 if is_exec else 0o644) << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(fp, "rb") as fh:
                    z.writestr(zi, fh.read())
    log(f"  ✓ zip    : {out_path}  ({os.path.getsize(out_path)//1024} KB)")
    return out_path


# ------------------------------------------------------------ 密钥工具（纯逻辑）
def find_ssh_keypair():
    """在本机常见位置找已有的 ed25519 密钥对, 找到返回 (priv, pub) 或 (None, None)。"""
    home = os.path.expanduser("~")
    cands = [os.path.join(home, ".ssh")]
    up = os.environ.get("USERPROFILE")
    if up:
        cands.append(os.path.join(up, ".ssh"))
    for d in cands:
        priv = os.path.join(d, "id_ed25519")
        if os.path.isfile(priv) and os.path.isfile(priv + ".pub"):
            return priv, priv + ".pub"
    return None, None


def generate_ssh_keypair(force=False, log=print):
    """本机生成 ed25519 密钥对(无口令)。返回 (priv, pub, pubkey_text)；失败返回 (None,None,None)"""
    priv, pub = find_ssh_keypair()
    if priv and not force:
        log(f"  [i] 已存在密钥对, 复用: {priv}")
    else:
        d = os.path.dirname(priv) if priv else os.path.join(
            os.path.expanduser("~"), ".ssh")
        os.makedirs(d, exist_ok=True)
        priv = os.path.join(d, "id_ed25519")
        if force:
            for f in (priv, priv + ".pub"):
                if os.path.isfile(f):
                    os.remove(f)
        log("  生成 ed25519 密钥对 (无口令) ...")
        try:
            # 空口令: 管道喂两个空行, 避开 Windows 下 -N "" 参数被丢弃的坑
            p = subprocess.run(
                ["ssh-keygen", "-t", "ed25519", "-f", priv],
                input="\r\n\r\n", capture_output=True, text=True, timeout=60,
            )
        except FileNotFoundError:
            log("  [X] 未找到 ssh-keygen。Windows 需安装 OpenSSH 客户端: 设置→可选功能→OpenSSH 客户端")
            return None, None, None
        except Exception as e:  # noqa: BLE001
            log(f"  [X] ssh-keygen 调用异常: {e}")
            return None, None, None
        if p.returncode != 0 or not os.path.isfile(priv + ".pub"):
            log(f"  [X] ssh-keygen 失败: {(p.stderr or p.stdout).strip()[:200]}")
            return None, None, None
    try:
        text = open(priv + ".pub", encoding="utf-8", errors="ignore").read().strip()
    except OSError as e:
        log(f"  [X] 读公钥失败: {e}")
        return None, None, None
    log(f"  ✅ 私钥: {priv}")
    log(f"  ✅ 公钥: {priv}.pub")
    return priv, priv + ".pub", text


def normalize_authkey(s):
    """允许用户带 TS_AUTHKEY= 前缀粘贴, 统一剥掉。"""
    s = (s or "").strip()
    for pfx in ("TS_AUTHKEY=", "ts_authkey="):
        if s.lower().startswith(pfx.lower()):
            s = s[len(pfx):].strip()
    return s


# ============================================================ 调试日志
DEBUG_LOG_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "debug.log")
_LOG_FH = None
_LOG_DAY = None


def _log_open():
    """打开按天轮转的调试日志。失败只返回 None, 绝不抛异常。"""
    global _LOG_FH, _LOG_DAY
    try:
        import datetime
        day = datetime.date.today().strftime("%Y%m%d")
        if _LOG_FH is not None and _LOG_DAY == day:
            return _LOG_FH
        if _LOG_FH is not None:
            try:
                _LOG_FH.close()
            except Exception:  # noqa: BLE001
                pass
        path = (DEBUG_LOG_PATH if _LOG_FH is None
                else DEBUG_LOG_PATH.replace(".log", f".{day}.log"))
        _LOG_FH = open(path, "a", encoding="utf-8")
        _LOG_DAY = day
        return _LOG_FH
    except Exception:  # noqa: BLE001
        return None


def dbg(msg):
    """写一行调试日志(带时间)。同时尽量打到 stderr。永不抛异常。"""
    try:
        import datetime
        ts = datetime.datetime.now().strftime("%H:%M:%S")
        line = f"[{ts}] {msg}"
        fh = _log_open()
        if fh is not None:
            fh.write(line + "\n")
            fh.flush()
        try:
            sys.stderr.write(line + "\n")
            sys.stderr.flush()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        pass


def dbg_exc(tag):
    """记录当前异常堆栈。"""
    try:
        import traceback
        dbg(f"--- EXCEPTION at {tag} ---")
        dbg(traceback.format_exc())
    except Exception:  # noqa: BLE001
        pass



# ------------------------------------------------------------ 配置记忆
CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".tailscale_remote")
CONFIG_FILE = os.path.join(CONFIG_DIR, "generator_config.json")

DEFAULT_CONFIG = {
    "os_index": 2,            # 0=Linux 1=Windows 2=双端
    "mode_index": 0,          # 0=离线 1=轻量
    "arch_index": 0,
    "fmt_index": 2,
    "prefix": "tailscale-remote",
    "out_dir": os.path.join(os.path.expanduser("~"), "Desktop"),
    "want_7z_linux": False,
    "want_7z_windows": False,
    "authkey": "",            # 受 remember_auth 开关控制
    "pubkey": "",
    "remember_auth": False,   # authkey 默认【不】记住, 避免明文长期落盘
    "send_history": [],        # 发送文件的目标 IP 历史
}


def load_config(path=None, log=None):
    """读取上次的配置; 文件不存在或损坏时返回默认值(绝不抛异常)。"""
    path = path or CONFIG_FILE     # 运行时解析, 便于测试时重定向
    cfg = dict(DEFAULT_CONFIG)
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in DEFAULT_CONFIG:
                    if k in data:
                        cfg[k] = data[k]
                if log:
                    log("  已载入上次配置")
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"  [i] 读取配置失败(忽略): {e}")
    return cfg


def save_config(cfg, path=None, log=None):
    """把配置写回磁盘。写失败只告警, 不影响主流程。"""
    path = path or CONFIG_FILE
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        if log:
            log(f"  已保存配置 -> {path}")
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"  [!] 保存配置失败: {e}")


def build_summary(linux_on, win_on, offline, want_7zip, arch, fmt_index,
                  has_auth, has_pub):
    """生成"点按钮前就能核对"的摘要文本, 避免选错平台还看不出来。"""
    plats = []
    if linux_on:
        plats.append(f"Linux({arch})")
    if win_on:
        plats.append("Windows")
    plat = " + ".join(plats) if plats else "(未选择)"

    mode = "离线（自带二进制，目标机无需联网）" if offline else "轻量（不带二进制，目标机需联网）"
    fmts = {0: ".tar.gz", 1: ".zip", 2: ".tar.gz + .zip"}[fmt_index]
    z = "、".join(
        ("7zz" if p == "linux" else "7za.exe") for p in want_7zip
    ) or "不内置 7-Zip"
    auth = "已填" if has_auth else "未填(对方手输)"
    pub = "已填" if has_pub else "未填"
    return (f"本次将生成： 【{plat}】 ｜ {mode} ｜ {fmts} ｜ 7-Zip: {z}"
            f" ｜ authkey {auth} ｜ 公钥 {pub}")


# ------------------------------------------------------------ 顶层入口脚本内容
DEPLOY_SH = """#!/usr/bin/env bash
# ============================================================
#  deploy.sh —— 【只需运行这个文件】一键部署 Tailscale 远程连接
#  用法:  bash deploy.sh
#  配套:  clean.sh  (用完清洗)
# ============================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ ! -f "$HERE/程序/linux/connect-offline.sh" ]; then
  echo "[X] 找不到 程序/linux/connect-offline.sh —— 包不完整, 或目录被改动了。"
  echo "    请确认 deploy.sh 同级存在『程序』文件夹。"
  read -r -p "按回车退出" _; exit 1
fi
cd "$HERE/程序/linux" || exit 1
exec bash connect-offline.sh
"""

CLEAN_SH = """#!/usr/bin/env bash
# ============================================================
#  clean.sh —— 【用完运行这个】一键清除所有连接痕迹
#  用法:  bash clean.sh        交互式, 每步确认
#         bash clean.sh -y     非交互, 一次清完
#  会清除: tailscaled 进程 / ~/.tailscale/ / crontab 自启 / known_hosts
# ============================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/程序/linux" || { echo "[X] 找不到 程序/linux/, 包不完整。"; exit 1; }
exec bash clean.sh "$@"
"""

DEPLOY_BAT = """@echo off
REM ============================================================
REM  deploy.bat —— 【只需右键"以管理员身份运行"这个】一键部署
REM  配套:  clean.bat  (用完清洗)
REM ============================================================
cd /d "%~dp0"
if not exist "程序\\windows\\connect-windows.ps1" (
  echo [X] 找不到 程序\\windows\\connect-windows.ps1 -- 包不完整。
  pause & exit /b 1
)
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%CD%\\程序\\windows\\connect-windows.ps1\"'"
"""

CLEAN_BAT = """@echo off
REM ============================================================
REM  clean.bat -- 【用完右键"以管理员身份运行"这个】一键清洗
REM  会删除: Tailscale / OpenSSH Server / 公钥 / 密钥 / 状态
REM ============================================================
cd /d "%~dp0"
if not exist "程序\\windows\\clean-windows.ps1" (
  echo [X] 找不到 程序\\windows\\clean-windows.ps1 -- 包不完整。
  pause & exit /b 1
)
powershell -ExecutionPolicy Bypass -Command "Start-Process powershell -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"%CD%\\程序\\windows\\clean-windows.ps1\"'"
"""

QUICK_TXT = """【怎么用 —— 就三步】
================================================================

  第1步  看这个文件: README.md       (想了解更多就看, 不看也能用)
  第2步  部署:  Linux   ->  bash deploy.sh
                 Windows -> 右键 deploy.bat  选"以管理员身份运行"
  第3步  用完:  Linux   ->  bash clean.sh
                 Windows -> 右键 clean.bat   选"以管理员身份运行"

  deploy 跑完后屏幕会打印 Tailscale IP(形如 100.x.x.x),
  回到你自己电脑用它连进来即可。

----------------------------------------------------------------
【只有这几个文件要碰】
  deploy.sh / deploy.bat    部署 (只跑一次)
  clean.sh  / clean.bat     清洗 (用完跑)
  README.md                 说明文档
  使用说明.txt              就是本文件

  『程序』文件夹是实现细节, 不需要打开, 也不用动。
----------------------------------------------------------------
【安全提醒】
  本包内含明文 Tailscale authkey, 只发给你信任的人。
  对方跑完 clean.sh / clean.bat 后本机痕迹会被清除;
  但你的 Tailscale 后台设备列表里仍会留着这台机器,
  要彻底移除请到 https://login.tailscale.com/admin/machines 删除该节点。
"""





def write_root_files(pkg_dir, linux_on, windows_on, log=print):
    """顶层只放醒目入口 + 文档, 实现细节全在 程序/ 下。"""
    made = []
    if linux_on:
        p = os.path.join(pkg_dir, "deploy.sh")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(DEPLOY_SH)
        os.chmod(p, 0o755)
        made.append("deploy.sh")
        p = os.path.join(pkg_dir, "clean.sh")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write(CLEAN_SH)
        os.chmod(p, 0o755)
        made.append("clean.sh")
    if windows_on:
        p = os.path.join(pkg_dir, "deploy.bat")
        with open(p, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(DEPLOY_BAT)
        made.append("deploy.bat")
        p = os.path.join(pkg_dir, "clean.bat")
        with open(p, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(CLEAN_BAT)
        made.append("clean.bat")

    p = os.path.join(pkg_dir, "使用说明.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(QUICK_TXT)
    made.append("使用说明.txt")
    log(f"  ✓ 顶层入口: {', '.join(made)}")
    return made


# ------------------------------------------------------------ 主体装配
def assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=(), offline=True, log=print):
    """offline=True  -> 保留 assets 预置二进制(包大, 目标机零下载)
       offline=False -> 剥离二进制(包小, 目标机联网自取)"""
    inner = os.path.join(pkg_dir, "程序")
    os.makedirs(inner, exist_ok=True)

    for plat in platforms:
        src = os.path.join(repo_root, plat)
        dst = os.path.join(inner, plat)
        log(f"复制 {plat}/ → 程序/{plat}/ …")
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "_build"),
        )

        assets = os.path.join(dst, "assets")

        # ---- 离线 / 轻量 开关 ----
        if plat == "linux":
            ts, tsd = os.path.join(assets, "tailscale"), os.path.join(assets, "tailscaled")
            if offline:
                if os.path.isfile(ts) and os.path.isfile(tsd):
                    log(f"  ✓ 离线模式: 保留预置二进制 ({(os.path.getsize(ts)+os.path.getsize(tsd))//1024} KB)")
                else:
                    log("  ⚠ 离线模式但 assets 缺 tailscale/tailscaled → 自动退回轻量模式")
            else:
                removed = 0
                for f in (ts, tsd):
                    if os.path.isfile(f):
                        os.remove(f)
                        removed += 1
                if removed:
                    log(f"  · 轻量模式: 已剥离 {removed} 个预置二进制 (目标机需联网自取)")
        else:
            # Windows 轻量模式同样要剥 MSI (约 38MB), 否则轻量包名不副实
            msis = [os.path.join(assets, f) for f in os.listdir(assets)
                    if f.lower().endswith(".msi")] if os.path.isdir(assets) else []
            if msis and not offline:
                freed = sum(os.path.getsize(m) for m in msis)
                for m in msis:
                    os.remove(m)
                log(f"  · 轻量模式: 已剥离 {len(msis)} 个预置 MSI ({freed//1024} KB, 目标机需联网自取)")
            elif msis:
                log(f"  ✓ 离线模式: 保留预置 MSI ({sum(os.path.getsize(m) for m in msis)//1024} KB)")

        # ---- 便携 7-Zip: 未勾选则剔除 ----
        zdir = os.path.join(assets, "7zip")
        if plat in want_7zip:
            exe = "7zz" if plat == "linux" else "7za.exe"
            fp = os.path.join(zdir, exe)
            if os.path.isfile(fp):
                os.chmod(fp, 0o755)
                log(f"  ✓ 内置便携 7-Zip（{exe}, {os.path.getsize(fp)//1024} KB）")
            else:
                log(f"  ⚠ 勾选了内置 7-Zip，但未找到 {plat}/assets/7zip/{exe}。")
                log("     先运行: python builder/fetch_7zip.py")
        elif os.path.isdir(zdir):
            shutil.rmtree(zdir)
            log("  · 未勾选，已剔除 assets/7zip（减包体积）")

        # ---- 烘焙密钥 ----
        keys_dir = os.path.join(dst, "keys")
        os.makedirs(keys_dir, exist_ok=True)
        if auth:
            with open(os.path.join(keys_dir, "authkey.local.txt"), "w", encoding="utf-8") as f:
                f.write("TS_AUTHKEY=" + auth + "\n")
            log(f"  ✓ 烘焙 authkey → 程序/{plat}/keys/authkey.local.txt")
        if plat == "windows" and pub:
            with open(os.path.join(keys_dir, "control.pub"), "w", encoding="utf-8") as f:
                f.write(pub + "\n")
            log("  ✓ 烘焙控制端公钥 → 程序/windows/keys/control.pub")

        # ---- 提示二进制是否齐全 ----
        if plat == "linux":
            if not (os.path.isfile(ts) and os.path.isfile(tsd)):
                log("  · 本包不含 Linux 预置二进制 → 目标机需联网（deploy.sh 会自动下载）")
        else:
            msi = [f for f in os.listdir(assets) if f.lower().endswith(".msi")] \
                if os.path.isdir(assets) else []
            if not msi:
                log("  · 本包不含 Windows 预置 MSI → 目标机需联网（deploy.bat 会自动下载）")


def write_pkg_readme(pkg_dir, linux_on, windows_on, has_auth, has_pub,
                     want_7zip=(), offline=True, log=print):
    """生成包内 README.md（覆盖仓库总览，面向拿到包的人）"""
    L = []
    L.append("# Tailscale 远程连接 · 部署包")
    L.append("")
    L.append("这个包的目标：**你不需要懂任何技术，运行两个文件就能让你的电脑被远程连进来。**")
    L.append("")
    L.append("## 就这几步")
    L.append("")
    L.append("| 步骤 | Linux | Windows |")
    L.append("|---|---|---|")
    L.append("| 1. 部署 | `bash deploy.sh` | 右键 `deploy.bat` → 以管理员身份运行 |")
    L.append("| 2. 记下屏幕打印的 100.x.x.x | 同左 | 同左 |")
    L.append("| 3. 传文件（可选） | 用 `scp` 往本机发，见下 | 同左 |")
    L.append("| 4. 用完清洗 | `bash clean.sh` | 右键 `clean.bat` → 以管理员身份运行 |")
    L.append("")
    L.append("> 部署完之后屏幕会显示你的 Tailscale IP（形如 `100.x.x.x`）。")
    L.append("> 回到你自己（控制端）的电脑上，用它连进来：")
    if linux_on:
        L.append("> - Linux 被控端：`ssh 你的用户名@100.x.x.x`（走 Tailscale SSH，免密码）")
    if windows_on:
        L.append("> - Windows 被控端：`ssh 你的用户名@100.x.x.x`（OpenSSH 公钥免密）")
    L.append("")
    L.append("## 目录里有什么")
    L.append("")
    L.append("```")
    L.append("deploy.sh / deploy.bat   ★ 部署 —— 只需要这个")
    L.append("clean.sh  / clean.bat    ★ 清洗 —— 用完只需要这个")
    L.append("使用说明.txt              三步极简说明")
    L.append("README.md                本文件")
    L.append("程序/                    实现细节（不用打开）")
    L.append("  ├── linux/             脚本 + 预置二进制 + 你的密钥")
    L.append("  └── windows/")
    L.append("```")
    L.append("")
    L.append("## 本包已预置的内容")
    L.append("")
    L.append(f"- Tailscale Authkey：{'已写入，目标机无需手填' if has_auth else '未预置（运行时会提示你粘贴）'}")
    if windows_on:
        L.append(f"- 控制端 SSH 公钥：{'已写入，免密登录已配好' if has_pub else '未预置（运行时会提示你粘贴）'}")
    L.append(f"- 运行模式：**{'离线（包内自带二进制，对方完全不联网也能装）' if offline else '轻量（包内不含二进制，对方需联网自动下载）'}**")
    if want_7zip:
        L.append("- 便携 7-Zip：已内置（万一连解压工具都没有）")
    L.append("")
    if not offline:
        L.append("> ⚠ 本包是**轻量版**：目标机必须能联网才能自动获取 Tailscale 官方安装包。")
        L.append("> 如果目标机连不上网，请让制包者改用「离线模式」重新生成。")
        L.append("")
    L.append("## 怎么传文件")
    L.append("")
    L.append("**不需要装任何东西** —— 用系统自带的 `scp` 即可（Linux / Windows 都自带）。")
    L.append("")
    L.append("在你自己的（控制端）电脑上执行，把 IP 换成上面显示的那个：")
    L.append("")
    L.append("```bash")
    L.append("scp -r 文件或目录 用户名@100.x.x.x:~/")
    L.append("```")
    L.append("")
    L.append("> 本包已在被控端准备好 OpenSSH（Windows）或用 Tailscale 内置 SSH（Linux），")
    L.append("> 所以是免密的。deploy 跑完后屏幕也会直接打印这条命令。")
    L.append("")
    L.append("")
    L.append("**Q: 部署要管理员权限吗？**  \nA: Linux 不需要（本工具固定跑在用户态 `~/.tailscale`）；"
             "Windows 需要，因为要装服务。")
    L.append("")
    L.append("**Q: 怎么让对方开机就自动连上？**  \nA: 部署时选 `y` 会写 crontab 自启；"
             "Windows 装完即开机自启，无需配置。")
    L.append("")
    L.append("**Q: 清洗之后真的没痕迹了吗？**  \nA: 本机是的（进程/配置/日志/自启全清）。"
             "但你的 Tailscale 后台设备列表里仍会显示这台机器，彻底移除请到 "
             "https://login.tailscale.com/admin/machines 删除该节点。")
    L.append("")
    L.append("**Q: 部署完过几天突然连不上？**  \nA: 可能是 key 过期。到 "
             "https://login.tailscale.com/admin/settings/keys 把这台机器的 Key expiry "
             "设为 `Disable`，然后重跑一次 deploy。")
    L.append("")
    L.append("---")
    L.append("")
    L.append("⚠ 本包内含明文 Tailscale authkey，请只发给你信任的人。")
    with open(os.path.join(pkg_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    log("  ✓ 写入包内 README.md")


def generate(repo_root, linux_on, windows_on, arch, auth, pub,
             out_dir, prefix, fmt, want_7zip=(), offline=True, log=print):
    """fmt: 'tar.gz' | 'zip' | 'both'；want_7zip: 内置便携 7-Zip 的平台集合；
       offline: True=内置二进制(离线可跑) / False=轻量包(目标机联网)"""
    if not (linux_on or windows_on):
        raise ValueError("至少选择一个目标系统")
    platforms = []
    if linux_on:
        platforms.append("linux")
    if windows_on:
        platforms.append("windows")
    want_7zip = tuple(p for p in want_7zip if p in platforms)
    os_tag = "both" if (linux_on and windows_on) else platforms[0]
    mode_tag = "offline" if offline else "slim"
    date = datetime.date.today().strftime("%Y%m%d")
    pkg_name = f"{prefix}-{os_tag}-{mode_tag}-{date}"

    build_root = os.path.join(repo_root, "builder", "_build")
    pkg_dir = os.path.join(build_root, pkg_name)
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir)
    os.makedirs(pkg_dir, exist_ok=True)

    if not auth:
        log("⚠ 未填写 Authkey：目标机运行时会改为交互式粘贴。")
    if windows_on and not pub:
        log("⚠ 未填写控制端公钥：Windows 目标运行时会提示你粘贴。")

    log(f"包名: {pkg_name}")
    assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=want_7zip, offline=offline, log=log)
    write_root_files(pkg_dir, linux_on, windows_on, log=log)
    write_pkg_readme(pkg_dir, linux_on, windows_on, bool(auth),
                     bool(pub and windows_on), want_7zip=want_7zip,
                     offline=offline, log=log)

    made = []
    if fmt in ("tar.gz", "both"):
        made.append(make_targz(pkg_dir, out_dir, pkg_name, log))
    if fmt in ("zip", "both"):
        made.append(make_zip(pkg_dir, out_dir, pkg_name, log))
    return made



# ============================================================ 发送文件(纯逻辑, 无 UI 依赖)
WIN_SCP_EXE = r"C://Windows//System32//OpenSSH//scp.exe"
TS_EXE_PATHS = [
    r"C://Program Files//Tailscale//tailscale.exe",
    "/Applications/Tailscale.app/Contents/MacOS/Tailscale",
    "/usr/local/bin/tailscale",
    "/usr/bin/tailscale",
]


def _find_ts():
    for p in TS_EXE_PATHS:
        if os.path.isfile(p):
            return p
    from shutil import which
    return which("tailscale")


def _find_scp():
    if os.name == "nt" and os.path.isfile(WIN_SCP_EXE):
        return WIN_SCP_EXE
    from shutil import which
    w = which("scp")
    if w:
        return w
    for c in (r"C://Program Files//Git//usr//bin//scp.exe", "/usr/bin/scp"):
        if os.path.isfile(c):
            return c
    return None


def _human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}PB"


def _dir_size(path):
    if os.path.isfile(path):
        try:
            return os.path.getsize(path)
        except OSError:
            return 0
    total = 0
    for root, _d, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _quote_arg(s):
    return f'"{s}"' if (" " in s or "\t" in s) else s


def _zip_dir(d, log=None):
    """目录打成 zip —— Taildrop 只支持文件, 不支持目录。"""
    import zipfile
    d = d.rstrip("/\\")
    out = os.path.join(os.path.dirname(d), f"{os.path.basename(d)}.zip")
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _sub, files in os.walk(d):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, os.path.dirname(d))
                try:
                    z.write(fp, rel.replace(os.sep, "/"))
                    n += 1
                except OSError:
                    pass
    if log:
        log(f"目录已打包: {os.path.basename(out)}  ({n} 个文件)")
    return out


def list_ts_targets(include_self=True):
    """列出 tailnet 里的设备。

    include_self=True 时把本机也列出来(标记 is_self), 因为用户需要看到并
    管理自己这台机器(比如退出 tailnet)。发送时才过滤掉本机。
    """
    ts = _find_ts()
    if not ts:
        return []
    my = set(_ts_my_ips())
    try:
        r = subprocess.run([ts, "status"], capture_output=True, text=True,
                           timeout=20)
    except Exception:  # noqa: BLE001
        return []
    out = []
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        ip = parts[0]
        if not (ip.count(".") == 3 and ip.split(".")[0] == "100"):
            continue
        is_self = ip in my
        if is_self and not include_self:
            continue
        osname = ""
        for pt in parts:
            if pt in ("windows", "linux", "macOS", "iOS", "android"):
                osname = pt
                break
        name = parts[1]
        if is_self:
            name = f"{name}  (本机)"
        out.append({"ip": ip, "name": name, "os": osname,
                    "offline": "offline" in line.lower(), "is_self": is_self})
    return out


def _ts_my_ips():
    """本机的所有 100.x.x.x(用于判断"选中的是不是我")。失败返回空表。"""
    ts = _find_ts()
    if not ts:
        return []
    out = []
    try:
        for flag in ("-4", "-6"):
            r = subprocess.run([ts, "ip", flag], capture_output=True,
                               text=True, timeout=10)
            for ln in (r.stdout or "").split():
                if ln.count(".") == 3 or ":" in ln:
                    out.append(ln)
    except Exception:  # noqa: BLE001
        pass
    return out


def ts_logout_self():
    """把【本机】从 tailnet 摘掉(仅限自己)。返回 (ok, msg)。"""
    ts = _find_ts()
    if not ts:
        return False, "本机没有 tailscale 客户端"
    dbg("[ts] logout self")
    try:
        r = subprocess.run([ts, "logout"], capture_output=True, text=True,
                           timeout=30)
    except Exception as e:  # noqa: BLE001
        dbg_exc("ts logout")
        return False, f"执行失败: {e}"
    if r.returncode == 0:
        return True, (r.stdout or r.stderr or "").strip() or "本机已退出"
    return False, (r.stderr or r.stdout or f"退出码 {r.returncode}").strip()


def ts_admin_url(ip=""):
    """Tailscale 管理后台里删除节点的地址。
    ★ tailscale CLI 只能 logout 自己; 删除【别的】设备必须去后台点。
      所以这里给出直达链接, 并把要处理的 IP 复制好, 让用户粘贴到后台搜索框。"""
    base = "https://login.tailscale.com/admin/machines"
    return base if not ip else base


class _SendTask:
    """一次单向发送: 控制端 -> 被控端。"""

    def __init__(self, targets, peer, method="taildrop", remote_dir="~",
                 user="", log=None):
        self.targets = [t for t in targets if os.path.exists(t)]
        self.peer = (peer or "").strip()
        self.method = method
        self.remote_dir = remote_dir or "~"
        self.user = (user or "").strip()
        self.log = log or (lambda s: None)
        self.proc = None
        self._bundles = []
        self._cancelled = False

    def build_cmd(self):
        if self.method == "taildrop":
            ts = _find_ts()
            if not ts:
                raise RuntimeError(
                    "本机没有 tailscale 客户端。\n"
                    "  请到 https://tailscale.com/download 安装。")
            files = []
            for t in self.targets:
                if os.path.isdir(t):
                    b = _zip_dir(t, self.log)
                    self._bundles.append(b)
                    files.append(b)
                else:
                    files.append(t)
            argv = [ts, "file", "cp", "--verbose"] + files + [self.peer + ":"]
            return argv, " ".join(_quote_arg(a) for a in argv)

        scp = _find_scp()
        if not scp:
            raise RuntimeError(
                "本机没有 scp。Windows: 设置 → 可选功能 → 添加 OpenSSH 客户端。")
        remote = f"{self.user}@{self.peer}" if self.user else self.peer
        argv = [scp, "-r", "-o", "StrictHostKeyChecking=accept-new",
                "-o", "ConnectTimeout=15"] + self.targets + \
               [f"{remote}:{self.remote_dir}"]
        return argv, " ".join(_quote_arg(a) for a in argv)

    def cancel(self):
        self._cancelled = True
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:  # noqa: BLE001
                pass

    def run(self):
        if not self.targets:
            return False, "没有可发送的文件"
        if not self.peer:
            return False, "请选一个被控端"
        try:
            argv, shown = self.build_cmd()
        except (RuntimeError, OSError) as e:
            return False, str(e)

        self.log("执行: " + shown)
        self.log("")
        dbg(f"[send] spawn: {shown}")
        spawn_kw = dict(stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                        stdin=subprocess.DEVNULL, text=True,
                        encoding="utf-8", errors="replace", bufsize=1)
        if os.name == "nt":
            # ★ CREATE_NO_WINDOW: 绝不给子进程弹黑框
            spawn_kw["creationflags"] = (
                subprocess.CREATE_NO_WINDOW
                | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
        try:
            self.proc = subprocess.Popen(argv, **spawn_kw)
        except Exception as e:  # noqa: BLE001
            dbg_exc("send Popen")
            return False, f"启动失败: {e}"
        dbg(f"[send] spawned pid={getattr(self.proc, 'pid', '?')}")

        import time as _t
        start = _t.time()
        total = sum(_dir_size(t) for t in self.targets)
        self.log(f"共 {len(self.targets)} 个目标, 合计 {_human(total)}")
        self.log("")
        for line in self.proc.stdout:
            if self._cancelled:
                return False, "已取消"
            line = line.rstrip()
            if line:
                self.log("  " + line)
        rc = self.proc.wait()
        cost = _t.time() - start
        for b in self._bundles:          # 清理临时 zip
            try:
                os.remove(b)
            except OSError:
                pass
        if rc == 0:
            self.log("")
            self.log(f"✅ 发送完成 ({cost:.1f} 秒)")
            return True, f"发送完成, 用时 {cost:.1f} 秒"
        self.log("")
        tail = ("  (scp 模式: 对方需已跑过 deploy)" if self.method == "scp"
                else "  (确认对方在线: tailscale status 看它的状态)")
        msg = f"退出码 {rc}。{tail}\n看上方日志里的具体报错。"
        self.log(f"❌ 失败: {msg}")
        return False, msg


# ============================================================ 图形界面
# 字号策略: 基准 15px(比常规 UI 大一档), 辅助文字 13px, 标题 21px。
# 所有尺寸同步放大, 避免"字小 + 控件挤"导致的可读性问题。
STYLE = """
QWidget { background:#EEF2F8; color:#16202E; font-size:18px; }
QGroupBox {
    background:#FFFFFF; border:1px solid #D3DBE8; border-radius:12px;
    margin-top:18px; padding:20px 16px 16px 16px;
    font-weight:bold; font-size:19px; color:#1E3A5F;
}
QGroupBox::title {
    subcontrol-origin: margin; left:14px; padding:2px 8px;
    color:#2C5A8C; background:#E8EFFA; border-radius:6px;
}
QLabel { background:transparent; }
QLabel#hint { color:#5A6B85; font-size:15px; }
QLabel#ok { color:#1B7F3B; font-size:15px; }
QLabel#bad { color:#C0392B; font-size:15px; }
QLabel#summary {
    background:#E8F0FE; border:1px solid #B9CDF5; border-radius:8px;
    padding:12px 14px; color:#1B3A6B; font-size:16px;
}
QLineEdit, QPlainTextEdit, QComboBox {
    background:#FFFFFF; border:1px solid #B8C4D6; border-radius:7px;
    padding:11px 13px; font-size:18px; selection-background-color:#2F6FED;
    min-height:26px;
}
QPlainTextEdit { line-height:1.5; }
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border:2px solid #2F6FED; }
QComboBox::drop-down { border:none; width:26px; }
QComboBox QAbstractItemView {
    background:#FFFFFF; border:1px solid #B8C4D6; font-size:18px;
    selection-background-color:#2F6FED; selection-color:#FFFFFF; padding:4px;
}
QRadioButton, QCheckBox { background:transparent; spacing:11px; font-size:18px; padding:5px 0; }
QPushButton {
    background:#E4EAF4; border:1px solid #B8C4D6; border-radius:7px;
    padding:11px 20px; color:#1E3A5F; font-size:16px; font-weight:bold;
}
QPushButton:hover { background:#D5DFF0; border-color:#93A8C6; }
QPushButton:pressed { background:#C6D4E8; }
QPushButton:disabled { color:#9AA8BC; background:#EEF2F7; border-color:#DDE4EE; }
QPushButton#primary {
    background:#2F6FED; color:#FFFFFF; border:none;
    font-size:19px; font-weight:bold; padding:15px 36px; border-radius:10px;
}
QPushButton#primary:hover { background:#2560DB; }
QPushButton#primary:pressed { background:#1E52BC; }
QPushButton#ghost {
    background:#FFFFFF; color:#2F6FED; border:2px solid #2F6FED;
    font-size:16px; padding:10px 18px;
}
QPushButton#ghost:hover { background:#EDF3FE; }
QPlainTextEdit#log {
    background:#0F1724; color:#D8E4F5; border:1px solid #24334A; border-radius:8px;
    font-family:Consolas,monospace; font-size:15px; padding:12px;
}
QLabel#tip  { background:#FFF7E6; border:1px solid #F0D9A8; border-radius:8px;
              padding:10px 12px; color:#8A5A00; font-size:14px; }
QGroupBox#drop {
    background:#FBFCFE; border:2px dashed #A9BEDC; border-radius:10px;
}
QPushButton#tab {
    background:#E4EAF4; border:1px solid #C6D0E0; border-radius:8px;
    padding:10px 20px; color:#2B3A55; font-size:16px;
}
QPushButton#tab:hover { background:#D5DFF0; }
QPushButton#tab:checked {
    background:#2F6FED; color:#FFFFFF; border-color:#2F6FED;
}
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical { background:#E3E9F2; width:12px; border-radius:6px; }
QScrollBar::handle:vertical { background:#B4C2D6; border-radius:6px; min-height:40px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QFrame#head { background:#FFFFFF; border:1px solid #D3DBE8; border-radius:12px; }
QFrame#foot { background:#FFFFFF; border:1px solid #D3DBE8; border-radius:12px; }
"""


def _label(text, obj=""):
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(True)
    return l


if HAS_QT:
    class MainWindow(QMainWindow):
        # ★ 子线程 -> 主线程的唯一通道。
        #   在子线程里直接碰 QWidget/QMessageBox 是 PyQt5 崩溃的头号原因,
        #   所以子线程只发这个信号, 一切 UI 操作都在主线程做。
        done_signal = pyqtSignal(object, object, object)   # (tag, ok, msg)

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Tailscale-Remote  工具箱")
            self.setStyleSheet(STYLE)
            self.resize(1120, 1080)
            self.setMinimumSize(960, 800)
            app = QApplication.instance()
            if app is not None:
                app.setFont(QFont("Microsoft YaHei", 13))
            self.cfg = load_config()    # 配置(记忆/历史) 先加载, UI 里要用
            # 子线程完成的信号 -> 主线程统一处理 UI (线程安全的关键)
            self.done_signal.connect(self._on_done_signal)
            self._build_ui()
            self._restore_config()      # 恢复上次选择(含记忆的密钥)
            self._scan_existing_key()

        def closeEvent(self, ev):
            """关闭窗口时再存一次, 保证选择不丢。"""
            try:
                save_config(self._snapshot_config())
            except Exception:  # noqa: BLE001
                pass
            ev.accept()

        # ---------------- UI 构建 ----------------
        def _mk_page(self, title):
            """新建一个带滚动区的页签, 返回 (QScrollArea, 内容布局)。"""
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            bd = QWidget()
            lay = QVBoxLayout(bd)
            lay.setContentsMargins(16, 14, 16, 14)
            lay.setSpacing(14)
            sc.setWidget(bd)
            self.tabs.addTab(sc, title)
            return sc, lay

        def _build_ui(self):
            outer = QWidget()
            self.setCentralWidget(outer)
            root = QVBoxLayout(outer)
            root.setContentsMargins(0, 0, 0, 0)
            root.setSpacing(0)

            # ---- 标题栏 ----
            head = QFrame()
            head.setObjectName("head")
            hl = QVBoxLayout(head)
            hl.setContentsMargins(18, 13, 18, 13)
            t1 = QLabel("Tailscale-Remote  工具箱")
            t1.setStyleSheet("font-size:26px;font-weight:bold;color:#12263F;background:transparent;")
            t2 = QLabel("左页一键生成部署包（对方解压后只跑 deploy）；右页把文件单向发到对方（对方零配置）。")
            t2.setObjectName("hint")
            hl.addWidget(t1)
            hl.addWidget(t2)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(14, 12, 14, 0)
            wl.addWidget(head)
            root.addWidget(wrap)

            # ---- 两个功能页(用按钮切换) ----
            _tf = QFont("Microsoft YaHei", 11)
            _tf.setBold(True)
            self.tabs = QWidget()          # 占位, 实际靠下面两个按钮切换
            self.btn_group = QButtonGroup(self)
            self.btn_group.setExclusive(True)
            hb = QHBoxLayout()
            hb.setContentsMargins(16, 0, 16, 0)
            hb.setSpacing(8)
            self.btn_tab_gen = QPushButton("  生成部署包  ")
            self.btn_tab_send = QPushButton("  发送文件到被控端  ")
            for i, b in enumerate([self.btn_tab_gen, self.btn_tab_send]):
                b.setCheckable(True)
                b.setMinimumHeight(46)
                b.setObjectName("tab")
                b.setFont(_tf)
                self.btn_group.addButton(b, i)
                hb.addWidget(b)
            hb.addStretch(1)
            wrap_tab = QWidget()
            lt = QVBoxLayout(wrap_tab)
            lt.setContentsMargins(0, 0, 0, 0)
            lt.addLayout(hb)
            root.addWidget(wrap_tab)
            self.btn_tab_gen.setChecked(True)

            # 两个页面容器(各自带滚动区)
            self.page_gen = QWidget()
            self.scroll_gen = QScrollArea()
            self.scroll_gen.setWidgetResizable(True)
            self.scroll_gen.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.form = QVBoxLayout(self.page_gen)
            self.form.setContentsMargins(16, 14, 16, 14)
            self.form.setSpacing(14)
            self.scroll_gen.setWidget(self.page_gen)

            self.page_send = QWidget()
            self.scroll_send = QScrollArea()
            self.scroll_send.setWidgetResizable(True)
            self.scroll_send.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.form_send = QVBoxLayout(self.page_send)
            self.form_send.setContentsMargins(16, 14, 16, 14)
            self.form_send.setSpacing(14)
            self.scroll_send.setWidget(self.page_send)

            wrap2 = QWidget()
            wl2 = QVBoxLayout(wrap2)
            wl2.setContentsMargins(14, 4, 14, 0)
            wl2.addWidget(self.scroll_gen)
            wl2.addWidget(self.scroll_send)
            root.addWidget(wrap2, 1)
            self.scroll_send.setVisible(False)
            self.btn_tab_gen.clicked.connect(lambda: self._switch_page(0))
            self.btn_tab_send.clicked.connect(lambda: self._switch_page(1))

            # 页1 内容
            self._card_target()
            self._card_mode()
            self._card_keys()
            self._card_output()
            self.form.addStretch(1)
            g_log = QGroupBox("运行日志")
            lv = QVBoxLayout(g_log)
            self.txt_log = QPlainTextEdit()
            self.txt_log.setObjectName("log")
            self.txt_log.setReadOnly(True)
            self.txt_log.setMinimumHeight(190)
            lv.addWidget(self.txt_log)
            self.form.addWidget(g_log)

            # 页2 内容(发送文件)
            self._card_send()

            # ---- 底部按钮(随页签切换) ----
            foot = QFrame()
            foot.setObjectName("foot")
            fl = QHBoxLayout(foot)
            fl.setContentsMargins(16, 9, 16, 9)
            fl.setSpacing(10)

            # 页1 按钮组
            self.wid_gen = QWidget()
            gl = QHBoxLayout(self.wid_gen)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(10)
            self.btn_gen = QPushButton("生成部署包")
            self.btn_gen.setObjectName("primary")
            self.btn_gen.setMinimumHeight(52)
            self.btn_gen.clicked.connect(self.on_generate)
            self.btn_open = QPushButton("打开输出目录")
            self.btn_open.clicked.connect(self.on_open)
            self.btn_clear = QPushButton("清空日志")
            self.btn_clear.clicked.connect(lambda: self.txt_log.clear())
            gl.addWidget(self.btn_gen)
            gl.addWidget(self.btn_open)
            gl.addWidget(self.btn_clear)
            fl.addWidget(self.wid_gen)

            # 页2 按钮组
            self.wid_send = QWidget()
            sl = QHBoxLayout(self.wid_send)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(10)
            self.btn_send = QPushButton("开始发送")
            self.btn_send.setObjectName("primary")
            self.btn_send.setMinimumHeight(52)
            self.btn_send.clicked.connect(self.on_send)
            self.btn_stop = QPushButton("停止")
            self.btn_stop.clicked.connect(self.on_stop)
            self.btn_sendclr = QPushButton("清空列表")
            self.btn_sendclr.clicked.connect(self.on_send_clear)
            sl.addWidget(self.btn_send)
            sl.addWidget(self.btn_stop)
            sl.addWidget(self.btn_sendclr)
            fl.addWidget(self.wid_send)

            fl.addStretch(1)
            self.lbl_status = _label("")
            fl.addWidget(self.lbl_status)
            wrap3 = QWidget()
            wl3 = QVBoxLayout(wrap3)
            wl3.setContentsMargins(14, 8, 14, 12)
            wl3.addWidget(foot)
            root.addWidget(wrap3)
            self._switch_page(0)

        def _card_target(self):
            g = QGroupBox("①  目标环境 —— 要连哪台机器？")
            v = QVBoxLayout(g)
            v.setSpacing(12)
            # 用下拉框而不是 radio: 高 DPI / 滚动区里 radio 容易点不中而"看起来选了别的"
            h1 = QHBoxLayout()
            h1.setSpacing(12)
            h1.addWidget(_label("目标平台"))
            self.cmb_os = QComboBox()
            self.cmb_os.addItems(["Linux 被控端", "Windows 被控端", "双端都要 (Linux + Windows)"])
            self.cmb_os.setCurrentIndex(2)
            self.cmb_os.setMinimumWidth(300)
            self.cmb_os.currentIndexChanged.connect(self._sync_platform_state)
            h1.addWidget(self.cmb_os)
            h1.addSpacing(28)
            h1.addWidget(_label("Linux 架构"))
            self.cmb_arch = QComboBox()
            self.cmb_arch.addItems(["amd64 (x86_64)", "arm64 (aarch64)"])
            self.cmb_arch.setFixedWidth(210)
            h1.addWidget(self.cmb_arch)
            h1.addStretch(1)
            v.addLayout(h1)
            h1b = QHBoxLayout()
            h1b.setSpacing(10)
            wlb = _label("Windows 架构")
            wlb.setWordWrap(False)
            h1b.addWidget(wlb)
            h1b.addWidget(_label("amd64（Windows 10 / 11 仅此）", "hint"))
            h1b.addStretch(1)
            v.addLayout(h1b)
            self.form.addWidget(g)

        def _card_mode(self):
            g = QGroupBox("②  运行模式 —— 对方机器能不能上网？")
            v = QVBoxLayout(g)
            v.setSpacing(10)
            h = QHBoxLayout()
            h.setSpacing(12)
            h.addWidget(_label("运行模式"))
            self.cmb_mode = QComboBox()
            self.cmb_mode.addItems([
                "离线（推荐）· 自带二进制，对方不联网也能装",
                "轻量 · 不带二进制(约20KB)，对方需联网自动下载",
            ])
            self.cmb_mode.setMinimumWidth(470)
            self.cmb_mode.currentIndexChanged.connect(self._sync_mode_hint)
            h.addWidget(self.cmb_mode)
            h.addStretch(1)
            v.addLayout(h)
            self.lbl_mode = _label("", "hint")
            v.addWidget(self.lbl_mode)
            self._sync_mode_hint()
            self.form.addWidget(g)

        def _card_keys(self):
            g = QGroupBox("③  密钥 —— 填上就能免密直连")
            v = QVBoxLayout(g)
            v.setSpacing(9)

            top1 = QHBoxLayout()
            lbl_a = _label("Tailscale Authkey")
            lbl_a.setWordWrap(False)
            top1.addWidget(lbl_a)
            top1.addStretch(1)
            b_open = QPushButton("① 去后台生成 authkey")
            b_open.setObjectName("ghost")
            b_open.clicked.connect(self._open_keys_url)
            top1.addWidget(b_open)
            v.addLayout(top1)

            self.ed_auth = QLineEdit()
            self.ed_auth.setEchoMode(QLineEdit.Password)
            self.ed_auth.setPlaceholderText("tskey-auth-…  （可带 TS_AUTHKEY= 前缀；留空则让对方手填）")
            v.addWidget(self.ed_auth)

            h2 = QHBoxLayout()
            h2.setSpacing(8)
            b_load = QPushButton("从文件导入")
            b_load.clicked.connect(lambda: self._load_file(self.ed_auth))
            h2.addWidget(b_load)
            b_show = QPushButton("显示 / 隐藏")
            b_show.setObjectName("ghost")
            b_show.clicked.connect(self._toggle_auth_visible)
            h2.addWidget(b_show)
            h2.addSpacing(18)
            self.cb_remember = QCheckBox("记住 authkey（明文存本机）")
            self.cb_remember.toggled.connect(self._on_choice_changed)
            h2.addWidget(self.cb_remember)
            h2.addStretch(1)
            v.addLayout(h2)
            self.lbl_auth = _label("状态：未填写（目标机运行时会提示手动粘贴）", "hint")
            v.addWidget(self.lbl_auth)
            v.addWidget(_label(
                "生成方法：登录 Tailscale 后台 → Settings → Keys → Generate auth key"
                "（建议勾 Reusable、过期设 Disable），复制以 tskey-auth- 开头的那串。", "hint"))

            v.addSpacing(5)

            top2 = QHBoxLayout()
            lbl2 = _label("控制端 SSH 公钥（仅 Windows 需要）")
            lbl2.setWordWrap(False)          # 保持单行, 不被按钮挤换行
            top2.addWidget(lbl2)
            top2.addStretch(1)
            b_gen = QPushButton("② 一键生成密钥对")
            b_gen.setObjectName("ghost")
            b_gen.clicked.connect(self._make_keypair)
            b_force = QPushButton("重生成")
            b_force.clicked.connect(lambda: self._make_keypair(force=True))
            top2.addWidget(b_gen)
            top2.addWidget(b_force)
            v.addLayout(top2)

            self.ed_pub = QPlainTextEdit()
            self.ed_pub.setPlaceholderText(
                "ssh-ed25519 AAAA…\n"
                "点上方『一键在本机生成』最省事 —— 自动生成并把公钥填到这里；\n"
                "也可以从 控制端电脑的 ~/.ssh/id_ed25519.pub 复制整行粘进来。")
            self.ed_pub.setMaximumHeight(104)
            v.addWidget(self.ed_pub)
            self.lbl_pub = _label("状态：未提供（Windows 目标机会被提示粘贴，或现场自动生成）", "hint")
            v.addWidget(self.lbl_pub)
            self.form.addWidget(g)

        def _card_output(self):
            g = QGroupBox("④  打包选项")
            v = QVBoxLayout(g)
            v.setSpacing(9)
            h1 = QHBoxLayout()
            h1.setSpacing(10)
            h1.addWidget(_label("压缩格式"))
            self.cmb_fmt = QComboBox()
            self.cmb_fmt.addItems([".tar.gz  (Linux 原生)", ".zip  (Windows 原生)", "两种都生成"])
            self.cmb_fmt.setCurrentIndex(2)
            self.cmb_fmt.setFixedWidth(230)
            h1.addWidget(self.cmb_fmt)
            h1.addSpacing(20)
            h1.addWidget(_label("包名前缀"))
            self.ed_prefix = QLineEdit("tailscale-remote")
            self.ed_prefix.setFixedWidth(230)
            h1.addWidget(self.ed_prefix)
            h1.addStretch(1)
            v.addLayout(h1)

            h2 = QHBoxLayout()
            h2.setSpacing(10)
            h2.addWidget(_label("输出目录"))
            self.ed_out = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop"))
            h2.addWidget(self.ed_out, 1)
            b_out = QPushButton("浏览…")
            b_out.clicked.connect(self._pick_out)
            h2.addWidget(b_out)
            v.addLayout(h2)

            h3 = QHBoxLayout()
            h3.setSpacing(18)
            self.cb_7z_lin = QCheckBox("Linux 内置 7-Zip (+2.7MB)")
            self.cb_7z_win = QCheckBox("Windows 内置 7-Zip (+0.6MB)")
            h3.addWidget(self.cb_7z_lin)
            h3.addWidget(self.cb_7z_win)
            h3.addStretch(1)
            v.addLayout(h3)
            v.addWidget(_label(
                "连解压软件都没有的机器才需要勾 —— 勾上包里会带 7zz / 7za.exe。"
                "首次使用请先运行 python builder/fetch_7zip.py 拉取二进制。", "hint"))
            v.addSpacing(6)
            self.lbl_summary = _label("", "summary")
            v.addWidget(self.lbl_summary)
            self.form.addWidget(g)

        # =============== 第二页: 发送文件到被控端 ===============
        def _card_send(self):
            """单向: 控制端 -> 被控端。被控端零改动(走 Tailscale Taildrop)。"""
            self.send_files = []
            self._send_task = None

            # --- ① 发给谁 ---
            g = QGroupBox("①  发给谁")
            v = QVBoxLayout(g)
            v.setSpacing(11)
            h1 = QHBoxLayout()
            h1.setSpacing(10)
            h1.addWidget(_label("被控端"))
            self.cmb_speer = QComboBox()
            self.cmb_speer.setMinimumWidth(460)
            self.cmb_speer.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
            self.cmb_speer.setToolTip("从 tailnet 里选; 点右边的刷新")
            h1.addWidget(self.cmb_speer)
            b_rf = QPushButton("刷新列表")
            b_rf.setObjectName("ghost")
            b_rf.clicked.connect(self.on_refresh_peers)
            h1.addWidget(b_rf)
            h1.addStretch(1)
            v.addLayout(h1)

            h2 = QHBoxLayout()
            h2.setSpacing(10)
            h2.addWidget(_label("方式"))
            self.cmb_smethod = QComboBox()
            self.cmb_smethod.addItems([
                "Taildrop（推荐）· 被控端零配置，只需 tailscale file get",
                "scp · 被控端需已跑 deploy（SSH 已就绪）",
            ])
            self.cmb_smethod.setMinimumWidth(430)
            self.cmb_smethod.currentIndexChanged.connect(self._on_smethod)
            h2.addWidget(self.cmb_smethod)
            h2.addStretch(1)
            v.addLayout(h2)

            # scp 专用
            self.row_scp = QWidget()
            rl = QHBoxLayout(self.row_scp)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            rl.addWidget(_label("用户名"))
            self.ed_suser = QLineEdit()
            self.ed_suser.setPlaceholderText("被控端上的用户名")
            self.ed_suser.setFixedWidth(190)
            rl.addWidget(self.ed_suser)
            rl.addWidget(_label("落到"))
            self.ed_sdir = QLineEdit("~")
            self.ed_sdir.setFixedWidth(180)
            rl.addWidget(self.ed_sdir)
            rl.addStretch(1)
            v.addWidget(self.row_scp)

            self.lbl_stip = _label("", "tip")
            v.addWidget(self.lbl_stip)

            # --- 设备管理表: 复制 IP / 删除节点 ---
            self.tbl_peers = QTableWidget(0, 4)
            self.tbl_peers.setHorizontalHeaderLabels(
                ["IP (双击复制)", "名称", "系统", "状态"])
            self.tbl_peers.verticalHeader().setVisible(False)
            self.tbl_peers.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.tbl_peers.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tbl_peers.setMaximumHeight(190)
            hh = self.tbl_peers.horizontalHeader()
            hh.setSectionResizeMode(0, QHeaderView.Stretch)
            hh.setSectionResizeMode(1, QHeaderView.Stretch)
            self.tbl_peers.doubleClicked.connect(self._on_tbl_dblclick)
            v.addWidget(self.tbl_peers)

            h3 = QHBoxLayout()
            h3.setSpacing(10)
            b_cp = QPushButton("复制选中 IP")
            b_cp.setObjectName("ghost")
            b_cp.clicked.connect(self.on_copy_ip)
            h3.addWidget(b_cp)
            b_del = QPushButton("删除该节点")
            b_del.setObjectName("ghost")
            b_del.clicked.connect(self.on_delete_peer)
            h3.addWidget(b_del)
            b_get = QPushButton("复制取文件命令")
            b_get.setObjectName("ghost")
            b_get.clicked.connect(self.on_copy_getcmd)
            h3.addWidget(b_get)
            h3.addStretch(1)
            self.lbl_manage = _label("", "hint")
            h3.addWidget(self.lbl_manage)
            v.addLayout(h3)
            self.form_send.addWidget(g)

            # --- ② 发什么 ---
            g2 = QGroupBox("②  发什么 —— 把文件/文件夹拖进来")
            v2 = QVBoxLayout(g2)
            v2.setSpacing(11)
            self.drop = QGroupBox("")
            self.drop.setObjectName("drop")
            self.drop.setAcceptDrops(True)
            dv = QVBoxLayout(self.drop)
            self.lbl_sfiles = _label("把文件或文件夹拖到这里", "hint")
            self.lbl_sfiles.setAlignment(Qt.AlignCenter)
            dv.addWidget(self.lbl_sfiles)
            hh = QHBoxLayout()
            hh.addStretch(1)
            b1 = QPushButton("选择文件…")
            b1.setObjectName("ghost")
            b1.clicked.connect(self.on_spick)
            hh.addWidget(b1)
            b2 = QPushButton("选择文件夹…")
            b2.setObjectName("ghost")
            b2.clicked.connect(self.on_spick_dir)
            hh.addWidget(b2)
            hh.addStretch(1)
            dv.addLayout(hh)
            v2.addWidget(self.drop)
            self.lbl_ssum = _label("", "sum")
            v2.addWidget(self.lbl_ssum)
            self.form_send.addWidget(g2)

            # --- ③ 日志 ---
            g3 = QGroupBox("③  传输日志")
            v3 = QVBoxLayout(g3)
            self.txt_slog = QPlainTextEdit()
            self.txt_slog.setObjectName("log")
            self.txt_slog.setReadOnly(True)
            self.txt_slog.setMinimumHeight(190)
            v3.addWidget(self.txt_slog)
            self.form_send.addWidget(g3)
            self.form_send.addStretch(1)
            self._on_smethod()

        # ---------- 页面切换 ----------
        def _switch_page(self, idx):
            on_gen = (idx == 0)
            self.scroll_gen.setVisible(on_gen)
            self.scroll_send.setVisible(not on_gen)
            self.wid_gen.setVisible(on_gen)
            self.wid_send.setVisible(not on_gen)
            self.lbl_status.setText("")

        # ---------- 拖拽(两个页共用一套) ----------
        def dragEnterEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dragMoveEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dropEvent(self, ev):
            paths = [u.toLocalFile() for u in ev.mimeData().urls()]
            self.add_send_files([p for p in paths if os.path.exists(p)])
            ev.acceptProposedAction()

        def on_spick(self):
            fs, _ = QFileDialog.getOpenFileNames(self, "选择要发送的文件")
            if fs:
                self.add_send_files(fs)

        def on_spick_dir(self):
            d = QFileDialog.getExistingDirectory(self, "选择要发送的文件夹")
            if d:
                self.add_send_files([d])

        def add_send_files(self, paths):
            for p in paths:
                if p not in self.send_files:
                    self.send_files.append(p)
            self._update_ssum()

        def on_send_clear(self):
            self.send_files = []
            self._update_ssum()

        def _update_ssum(self):
            if not hasattr(self, "lbl_ssum"):
                return
            if not self.send_files:
                self.lbl_sfiles.setText("把文件或文件夹拖到这里")
                self.lbl_ssum.setText("还没选文件")
                return
            total = sum(_dir_size(f) for f in self.send_files)
            names = [os.path.basename(f.rstrip("/\\")) for f in self.send_files[:4]]
            more = f" 等 {len(self.send_files)} 个" if len(self.send_files) > 4 else ""
            self.lbl_sfiles.setText("、".join(names) + more)
            where = ("对方用 tailscale file get 取走"
                     if self.cmb_smethod.currentIndex() == 0
                     else f"落到对方 {self.ed_sdir.text() or '~'}")
            self.lbl_ssum.setText(
                f"共 {len(self.send_files)} 个目标, 合计 {_human(total)}  →  {where}")

        def _on_smethod(self):
            is_scp = self.cmb_smethod.currentIndex() == 1
            self.row_scp.setVisible(is_scp)
            self.lbl_stip.setText(
                "对方机器上执行这一句就能取走:   tailscale file get"
                if not is_scp else
                "对方需已跑过 deploy(SSH 就绪)。Linux 走 Tailscale SSH 免密, "
                "Windows 走公钥免密。")
            self._update_ssum()

        # ---------- 目标列表 ----------
        def on_refresh_peers(self):
            keep = self.cmb_speer.currentText()
            self.cmb_speer.blockSignals(True)
            self.cmb_speer.clear()
            peers = list_ts_targets()
            for pr in peers:
                tag = "（离线）" if pr["offline"] else ""
                osname = ("· " + pr["os"]) if pr["os"] else ""
                self.cmb_speer.addItem(
                    f"{pr['ip']}  {pr['name']}{osname}{tag}", pr)
            for h in self.cfg.get("send_history", []):
                if h.get("ip") and not any(x["ip"] == h["ip"] for x in peers):
                    self.cmb_speer.addItem(f"{h['ip']}   （历史）", h)
            if not peers and not self.cfg.get("send_history"):
                self.cmb_speer.addItem("", None)
            self.cmb_speer.blockSignals(False)
            if keep:
                for i in range(self.cmb_speer.count()):
                    if keep in (self.cmb_speer.itemText(i) or ""):
                        self.cmb_speer.setCurrentIndex(i)
                        break
            self._fill_peer_table(peers)
            self.lbl_status.setText(f"找到 {len(peers)} 台设备")
            dbg(f"[peers] refreshed, {len(peers)} devices")

        def _fill_peer_table(self, peers):
            """把设备列表填进表格。"""
            try:
                self.tbl_peers.setRowCount(0)
                for i, pr in enumerate(peers):
                    self.tbl_peers.insertRow(i)
                    st = "离线" if pr["offline"] else "在线"
                    if pr.get("is_self"):
                        st = "本机"
                    for col, txt in enumerate(
                            [pr["ip"], pr["name"], pr["os"] or "-", st]):
                        it = QTableWidgetItem(txt)
                        if col == 3:
                            if pr.get("is_self"):
                                it.setForeground(QColor("#2F6FED"))
                            elif not pr["offline"]:
                                it.setForeground(QColor("#1B7F3B"))
                        self.tbl_peers.setItem(i, col, it)
            except Exception:  # noqa: BLE001
                dbg_exc("fill_peer_table")

        def _selected_peer(self):
            """取表格里选中那行的设备信息(没有就回退到下拉框)。"""
            r = self.tbl_peers.currentRow()
            if r >= 0:
                it = self.tbl_peers.item(r, 0)
                ip = it.text() if it else ""
                if ip:
                    return {"ip": ip,
                            "name": (self.tbl_peers.item(r, 1).text()
                                      if self.tbl_peers.item(r, 1) else ""),
                            "os": (self.tbl_peers.item(r, 2).text()
                                   if self.tbl_peers.item(r, 2) else "")}
            return None

        def _clip(self, text, what):
            """复制到剪贴板并给视觉反馈。"""
            try:
                QGuiApplication.clipboard().setText(text)
                self.lbl_manage.setText(f"✓ 已复制{what}: {text}")
                self.lbl_status.setText(f"已复制{what}")
                dbg(f"[copy] {what} = {text}")
                return True
            except Exception:  # noqa: BLE001
                dbg_exc("clipboard")
                self.lbl_manage.setText(f"[X] 复制失败, 请手动复制: {text}")
                return False

        def _on_tbl_dblclick(self, idx):
            r = idx.row()
            it = self.tbl_peers.item(r, 0)
            if not it:
                return
            self._clip(it.text(), "IP")
            if it.text() in _ts_my_ips():
                self.lbl_manage.append("  (本机, 不能作为发送目标)")
                return
            # 双击同时选中它作为发送目标
            for i in range(self.cmb_speer.count()):
                d = self.cmb_speer.itemData(i)
                if isinstance(d, dict) and d.get("ip") == it.text():
                    self.cmb_speer.setCurrentIndex(i)
                    break

        def on_copy_ip(self):
            pr = self._selected_peer() or {"ip": self.current_peer_ip()}
            ip = pr.get("ip", "")
            if not ip:
                QMessageBox.warning(self, "没选中", "请先在表格里点一行")
                return
            self._clip(ip, "IP")

        def on_copy_getcmd(self):
            """一键复制『让对方取文件』的命令。"""
            cmd = "tailscale file get ."
            self._clip(cmd, "取文件命令")

        def on_delete_peer(self):
            """删除设备节点。

            ★ 事实: tailscale CLI 只能 logout【自己】, 删除别的设备必须去
              Tailscale 管理后台点。所以这里做两件事:
              1) 选中的是本机 -> 直接 logout, 一步到位
              2) 选中的是别的设备 -> 复制 IP + 打开后台 machines 页(可搜索)
            """
            pr = self._selected_peer() or {"ip": self.current_peer_ip(),
                                            "name": ""}
            ip = pr.get("ip", "")
            if not ip:
                QMessageBox.warning(self, "没选中", "请先在表格里点一行设备")
                return
            nm = pr.get("name", "")

            # ---- 本机 ----
            if ip in _ts_my_ips():
                r = QMessageBox.question(
                    self, "退出本机",
                    f"这 IP ({ip}) 是【你自己这台电脑】。\n\n"
                    "确定要让本机退出 tailnet 吗?\n"
                    "退出后本机的 Tailscale 通道会断开, 需要重新授权才能用。")
                if r != QMessageBox.Yes:
                    return
                dbg(f"[delete] logout SELF ip={ip}")
                ok, msg = ts_logout_self()
                if ok:
                    self.lbl_manage.setText("✓ 本机已退出 tailnet")
                    self.lbl_status.setText("本机已退出")
                    QMessageBox.information(self, "已退出",
                                            f"本机已退出 tailnet。\n\n{msg}")
                    self.on_refresh_peers()
                else:
                    dbg(f"[delete] logout FAILED msg={msg}")
                    self.lbl_manage.setText(f"[X] 退出失败: {msg}")
                    QMessageBox.warning(self, "退出失败", msg)
                return

            # ---- 别的设备: 只能去后台删 ----
            r = QMessageBox.question(
                self, "删除其他设备",
                f"要移除这台设备吗?\n\n"
                f"  名称: {nm or '(未知)'}\n"
                f"  IP  : {ip}\n\n"
                "tailscale 命令行【只能退出本机】, 删除别的设备要在管理后台点。\n"
                "我现在帮你做两件事:\n"
                "  1. 复制该 IP  (方便你在后台搜索框粘贴)\n"
                "  2. 打开 Tailscale 后台的 machines 页\n\n"
                "确定继续吗?")
            if r != QMessageBox.Yes:
                return
            self._clip(ip, "IP")
            dbg(f"[delete] open admin console for {ip}")
            try:
                QDesktopServices.openUrl(QUrl(ts_admin_url()))
            except Exception:  # noqa: BLE001
                dbg_exc("open admin url")
            self.lbl_manage.setText(
                f"已在后台打开, 搜索 {ip} 点 ... → Delete 即可")
            self.lbl_status.setText("已跳转后台")
            QMessageBox.information(
                self, "已打开管理后台",
                f"IP 已复制: {ip}\n\n"
                "在后台的 machines 列表里搜索这个 IP, "
                "点右侧 ... → Delete 即可移除。\n\n"
                "后台地址: " + ts_admin_url())

        def current_peer_ip(self):
            d = self.cmb_speer.currentData()
            if isinstance(d, dict):
                return d.get("ip", "")
            if isinstance(d, str):
                return d
            # ★ 空下拉框 / 空文本时 .split() 会返回 [] -> [0] 越界。
            #   这在"还没点刷新列表"时就会命中。
            parts = (self.cmb_speer.currentText() or "").split()
            return parts[0].strip() if parts else ""

        # ---------- 发送 ----------
        def on_send(self):
            ip = self.current_peer_ip()
            if not ip:
                QMessageBox.warning(self, "没选被控端",
                                    "点『刷新列表』选一台设备。\n"
                                    "若对方不在列表里, 可直接在 IP 框里手输 100.x.x.x")
                return
            if not self.send_files:
                QMessageBox.warning(self, "没有文件", "请先把要发的文件拖进来")
                return
            if ip in _ts_my_ips():
                QMessageBox.warning(
                    self, "目标是自己",
                    f"{ip} 是本机的 IP。\n\n"
                    "Taildrop 需要发给【别的】设备。\n"
                    "请在表格里选一台别的机器。")
                dbg(f"[send] blocked: target is self ({ip})")
                return
            method = "scp" if self.cmb_smethod.currentIndex() == 1 else "taildrop"
            hist = [h for h in self.cfg.get("send_history", [])
                    if h.get("ip") != ip]
            d = self.cmb_speer.currentData()
            hist.insert(0, {"ip": ip, "name": d.get("name", "") if isinstance(d, dict) else ""})
            self.cfg["send_history"] = hist[:12]
            save_config(self.cfg)

            self.txt_slog.clear()
            self._slog(f"目标: {ip}")
            self._slog(f"方式: {'scp' if method == 'scp' else 'Taildrop'}")
            self._slog("")
            self.btn_send.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.lbl_status.setText("发送中…")

            import threading
            t = _SendTask(self.send_files, ip, method,
                          remote_dir=self.ed_sdir.text() or "~",
                          user=self.ed_suser.text().strip(),
                          log=self._slog)
            self._send_task = t
            threading.Thread(target=self._send_worker, args=(t,),
                             daemon=True).start()

        def _send_worker(self, t):
            """子线程: 只跑传输, 结果通过信号回主线程。绝不碰任何 UI 控件。

            ★ 这里必须把【所有】异常都吃掉并转成信号: 子线程里任何
              未捕获异常都会导致进程静默退出, 表现为"发个文件程序就没了"。
            """
            ok, msg = False, "发送线程未知错误"
            try:
                ok, msg = t.run()
            except BaseException as e:            # noqa: BLE001
                dbg_exc("send worker")
                msg = f"发送失败: {type(e).__name__}: {e}"
            finally:
                try:
                    self.done_signal.emit("send", ok, msg)
                except BaseException:              # noqa: BLE001
                    dbg_exc("emit send signal")

        def _on_done_signal(self, tag, ok, msg):
            """主线程: 收到子线程结果, 这里才允许动 UI。"""
            dbg(f"[ui] done signal tag={tag} ok={ok} msg={msg}")
            try:
                if tag != "send":
                    return
                self.btn_send.setEnabled(True)
                self.btn_stop.setEnabled(False)
                self.lbl_status.setText(str(msg))
                if ok:
                    extra = ("\n\n让对方在他的机器上执行:\n    tailscale file get ."
                             if self.cmb_smethod.currentIndex() == 0 else "")
                    QMessageBox.information(self, "发送完成", str(msg) + extra)
            except Exception:  # noqa: BLE001
                dbg_exc("on_done_signal")

        def on_stop(self):
            if self._send_task:
                self._send_task.cancel()
            self.lbl_status.setText("已取消")

        def _slog(self, s):
            self.txt_slog.appendPlainText(s)

        # ---------------- 状态同步 ----------------
        def _sel_platform(self):
            """下拉框 -> (linux_on, windows_on)。索引: 0=Linux 1=Windows 2=双端"""
            i = self.cmb_os.currentIndex()
            return (i in (0, 2), i in (1, 2))

        def _sync_platform_state(self):
            linux_on, win_on = self._sel_platform()
            self.cmb_arch.setEnabled(linux_on)
            self.cb_7z_lin.setEnabled(linux_on)
            self.cb_7z_win.setEnabled(win_on)
            if not linux_on:
                self.lbl_pub.setObjectName("hint")
                self.lbl_pub.setStyleSheet("color:#B25E00;")
            else:
                self.lbl_pub.setObjectName("hint")
                self.lbl_pub.setStyleSheet("")
            self._sync_summary()

        def _sync_mode_hint(self):
            if self.cmb_mode.currentIndex() == 0:
                self.lbl_mode.setText(
                    "→ 包约 70MB 起。对方机器断网 / 没有 curl / wget 也能完成部署。最稳妥。")
            else:
                self.lbl_mode.setText(
                    "→ 包约 3MB。适合对方能上网的情况（deploy 会自动下载官方安装包）。")
            self._sync_summary()

        def _sel_want_7zip(self):
            out = []
            linux_on, win_on = self._sel_platform()
            if linux_on and self.cb_7z_lin.isChecked():
                out.append("linux")
            if win_on and self.cb_7z_win.isChecked():
                out.append("windows")
            return out

        def _sync_summary(self):
            """实时摘要: 点"生成"之前就能核对, 杜绝"选单端却生成双端"而不自知。"""
            if not hasattr(self, "lbl_summary"):
                return
            linux_on, win_on = self._sel_platform()
            self.lbl_summary.setText(build_summary(
                linux_on, win_on,
                self.cmb_mode.currentIndex() == 0,
                self._sel_want_7zip(),
                self.cmb_arch.currentText().split()[0],
                self.cmb_fmt.currentIndex(),
                bool(normalize_authkey(self.ed_auth.text())),
                bool(self.ed_pub.toPlainText().strip()),
            ))

        def _snapshot_config(self):
            cfg = {
                "os_index": self.cmb_os.currentIndex(),
                "mode_index": self.cmb_mode.currentIndex(),
                "arch_index": self.cmb_arch.currentIndex(),
                "fmt_index": self.cmb_fmt.currentIndex(),
                "prefix": self.ed_prefix.text().strip(),
                "out_dir": self.ed_out.text().strip(),
                "want_7z_linux": self.cb_7z_lin.isChecked(),
                "want_7z_windows": self.cb_7z_win.isChecked(),
                "pubkey": self.ed_pub.toPlainText().strip(),
                "remember_auth": self.cb_remember.isChecked(),
                "authkey": (normalize_authkey(self.ed_auth.text())
                            if self.cb_remember.isChecked() else ""),
            }
            return cfg

        def _autosave(self):
            """任何改动都静默存一次(防闪退/误关导致的选择丢失)。"""
            try:
                save_config(self._snapshot_config())
            except Exception:  # noqa: BLE001
                pass

        def _restore_config(self):
            cfg = load_config(log=self._log)
            self.cmb_os.setCurrentIndex(int(cfg["os_index"]))
            self.cmb_mode.setCurrentIndex(int(cfg["mode_index"]))
            self.cmb_arch.setCurrentIndex(int(cfg["arch_index"]))
            self.cmb_fmt.setCurrentIndex(int(cfg["fmt_index"]))
            self.ed_prefix.setText(cfg["prefix"])
            if os.path.isdir(cfg["out_dir"]):
                self.ed_out.setText(cfg["out_dir"])
            self.cb_7z_lin.setChecked(bool(cfg["want_7z_linux"]))
            self.cb_7z_win.setChecked(bool(cfg["want_7z_windows"]))
            self.cb_remember.setChecked(bool(cfg["remember_auth"]))
            if cfg["pubkey"]:
                self.ed_pub.setPlainText(cfg["pubkey"])
            if cfg["remember_auth"] and cfg["authkey"]:
                self.ed_auth.setText(cfg["authkey"])
                self._log("  已恢复上次填写的 authkey（勾了记住）")
            elif cfg["authkey"]:
                self._log("  检测到上次填过 authkey，但未勾『记住』，已跳过恢复")
            # 连接信号（放在设置完值之后，避免恢复过程触发联动抖动）
            for w in (self.cmb_os, self.cmb_mode, self.cmb_arch, self.cmb_fmt):
                w.currentIndexChanged.connect(self._on_choice_changed)
            self.cb_7z_lin.toggled.connect(self._on_choice_changed)
            self.cb_7z_win.toggled.connect(self._on_choice_changed)
            self.ed_auth.textChanged.connect(self._on_choice_changed)
            self.ed_pub.textChanged.connect(self._on_choice_changed)
            self.ed_out.textChanged.connect(self._on_choice_changed)
            self.ed_prefix.textChanged.connect(self._on_choice_changed)
            self.cb_remember.toggled.connect(self._on_choice_changed)
            self._check_auth()
            self._check_pub()
            self._sync_platform_state()
            self._sync_mode_hint()
            self._sync_summary()
            self._log(f"  配置文件: {CONFIG_FILE}")

        def _on_choice_changed(self, *args):
            self._sync_platform_state()
            self._sync_mode_hint()
            self._check_auth()
            self._check_pub()
            self._sync_summary()
            self._autosave()

        # ---------------- 密钥相关 ----------------
        def _log(self, msg):
            self.txt_log.appendPlainText(msg)
            print(msg)

        @staticmethod
        def _set_state(lbl, name, text):
            """改状态标签: QSS 靠 objectName 匹配, 换名后要强制重刷样式。"""
            lbl.setText(text)
            lbl.setObjectName(name)
            lbl.style().unpolish(lbl)
            lbl.style().polish(lbl)

        def _toggle_auth_visible(self):
            if self.ed_auth.echoMode() == QLineEdit.Password:
                self.ed_auth.setEchoMode(QLineEdit.Normal)
            else:
                self.ed_auth.setEchoMode(QLineEdit.Password)

        def _check_auth(self):
            k = normalize_authkey(self.ed_auth.text())
            if not k:
                self._set_state(self.lbl_auth, "hint",
                                "状态：未填写（目标机运行时会提示手动粘贴）")
            elif k.startswith("tskey-auth-"):
                self._set_state(self.lbl_auth, "ok",
                                f"状态：✓ 格式正确（{len(k)} 字符）")
            else:
                self._set_state(self.lbl_auth, "bad",
                                "状态：✗ 不像 authkey，应该以 tskey-auth- 开头")

        def _check_pub(self):
            t = self.ed_pub.toPlainText().strip()
            if not t:
                self._set_state(self.lbl_pub, "hint",
                                "状态：未提供（Windows 目标机会被提示粘贴，或现场自动生成）")
                return
            first = t.splitlines()[0].strip()
            if first.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-")):
                self._set_state(self.lbl_pub, "ok",
                                "状态：✓ 公钥格式正确，会自动写入包内并配置免密登录")
            else:
                self._set_state(self.lbl_pub, "bad",
                                "状态：✗ 格式不对，应为 ssh-ed25519 AAAA… 开头的整行")

        def _open_keys_url(self):
            QDesktopServices.openUrl(QUrl(TS_KEYS_URL))
            self._log(f"已在浏览器打开: {TS_KEYS_URL}")
            self._log("  提示: 勾上 Reusable，过期时间设 Disable（否则到期要重跑 deploy）")

        def _make_keypair(self, force=False):
            self._log("")
            self._log("── 生成 SSH 密钥对 ──")
            priv, _pub, text = generate_ssh_keypair(force=force, log=self._log)
            if not text:
                QMessageBox.warning(
                    self, "生成失败",
                    "未能生成密钥对。\n\nWindows 需先安装 OpenSSH 客户端：\n"
                    "设置 → 可选功能 → 添加 OpenSSH 客户端\n\n"
                    "或手动生成后把公钥整行粘进下面的框：\n"
                    '  ssh-keygen -t ed25519 -f "%USERPROFILE%\\.ssh\\id_ed25519"')
                return
            self.ed_pub.setPlainText(text)
            self._log("")
            self._log("★ 私钥必须留在你自己（控制端）这台电脑上，不要发给任何人：")
            self._log(f"    {priv}")
            self._log("★ 公钥会自动打进部署包，配置到对方机器实现免密登录。")
            self._log(f"★ 之后连接命令:  ssh -i \"{priv}\" 对方用户名@对方IP")
            QMessageBox.information(
                self, "密钥已就绪",
                f"已生成并填入公钥。\n\n私钥位置（留在你自己电脑，不要外发）：\n{priv}\n\n"
                "部署包会把这个公钥写进对方的 authorized_keys，实现免密登录。")

        def _scan_existing_key(self):
            priv, _pub = find_ssh_keypair()
            if priv:
                self._log(f"检测到本机已有 SSH 密钥对: {priv}")
                self._log("（Windows 被控端需要公钥 —— 点『一键在本机生成密钥对』即可自动填入）")
            else:
                self._log("未检测到本机 SSH 密钥对；若目标是 Windows，建议点『一键在本机生成』。")

        def _load_file(self, line_edit):
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "All Files (*)")
            if path:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        line_edit.setText(f.read().strip())
                except Exception as e:  # noqa: BLE001
                    QMessageBox.warning(self, "读取失败", str(e))

        def _pick_out(self):
            d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ed_out.text())
            if d:
                self.ed_out.setText(d)

        # ---------------- 生成 ----------------
        def on_generate(self):
            linux_on, windows_on = self._sel_platform()

            if not (linux_on or windows_on):
                QMessageBox.warning(self, "请选择", "至少选择一个目标系统。")
                return
            if not (
                os.path.isdir(os.path.join(REPO_ROOT, "linux"))
                and os.path.isdir(os.path.join(REPO_ROOT, "windows"))
            ):
                QMessageBox.critical(
                    self, "路径错误",
                    f"未在脚本上级找到 linux/ 与 windows/ 目录。\n脚本位置: {REPO_ROOT}")
                return

            arch = self.cmb_arch.currentText().split()[0] if linux_on else "amd64"
            auth = normalize_authkey(self.ed_auth.text())
            pub = self.ed_pub.toPlainText().strip()
            out_dir = self.ed_out.text().strip() or os.path.join(
                os.path.expanduser("~"), "Desktop")
            prefix = self.ed_prefix.text().strip() or "tailscale-remote"
            fmt = {0: "tar.gz", 1: "zip", 2: "both"}[self.cmb_fmt.currentIndex()]
            offline = self.cmb_mode.currentIndex() == 0
            want_7zip = []
            if linux_on and self.cb_7z_lin.isChecked():
                want_7zip.append("linux")
            if windows_on and self.cb_7z_win.isChecked():
                want_7zip.append("windows")

            self._autosave()
            self.txt_log.clear()
            self._log("=" * 62)
            self._log("开始生成部署包")
            self._log(self.lbl_summary.text())
            self._log("=" * 62)
            self.btn_gen.setEnabled(False)
            self.lbl_status.setText("生成中…")
            QApplication.processEvents()
            try:
                made = generate(REPO_ROOT, linux_on, windows_on, arch, auth, pub,
                                out_dir, prefix, fmt, want_7zip=want_7zip,
                                offline=offline, log=self._log)
            except Exception as e:  # noqa: BLE001
                self._log(f"[X] 生成失败: {e}")
                self.lbl_status.setText("失败")
                QMessageBox.critical(self, "生成失败", str(e))
                self.btn_gen.setEnabled(True)
                return
            self.btn_gen.setEnabled(True)

            total = sum(os.path.getsize(m) for m in made) // 1024
            self._log("")
            self._log("=" * 62)
            self._log(f"✅ 完成！共 {len(made)} 个文件，合计约 {total} KB")
            for m in made:
                self._log("   " + m)
            self._log("")
            self._log("包内结构（对方只需要碰 deploy / clean）:")
            self._log("   deploy.sh / deploy.bat   ← 部署, 只跑这个")
            self._log("   clean.sh  / clean.bat    ← 用完清洗")
            self._log("   README.md   使用说明.txt")
            self._log("   程序/                    ← 实现细节, 不用打开")
            self.lbl_status.setText(f"已生成 {len(made)} 个文件")
            QMessageBox.information(
                self, "生成完成",
                f"已生成 {len(made)} 个压缩包（合计 {total} KB）\n\n"
                f"位置：{out_dir}\n\n"
                "把它发给目标机，对方解压后运行 deploy 即可。")

        def on_open(self):
            out_dir = self.ed_out.text().strip() or os.path.join(
                os.path.expanduser("~"), "Desktop")
            if os.path.isdir(out_dir):
                try:
                    os.startfile(out_dir)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass


def _install_excepthook():
    """全局未捕获异常钩子: 写日志 + 提示用户, 绝不静默死掉。"""
    def _hook(exc_type, exc_val, exc_tb):
        try:
            import traceback
            tb = "".join(traceback.format_exception(exc_type, exc_val, exc_tb))
            dbg("=== UNCAUGHT EXCEPTION ===")
            dbg(tb)
        except Exception:  # noqa: BLE001
            pass
        try:
            if HAS_QT:
                from PyQt5.QtWidgets import QApplication, QMessageBox
                QMessageBox.critical(
                    None, "程序出错",
                    "出了个未预期的问题, 但程序不会退出。\n\n"
                    "详细信息已写到:\n" + DEBUG_LOG_PATH +
                    "\n\n(通常是拖拽了失效的路径, 或目标机状态变了)")
        except Exception:  # noqa: BLE001
            pass
    sys.excepthook = _hook
    try:
        import threading
        def _thook(args):
            if args.exc_type is SystemExit:
                return
            _hook(args.exc_type, args.exc_value, args.exc_traceback)
        threading.excepthook = _thook      # Python 3.8+
    except Exception:  # noqa: BLE001
        pass


def _clear_alive_sentinel():
    """删掉启动器放的哨兵文件 —— 它的存在表示"GUI 没起来"。

    静默启动(pythonw / .vbs)拿不到任何 stderr, 出错时用户只看到
    "什么都没发生"。这个哨兵就是启动器的失败判据。
    """
    for name in (".toolbox-alive",):
        p = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
        try:
            if os.path.isfile(p):
                os.remove(p)
        except OSError:
            pass


def _fail_fast(msg, detail=""):
    """GUI 起不来时: 写日志 + 弹窗(若可能) + 非零退出。

    静默启动下没有控制台, 所以必须靠弹窗/日志告知, 不能静默退出。
    """
    try:
        import datetime
        path = DEBUG_LOG_PATH
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"\n=== FATAL {datetime.datetime.now():%H:%M:%S} ===\n"
                    f"{msg}\n{detail}\n")
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"{msg}\n\n{detail}\n\n细节: {DEBUG_LOG_PATH}",
            "Tailscale 工具箱 - 无法启动", 0x10)
    except Exception:  # noqa: BLE001
        pass
    sys.exit(1)


def main():
    _install_excepthook()
    dbg("=== application start ===")
    dbg(f"python {sys.version.split()[0]}  pyqt={HAS_QT}  os={os.name}  "
        f"exe={sys.executable}")
    if not HAS_QT:
        dbg("FATAL: PyQt5 不可用")
        _fail_fast(
            "没有安装 PyQt5, 图形界面无法启动。",
            f"请执行:\n    {sys.executable} -m pip install PyQt5")
    try:
        app = QApplication(sys.argv)
        app.setFont(QFont("Microsoft YaHei", 13))
        w = MainWindow()
        w.show()
    except BaseException:                     # noqa: BLE001
        dbg_exc("GUI 初始化")
        _fail_fast("图形界面初始化失败。", "详见下方日志里的堆栈。")
        return
    # 走到这说明 GUI 真的起来了 -> 撤掉哨兵
    _clear_alive_sentinel()
    dbg("GUI up, sentinel cleared")
    try:
        rc = app.exec_()
    except BaseException:                     # noqa: BLE001
        dbg_exc("app.exec_")
        rc = 1
    dbg(f"=== application exit rc={rc} ===")
    sys.exit(rc)


if __name__ == "__main__":
    main()
