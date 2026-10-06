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

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QComboBox, QLineEdit,
        QPlainTextEdit, QPushButton, QFileDialog, QMessageBox, QLabel,
        QCheckBox, QFrame, QScrollArea,
    )
    from PyQt5.QtCore import Qt, QUrl
    from PyQt5.QtGui import QFont, QDesktopServices
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
    "with_transfer": True,
    "authkey": "",            # 受 remember_auth 开关控制
    "pubkey": "",
    "remember_auth": False,   # authkey 默认【不】记住, 避免明文长期落盘
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
                  has_auth, has_pub, with_transfer=True):
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
    tr = "含传文件工具" if with_transfer else "不含传文件"
    return (f"本次将生成： 【{plat}】 ｜ {mode} ｜ {fmts} ｜ 7-Zip: {z}"
            f" ｜ authkey {auth} ｜ 公钥 {pub} ｜ {tr}")


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


QUICK_TXT_TRANSFER = """【怎么用 —— 部署 + 传文件】
================================================================

  ■ 第一步: 部署远程连接
     Linux   ->  bash deploy.sh
     Windows -> 右键 deploy.bat  选"以管理员身份运行"

     跑完屏幕会打印 Tailscale IP(形如 100.x.x.x), 记下来。

----------------------------------------------------------------
  ■ 第二步: 传文件(双向, 双方都能收也能发)

     【被控端】双击 接收文件.bat (Linux 用 bash 收文件.sh)
        屏幕会显示:  一个 100.x.x.x 的 IP  +  一个 6 位口令
        ★ 保持这个窗口开着, 文件会存到 程序/文件传输/inbox/

     【控制端】把要发的文件/文件夹, 拖到 发送文件.bat 图标上
        按提示填对方的 IP 和口令, 回车即可。

     ★ 不用装 scp / rsync / sftp, 只用 python3 标准库。
     ★ 对方没装 python? 见 README 的说明。

----------------------------------------------------------------
  ■ 第三步: 用完清洗
     Linux   ->  bash clean.sh
     Windows -> 右键 clean.bat  选"以管理员身份运行"

----------------------------------------------------------------
【只有这几个文件要碰】
  deploy.sh / deploy.bat    部署 (只跑一次)
  接收文件.bat / 收文件.sh  启动接收, 保持窗口开着
  发送文件.bat              拖文件到它上面就能发
  clean.sh  / clean.bat     清洗 (用完跑)
  README.md                 说明文档
  使用说明.txt              就是本文件

  『程序』文件夹是实现细节, 不需要打开, 也不用动。
----------------------------------------------------------------
【安全提醒】
  本包内含明文 Tailscale authkey, 只发给你信任的人。
  对方跑完 clean 脚本后本机痕迹会被清除;
  但你的 Tailscale 后台设备列表里仍会留着这台机器,
  要彻底移除请到 https://login.tailscale.com/admin/machines 删除该节点。
"""


# ------------------------------------------------------------ 传文件入口(顶层)
# 这几个只在包根做"定位 + 转交", 实际逻辑在 程序/文件传输/p2p.py
DRAG_SEND_BAT = """@echo off
REM ============================================================
REM  发送文件 —— 把文件/文件夹拖到本文件图标上松手即可
REM
REM  前提: 对方已经双击运行了『接收文件.bat』并把窗口留着
REM  屏幕会显示一个 100.x.x.x 的 IP 和 6 位口令, 按提示填进去。
REM ============================================================
cd /d "%~dp0"
if not exist "程序\\文件传输\\p2p.py" (
  echo [X] 找不到 程序\\文件传输\\p2p.py, 包不完整。
  pause & exit /b 1
)
cd /d "程序\\文件传输"
call dragdrop-send.bat %*
"""

RECV_FILES_BAT = """@echo off
REM ============================================================
REM  接收文件 —— 双击本文件, 保持窗口开着
REM
REM  启动后会显示  6 位口令 + 本机 Tailscale IP,
REM  把这两个告诉对方(或对方直接发过来), 文件就会存到 inbox 文件夹。
REM
REM  依赖: python3 本体即可(标准库), 不需要 scp/rsync/sftp。
REM ============================================================
cd /d "%~dp0"
if not exist "程序\\文件传输\\p2p.py" (
  echo [X] 找不到 程序\\文件传输\\p2p.py, 包不完整。
  pause & exit /b 1
)
cd /d "程序\\文件传输"
python p2p.py serve --dir inbox --gui
if errorlevel 1 (
  echo.
  echo [!] 启动失败。若提示找不到 python, 请先安装 Python 3.8+。
  pause
)
"""

RECV_SH = """#!/usr/bin/env bash
# ============================================================
#  收文件.sh —— 被控端(Linux / macOS) 一键启动文件接收
#
#  bash 收文件.sh
#  屏幕会显示 6 位口令 + 本机 Tailscale IP, 保持窗口开着。
#  收件目录: 程序/文件传输/inbox
#
#  依赖: python3 本体(标准库即可), 不需要 scp / rsync / sftp。
#  GUI 需要 tkinter(可选), 缺了自动降级为命令行, 功能不受影响。
# ============================================================
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/程序/文件传输" 2>/dev/null || {
  echo "[X] 找不到 程序/文件传输/, 包不完整。"; exit 1; }
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done
if [ -z "$PY" ]; then
  echo "[X] 本机没有 python3 / python。"
  echo "    请先装 python3, 或建好通道后直接用 scp。"
  exit 1
fi
exec "$PY" p2p.py serve --dir inbox --gui
"""


def write_root_files(pkg_dir, linux_on, windows_on, with_transfer, log=print):
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

    if with_transfer:
        # 传文件入口: 控制端拖拽发送 / 被控端一键接收
        p = os.path.join(pkg_dir, "发送文件.bat")
        with open(p, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(DRAG_SEND_BAT)
        made.append("发送文件.bat")
        p = os.path.join(pkg_dir, "接收文件.bat")
        with open(p, "w", encoding="utf-8", newline="\r\n") as f:
            f.write(RECV_FILES_BAT)
        made.append("接收文件.bat")
        if linux_on:
            p = os.path.join(pkg_dir, "收文件.sh")
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(RECV_SH)
            os.chmod(p, 0o755)
            made.append("收文件.sh")

    p = os.path.join(pkg_dir, "使用说明.txt")
    with open(p, "w", encoding="utf-8") as f:
        f.write(QUICK_TXT if not with_transfer else QUICK_TXT_TRANSFER)
    made.append("使用说明.txt")
    log(f"  ✓ 顶层入口: {', '.join(made)}")
    return made


# ------------------------------------------------------------ 主体装配
def assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=(), offline=True, with_transfer=True, log=print):
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

    # ---- 传文件工具 (顶层放入口, 逻辑收进 程序/文件传输/) ----
    if with_transfer:
        tsrc = os.path.join(repo_root, "tools")
        if os.path.isdir(tsrc):
            tdst = os.path.join(inner, "文件传输")
            shutil.copytree(tsrc, tdst,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc",
                                                          "inbox", "收件箱"))
            for f in os.listdir(tdst):
                fp = os.path.join(tdst, f)
                if os.path.isfile(fp) and f.endswith((".sh", ".py")):
                    os.chmod(fp, 0o755)
            log("  ✓ 内置传文件工具 → 程序/文件传输/ (p2p.py + 拖拽入口)")
        else:
            log("  ⚠ 未找到 tools/ 目录, 本包不含传文件功能")


def write_pkg_readme(pkg_dir, linux_on, windows_on, has_auth, has_pub,
                     want_7zip=(), offline=True, with_transfer=True, log=print):
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
    if with_transfer:
        L.append("| 3. 传文件（可选） | `bash 收文件.sh` 接收 | 双击 `接收文件.bat` 接收 |")
        L.append("| 4. 用完清洗 | `bash clean.sh` | 右键 `clean.bat` → 以管理员身份运行 |")
    else:
        L.append("| 3. 用完清洗 | `bash clean.sh` | 右键 `clean.bat` → 以管理员身份运行 |")
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
    if with_transfer:
        L.append("接收文件.bat / 收文件.sh  ★ 启动接收, 保持窗口开着")
        L.append("发送文件.bat              ★ 把文件拖到它上面就能发")
    L.append("程序/                    实现细节（不用打开）")
    L.append("  ├── linux/             脚本 + 预置二进制 + 你的密钥")
    L.append("  ├── windows/")
    if with_transfer:
        L.append("  └── 文件传输/           p2p.py 双向传文件")
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
    if with_transfer:
        L.append("## 怎么传文件（双向）")
        L.append("")
        L.append("建好通道后，传文件**不用 scp / rsync / sftp**，只靠 python3 标准库。")
        L.append("")
        L.append("**被控端（收）**")
        L.append("")
        L.append("```")
        L.append("Windows : 双击 接收文件.bat        # 保持窗口开着")
        L.append("Linux   : bash 收文件.sh")
        L.append("```")
        L.append("")
        L.append("屏幕会显示一个 `100.x.x.x` 的 IP 和一个 6 位口令，把它们告诉对方。")
        L.append("收到的文件在 `程序/文件传输/inbox/`。")
        L.append("")
        L.append("**控制端（发）**")
        L.append("")
        L.append("最省事：把要发的文件/文件夹**直接拖到 `发送文件.bat` 图标上**，按提示填 IP 和口令。")
        L.append("")
        L.append("也可以用命令行：")
        L.append("")
        L.append("```bash")
        L.append("cd 程序/文件传输")
        L.append("python p2p.py send 100.x.x.x 报告.pdf")
        L.append("python p2p.py send 100.x.x.x -a 整个目录      # 自动打包成 zip")
        L.append("")
        L.append("# 反向: 从对方取文件")
        L.append("python p2p.py fetch 100.x.x.x -l               # 先看有什么")
        L.append("python p2p.py fetch 100.x.x.x 截图.png         # 取回")
        L.append("```")
        L.append("")
        L.append("> 依赖：**python3 本体即可**。`--gui` 弹窗按钮需要 tkinter，")
        L.append("> 精简 Linux 没有 tkinter 也能正常收文件（自动用命令行模式）。")
        L.append("")
    L.append("## 常见问题")
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
             out_dir, prefix, fmt, want_7zip=(), offline=True,
             with_transfer=True, log=print):
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
                     want_7zip=want_7zip, offline=offline,
                     with_transfer=with_transfer, log=log)
    write_root_files(pkg_dir, linux_on, windows_on, with_transfer, log=log)
    write_pkg_readme(pkg_dir, linux_on, windows_on, bool(auth),
                     bool(pub and windows_on), want_7zip=want_7zip,
                     offline=offline, with_transfer=with_transfer, log=log)

    made = []
    if fmt in ("tar.gz", "both"):
        made.append(make_targz(pkg_dir, out_dir, pkg_name, log))
    if fmt in ("zip", "both"):
        made.append(make_zip(pkg_dir, out_dir, pkg_name, log))
    return made


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
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Tailscale-Remote  离线部署包生成器")
            self.setStyleSheet(STYLE)
            self.resize(1120, 1080)
            self.setMinimumSize(960, 800)
            app = QApplication.instance()
            if app is not None:
                app.setFont(QFont("Microsoft YaHei", 13))
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
            t1 = QLabel("Tailscale-Remote  离线部署包生成器")
            t1.setStyleSheet("font-size:26px;font-weight:bold;color:#12263F;background:transparent;")
            t2 = QLabel("勾选环境 → 填密钥 → 生成包。对方解压后只看得见 deploy / clean 两个要跑的文件。")
            t2.setObjectName("hint")
            hl.addWidget(t1)
            hl.addWidget(t2)
            wrap = QWidget()
            wl = QVBoxLayout(wrap)
            wl.setContentsMargins(14, 12, 14, 0)
            wl.addWidget(head)
            root.addWidget(wrap)

            # ---- 滚动区 ----
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            body = QWidget()
            self.form = QVBoxLayout(body)
            self.form.setContentsMargins(16, 12, 16, 12)
            self.form.setSpacing(12)
            scroll.setWidget(body)
            wrap2 = QWidget()
            wl2 = QVBoxLayout(wrap2)
            wl2.setContentsMargins(0, 0, 0, 0)
            wl2.addWidget(scroll)
            root.addWidget(wrap2, 1)

            self._card_target()
            self._card_mode()
            self._card_keys()
            self._card_output()
            self.form.addStretch(1)

            # ---- 日志 ----
            g_log = QGroupBox("运行日志")
            lv = QVBoxLayout(g_log)
            self.txt_log = QPlainTextEdit()
            self.txt_log.setObjectName("log")
            self.txt_log.setReadOnly(True)
            self.txt_log.setMinimumHeight(190)
            lv.addWidget(self.txt_log)
            self.form.addWidget(g_log)

            # ---- 底部按钮 ----
            foot = QFrame()
            foot.setObjectName("foot")
            fl = QHBoxLayout(foot)
            fl.setContentsMargins(16, 9, 16, 9)
            fl.setSpacing(10)
            self.btn_gen = QPushButton("生成部署包")
            self.btn_gen.setObjectName("primary")
            self.btn_gen.setMinimumHeight(52)
            self.btn_gen.clicked.connect(self.on_generate)
            self.btn_open = QPushButton("打开输出目录")
            self.btn_open.clicked.connect(self.on_open)
            self.btn_clear = QPushButton("清空日志")
            self.btn_clear.clicked.connect(lambda: self.txt_log.clear())
            fl.addWidget(self.btn_gen)
            fl.addWidget(self.btn_open)
            fl.addWidget(self.btn_clear)
            fl.addStretch(1)
            self.lbl_status = _label("")
            fl.addWidget(self.lbl_status)
            wrap3 = QWidget()
            wl3 = QVBoxLayout(wrap3)
            wl3.setContentsMargins(14, 8, 14, 12)
            wl3.addWidget(foot)
            root.addWidget(wrap3)

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
            self.cb_transfer = QCheckBox("带传文件工具（双向+拖拽）")
            self.cb_transfer.setChecked(True)
            h3.addWidget(self.cb_7z_lin)
            h3.addWidget(self.cb_7z_win)
            h3.addSpacing(16)
            h3.addWidget(self.cb_transfer)
            h3.addStretch(1)
            v.addLayout(h3)
            v.addWidget(_label(
                "连解压软件都没有的机器才需要勾 —— 勾上包里会带 7zz / 7za.exe。"
                "首次使用请先运行 python builder/fetch_7zip.py 拉取二进制。", "hint"))
            v.addSpacing(6)
            self.lbl_summary = _label("", "summary")
            v.addWidget(self.lbl_summary)
            self.form.addWidget(g)

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
                self.cb_transfer.isChecked(),
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
                "with_transfer": self.cb_transfer.isChecked(),
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
            self.cb_transfer.setChecked(bool(cfg.get("with_transfer", True)))
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
            self.cb_transfer.toggled.connect(self._on_choice_changed)
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
                                offline=offline,
                                with_transfer=self.cb_transfer.isChecked(),
                                log=self._log)
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


def main():
    if not HAS_QT:
        print("PyQt5 未安装，无法启动图形界面。请先: pip install PyQt5")
        print("(核心打包逻辑仍可无头调用: from build_gui import generate)")
        sys.exit(1)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 13))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
