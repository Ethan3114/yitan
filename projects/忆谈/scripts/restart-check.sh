#!/usr/bin/env bash
# 重启恢复钩子。标准输出最后一行只放结果。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ACTION="${1:-}"
PORT=8091
DIR="${RESTART_CHECK_DIR:-}"
if [ -z "$DIR" ]; then echo "缺少 RESTART_CHECK_DIR" >&2; exit 1; fi
PY="$ROOT/backend/.venv/bin/python"
KEYFILE="$DIR/fernet.key"
COOKIE="$DIR/cookies.txt"
PIDFILE="$DIR/server.pid"

if [ ! -f "$KEYFILE" ]; then
  "$PY" -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' > "$KEYFILE"
fi

export DATA_DIR="$DIR"
export DATABASE_URL="sqlite:///$DIR/restart.db"
export AUDIO_FERNET_KEY="$(tr -d '\n' < "$KEYFILE")"
export INVITE_CODE="yitan-local"
export DOUBAO_API_KEY=""
export WEB_ORIGIN="http://127.0.0.1:3011"

start_server() {
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "端口 ${PORT} 已被占用" >&2
    exit 1
  fi
  cd "$ROOT/backend"
  nohup "$PY" -m uvicorn app.main:app_factory --factory --host 127.0.0.1 --port "$PORT" > "$DIR/server.log" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do
    if curl -sf "http://127.0.0.1:${PORT}/health" >/dev/null; then
      cat "$PIDFILE"
      return
    fi
    sleep 0.3
  done
  echo "服务没有起来" >&2
  exit 1
}

case "$ACTION" in
  start) start_server ;;
  write)
    MARK="${2:-}"
    curl -sf -c "$COOKIE" -H 'Content-Type: application/json' -d '{"code":"yitan-local"}' "http://127.0.0.1:${PORT}/api/v1/auth/login" >/dev/null
    curl -sf -b "$COOKIE" -H 'Content-Type: application/json' -d "{\"elder_name\":\"${MARK}\"}" "http://127.0.0.1:${PORT}/api/v1/profiles" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["id"])'
    ;;
  read)
    RID="${2:-}"
    curl -sf -b "$COOKIE" "http://127.0.0.1:${PORT}/api/v1/profiles/${RID}" | "$PY" -c 'import json,sys; print(json.load(sys.stdin)["elder_name"])'
    ;;
  stop)
    if [ -f "$PIDFILE" ]; then kill "$(cat "$PIDFILE")" >/dev/null 2>&1 || true; fi
    echo stopped
    ;;
  *) echo "未知动作" >&2; exit 1 ;;
esac
