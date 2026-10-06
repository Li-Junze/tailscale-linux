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
import shutil
import tarfile
import zipfile
import datetime
import subprocess

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QRadioButton, QButtonGroup, QComboBox, QLineEdit,
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
    if ti.isdir() or ext in (".sh", ".exe", ".bat") or name in (
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
                is_exec = ext in (".sh", ".exe", ".bat") or base in (
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


# ============================================================ 图形界面
STYLE = """
QWidget { background:#F4F6FB; color:#1F2430; font-size:13px; }
QGroupBox {
    background:#FFFFFF; border:1px solid #DDE3EE; border-radius:8px;
    margin-top:14px; padding:14px 12px 12px 12px; font-weight:bold; color:#2B3A55;
}
QGroupBox::title { subcontrol-origin: margin; left:12px; padding:0 6px; color:#4A5D80; }
QLabel { background:transparent; }
QLabel#hint { color:#6B7A93; font-size:12px; }
QLineEdit, QPlainTextEdit, QComboBox {
    background:#FFFFFF; border:1px solid #C6D0E0; border-radius:5px;
    padding:5px 7px; selection-background-color:#2F6FED;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border:1px solid #2F6FED; }
QRadioButton, QCheckBox { background:transparent; spacing:7px; }
QPushButton {
    background:#EDF1F8; border:1px solid #C6D0E0; border-radius:5px;
    padding:6px 14px; color:#2B3A55;
}
QPushButton:hover { background:#E2E9F5; border-color:#9FB2CE; }
QPushButton:pressed { background:#D6E0F0; }
QPushButton:disabled { color:#A9B4C6; background:#F0F3F8; border-color:#DFE5EE; }
QPushButton#primary {
    background:#2F6FED; color:#FFFFFF; border:none; font-weight:bold; padding:9px 20px;
}
QPushButton#primary:hover { background:#2560DB; }
QPushButton#primary:pressed { background:#1E52BC; }
QPushButton#ghost { background:#FFFFFF; color:#2F6FED; border:1px solid #2F6FED; }
QPushButton#ghost:hover { background:#EEF3FE; }
QPlainTextEdit#log {
    background:#101827; color:#D6E2F5; border:1px solid #24334A; border-radius:6px;
    font-family:Consolas,monospace; font-size:12px;
}
QScrollArea { border:none; background:transparent; }
QFrame#head { background:#FFFFFF; border:1px solid #DDE3EE; border-radius:8px; }
QFrame#foot { background:#FFFFFF; border:1px solid #DDE3EE; border-radius:8px; }
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
            self.resize(920, 940)
            self.setMinimumSize(840, 720)
            app = QApplication.instance()
            if app is not None:
                app.setFont(QFont("Microsoft YaHei", 9))
            self._build_ui()
            self._sync_platform_state()
            self._scan_existing_key()

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
            t1.setStyleSheet("font-size:17px;font-weight:bold;color:#1F2430;background:transparent;")
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
            self.txt_log.setMinimumHeight(150)
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
            self.btn_gen.setMinimumHeight(42)
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
            v.setSpacing(9)
            h1 = QHBoxLayout()
            h1.setSpacing(18)
            self.rb_linux = QRadioButton("Linux 被控端")
            self.rb_windows = QRadioButton("Windows 被控端")
            self.rb_both = QRadioButton("双端都要")
            self.os_group = QButtonGroup(self)
            for i, rb in enumerate([self.rb_linux, self.rb_windows, self.rb_both]):
                self.os_group.addButton(rb, i)
                h1.addWidget(rb)
            self.rb_both.setChecked(True)
            h1.addStretch(1)
            v.addLayout(h1)
            self.os_group.buttonClicked.connect(self._sync_platform_state)

            h2 = QHBoxLayout()
            h2.setSpacing(10)
            h2.addWidget(_label("Linux 架构"))
            self.cmb_arch = QComboBox()
            self.cmb_arch.addItems(["amd64 (x86_64)", "arm64 (aarch64)"])
            self.cmb_arch.setFixedWidth(150)
            h2.addWidget(self.cmb_arch)
            h2.addSpacing(20)
            h2.addWidget(_label("Windows 架构"))
            h2.addWidget(_label("amd64（Windows 10/11 仅此）", "hint"))
            h2.addStretch(1)
            v.addLayout(h2)
            self.form.addWidget(g)

        def _card_mode(self):
            g = QGroupBox("②  运行模式 —— 对方机器能不能上网？")
            v = QVBoxLayout(g)
            v.setSpacing(8)
            self.rb_offline = QRadioButton("离线模式（推荐）— 包内自带二进制，对方完全不联网也能装")
            self.rb_slim = QRadioButton("轻量模式 — 包很小不带二进制，对方需联网自动下载")
            self.mode_group = QButtonGroup(self)
            self.mode_group.addButton(self.rb_offline, 0)
            self.mode_group.addButton(self.rb_slim, 1)
            self.rb_offline.setChecked(True)
            self.rb_offline.toggled.connect(self._sync_mode_hint)
            v.addWidget(self.rb_offline)
            v.addWidget(self.rb_slim)
            self.lbl_mode = _label("", "hint")
            v.addWidget(self.lbl_mode)
            self._sync_mode_hint()
            self.form.addWidget(g)

        def _card_keys(self):
            g = QGroupBox("③  密钥 —— 填上就能免密直连")
            v = QVBoxLayout(g)
            v.setSpacing(9)

            top1 = QHBoxLayout()
            top1.addWidget(_label("Tailscale Authkey"))
            top1.addStretch(1)
            b_open = QPushButton("① 去 Tailscale 后台生成")
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
            h2.addStretch(1)
            self.ed_auth.textChanged.connect(self._check_auth)
            v.addLayout(h2)
            self.lbl_auth = _label("状态：未填写（目标机运行时会提示手动粘贴）", "hint")
            v.addWidget(self.lbl_auth)
            v.addWidget(_label(
                "生成方法：登录 Tailscale 后台 → Settings → Keys → Generate auth key"
                "（建议勾 Reusable、过期设 Disable），复制以 tskey-auth- 开头的那串。", "hint"))

            v.addSpacing(5)

            top2 = QHBoxLayout()
            lbl2 = _label("控制端 SSH 公钥（仅 Windows 被控端需要）")
            lbl2.setWordWrap(False)          # 保持单行, 不被按钮挤换行
            top2.addWidget(lbl2)
            top2.addStretch(1)
            b_gen = QPushButton("② 一键在本机生成密钥对")
            b_gen.setObjectName("ghost")
            b_gen.clicked.connect(self._make_keypair)
            b_force = QPushButton("强制重生成")
            b_force.clicked.connect(lambda: self._make_keypair(force=True))
            top2.addWidget(b_gen)
            top2.addWidget(b_force)
            v.addLayout(top2)

            self.ed_pub = QPlainTextEdit()
            self.ed_pub.setPlaceholderText(
                "ssh-ed25519 AAAA…\n"
                "点上方『一键在本机生成』最省事 —— 自动生成并把公钥填到这里；\n"
                "也可以从 控制端电脑的 ~/.ssh/id_ed25519.pub 复制整行粘进来。")
            self.ed_pub.setMaximumHeight(84)
            v.addWidget(self.ed_pub)
            self.lbl_pub = _label("状态：未提供（Windows 目标机会被提示粘贴，或现场自动生成）", "hint")
            v.addWidget(self.lbl_pub)
            self.ed_pub.textChanged.connect(self._check_pub)
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
            self.cmb_fmt.setFixedWidth(190)
            h1.addWidget(self.cmb_fmt)
            h1.addSpacing(20)
            h1.addWidget(_label("包名前缀"))
            self.ed_prefix = QLineEdit("tailscale-remote")
            self.ed_prefix.setFixedWidth(190)
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
            self.cb_7z_lin = QCheckBox("Linux 包内置便携 7-Zip  (+2.7MB)")
            self.cb_7z_win = QCheckBox("Windows 包内置便携 7-Zip  (+0.6MB)")
            h3.addWidget(self.cb_7z_lin)
            h3.addWidget(self.cb_7z_win)
            h3.addStretch(1)
            v.addLayout(h3)
            v.addWidget(_label(
                "连解压软件都没有的机器才需要勾 —— 勾上包里会带 7zz / 7za.exe。"
                "首次使用请先运行 python builder/fetch_7zip.py 拉取二进制。", "hint"))
            self.form.addWidget(g)

        # ---------------- 状态同步 ----------------
        def _sync_platform_state(self):
            linux_on = self.rb_linux.isChecked() or self.rb_both.isChecked()
            win_on = self.rb_windows.isChecked() or self.rb_both.isChecked()
            self.cmb_arch.setEnabled(linux_on)
            self.cb_7z_lin.setEnabled(linux_on)
            self.cb_7z_win.setEnabled(win_on)
            if not linux_on:
                self.lbl_pub.setStyleSheet("color:#B25E00;")
            else:
                self.lbl_pub.setStyleSheet("")

        def _sync_mode_hint(self):
            if self.rb_offline.isChecked():
                self.lbl_mode.setText(
                    "→ 包约 70MB 起。对方机器断网 / 没有 curl / wget 也能完成部署。最稳妥。")
            else:
                self.lbl_mode.setText(
                    "→ 包约 3MB。适合对方能上网的情况（deploy 会自动下载官方安装包）。")

        # ---------------- 密钥相关 ----------------
        def _log(self, msg):
            self.txt_log.appendPlainText(msg)
            print(msg)

        def _toggle_auth_visible(self):
            if self.ed_auth.echoMode() == QLineEdit.Password:
                self.ed_auth.setEchoMode(QLineEdit.Normal)
            else:
                self.ed_auth.setEchoMode(QLineEdit.Password)

        def _check_auth(self):
            k = normalize_authkey(self.ed_auth.text())
            if not k:
                self.lbl_auth.setText("状态：未填写（目标机运行时会提示手动粘贴）")
                self.lbl_auth.setStyleSheet("color:#6B7A93;")
            elif k.startswith("tskey-auth-"):
                self.lbl_auth.setText(f"状态：✓ 格式正确（{len(k)} 字符）")
                self.lbl_auth.setStyleSheet("color:#1B7F3B;")
            else:
                self.lbl_auth.setText("状态：✗ 不像 authkey，应该以 tskey-auth- 开头")
                self.lbl_auth.setStyleSheet("color:#C0392B;")

        def _check_pub(self):
            t = self.ed_pub.toPlainText().strip()
            if not t:
                self.lbl_pub.setText("状态：未提供（Windows 目标机会被提示粘贴，或现场自动生成）")
                self.lbl_pub.setStyleSheet("")
                return
            first = t.splitlines()[0].strip()
            if first.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-")):
                self.lbl_pub.setText("状态：✓ 公钥格式正确，会自动写入包内并配置免密登录")
                self.lbl_pub.setStyleSheet("color:#1B7F3B;")
            else:
                self.lbl_pub.setText("状态：✗ 格式不对，应为 ssh-ed25519 AAAA… 开头的整行")
                self.lbl_pub.setStyleSheet("color:#C0392B;")

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
            linux_on = self.rb_linux.isChecked() or self.rb_both.isChecked()
            windows_on = self.rb_windows.isChecked() or self.rb_both.isChecked()
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
            offline = self.rb_offline.isChecked()
            want_7zip = []
            if linux_on and self.cb_7z_lin.isChecked():
                want_7zip.append("linux")
            if windows_on and self.cb_7z_win.isChecked():
                want_7zip.append("windows")

            self.txt_log.clear()
            self._log("=" * 62)
            self._log("开始生成部署包")
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


def main():
    if not HAS_QT:
        print("PyQt5 未安装，无法启动图形界面。请先: pip install PyQt5")
        print("(核心打包逻辑仍可无头调用: from build_gui import generate)")
        sys.exit(1)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
