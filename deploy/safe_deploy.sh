#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
BRANCH="${BRANCH:-main}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/system-health}"
SERVICES="${SERVICES:-tradingbot-api tradingbot-frontend}"
REQUESTED_VENV_DIR="${VENV_DIR:-}"
INSTALL_DEPS="${INSTALL_DEPS:-auto}"

cd "$APP_DIR"

detect_service_venv() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  for service in $SERVICES; do
    exec_start="$(systemctl show -p ExecStart --value "$service" 2>/dev/null || true)"
    runtime_path="$(printf '%s\n' "$exec_start" | grep -oE '/[^[:space:];]+/bin/(python[0-9.]*|uvicorn)' | head -n 1 || true)"
    if [ -n "$runtime_path" ]; then
      venv_candidate="$(dirname "$(dirname "$runtime_path")")"
      if [ -d "$venv_candidate" ]; then
        printf '%s\n' "$venv_candidate"
        return 0
      fi
    fi
  done
  return 1
}

if [ -n "$REQUESTED_VENV_DIR" ]; then
  VENV_DIR="$REQUESTED_VENV_DIR"
else
  detected_venv="$(detect_service_venv || true)"
  VENV_DIR="${detected_venv:-$APP_DIR/.venv}"
fi

if [ -x "$VENV_DIR/bin/python" ]; then
  PYTHON="$VENV_DIR/bin/python"
elif [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  if [ "$VENV_DIR" != "$APP_DIR/.venv" ]; then
    echo "[deploy] Existing venv at $VENV_DIR is incomplete; refusing to recreate a service venv."
    exit 1
  fi
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
  req_hash="$(sha256sum requirements.txt | awk '{print $1}')"
  req_hash_file="$VENV_DIR/.requirements.sha256"
  installed_hash="$(cat "$req_hash_file" 2>/dev/null || true)"
  if [ "$INSTALL_DEPS" = "always" ] || [ "$INSTALL_DEPS" = "1" ] || [ "$req_hash" != "$installed_hash" ]; then
    echo "[deploy] Installing/updating Python dependencies in venv..."
    "$PYTHON" -m pip install --disable-pip-version-check --upgrade pip >/tmp/tradingbot-pip-upgrade.log
    "$PYTHON" -m pip install --disable-pip-version-check -r requirements.txt >/tmp/tradingbot-pip-install.log
    printf '%s\n' "$req_hash" > "$req_hash_file"
  else
    echo "[deploy] Python dependencies already match requirements.txt."
  fi
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
