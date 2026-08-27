#!/bin/sh
set -eu

usage() {
  cat <<'USAGE'
Usage:
  ./deploy.sh <company-key> [server-ip]

Company key:
  lowercase letters, numbers, and hyphens; maximum 32 characters.
  "default" is the original single-company installation.

First/default company:
  COMPANY_NAME="Acme" WMS_ADMIN_PHONE=13812345678 ./deploy.sh default 193.112.26.217

Additional company:
  APP_PORT=8001 PUBLIC_PORT=8081 COMPANY_NAME="Beta" WMS_ADMIN_PHONE=13912345678 ./deploy.sh beta 193.112.26.217
USAGE
}

if [ "${1:-}" = "-h" ] || [ "${1:-}" = "--help" ]; then
  usage
  exit 0
fi

COMPANY_KEY=${1:-default}
REMOTE_HOST=${2:-193.112.26.217}
SSH_TARGET="ubuntu@$REMOTE_HOST"
COMPANY_NAME=${COMPANY_NAME:-}

case "$COMPANY_KEY" in
  default) ;;
  ''|-*|*-|[!a-z0-9-]*|*[!a-z0-9-]*)
    echo "Invalid company key: $COMPANY_KEY" >&2
    usage
    exit 1
    ;;
esac

if [ "${#COMPANY_KEY}" -gt 32 ]; then
  echo "Company key must be at most 32 characters." >&2
  exit 1
fi

if [ -z "$COMPANY_NAME" ]; then
  echo "COMPANY_NAME is required, for example COMPANY_NAME=\"Acme\"." >&2
  exit 1
fi

case "$COMPANY_NAME" in
  ''|' '*|*' '*|*[!A-Za-z0-9.,\ -]*)
    echo "COMPANY_NAME must use English letters, numbers, spaces, or . , - only." >&2
    exit 1
    ;;
esac

if [ "$COMPANY_KEY" = "default" ]; then
  REMOTE_DIR=${REMOTE_DIR:-/opt/wms}
  SERVICE_NAME=wms
  ENV_FILE=/etc/wms/wms.env
  NGINX_SITE=wms
  APP_PORT=${APP_PORT:-8000}
  PUBLIC_PORT=${PUBLIC_PORT:-80}
else
  REMOTE_DIR=${REMOTE_DIR:-/opt/wms-$COMPANY_KEY}
  SERVICE_NAME=wms-$COMPANY_KEY
  ENV_FILE=/etc/wms/$COMPANY_KEY.env
  NGINX_SITE=wms-$COMPANY_KEY
  : "${APP_PORT:?Set APP_PORT, for example APP_PORT=8001}"
  : "${PUBLIC_PORT:?Set PUBLIC_PORT, for example PUBLIC_PORT=8081}"
fi

if [ -z "${WMS_ADMIN_PHONE:-}" ]; then
  echo "WMS_ADMIN_PHONE is required." >&2
  exit 1
fi
ADMIN_PHONE="$WMS_ADMIN_PHONE"

echo "==> Checking SSH access"
ssh -o BatchMode=yes "$SSH_TARGET" "id && test -f /etc/os-release"

echo "==> Checking whether this is an initial deployment"
DB_EXISTS=no
if ssh "$SSH_TARGET" "test -f '$REMOTE_DIR/data/warehouse.db'"; then
  DB_EXISTS=yes
fi

if [ "$DB_EXISTS" = "no" ] && [ -z "${WMS_ADMIN_PASSWORD:-}" ]; then
  ADMIN_PASSWORD="$(openssl rand -base64 18 | tr +/ Aa)"
  SHOW_PASSWORD=1
else
  ADMIN_PASSWORD="${WMS_ADMIN_PASSWORD:-unused-for-existing-database}"
  SHOW_PASSWORD=0
fi

echo "==> Installing system dependencies"
ssh "$SSH_TARGET" '
  set -eu
  export DEBIAN_FRONTEND=noninteractive
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3 nginx sqlite3 rsync curl >/dev/null
'

echo "==> Uploading application"
rsync -az --delete \
  --exclude '.pycache/' \
  --exclude '__pycache__/' \
  --exclude 'data/' \
  --exclude '.DS_Store' \
./ "$SSH_TARGET:/tmp/wms-next/"

echo "==> Installing company instance"
ssh "$SSH_TARGET" \
  "sudo env COMPANY_KEY='$COMPANY_KEY' COMPANY_NAME='$COMPANY_NAME' \
   REMOTE_DIR='$REMOTE_DIR' ENV_FILE='$ENV_FILE' APP_PORT='$APP_PORT' \
   SERVICE_NAME='$SERVICE_NAME' NGINX_SITE='$NGINX_SITE' \
   PUBLIC_PORT='$PUBLIC_PORT' \
   ADMIN_PHONE='$ADMIN_PHONE' ADMIN_PASSWORD='$ADMIN_PASSWORD' \
   DB_EXISTS='$DB_EXISTS' sh -s" <<'REMOTE'
set -eu

mkdir -p "$REMOTE_DIR"
if [ "$DB_EXISTS" = "yes" ] && [ -f "$REMOTE_DIR/data/warehouse.db" ]; then
  mkdir -p "$REMOTE_DIR/data/backups"
  backup="$REMOTE_DIR/data/backups/before-$(date +%Y%m%d-%H%M%S).db"
  sqlite3 "$REMOTE_DIR/data/warehouse.db" ".backup '$backup'"
fi

rsync -a --delete \
  --exclude data \
  --exclude .pycache \
  --exclude __pycache__ \
  /tmp/wms-next/ "$REMOTE_DIR/"
rm -rf /tmp/wms-next
mkdir -p "$REMOTE_DIR/data"
install -d -m 700 /etc/wms

if [ ! -f "$ENV_FILE" ]; then
  umask 077
  cat > "$ENV_FILE" <<ENV
WMS_ADMIN_PHONE=$ADMIN_PHONE
WMS_ADMIN_PASSWORD=$ADMIN_PASSWORD
WMS_COMPANY_NAME=$COMPANY_NAME
ENV
fi

chown -R www-data:www-data "$REMOTE_DIR"

cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<SERVICE
[Unit]
Description=Warehouse Management System - $COMPANY_NAME
After=network.target

[Service]
Type=simple
User=www-data
Group=www-data
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$ENV_FILE
ExecStart=/usr/bin/python3 $REMOTE_DIR/server/main.py --host 127.0.0.1 --port $APP_PORT --db $REMOTE_DIR/data/warehouse.db
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$REMOTE_DIR/data

[Install]
WantedBy=multi-user.target
SERVICE

cat > "/etc/nginx/sites-available/$NGINX_SITE" <<NGINX
server {
    listen $PUBLIC_PORT default_server;
    server_name _;
    client_max_body_size 8m;

    location / {
        proxy_pass http://127.0.0.1:$APP_PORT;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX

ln -sfn "/etc/nginx/sites-available/$NGINX_SITE" \
  "/etc/nginx/sites-enabled/$NGINX_SITE"
if [ "$COMPANY_KEY" = "default" ]; then
  rm -f /etc/nginx/sites-enabled/default
fi

nginx -t
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl reload nginx
REMOTE

echo "==> Running remote tests"
ssh "$SSH_TARGET" \
  "cd '$REMOTE_DIR' && PYTHONPYCACHEPREFIX=/tmp/wms-pycache python3 -m unittest discover -s tests -v"

echo "==> Health check"
ssh "$SSH_TARGET" \
  "for i in 1 2 3 4 5; do curl -fsS http://127.0.0.1:$APP_PORT/api/health && exit 0; sleep 1; done; exit 1"
echo

if [ "$SHOW_PASSWORD" -eq 1 ]; then
  echo "==> Initial admin credentials, shown only once:"
  echo "company: $COMPANY_NAME"
  echo "phone: $ADMIN_PHONE"
  echo "password: $ADMIN_PASSWORD"
  echo "Change the password immediately after first login."
fi

echo "==> Deployment complete: http://$REMOTE_HOST:$PUBLIC_PORT/"
