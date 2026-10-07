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
import re
import time
import sys
import json
import shutil
import tarfile
import zipfile
import datetime
import subprocess


def _no_window():
    """★ 铁律: 本程序会反复调用 tailscale / ssh-keygen 等控制台程序。

    从 pythonw(无控制台) 里 subprocess 一个控制台程序时, Windows 会给它
    **新建一个控制台窗口** —— 哪怕你 capture_output=True 也一样。表现就是
    "用着用着老闪黑框"。所以每一次 spawn 都必须带 CREATE_NO_WINDOW。
    """
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    return 0


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
    log(f"  [OK] tar.gz : {out_path}  ({os.path.getsize(out_path)//1024} KB)")
    return out_path


def make_zip(pkg_dir, out_dir, pkg_name, log=print):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, pkg_name + ".zip")
    # ★ 包里也套一层顶层目录(与 tar.gz 一致): 这样"解压到当前文件夹"不会把
    #   一堆文件撒得到处都是, 收件人永远得到一个干净的同名文件夹。
    top = os.path.basename(pkg_dir)
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(pkg_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, pkg_dir).replace(os.sep, "/")
                zi = zipfile.ZipInfo(top + "/" + rel)
                base = os.path.basename(f)
                ext = os.path.splitext(base)[1].lower()
                is_exec = ext in (".sh", ".exe", ".bat", ".py") or base in (
                    "tailscale", "tailscaled", "7zz", "7za"
                )
                zi.external_attr = ((0o755 if is_exec else 0o644) << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(fp, "rb") as fh:
                    z.writestr(zi, fh.read())
    log(f"  [OK] zip    : {out_path}  ({os.path.getsize(out_path)//1024} KB)")
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
                creationflags=_no_window(),
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
    log(f"  [OK] 私钥: {priv}")
    log(f"  [OK] 公钥: {priv}.pub")
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
    "ssh_users": {},           # {IP: 被控端 Windows 用户名}, 记住后一键连接不再手打
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


# ------------------------------------------------------------ SSH 免密快捷
# 被控端部署好后, 控制端只需记住「IP + 对方用户名」即可免密连入。
# 最容易翻车的一步就是手打用户名打错一个字母（实测踩过: lllxx 打成 lllxxx,
# 服务器上没这个账号 -> 公钥压根不参与验证 -> 反复要密码 -> Permission denied）。
# 所以这里把用户名固化进 ~/.ssh/config, 之后 ssh <别名> 就行, 永不手打。
SSH_DIR    = os.path.join(os.path.expanduser("~"), ".ssh")
SSH_CONFIG = os.path.join(SSH_DIR, "config")


def ssh_default_key():
    """控制端私钥路径: 依次找 id_ed25519 / id_rsa / id_ecdsa, 都没有则返回空串。"""
    for n in ("id_ed25519", "id_rsa", "id_ecdsa"):
        p = os.path.join(SSH_DIR, n)
        if os.path.isfile(p):
            return p
    return ""


def sanitize_alias(name):
    """把 Tailscale 设备名变成合法 ssh 别名（只保留字母数字与 . _ -）。"""
    s = re.sub(r"[^A-Za-z0-9_.\-]", "-", (name or "").strip())
    return s.strip("-.")


def _ssh_alias_block(alias, host, user, key):
    out = [f"Host {alias}",
           f"    HostName {host}",
           f"    User {user}"]
    if key:
        out.append("    IdentityFile " + key.replace("\\", "/"))
    out += ["    IdentitiesOnly yes",
            "    StrictHostKeyChecking accept-new",
            "    ServerAliveInterval 30",
            "    ServerAliveCountMax 6"]
    return out


def upsert_ssh_alias(alias, host, user, key=None, log=None):
    """在 ~/.ssh/config 里新增或就地更新一个 Host 块。

    只动别名完全相同的那个块, 其余内容逐行原样保留 —— 不会破坏你已有的
    github 等配置。返回 (ok, msg)。
    """
    alias = (alias or "").strip()
    host = (host or "").strip()
    user = (user or "").strip()
    if not alias or not host or not user:
        return False, "别名 / IP / 用户名 缺一不可"
    key = key or ssh_default_key()
    try:
        os.makedirs(SSH_DIR, exist_ok=True)
        old = ""
        if os.path.isfile(SSH_CONFIG):
            with open(SSH_CONFIG, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
        lines = old.replace("\r\n", "\n").split("\n")

        start = end = -1
        for i, ln in enumerate(lines):
            s = ln.strip()
            if not s or s[:1].isspace():
                continue
            toks = s.split()
            if toks and toks[0].lower() == "host" and len(toks) > 1 and toks[1] == alias:
                start = i
                j = i + 1
                while j < len(lines):
                    t = lines[j]
                    if t.strip() and not t[:1].isspace():
                        break
                    j += 1
                end = j
                break

        block = _ssh_alias_block(alias, host, user, key)
        if start >= 0:
            lines[start:end] = block
            action = "已更新"
        else:
            while lines and not lines[-1].strip():
                lines.pop()
            if lines:
                lines.append("")
            lines += block
            action = "已新增"
        with open(SSH_CONFIG, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines).rstrip("\n") + "\n")
        msg = f"{action}别名【{alias}】-> {user}@{host}"
        if log:
            log("  " + msg)
        return True, msg
    except Exception as e:  # noqa: BLE001
        if log:
            log(f"  [!] 写 ssh 配置失败: {e}")
        return False, f"写入 ssh 配置失败: {e}"


def read_ssh_aliases():
    """读回 ~/.ssh/config 里已有的 Host 别名（界面展示用）。"""
    out = []
    try:
        with open(SSH_CONFIG, "r", encoding="utf-8", errors="replace") as f:
            for ln in f:
                s = ln.strip()
                if not s or s[:1].isspace():
                    continue
                t = s.split()
                if t and t[0].lower() == "host" and len(t) > 1 \
                        and t[1] not in out and "*" not in t[1]:
                    out.append(t[1])
    except OSError:
        pass
    return out


def read_ssh_hosts():
    """读回 ~/.ssh/config 里的 {HostName/IP: User} 映射。

    用途: 界面选中一台设备时, 若本地配置里已经连过这个 IP, 就能把对方用户名
    自动填出来 —— 省掉那个"错一个字母就全废"的手打环节。
    """
    out = {}
    try:
        with open(SSH_CONFIG, "r", encoding="utf-8", errors="replace") as f:
            host = user = None
            for raw in f:
                s = raw.strip()
                if not s or s.startswith("#"):
                    continue
                # ★ 缩进必须拿【原始行】判断: 先 strip 再判会让每一行都变成"新块开头"
                if raw[:1] not in (" ", "\t"):             # 新的 Host 块开始
                    if host and user:
                        out.setdefault(host, user)
                    host = user = None
                    t = s.split()
                    if t and t[0].lower() != "host":
                        host = user = None
                    continue
                t = s.split(None, 1)
                if len(t) < 2:
                    continue
                k, v = t[0].lower(), t[1].strip()
                if k == "hostname":
                    host = v
                elif k == "user":
                    user = v
            if host and user:
                out.setdefault(host, user)
    except OSError:
        pass
    return out


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

# ★★ 编码铁律（血泪教训, 别改回去）★★
#  cmd.exe 用**系统 OEM 码页**（简体中文 = 936/GBK）解码 .bat 的每一行。
#  所以含 UTF-8 中文的 .bat 会让路径字符串变乱码 -> `if not exist` 永远判 fail,
#  现象是"文件明明在, 却报 找不到/包不完整"然后退出（真机已复现）。
#  另外 LF 换行会让 cmd 的括号块解析碎裂。
#
#  因此这里的 bat 遵守三条:
#    1) 逻辑与路径**全部 ASCII**; 中文只允许出现在 echo 的提示文字里;
#    2) 查找"程序"目录一律用 `for /d` 通配, 不写字面中文路径
#       —— 顺带免疫"解压后中文目录名乱码"的问题;
#    3) 落盘时统一转成 GBK + CRLF（见 write_root_files / normalize_tree）。
#  提权不再在 bat 里拼三层引号, 交给 ps1 自己 UAC 重启自己。
DEPLOY_BAT = """@echo off
REM ============================================================
REM  deploy.bat -- 一键部署  (右键 -> 以管理员身份运行)
REM  This file is ASCII-only on purpose: cmd.exe decodes .bat with the
REM  OEM code page, so non-ASCII bytes in the *logic* would break it.
REM ============================================================
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
set "PS1="
for /d %%D in ("%~dp0*") do if exist "%%~fD\\windows\\connect-windows.ps1" set "PS1=%%~fD\\windows\\connect-windows.ps1"
if not defined PS1 if exist "%~dp0windows\\connect-windows.ps1" set "PS1=%~dp0windows\\connect-windows.ps1"
if not defined PS1 (
  echo.
  echo [X] 包不完整: 找不到 程序\\windows\\connect-windows.ps1
  echo     请把整个文件夹一起解压后再运行（不要只拷 deploy.bat）。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
"""

CLEAN_BAT = """@echo off
REM ============================================================
REM  clean.bat -- 一键清洗  (右键 -> 以管理员身份运行)
REM  会删除: Tailscale / OpenSSH Server / 公钥 / 密钥 / 状态
REM  ASCII-only on purpose (see deploy.bat).
REM ============================================================
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
set "PS1="
for /d %%D in ("%~dp0*") do if exist "%%~fD\\windows\\clean-windows.ps1" set "PS1=%%~fD\\windows\\clean-windows.ps1"
if not defined PS1 if exist "%~dp0windows\\clean-windows.ps1" set "PS1=%~dp0windows\\clean-windows.ps1"
if not defined PS1 (
  echo.
  echo [X] 包不完整: 找不到 程序\\windows\\clean-windows.ps1
  echo     请把整个文件夹一起解压后再运行（不要只拷 clean.bat）。
  echo.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*
"""


# ------------------------------------------------------------ 网页消息 / 弹窗
# ★ 中继地址: Cloudflare Pages 项目 ts-remote-web。
#   为什么不用 *.workers.dev —— 它在国内被 DNS 污染(解析到 75.126.x),
#   被控端根本连不上; *.pages.dev 解析正常、可达。
RELAY_URL = "https://ts-remote-web.pages.dev"


def new_room_code():
    """生成一个房间号。双方填同一个值才能配对。"""
    import random
    return "r" + "".join(random.choice("abcdefghijkmnpqrstuvwxyz23456789")
                         for _ in range(6))


# 顶层入口: 双击 → 右下角开始收消息弹窗(隐藏常驻托盘)
NOTIFY_BAT = """@echo off
REM ============================================================
REM  消息弹窗.bat -- 打开「右下角消息弹窗」（被控端）
REM  之后网页上发的文字会像系统通知一样在右下角弹出, 可直接打字回复;
REM  文件双向自动收发（收件箱 / 发件箱）。隐藏常驻托盘。
REM  ASCII-only on purpose (see deploy.bat).
REM ============================================================
chcp 936 >nul 2>nul
setlocal enableextensions
cd /d "%~dp0" 2>nul
set "PS1="
for /d %%D in ("%~dp0*") do if exist "%%~fD\\windows\\notify-windows.ps1" set "PS1=%%~fD\\windows\\notify-windows.ps1"
if not defined PS1 if exist "%~dp0windows\\notify-windows.ps1" set "PS1=%~dp0windows\\notify-windows.ps1"
if not defined PS1 (
  echo.
  echo [X] 包不完整: 找不到 程序\\windows\\notify-windows.ps1
  echo     请把整个文件夹一起解压后再运行。
  echo.
  pause
  exit /b 1
)
set "ROOM=%~1"
set "PS1DIR="
for %%F in ("%PS1%") do set "PS1DIR=%%~dpF"
if not defined ROOM if exist "%PS1DIR%keys\\notify.local.txt" set /p ROOM=<"%PS1DIR%keys\\notify.local.txt"
if not defined ROOM (
  echo.
  echo [!] 没有房间号。请用:  消息弹窗.bat 你的房间号
  echo.
  pause
  exit /b 1
)
set "PS1F=%PS1%"
set "ROOMF=%ROOM%"
set "RELAYF=__RELAY__"
REM 隐藏启动: 外层 powershell 也是 Hidden, 所以只会有一瞬间的 cmd 闪过
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -Command "Start-Process powershell -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-WindowStyle','Hidden','-File',$env:PS1F,'-Room',$env:ROOMF,'-Relay',$env:RELAYF) -WindowStyle Hidden"
exit /b 0
"""


# ------------------------------------------------------------ 编码/换行归一化
def _read_any(path):
    """按 utf-8-sig -> gbk -> latin-1 依次尝试解码, 换行统一成 \\n。"""
    raw = open(path, "rb").read()
    for enc in ("utf-8-sig", "gbk", "latin-1"):
        try:
            s = raw.decode(enc)
            return s.replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1").replace("\r\n", "\n").replace("\r", "\n")


def _write_enc(path, text, enc, newline):
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if newline != "\n":
        text = text.replace("\n", newline)
    if enc == "utf-8-bom":
        data = b"\xef\xbb\xbf" + text.encode("utf-8")
    else:
        data = text.encode(enc)
    with open(path, "wb") as f:
        f.write(data)


def normalize_tree(root, plat, log=None):
    """把复制进来的脚本转成"目标机能正确读"的编码与换行。

    Windows: .bat -> GBK + CRLF (cmd 按 OEM 码页读; LF 会让括号块碎裂)
             .ps1 -> UTF-8 BOM + CRLF (PS 5.1 对无 BOM 文件按 ANSI 读, 中文会乱)
             .txt -> GBK (双击用记事本看, 中文正常)
    Linux  : .sh  -> UTF-8 + LF  (绝不能有 CR, 否则 shebang 报 bad interpreter)
    """
    fixed = []
    for dirpath, _dirs, files in os.walk(root):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            ext = os.path.splitext(fn)[1].lower()
            try:
                if ext == ".bat":
                    _write_enc(fp, _read_any(fp), "gbk", "\r\n")
                    fixed.append((".bat->GBK/CRLF", os.path.relpath(fp, root)))
                elif ext == ".ps1":
                    _write_enc(fp, _read_any(fp), "utf-8-bom", "\r\n")
                    fixed.append((".ps1->UTF8-BOM/CRLF", os.path.relpath(fp, root)))
                elif ext == ".sh":
                    _write_enc(fp, _read_any(fp), "utf-8", "\n")
                    os.chmod(fp, 0o755)
                elif ext == ".txt" and plat == "windows":
                    _write_enc(fp, _read_any(fp), "gbk", "\r\n")
                    fixed.append((".txt->GBK/CRLF", os.path.relpath(fp, root)))
            except Exception as e:  # noqa: BLE001
                if log:
                    log(f"  [!] 编码归一化失败 {fn}: {e}")
    if log and fixed:
        log(f"  [OK] 编码归一化 {len(fixed)} 个文件 "
            f"({', '.join(sorted({t for t, _ in fixed}))})")
    return fixed


def verify_package(pkg_dir, log=print):
    """自检: 出包前确认每个脚本都是目标机能读的形态。返回问题列表。"""
    problems = []
    for dirpath, _dirs, files in os.walk(pkg_dir):
        for fn in files:
            fp = os.path.join(dirpath, fn)
            rel = os.path.relpath(fp, pkg_dir)
            ext = os.path.splitext(fn)[1].lower()
            if ext not in (".bat", ".ps1", ".sh"):
                continue          # 二进制/文档不在此检查范围
            raw = open(fp, "rb").read()
            if ext == ".bat":
                if raw.startswith(b"\xef\xbb\xbf"):
                    problems.append(f"{rel}: .bat 带 BOM, cmd 会报错")
                try:
                    raw.decode("gbk")
                except UnicodeDecodeError:
                    problems.append(f"{rel}: .bat 不是合法 GBK")
                if b"\n" in raw.replace(b"\r\n", b""):
                    problems.append(f"{rel}: .bat 含裸 LF, 会导致括号块碎裂")
                # 逻辑行(非 echo/REM)必须纯 ASCII, 否则路径匹配会失败
                for i, ln in enumerate(raw.split(b"\r\n"), 1):
                    s = ln.strip()
                    low = s.lower()
                    is_echo = low.startswith(b"echo")
                    is_rem = low.startswith(b"rem")
                    if is_rem:
                        continue
                    if is_echo:
                        # ★ echo 文本里出现 ASCII 半角括号时, 若该行在 if(...) 块内,
                        #   cmd 会把它当成块的收尾 -> "此时不应有 xxx" 直接中止。
                        #   中文全角括号（）无害, 只查半角。
                        if b"(" in s[4:] or b")" in s[4:]:
                            problems.append(
                                f"{rel}: 第{i}行 echo 文本含半角括号, 会破坏 if() 块")
                        continue
                    if any(b > 127 for b in ln):
                        problems.append(f"{rel}: 第{i}行(非 echo)含非 ASCII 字节")
            elif ext == ".ps1":
                if not raw.startswith(b"\xef\xbb\xbf"):
                    problems.append(f"{rel}: .ps1 缺 UTF-8 BOM, 中文会乱码")
            elif ext == ".sh":
                if b"\r" in raw:
                    problems.append(f"{rel}: .sh 含 CR, shebang 会报 bad interpreter")
    if problems:
        for p in problems:
            log(f"  [X] 自检: {p}")
    else:
        log("  [OK] 编码自检通过: bat=GBK/CRLF, ps1=UTF-8-BOM, sh=UTF-8/LF")
    return problems

QUICK_TXT = """【怎么用 —— 只需要点一下】
================================================================

  第1步  双击 deploy.bat  (Windows) / bash deploy.sh  (Linux)
         弹出的窗口里点【是】同意提权 —— 就这一下, 全程不需要输入任何东西。
         密钥和入网凭证都已在包里备好, 脚本自己装、自己配、自己启动。

  第2步  等它跑完。窗口最后会单独框出一行:
             ==============================================================
               请把【下面这一行】复制发给对方（就是最后这行）:

                 ssh 你的用户名@100.x.x.x
             ==============================================================
         把这一行发给要连你电脑的人, 他粘贴执行就能连进来, 不用输密码。

  第3步  用完清洗: 右键 clean.bat → 以管理员身份运行
                   (Linux: bash clean.sh)

----------------------------------------------------------------
【开机自启 —— 不用你管】
  Tailscale 服务、sshd 服务都已被设为"开机自动启动", 并建了开机任务
  自动入网。也就是说这台电脑重启之后什么都不用点, 依然能被连上。

----------------------------------------------------------------
【只有这几个文件要碰】
  deploy.bat / deploy.sh    部署 (只跑一次)
  clean.bat  / clean.sh     清洗 (用完跑)
  连接信息.txt              部署后自动生成, 里面就是那行连接命令
  README.md                 说明文档
  使用说明.txt              就是本文件

  『程序』文件夹是实现细节, 不需要打开, 也不用动。
----------------------------------------------------------------
【安全提醒】
  本包内含明文 Tailscale authkey 与控制端公钥, 只发给你信任的人。
  对方跑完 clean 后本机痕迹会被清除;
  你的 Tailscale 后台设备列表里仍会留着这台机器,
  要彻底移除请到 https://login.tailscale.com/admin/machines 删除该节点。
"""





def write_root_files(pkg_dir, linux_on, windows_on, log=print, notify_room=""):
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
        # ★ .bat 必须 GBK + CRLF: cmd 用 OEM 码页(936)解码, UTF-8 中文会变乱码
        p = os.path.join(pkg_dir, "deploy.bat")
        _write_enc(p, DEPLOY_BAT, "gbk", "\r\n")
        made.append("deploy.bat")
        p = os.path.join(pkg_dir, "clean.bat")
        _write_enc(p, CLEAN_BAT, "gbk", "\r\n")
        made.append("clean.bat")
        if notify_room:
            p = os.path.join(pkg_dir, "消息弹窗.bat")
            _write_enc(p, NOTIFY_BAT.replace("__RELAY__", RELAY_URL), "gbk", "\r\n")
            made.append("消息弹窗.bat")

    # ★ 这个文件是给"双击用记事本看"的普通人, 存 GBK 才是记事本最稳的形态
    p = os.path.join(pkg_dir, "使用说明.txt")
    _write_enc(p, QUICK_TXT, "gbk", "\r\n")
    made.append("使用说明.txt")
    log(f"  [OK] 顶层入口: {', '.join(made)}")
    return made


# ------------------------------------------------------------ 主体装配
def assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=(), offline=True, log=print, notify_room=""):
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
        # ★ 复制过来的模板可能是 UTF-8/LF, 目标机读不了 -> 统一转码
        normalize_tree(dst, plat, log=log)

        assets = os.path.join(dst, "assets")

        # ---- 离线 / 轻量 开关 ----
        if plat == "linux":
            ts, tsd = os.path.join(assets, "tailscale"), os.path.join(assets, "tailscaled")
            if offline:
                if os.path.isfile(ts) and os.path.isfile(tsd):
                    log(f"  [OK] 离线模式: 保留预置二进制 ({(os.path.getsize(ts)+os.path.getsize(tsd))//1024} KB)")
                else:
                    log("  [!] 离线模式但 assets 缺 tailscale/tailscaled → 自动退回轻量模式")
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
                log(f"  [OK] 离线模式: 保留预置 MSI ({sum(os.path.getsize(m) for m in msis)//1024} KB)")

        # ---- 便携 7-Zip: 未勾选则剔除 ----
        zdir = os.path.join(assets, "7zip")
        if plat in want_7zip:
            exe = "7zz" if plat == "linux" else "7za.exe"
            fp = os.path.join(zdir, exe)
            if os.path.isfile(fp):
                os.chmod(fp, 0o755)
                log(f"  [OK] 内置便携 7-Zip（{exe}, {os.path.getsize(fp)//1024} KB）")
            else:
                log(f"  [!] 勾选了内置 7-Zip，但未找到 {plat}/assets/7zip/{exe}。")
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
            log(f"  [OK] 烘焙 authkey → 程序/{plat}/keys/authkey.local.txt")
        if plat == "windows":
            if not pub:
                # ★ 零交互的前提: 对方机器上绝对不能出现"请粘贴公钥"这样的提问。
                #   控制端没密钥就现场生成一对(本机私钥留下, 公钥烘焙进包)。
                log("  · 未填控制端公钥 —— 自动生成一对 ed25519（对方将全程无需输入）")
                _priv, _pubp, pub = generate_ssh_keypair(log=log)
            if pub:
                with open(os.path.join(keys_dir, "control.pub"), "w", encoding="utf-8") as f:
                    f.write(pub + "\n")
                log("  [OK] 烘焙控制端公钥 → 程序/windows/keys/control.pub")
                log("      （本机 ~/.ssh/id_ed25519 是配对私钥，连对方时自动用它，无需密码）")
            else:
                log("  [!] 拿不到控制端公钥，对方将无法免密登录（仍需交互式粘贴）")
            # ---- 烘焙房间号: 双击『消息弹窗.bat』即可自动配对 ----
            if notify_room:
                with open(os.path.join(keys_dir, "notify.local.txt"),
                          "w", encoding="utf-8") as f:
                    f.write(notify_room + "\n")
                log(f"  [OK] 烘焙消息房间号 → 程序/windows/keys/notify.local.txt ({notify_room})")

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
                     want_7zip=(), offline=True, log=print, notify_room=""):
    """生成包内 README.md（覆盖仓库总览，面向拿到包的人）"""
    L = []
    L.append("# Tailscale 远程连接 · 部署包")
    L.append("")
    L.append("这个包的目标：**你不需要懂任何技术，双击一下就能让你的电脑被远程连进来。**")
    L.append("")
    L.append("## 你需要做的（真的只有一下）")
    L.append("")
    L.append("| 步骤 | Linux | Windows |")
    L.append("|---|---|---|")
    L.append("| 1. 部署 | `bash deploy.sh` | 双击 `deploy.bat`，弹窗点【是】 |")
    L.append("| 2. 拿连接命令 | 窗口**最后一行** | 窗口**最后一行** |")
    L.append("| 3. 传文件（可选） | 用 `scp` 往本机发，见下 | 同左 |")
    L.append("| 4. 用完清洗 | `bash clean.sh` | 双击 `clean.bat` |")
    L.append("")
    L.append("> **全程不需要输入任何东西。** 密钥、入网凭证都已在包里备好，")
    L.append("> 脚本会自己安装、自己配置、自己设成开机自启。")
    L.append("")
    L.append("> 部署完之后，窗口会单独框出**最后一行**，形如：")
    L.append("> ```")
    L.append("> ssh 你的用户名@100.x.x.x")
    L.append("> ```")
    L.append("> 把这一行发给要连你电脑的人，他粘贴执行即可，不用输密码。")
    L.append("")
    L.append("> 部署后包里还会多出一个 `连接信息.txt`，内容就是那行命令，方便随时翻。")
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
    L.append("连接信息.txt              部署后自动生成：那行连接命令")
    L.append("使用说明.txt              三步极简说明")
    L.append("README.md                本文件")
    L.append("程序/                    实现细节（不用打开）")
    L.append("  ├── linux/             脚本 + 预置二进制 + 你的密钥")
    L.append("  └── windows/")
    L.append("```")
    L.append("")
    L.append("## 开机自启")
    L.append("")
    L.append("Tailscale 服务与 sshd 服务都会被设为**开机自动启动**，并写入一个开机任务")
    L.append("自动入网。也就是说这台电脑重启之后什么都不用点，依然能被连上。")
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
        L.append("> [!] 本包是**轻量版**：目标机必须能联网才能自动获取 Tailscale 官方安装包。")
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
    if notify_room:
        L.append("## 网页消息 / 弹窗（已启用）")
        L.append("")
        L.append(f"- 控制台网址：{RELAY_URL}")
        L.append(f"- 房间号：`{notify_room}`（网页上要填同一个）")
        L.append("- 被控端：双击包里的 `消息弹窗.bat`，之后就常驻托盘。")
        L.append("- 效果：网页上打字 → 这台电脑右下角弹通知，可直接打字回复；")
        L.append("  文件双向互传，收到的自动存到 `%USERPROFILE%\\TailscaleRemote\\收件箱`，")
        L.append("  丢进 `发件箱` 的文件会自动传回网页，全程不用确认。")
        L.append("")
    L.append("[!] 本包内含明文 Tailscale authkey，请只发给你信任的人。")
    with open(os.path.join(pkg_dir, "README.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    log("  [OK] 写入包内 README.md")


def generate(repo_root, linux_on, windows_on, arch, auth, pub,
             out_dir, prefix, fmt, want_7zip=(), offline=True, log=print,
             notify_room=""):
    """fmt: 'tar.gz' | 'zip' | 'both'；want_7zip: 内置便携 7-Zip 的平台集合；
       offline: True=内置二进制(离线可跑) / False=轻量包(目标机联网)；
       notify_room: 非空则在包内烘焙该房间号并放出『消息弹窗.bat』"""
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
        log("[!] 未填 Authkey —— 对方无法自动入网，你还得把 key 单独发过去。")
        log("    想做到『对方零输入』，请在界面上把 authkey 填好再生成。")
    # ★ 零交互前提: Windows 包必须自带控制端公钥, 否则对方要手动粘。
    #   这里【在生成阶段就补好】, 好让 README 和实际落盘内容一致。
    pub = (pub or "").strip()
    if windows_on and not pub:
        log("  · 未填控制端公钥 —— 自动生成一对 ed25519（对方将全程无需输入）")
        _p, _pp, pub = generate_ssh_keypair(log=log)
        if not pub:
            log("[!] 无法自动生成公钥：对方机器上仍需手动粘贴公钥才能免密。")

    log(f"包名: {pkg_name}")
    notify_room = (notify_room or "").strip()
    if notify_room and not windows_on:
        log("  [!] 消息弹窗目前只支持 Windows 被控端, 已忽略房间号。")
        notify_room = ""
    if notify_room:
        log(f"  [i] 网页消息已启用 · 房间号 {notify_room}")
        log(f"      控制台: {RELAY_URL}   对方双击包里的『消息弹窗.bat』即可。")
    assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=want_7zip, offline=offline, log=log,
                     notify_room=notify_room)
    write_root_files(pkg_dir, linux_on, windows_on, log=log,
                     notify_room=notify_room)
    write_pkg_readme(pkg_dir, linux_on, windows_on, bool(auth),
                     bool(pub and windows_on), want_7zip=want_7zip,
                     offline=offline, log=log, notify_room=notify_room)

    # ★ 出包前自检编码/换行 —— 这些坑一旦漏到目标机就是"包根本不跑"
    problems = verify_package(pkg_dir, log=log)
    if problems:
        log(f"[!] 自检发现 {len(problems)} 处问题, 包已生成但请先修复再发出去。")

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


# ------------------------------------------------------------ Tailscale 自愈
# ★ 用户最常遇到的一类"工具没反应"：tailscaled 服务在跑，但托盘程序
#   (tailscale-ipn.exe) 没运行 —— BackendState 卡在 NoState，一个 100.x
#   地址都没有，于是设备列表一片空白、发文件也发不出去。
#   这里做自动恢复，用户什么都不用管。
TS_IPN_PATHS = [
    r"C://Program Files//Tailscale//tailscale-ipn.exe",
]


# _no_window() 定义在文件顶部的 _hide_console 旁边 —— 全局唯一, 别再定义第二份。


def ts_backend_state():
    """取 tailscaled 的 BackendState：Running / NoState / Stopped / NeedsLogin ..."""
    ts = _find_ts()
    if not ts:
        return ""
    try:
        r = subprocess.run([ts, "status", "--json"], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=15, creationflags=_no_window())
        if r.returncode != 0:
            return ""
        return str((json.loads(r.stdout or "{}") or {}).get("BackendState") or "")
    except Exception:                                        # noqa: BLE001
        return ""


def ensure_tailscale_up(log=None, wait=10):
    """确保本机 Tailscale 处于 Running（可用）。返回 (ok, msg)。

    恢复手段：把 Tailscale 托盘程序拉起来 —— 它会自动用已保存的登录态重连。
    这比让用户自己去点托盘图标省事得多。
    """
    st = ts_backend_state()
    if st == "Running":
        return True, "Tailscale 正常"
    ts = _find_ts()
    if not ts:
        return False, "没找到 tailscale（本机未安装？）"
    if log:
        log(f"  [!] Tailscale 当前状态: {st or '未知'} —— 正在自动恢复...")

    ipn = next((p for p in TS_IPN_PATHS if os.path.isfile(p)), None)
    if ipn:
        try:
            subprocess.Popen([ipn], close_fds=True,
                             creationflags=(_no_window()
                                            | getattr(subprocess, "DETACHED_PROCESS", 0)))
            if log:
                log("  已启动 Tailscale 托盘程序，等它把连接拉起来…")
        except Exception as e:                               # noqa: BLE001
            if log:
                log(f"  [!] 启动托盘程序失败: {e}")
    try:
        # 顺带戳一下 up（已登录时是幂等的，只在需要时生效）
        subprocess.run([ts, "up"], capture_output=True, timeout=6,
                       creationflags=_no_window())
    except Exception:                                        # noqa: BLE001
        pass

    for _ in range(max(1, int(wait))):
        time.sleep(1)
        if ts_backend_state() == "Running":
            if log:
                log("  [OK] Tailscale 已恢复，设备列表可用。")
            return True, "已恢复"
    return False, (f"仍处于 {ts_backend_state() or '未知'} 状态"
                   "（可能需要手动打开 Tailscale 登录一次）")


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


def list_ts_targets(include_self=True, my=None):
    """列出 tailnet 里的设备。

    include_self=True 时把本机也列出来(标记 is_self), 因为用户需要看到并
    管理自己这台机器(比如退出 tailnet)。发送时才过滤掉本机。

    my: 已知的本机 IP 列表。★ 传进来就少起两次进程 —— 每次 spawn 都是一次
        可能的"闪黑框 + 几十毫秒", 能省则省。
    """
    ts = _find_ts()
    if not ts:
        return []
    if my is None:
        my = set(_ts_my_ips())
    else:
        my = set(my)
    try:
        r = subprocess.run([ts, "status"], capture_output=True, text=True,
                           timeout=20, creationflags=_no_window())
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
                               text=True, timeout=10,
                               creationflags=_no_window())
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
                           timeout=30, creationflags=_no_window())
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
            # ★ 必须吃掉所有异常: 某些环境里 os.remove 会被拦截并抛非 OSError,
            #   一旦冒泡出去, 明明已经发成功的文件会被报成"失败"。
            try:
                os.remove(b)
            except BaseException:        # noqa: BLE001
                try:
                    os.rename(b, b + ".delete-me")
                except BaseException:    # noqa: BLE001
                    pass
        if rc == 0:
            self.log("")
            self.log(f"[OK] 发送完成 ({cost:.1f} 秒)")
            return True, f"发送完成, 用时 {cost:.1f} 秒"
        self.log("")
        tail = ("  (scp 模式: 对方需已跑过 deploy)" if self.method == "scp"
                else "  (确认对方在线: tailscale status 看它的状态)")
        msg = f"退出码 {rc}。{tail}\n看上方日志里的具体报错。"
        self.log(f"[X] 失败: {msg}")
        return False, msg

# ============================================================ 设备列表(带缓存)
# tailscale status 是一个可能几百毫秒的 subprocess, 每次点按钮都跑一遍会让
# 界面明显卡。这里做一层短 TTL 缓存 + 一次调用同时拿到"本机 IP"和"设备表"。
_PEER_CACHE = {"t": 0.0, "peers": [], "my": []}


def ts_snapshot(ttl=3.0):
    """一次拿全: (peers, my_ips)。ttl 秒内直接复用上次结果。

    返回 (list[dict], list[str])；失败时返回 ([], []) 而不是抛异常。
    """
    import time as _time
    now = _time.time()
    if _PEER_CACHE["t"] and now - _PEER_CACHE["t"] < ttl:
        return list(_PEER_CACHE["peers"]), list(_PEER_CACHE["my"])
    try:
        my = _ts_my_ips_fast()
        peers = list_ts_targets(my=my)
    except Exception:  # noqa: BLE001
        dbg_exc("ts_snapshot")
        return list(_PEER_CACHE["peers"]), list(_PEER_CACHE["my"])
    _PEER_CACHE["t"] = now
    _PEER_CACHE["peers"] = peers
    _PEER_CACHE["my"] = my
    return list(peers), list(my)


def _ts_my_ips_fast():
    """只查 IPv4 —— 本机判定用 v4 足够, 省掉一次 subprocess。"""
    ts = _find_ts()
    if not ts:
        return []
    try:
        r = subprocess.run([ts, "ip", "-4"], capture_output=True, text=True,
                           timeout=8, creationflags=_no_window())
        return [x for x in (r.stdout or "").split()
                if x.count(".") == 3]
    except Exception:  # noqa: BLE001
        return []


def invalidate_peer_cache():
    _PEER_CACHE["t"] = 0.0
