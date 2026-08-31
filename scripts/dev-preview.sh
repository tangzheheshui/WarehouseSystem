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

  WAREHOUSE_ID=$(curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/warehouses" \
    -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
    --data '{"warehouseName":"主仓库","address":"上海市青浦区物流园","contactName":"刘仓管","contactPhone":"13800000000"}' \
    | jsonget id)

  for location in "A库区|A货区|A-01" "A库区|A货区|A-02" "A库区|B货区|B-01" "B库区|A货区|A-01"; do
    AREA=$(echo "$location" | cut -d'|' -f1)
    CARGO=$(echo "$location" | cut -d'|' -f2)
    NAME=$(echo "$location" | cut -d'|' -f3)
    curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/warehouse-locations" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data "{\"warehouseId\":$WAREHOUSE_ID,\"storageArea\":\"$AREA\",\"cargoArea\":\"$CARGO\",\"locationName\":\"$NAME\"}" >/dev/null
  done

  for project_name in 上海冷链仓储项目 深圳电子仓项目 广州日用品仓项目 佛山家居仓项目; do
    curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/projects" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data "{\"projectName\":\"$project_name\",\"warehouseId\":$WAREHOUSE_ID,\"storageArea\":\"A库区\",\"remark\":\"本地预览数据\"}" >/dev/null
  done

  for product in "P001|东海带鱼|冷链食品|箱|365" "P002|冷冻虾仁|冷链食品|箱|180" "P003|智能手表|电子产品|个|730" "P004|洗衣液|日用品|瓶|1095" "P005|实木餐桌|家居|件|3650"; do
    CODE=$(echo "$product" | cut -d'|' -f1)
    NAME=$(echo "$product" | cut -d'|' -f2)
    CATEGORY=$(echo "$product" | cut -d'|' -f3)
    UNIT=$(echo "$product" | cut -d'|' -f4)
    SHELF=$(echo "$product" | cut -d'|' -f5)
    curl -sS -X POST "http://127.0.0.1:$SEED_PORT/api/products" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data "{\"productCode\":\"$CODE\",\"productName\":\"$NAME\",\"category\":\"$CATEGORY\",\"unit\":\"$UNIT\",\"shelfLifeDays\":$SHELF}" >/dev/null
  done

  PROJECT_IDS=$(curl -sS "http://127.0.0.1:$SEED_PORT/api/projects" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import json,sys; print(json.dumps([p["id"] for p in json.load(sys.stdin)]))')

  PRODUCT_IDS=$(curl -sS "http://127.0.0.1:$SEED_PORT/api/products?status=active" \
    -H "Authorization: Bearer $TOKEN" \
    | python3 -c 'import json,sys; print(json.dumps([p["product_id"] for p in json.load(sys.stdin)]))')

  for PROJECT_ID in $(printf '%s' "$PROJECT_IDS" | python3 -c 'import json,sys; print(" ".join(map(str, json.load(sys.stdin))))'); do
    curl -sS -X PUT "http://127.0.0.1:$SEED_PORT/api/projects/$PROJECT_ID/products" \
      -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
      --data "{\"productIds\":$PRODUCT_IDS}" >/dev/null
  done

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
