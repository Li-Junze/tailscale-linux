#!/usr/bin/env bash
# ============================================================
#  extract.sh —— 解压助手 (tailscale-remote / linux)
#  用途: 把下载的 .tar.gz 离线包解压到正确位置, 并修复 .sh 执行权限。
#  解决痛点: 补丁包内路径带目录前缀, 必须在【父目录】解才能正确覆盖,
#            本脚本自动处理, 不必再记"解到哪一层"。
#  用法:
#    bash extract.sh                 # 自动找当前目录或上级目录最新的 .tar.gz
#    bash extract.sh <包路径.tar.gz>  # 指定包
# ============================================================
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# 找包: 先在脚本所在目录, 再在上一级目录 (适配组合包放在父目录的情况)
PKG="${1:-}"
if [ -z "$PKG" ]; then
  PKG="$(ls -t "$SCRIPT_DIR"/*.tar.gz 2>/dev/null | head -n1)"
fi
if [ -z "$PKG" ] || [ ! -f "$PKG" ]; then
  PKG="$(ls -t "$SCRIPT_DIR"/../*.tar.gz 2>/dev/null | head -n1)"
fi
[ -f "$PKG" ] || { echo "[X] 找不到 .tar.gz 包。用法: bash extract.sh <包.tar.gz>"; exit 1; }

echo "解压: $PKG"
tar xzf "$PKG" || { echo "[X] 解压失败"; exit 1; }

# 修复执行位 (Windows 打的包常丢失 Unix 执行位)
chmod +x "$SCRIPT_DIR"/*.sh 2>/dev/null

echo ""
echo "=== 解压完成 ==="
if [ -f "$SCRIPT_DIR/../deploy.sh" ]; then
  echo "包版本: $(grep -m1 SCRIPT_ID= "$SCRIPT_DIR/connect-offline.sh" | cut -d"'" -f2)"
  echo ""
  echo "下一步（只需这一个）: bash deploy.sh"
elif [ -f "$SCRIPT_DIR/connect-offline.sh" ]; then
  echo "脚本版本: $(grep -m1 SCRIPT_ID= "$SCRIPT_DIR/connect-offline.sh" | cut -d"'" -f2)"
  echo "下一步   : bash connect-offline.sh"
else
  echo "[!] 未找到 connect-offline.sh, 请检查包内容是否正确"
fi
