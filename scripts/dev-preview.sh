#!/bin/sh
set -eu

# 一键本地预览：固定端口、固定假数据、重复启动秒级完成。
# 用法：scripts/dev-preview.sh [端口]
# 重置假数据：rm -rf .preview

PORT=${1:-8000}
SEED_PORT=${WMS_PREVIEW_SEED_PORT:-18099}
BASE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PREVIEW_DIR="$BASE_DIR/.preview"
DB="$PREVIEW_DIR/warehouse.db"
MARKER="$PREVIEW_DIR/.seeded"

cd "$BASE_DIR"
mkdir -p "$PREVIEW_DIR"

jsonget() {
  python3 -c 'import json,sys; print(json.load(sys.stdin)[sys.argv[1]])' "$1"
}

wait_health() {
  i=0
  while [ "$i" -lt 100 ]; do
    if curl -sf "http://127.0.0.1:$1/api/health" >/dev/null 2>&1; then
      return 0
    fi
    i=$((i + 1))
    sleep 0.1
  done
  return 1
}

if [ ! -f "$MARKER" ]; then
  echo "==> 初始化预览数据（仅首次）"
  rm -f "$DB" "$DB-wal" "$DB-shm" "$MARKER"

  python3 server/main.py --db "$DB" --host 127.0.0.1 --port "$SEED_PORT" >/dev/null 2>&1 &
  SEED_PID=$!
  trap 'kill "$SEED_PID" 2>/dev/null || true' EXIT

  if ! wait_health "$SEED_PORT"; then
    kill "$SEED_PID" 2>/dev/null || true
    echo "预览数据初始化失败：端口 $SEED_PORT 无法启动服务" >&2
    exit 1
  fi

  TOKEN=$(curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/login" \
    -H 'Content-Type: application/json' \
    --data '{"username":"admin","password":"Admin123456"}' | jsonget token)

  curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/change-password" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data '{"oldPassword":"Admin123456","newPassword":"PreviewAdmin123"}' >/dev/null

  for project_name in 上海冷链仓储项目 深圳电子仓项目 广州日用品仓项目 佛山家居仓项目; do
    curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/projects" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data "{\"projectName\":\"$project_name\",\"remark\":\"本地预览数据\"}" >/dev/null
  done

  PROJECT_IDS=$(curl -sS "http://127.0.0.1:$SEED_PORT/api/projects" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import json,sys; print(json.dumps([p["id"] for p in json.load(sys.stdin)]))')

  curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/users" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data "{\"username\":\"preview\",\"displayName\":\"刘海峰\",\"role\":\"project_admin\",\"password\":\"Preview123456\",\"projectIds\":$PROJECT_IDS}" >/dev/null

  kill "$SEED_PID" 2>/dev/null || true
  wait "$SEED_PID" 2>/dev/null || true
  trap - EXIT

  python3 - "$DB" <<'PY'
import sqlite3
import sys

connection = sqlite3.connect(sys.argv[1])
connection.execute(
    "UPDATE users SET must_change_password = 0 WHERE username = 'preview'"
)
connection.commit()
connection.close()
PY

  touch "$MARKER"
  echo "==> 预览数据就绪"
fi

echo "==> 本地预览: http://127.0.0.1:$PORT/"
echo "==> 项目管理员: preview / Preview123456"
echo "==> 系统管理员: admin / PreviewAdmin123"
exec python3 server/main.py --db "$DB" --host 127.0.0.1 --port "$PORT"
