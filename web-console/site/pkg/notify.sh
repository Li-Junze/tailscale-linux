#!/bin/sh
# notify.sh -- Linux 被控端消息通道(心跳 + 弹窗 + 文件 + 远程指令)
# 零依赖: 只要 curl。有 python3 时 JSON 解析更稳, 没有也能凑合跑。
set -e

DIR=$(cd "$(dirname "$0")" && pwd)
ROOM=""; RELAY="https://ts-remote-web.pages.dev"
# 不能用 . agent.ini 来 source: installed=2026-10-07 10:00:00 里的空格会让 sh 报错
if [ -f "$DIR/agent.ini" ]; then
  ROOM=$(sed -n 's/^room=//p' "$DIR/agent.ini" | head -1)
  _r=$(sed -n 's/^relay=//p' "$DIR/agent.ini" | head -1)
  [ -n "$_r" ] && RELAY="$_r"
  TOK=$(sed -n 's/^token=//p' "$DIR/agent.ini" | head -1)
fi
[ -z "$ROOM" ] && { echo "no room (agent.ini missing)"; exit 1; }

# 控制台有门禁, 所有请求都要带设备令牌
u() { if [ -n "$TOK" ]; then case "$1" in *\?*) echo "$1&t=$TOK" ;; *) echo "$1?t=$TOK" ;; esac; else echo "$1"; fi }

BASE="$HOME/TailscaleRemote"
INBOX="$BASE/Inbox"; OUTBOX="$BASE/Outbox"; SENT="$OUTBOX/sent"
mkdir -p "$INBOX" "$OUTBOX" "$SENT"
UA="ts-remote-agent/1.1-web"
LAST=0

jget() {   # jget <json> <field>  -> 取值
  if command -v python3 >/dev/null 2>&1; then
    printf '%s' "$1" | python3 -c "
import sys,json
d=json.load(sys.stdin)
for k in sys.argv[1:]:
    d=d.get(k) if isinstance(d,dict) else None
print('' if d is None else d)
" $2 2>/dev/null
  else
    printf '%s' "$1" | sed -n "s/.*\"$2\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/p" | head -1
  fi
}

beat() {
  IP=$(tailscale ip -4 2>/dev/null | head -1)
  [ -z "$IP" ] && IP=$(hostname -I 2>/dev/null | awk '{print $1}')
  curl -s -m 15 -X POST "$(u "$RELAY/api/beat")" -H 'Content-Type: application/json' \
       -d "{\"room\":\"$ROOM\",\"side\":\"pc\",\"info\":{\"host\":\"$(hostname)\",\"user\":\"$(id -un)\",\"ip\":\"$IP\",\"ver\":\"1.1-web\",\"path\":\"$DIR\"}}" \
       >/dev/null 2>&1 || true
}

send_text() {
  BODY=$(printf '%s' "$1" | awk '{printf "%s\\n", $0}' | sed 's/\\n$//')
  curl -s -m 15 -X POST "$(u "$RELAY/api/send")" -H 'Content-Type: application/json' \
       -d "{\"room\":\"$ROOM\",\"side\":\"pc\",\"kind\":\"${2:-text}\",\"body\":\"$BODY\"}" >/dev/null 2>&1 || true
}

download_file() {
  FID="$1"; NAME="$2"; CH="$3"
  SAFE=$(printf '%s' "$NAME" | tr '/\\:*?"<>|' '_______')
  DEST="$INBOX/$SAFE"
  : > "$DEST.part"
  i=0
  while [ "$i" -lt "$CH" ]; do
    curl -s -m 60 "$(u "$RELAY/api/download?fileId=$FID&i=$i")" >> "$DEST.part" || break
    i=$((i+1))
  done
  [ -f "$DEST" ] && DEST="$INBOX/${SAFE%.*}_$(date +%H%M%S).${SAFE##*.}"
  mv "$DEST.part" "$DEST"
  send_text "saved to inbox: $(basename "$DEST")" sys
  notify "$(basename "$DEST")"
}

upload_file() {
  P="$1"; NAME=$(basename "$P"); SZ=$(wc -c < "$P" | tr -d ' ')
  R=$(curl -s -m 20 -X POST "$(u "$RELAY/api/upload/begin")" -H 'Content-Type: application/json' \
      -d "{\"room\":\"$ROOM\",\"side\":\"pc\",\"name\":\"$NAME\",\"size\":$SZ}")
  FID=$(jget "$R" fileId)
  [ -z "$FID" ] && return 1
  CH=1180000; N=$(( (SZ + CH - 1) / CH )); i=0
  while [ "$i" -lt "$N" ]; do
    dd if="$P" bs=$CH skip=$i count=1 2>/dev/null | \
      curl -s -m 120 -X PUT --data-binary @- "$(u "$RELAY/api/upload/chunk?fileId=$FID&i=$i")" >/dev/null || return 1
    i=$((i+1))
  done
  curl -s -m 15 -X POST "$(u "$RELAY/api/send")" -H 'Content-Type: application/json' \
       -d "{\"room\":\"$ROOM\",\"side\":\"pc\",\"kind\":\"file\",\"body\":\"{\\\"fileId\\\":\\\"$FID\\\",\\\"name\\\":\\\"$NAME\\\",\\\"size\\\":$SZ}\"}" >/dev/null
}

notify() {
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "网页控制台" "$1" 2>/dev/null || true
  fi
  printf '\n[web] %s\n' "$1"
}

cleanup() {
  send_text "[sys] 收到清除指令, 开始卸载 ..." sys
  sh "$DIR/clean.sh" >/dev/null 2>&1 || true
  send_text "[sys] 清除完成" sys
  exit 0
}

beat
# 启动即对齐游标, 避免重放历史指令(上次下发过的 cleanup 会把刚起来的 agent 自己卸掉)
if command -v python3 >/dev/null 2>&1; then
  _mx=$(curl -s -m 20 "$(u "$RELAY/api/pull?room=$ROOM&after=0")" | python3 -c "
import sys,json
try: print(max([m['id'] for m in json.load(sys.stdin).get('msgs',[])] or [0]))
except Exception: print(0)
" 2>/dev/null)
  case "$_mx" in ''|*[!0-9]*) _mx=0 ;; esac
  LAST=$_mx
fi
echo "room=$ROOM relay=$RELAY dir=$DIR last=$LAST"

while true; do
  D=$(curl -s -m 20 "$(u "$RELAY/api/pull?room=$ROOM&after=$LAST")")
  [ -z "$D" ] && { sleep 3; continue; }
  if command -v python3 >/dev/null 2>&1; then
    CNT=$(printf '%s' "$D" | python3 -c "import sys,json; print(len(json.load(sys.stdin).get('msgs',[])))" 2>/dev/null || echo 0)
    IDX=0
    while [ "$IDX" -lt "$CNT" ]; do
      M=$(printf '%s' "$D" | python3 -c "
import sys,json
m=json.load(sys.stdin)['msgs'][$IDX]
print(json.dumps(m,ensure_ascii=False))
" 2>/dev/null)
      IDX=$((IDX+1))
      [ -z "$M" ] && continue
      ID=$(jget "$M" id); SIDE=$(jget "$M" side); KIND=$(jget "$M" kind); BODY=$(jget "$M" body)
      case "$ID" in ''|*[!0-9]*) ID=0 ;; esac
      [ "$ID" -gt "$LAST" ] && LAST=$ID
      [ "$SIDE" = "pc" ] && continue
      case "$KIND" in
        text) notify "$BODY" ;;
        file)
          FID=$(jget "$M" body | sed -n 's/.*"fileId":"\([^"]*\)".*/\1/p')
          NM=$(printf '%s' "$BODY" | sed -n 's/.*"name":"\([^"]*\)".*/\1/p')
          if [ -n "$FID" ]; then
            FI=$(curl -s -m 20 "$(u "$RELAY/api/fileinfo?fileId=$FID")")
            SZ=$(jget "$FI" size); CH=$(jget "$FI" chunks)
            download_file "$FID" "$NM" "${CH:-1}"
          fi ;;
        cmd) case "$BODY" in
               *cleanup*) cleanup ;;
               *info*)    send_text "[info] host=$(hostname) user=$(id -un) path=$DIR" sys ;;
               *join*)    AK=$(echo "$BODY" | sed -n 's/.*"authkey":"\([^"]*\)".*/\1/p')
                           if [ -n "$AK" ] && command -v tailscale >/dev/null 2>&1; then
                             send_text "[sys] 收到入网指令, 正在加入 tailnet ..." sys
                             tailscale up --authkey="$AK" --accept-routes >/dev/null 2>&1
                             sleep 5
                             IP=$(tailscale ip -4 2>/dev/null | head -1)
                             [ -n "$IP" ] && send_text "[sys] 入网成功, 本机 IP = $IP" sys \
                                          || send_text "[sys] 入网命令已执行, 暂未拿到 100.x 地址" sys
                           else
                             send_text "[sys] join 失败: 缺 authkey 或没装 tailscale" sys
                           fi ;;
             esac ;;
      esac
    done
  fi

  # 发件箱: 丢文件进去自动回传
  for f in "$OUTBOX"/*; do
    [ -f "$f" ] || continue
    upload_file "$f" && { send_text "sent to web: $(basename "$f")" sys; mv "$f" "$SENT/"; }
  done

  sleep 3
  beat
done
