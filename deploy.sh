#!/bin/sh
set -eu

usage() {
  cat <<'USAGE'
Usage:
  ./deploy.sh <company-key> [server-ip] [admin-name] [admin-username]

Example:
  ./deploy.sh acme 193.112.26.217 张三 zhangsan

company-key must be lowercase English letters, numbers, or hyphens.
admin-name and admin-username are required only for the first deployment.
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

COMPANY_KEY=${1:-}
REMOTE_HOST=${2:-193.112.26.217}
ADMIN_NAME=${3:-}
ADMIN_USERNAME=${4:-}
SSH_TARGET="ubuntu@$REMOTE_HOST"

case "$COMPANY_KEY" in
  ''|-*|*-|[!a-z0-9-]*|*[!a-z0-9-]*)
    echo "company-key invalid: use lowercase English letters, numbers, or hyphens" >&2
    usage
    exit 1
    ;;
esac
if [ "${#COMPANY_KEY}" -gt 32 ]; then
  echo "company-key must be at most 32 characters" >&2
  exit 1
fi
if [ "${APP_PORT:-}" ]; then
  case "$APP_PORT" in
    *[!0-9]*|'') echo "APP_PORT must be a number" >&2; exit 1 ;;
  esac
fi

echo "==> Checking SSH access"
ssh -o BatchMode=yes "$SSH_TARGET" "id && test -f /etc/os-release"

echo "==> Preparing server"
ssh "$SSH_TARGET" '
  set -eu
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 nginx sqlite3 rsync curl openssl >/dev/null
'

DB_EXISTS=no
REMOTE_DIR="/opt/wms-$COMPANY_KEY"
SERVICE_NAME="wms-$COMPANY_KEY"
ENV_FILE="/etc/wms/$COMPANY_KEY.env"
if ssh "$SSH_TARGET" "test -f '$REMOTE_DIR/data/warehouse.db'"; then
  DB_EXISTS=yes
fi

if [ "$DB_EXISTS" = "no" ]; then
  if [ -z "$ADMIN_NAME" ] || [ -z "$ADMIN_USERNAME" ]; then
    echo "First deployment needs admin-name and admin-username." >&2
    usage
    exit 1
  fi
  if [ -z "${WMS_ADMIN_PASSWORD:-}" ]; then
    ADMIN_PASSWORD="$(openssl rand -base64 18 | tr +/ Aa)"
    SHOW_PASSWORD=1
  else
    ADMIN_PASSWORD="$WMS_ADMIN_PASSWORD"
    SHOW_PASSWORD=0
  fi
else
  ADMIN_PASSWORD="unused-for-existing-database"
  SHOW_PASSWORD=0
fi

if [ -z "${APP_PORT:-}" ]; then
  if [ "$DB_EXISTS" = yes ]; then
    APP_PORT=$(ssh "$SSH_TARGET" "sudo awk -F= '\$1 == \"WMS_APP_PORT\" {print \$2}' '$ENV_FILE'")
  fi
fi

if [ -z "${APP_PORT:-}" ]; then
  APP_PORT=$(ssh "$SSH_TARGET" '
    set -eu
    for port in $(seq 8000 8100); do
      used=no
      for file in /etc/wms/*.env; do
        [ -e "$file" ] || continue
        if grep -q "^WMS_APP_PORT=$port$" "$file"; then used=yes; break; fi
      done
      if [ "$used" = no ] && ! ss -atn | grep -Eq "[[:space:]]:$port[[:space:]]"; then
        echo "$port"
        exit 0
      fi
    done
    exit 1
  ')
fi

echo "==> Uploading application"
rsync -az --delete \
  --exclude '.git/' \
  --exclude '.pycache/' \
  --exclude '__pycache__/' \
  --exclude '/data/' \
  --exclude '.DS_Store' \
  ./ "$SSH_TARGET:/tmp/wms-$COMPANY_KEY/"

echo "==> Installing company instance"
ssh "$SSH_TARGET" \
  "sudo env COMPANY_KEY='$COMPANY_KEY' REMOTE_DIR='$REMOTE_DIR' \
   SERVICE_NAME='$SERVICE_NAME' ENV_FILE='$ENV_FILE' APP_PORT='$APP_PORT' \
   ADMIN_NAME='$ADMIN_NAME' ADMIN_USERNAME='$ADMIN_USERNAME' \
   ADMIN_PASSWORD='$ADMIN_PASSWORD' DB_EXISTS='$DB_EXISTS' sh -s" <<'REMOTE'
set -eu

mkdir -p "$REMOTE_DIR"
if [ "$DB_EXISTS" = yes ] && [ -f "$REMOTE_DIR/data/warehouse.db" ]; then
  mkdir -p "$REMOTE_DIR/data/backups"
  backup="$REMOTE_DIR/data/backups/before-$(date +%Y%m%d-%H%M%S).db"
  sqlite3 "$REMOTE_DIR/data/warehouse.db" ".backup '$backup'"
fi

rsync -a --delete \
  --exclude /data \
  --exclude .pycache \
  --exclude __pycache__ \
  "/tmp/wms-$COMPANY_KEY/" "$REMOTE_DIR/"
rm -rf "/tmp/wms-$COMPANY_KEY"
mkdir -p "$REMOTE_DIR/data"
install -d -m 700 /etc/wms

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<ENV
WMS_COMPANY_KEY=$COMPANY_KEY
WMS_APP_PORT=$APP_PORT
WMS_BASE_PATH=/$COMPANY_KEY
WMS_ADMIN_NAME=$ADMIN_NAME
WMS_ADMIN_USERNAME=$ADMIN_USERNAME
WMS_ADMIN_PASSWORD=$ADMIN_PASSWORD
ENV
fi
chown root:root "$ENV_FILE"
chmod 600 "$ENV_FILE"
chown -R www-data:www-data "$REMOTE_DIR"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=Warehouse Management System - $COMPANY_KEY
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REMOTE_DIR/server/main.py --host 127.0.0.1 --port $APP_PORT --db $REMOTE_DIR/data/warehouse.db --base-path /$COMPANY_KEY
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$REMOTE_DIR/data

[Install]
WantedBy=multi-user.target
SERVICE

sudo mkdir -p /etc/nginx/wms-routes
cat > "/etc/nginx/wms-routes/$COMPANY_KEY.conf" <<ROUTE
location = /$COMPANY_KEY {
    return 308 /$COMPANY_KEY/;
}

location ^~ /$COMPANY_KEY/ {
    proxy_pass http://127.0.0.1:$APP_PORT;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto \$scheme;
}
ROUTE

cat > /etc/nginx/sites-available/wms <<NGINX
server {
    listen 80 default_server;
    server_name _;
    client_max_body_size 16m;
    include /etc/nginx/wms-routes/*.conf;
}
NGINX
ln -sfn /etc/nginx/sites-available/wms /etc/nginx/sites-enabled/wms
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl reload nginx
REMOTE

echo "==> Running remote tests"
ssh "$SSH_TARGET" \
  "cd '$REMOTE_DIR' && PYTHONPYCACHEPREFIX=/tmp/wms-pycache python3 -m unittest discover -s tests/cases -v"

echo "==> Health check"
ssh "$SSH_TARGET" \
  "for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS 'http://127.0.0.1:$APP_PORT/$COMPANY_KEY/api/health' && exit 0; sleep 1; done; exit 1"
echo

if [ "$SHOW_PASSWORD" -eq 1 ]; then
  echo "==> Initial admin credentials, shown only once:"
  echo "username: $ADMIN_USERNAME"
  echo "password: $ADMIN_PASSWORD"
  echo "Change the password immediately after first login."
fi

echo "==> Deployment complete: http://$REMOTE_HOST/$COMPANY_KEY/"
