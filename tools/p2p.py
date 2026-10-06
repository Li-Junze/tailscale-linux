#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
p2p.py —— Tailscale 点对点文件传输 (双向)
=================================================
在已连好的 Tailscale 通道上直接传文件, 双方都能收也能发。

**零依赖**: 只用 Python 标准库(socket / threading / hashlib / zipfile),
被控端**不需要装任何东西**, 也不需要 scp / rsync / sftp。

    被控端(serve) 依赖: python3 本体即可 —— socket/threading/zipfile 全是内置。
    控制端(send/fetch) 依赖: 同上。
    ⚠ 只有 `--gui` 弹窗模式额外需要 tkinter, 属于【纯可选增强】。
      Linux 最小化镜像常缺 tkinter, 此时 serve 会自动降级为纯命令行模式,
      功能完全不受影响(只是没有那个"打开收件目录"的按钮)。

★ 为什么需要它
    通过 SSH 传文件常被误认为"顺便就能 scp"。实际上很多场景传文件更别扭:
    - 被控端 Windows 没装 OpenSSH 客户端 / 目录有中文空格
    - 想把文件丢给对方但对方不会敲命令
    - 临时传个安装包, 不想先建 SSH 会话
    本工具把这些都绕开: 被控端起一个 serve, 控制端 send/fetch 即可。

────────────────────────────────────────────────────────────────────
【角色一：被控端 —— 启动接收端, 之后什么都不用管】
    python p2p.py serve                  # 监听 0.0.0.0:8765, 存到 ./收件箱
    python p2p.py serve --port 9000 --dir D:\\收件
    python p2p.py serve --gui            # 弹窗界面, 收完直接点"打开目录"

【角色二：控制端 —— 往被控端送文件】
    python p2p.py send 100.x.x.x 文件1 文件2 目录3
    python p2p.py send 100.x.x.x -a                 # 打包成一个 zip 再传(推荐)
    python p2p.py send 100.x.x.x -a .              # 打包整个当前目录

【角色三：控制端 —— 从被控端取文件(反向拉取)】
    python p2p.py fetch 100.x.x.x /远程/路径/文件
    python p2p.py fetch 100.x.x.x -l                # 列出对方可取的文件清单

【拖拽传文件(Windows 资源管理器直接拖)】
    双击 dragdrop-send.bat  →  把文件/文件夹拖到它上面
    双击 dragdrop-fetch.bat →  把"对方给的文件"拖到它上面(先建立 SSH)
    被控端想要 GUI 收文件:  python p2p.py serve --gui

────────────────────────────────────────────────────────────────────
【安全设计】
    · 默认只监听, 不做任何出网操作; 传输走 Tailscale 私网(100.x.x.x), 不经公网。
    · 每个连接需要口令(默认自动生成一个 6 位数字, 打印在 serve 端),
      防止 tailnet 内其他设备误连。
    · 写入时用「临时文件 + 原子改名」, 中断不会留半个坏文件。
    · 支持 --no-auth 仅在你确认网络完全可控时使用。
"""
import argparse
import getpass
import os
import socket
import struct
import sys
import threading
import time
import zipfile

VERSION = "1.0"
DEFAULT_PORT = 8765
CHUNK = 1 << 20          # 1MB
PROTO_MAGIC = b"TSP2P001"
BUF_MAX = 64 << 20       # 单包上限保护


# ============================================================ 协议
def _send_all(sock, data):
    """循环发全(TCP 短写是常态, 不能只 send 一次)。"""
    total = 0
    n = len(data)
    while total < n:
        sent = sock.send(data[total:])
        if sent == 0:
            raise ConnectionError("对方已断开")
        total += sent


def _recv_exact(sock, n):
    """收满 n 字节, 不足则抛错(避免截断文件)。"""
    buf = bytearray()
    while len(buf) < n:
        chunk = sock.recv(min(CHUNK, n - len(buf)))
        if not chunk:
            raise ConnectionError(f"连接中断(只收到 {len(buf)}/{n} 字节)")
        buf += chunk
    return bytes(buf)


def _send_text(sock, text):
    b = text.encode("utf-8")
    _send_all(sock, struct.pack("!I", len(b)))
    _send_all(sock, b)


def _recv_text(sock):
    (n,) = struct.unpack("!I", _recv_exact(sock, 4))
    return _recv_exact(sock, n).decode("utf-8")


# ============================================================ 工具
def human(n):
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f}{u}" if u != "B" else f"{n}B"
        n /= 1024
    return f"{n:.1f}PB"


def make_zip(paths, out_zip):
    """把多个文件/目录打成一个 zip(跨平台最稳的传输单位)。"""
    with zipfile.ZipFile(out_zip, "w", zipfile.ZIP_DEFLATED) as z:
        for p in paths:
            p = os.path.abspath(p)
            if not os.path.exists(p):
                print(f"  [跳过] 不存在: {p}")
                continue
            if os.path.isdir(p):
                for root, _d, files in os.walk(p):
                    for f in files:
                        fp = os.path.join(root, f)
                        rel = os.path.relpath(fp, os.path.dirname(p))
                        z.write(fp, rel.replace(os.sep, "/"))
                        print(f"  + {rel}")
            else:
                z.write(p, os.path.basename(p))
                print(f"  + {os.path.basename(p)}")
    return out_zip


def safe_name(name, fallback="file"):
    """去掉路径分隔符与危险字符, 防止对方写到任意位置。"""
    name = (name or "").replace("\\", "/").split("/")[-1]
    bad = '<>:"/\\|?*\0'
    name = "".join("_" if c in bad else c for c in name).strip(" .")
    return name or fallback


def unique_path(d, name):
    """同名不覆盖, 自动加 (1) (2) 序号。"""
    p = os.path.join(d, name)
    if not os.path.exists(p):
        return p
    stem, ext = os.path.splitext(name)
    i = 1
    while True:
        p2 = os.path.join(d, f"{stem}({i}){ext}")
        if not os.path.exists(p2):
            return p2
        i += 1


def ask_pass_local(prompt="口令: "):
    """尽量不把口令回显到屏幕。"""
    try:
        return getpass.getpass(prompt)
    except Exception:  # noqa: BLE001
        return input(prompt)


# ============================================================ 服务端(被控端)
def handle_client(conn, addr, outdir, auth, no_auth):
    print(f"\n[连接] 来自 {addr[0]}:{addr[1]}")
    try:
        if _recv_exact(conn, len(PROTO_MAGIC)) != PROTO_MAGIC:
            print("  [X] 协议不匹配"); return
        _send_all(conn, b"OK")
        if not no_auth:
            got = _recv_text(conn)
            if got != auth:
                _send_text(conn, "AUTH_FAIL")
                print("  [X] 口令错误, 已拒绝")
                return
            _send_text(conn, "AUTH_OK")
            print("  ✓ 口令正确")

        # 动作: PUT(送入) / LIST(列清单) / GET(取回)
        action = _recv_text(conn)
        if action == "LIST":
            items = []
            for root, _d, files in os.walk(outdir):
                for f in files:
                    fp = os.path.join(root, f)
                    rel = os.path.relpath(fp, outdir).replace(os.sep, "/")
                    try:
                        items.append(f"{rel}\t{os.path.getsize(fp)}")
                    except OSError:
                        pass
            _send_text(conn, "\n".join(items) if items else "")
            n = _recv_exact(conn, 4)
            if n == b"BYE":
                print("  已发送文件清单")
            return

        if action == "GET":
            rel = _recv_text(conn)
            fp = os.path.abspath(os.path.join(outdir, rel))
            # 越界保护: 不允许读到 outdir 之外
            if not os.path.abspath(fp).startswith(os.path.abspath(outdir)):
                _send_text(conn, "DENY")
                print(f"  [X] 拒绝越界读取: {rel}")
                return
            if not os.path.isfile(fp):
                _send_text(conn, "MISS")
                print(f"  [X] 不存在: {rel}")
                return
            size = os.path.getsize(fp)
            _send_text(conn, f"OK {size} {os.path.basename(fp)}")
            with open(fp, "rb") as f:
                while True:
                    b = f.read(CHUNK)
                    if not b:
                        break
                    _send_all(conn, b)
            print(f"  ✓ 已发送: {rel} ({human(size)})")
            return

        # PUT
        name = safe_name(_recv_text(conn))
        (size,) = struct.unpack("!Q", _recv_exact(conn, 8))
        dest = unique_path(outdir, name)
        print(f"  <- 接收: {name}  ({human(size)})")
        got = 0
        tmp = dest + ".part"
        with open(tmp, "wb") as f:
            while got < size:
                want = min(CHUNK, size - got)
                b = _recv_exact(conn, want)
                f.write(b)
                got += len(b)
                pct = got * 100 // size if size else 100
                sys.stdout.write(f"\r     {pct:3d}%  {human(got)}/{human(size)}")
                sys.stdout.flush()
        os.replace(tmp, dest)          # 原子改名, 不留半个坏文件
        print(f"\r     100%  完成 -> {dest}" + " " * 20)
        _send_text(conn, "OK")
    except Exception as e:  # noqa: BLE001
        print(f"  [X] 出错: {e}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def serve(port, outdir, auth, no_auth, gui=False):
    os.makedirs(outdir, exist_ok=True)
    outdir = os.path.abspath(outdir)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        srv.bind(("0.0.0.0", port))
    except OSError as e:
        print(f"[X] 端口 {port} 绑定失败: {e}")
        print("    换个端口:  python p2p.py serve --port 9000")
        sys.exit(1)
    srv.listen(8)
    print("=" * 62)
    print("  Tailscale 点对点文件传输 —— 接收端已启动")
    print("=" * 62)
    print(f"  监听    : 0.0.0.0:{port}")
    print(f"  收件目录: {outdir}")
    print(f"  口令    : {'(无)' if no_auth else auth}")
    # 本机 Tailscale IP 提示
    ips = local_tailscale_ips()
    if ips:
        print(f"  本机 Tailscale IP: {', '.join(ips)}")
        print("  → 让对方用这个 IP 传文件给你:")
        print(f"       python p2p.py send {ips[0]} <文件>")
    else:
        print("  ⚠ 没检测到 Tailscale IP, 请确认 Tailscale 已连上")
    print("")
    print("  保持这个窗口开着。按 Ctrl+C 停止接收。")
    print("=" * 62)

    stop = threading.Event()
    if gui:
        # tkinter 是可选依赖, 缺失就静默降级为命令行模式(被控端零依赖优先)
        threading.Thread(target=_serve_gui, args=(srv, outdir, stop), daemon=True).start()
    try:
        while not stop.is_set():
            srv.settimeout(1.0)
            try:
                conn, addr = srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(target=handle_client,
                                 args=(conn, addr, outdir, auth, no_auth), daemon=True)
            t.start()
    except KeyboardInterrupt:
        print("\n已停止接收。")
    finally:
        stop.set()
        try:
            srv.close()
        except Exception:  # noqa: BLE001
            pass
        print(f"收到的东西在: {outdir}")


def _serve_gui(srv, outdir, stop):
    """极简 Tk 界面: 显示状态 + 打开收件目录 + 停止。"""
    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:  # noqa: BLE001
        print("  [i] 本机没有 tkinter(正常, 精简版 Linux 常见), 已降级为命令行模式。")
        print("      收文件功能完全不受影响, 保持本窗口开着即可。")
        return
    try:
        root = tk.Tk()
    except Exception as e:  # noqa: BLE001
        print(f"  [i] 无法启动 GUI ({e}), 已降级为命令行模式。")
        print("      收文件功能完全不受影响, 保持本窗口开着即可。")
        return
    root.title("Tailscale 文件接收")
    root.geometry("440x230")
    root.configure(bg="#F4F6FB")
    tk.Label(root, text="● 正在接收文件", font=("Microsoft YaHei", 16, "bold"),
             bg="#F4F6FB", fg="#1B7F3B").pack(pady=(24, 6))
    tk.Label(root, text=f"收件目录\n{outdir}", font=("Microsoft YaHei", 9),
             bg="#F4F6FB", fg="#555", wraplength=400).pack()

    def open_dir():
        if sys.platform.startswith("win"):
            os.startfile(outdir)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            os.system(f'open "{outdir}"')
        else:
            os.system(f'xdg-open "{outdir}" >/dev/null 2>&1 &')

    def quit_app():
        stop.set()
        try:
            srv.close()
        except Exception:  # noqa: BLE001
            pass
        root.destroy()

    b1 = tk.Button(root, text="打开收件目录", font=("Microsoft YaHei", 11),
                   command=open_dir, width=18)
    b1.pack(pady=14)
    b2 = tk.Button(root, text="停止接收并关闭", font=("Microsoft YaHei", 11),
                   command=quit_app, width=18)
    b2.pack()
    root.mainloop()


def local_tailscale_ips():
    """尽力猜本机 Tailscale IP(不依赖外部命令, 跨平台)。"""
    out = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("192.0.2.1", 9))     # 保留地址, 不会真发包
        out.append(s.getsockname()[0])
        s.close()
    except Exception:  # noqa: BLE001
        pass
    if sys.platform.startswith("win"):
        try:
            import subprocess as sp
            r = sp.run(["ipconfig"], capture_output=True, text=True, timeout=10)
            for line in r.stdout.splitlines():
                if "100." in line and ":" in line:
                    for tok in line.split(":"):
                        t = tok.strip()
                        if t.startswith("100.") and t.count(".") == 3:
                            out.append(t)
        except Exception:  # noqa: BLE001
            pass
    # 去重保序
    seen, res = set(), []
    for x in out:
        if x not in seen and x.count(".") == 3:
            seen.add(x)
            res.append(x)
    return res


# ============================================================ 客户端(控制端)
def send(host, port, paths, as_zip, auth, no_auth, timeout=20):
    files = [p for p in paths if os.path.exists(p)]
    if not files:
        print("[X] 没有可传的文件(路径不存在)")
        return 1

    tmp_zip = None
    if as_zip:
        tmp_zip = os.path.join(os.environ.get("TEMP", "."),
                               f"p2p_{int(time.time())}.zip")
        print(f"打包 {len(files)} 项 → 临时 zip …")
        make_zip(files, tmp_zip)
        send_files = [tmp_zip]
    else:
        send_files = files

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        print(f"连接 {host}:{port} …")
        sock.connect((host, port))
    except Exception as e:  # noqa: BLE001
        print(f"[X] 连接失败: {e}")
        print("    检查: 1) 对方 serve 是否开着  2) IP 对不对  3) Tailscale 是否连通")
        if tmp_zip and os.path.isfile(tmp_zip):
            os.remove(tmp_zip)
        return 1

    try:
        _send_all(sock, PROTO_MAGIC)
        if _recv_exact(sock, 2) != b"OK":
            print("[X] 协议不匹配, 对方可能不是本程序")
            return 1
        if not no_auth:
            if auth:                     # 命令行给了 --auth -> 免交互
                got = auth
            else:
                print("输入口令 …")
                got = ask_pass_local()
            _send_text(sock, got)
            r = _recv_text(sock)
            if r != "AUTH_OK":
                print(f"[X] 口令错误({r})")
                return 1
            print("✓ 认证通过")

        for fp in send_files:
            name = os.path.basename(fp)
            size = os.path.getsize(fp)
            _send_text(sock, "PUT")
            _send_text(sock, name)
            _send_all(sock, struct.pack("!Q", size))
            print(f"\n→ {name}  ({human(size)})")
            sent = 0
            with open(fp, "rb") as f:
                while True:
                    b = f.read(CHUNK)
                    if not b:
                        break
                    _send_all(sock, b)
                    sent += len(b)
                    print(f"\r   {sent*100//size:3d}%  {human(sent)}/{human(size)}",
                          end="")
            print(f"\r   100%  完成" + " " * 24)
            ack = _recv_text(sock)
            if ack == "OK":
                print(f"   ✓ 对方已保存")
            else:
                print(f"   [!] 对方返回: {ack}")
    except Exception as e:  # noqa: BLE001
        print(f"[X] 传输中断: {e}")
        return 1
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass
        if tmp_zip and os.path.isfile(tmp_zip):
            try:
                os.remove(tmp_zip)
            except OSError:
                pass
    print("\n全部发送完毕。")
    return 0


def fetch(host, port, rel, auth, no_auth, list_only=False, timeout=30):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        print(f"连接 {host}:{port} …")
        sock.connect((host, port))
    except Exception as e:  # noqa: BLE001
        print(f"[X] 连接失败: {e}")
        return 1
    try:
        _send_all(sock, PROTO_MAGIC)
        if _recv_exact(sock, 2) != b"OK":
            print("[X] 协议不匹配")
            return 1
        if not no_auth:
            if auth:
                _send_text(sock, auth)
            else:
                print("输入口令 …")
                _send_text(sock, ask_pass_local())
            if _recv_text(sock) != "AUTH_OK":
                print("[X] 口令错误")
                return 1
        if list_only:
            _send_text(sock, "LIST")
            txt = _recv_text(sock)
            _send_all(sock, b"BYE")
            if not txt.strip():
                print("对方收件目录是空的")
            else:
                print("对方可取的文件:")
                for line in txt.splitlines():
                    parts = line.rsplit("\t", 1)
                    nm = parts[0]
                    sz = parts[1] if len(parts) > 1 else "?"
                    try:
                        sz = human(int(sz))
                    except ValueError:
                        pass
                    print(f"  {nm}   ({sz})")
            return 0

        _send_text(sock, "GET")
        _send_text(sock, rel)
        head = _recv_text(sock)
        if head == "DENY":
            print("[X] 对方拒绝(路径越界)")
            return 1
        if head == "MISS":
            print(f"[X] 对方没有这个文件: {rel}")
            return 1
        # "OK <size> <name>"
        _, size_s, name = head.split(" ", 2)
        size = int(size_s)
        name = safe_name(name, os.path.basename(rel))
        dest = unique_path(os.getcwd(), name)
        got = 0
        with open(dest, "wb") as f:
            while got < size:
                want = min(CHUNK, size - got)
                b = _recv_exact(sock, want)
                f.write(b)
                got += len(b)
                print(f"\r   {got*100//size:3d}%  {human(got)}/{human(size)}",
                      end="")
        print(f"\r   100%  完成" + " " * 24)
        print(f"✓ 已保存到: {dest}")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[X] 传输中断: {e}")
        return 1
    finally:
        try:
            sock.close()
        except Exception:  # noqa: BLE001
            pass


# ============================================================ CLI
def main():
    ap = argparse.ArgumentParser(
        prog="p2p.py",
        description="Tailscale 点对点文件传输(双向, 零依赖)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 被控端: 开接收窗口
  python p2p.py serve --gui

  # 控制端: 送文件给被控端
  python p2p.py send 100.101.102.103 报告.pdf
  python p2p.py send 100.101.102.103 -a 整个目录

  # 控制端: 看被控端有哪些文件可取
  python p2p.py fetch 100.101.102.103 -l
""")
    ap.add_argument("--version", action="version", version=f"p2p.py {VERSION}")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="被控端: 启动接收")
    s.add_argument("--port", type=int, default=DEFAULT_PORT)
    s.add_argument("--dir", default=os.path.join(os.getcwd(), "收件箱"),
                   help="收件目录(默认 ./收件箱)")
    s.add_argument("--gui", action="store_true", help="弹窗界面(需 tkinter)")
    s.add_argument("--no-auth", action="store_true", help="不校验口令(谨慎)")
    s.add_argument("--auth", default=None, help="指定口令(默认随机 6 位)")

    c = sub.add_parser("send", help="控制端: 送文件给对方")
    c.add_argument("host")
    c.add_argument("paths", nargs="+", help="文件或目录")
    c.add_argument("-a", "--as-zip", action="store_true", help="先打包成 zip 再传")
    c.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    c.add_argument("--no-auth", action="store_true")
    c.add_argument("--auth", default=None, help="指定口令(免交互)")

    f = sub.add_parser("fetch", help="控制端: 从对方取文件")
    f.add_argument("host")
    f.add_argument("rel", nargs="?", default="", help="对方目录里的相对路径")
    f.add_argument("-l", "--list", dest="list_only", action="store_true",
                   help="只列清单")
    f.add_argument("-p", "--port", type=int, default=DEFAULT_PORT)
    f.add_argument("--no-auth", action="store_true")
    f.add_argument("--auth", default=None)

    a = ap.parse_args()
    import random
    rnd = random.SystemRandom()

    if a.cmd == "serve":
        auth = a.auth or f"{rnd.randint(0, 999999):06d}"
        serve(a.port, a.dir, auth, a.no_auth, a.gui)
        return 0

    auth = a.auth
    if a.cmd == "send":
        return send(a.host, a.port, a.paths, a.as_zip, auth, a.no_auth)
    if a.cmd == "fetch":
        return fetch(a.host, a.port, a.rel, auth, a.no_auth, a.list_only)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断。")
        sys.exit(130)
