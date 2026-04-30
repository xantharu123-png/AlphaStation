#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
BRANCH="${BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/system-health}"
SERVICES="${SERVICES:-tradingbot-api tradingbot-bg tradingbot-frontend}"
VENV_DIR="${VENV_DIR:-$APP_DIR/.venv}"

cd "$APP_DIR"

if [ -x "$VENV_DIR/bin/python" ]; then
  PYTHON="$VENV_DIR/bin/python"
elif [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[deploy] Existing venv at $VENV_DIR looks incomplete; recreating it."
  rm -rf "$VENV_DIR"
  python3 -m venv "$VENV_DIR"
  PYTHON="$VENV_DIR/bin/python"
else
  echo "[deploy] Creating Python venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  PYTHON="$VENV_DIR/bin/python"
fi

echo "[deploy] Python runtime: $PYTHON"

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

if [ -f requirements.txt ]; then
  echo "[deploy] Installing/updating Python dependencies in venv..."
  "$PYTHON" -m pip install --upgrade pip >/tmp/tradingbot-pip-upgrade.log
  "$PYTHON" -m pip install -r requirements.txt >/tmp/tradingbot-pip-install.log
fi

echo "[deploy] Compile check..."
"$PYTHON" -m py_compile api.py modules/new_listing_scanner.py modules/data_fetchers.py modules/scanners.py modules/scorers.py

if "$PYTHON" -m pytest --version >/dev/null 2>&1; then
  echo "[deploy] Pytest smoke checks..."
  "$PYTHON" -m pytest test_calendar_and_crypto_safety.py test_trading_logic.py -q
else
  echo "[deploy] pytest not available in venv; skipping pytest smoke checks."
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
