#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
BRANCH="${BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/system-health}"
SERVICES="${SERVICES:-tradingbot-api tradingbot-bg tradingbot-frontend}"

cd "$APP_DIR"

old_rev="$(git rev-parse --short HEAD)"
echo "[deploy] Current revision: $old_rev"

git fetch origin "$BRANCH"
new_rev="$(git rev-parse --short "origin/$BRANCH")"
echo "[deploy] Target revision:  $new_rev"

if [ "$old_rev" = "$new_rev" ]; then
  echo "[deploy] Already up to date."
else
  git pull --ff-only origin "$BRANCH"
fi

echo "[deploy] Compile check..."
python3 -m py_compile api.py modules/new_listing_scanner.py modules/data_fetchers.py modules/scanners.py modules/scorers.py

if command -v pytest >/dev/null 2>&1; then
  echo "[deploy] Pytest smoke checks..."
  python3 -m pytest test_calendar_and_crypto_safety.py test_trading_logic.py -q
else
  echo "[deploy] pytest not installed; skipping pytest smoke checks."
fi

echo "[deploy] Restarting services: $SERVICES"
for service in $SERVICES; do
  systemctl restart "$service"
done

echo "[deploy] Waiting for API health..."
for _ in $(seq 1 20); do
  if curl -fsS "$HEALTH_URL" >/tmp/tradingbot-health.json; then
    if grep -q '"status"[[:space:]]*:[[:space:]]*"critical"' /tmp/tradingbot-health.json; then
      echo "[deploy] API returned critical health:"
      cat /tmp/tradingbot-health.json
      exit 1
    fi
    echo "[deploy] Health OK"
    cat /tmp/tradingbot-health.json
    exit 0
  fi
  sleep 2
done

echo "[deploy] Health endpoint did not become ready."
for service in $SERVICES; do
  systemctl status "$service" --no-pager -l || true
done
exit 1
