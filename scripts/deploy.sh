#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/deploy.sh <company-key> [server-ip] [admin-name] [admin-username]

Example:
  ./scripts/deploy.sh acme 193.112.26.217 张三 zhangsan

company-key must be lowercase English letters, numbers, or hyphens.
admin-name and admin-username are required only for the first deployment.

Each company is served at its own subdomain:
  https://<company-key>.wms.tangzheheshui.cn
Override the base domain with the WMS_DOMAIN environment variable.
DNS needs a wildcard A record: *.wms -> <server-ip>.
USAGE
}

WMS_DOMAIN=${WMS_DOMAIN:-wms.tangzheheshui.cn}

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
   WMS_DOMAIN='$WMS_DOMAIN' \
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
WMS_BASE_PATH=/
WMS_ADMIN_NAME=$ADMIN_NAME
WMS_ADMIN_USERNAME=$ADMIN_USERNAME
WMS_ADMIN_PASSWORD=$ADMIN_PASSWORD
ENV
fi
# 历史实例的 env 里可能是 /<company>，统一改成根路径（子域名部署）
if grep -q '^WMS_BASE_PATH=' "$ENV_FILE"; then
  sed -i 's|^WMS_BASE_PATH=.*|WMS_BASE_PATH=/|' "$ENV_FILE"
else
  printf '\nWMS_BASE_PATH=/\n' >> "$ENV_FILE"
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
ExecStart=/usr/bin/python3 $REMOTE_DIR/server/main.py --host 127.0.0.1 --port $APP_PORT --db $REMOTE_DIR/data/warehouse.db --base-path /
Restart=always
RestartSec=3
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$REMOTE_DIR/data

[Install]
WantedBy=multi-user.target
SERVICE

# —— 旧路径路由：/acme/... 永久跳转到 acme.wms...（历史链接不断）——
sudo mkdir -p /etc/nginx/wms-routes
cat > "/etc/nginx/wms-routes/$COMPANY_KEY.conf" <<ROUTE
location = /$COMPANY_KEY {
    return 308 https://$COMPANY_KEY.$WMS_DOMAIN/;
}

location ^~ /$COMPANY_KEY/ {
    rewrite ^/$COMPANY_KEY/(.*)\$ https://$COMPANY_KEY.$WMS_DOMAIN/\$1 permanent;
}
ROUTE

# —— 共享站点：catch-all 只做 ACME 验证和旧路径跳转 ——
cat > /etc/nginx/sites-available/wms <<NGINX
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    client_max_body_size 16m;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    include /etc/nginx/wms-routes/*.conf;
}
NGINX
ln -sfn /etc/nginx/sites-available/wms /etc/nginx/sites-enabled/wms
rm -f /etc/nginx/sites-enabled/default
sudo mkdir -p /var/www/certbot

# —— 落地页 $WMS_DOMAIN → 默认公司（当前部署的公司）；先 HTTP，证书就绪后升级 ——
LANDING_CERT="/etc/letsencrypt/live/$WMS_DOMAIN"
write_landing_http() {
  cat > /etc/nginx/sites-available/wms-landing <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $WMS_DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 308 https://$COMPANY_KEY.$WMS_DOMAIN/; }
}
NGINX
}
write_landing_https() {
  cat > /etc/nginx/sites-available/wms-landing <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $WMS_DOMAIN;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 308 https://$WMS_DOMAIN\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $WMS_DOMAIN;
    ssl_certificate $LANDING_CERT/fullchain.pem;
    ssl_certificate_key $LANDING_CERT/privkey.pem;
    location / { return 308 https://$COMPANY_KEY.$WMS_DOMAIN/; }
}
NGINX
}
write_landing_http
ln -sfn /etc/nginx/sites-available/wms-landing /etc/nginx/sites-enabled/wms-landing

# —— 公司站点：先写 HTTP 版（ACME 验证 + 直接反代），证书就绪后升级为 HTTPS ——
CERT_NAME="$COMPANY_KEY.$WMS_DOMAIN"
CERT_DIR="/etc/letsencrypt/live/$CERT_NAME"
cat > "/etc/nginx/sites-available/wms-$COMPANY_KEY" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $CERT_NAME;
    client_max_body_size 16m;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
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
ln -sfn "/etc/nginx/sites-available/wms-$COMPANY_KEY" "/etc/nginx/sites-enabled/wms-$COMPANY_KEY"

nginx -t
systemctl daemon-reload
systemctl enable --now "$SERVICE_NAME"
systemctl restart "$SERVICE_NAME"
systemctl reload nginx

# —— ACME 验证位已生效，现在签证书 ——
if [ ! -f "$LANDING_CERT/fullchain.pem" ]; then
  certbot certonly --webroot -w /var/www/certbot -d "$WMS_DOMAIN" \
    --non-interactive --agree-tos >/dev/null 2>&1 || true
fi
if [ -f "$LANDING_CERT/fullchain.pem" ]; then
  write_landing_https
fi

if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
  certbot certonly --webroot -w /var/www/certbot -d "$CERT_NAME" \
    --non-interactive --agree-tos >/dev/null 2>&1 || true
fi

if [ -f "$CERT_DIR/fullchain.pem" ]; then
  cat > "/etc/nginx/sites-available/wms-$COMPANY_KEY" <<NGINX
server {
    listen 80;
    listen [::]:80;
    server_name $CERT_NAME;
    client_max_body_size 16m;
    location /.well-known/acme-challenge/ { root /var/www/certbot; }
    location / { return 308 https://\$host\$request_uri; }
}

server {
    listen 443 ssl http2;
    listen [::]:443 ssl http2;
    server_name $CERT_NAME;
    client_max_body_size 16m;
    ssl_certificate $CERT_DIR/fullchain.pem;
    ssl_certificate_key $CERT_DIR/privkey.pem;
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
fi

nginx -t
systemctl reload nginx
REMOTE

echo "==> Running remote tests"
ssh "$SSH_TARGET" \
  "cd '$REMOTE_DIR' && PYTHONPYCACHEPREFIX=/tmp/wms-pycache python3 -m unittest discover -s tests/cases -v"

echo "==> Health check"
ssh "$SSH_TARGET" \
  "for i in 1 2 3 4 5 6 7 8 9 10; do curl -fsS 'http://127.0.0.1:$APP_PORT/api/health' && exit 0; sleep 1; done; exit 1"
echo

if [ "$SHOW_PASSWORD" -eq 1 ]; then
  echo "==> Initial admin credentials, shown only once:"
  echo "username: $ADMIN_USERNAME"
  echo "password: $ADMIN_PASSWORD"
  echo "Change the password immediately after first login."
fi

if ssh "$SSH_TARGET" "sudo test -f '/etc/letsencrypt/live/$COMPANY_KEY.$WMS_DOMAIN/fullchain.pem'"; then
  echo "==> Deployment complete: https://$COMPANY_KEY.$WMS_DOMAIN/"
else
  echo "==> Deployment complete (HTTP only): http://$COMPANY_KEY.$WMS_DOMAIN/"
  echo "    HTTPS 未启用：$COMPANY_KEY.$WMS_DOMAIN 尚未解析到 $REMOTE_HOST。"
  echo "    在 DNSPod 添加泛解析 A 记录：*.wms -> $REMOTE_HOST（再加一条 wms -> $REMOTE_HOST 做落地页），"
  echo "    等解析生效后重跑本命令即可自动签发证书并切换 HTTPS。"
fi
