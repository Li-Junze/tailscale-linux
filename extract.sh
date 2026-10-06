#!/usr/bin/env bash
# ============================================================
#  extract.sh —— 解压助手 (tailscale-linux)
#  用途: 把下载的 .tar.gz 离线包解压到正确位置, 并修复 .sh 执行权限。
#  解决痛点: 补丁包内路径带目录前缀, 必须在【父目录】解才能正确覆盖,
#            本脚本自动处理, 不必再记"解到哪一层"。
#  用法:
#    bash extract.sh                 # 自动找当前目录最新的 .tar.gz
#    bash extract.sh <包路径.tar.gz>  # 指定包
# ============================================================
set -u

PKG="${1:-$(ls -t *.tar.gz 2>/dev/null | head -n1)}"
[ -f "$PKG" ] || { echo "[X] 找不到 .tar.gz 包。用法: bash extract.sh <包.tar.gz>"; exit 1; }

echo "解压: $PKG"
tar xzf "$PKG" || { echo "[X] 解压失败"; exit 1; }

# 进入包内顶层目录 (用于赋权 / 显示版本)
TOP="$(tar tzf "$PKG" 2>/dev/null | head -n1 | cut -d/ -f1)"
echo "顶层目录: ${TOP:-<无>}"
[ -n "$TOP" ] && cd "$TOP" 2>/dev/null

# 修复执行位 (Windows 打的包常丢失 Unix 执行位)
chmod +x *.sh 2>/dev/null

echo ""
echo "=== 解压完成 ==="
if [ -f connect-offline.sh ]; then
  echo "脚本版本: $(grep -m1 SCRIPT_ID= connect-offline.sh | cut -d"'" -f2)"
  echo "下一步   : bash connect-offline.sh"
else
  echo "[!] 未找到 connect-offline.sh, 请检查包内容是否正确"
fi
