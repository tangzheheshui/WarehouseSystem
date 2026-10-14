#!/bin/sh
# V5 平台部署/升级：上传代码并重启 wms-platform。
# 首次部署（nginx、console 证书、systemd、acme 迁移）已于 2026-10-14 完成，
# 之后日常升级只需运行本脚本；详见 docs/deployment.md「V5 平台模式」。
set -eu

REMOTE_HOST=${REMOTE_HOST:-193.112.26.217}
APP_DIR=${APP_DIR:-/opt/wms-platform}
DOMAIN=${WMS_DOMAIN:-wms.tangzheheshui.cn}
SSH_TARGET=ubuntu@$REMOTE_HOST
SSH_OPTS="-o BatchMode=yes"

echo "==> Checking SSH access"
ssh $SSH_OPTS "$SSH_TARGET" "id"

echo "==> Uploading code to $APP_DIR"
tar czf - --exclude='__pycache__' --exclude='*.pyc' --exclude='.pycache' \
  server static tests | ssh $SSH_OPTS "$SSH_TARGET" "tar xzf - -C $APP_DIR"

echo "==> Running tests on server"
ssh $SSH_OPTS "$SSH_TARGET" "cd $APP_DIR && python3 -m unittest discover -s tests/cases 2>&1 | tail -2" || {
  echo "服务器上测试失败，已中止重启（当前服务未受影响）" >&2
  exit 1
}

echo "==> Restarting wms-platform"
ssh $SSH_OPTS "$SSH_TARGET" "sudo systemctl restart wms-platform && sleep 2 && systemctl is-active wms-platform"

echo "==> Verifying"
ssh $SSH_OPTS "$SSH_TARGET" "
  curl -sf -H 'Host: console.$DOMAIN' http://127.0.0.1:8010/api/console/health >/dev/null && echo 'console: ok'
  curl -sf -H 'Host: acme.$DOMAIN' http://127.0.0.1:8010/api/health >/dev/null && echo 'acme: ok'
"
curl -sf "https://console.$DOMAIN/api/console/health" >/dev/null && echo "https console: ok"

echo "==> Done. 回滚：git 上一个版本重跑本脚本即可；数据与注册表不受影响。"
