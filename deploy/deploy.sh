#!/bin/sh
# Builds the dashboard and installs it on a server (Ubuntu or Debian, with a sudo-capable SSH user).
#   sh deploy/deploy.sh            test, build and install the app, then restart it
#   sh deploy/deploy.sh config     install Caddy if needed, and this site's config
#   sh deploy/deploy.sh import     add the built-in publishers and import their reports
# Uses the SSH host in $RSSAC002_HOST (default: rssac002-vps from ~/.ssh/config) and the host
# name in $RSSAC002_DOMAIN (default: rssac002.jorrit.io).
set -eu
cd "$(dirname "$0")/.."
HOST="${RSSAC002_HOST:-rssac002-vps}"
DOMAIN="${RSSAC002_DOMAIN:-rssac002.jorrit.io}"

case "${1:-app}" in
config)
  sed "s/{\$RSSAC002_DOMAIN:rssac002.jorrit.io}/$DOMAIN/" deploy/Caddyfile | ssh "$HOST" 'cat > /tmp/rssac002.caddy'
  ssh "$HOST" 'sudo sh -s' <<'REMOTE'
set -eu
export DEBIAN_FRONTEND=noninteractive
if ! command -v caddy >/dev/null 2>&1; then
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -fsSL https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get -y -qq install caddy >/dev/null
fi
install -d -m 755 /etc/caddy/sites
if ! grep -q "^import /etc/caddy/sites/\*.caddy" /etc/caddy/Caddyfile 2>/dev/null; then
  printf '# Each site on this server has its own file in /etc/caddy/sites/.\nimport /etc/caddy/sites/*.caddy\n' > /etc/caddy/Caddyfile
fi
cp -p /etc/caddy/sites/rssac002.caddy /etc/caddy/sites/rssac002.caddy.previous 2>/dev/null || true
install -m 644 /tmp/rssac002.caddy /etc/caddy/sites/rssac002.caddy && rm /tmp/rssac002.caddy
# A config that does not validate is rolled back instead of being left for the next restart.
if ! out=$(caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile 2>&1); then
  echo "$out" | tail -5 >&2
  [ -f /etc/caddy/sites/rssac002.caddy.previous ] && mv /etc/caddy/sites/rssac002.caddy.previous /etc/caddy/sites/rssac002.caddy
  echo "The new Caddy config is invalid; the previous one is back in place." >&2
  exit 1
fi
rm -f /etc/caddy/sites/rssac002.caddy.previous
systemctl enable --quiet caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy
echo "Caddy config installed"
REMOTE
  ;;

import)
  ssh "$HOST" 'sudo -u rssac002 sh -s' <<'REMOTE'
set -eu
cd /var/lib/rssac002
export HOME=/var/lib/rssac002 RSSAC002_DB=/var/lib/rssac002/dashboard.sqlite3
/opt/rssac002/venv/bin/rssac002-dashboard import sidn-nl
/opt/rssac002/venv/bin/rssac002-dashboard import root-servers --since 2025-03-01 | grep -v "^  "
/opt/rssac002/venv/bin/rssac002-dashboard sources
REMOTE
  ;;

app)
  uv run pytest -q
  rm -rf dist && uv build --wheel -q
  wheel=$(ls dist/*.whl)
  scp -q "$wheel" deploy/rssac002.service "$HOST:/tmp/"
  ssh "$HOST" "sudo sh -s $(basename "$wheel")" <<'REMOTE'
set -eu
wheel="/tmp/$1"
export DEBIAN_FRONTEND=noninteractive
command -v git >/dev/null 2>&1 && python3 -c "import venv, ensurepip" 2>/dev/null || { apt-get update -qq && apt-get -y -qq install git python3-venv >/dev/null; }
id rssac002 >/dev/null 2>&1 || useradd --system --home-dir /var/lib/rssac002 --no-create-home --shell /usr/sbin/nologin rssac002
install -d -m 750 -o rssac002 -g rssac002 /var/lib/rssac002
install -d -m 755 /opt/rssac002 /etc/rssac002
if [ ! -f /etc/rssac002/env ]; then
  # Read-only on the web: without RSSAC002_ADMIN_PASSWORD_HASH, sources are managed from this server only.
  printf 'RSSAC002_DB=/var/lib/rssac002/dashboard.sqlite3\nRSSAC002_COOKIE_SECURE=true\nRSSAC002_SCAN_INTERVAL_HOURS=6\n' > /etc/rssac002/env
  chmod 640 /etc/rssac002/env && chgrp rssac002 /etc/rssac002/env
fi
# Caddy passes the visitor's address on; without this every visitor would share one request budget.
grep -q '^RSSAC002_TRUST_PROXY=' /etc/rssac002/env || echo 'RSSAC002_TRUST_PROXY=true' >> /etc/rssac002/env
[ -x /opt/rssac002/venv/bin/python ] || python3 -m venv /opt/rssac002/venv
/opt/rssac002/venv/bin/pip install -q --disable-pip-version-check "$wheel"
/opt/rssac002/venv/bin/pip install -q --disable-pip-version-check --force-reinstall --no-deps "$wheel"
install -m 644 /tmp/rssac002.service /etc/systemd/system/rssac002.service
rm -f "$wheel" /tmp/rssac002.service
systemctl daemon-reload
systemctl enable --quiet rssac002
systemctl restart rssac002
for _ in $(seq 1 30); do
  if curl -fsS --max-time 2 http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "deployed and healthy"
    exit 0
  fi
  sleep 0.5
done
echo "service did not become healthy" >&2
journalctl -u rssac002 -n 30 --no-pager >&2
exit 1
REMOTE
  echo "Deployed to https://$DOMAIN"
  ;;

*)
  echo "usage: deploy.sh [app|config|import]" >&2
  exit 1
  ;;
esac
