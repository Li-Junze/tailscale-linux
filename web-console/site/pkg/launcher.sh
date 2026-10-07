#!/bin/sh
# ============================================================
#  Tailscale Remote Toolbox - one click installer (Linux)
#  Keep setup.zip next to this file, then:  sh launcher.sh
# ============================================================
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
ZIP="$HERE/setup.zip"
if [ ! -f "$ZIP" ]; then ZIP=$(ls "$HERE"/*.zip 2>/dev/null | head -1); fi

if [ -z "$ZIP" ] || [ ! -f "$ZIP" ]; then
  echo ""
  echo "  [X] setup.zip not found - keep it next to this script."
  echo ""
  exit 1
fi

echo ""
echo "  =========================================="
echo "   Tailscale Remote - one click setup"
echo "  =========================================="
echo ""

DEST="$HOME/.cache/tsr-setup"
rm -rf "$DEST"; mkdir -p "$DEST"

echo "  [1/3] Unpacking ..."
if command -v unzip >/dev/null 2>&1; then
  unzip -q -o "$ZIP" -d "$DEST"
elif command -v python3 >/dev/null 2>&1; then
  python3 -c "import zipfile,sys; zipfile.ZipFile(sys.argv[1]).extractall(sys.argv[2])" "$ZIP" "$DEST"
else
  echo "  [X] need unzip or python3"; exit 1
fi

MAIN="$DEST/tailscale-remote/install.sh"
[ -f "$MAIN" ] || MAIN="$DEST/install.sh"
[ -f "$MAIN" ] || { echo "  [X] install.sh missing"; exit 1; }

echo "  [2/3] Deploying (sudo may ask your password once) ..."
chmod +x "$MAIN" 2>/dev/null || true
if [ "$(id -u)" = "0" ]; then
  sh "$MAIN" --source-dir "$HERE"
elif command -v sudo >/dev/null 2>&1; then
  sudo sh "$MAIN" --source-dir "$HERE"
else
  sh "$MAIN" --source-dir "$HERE"
fi

echo ""
echo "  [3/3] Done."
echo ""
