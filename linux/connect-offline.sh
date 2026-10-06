#!/usr/bin/env bash
# ============================================================
#  connect-offline.sh —— 纯 Tailscale 离线方案 (v0.3-offline)
#  Linux 被控端「零额外依赖」版, 2026-10-06
#
#  设计目标 (对比原版 connect.sh):
#    · 离线零下载 —— Tailscale 静态二进制预置在 assets/, 目标机
#      解包即用。运行期完全不碰 curl / wget / python3 / tar。
#    · 免密免公钥 —— 用 authkey 入网 + Tailscale SSH (tailscaled
#      内置服务端, 端口 22, tailnet 身份认证)。目标机无需密码、
#      无需 sshd、无需 ssh 客户端、无需任何传统 OpenSSH 组件。
#    · 无 sudo —— 固定用户态 ~/.tailscale, 不动系统 (离线机通常
#      也拿不到管理员密码)。
#    · 不依赖 ss / netstat / tar / curl / wget / python3。
#
#  被控端(目标 Linux 机)运行期只需:
#      bash  +  常见 coreutils(mkdir/cp/chmod/grep/sed/awk/date/sleep)
#           +  本包 assets/ 里预置的 tailscale / tailscaled 二进制
#  主控端(你自己电脑)连接只需: 任意能跑 `ssh`/`tailscale ssh` 的环境
#      (Windows/macOS/Linux 都自带或装好 Tailscale 即可)。
#
#  用法:  bash connect-offline.sh
# ============================================================
SCRIPT_ID='v0.4-offline-20261006'
echo "脚本版本: $SCRIPT_ID   (没有这一行 = 旧文件)"
export DEBIAN_FRONTEND=noninteractive

OS="$(uname -s)"
case "$OS" in
  Linux)  : ;;
  Darwin) echo "  [提示] macOS 建议直接用系统 Tailscale.app; 本脚本主要为 Linux 被控端设计。" ;;
  *) echo "[X] 不支持 $OS。你自己的 Windows/macOS 电脑用 tailscale ssh 直连即可, 不需要本脚本。"; exit 1 ;;
esac

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSET_TS="$SCRIPT_DIR/assets/tailscale"
ASSET_TSD="$SCRIPT_DIR/assets/tailscaled"
TSDIR="$HOME/.tailscale"
TS_SOCKET="$TSDIR/tailscaled.sock"
TS="$TSDIR/tailscale"
REAL_USER="${SUDO_USER:-$(whoami)}"
KEYS_URL='https://login.tailscale.com/admin/settings/keys'
USERSPACE=1   # 本脚本固定用户态, 不触碰系统 tailscaled

run_ts() { "$TS" --socket="$TS_SOCKET" "$@" 2>&1; }

# ---------- 极简依赖自检: 只要求 bash + coreutils ----------
need_cmds() {
  local missing=()
  for c in mkdir cp chmod grep sed awk date sleep; do
    command -v "$c" >/dev/null 2>&1 || missing+=("$c")
  done
  if [ ${#missing[@]} -gt 0 ]; then
    echo "[X] 缺少基础命令: ${missing[*]}  (连 coreutils 都没有的话这台机基本不可用了)"
    return 1
  fi
  return 0
}

# ---------- 安装 Tailscale: 优先用包内预置二进制(离线), 否则联网自取 ----------
install_offline() {
  # 关键: 只判断文件【是否存在】(-f), 不要求可执行位(-x) —— Windows 打的 tar
  #       无法可靠保留 Unix 执行位, assets 常被打成 644, 用 -x 判断会误报
  #       "未预置二进制"。复制到 ~/.tailscale 后再统一 chmod +x 即可运行。
  if [ -f "$ASSET_TS" ] && [ -f "$ASSET_TSD" ]; then
    echo "      使用包内预置静态二进制 (离线, 零下载) ..."
    mkdir -p "$TSDIR"
    cp -f "$ASSET_TS" "$TSDIR/tailscale"
    cp -f "$ASSET_TSD" "$TSDIR/tailscaled"
    chmod +x "$TSDIR/tailscale" "$TSDIR/tailscaled" 2>/dev/null
    if [ -f "$TSDIR/tailscale" ] && [ -f "$TSDIR/tailscaled" ]; then
      return 0
    fi
    echo "      [X] 复制预置二进制失败 (磁盘只读?)"; return 1
  fi

  # 轻量包(未内置二进制): 目标机有网时自己下载
  echo "      本包为【轻量模式】(未内置二进制), 尝试联网下载 Tailscale 静态包 ..."
  download_tailscale
}
# ---------- 联网下载 fallback (仅轻量模式需要) ----------
# 架构名与 Tailscale 官方发布名对齐 (amd64/arm64/arm/386)
detect_arch() {
  local m; m="$(uname -m 2>/dev/null)"
  case "$m" in
    x86_64|amd64)  echo amd64 ;;
    aarch64|arm64) echo arm64 ;;
    armv7l|armv7)  echo arm ;;
    i386|i686)     echo 386 ;;
    *) echo "" ;;
  esac
}

download_tailscale() {
  local TA; TA="$(detect_arch)"
  if [ -z "$TA" ]; then
    echo "[X] 无法识别本机架构 ($(uname -m 2>/dev/null))。"
    echo "    请在有网机器上用 stage-tailscale.sh 手动预置后拷入 assets/。"
    return 1
  fi
  local DL
  command -v curl >/dev/null 2>&1 && DL=curl
  if [ -z "${DL:-}" ] && command -v wget >/dev/null 2>&1; then DL=wget; fi
  if [ -z "${DL:-}" ]; then
    echo "[X] 本包为【轻量模式】(未内置二进制), 且本机没有 curl / wget 无法下载。"
    echo "    两条路任选其一:"
    echo "      A) 换一台有网的机器执行:  bash stage-tailscale.sh ${TA}"
    echo "         再把生成的 assets/ 拷进 程序/linux/assets/ 重跑本脚本"
    echo "      B) 重新打包时勾选【离线模式】, 用内置二进制的完整包"
    return 1
  fi
  local TMP="$HOME/.tailscale_dl"
  local URL="https://pkgs.tailscale.com/stable/tailscale_latest_${TA}.tgz"
  rm -rf "$TMP"; mkdir -p "$TMP/x"
  echo "      本机架构: $TA"
  echo "      下载: $URL"
  if [ "$DL" = "curl" ]; then
    curl -fsSL --connect-timeout 20 -o "$TMP/ts.tgz" "$URL" \
      || { echo "      [X] 下载失败 (无外网/被墙?)"; rm -rf "$TMP"; return 1; }
  else
    wget -q -T 20 -O "$TMP/ts.tgz" "$URL" \
      || { echo "      [X] 下载失败 (无外网/被墙?)"; rm -rf "$TMP"; return 1; }
  fi
  [ -s "$TMP/ts.tgz" ] || { echo "      [X] 下载内容为空"; rm -rf "$TMP"; return 1; }
  command -v tar >/dev/null 2>&1 || { echo "      [X] 缺 tar, 无法解包"; rm -rf "$TMP"; return 1; }
  echo "      解包 ..."
  tar xzf "$TMP/ts.tgz" -C "$TMP/x" --strip-components=1 \
    || { echo "      [X] 解包失败"; rm -rf "$TMP"; return 1; }
  mkdir -p "$TSDIR"
  cp -f "$TMP/x/tailscale"  "$TSDIR/tailscale"  2>/dev/null
  cp -f "$TMP/x/tailscaled" "$TSDIR/tailscaled" 2>/dev/null
  chmod +x "$TSDIR/tailscale" "$TSDIR/tailscaled" 2>/dev/null
  rm -rf "$TMP"
  if [ -f "$TSDIR/tailscale" ] && [ -f "$TSDIR/tailscaled" ]; then
    echo "      ✅ 联网获取成功 (已放入 $TSDIR)"
    return 0
  fi
  echo "      [X] 下载包结构异常, 未找到 tailscale / tailscaled"
  return 1
}

# ---------- 就绪判断 ----------
# ★ 关键: 不能用 `tailscale status`(不带 --json) 判断 daemon 就绪 ——
#   它在 daemon 已运行但【尚未登录/未 up】时退出码为 1 (输出 "Tailscale is
#   stopped."), 会把"起来了但还没授权"误判成"起不来"而错误退出。
#   --json 只要 daemon 可达就返回 0 并带 BackendState, 才是正确判据。
daemon_ready() {
  [ -S "$TS_SOCKET" ] || return 1
  run_ts status --json 2>/dev/null | grep -q '"BackendState"'
}

# ---------- 启动用户态 tailscaled ----------
start_daemon() {
  if daemon_ready; then
    echo "      tailscaled 已在运行"; return 0
  fi
  pkill -f "$TSDIR/tailscaled" 2>/dev/null; sleep 1
  mkdir -p "$TSDIR"
  nohup "$TSDIR/tailscaled" --tun=userspace-networking --statedir="$TSDIR" --socket="$TS_SOCKET" \
    > "$TSDIR/tailscaled.log" 2>&1 &
  local t=0
  while [ $t -lt 15 ]; do
    daemon_ready && return 0
    sleep 1; t=$((t+1))
  done
  echo "      [!] tailscaled 未就绪 (socket: $TS_SOCKET)"
  echo "      [!] socket 存在? $([ -S "$TS_SOCKET" ] && echo 是 || echo 否) | 日志尾部:"
  tail -n 15 "$TSDIR/tailscaled.log" 2>/dev/null | sed 's/^/      | /'
  return 1
}

# ---------- 开机自启: 写入 crontab @reboot (用户态, 不动系统服务) ----------
#   只启动 tailscaled 即可 —— 登录态持久化在 statedir, 重启后自动重连 tailnet,
#   无需重新粘贴 authkey (前提: Key expiry 设为 Disable)。
setup_autostart() {
  local line="@reboot $TSDIR/tailscaled --tun=userspace-networking --statedir=$TSDIR --socket=$TS_SOCKET"
  if ! command -v crontab >/dev/null 2>&1; then
    echo "      [!] 本机无 crontab 命令, 需手动配置开机自启:"; echo "        $line"; return 1
  fi
  if crontab -l 2>/dev/null | grep -qF "$TSDIR/tailscaled"; then
    echo "      开机自启已存在 (crontab 已有该条目), 跳过"; return 0
  fi
  ( crontab -l 2>/dev/null; echo "$line" ) | crontab -
  if crontab -l 2>/dev/null | grep -qF "$TSDIR/tailscaled"; then
    echo "      ✅ 已写入 crontab @reboot 开机自启"
  else
    echo "      [!] 写入 crontab 失败, 请手动加: $line"
  fi
}

# ---------- authkey 授权 (带 SSH 公钥识别提示) ----------
update_key() {
  # 若已通过环境变量/配置文件提供 key, 直接尝试 (优先级: 环境变量 > keys/ > 我的连接信息.txt)
  local CFG="$SCRIPT_DIR/我的连接信息.txt"
  local KEYF="$SCRIPT_DIR/keys/authkey.local.txt"
  if [ -z "$TS_AUTHKEY" ] && [ -f "$KEYF" ]; then
    TS_AUTHKEY="$(grep -oE 'TS_AUTHKEY=[^ ]+' "$KEYF" 2>/dev/null | head -n1 | cut -d= -f2-)"
  fi
  if [ -z "$TS_AUTHKEY" ] && [ -f "$CFG" ]; then
    TS_AUTHKEY="$(grep -oE 'TS_AUTHKEY=[^ ]+' "$CFG" 2>/dev/null | head -n1 | cut -d= -f2-)"
  fi
  if [ -n "$TS_AUTHKEY" ]; then
    echo "      检测到预置 TS_AUTHKEY, 直接授权 ..."
    if [ "$1" = "1" ]; then
      OUT="$(run_ts up --authkey="$TS_AUTHKEY" --ssh --force-reauth)"
    else
      OUT="$(run_ts up --authkey="$TS_AUTHKEY" --ssh)"
    fi
    if [ $? -eq 0 ]; then echo "      ✅ 授权成功 (Tailscale SSH 已随启)"; return 0; fi
    echo "      [!] 预置 key 失败: $OUT  (过期/已用? 下面改手动粘贴)"
  fi

  echo ""
  echo "      第1步: 在浏览器打开: $KEYS_URL"
  echo "      第2步: Generate auth key (★建议勾选 Reusable + 允许 SSH), 复制"
  echo "      第3步: 粘贴到下面 (以 tskey-auth- 开头, 不是 SSH 公钥)"
  while true; do
    printf "      粘贴 auth key 后回车 (直接回车=取消): "
    read -r KEY
    [ -z "$KEY" ] && { echo "      已取消"; return 1; }
    case "$KEY" in
      ssh-*|ecdsa-sha2-*|sk-*)
        echo "      [!] 这是 SSH 公钥, 不是 Tailscale auth key! key 以 tskey-auth- 开头。"
        printf "      仍要继续? [y/N]: "; read -r c
        case "$c" in y|Y) ;; *) continue ;; esac ;;
    esac
    echo "      正在授权 ..."
    if [ "$1" = "1" ]; then
      OUT="$(run_ts up --authkey="$KEY" --ssh --force-reauth)"
    else
      OUT="$(run_ts up --authkey="$KEY" --ssh)"
    fi
    if [ $? -eq 0 ]; then echo "      ✅ 授权成功 (Tailscale SSH 已随启)"; return 0; fi
    echo "      授权失败: $OUT  (key 可能过期/已用/复制不全)"
  done
}

# ---------- 角色选择 ----------
need_cmds || { read -r -p "按回车退出" _; exit 1; }
echo ""
echo "============ 纯 Tailscale 离线版 v0.3 (被控端零额外依赖) ============"
echo "  [1] 主控端 : 我自己的电脑, 主动连远方 (需本机有 ssh 客户端)"
echo "  [2] 被控端 : 远方的电脑(目标机), 让我的电脑连进来 (零额外依赖)"
read -r -p "  请选择 (1/2, 回车=2 被控端): " role
[ "$role" != "1" ] && role="2"

# ---------- [1/4] 准备 Tailscale (离线) ----------
echo ""
echo "[1/4] 准备 Tailscale ..."
install_offline || { read -r -p "按回车退出" _; exit 1; }
start_daemon || { read -r -p "按回车退出" _; exit 1; }

# ---------- [2/4] 检查后台 + 授权 ----------
echo "[2/4] 检查 Tailscale 后台响应 ..."
tries=0
while :; do
  J="$(run_ts status --json 2>/dev/null)"
  printf '%s' "$J" | grep -q '"BackendState"' && break
  tries=$((tries+1)); printf "."
  if [ $tries -ge 30 ]; then
    echo ""; echo "      [X] tailscaled 未就绪, 日志尾部:"
    tail -n 15 "$TSDIR/tailscaled.log" 2>/dev/null | sed 's/^/      | /'
    read -r -p "按回车退出" _; exit 1
  fi
  sleep 2
done
echo ""
STATE="$(printf '%s' "$J" | grep -o '"BackendState": *"[A-Za-z]*"' | head -n1 | sed 's/.*"\([A-Za-z]*\)"$/\1/')"
[ -z "$STATE" ] && STATE="NeedsLogin"

if [ "$STATE" = "Running" ]; then
  echo "[2/4] Tailscale 已登录"
else
  update_key 0 || { read -r -p "按回车退出" _; exit 1; }
  printf '%s' "$(run_ts status --json 2>/dev/null)" | grep -q '"BackendState": *"Running"' \
    || { echo "[X] 授权后未进入运行状态, 请重跑本脚本"; read -r -p "按回车退出" _; exit 1; }
  echo "[2/4] Tailscale 就绪 (Tailscale SSH 已随启)"
fi

# ---------- [3/4] 被控端: 输出连接信息 ----------
if [ "$role" = "2" ]; then
  ST="$(run_ts status)"
  SELF_IP="$(printf '%s' "$ST" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n1)"
  read -r -p "   是否配置开机自启 (crontab @reboot, 默认 y)? [Y/n]: " a
  case "$a" in n|N) echo "      (已跳过, 可稍后手动运行本脚本或自行加 crontab)";; *) setup_autostart ;; esac
  echo ""
  echo "==================== 本机(被控端)已就绪 ===================="
  echo ""
  echo "   回到你自己的电脑, 终端直接执行:"
  echo ""
  echo "        ssh ${REAL_USER}@${SELF_IP}"
  echo "        或:  tailscale ssh ${REAL_USER}@${SELF_IP}"
  echo ""
  echo "   ★ 通道 = Tailscale SSH (端口 22, tailnet 身份认证,"
  echo "            免密码 / 免公钥 / 无需 sshd / 无需目标机密码)"
  echo ""
  echo "   本机 Tailscale IP : ${SELF_IP}"
  echo "   登录用户名        : ${REAL_USER}"
  echo "   生成/更新 key 页   : ${KEYS_URL}"
  echo ""
  echo "   重启后自启: 已尝试写入 crontab @reboot (见上方状态)"
  echo "   用完清洗  : bash clean.sh   (一键抹除本机所有连接痕迹)"
  echo "   ★ 重要: 到 login.tailscale.com 把本机『Key expiry』设为 Disable,"
  echo "     否则密钥过期后需重跑本脚本重新授权一次。"
  echo ""
  echo "================================================================"
  read -r -p "按回车退出" _; exit 0
fi

# ---------- [4/4] 主控端: 等目标机入网并连接 ----------
echo "[3/4] 等待目标机加入组网 ..."
echo "      请在目标机上运行本脚本并选择 [2] 被控端完成授权"
printf "      等待中(每3秒刷新) "
SELF_IP=""; PEER_IPS=(); PEER_NAMES=()
for w in $(seq 1 40); do
  ST="$(run_ts status 2>/dev/null)"
  SELF_IP=""; PEER_IPS=(); PEER_NAMES=()
  while IFS= read -r line; do
    case "$line" in *offline*) continue ;; esac
    IP="$(printf '%s' "$line" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n1)"
    [ -z "$IP" ] && continue
    NAME="$(printf '%s' "$line" | awk 'NR==1{print $2}')"
    if [ -z "$SELF_IP" ]; then SELF_IP="$IP"; continue; fi
    [ "$IP" = "$SELF_IP" ] && continue
    PEER_IPS+=("$IP"); PEER_NAMES+=("$NAME")
  done <<< "$ST"
  [ ${#PEER_IPS[@]} -gt 0 ] && break
  printf "."; sleep 3
done
echo ""
[ ${#PEER_IPS[@]} -eq 0 ] && {
  echo "[X] 超时, 组网里还没有目标机。"
  echo "    请确认目标机已运行本脚本选[2]并完成 key 授权, 完成后重跑本脚本。"
  read -r -p "按回车退出" _; exit 0
}
echo "      发现以下设备:"
for i in "${!PEER_IPS[@]}"; do
  echo "        [$((i + 1))] ${PEER_NAMES[$i]}  ${PEER_IPS[$i]}"
done
read -r -p "      回车=连[1], 序号选择, 或直接输入IP, s=退出: " sel
case "$sel" in
  s|S) echo "已退出。"; read -r -p "按回车退出" _; exit 0 ;;
  '' ) TARGET_IP="${PEER_IPS[0]}" ;;
  *.*.*.* ) TARGET_IP="$sel" ;;
  *[!0-9]* ) echo "无效输入"; read -r -p "按回车退出" _; exit 1 ;;
  * ) n="$sel"; [ "$n" -ge 1 ] && [ "$n" -le ${#PEER_IPS[@]} ] && TARGET_IP="${PEER_IPS[$((n - 1))]}";;
esac
read -r -p "      目标机登录用户名 (直接回车=${REAL_USER}): " suser
[ -z "$suser" ] && suser="$REAL_USER"
echo ""
echo "连接 ${suser}@${TARGET_IP}  (Tailscale SSH: 免密码, 走 tailnet 认证)"
echo ""
if command -v tailscale >/dev/null 2>&1; then
  tailscale ssh "${suser}@${TARGET_IP}"
else
  ssh -o StrictHostKeyChecking=accept-new "${suser}@${TARGET_IP}"
fi
read -r -p "连接已结束, 按回车退出" _
