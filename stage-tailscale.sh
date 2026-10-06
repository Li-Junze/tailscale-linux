#!/usr/bin/env bash
# ============================================================
#  stage-tailscale.sh —— 把 Tailscale 官方静态二进制预置进 assets/
#  用法:  bash stage-tailscale.sh [arch]
#    arch 可选: amd64(默认) / arm64 / arm / 386
#  运行环境: 一台「有网 + 有 curl/wget/python3 + 有 tar」的机器。
#  作用: 下载对应架构的 tailscale 静态包, 抽出 tailscale + tailscaled
#         两个静态二进制, 放进本目录 assets/, 使 connect-offline.sh
#         在目标机上「零下载」即可使用。
# ============================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ARCH="${1:-amd64}"
case "$ARCH" in
  amd64|x86_64) TA=amd64 ;; arm64|aarch64) TA=arm64 ;; arm|armv7l|armv6l) TA=arm ;;
  386|i386|i686) TA=386 ;; *) echo "[X] 未知架构: $ARCH"; exit 1 ;;
esac
URL="https://pkgs.tailscale.com/stable/tailscale_latest_${TA}.tgz"
DL=""; command -v curl >/dev/null 2>&1 && DL="curl"
command -v wget >/dev/null 2>&1 && DL="${DL:-wget}"
command -v python3 >/dev/null 2>&1 && DL="${DL:-python3}"
[ -z "$DL" ] && { echo "[X] 需要 curl/wget/python3 任一"; exit 1; }
[ -x "$(command -v tar)" ] || { echo "[X] 需要 tar 解压"; exit 1; }

# 注意: Git Bash 的 mktemp 会返回 C:\Users\... 混合路径, 导致 tar 把路径误判为
#       远程归档地址而报 "Cannot connect to C: resolve failed"。固定用脚本目录下的
#       POSIX 路径, 避免该坑。
TMP="$SCRIPT_DIR/.stage_tmp"
mkdir -p "$TMP"
echo "下载 $URL → $TMP/ts.tgz"
case "$DL" in
  curl)    curl -fsSL "$URL" -o "$TMP/ts.tgz" ;;
  wget)    wget -qO "$TMP/ts.tgz" "$URL" ;;
  python3) python3 -c 'import sys,urllib.request;urllib.request.urlretrieve(sys.argv[1],sys.argv[2])' "$URL" "$TMP/ts.tgz" ;;
esac || { echo "[X] 下载失败"; rm -rf "$TMP"; exit 1; }

mkdir -p "$TMP/x"
tar -xzf "$TMP/ts.tgz" -C "$TMP/x" --strip-components=1
SRC="$(find "$TMP/x" -maxdepth 2 -type f \( -name tailscale -o -name tailscaled \))"
[ -z "$SRC" ] && { echo "[X] 包内未找到二进制"; rm -rf "$TMP"; exit 1; }
mkdir -p "$SCRIPT_DIR/assets"
for f in $SRC; do cp -f "$f" "$SCRIPT_DIR/assets/"; done
chmod +x "$SCRIPT_DIR/assets/tailscale" "$SCRIPT_DIR/assets/tailscaled" 2>/dev/null
ls -la "$SCRIPT_DIR/assets/"
echo "✅ 已预置到 assets/。把整个目录重新打包成 tar.gz 传到目标机即可离线运行。"
rm -rf "$TMP"
