#!/usr/bin/env bash
# ============================================================
#  clean.sh —— 纯 Tailscale 离线工具包 · 痕迹清洗 (v0.1-clean)
#  2026-10-06
#
#  用途: 用过 connect-offline.sh 连过之后, 一键抹除本工具在目标
#        Linux 机上【产生的一切】, 做到本机完全不留痕。
#
#  本机可清除 (离线即可):
#    1. 停掉并杀除 tailscaled 进程
#    2. 删除 ~/.tailscale/ 整个目录 (含登录态/设备私钥/日志/复制出的二进制
#       / Tailscale SSH 服务端配置 —— 删掉即"失忆", 本机不再记得连过网)
#    3. 清掉 crontab 里的 @reboot 自启行
#    4. (可选) 清 ~/.ssh/known_hosts 里 tailscale 的 100.x 条目
#    5. (可选) 删除本工具解包的目录本身
#
#  ⚠ 服务器侧需手动/联网补刀: Tailscale 账号的「设备列表」里这台机器
#    仍会显示。彻底抹除需要在联网时 logout + 到 login.tailscale.com
#    删除该设备节点 (详见末尾指引)。
#
#  依赖: 仅 bash + 极少量 coreutils/procps (pkill/ps 缺失时自动降级)
#  零 sudo、零网络 (除非你选联网 logout)。
#
#  用法:
#    bash clean.sh            # 交互式 (每步确认)
#    bash clean.sh -y         # 非交互, 一次性清本机全部 (不含删包目录/联网)
# ============================================================
SCRIPT_ID='v0.1-clean-20261006'
echo "清洗脚本版本: $SCRIPT_ID"
export DEBIAN_FRONTEND=noninteractive

# ---------- 路径定义 (必须与 connect-offline.sh 完全一致) ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TSDIR="$HOME/.tailscale"
TS_SOCKET="$TSDIR/tailscaled.sock"
TS="$TSDIR/tailscale"

NONINTERACTIVE=0
[ "$1" = "-y" ] || [ "$1" = "--yes" ] && NONINTERACTIVE=1

# ---------- 安全护栏: 禁止删 HOME 之外的路径 ----------
case "$TSDIR" in
  "$HOME"/*) ;;
  *) echo "[X] 安全拦截: TSDIR=$TSDIR 不在 \$HOME 之下, 拒绝执行避免误删。"; exit 1 ;;
esac
if [ "$(id -u)" = "0" ]; then
  echo "  [!] 检测到以 root 运行。本工具设计为用户态, 普通用户即可清洗。"
  echo "      若确为 root 环境, 将清理 /root/.tailscale。继续? [y/N]"
  [ "$NONINTERACTIVE" = 1 ] || { read -r r; case "$r" in y|Y) ;; *) echo "已取消"; exit 0 ;; esac; }
fi

ask() {  # ask <默认y/n> <提示>
  local dflt="$1"; shift
  if [ "$NONINTERACTIVE" = 1 ]; then
    [ "$dflt" = "y" ] && return 0 || return 1
  fi
  printf "%s [%s]: " "$*" "$dflt"
  read -r r
  case "$r" in
    y|Y) return 0 ;;
    n|N) return 1 ;;
    *) [ "$dflt" = "y" ] && return 0 || return 1 ;;
  esac
}

# ---------- 1. 停止 tailscaled 进程 ----------
stop_daemon() {
  echo ""
  echo "[1/5] 停止 tailscaled 进程 ..."
  # 优雅: 通过 socket 退出登录 (有网时服务器侧设备转 offline)
  if [ -S "$TS_SOCKET" ] && [ -f "$TS" ] && [ -x "$TS" ]; then
    "$TS" --socket="$TS_SOCKET" logout >/dev/null 2>&1 && echo "      ✅ 已 logout (服务器侧设备转 offline)"
  fi
  # 杀进程: 优先 pkill, 逐级降级到 pgrep/kill、ps+awk/kill
  local killed=0
  if command -v pkill >/dev/null 2>&1; then
    pkill -f "$TSDIR/tailscaled" 2>/dev/null && killed=1
  elif command -v pgrep >/dev/null 2>&1; then
    for p in $(pgrep -f "$TSDIR/tailscaled"); do kill "$p" 2>/dev/null && killed=1; done
  elif command -v ps >/dev/null 2>&1; then
    for p in $(ps -e -o pid,args 2>/dev/null | grep -F "$TSDIR/tailscaled" | grep -v grep | awk '{print $1}'); do
      kill "$p" 2>/dev/null && killed=1
    done
  else
    echo "      [!] 无 pkill/pgrep/ps, 无法自动杀进程。请手动: pkill -f $TSDIR/tailscaled"
  fi
  sleep 2
  if command -v pkill >/dev/null 2>&1; then pkill -9 -f "$TSDIR/tailscaled" 2>/dev/null; fi
  if [ "$killed" = 1 ]; then echo "      ✅ 已发送停止信号"; else echo "      (未发现运行中的 tailscaled 进程)"; fi
}

# ---------- 2. 删除 ~/.tailscale 目录 ----------
remove_statedir() {
  echo ""
  echo "[2/5] 删除状态目录 $TSDIR ..."
  if [ ! -e "$TSDIR" ]; then echo "      (目录不存在, 跳过)"; return 0; fi
  rm -rf "$TSDIR" 2>/dev/null
  if [ ! -e "$TSDIR" ]; then
    echo "      ✅ 已删除 (登录态/设备私钥/日志/复制出的二进制 全部清除)"
  else
    echo "      [X] 删除失败 (权限/被占用?)。可手动: rm -rf $TSDIR"
    return 1
  fi
}

# ---------- 3. 清理 crontab 自启行 ----------
remove_cron() {
  echo ""
  echo "[3/5] 清理 crontab 开机自启行 ..."
  if ! command -v crontab >/dev/null 2>&1; then echo "      (无 crontab 命令, 跳过)"; return 0; fi
  if ! crontab -l 2>/dev/null | grep -qF "$TSDIR/tailscaled"; then
    echo "      (crontab 中无本工具条目, 跳过)"; return 0
  fi
  crontab -l 2>/dev/null | grep -vF "$TSDIR/tailscaled" | crontab - 2>/dev/null
  if crontab -l 2>/dev/null | grep -qF "$TSDIR/tailscaled"; then
    echo "      [X] 删除失败, 请手动: crontab -e  删掉含 $TSDIR/tailscaled 的行"
    return 1
  fi
  echo "      ✅ 已移除 @reboot 自启行"
}

# ---------- 4. 清理 known_hosts 中的 tailscale 条目 ----------
clean_known_hosts() {
  echo ""
  echo "[4/5] 清理 SSH known_hosts 中 Tailscale(100.x) 条目 ..."
  local kh="$HOME/.ssh/known_hosts"
  [ -f "$kh" ] || { echo "      (无 known_hosts, 跳过)"; return 0; }
  if ! grep -qE '^\[?100\.' "$kh" 2>/dev/null; then echo "      (无 tailscale IP 条目, 跳过)"; return 0; fi
  if ask n "      发现 tailscale(100.x) 条目, 是否清除? (默认 n)"; then
    grep -vE '^\[?100\.' "$kh" > "$kh.tmp" 2>/dev/null && mv "$kh.tmp" "$kh" && echo "      ✅ 已清除 100.x 条目" \
      || echo "      [!] 清理失败, 可手动: ssh-keygen -R <IP>"
  else
    echo "      (已跳过。主控端可在自己电脑上 ssh-keygen -R <目标IP>)"
  fi
}

# ---------- 5. 删除解包目录本身 (可选) ----------
remove_pkg() {
  echo ""
  echo "[5/5] 是否删除本工具解包目录?"
  echo "      目录: $SCRIPT_DIR"
  if ask n "      删除后无法再次使用本工具, 确认删除? (默认 n)"; then
    # 把待删目录登记, 放最后执行, 避免删掉正在运行的脚本文件
    echo "      ✅ 已标记, 将在最后删除"
    DEL_PKG=1
  else
    echo "      (保留工具包, 可反复使用)"
    DEL_PKG=0
  fi
}

# ====================== 主流程 ======================
stop_daemon
remove_statedir
remove_cron
clean_known_hosts
remove_pkg

# ---------- 服务器侧补刀指引 ----------
echo ""
echo "==================== 本机清洗结果 ===================="
if [ ! -e "$TSDIR" ] && ! ( command -v crontab >/dev/null 2>&1 && crontab -l 2>/dev/null | grep -qF "$TSDIR/tailscaled" ); then
  echo "  ✅ 本机已无本工具任何痕迹 (进程/状态目录/crontab 已清)。"
else
  echo "  ⚠ 本机仍有残留, 见上方 [X] 提示手动处理。"
fi
echo ""
echo "  ⚠ 服务器侧仍需处理: Tailscale 账号『设备列表』里这台机器"
echo "     仍会显示。彻底抹除两步 (需联网):"
echo "      1) 联网后重跑:  bash connect-offline.sh 选 [2] 并完成授权后,"
echo "         本 clean.sh 的 logout 已让设备转 offline;"
echo "      2) 打开 https://login.tailscale.com/admin/machines"
echo "         找到本机设备 → 右键/菜单 → Delete (删除节点)。"
echo "     删除节点后, 这台机器在 tailnet 中彻底消失, 无法再被连入。"
echo ""
if [ "${DEL_PKG:-0}" = 1 ]; then
  echo "  正在删除工具包目录: $SCRIPT_DIR"
  rm -rf "$SCRIPT_DIR" 2>/dev/null && echo "  ✅ 工具包目录已删除" || echo "  [!] 删除失败, 请手动 rm -rf $SCRIPT_DIR"
fi
echo "======================================================"
