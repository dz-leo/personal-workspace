#!/bin/bash
# 个人账本 —— 双击启动（起后端 + 自动开浏览器）
# 注意：这个终端窗口别关，关了服务就停。想开机自启请用 launchd（见下方说明）。

cd "$(dirname "$0")" || exit 1
PORT=8765
LOG=/tmp/ledger-server.log
CONF="${LEDGER_CONF:-$HOME/.ledger.conf}"

# ---- 挑一个装了 pymysql 的 python：环境变量 > 本机常见路径 > PATH 里的 python3 ----
PY=""
for c in "${LEDGER_PYTHON:-}" \
         "$HOME/.workbuddy/binaries/python/envs/default/bin/python" \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
  [ -n "$c" ] || continue
  if [ -x "$c" ] && "$c" -c "import pymysql" >/dev/null 2>&1; then
    PY="$c"
    break
  fi
done
if [ -z "$PY" ]; then
  # 再试 PATH 里的 python3 / python
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1 && "$c" -c "import pymysql" >/dev/null 2>&1; then
      PY="$(command -v "$c")"
      break
    fi
  done
fi
if [ -z "$PY" ]; then
  echo "[错误] 没找到装了 PyMySQL 的 python。"
  echo "       装一个： pip3 install pymysql"
  echo "       或指定解释器： LEDGER_PYTHON=/path/to/python bash start.command"
  exit 1
fi

# ---- 配置文件（数据库口令）缺失就先把模板生成出来，别让服务起来连不上库 ----
if [ ! -f "$CONF" ]; then
  echo "首次运行：正在生成数据库配置 $CONF"
  "$PY" server.py --init-conf || exit 1
  echo ""
  echo "请把上面提示里的 db_password= 填成本机 MySQL 的 root 密码，然后重新双击本文件。"
  exit 1
fi
if ! grep -q '^db_password=..*' "$CONF" 2>/dev/null; then
  echo "[错误] $CONF 里 db_password 还是空的，先填上 MySQL 密码再启动。"
  exit 1
fi

# 端口已被占用 → 说明服务已在跑，直接开浏览器
if curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/"; then
  echo "账本服务已在运行，直接打开浏览器…"
else
  echo "正在启动账本服务（端口 ${PORT}）…"
  "$PY" server.py --host 0.0.0.0 --port "$PORT" > "$LOG" 2>&1 &
  SRV_PID=$!
  for i in $(seq 1 20); do
    sleep 0.5
    if curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/"; then
      echo "服务已就绪（PID ${SRV_PID}）"
      break
    fi
  done
  if ! curl -s --noproxy '*' -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/"; then
    echo "启动失败，查看日志："
    tail -20 "$LOG"
  fi
fi

LAN_IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)
echo "本机访问：http://127.0.0.1:${PORT}/"
[ -n "$LAN_IP" ] && echo "手机访问（同一 WiFi）：http://${LAN_IP}:${PORT}/"
open "http://127.0.0.1:${PORT}/"

echo ""
echo "────────────────────────────────────────"
echo "保持这个窗口开着，服务才不会停。"
echo "想开机自启、崩溃自动重启，在终端执行一次："
echo "  launchctl bootstrap gui/\$UID ~/Library/LaunchAgents/com.dazhi.ledger.plist"
echo "────────────────────────────────────────"