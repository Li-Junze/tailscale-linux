#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_gui.py —— Tailscale-Remote 工具箱（图形界面）

两页：
  左页「生成部署包」  按目标环境勾选，一键生成对方解压后只跑 deploy 的包
  右页「发送文件」    单向把文件发到被控端（Taildrop / scp），被控端零配置

架构要点（本次重构的核心）：
  ★ 纯逻辑全部在 core.py，本文件只管界面
  ★ 任何 subprocess / 打包 / 传输都跑在子线程，绝不阻塞 UI
  ★ 子线程只发信号，绝不去碰 QWidget（跨线程碰控件 = 卡死 / 崩溃 / 不刷新）
  ★ 日志走信号 + 90ms 批量落地，高频输出也不卡界面
"""
import os
import sys
import time
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from core import *                                          # noqa: F401,F403
from core import (                                          # 下划线开头的不会随 * 进来
    dbg, dbg_exc, _human, _dir_size, _find_ts, _hide_console,
    _ts_my_ips, _SendTask, ts_snapshot, invalidate_peer_cache,
)

_hide_console()                                             # noqa: F405

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QLineEdit, QComboBox, QPlainTextEdit, QPushButton,
        QCheckBox, QFrame, QScrollArea, QFileDialog, QMessageBox,
        QListWidget, QListWidgetItem, QAbstractItemView,
        QTableWidget, QTableWidgetItem, QHeaderView, QSizePolicy,
    )
    from PyQt5.QtCore import Qt, QUrl, QTimer, pyqtSignal
    from PyQt5.QtGui import QFont, QDesktopServices, QGuiApplication, QColor
except Exception:                                           # noqa: BLE001
    HAS_QT = False                                          # noqa: F405

# ============================================================ 主题
QSS = """
/* ---------- 基础 ---------- */
QWidget#page     { background:#F4F6FA; }
QFrame#card      { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px; }
QFrame#head      { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px; }
QFrame#foot      { background:#FFFFFF; border:1px solid #E2E8F0; border-radius:14px; }

QLabel#title     { font-size:27px; font-weight:bold; color:#0F172A; background:transparent; }
QLabel#subtitle  { font-size:16px; color:#64748B; background:transparent; }
QLabel#cardTitle { font-size:21px; font-weight:bold; color:#0F172A; background:transparent; }
QLabel#cardSub   { font-size:16px; color:#64748B; background:transparent; }
QLabel#hint      { font-size:16px; color:#64748B; background:transparent; }
QLabel#ok        { font-size:16px; color:#15803D; background:transparent; }
QLabel#bad       { font-size:16px; color:#DC2626; background:transparent; }
QLabel#sum {
    background:#EFF6FF; border:1px solid #BFDBFE; border-radius:10px;
    padding:13px 16px; font-size:17px; color:#1E3A8A;
}
QLabel#field     { font-size:18px; color:#0F172A; background:transparent; }

/* ---------- 输入控件 ---------- */
QLineEdit, QPlainTextEdit, QComboBox {
    background:#FFFFFF; border:1px solid #CBD5E1; border-radius:9px;
    padding:11px 13px; font-size:18px; color:#0F172A;
    selection-background-color:#2563EB; selection-color:#FFFFFF;
}
QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus { border:2px solid #2563EB; }
QComboBox::drop-down { border:none; width:30px; }
QComboBox QAbstractItemView {
    background:#FFFFFF; border:1px solid #CBD5E1; font-size:18px;
    selection-background-color:#2563EB; selection-color:#FFFFFF; padding:4px;
}
QCheckBox { font-size:18px; spacing:11px; background:transparent; padding:5px 0; }
QCheckBox::indicator {
    width:20px; height:20px; border:2px solid #CBD5E1; border-radius:6px;
    background:#FFFFFF;
}
QCheckBox::indicator:hover   { border-color:#2563EB; }
QCheckBox::indicator:checked { background:#2563EB; border-color:#2563EB; }
QCheckBox::indicator:disabled { background:#F1F5F9; border-color:#E2E8F0; }

/* ---------- 按钮 ---------- */
QPushButton {
    background:#F1F5F9; border:1px solid #CBD5E1; border-radius:9px;
    padding:10px 20px; font-size:17px; color:#0F172A;
}
QPushButton:hover   { background:#E2E8F0; border-color:#94A3B8; }
QPushButton:pressed { background:#CBD5E1; }
QPushButton:disabled{ color:#94A3B8; background:#F8FAFC; border-color:#E2E8F0; }
QPushButton#primary {
    background:#2563EB; color:#FFFFFF; border:none;
    font-size:20px; font-weight:bold; padding:14px 36px; border-radius:10px;
}
QPushButton#primary:hover    { background:#1D4ED8; }
QPushButton#primary:pressed  { background:#1E40AF; }
QPushButton#primary:disabled { background:#93B4F7; color:#EFF6FF; }
QPushButton#ghost {
    background:#FFFFFF; color:#2563EB; border:1px solid #93B4F7; font-size:17px;
}
QPushButton#ghost:hover { background:#EFF6FF; }
QPushButton#danger {
    background:#FFFFFF; color:#DC2626; border:1px solid #FCA5A5; font-size:17px;
}
QPushButton#danger:hover { background:#FEF2F2; }

/* ---------- 侧边栏 ---------- */
QWidget#sidebar { background:#101828; }
QWidget#sidebar QLabel { color:#94A3B8; background:transparent; }
QPushButton#nav {
    background:transparent; color:#CBD5E1; border:none; border-radius:10px;
    text-align:left; padding:14px 16px; font-size:19px;
}
QPushButton#nav:hover   { background:#1E293B; color:#FFFFFF; }
QPushButton#nav:checked { background:#2563EB; color:#FFFFFF; font-weight:bold; }
QPushButton#nav:checked:hover { background:#1D4ED8; }
QLabel#brand { color:#FFFFFF; font-size:23px; font-weight:bold; background:transparent; }
QLabel#brandSub { color:#7C8CA5; font-size:15px; background:transparent; }

/* ---------- 列表 / 表格 ---------- */
QListWidget {
    background:#FFFFFF; border:1px solid #CBD5E1; border-radius:10px;
    font-size:17px; padding:6px;
}
QListWidget::item { padding:7px 9px; border-radius:6px; }
QListWidget::item:selected { background:#EFF6FF; color:#1E3A8A; }
QTableWidget {
    background:#FFFFFF; border:1px solid #CBD5E1; border-radius:10px;
    font-size:17px; gridline-color:#E2E8F0;
}
QHeaderView::section {
    background:#F1F5F9; color:#475569; font-size:16px; font-weight:bold;
    border:none; border-bottom:1px solid #CBD5E1; padding:9px 11px;
}
QTableWidget::item { padding:7px 11px; }
QTableWidget::item:selected { background:#EFF6FF; color:#1E3A8A; }

/* ---------- 拖拽区 ---------- */
QFrame#dropzone {
    background:#F8FAFC; border:2px dashed #94A3B8; border-radius:12px;
}
QFrame#dropzone[hot="true"] {
    background:#EFF6FF; border:2px dashed #2563EB;
}

/* ---------- 控制台 ---------- */
QPlainTextEdit#console {
    background:#0F172A; color:#D6E2F5; border:1px solid #1E293B;
    border-radius:10px; font-family:Consolas,"Courier New",monospace;
    font-size:15px; padding:13px;
}

/* ---------- 滚动 ---------- */
QScrollArea { border:none; background:transparent; }
QScrollBar:vertical   { background:#E8EDF5; width:12px; border-radius:6px; margin:0; }
QScrollBar::handle:vertical   { background:#B9C4D4; border-radius:6px; min-height:44px; }
QScrollBar:horizontal { background:#E8EDF5; height:12px; border-radius:6px; }
QScrollBar::handle:horizontal { background:#B9C4D4; border-radius:6px; min-width:44px; }
QScrollBar::add-line, QScrollBar::sub-line { width:0; height:0; }
"""

FONT = "Microsoft YaHei"


def _lbl(text, obj="", wrap=True):
    l = QLabel(text)
    if obj:
        l.setObjectName(obj)
    l.setWordWrap(wrap)
    return l


class DropZone(QFrame):
    """拖放区：自己吃掉拖放事件，不再依赖主窗口转发（旧版偶尔收不到）。"""

    filesDropped = pyqtSignal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(132)
        v = QVBoxLayout(self)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)
        self.lbl = _lbl("把文件或文件夹拖到这里", "hint")
        self.lbl.setAlignment(Qt.AlignCenter)
        self.lbl.setStyleSheet("font-size:19px;color:#334155;font-weight:bold;")
        self.sub = _lbl("也可以点下面的按钮选择，支持一次拖多个", "hint")
        self.sub.setAlignment(Qt.AlignCenter)
        v.addWidget(self.lbl)
        v.addWidget(self.sub)
        v.addStretch(1)

    def _hot(self, on):
        self.setProperty("hot", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, ev):
        if ev.mimeData().hasUrls():
            self._hot(True)
            ev.acceptProposedAction()
        else:
            ev.ignore()

    def dragMoveEvent(self, ev):
        if ev.mimeData().hasUrls():
            ev.acceptProposedAction()

    def dragLeaveEvent(self, ev):
        self._hot(False)

    def dropEvent(self, ev):
        self._hot(False)
        paths = [u.toLocalFile() for u in ev.mimeData().urls()]
        paths = [p for p in paths if p and os.path.exists(p)]
        if paths:
            self.filesDropped.emit(paths)
        ev.acceptProposedAction()


if HAS_QT:

    class MainWindow(QMainWindow):
        # ---- 子线程 -> 主线程 的三条通道（子线程只 emit，绝不碰控件）----
        log_sig = pyqtSignal(str, str)          # (目标控制台, 一行文本)
        job_done = pyqtSignal(str, bool, object)  # (tag, ok, msg|list)
        peers_ready = pyqtSignal(object, object)  # (peers, my_ips)
        sizes_ready = pyqtSignal(object)          # {路径: 字节数}

        MAX_LOG_LINES = 4000

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Tailscale-Remote 工具箱")
            self.setStyleSheet(QSS)
            self.resize(1380, 900)
            self.setMinimumSize(1120, 700)

            self.cfg = load_config()            # noqa: F405
            self.send_files = []
            self._sizes = {}
            self._send_task = None
            self._my_ips = []
            self._peers = []
            self._busy = False
            self._buf = {"gen": [], "send": []}

            self.log_sig.connect(self._on_log_line)
            self.job_done.connect(self._on_job_done)
            self.peers_ready.connect(self._on_peers_ready)
            self.sizes_ready.connect(self._on_sizes)

            self._log_flush = QTimer(self)
            self._log_flush.setInterval(90)
            self._log_flush.timeout.connect(self._flush_log)
            self._log_flush.start()          # ★ 不 start 就永远不落地（日志空白的真凶）

            self._auto_peers = QTimer(self)
            self._auto_peers.setInterval(20000)
            self._auto_peers.timeout.connect(self._auto_refresh_peers)
            self._auto_peers.start()         # 发送页每 20s 静默刷新一次设备

            self._build_ui()
            self._restore_config()
            self._scan_existing_key()

            # 启动后延迟再做重活，窗口先出来（首屏 0 卡顿）
            QTimer.singleShot(400, self.refresh_peers)
            QTimer.singleShot(0, self._center)

        def _center(self):
            try:
                scr = QApplication.primaryScreen().availableGeometry()
                fg = self.frameGeometry()
                fg.moveCenter(scr.center())
                self.move(max(scr.left(), fg.left()), max(scr.top(), fg.top()))
            except Exception:                                  # noqa: BLE001
                pass

        # ==================================================== 界面骨架
        def _build_ui(self):
            root = QWidget()
            root.setObjectName("page")
            self.setCentralWidget(root)
            hl = QHBoxLayout(root)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setSpacing(0)

            # ---------- 侧边栏 ----------
            side = QWidget()
            side.setObjectName("sidebar")
            side.setFixedWidth(256)
            sv = QVBoxLayout(side)
            sv.setContentsMargins(16, 20, 16, 20)
            sv.setSpacing(10)
            sv.addWidget(_lbl("Tailscale", "brand", False))
            sv.addWidget(_lbl("远程工具箱", "brandSub", False))
            sv.addSpacing(18)

            self.btn_nav_gen = QPushButton("  生成部署包")
            self.btn_nav_send = QPushButton("  发送文件")
            for i, b in enumerate((self.btn_nav_gen, self.btn_nav_send)):
                b.setObjectName("nav")
                b.setCheckable(True)
                b.setMinimumHeight(52)
                b.setCursor(Qt.PointingHandCursor)
                b.clicked.connect(lambda _=False, x=i: self._switch_page(x))
                sv.addWidget(b)
            self.btn_nav_gen.setChecked(True)
            sv.addSpacing(10)
            self.lbl_ts_state = _lbl("Tailscale：检测中…", "brandSub")
            sv.addWidget(self.lbl_ts_state)
            sv.addStretch(1)
            tip = _lbl("被控端解压后只跑 deploy\n发文件走 Taildrop，对方零配置",
                       "brandSub")
            sv.addWidget(tip)
            hl.addWidget(side)

            # ---------- 右侧 ----------
            right = QWidget()
            rv = QVBoxLayout(right)
            rv.setContentsMargins(18, 18, 18, 14)
            rv.setSpacing(12)

            head = QFrame()
            head.setObjectName("head")
            hh = QVBoxLayout(head)
            hh.setContentsMargins(20, 16, 20, 16)
            hh.setSpacing(4)
            self.lbl_head_t = _lbl("生成部署包", "title", False)
            self.lbl_head_s = _lbl("", "subtitle")
            hh.addWidget(self.lbl_head_t)
            hh.addWidget(self.lbl_head_s)
            rv.addWidget(head)

            # 两个页面（各自滚动区，切换 visible）
            self.page_gen = QWidget()
            self.page_gen.setObjectName("page")
            self.scroll_gen = QScrollArea()
            self.scroll_gen.setWidgetResizable(True)
            self.scroll_gen.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.form_gen = QVBoxLayout(self.page_gen)
            self.form_gen.setContentsMargins(2, 2, 10, 2)
            self.form_gen.setSpacing(14)
            self.scroll_gen.setWidget(self.page_gen)

            self.page_send = QWidget()
            self.page_send.setObjectName("page")
            self.scroll_send = QScrollArea()
            self.scroll_send.setWidgetResizable(True)
            self.scroll_send.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.form_send = QVBoxLayout(self.page_send)
            self.form_send.setContentsMargins(2, 2, 10, 2)
            self.form_send.setSpacing(14)
            self.scroll_send.setWidget(self.page_send)

            rv.addWidget(self.scroll_gen, 1)
            rv.addWidget(self.scroll_send, 1)
            self.scroll_send.setVisible(False)

            # ---------- 底部操作条 ----------
            foot = QFrame()
            foot.setObjectName("foot")
            fl = QHBoxLayout(foot)
            fl.setContentsMargins(16, 12, 16, 12)
            fl.setSpacing(10)

            self.wid_gen_btn = QWidget()
            gl = QHBoxLayout(self.wid_gen_btn)
            gl.setContentsMargins(0, 0, 0, 0)
            gl.setSpacing(10)
            self.btn_gen = QPushButton("生成部署包")
            self.btn_gen.setObjectName("primary")
            self.btn_gen.setCursor(Qt.PointingHandCursor)
            self.btn_gen.clicked.connect(self.on_generate)
            self.btn_open = QPushButton("打开输出目录")
            self.btn_open.clicked.connect(self.on_open)
            self.btn_clr_gen = QPushButton("清空日志")
            self.btn_clr_gen.clicked.connect(lambda: self.txt_gen.clear())
            gl.addWidget(self.btn_gen)
            gl.addWidget(self.btn_open)
            gl.addWidget(self.btn_clr_gen)
            fl.addWidget(self.wid_gen_btn)

            self.wid_send_btn = QWidget()
            sl = QHBoxLayout(self.wid_send_btn)
            sl.setContentsMargins(0, 0, 0, 0)
            sl.setSpacing(10)
            self.btn_send = QPushButton("开始发送")
            self.btn_send.setObjectName("primary")
            self.btn_send.setCursor(Qt.PointingHandCursor)
            self.btn_send.clicked.connect(self.on_send)
            self.btn_stop = QPushButton("停止")
            self.btn_stop.setObjectName("danger")
            self.btn_stop.clicked.connect(self.on_stop)
            self.btn_stop.setEnabled(False)
            self.btn_clr_send = QPushButton("清空列表")
            self.btn_clr_send.clicked.connect(self.on_send_clear)
            sl.addWidget(self.btn_send)
            sl.addWidget(self.btn_stop)
            sl.addWidget(self.btn_clr_send)
            fl.addWidget(self.wid_send_btn)

            fl.addStretch(1)
            self.lbl_status = _lbl("", "hint", False)
            fl.addWidget(self.lbl_status)
            rv.addWidget(foot)
            hl.addWidget(right, 1)

            # 页内容
            self._build_page_gen()
            self._build_page_send()
            self._switch_page(0)

        # ---------- 卡片工厂 ----------
        def _card(self, parent, title, sub=""):
            f = QFrame()
            f.setObjectName("card")
            v = QVBoxLayout(f)
            v.setContentsMargins(18, 16, 18, 18)
            v.setSpacing(11)
            v.addWidget(_lbl(title, "cardTitle", False))
            if sub:
                v.addWidget(_lbl(sub, "cardSub"))
            parent.addWidget(f)
            return f, v

        # ==================================================== 页 1：生成部署包
        def _build_page_gen(self):
            L = self.form_gen

            # ① 目标环境
            c, v = self._card(L, "①  目标环境", "要连哪台机器？")
            h = QHBoxLayout()
            h.setSpacing(12)
            h.addWidget(_lbl("目标平台", "field", False))
            self.cmb_os = QComboBox()
            self.cmb_os.addItems(["Linux 被控端", "Windows 被控端",
                                  "双端都要 (Linux + Windows)"])
            self.cmb_os.setMinimumWidth(300)
            h.addWidget(self.cmb_os)
            h.addSpacing(20)
            h.addWidget(_lbl("Linux 架构", "field", False))
            self.cmb_arch = QComboBox()
            self.cmb_arch.addItems(["amd64 (x86_64)", "arm64 (aarch64)"])
            self.cmb_arch.setFixedWidth(210)
            h.addWidget(self.cmb_arch)
            h.addStretch(1)
            v.addLayout(h)
            v.addWidget(_lbl("Windows 只有 amd64（Win10/11 通用），无需选择。",
                             "hint"))

            # ② 运行模式
            c, v = self._card(L, "②  运行模式", "对方机器能不能上网？")
            h = QHBoxLayout()
            h.setSpacing(12)
            h.addWidget(_lbl("模式", "field", False))
            self.cmb_mode = QComboBox()
            self.cmb_mode.addItems([
                "离线（推荐）· 自带二进制，对方不联网也能装",
                "轻量 · 不带二进制（约 20KB），对方需联网自动下载",
            ])
            self.cmb_mode.setMinimumWidth(470)
            h.addWidget(self.cmb_mode)
            h.addStretch(1)
            v.addLayout(h)
            self.lbl_mode = _lbl("", "hint")
            v.addWidget(self.lbl_mode)

            # ③ 密钥
            c, v = self._card(L, "③  密钥", "填上就能免密直连")
            t1 = QHBoxLayout()
            t1.addWidget(_lbl("Tailscale Authkey", "field", False))
            t1.addStretch(1)
            b_open = QPushButton("去后台生成 authkey")
            b_open.setObjectName("ghost")
            b_open.clicked.connect(self._open_keys_url)
            t1.addWidget(b_open)
            v.addLayout(t1)

            self.ed_auth = QLineEdit()
            self.ed_auth.setEchoMode(QLineEdit.Password)
            self.ed_auth.setPlaceholderText(
                "tskey-auth-… （可带 TS_AUTHKEY= 前缀；留空则对方手填）")
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
            h2.addSpacing(16)
            self.cb_remember = QCheckBox("记住 authkey（明文存本机）")
            h2.addWidget(self.cb_remember)
            h2.addStretch(1)
            v.addLayout(h2)
            self.lbl_auth = _lbl("", "hint")
            v.addWidget(self.lbl_auth)
            v.addWidget(_lbl(
                "后台 → Settings → Keys → Generate auth key（建议勾 Reusable、"
                "过期设 Disable），复制 tskey-auth- 开头那串。", "hint"))

            t2 = QHBoxLayout()
            t2.addWidget(_lbl("控制端 SSH 公钥（仅 Windows 需要）", "field", False))
            t2.addStretch(1)
            b_gen = QPushButton("一键生成密钥对")
            b_gen.setObjectName("ghost")
            b_gen.clicked.connect(self._make_keypair)
            b_force = QPushButton("重新生成")
            b_force.clicked.connect(lambda: self._make_keypair(force=True))
            t2.addWidget(b_gen)
            t2.addWidget(b_force)
            v.addLayout(t2)

            self.ed_pub = QPlainTextEdit()
            self.ed_pub.setPlaceholderText(
                "ssh-ed25519 AAAA…\n点上方『一键生成密钥对』最省事；"
                "也可从 ~/.ssh/id_ed25519.pub 复制整行粘进来。")
            self.ed_pub.setFixedHeight(96)
            v.addWidget(self.ed_pub)
            self.lbl_pub = _lbl("", "hint")
            v.addWidget(self.lbl_pub)

            # ④ 打包选项
            c, v = self._card(L, "④  打包选项")
            h = QHBoxLayout()
            h.setSpacing(12)
            h.addWidget(_lbl("压缩格式", "field", False))
            self.cmb_fmt = QComboBox()
            self.cmb_fmt.addItems([".tar.gz（Linux 原生）", ".zip（Windows 原生）",
                                   "两种都生成"])
            self.cmb_fmt.setFixedWidth(240)
            h.addWidget(self.cmb_fmt)
            h.addSpacing(18)
            h.addWidget(_lbl("包名前缀", "field", False))
            self.ed_prefix = QLineEdit("tailscale-remote")
            self.ed_prefix.setFixedWidth(240)
            h.addWidget(self.ed_prefix)
            h.addStretch(1)
            v.addLayout(h)

            h = QHBoxLayout()
            h.setSpacing(10)
            h.addWidget(_lbl("输出目录", "field", False))
            self.ed_out = QLineEdit(os.path.join(os.path.expanduser("~"), "Desktop"))
            h.addWidget(self.ed_out, 1)
            b_out = QPushButton("浏览…")
            b_out.clicked.connect(self._pick_out)
            h.addWidget(b_out)
            v.addLayout(h)

            h = QHBoxLayout()
            h.setSpacing(18)
            self.cb_7z_lin = QCheckBox("Linux 内置 7-Zip (+2.7MB)")
            self.cb_7z_win = QCheckBox("Windows 内置 7-Zip (+0.6MB)")
            h.addWidget(self.cb_7z_lin)
            h.addWidget(self.cb_7z_win)
            h.addStretch(1)
            v.addLayout(h)
            v.addWidget(_lbl("连解压软件都没有的机器才需要勾。首次用请先跑 "
                             "python builder/fetch_7zip.py", "hint"))
            self.lbl_summary = _lbl("", "sum")
            v.addWidget(self.lbl_summary)

            # 日志
            c, v = self._card(L, "运行日志")
            self.txt_gen = QPlainTextEdit()
            self.txt_gen.setObjectName("console")
            self.txt_gen.setReadOnly(True)
            self.txt_gen.setMaximumBlockCount(self.MAX_LOG_LINES)
            self.txt_gen.setMinimumHeight(200)
            v.addWidget(self.txt_gen)
            L.addStretch(1)

        # ==================================================== 页 2：发送文件
        def _build_page_send(self):
            L = self.form_send

            # ① 发给谁
            c, v = self._card(L, "①  发给谁", "从 tailnet 里挑一台，双击表格行即选中")
            h = QHBoxLayout()
            h.setSpacing(10)
            h.addWidget(_lbl("被控端", "field", False))
            self.cmb_peer = QComboBox()
            self.cmb_peer.setMinimumWidth(420)
            self.cmb_peer.setEditable(True)
            self.cmb_peer.setPlaceholderText("100.x.x.x")
            h.addWidget(self.cmb_peer, 1)
            b_rf = QPushButton("刷新列表")
            b_rf.setObjectName("ghost")
            b_rf.clicked.connect(lambda: self.refresh_peers(force=True))
            h.addWidget(b_rf)
            v.addLayout(h)

            h = QHBoxLayout()
            h.setSpacing(10)
            h.addWidget(_lbl("方式", "field", False))
            self.cmb_method = QComboBox()
            self.cmb_method.addItems([
                "Taildrop（推荐）· 对方零配置，只需 tailscale file get",
                "scp · 对方需已跑过 deploy（SSH 已就绪）",
            ])
            h.addWidget(self.cmb_method, 1)
            v.addLayout(h)

            self.row_scp = QWidget()
            rl = QHBoxLayout(self.row_scp)
            rl.setContentsMargins(0, 0, 0, 0)
            rl.setSpacing(10)
            rl.addWidget(_lbl("对方用户名", "field", False))
            self.ed_user = QLineEdit()
            self.ed_user.setPlaceholderText("对方机器上的用户名")
            self.ed_user.setFixedWidth(180)
            rl.addWidget(self.ed_user)
            rl.addWidget(_lbl("落到目录", "field", False))
            self.ed_dir = QLineEdit("~")
            self.ed_dir.setFixedWidth(150)
            rl.addWidget(self.ed_dir)
            rl.addStretch(1)
            v.addWidget(self.row_scp)

            self.lbl_tip = _lbl("", "sum")
            v.addWidget(self.lbl_tip)

            self.tbl = QTableWidget(0, 4)
            self.tbl.setHorizontalHeaderLabels(["IP（双击复制并选中）", "名称", "系统", "状态"])
            self.tbl.verticalHeader().setVisible(False)
            self.tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
            self.tbl.setSelectionMode(QAbstractItemView.SingleSelection)
            self.tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
            self.tbl.setMinimumHeight(180)
            self.tbl.setMaximumHeight(300)
            th = self.tbl.horizontalHeader()
            th.setSectionResizeMode(0, QHeaderView.ResizeToContents)
            th.setSectionResizeMode(1, QHeaderView.Stretch)
            th.setSectionResizeMode(2, QHeaderView.ResizeToContents)
            th.setSectionResizeMode(3, QHeaderView.ResizeToContents)
            self.tbl.doubleClicked.connect(self._on_tbl_dblclick)
            self.tbl.itemSelectionChanged.connect(self._on_tbl_select)
            v.addWidget(self.tbl)

            h = QHBoxLayout()
            h.setSpacing(10)
            b_cp = QPushButton("复制 IP")
            b_cp.setObjectName("ghost")
            b_cp.clicked.connect(self.on_copy_ip)
            h.addWidget(b_cp)
            b_get = QPushButton("复制取文件命令")
            b_get.setObjectName("ghost")
            b_get.clicked.connect(self.on_copy_getcmd)
            h.addWidget(b_get)
            b_del = QPushButton("移除该节点")
            b_del.setObjectName("danger")
            b_del.clicked.connect(self.on_delete_peer)
            h.addWidget(b_del)
            h.addStretch(1)
            self.lbl_manage = _lbl("", "hint", False)
            h.addWidget(self.lbl_manage)
            v.addLayout(h)

            # ② 发什么
            c, v = self._card(L, "②  发什么", "拖进来，或用按钮选择")
            self.drop = DropZone()
            self.drop.filesDropped.connect(self.add_send_files)
            v.addWidget(self.drop)
            h = QHBoxLayout()
            h.addStretch(1)
            b1 = QPushButton("选择文件…")
            b1.setObjectName("ghost")
            b1.clicked.connect(self.on_pick_files)
            h.addWidget(b1)
            b2 = QPushButton("选择文件夹…")
            b2.setObjectName("ghost")
            b2.clicked.connect(self.on_pick_dir)
            h.addWidget(b2)
            b3 = QPushButton("移除选中")
            b3.clicked.connect(self.on_remove_sel)
            h.addWidget(b3)
            h.addStretch(1)
            v.addLayout(h)
            self.lst_files = QListWidget()
            self.lst_files.setMinimumHeight(110)
            self.lst_files.setMaximumHeight(190)
            v.addWidget(self.lst_files)
            self.lbl_sum = _lbl("还没选文件", "hint")
            v.addWidget(self.lbl_sum)

            # ③ 日志
            c, v = self._card(L, "③  传输日志")
            self.txt_send = QPlainTextEdit()
            self.txt_send.setObjectName("console")
            self.txt_send.setReadOnly(True)
            self.txt_send.setMaximumBlockCount(self.MAX_LOG_LINES)
            self.txt_send.setMinimumHeight(190)
            v.addWidget(self.txt_send)
            L.addStretch(1)

        # ==================================================== 页面切换
        def _switch_page(self, idx):
            gen = (idx == 0)
            self.btn_nav_gen.setChecked(gen)
            self.btn_nav_send.setChecked(not gen)
            self.scroll_gen.setVisible(gen)
            self.scroll_send.setVisible(not gen)
            self.wid_gen_btn.setVisible(gen)
            self.wid_send_btn.setVisible(not gen)
            self.lbl_head_t.setText("生成部署包" if gen else "发送文件到被控端")
            self.lbl_head_s.setText(
                "按目标环境勾选 → 一键生成：对方解压后只跑 deploy，用完跑 clean。"
                if gen else
                "单向发送：对方什么都不用装，收到后执行 tailscale file get 取走。")
            self.lbl_status.setText("")
            if not gen:
                QTimer.singleShot(120, self.refresh_peers)

        # ==================================================== 日志（线程安全）
        def _log(self, target, s):
            """主线程/子线程都能调：只入缓冲，由定时器批量落地。"""
            try:
                self._buf.setdefault(target, []).append(str(s))
            except Exception:                                # noqa: BLE001
                pass

        def _on_log_line(self, target, s):
            self._buf.setdefault(target, []).append(s)

        def _flush_log(self):
            for tgt, box in (("gen", self.txt_gen), ("send", self.txt_send)):
                lines = self._buf.get(tgt)
                if not lines:
                    continue
                # 一次最多落地 300 行: 极端刷屏时也不会一次拼出巨大文本块
                chunk, rest = lines[:300], lines[300:]
                self._buf[tgt] = rest
                box.appendPlainText("\n".join(chunk))
                sb = box.verticalScrollBar()
                sb.setValue(sb.maximum())

        def _log_now(self, target, s):
            self._log(target, s)
            self._flush_log()

        def _show_log(self, target):
            """把日志区滚进视野 —— 否则跑完什么都看不到（用户以为没反应）。"""
            try:
                box = self.txt_gen if target == "gen" else self.txt_send
                area = self.scroll_gen if target == "gen" else self.scroll_send
                QTimer.singleShot(60, lambda: area.ensureWidgetVisible(box, 0, 40))
            except Exception:                                  # noqa: BLE001
                pass

        # ==================================================== 后台任务框架
        def _bg(self, tag, fn):
            """把 fn() 扔到子线程；fn 返回 (ok, msg)，结果走 job_done 回主线程。"""

            def _w():
                ok, msg = False, ""
                try:
                    ok, msg = fn()
                except BaseException as e:                    # noqa: BLE001
                    dbg_exc("bg:" + tag)
                    msg = f"{type(e).__name__}: {e}"
                finally:
                    try:
                        self.job_done.emit(tag, ok, msg)
                    except BaseException:                     # noqa: BLE001
                        dbg_exc("emit:" + tag)

            threading.Thread(target=_w, daemon=True).start()

        def _set_busy(self, on, gen_btn=True):
            self._busy = on
            if gen_btn:
                self.btn_gen.setEnabled(not on)
                self.btn_gen.setText("生成中…" if on else "生成部署包")
            else:
                self.btn_send.setEnabled(not on)
                self.btn_stop.setEnabled(on)
                self.btn_send.setText("发送中…" if on else "开始发送")

        def _on_job_done(self, tag, ok, msg):
            dbg(f"[ui] job done tag={tag} ok={ok} msg={msg}")
            try:
                if tag == "gen":
                    self._set_busy(False, True)
                    self._on_gen_done(ok, msg)
                elif tag == "send":
                    self._set_busy(False, False)
                    self._on_send_done(ok, msg)
                elif tag == "logout":
                    self._on_logout_done(ok, msg)
                elif tag == "peers":
                    pass
            except Exception:                                 # noqa: BLE001
                dbg_exc("on_job_done")

        def closeEvent(self, ev):
            try:
                save_config(self._snapshot_config())           # noqa: F405
            except Exception:                                  # noqa: BLE001
                pass
            ev.accept()

        # ==================================================== 统一拖拽（主窗口兜底）
        def dragEnterEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dragMoveEvent(self, ev):
            if ev.mimeData().hasUrls():
                ev.acceptProposedAction()

        def dropEvent(self, ev):
            paths = [u.toLocalFile() for u in ev.mimeData().urls()]
            paths = [p for p in paths if p and os.path.exists(p)]
            if paths:
                self._switch_page(1)
                self.add_send_files(paths)
            ev.acceptProposedAction()

        # ==================================================== 设备列表
        def refresh_peers(self, force=False):
            if force:
                invalidate_peer_cache()

            def _w():
                try:
                    peers, my = ts_snapshot(ttl=3.0)
                except BaseException:                          # noqa: BLE001
                    dbg_exc("ts_snapshot")
                    peers, my = [], []
                try:
                    self.peers_ready.emit(peers or [], my or [])
                except BaseException:                          # noqa: BLE001
                    dbg_exc("emit peers")
            threading.Thread(target=_w, daemon=True).start()

        def _auto_refresh_peers(self):
            if self.scroll_send.isVisible() and not self._busy:
                self.refresh_peers()

        def _on_peers_ready(self, peers, my):
            self._peers = peers or []
            self._my_ips = my or []
            ts_ok = bool(self._my_ips) or bool(peers)
            self.lbl_ts_state.setText(
                "Tailscale：已连接" if ts_ok else "Tailscale：未连接 / 未安装")
            self.cmb_peer.blockSignals(True)
            keep = self.cmb_peer.currentText()
            self.cmb_peer.clear()
            for p in self._peers:
                tag = "  （离线）" if p.get("offline") else ""
                osn = f" · {p['os']}" if p.get("os") else ""
                self.cmb_peer.addItem(f"{p['ip']}   {p['name']}{osn}{tag}", p)
            for h in (self.cfg.get("send_history") or []):
                if h.get("ip") and not any(x.get("ip") == h.get("ip")
                                           for x in self._peers):
                    self.cmb_peer.addItem(f"{h['ip']}   （历史）", h)
            self.cmb_peer.blockSignals(False)
            if keep:
                i = self.cmb_peer.findText(keep)
                if i >= 0:
                    self.cmb_peer.setCurrentIndex(i)
            self._fill_table()
            self._auto_pick_target()
            if not self._busy:
                self.lbl_status.setText(f"tailnet 内 {len(self._peers)} 台设备")
            dbg(f"[peers] ui updated: {len(self._peers)} devices, "
                f"target={self.current_peer_ip()}")

        def _fill_table(self):
            try:
                self.tbl.setRowCount(0)
                for i, p in enumerate(self._peers):
                    self.tbl.insertRow(i)
                    st = "本机" if p.get("is_self") else ("离线" if p.get("offline") else "在线")
                    for col, txt in enumerate([p["ip"], p.get("name") or "",
                                               p.get("os") or "-", st]):
                        it = QTableWidgetItem(txt)
                        if col == 3:
                            it.setForeground(QColor(
                                "#2563EB" if p.get("is_self")
                                else ("#15803D" if not p.get("offline") else "#94A3B8")))
                        self.tbl.setItem(i, col, it)
            except Exception:                                  # noqa: BLE001
                dbg_exc("fill_table")

        def _auto_pick_target(self):
            """没选目标时，自动挑第一台在线的『别人』的机器 —— 省一次点击。"""
            if self.cmb_peer.currentIndex() >= 0 and self.current_peer_ip():
                return
            for i, p in enumerate(self._peers):
                if not p.get("is_self") and not p.get("offline"):
                    self.cmb_peer.setCurrentIndex(i)
                    return

        def _sel_row_peer(self):
            r = self.tbl.currentRow()
            if r >= 0:
                it = self.tbl.item(r, 0)
                if it:
                    return {"ip": it.text(),
                            "name": self.tbl.item(r, 1).text() if self.tbl.item(r, 1) else "",
                            "os": self.tbl.item(r, 2).text() if self.tbl.item(r, 2) else ""}
            return None

        def _on_tbl_select(self):
            pr = self._sel_row_peer()
            if not pr:
                return
            for i in range(self.cmb_peer.count()):
                d = self.cmb_peer.itemData(i)
                if isinstance(d, dict) and d.get("ip") == pr["ip"]:
                    self.cmb_peer.setCurrentIndex(i)
                    break
            else:
                self.cmb_peer.setEditText(pr["ip"])
            self._update_sum()

        def _on_tbl_dblclick(self, idx):
            it = self.tbl.item(idx.row(), 0)
            if not it:
                return
            self._clip(it.text(), "IP")

        def current_peer_ip(self):
            d = self.cmb_peer.currentData()
            if isinstance(d, dict):
                return (d.get("ip") or "").strip()
            txt = (self.cmb_peer.currentText() or "").strip()
            parts = txt.split()
            ip = parts[0].strip() if parts else ""
            return ip if ip.count(".") == 3 else ""

        def _clip(self, text, what):
            try:
                QGuiApplication.clipboard().setText(text)
                self.lbl_manage.setText(f"已复制{what}：{text}")
                dbg(f"[copy] {what} = {text}")
                return True
            except Exception:                                  # noqa: BLE001
                dbg_exc("clipboard")
                self.lbl_manage.setText(f"复制失败，请手动复制：{text}")
                return False

        def on_copy_ip(self):
            pr = self._sel_row_peer() or {"ip": self.current_peer_ip()}
            if not pr.get("ip"):
                QMessageBox.warning(self, "没选中", "先在表格里点一行设备。")
                return
            self._clip(pr["ip"], "IP")

        def on_copy_getcmd(self):
            self._clip("tailscale file get .", "取文件命令")

        def on_delete_peer(self):
            pr = self._sel_row_peer() or {"ip": self.current_peer_ip(), "name": ""}
            ip = pr.get("ip", "")
            if not ip:
                QMessageBox.warning(self, "没选中", "先在表格里点一行设备。")
                return
            if ip in self._my_ips:
                r = QMessageBox.question(
                    self, "退出本机",
                    f"{ip} 是【你自己这台电脑】。\n\n确定让本机退出 tailnet 吗？\n"
                    "退出后需要重新授权才能再用。")
                if r != QMessageBox.Yes:
                    return
                self.lbl_status.setText("正在退出 tailnet…")
                self._bg("logout", ts_logout_self)            # noqa: F405
                return
            r = QMessageBox.question(
                self, "移除其他设备",
                f"要移除这台设备吗？\n\n  名称：{pr.get('name') or '(未知)'}\n  IP：{ip}\n\n"
                "命令行只能退出本机，移除别的设备要去管理后台。\n"
                "我会：① 复制该 IP  ② 打开后台 machines 页")
            if r != QMessageBox.Yes:
                return
            self._clip(ip, "IP")
            try:
                QDesktopServices.openUrl(QUrl(ts_admin_url()))  # noqa: F405
            except Exception:                                  # noqa: BLE001
                dbg_exc("open admin")
            self.lbl_manage.setText("已打开后台，搜索该 IP → … → Delete")

        def _on_logout_done(self, ok, msg):
            self.lbl_status.setText("本机已退出" if ok else f"退出失败：{msg}")
            if ok:
                QMessageBox.information(self, "已退出", f"本机已退出 tailnet。\n\n{msg}")
                invalidate_peer_cache()
                QTimer.singleShot(500, lambda: self.refresh_peers(force=True))
            else:
                QMessageBox.warning(self, "退出失败", str(msg))

        # ==================================================== 待发文件
        def add_send_files(self, paths):
            n = 0
            for p in paths:
                if p and p not in self.send_files:
                    self.send_files.append(p)
                    n += 1
            self._render_files()
            if n:
                self.lbl_status.setText(f"已加入 {n} 个目标")

        def on_pick_files(self):
            fs, _ = QFileDialog.getOpenFileNames(self, "选择要发送的文件")
            if fs:
                self.add_send_files(fs)

        def on_pick_dir(self):
            d = QFileDialog.getExistingDirectory(self, "选择要发送的文件夹")
            if d:
                self.add_send_files([d])

        def on_remove_sel(self):
            r = self.lst_files.currentRow()
            if 0 <= r < len(self.send_files):
                self.send_files.pop(r)
                self._render_files()

        def on_send_clear(self):
            self.send_files = []
            self._render_files()

        def _render_files(self):
            keep = self.lst_files.currentRow()
            self.lst_files.clear()
            for p in self.send_files:
                mark = "[目录] " if os.path.isdir(p) else ""
                sz = self._sizes.get(p)
                size_txt = _human(sz) if sz is not None else "统计中…"
                QListWidgetItem(f"{mark}{os.path.basename(p.rstrip('/\\\\'))}"
                                f"    {size_txt}", self.lst_files)
            if 0 <= keep < self.lst_files.count():
                self.lst_files.setCurrentRow(keep)
            self._update_sum()
            self._start_size_calc()

        def _start_size_calc(self):
            """体量统计放到后台 —— 拖进一个几 GB 的目录也不会卡住界面。"""
            todo = [p for p in self.send_files if p not in self._sizes]
            if not todo:
                return

            def _w():
                out = {}
                for p in todo:
                    try:
                        out[p] = _dir_size(p)
                    except BaseException:                      # noqa: BLE001
                        out[p] = 0
                try:
                    self.sizes_ready.emit(out)
                except BaseException:                          # noqa: BLE001
                    pass

            threading.Thread(target=_w, daemon=True).start()

        def _on_sizes(self, d):
            if d:
                self._sizes.update(d)
            self._render_files()

        def _update_sum(self):
            if not self.send_files:
                self.lbl_sum.setText("还没选文件")
                self.drop.lbl.setText("把文件或文件夹拖到这里")
                return
            total = sum(self._sizes.get(p, 0) for p in self.send_files)
            pending = any(p not in self._sizes for p in self.send_files)
            where = ("对方跑 tailscale file get 取走"
                     if self.cmb_method.currentIndex() == 0
                     else f"落到对方 {self.ed_dir.text() or '~'}")
            self.lbl_sum.setText(f"共 {len(self.send_files)} 个目标，合计 "
                                 f"{_human(total)}{'（统计中…）' if pending else ''}"
                                 f"  →  {where}")
            self.drop.lbl.setText(f"已选 {len(self.send_files)} 个目标，继续拖可追加")

        # ==================================================== 发送
        def on_send(self):
            ip = self.current_peer_ip()
            if not ip:
                QMessageBox.warning(self, "没选被控端",
                                    "点『刷新列表』挑一台；也可直接在框里手输 100.x.x.x")
                return
            if not self.send_files:
                QMessageBox.warning(self, "没有文件", "先把要发的文件拖进来。")
                return
            if ip in self._my_ips:
                QMessageBox.warning(self, "目标是自己",
                                    f"{ip} 是本机 IP，得发给【别的】设备。")
                return

            method = "scp" if self.cmb_method.currentIndex() == 1 else "taildrop"
            hist = [h for h in (self.cfg.get("send_history") or [])
                    if h.get("ip") != ip]
            d = self.cmb_peer.currentData()
            hist.insert(0, {"ip": ip,
                            "name": d.get("name", "") if isinstance(d, dict) else ""})
            self.cfg["send_history"] = hist[:12]
            try:
                save_config(self.cfg)                          # noqa: F405
            except Exception:                                  # noqa: BLE001
                pass

            self.txt_send.clear()
            self._log_now("send", f"目标：{ip}")
            self._log_now("send", f"方式：{'scp' if method == 'scp' else 'Taildrop'}")
            self._log_now("send", "")
            self._set_busy(True, False)
            self.lbl_status.setText("发送中…")
            self._show_log("send")

            task = _SendTask(                                  # noqa: F405
                self.send_files, ip, method,
                remote_dir=self.ed_dir.text() or "~",
                user=self.ed_user.text().strip(),
                log=lambda s: self.log_sig.emit("send", str(s)))
            self._send_task = task
            self._bg("send", task.run)

        def _on_send_done(self, ok, msg):
            self.lbl_status.setText(str(msg))
            if ok:
                extra = ("\n\n让对方在他的机器上执行：\n    tailscale file get ."
                         if self.cmb_method.currentIndex() == 0 else "")
                QMessageBox.information(self, "发送完成", str(msg) + extra)
            else:
                QMessageBox.warning(self, "发送失败", str(msg))

        def on_stop(self):
            if not (self._busy and self._send_task):
                return                      # 没在传就别乱改状态文字
            self._send_task.cancel()
            self.lbl_status.setText("已请求停止…")

        # ==================================================== 生成
        def on_generate(self):
            linux_on, win_on = self._sel_platform()
            if not (linux_on or win_on):
                QMessageBox.warning(self, "请选择", "至少选择一个目标系统。")
                return
            if not (os.path.isdir(os.path.join(REPO_ROOT, "linux")) and      # noqa: F405
                    os.path.isdir(os.path.join(REPO_ROOT, "windows"))):      # noqa: F405
                QMessageBox.critical(self, "路径错误",
                                     f"未找到 linux/ 与 windows/ 目录。\n{REPO_ROOT}")  # noqa: F405
                return

            arch = self.cmb_arch.currentText().split()[0] if linux_on else "amd64"
            auth = normalize_authkey(self.ed_auth.text())       # noqa: F405
            pub = self.ed_pub.toPlainText().strip()
            out_dir = self.ed_out.text().strip() or os.path.join(
                os.path.expanduser("~"), "Desktop")
            prefix = self.ed_prefix.text().strip() or "tailscale-remote"
            fmt = {0: "tar.gz", 1: "zip", 2: "both"}[self.cmb_fmt.currentIndex()]
            offline = self.cmb_mode.currentIndex() == 0
            want7 = []
            if linux_on and self.cmb_7z_lin_is_on():
                want7.append("linux")
            if win_on and self.cmb_7z_win_is_on():
                want7.append("windows")

            self._autosave()
            self.txt_gen.clear()
            for s in ("=" * 60, "开始生成部署包", self.lbl_summary.text(), "=" * 60):
                self._log_now("gen", s)
            self._set_busy(True, True)
            self.lbl_status.setText("生成中…")
            self._show_log("gen")

            def _fn():
                return (True, generate(                         # noqa: F405
                    REPO_ROOT, linux_on, win_on, arch, auth, pub,   # noqa: F405
                    out_dir, prefix, fmt, want_7zip=want7,
                    offline=offline,
                    log=lambda s: self.log_sig.emit("gen", str(s))))

            self._gen_ctx = (out_dir,)
            self._bg("gen", _fn)

        def _on_gen_done(self, ok, msg):
            made = msg if isinstance(msg, (list, tuple)) else []
            if not ok or not made:
                self.lbl_status.setText("生成失败")
                QMessageBox.critical(self, "生成失败",
                                     str(msg) if not isinstance(msg, (list, tuple))
                                     else "未能生成压缩包")
                return
            out_dir = getattr(self, "_gen_ctx", ("",))[0]
            total = sum(os.path.getsize(m) for m in made) // 1024
            for s in ("", "=" * 60,
                      f"完成！共 {len(made)} 个文件，合计约 {total} KB", ""):
                self._log_now("gen", s)
            for m in made:
                self._log_now("gen", "   " + m)
            self._log_now("gen", "")
            self._log_now("gen", "对方解压后：deploy 部署 / clean 清洗")
            self.lbl_status.setText(f"已生成 {len(made)} 个文件")
            QMessageBox.information(
                self, "生成完成",
                f"已生成 {len(made)} 个压缩包（合计 {total} KB）\n\n位置：{out_dir}\n\n"
                "发给目标机，对方解压后运行 deploy 即可。")

        def on_open(self):
            d = self.ed_out.text().strip() or os.path.join(
                os.path.expanduser("~"), "Desktop")
            if os.path.isdir(d):
                try:
                    os.startfile(d)                            # type: ignore[attr-defined]
                except Exception:                              # noqa: BLE001
                    pass

        # ==================================================== 表单联动
        def _sel_platform(self):
            i = self.cmb_os.currentIndex()
            return (i in (0, 2), i in (1, 2))

        def cmb_7z_lin_is_on(self):
            return self.cb_7z_lin.isChecked()

        def cmb_7z_win_is_on(self):
            return self.cb_7z_win.isChecked()

        def _sync_platform(self):
            lo, wo = self._sel_platform()
            self.cmb_arch.setEnabled(lo)
            self.cb_7z_lin.setEnabled(lo)
            self.cb_7z_win.setEnabled(wo)
            self._sync_summary()

        def _sync_mode(self):
            self.lbl_mode.setText(
                "→ 包约 70MB 起。对方断网 / 没 curl / wget 也能部署，最稳妥。"
                if self.cmb_mode.currentIndex() == 0 else
                "→ 包约 3MB。适合对方能上网（deploy 会自动下载官方安装包）。")
            self._sync_summary()

        def _sync_summary(self):
            if not hasattr(self, "lbl_summary"):
                return
            lo, wo = self._sel_platform()
            w7 = []
            if lo and self.cb_7z_lin.isChecked():
                w7.append("linux")
            if wo and self.cb_7z_win.isChecked():
                w7.append("windows")
            self.lbl_summary.setText(build_summary(              # noqa: F405
                lo, wo, self.cmb_mode.currentIndex() == 0, w7,
                self.cmb_arch.currentText().split()[0],
                self.cmb_fmt.currentIndex(),
                bool(normalize_authkey(self.ed_auth.text())),    # noqa: F405
                bool(self.ed_pub.toPlainText().strip())))

        def _snapshot_config(self):
            return {
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
                "authkey": (normalize_authkey(self.ed_auth.text())  # noqa: F405
                            if self.cb_remember.isChecked() else ""),
            }

        def _autosave(self):
            try:
                save_config(self._snapshot_config())            # noqa: F405
            except Exception:                                   # noqa: BLE001
                pass

        def _restore_config(self):
            cfg = self.cfg
            self.cmb_os.setCurrentIndex(int(cfg.get("os_index", 2)))
            self.cmb_mode.setCurrentIndex(int(cfg.get("mode_index", 0)))
            self.cmb_arch.setCurrentIndex(int(cfg.get("arch_index", 0)))
            self.cmb_fmt.setCurrentIndex(int(cfg.get("fmt_index", 2)))
            self.ed_prefix.setText(cfg.get("prefix", "tailscale-remote"))
            if os.path.isdir(cfg.get("out_dir", "")):
                self.ed_out.setText(cfg["out_dir"])
            self.cb_7z_lin.setChecked(bool(cfg.get("want_7z_linux")))
            self.cb_7z_win.setChecked(bool(cfg.get("want_7z_windows")))
            self.cb_remember.setChecked(bool(cfg.get("remember_auth")))
            if cfg.get("pubkey"):
                self.ed_pub.setPlainText(cfg["pubkey"])
            if cfg.get("remember_auth") and cfg.get("authkey"):
                self.ed_auth.setText(cfg["authkey"])
            for h in (cfg.get("send_history") or []):
                if h.get("ip"):
                    self.cmb_peer.addItem(f"{h['ip']}   （历史）", h)

            # 值设完再连信号，避免恢复过程产生抖动
            for w in (self.cmb_os, self.cmb_mode, self.cmb_arch, self.cmb_fmt):
                w.currentIndexChanged.connect(self._on_changed)
            for w in (self.cb_7z_lin, self.cb_7z_win, self.cb_remember):
                w.toggled.connect(self._on_changed)
            for w in (self.ed_auth, self.ed_out, self.ed_prefix):
                w.textChanged.connect(self._on_changed)
            self.ed_pub.textChanged.connect(self._on_changed)
            # ★ 这些槽函数不带参数, 而 Qt 信号会传参 -> 必须用 lambda 吞掉,
            #   否则每次改动都抛 TypeError（轻则弹错误框, 重则看起来"卡死"）。
            self.cmb_method.currentIndexChanged.connect(lambda *_: self._on_method())
            self.ed_dir.textChanged.connect(lambda *_: self._update_sum())

            self._on_changed()
            self._on_method()
            self._log_now("gen", f"配置文件：{CONFIG_FILE}")    # noqa: F405

        def _on_changed(self, *a):
            self._sync_platform()
            self._sync_mode()
            self._check_auth()
            self._check_pub()
            self._sync_summary()
            self._autosave()

        def _on_method(self):
            is_scp = self.cmb_method.currentIndex() == 1
            self.row_scp.setVisible(is_scp)
            self.lbl_tip.setText(
                "对方在本机执行这一句就能取走：  tailscale file get ."
                if not is_scp else
                "对方需已跑过 deploy（SSH 就绪）。Linux 走 Tailscale SSH 免密，"
                "Windows 走公钥免密。")
            self._update_sum()

        def _check_auth(self):
            t = normalize_authkey(self.ed_auth.text())          # noqa: F405
            if not t:
                self.lbl_auth.setText("状态：未填写（对方运行时会提示手填）")
                self.lbl_auth.setStyleSheet("color:#64748B;")
            elif t.startswith("tskey-"):
                self.lbl_auth.setText("状态：[OK] 格式正确，会写进包里，对方无需手填")
                self.lbl_auth.setStyleSheet("color:#15803D;")
            else:
                self.lbl_auth.setText("状态：[!] 不像 authkey（应以 tskey- 开头）")
                self.lbl_auth.setStyleSheet("color:#B45309;")

        def _check_pub(self):
            t = self.ed_pub.toPlainText().strip()
            if not t:
                self.lbl_pub.setText("状态：未提供（Windows 目标会被提示粘贴或现场生成）")
                self.lbl_pub.setStyleSheet("color:#64748B;")
                return
            first = t.splitlines()[0].strip()
            ok = first.startswith(("ssh-ed25519", "ssh-rsa", "ecdsa-sha2-",
                                   "sk-ssh-ed25519"))
            self.lbl_pub.setText(
                "状态：[OK] 公钥格式正确，会自动写入包内并配好免密" if ok else
                "状态：x 格式不对，应为 ssh-ed25519 AAAA… 开头的一整行")
            self.lbl_pub.setStyleSheet("color:#15803D;" if ok else "color:#DC2626;")

        def _toggle_auth_visible(self):
            if self.ed_auth.echoMode() == QLineEdit.Password:
                self.ed_auth.setEchoMode(QLineEdit.Normal)
            else:
                self.ed_auth.setEchoMode(QLineEdit.Password)

        def _open_keys_url(self):
            try:
                QDesktopServices.openUrl(QUrl(TS_KEYS_URL))     # noqa: F405
            except Exception:                                   # noqa: BLE001
                dbg_exc("open keys url")
            self._log_now("gen", f"已打开：{TS_KEYS_URL}")      # noqa: F405
            self._log_now("gen", "  提示：勾 Reusable，过期设 Disable")

        def _make_keypair(self, force=False):
            self._log_now("gen", "── 生成 SSH 密钥对 ──")
            priv, _pub, text = generate_ssh_keypair(            # noqa: F405
                force=force, log=lambda s: self._log_now("gen", str(s)))
            if not text:
                QMessageBox.warning(
                    self, "生成失败",
                    "未能生成密钥对。\n\nWindows 需先装 OpenSSH 客户端：\n"
                    "设置 → 可选功能 → 添加 OpenSSH 客户端")
                return
            self.ed_pub.setPlainText(text)
            for s in ("", "★ 私钥留在你自己这台电脑，不要外发：", f"    {priv}",
                      "★ 公钥会打进部署包，配置到对方实现免密登录。"):
                self._log_now("gen", s)
            QMessageBox.information(
                self, "密钥已就绪",
                f"已生成并填入公钥。\n\n私钥（留在自己电脑）：\n{priv}")

        def _scan_existing_key(self):
            priv, _p = find_ssh_keypair()                       # noqa: F405
            if priv:
                self._log_now("gen", f"检测到本机已有 SSH 密钥对：{priv}")
            else:
                self._log_now("gen",
                              "未检测到本机 SSH 密钥对；若目标是 Windows，"
                              "建议点『一键生成密钥对』。")

        def _load_file(self, edit):
            p, _ = QFileDialog.getOpenFileName(self, "选择文件", "", "All Files (*)")
            if p:
                try:
                    with open(p, "r", encoding="utf-8", errors="ignore") as f:
                        edit.setText(f.read().strip())
                except Exception as e:                          # noqa: BLE001
                    QMessageBox.warning(self, "读取失败", str(e))

        def _pick_out(self):
            d = QFileDialog.getExistingDirectory(self, "选择输出目录",
                                                 self.ed_out.text())
            if d:
                self.ed_out.setText(d)


# ============================================================ 启动 / 兜底
def _install_excepthook():
    def _hook(exc_type, exc_val, exc_tb):
        try:
            import traceback
            dbg("=== UNCAUGHT EXCEPTION ===")
            dbg("".join(traceback.format_exception(exc_type, exc_val, exc_tb)))
        except Exception:                                      # noqa: BLE001
            pass
    sys.excepthook = _hook
    try:
        def _thook(args):
            if args.exc_type is SystemExit:
                return
            _hook(args.exc_type, args.exc_value, args.exc_traceback)
        threading.excepthook = _thook
    except Exception:                                          # noqa: BLE001
        pass


def _clear_alive_sentinel():
    p = os.path.join(_HERE, ".toolbox-alive")
    try:
        if os.path.isfile(p):
            os.remove(p)
    except Exception:                                          # noqa: BLE001
        pass


def _fail_fast(msg, detail=""):
    try:
        import datetime
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:   # noqa: F405
            f.write(f"\n=== FATAL {datetime.datetime.now():%H:%M:%S} ===\n"
                    f"{msg}\n{detail}\n")
    except Exception:                                          # noqa: BLE001
        pass
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(
            None, f"{msg}\n\n{detail}\n\n细节：{DEBUG_LOG_PATH}",  # noqa: F405
            "Tailscale 工具箱 - 无法启动", 0x10)
    except Exception:                                          # noqa: BLE001
        pass
    sys.exit(1)


def main():
    _install_excepthook()
    dbg("=== application start (v2) ===")
    dbg(f"python {sys.version.split()[0]}  pyqt={HAS_QT}  exe={sys.executable}")
    if not HAS_QT:
        dbg("FATAL: PyQt5 不可用")
        _fail_fast("没有安装 PyQt5，图形界面无法启动。",
                   f"请执行：\n    {sys.executable} -m pip install PyQt5")
    try:
        app = QApplication(sys.argv)
        # ★ 全局字体用「像素」而不是「点」: 本机 logicalDotsPerInch=192,
        #   用 pt 会让没在 QSS 里指定字号的控件(消息框/表格项)字号翻倍,
        #   与 QSS 里的 px 字号打架 -> 文字被撑爆/裁切。px 统一最稳。
        _f = QFont(FONT)
        _f.setPixelSize(17)
        app.setFont(_f)
        app.setStyle("Fusion")
        w = MainWindow()
        w.show()
    except BaseException:                                      # noqa: BLE001
        dbg_exc("GUI 初始化")
        _fail_fast("图形界面初始化失败。", "详见日志里的堆栈。")
        return
    _clear_alive_sentinel()
    dbg("GUI up, sentinel cleared")
    try:
        rc = app.exec_()
    except BaseException:                                      # noqa: BLE001
        dbg_exc("app.exec_")
        rc = 1
    dbg(f"=== application exit rc={rc} ===")
    sys.exit(rc)


if __name__ == "__main__":
    main()
