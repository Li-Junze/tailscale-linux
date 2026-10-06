#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Tailscale-Remote 离线包配置生成器
=================================
操作员（你）在本机运行本程序，按"目标机环境"勾选 / 填写，
一键生成「配置已烘焙、目标机点击即运行」的离线压缩包。

  · 目标机无需联网下载、无需手动填 key
  · 生成的包内含预置二进制（离线零下载）、已写好的 authkey / 公钥
  · 目标机按包内「连接说明.txt」或顶层一键部署脚本即可运行

依赖: PyQt5   (pip install PyQt5)   —— 仅图形界面需要；
      核心打包逻辑不依赖 PyQt5，可单独调用做自动化 / 测试。
运行: python build_gui.py
"""
import os
import sys
import shutil
import tarfile
import zipfile
import datetime

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QGroupBox, QRadioButton, QButtonGroup, QComboBox, QLineEdit,
        QPlainTextEdit, QPushButton, QFileDialog, QMessageBox, QLabel,
        QCheckBox,
    )
    from PyQt5.QtCore import Qt  # noqa: F401
    from PyQt5.QtGui import QFont
    HAS_QT = True
except Exception:  # noqa: BLE001
    HAS_QT = False

# 脚本位于 builder/ ，仓库根 = builder 的上级
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ============================================================ 纯逻辑（无 UI 依赖）
def _tar_filter(ti):
    if ti.name.endswith(".sh") or ti.isdir():
        ti.mode = 0o755
    return ti


def _exe_filter(ti):
    """包内所有可执行物（.sh / .exe / linux 二进制 / 7zz）都要执行位。"""
    name = os.path.basename(ti.name)
    ext = os.path.splitext(name)[1].lower()
    if ti.isdir() or ext in (".sh", ".exe") or name in ("tailscale", "tailscaled", "7zz", "7za"):
        ti.mode = 0o755
    return ti


def make_targz(pkg_dir, out_dir, pkg_name, log=print):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, pkg_name + ".tar.gz")
    arcname = os.path.basename(pkg_dir)
    with tarfile.open(out_path, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        tar.add(pkg_dir, arcname=arcname, filter=_exe_filter)
    size = os.path.getsize(out_path) // 1024
    log(f"  ✓ tar.gz: {out_path}  ({size} KB)")
    return out_path


def make_zip(pkg_dir, out_dir, pkg_name, log=print):
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, pkg_name + ".zip")
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _dirs, files in os.walk(pkg_dir):
            for f in files:
                fp = os.path.join(root, f)
                rel = os.path.relpath(fp, pkg_dir)
                zi = zipfile.ZipInfo(rel)
                base = os.path.basename(f)
                ext = os.path.splitext(base)[1].lower()
                is_exec = (
                    ext in (".sh", ".exe")
                    or base in ("tailscale", "tailscaled", "7zz", "7za")
                )
                zi.external_attr = ((0o755 if is_exec else 0o644) << 16)
                zi.compress_type = zipfile.ZIP_DEFLATED
                with open(fp, "rb") as fh:
                    z.writestr(zi, fh.read())
    size = os.path.getsize(out_path) // 1024
    log(f"  ✓ zip:    {out_path}  ({size} KB)")
    return out_path


def write_top_readme(pkg_dir, linux_on, windows_on, has_auth, has_pub,
                     want_7zip=(), log=print):
    L = []
    L.append("Tailscale-Remote 离线包 —— 使用说明")
    L.append("=" * 46)
    L.append("")
    L.append("本包已预置配置：目标机无需联网下载、无需手动填 key，按下方「运行」即可。")
    L.append("")
    L.append("【已内置】")
    L.append("  · Tailscale Authkey：" + ("已写入各平台 keys/authkey.local.txt" if has_auth else "未预置（运行时需手动粘贴）"))
    if windows_on:
        L.append("  · 控制端公钥：" + ("已写入 windows/keys/control.pub" if has_pub else "未预置（Windows 运行时可自动生成或粘贴）"))
    L.append("  · 二进制：已预置（离线零下载）")
    if want_7zip:
        L.append("  · 便携 7-Zip：已内置（目标机无任何解压工具时用）")
        for p in want_7zip:
            L.append("      " + ("linux/assets/7zip/7zz" if p == "linux" else "windows/assets/7zip/7za.exe"))
        L.append("")
        L.append("【若目标机连解压都没有】")
        L.append("  请把压缩包后缀改成 .zip 后用 Windows 资源管理器/7-Zip 打开；")
        L.append("  Linux 可执行：./assets/7zip/7zz x 包.tar.gz")
        L.append("  Windows 可执行：.\\assets\\7zip\\7za.exe x 包.tar.gz")
    L.append("")
    L.append("【运行】")
    if linux_on:
        L.append("  · Linux 被控端：双击「一键部署.sh」或「linux/运行.sh」，或终端执行")
        L.append("        cd linux && bash connect-offline.sh")
    if windows_on:
        L.append("  · Windows 被控端：右键「windows/connect-windows.bat」→ 以管理员身份运行")
        L.append("        （也可双击顶层「一键部署.bat」）")
    L.append("")
    L.append("【控制端连接】（在您自己的电脑上）")
    if linux_on:
        L.append("  · Linux：  ssh <用户名>@<目标Tailscale IP>")
        L.append("            或 tailscale ssh <用户名>@<目标IP>")
    if windows_on:
        L.append("  · Windows：ssh <Windows用户名>@<目标Tailscale IP>")
    L.append("  运行后脚本会打印目标的 Tailscale IP（100.x.x.x），记下它即可连接。")
    L.append("")
    L.append("【用完清理】")
    if linux_on:
        L.append("  · Linux：  cd linux && bash clean.sh")
    if windows_on:
        L.append("  · Windows：右键 windows/clean-windows.bat → 管理员")
    L.append("")
    L.append("⚠ 安全：authkey 明文存于包内 keys/，仅发可信目标；对方 clean 会清除。")
    L.append("   彻底下线还需到 https://login.tailscale.com/admin/machines 删除节点。")
    with open(os.path.join(pkg_dir, "连接说明.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    log("  ✓ 写入 连接说明.txt")


def assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=(), log=print):
    """platforms: iterable with 'linux' / 'windows'
       want_7zip: 需要内置便携 7-Zip 的平台集合（'linux' / 'windows'）"""
    for plat in platforms:
        src = os.path.join(repo_root, plat)
        dst = os.path.join(pkg_dir, plat)
        log(f"复制 {plat}/ → 包内 …")
        shutil.copytree(
            src, dst,
            ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc", "_build"),
        )

        # ---- 便携 7-Zip: 未勾选则从包里剔除, 勾选但缺失则告警 ----
        zdir = os.path.join(dst, "assets", "7zip")
        if plat in want_7zip:
            exe = "7zz" if plat == "linux" else "7za.exe"
            fp = os.path.join(zdir, exe)
            if os.path.isfile(fp):
                os.chmod(fp, 0o755)
                kb = os.path.getsize(fp) // 1024
                log(f"  ✓ 内置便携 7-Zip（{exe}, {kb} KB）")
            else:
                log(f"  ⚠ 勾选了内置 7-Zip，但未找到 {plat}/assets/7zip/{exe}，将不含解压工具。")
                log("     先运行: python builder/fetch_7zip.py  （联网下载官方二进制）后重试。")
        elif os.path.isdir(zdir):
            shutil.rmtree(zdir)
            log("  · 未勾选，已剔除 assets/7zip（减包体积）")

        keys_dir = os.path.join(dst, "keys")
        os.makedirs(keys_dir, exist_ok=True)
        if auth:
            with open(os.path.join(keys_dir, "authkey.local.txt"), "w", encoding="utf-8") as f:
                f.write("TS_AUTHKEY=" + auth + "\n")
            log(f"  ✓ 写入 {plat}/keys/authkey.local.txt")
        if plat == "windows" and pub:
            with open(os.path.join(keys_dir, "control.pub"), "w", encoding="utf-8") as f:
                f.write(pub + "\n")
            log(f"  ✓ 写入 {plat}/keys/control.pub（控制端公钥）")

        assets = os.path.join(dst, "assets")
        if plat == "linux":
            if not os.path.isfile(os.path.join(assets, "tailscale")):
                log("  ⚠ 未找到 Linux 预置二进制 assets/tailscale：包将不含离线二进制，目标需联网。")
            if arch == "arm64":
                log("  ⚠ 当前仅预置 amd64 二进制；arm64 目标请自行替换 assets/tailscale、tailscaled。")
        else:
            msilist = (
                [f for f in os.listdir(assets) if f.lower().endswith(".msi")]
                if os.path.isdir(assets) else []
            )
            if not msilist:
                log("  ⚠ 未找到 Windows 预置 MSI：包将不含离线安装包，目标需联网安装 Tailscale。")

        if plat == "linux":
            run_sh = os.path.join(dst, "运行.sh")
            with open(run_sh, "w", encoding="utf-8") as f:
                f.write('#!/usr/bin/env bash\ncd "$(dirname "$0")"\nexec bash connect-offline.sh\n')
            os.chmod(run_sh, 0o755)

        if plat == "linux":
            launcher = os.path.join(pkg_dir, "一键部署.sh")
            with open(launcher, "w", encoding="utf-8") as f:
                f.write('#!/usr/bin/env bash\ncd "$(dirname "$0")/linux"\nexec bash connect-offline.sh\n')
            os.chmod(launcher, 0o755)
        else:
            launcher = os.path.join(pkg_dir, "一键部署.bat")
            with open(launcher, "w", encoding="utf-8") as f:
                f.write('@echo off\ncd /d "%~dp0windows"\ncall connect-windows.bat\n')
    # 顶层 README 参考
    top_readme = os.path.join(repo_root, "README.md")
    if os.path.isfile(top_readme):
        shutil.copy2(top_readme, os.path.join(pkg_dir, "README.md"))


def generate(repo_root, linux_on, windows_on, arch, auth, pub,
             out_dir, prefix, fmt, want_7zip=(), log=print):
    """fmt: 'tar.gz' | 'zip' | 'both'。
       want_7zip: 内置便携 7-Zip 的平台集合（'linux' / 'windows'），空=不带。
       返回生成的包路径列表。"""
    if not (linux_on or windows_on):
        raise ValueError("至少选择一个目标系统")
    platforms = []
    if linux_on:
        platforms.append("linux")
    if windows_on:
        platforms.append("windows")
    want_7zip = tuple(p for p in want_7zip if p in platforms)
    os_tag = "both" if (linux_on and windows_on) else platforms[0]
    date = datetime.date.today().strftime("%Y%m%d")
    pkg_name = f"{prefix}-{os_tag}-{date}"

    build_root = os.path.join(repo_root, "builder", "_build")
    pkg_dir = os.path.join(build_root, pkg_name)
    if os.path.exists(pkg_dir):
        shutil.rmtree(pkg_dir)
    os.makedirs(pkg_dir, exist_ok=True)

    if not auth:
        log("⚠ 未填写 Authkey：目标机运行时会改为交互式粘贴。")
    if windows_on and not pub:
        log("⚠ 未填写控制端公钥：Windows 目标运行时会自动生成或让你粘贴。")

    assemble_package(pkg_dir, platforms, auth, pub, arch, repo_root,
                     want_7zip=want_7zip, log=log)
    write_top_readme(pkg_dir, linux_on, windows_on, bool(auth),
                     bool(pub and windows_on), want_7zip=want_7zip, log=log)

    made = []
    if fmt in ("tar.gz", "both"):
        made.append(make_targz(pkg_dir, out_dir, pkg_name, log))
    if fmt in ("zip", "both"):
        made.append(make_zip(pkg_dir, out_dir, pkg_name, log))
    return made


# ============================================================ 图形界面
if HAS_QT:
    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("Tailscale-Remote 离线包配置生成器")
            self.resize(840, 830)
            app = QApplication.instance()
            if app is not None:
                app.setFont(QFont("Microsoft YaHei", 9))
            self._build_ui()
            self._refresh_linux_arch_state()

        def _build_ui(self):
            central = QWidget()
            self.setCentralWidget(central)
            root = QVBoxLayout(central)
            root.setSpacing(10)
            root.setContentsMargins(14, 14, 14, 14)

            g_env = QGroupBox("① 目标环境（被控端）")
            env_v = QVBoxLayout(g_env)
            h1 = QHBoxLayout()
            self.rb_linux = QRadioButton("Linux")
            self.rb_windows = QRadioButton("Windows")
            self.rb_both = QRadioButton("双端 (Linux + Windows)")
            self.os_group = QButtonGroup(self)
            for i, rb in enumerate([self.rb_linux, self.rb_windows, self.rb_both]):
                self.os_group.addButton(rb, i)
                h1.addWidget(rb)
            self.rb_both.setChecked(True)
            env_v.addLayout(h1)
            h2 = QHBoxLayout()
            h2.addWidget(QLabel("Linux 架构:"))
            self.cmb_arch = QComboBox()
            self.cmb_arch.addItems(["amd64 (x86_64)", "arm64 (aarch64)"])
            h2.addWidget(self.cmb_arch)
            h2.addSpacing(24)
            h2.addWidget(QLabel("Windows 架构:"))
            h2.addWidget(QLabel("<b>amd64</b>（仅此）"))
            h2.addStretch(1)
            env_v.addLayout(h2)
            self.os_group.buttonClicked.connect(self._refresh_linux_arch_state)
            root.addWidget(g_env)

            g_cred = QGroupBox("② 凭证与密钥（会烘焙进包内）")
            cred_v = QVBoxLayout(g_cred)
            hl1 = QHBoxLayout()
            hl1.addWidget(QLabel("Tailscale Authkey:"))
            self.ed_auth = QLineEdit()
            self.ed_auth.setEchoMode(QLineEdit.Password)
            self.ed_auth.setPlaceholderText("tskey-auth-xxxx（可带或不带 TS_AUTHKEY= 前缀）")
            hl1.addWidget(self.ed_auth, 1)
            btn_auth = QPushButton("浏览…")
            btn_auth.clicked.connect(lambda: self._load_file(self.ed_auth, False))
            hl1.addWidget(btn_auth)
            cred_v.addLayout(hl1)
            hl2 = QHBoxLayout()
            hl2.addWidget(QLabel("控制端公钥:"))
            self.ed_pub = QPlainTextEdit()
            self.ed_pub.setPlaceholderText(
                "ssh-ed25519 AAAA…（仅 Windows 目标需要；粘贴整行或浏览 .pub 文件；留空则目标端自动生成）"
            )
            self.ed_pub.setMaximumHeight(62)
            hl2.addWidget(self.ed_pub, 1)
            btn_pub = QPushButton("浏览…")
            btn_pub.clicked.connect(self._load_file_pub)
            hl2.addWidget(btn_pub)
            cred_v.addLayout(hl2)
            cred_v.addWidget(
                QLabel(
                    "⚠ Authkey 以明文写入包内 keys/，仅发给可信目标；对方 clean 会清除。"
                    "建议用「限设备 / 可过期」的 key。",
                )
            )
            root.addWidget(g_cred)

            g_opt = QGroupBox("③ 打包选项")
            opt_v = QVBoxLayout(g_opt)
            hl3 = QHBoxLayout()
            hl3.addWidget(QLabel("输出格式:"))
            self.cmb_fmt = QComboBox()
            self.cmb_fmt.addItems([".tar.gz（Linux 原生）", ".zip（Windows 原生）", "两者都生成"])
            self.cmb_fmt.setCurrentIndex(2)
            hl3.addWidget(self.cmb_fmt)
            opt_v.addLayout(hl3)
            hl4 = QHBoxLayout()
            hl4.addWidget(QLabel("输出目录:"))
            self.ed_out = QLineEdit(os.path.expanduser("~/Desktop"))
            hl4.addWidget(self.ed_out, 1)
            btn_out = QPushButton("浏览…")
            btn_out.clicked.connect(self._pick_out)
            hl4.addWidget(btn_out)
            opt_v.addLayout(hl4)
            hl5 = QHBoxLayout()
            hl5.addWidget(QLabel("包名前缀:"))
            self.ed_prefix = QLineEdit("tailscale-remote")
            hl5.addWidget(self.ed_prefix, 1)
            opt_v.addLayout(hl5)

            hl6 = QHBoxLayout()
            self.cb_7z_lin = QCheckBox("Linux 包内置便携 7-Zip (7zz, +2.7MB)")
            self.cb_7z_win = QCheckBox("Windows 包内置便携 7-Zip (7za.exe, +0.6MB)")
            hl6.addWidget(self.cb_7z_lin)
            hl6.addSpacing(16)
            hl6.addWidget(self.cb_7z_win)
            hl6.addStretch(1)
            opt_v.addLayout(hl6)
            opt_v.addWidget(
                QLabel(
                    "勾选后，目标机即使没有任何解压工具也能解开本包"
                    "（Linux 用 assets/7zip/7zz，Windows 用 assets\\7zip\\7za.exe）。\n"
                    "确定目标机自带 tar / 7-Zip 时可不勾，能省几 MB 体积。"
                )
            )
            root.addWidget(g_opt)

            h_btn = QHBoxLayout()
            self.btn_gen = QPushButton("▶ 生成压缩包")
            self.btn_gen.setStyleSheet(
                "QPushButton{background:#2e7d32;color:#fff;font-weight:bold;padding:6px 16px;}"
            )
            self.btn_gen.clicked.connect(self.on_generate)
            self.btn_open = QPushButton("打开输出文件夹")
            self.btn_open.clicked.connect(self.on_open)
            self.btn_exit = QPushButton("退出")
            self.btn_exit.clicked.connect(self.close)
            h_btn.addWidget(self.btn_gen)
            h_btn.addWidget(self.btn_open)
            h_btn.addStretch(1)
            h_btn.addWidget(self.btn_exit)
            root.addLayout(h_btn)

            g_log = QGroupBox("运行日志")
            log_v = QVBoxLayout(g_log)
            self.txt_log = QPlainTextEdit()
            self.txt_log.setReadOnly(True)
            log_v.addWidget(self.txt_log)
            root.addWidget(g_log, 1)

        def _refresh_linux_arch_state(self):
            linux_on = self.rb_linux.isChecked() or self.rb_both.isChecked()
            self.cmb_arch.setEnabled(linux_on)
            self.cb_7z_lin.setEnabled(linux_on)
            win_on = self.rb_windows.isChecked() or self.rb_both.isChecked()
            self.cb_7z_win.setEnabled(win_on)

        def _load_file(self, line_edit, _is_pub):
            path, _ = QFileDialog.getOpenFileName(self, "选择 authkey 文件", "", "All Files (*)")
            if path:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        line_edit.setText(f.read().strip())
                except Exception as e:  # noqa: BLE001
                    QMessageBox.warning(self, "读取失败", str(e))

        def _load_file_pub(self):
            path, _ = QFileDialog.getOpenFileName(
                self, "选择控制端公钥文件", "", "Public Key (*.pub);;All Files (*)"
            )
            if path:
                try:
                    with open(path, "r", encoding="utf-8", errors="ignore") as f:
                        self.ed_pub.setPlainText(f.read().strip())
                except Exception as e:  # noqa: BLE001
                    QMessageBox.warning(self, "读取失败", str(e))

        def _pick_out(self):
            d = QFileDialog.getExistingDirectory(self, "选择输出目录", self.ed_out.text())
            if d:
                self.ed_out.setText(d)

        def _log(self, msg):
            self.txt_log.appendPlainText(msg)
            print(msg)

        def on_generate(self):
            self.txt_log.clear()
            linux_on = self.rb_linux.isChecked() or self.rb_both.isChecked()
            windows_on = self.rb_windows.isChecked() or self.rb_both.isChecked()
            if not (linux_on or windows_on):
                QMessageBox.warning(self, "请选择", "至少选择一个目标系统。")
                return
            arch = self.cmb_arch.currentText().split()[0] if linux_on else "amd64"
            auth = self.ed_auth.text().strip()
            if auth.startswith("TS_AUTHKEY="):
                auth = auth[len("TS_AUTHKEY="):].strip()
            pub = self.ed_pub.toPlainText().strip()
            out_dir = self.ed_out.text().strip() or os.path.expanduser("~/Desktop")
            prefix = self.ed_prefix.text().strip() or "tailscale-remote"
            fmt_map = {0: "tar.gz", 1: "zip", 2: "both"}
            fmt = fmt_map[self.cmb_fmt.currentIndex()]
            want_7zip = []
            if linux_on and self.cb_7z_lin.isChecked():
                want_7zip.append("linux")
            if windows_on and self.cb_7z_win.isChecked():
                want_7zip.append("windows")

            if not (
                os.path.isdir(os.path.join(REPO_ROOT, "linux"))
                and os.path.isdir(os.path.join(REPO_ROOT, "windows"))
            ):
                QMessageBox.critical(
                    self, "路径错误",
                    f"未在当前脚本上级找到 linux/ 与 windows/ 目录。\n脚本位置: {REPO_ROOT}",
                )
                return
            try:
                made = generate(REPO_ROOT, linux_on, windows_on, arch, auth, pub,
                                out_dir, prefix, fmt, want_7zip=want_7zip, log=self._log)
            except Exception as e:  # noqa: BLE001
                self._log(f"[X] 生成失败: {e}")
                QMessageBox.critical(self, "失败", str(e))
                return

            self._log("")
            self._log("✅ 生成完成：")
            for m in made:
                self._log("   " + m)
            self._log(f"   输出目录: {out_dir}")
            QMessageBox.information(self, "完成", f"已生成 {len(made)} 个压缩包。\n{out_dir}")

        def on_open(self):
            out_dir = self.ed_out.text().strip() or os.path.expanduser("~/Desktop")
            if os.path.isdir(out_dir):
                try:
                    os.startfile(out_dir)  # type: ignore[attr-defined]
                except Exception:  # noqa: BLE001
                    pass


def main():
    if not HAS_QT:
        print("PyQt5 未安装，无法启动图形界面。请先: pip install PyQt5")
        sys.exit(1)
    app = QApplication(sys.argv)
    app.setFont(QFont("Microsoft YaHei", 9))
    w = MainWindow()
    w.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
