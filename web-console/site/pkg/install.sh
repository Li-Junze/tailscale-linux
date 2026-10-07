#!/bin/sh
# install.sh -- Tailscale 远程工具箱 · Linux 被控端一键部署
# 由网页控制台生成, 房间号/中继/回信地址已烘焙。sudo 最多问一次密码。
set -e

ROOM="__ROOM__"
PTOK="__PTOK__"
u() { if [ -n "$PTOK" ]; then case "$1" in *\?*) echo "$1&t=$PTOK" ;; *) echo "$1?t=$PTOK" ;; esac; else echo "$1"; fi }
RELAY="__RELAY__"
CTRL_USER="__CTRL_USER__"
CTRL_HOST="__CTRL_HOST__"
CTRL_PATH="__CTRL_PATH__"
SRC_DIR=""
KEEP_SOURCE=0
INSTALL_DIR="$HOME/.local/share/tailscale-remote"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

while [ $# -gt 0 ]; do
  case "$1" in
    --room)        ROOM="$2"; shift 2 ;;
    --relay)       RELAY="$2"; shift 2 ;;
    --source-dir)  SRC_DIR="$2"; shift 2 ;;
    --install-dir) INSTALL_DIR="$2"; shift 2 ;;
    --keep-source) KEEP_SOURCE=1; shift ;;
    --dry-run)     DRY=1; shift ;;
    --elevated)    ELEVATED=1; shift ;;
    *) shift ;;
  esac
done

say()  { printf '%s\n' "$*"; }
step() { printf '\n==== %s ====\n' "$*"; }

# ---- 0. 提权: 不是 root 就 sudo 重启自己(只问一次密码) ----
if [ "$(id -u)" != "0" ] && [ -z "$ELEVATED" ] && [ -z "$DRY" ]; then
  say "[!] 需要 root, 正在用 sudo 重新运行 (可能要输一次密码) ..."
  exec sudo env "HOME=$HOME" sh "$0" --elevated \
       --room "$ROOM" --relay "$RELAY" \
       --source-dir "$SRC_DIR" --install-dir "$INSTALL_DIR" \
       $( [ "$KEEP_SOURCE" = 1 ] && echo --keep-source )
fi

step "Tailscale 远程工具箱 · 一键部署 (Linux)"
say "  本机: $(hostname) / $(id -un)     房间: $ROOM"
say "  安装目录: $INSTALL_DIR"

# ---- 1. 装 tailscale ----
step "1/6 安装 Tailscale"
if command -v tailscale >/dev/null 2>&1; then
  say "  [OK] 已安装, 跳过"
else
  if [ -n "$DRY" ]; then say "  [DryRun] 将安装 tailscale"; else
    if command -v apt-get >/dev/null 2>&1; then
      curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg \
        | tee /usr/share/keyrings/tailscale-archive-keyring.gpg >/dev/null
      curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list \
        | tee /etc/apt/sources.list.d/tailscale.list >/dev/null
      apt-get update -qq && apt-get install -y -qq tailscale
    elif command -v dnf >/dev/null 2>&1; then
      dnf install -y 'dnf-command(config-manager)'
      dnf config-manager --add-repo https://pkgs.tailscale.com/stable/fedora/tailscale.repo
      dnf install -y tailscale
    elif command -v yum >/dev/null 2>&1; then
      yum install -y yum-utils
      yum-config-manager --add-repo https://pkgs.tailscale.com/stable/centos/8/tailscale.repo
      yum install -y tailscale
    else
      say "  [!] 认不出包管理器, 请用 https://tailscale.com/download 手动装"
    fi
  fi
fi

# ---- 2. 入网 ----
step "2/6 加入 Tailscale 网络"
if command -v tailscale >/dev/null 2>&1 && [ -z "$DRY" ]; then
  KEY=""
  [ -f "$SCRIPT_DIR/keys/authkey.local.txt" ] && KEY=$(tr -d '\r\n' < "$SCRIPT_DIR/keys/authkey.local.txt")
  systemctl enable --now tailscaled >/dev/null 2>&1 || true
  if [ -n "$KEY" ]; then tailscale up --authkey="$KEY" --accept-routes || true
  else tailscale up --accept-routes || true; fi
  sleep 4
  IP=$(tailscale ip -4 2>/dev/null | head -1)
  say "  [OK] Tailscale IP: ${IP:-未拿到, 请检查是否已登录}"
else
  [ -n "$DRY" ] && say "  [DryRun] 将入网"
fi

# ---- 3. OpenSSH ----
step "3/6 开启 SSH 服务"
if [ -z "$DRY" ]; then
  if command -v apt-get >/dev/null 2>&1; then apt-get install -y -qq openssh-server || true
  elif command -v dnf >/dev/null 2>&1; then dnf install -y openssh-server || true
  elif command -v yum >/dev/null 2>&1; then yum install -y openssh-server || true; fi
  systemctl enable --now ssh sshd >/dev/null 2>&1 || true
  say "  [OK] sshd 已设为开机自启"
else
  say "  [DryRun] 将开启 sshd"
fi

# ---- 4. 公钥免密 ----
step "4/6 部署控制端公钥"
REAL_HOME=$(getent passwd "${SUDO_USER:-$(id -un)}" 2>/dev/null | cut -d: -f6)
[ -z "$REAL_HOME" ] && REAL_HOME="$HOME"
if [ -f "$SCRIPT_DIR/keys/control.pub" ] && [ -z "$DRY" ]; then
  mkdir -p "$REAL_HOME/.ssh"; chmod 700 "$REAL_HOME/.ssh"
  touch "$REAL_HOME/.ssh/authorized_keys"; chmod 600 "$REAL_HOME/.ssh/authorized_keys"
  PUB=$(tr -d '\r\n' < "$SCRIPT_DIR/keys/control.pub")
  grep -qF "$PUB" "$REAL_HOME/.ssh/authorized_keys" 2>/dev/null || cat "$SCRIPT_DIR/keys/control.pub" >> "$REAL_HOME/.ssh/authorized_keys"
  chown -R "${SUDO_USER:-$(id -un)}" "$REAL_HOME/.ssh" 2>/dev/null || true
  say "  [OK] 公钥已写入 $REAL_HOME/.ssh/authorized_keys"
else
  say "  [--] 无公钥或 DryRun, 跳过"
fi

# ---- 5. 搬迁 + 自启 ----
step "5/6 安装到标准目录 + 开机自启"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ] && [ -z "$DRY" ]; then
  mkdir -p "$INSTALL_DIR"
  cp -r "$SCRIPT_DIR/." "$INSTALL_DIR/"
  cat > "$INSTALL_DIR/agent.ini" <<EOF
room=$ROOM
token=$PTOK
relay=$RELAY
installdir=$INSTALL_DIR
host=$(hostname)
user=$(id -un)
control=$CTRL_USER@$CTRL_HOST
installed=$(date '+%Y-%m-%d %H:%M:%S')
EOF
  chmod +x "$INSTALL_DIR"/*.sh 2>/dev/null || true
  say "  [OK] 已安装到 $INSTALL_DIR"
fi
if [ -z "$DRY" ] && [ -f "$INSTALL_DIR/notify.sh" ]; then
  chmod +x "$INSTALL_DIR/notify.sh"
  ( crontab -l 2>/dev/null | grep -v 'tailscale-remote/notify.sh'; echo "@reboot sleep 20 && $INSTALL_DIR/notify.sh >/dev/null 2>&1 &" ) | crontab - 2>/dev/null || true
  nohup "$INSTALL_DIR/notify.sh" >/dev/null 2>&1 &
  say "  [OK] 已注册 @reboot 并立即启动消息通道"
fi

# ---- 6. 回传 ----
step "6/6 回传本机信息"
IP=$(tailscale ip -4 2>/dev/null | head -1)
[ -z "$IP" ] && IP=$(hostname -I 2>/dev/null | awk '{print $1}')
INFO=$(cat <<EOF
=========== Tailscale 远程工具箱 · 部署回执 (Linux) ===========
主机名        : $(hostname)
用户名        : $(id -un)
Tailscale IP  : $IP
SSH 连接命令  : ssh $(id -un)@$IP
房间号        : $ROOM
中继地址      : $RELAY
安装目录      : $INSTALL_DIR
  脚本        : $INSTALL_DIR/notify.sh
  配置        : $INSTALL_DIR/agent.ini
  卸载        : $INSTALL_DIR/clean.sh
自启方式      : crontab @reboot
引导来源      : ${SRC_DIR:-未知} (已清理)
部署时间      : $(date '+%Y-%m-%d %H:%M:%S')
============================================================
EOF
)
if [ -z "$DRY" ]; then
  printf '%s' "$INFO" > "$INSTALL_DIR/info-$(hostname).txt"
  # ① 中继
  BODY=$(printf '%s' "$INFO" | sed 's/\\/\\\\/g; s/"/\\"/g' | awk '{printf "%s\\n", $0}')
  curl -s -m 20 -X POST "$(u "$RELAY/api/send")" -H 'Content-Type: application/json' \
       -d "{\"room\":\"$ROOM\",\"side\":\"pc\",\"kind\":\"sys\",\"body\":\"$BODY\"}" >/dev/null 2>&1 \
    && say "  [OK] 中继回传成功" || say "  [!] 中继回传失败"
  # ② scp 到控制端
  if [ -n "$CTRL_HOST" ] && [ -n "$CTRL_USER" ] && command -v scp >/dev/null 2>&1; then
    scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o BatchMode=yes \
        -o ConnectTimeout=15 "$INSTALL_DIR/info-$(hostname).txt" \
        "$CTRL_USER@$CTRL_HOST:$CTRL_PATH" >/dev/null 2>&1 \
      && say "  [OK] scp 回传到 $CTRL_USER@$CTRL_HOST:$CTRL_PATH" \
      || say "  [!] scp 回传失败(控制端需开 sshd)"
  fi
  say "  [OK] 回执已存: $INSTALL_DIR/info-$(hostname).txt"
fi

# ---- 7. 清理 ----
if [ "$KEEP_SOURCE" != 1 ] && [ -z "$DRY" ]; then
  step "清理安装残留"
  [ -n "$SRC_DIR" ] && rm -f "$SRC_DIR/setup.zip" "$SRC_DIR/launcher.sh" 2>/dev/null || true
  if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ] && [ -d "$SCRIPT_DIR" ]; then rm -rf "$SCRIPT_DIR"; fi
  say "  [OK] 已清理"
fi

say ""
say "================================================================"
say "  部署完成。把这【最后一行】复制发给控制端:"
say ""
say "    ssh $(id -un)@${IP:-100.x.x.x}"
say ""
say "================================================================"
