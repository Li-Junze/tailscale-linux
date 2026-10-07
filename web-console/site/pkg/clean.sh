#!/bin/sh
# clean.sh -- Linux 本地卸载(撤掉远程连接工具, 和网页「清除」等价)
set -e

DIR=$(cd "$(dirname "$0")" && pwd)
INSTALL_DIR="$DIR"
[ -f "$DIR/agent.ini" ] && INSTALL_DIR=$(sed -n 's/^installdir=//p' "$DIR/agent.ini" | head -1)
[ -z "$INSTALL_DIR" ] && INSTALL_DIR="$DIR"

if [ "$(id -u)" != "0" ]; then
  echo "[!] 需要 root, 用 sudo 重新运行 ..."
  exec sudo sh "$0"
fi

echo ""
echo "==== 开始卸载 Tailscale 远程工具箱 ===="

echo "[1/5] 停止消息通道 ..."
pkill -f 'tailscale-remote/notify.sh' 2>/dev/null || true
pkill -f 'notify.sh' 2>/dev/null || true
echo "      [OK]"

echo "[2/5] 移除开机自启 ..."
crontab -l 2>/dev/null | grep -v 'tailscale-remote/notify.sh' | crontab - 2>/dev/null || true
echo "      [OK]"

echo "[3/5] 撤销控制端公钥 ..."
REAL_HOME=$(getent passwd "${SUDO_USER:-$(id -un)}" 2>/dev/null | cut -d: -f6)
[ -z "$REAL_HOME" ] && REAL_HOME="$HOME"
AK="$REAL_HOME/.ssh/authorized_keys"
if [ -f "$AK" ] && [ -f "$INSTALL_DIR/keys/control.pub" ]; then
  PUB=$(tr -d '\r\n' < "$INSTALL_DIR/keys/control.pub")
  grep -vF "$PUB" "$AK" > "$AK.tmp" 2>/dev/null || true
  mv "$AK.tmp" "$AK"; chmod 600 "$AK"
  echo "      [OK] 已撤销"
else
  echo "      [--] 无记录"
fi

echo "[4/5] 退出 Tailscale 网络 ..."
case "$1" in
  --keep-tailscale) echo "      [--] 保留" ;;
  *) if command -v tailscale >/dev/null 2>&1; then tailscale logout 2>/dev/null || true; echo "      [OK] 已 logout"; fi ;;
esac

echo "[5/5] 删除安装目录 ..."
for d in "$INSTALL_DIR" "$HOME/TailscaleRemote"; do
  if [ -n "$d" ] && [ -d "$d" ]; then rm -rf "$d"; echo "      [OK] 已删 $d"; fi
done

echo ""
echo "==== 卸载完成 ===="
