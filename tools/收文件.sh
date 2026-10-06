#!/usr/bin/env bash
# ============================================================
#  收文件.sh —— 被控端(Linux / macOS) 双击等价: 一键启动文件接收
#
#  做法: bash 收文件.sh
#  屏幕会显示 6 位口令 + 本机 Tailscale IP, 保持窗口开着。
#  对方把文件拖到 dragdrop-send.bat, 或用 p2p.py send 送过来。
#  收件目录: ./收件箱
#
#  依赖: python3 本体(标准库即可)。不需要 scp/rsync/sftp。
# ============================================================
set -u
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
done

if [ -z "$PY" ]; then
  echo "[X] 本机没有 python3 / python。"
  echo "    被控端想零依赖收文件, 请先装 python3, 或改用 'bash deploy.sh' 建好通道后用 scp。"
  exit 1
fi

echo "启动接收端 ..."
echo
# GUI 需要 tkinter, 缺了会自动降级为命令行模式(功能不受影响)
exec "$PY" p2p.py serve --dir "收件箱" --gui
