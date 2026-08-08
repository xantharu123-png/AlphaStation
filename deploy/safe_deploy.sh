#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
BRANCH="${BRANCH:-main}"
EXPECTED_REVISION="${EXPECTED_REVISION:-}"
COMMERCIAL_DEPLOY="${COMMERCIAL_DEPLOY:-auto}"
# Zielarchitektur: tradingbot-api (FastAPI :8000) + tradingbot-bg
# (bg_service.py); das statische Frontend wird von nginx ausgeliefert.
# Solange ein alter tradingbot-frontend-Service aktiv ist, wird er aus
# Kompatibilitaetsgruenden mitgestartet und seine Auslieferung auf :3000
# bytegenau gegen die deployte index.html geprueft.
SERVICES="${SERVICES:-tradingbot-api tradingbot-bg}"
LEGACY_FRONTEND_ACTIVE=0
API_BIND_OVERRIDE_DIR="/etc/systemd/system/tradingbot-api.service.d"
API_BIND_OVERRIDE_FILE="$API_BIND_OVERRIDE_DIR/legacy-direct-frontend.conf"
if command -v systemctl >/dev/null 2>&1 \
  && systemctl is-active --quiet tradingbot-frontend.service 2>/dev/null; then
  LEGACY_FRONTEND_ACTIVE=1
  case " $SERVICES " in
    *" tradingbot-frontend "*) ;;
    *) SERVICES="$SERVICES tradingbot-frontend" ;;
  esac
fi
REQUESTED_VENV_DIR="${VENV_DIR:-}"
INSTALL_DEPS="${INSTALL_DEPS:-auto}"
SERVICE_VENV_DIR="$APP_DIR/venv"

cd "$APP_DIR"

if [ "$COMMERCIAL_DEPLOY" = "auto" ]; then
  strict_mode="$(grep -E '^COMMERCIAL_STRICT_MODE=' .env 2>/dev/null | tail -n 1 | cut -d= -f2- | tr '[:upper:]' '[:lower:]' || true)"
  case "$strict_mode" in
    1|true|yes|on) COMMERCIAL_DEPLOY="1" ;;
    *) COMMERCIAL_DEPLOY="0" ;;
  esac
fi
if [ -z "${HEALTH_URL:-}" ] && [ "$COMMERCIAL_DEPLOY" = "1" ]; then
  HEALTH_URL="http://127.0.0.1:8000/api/commercial-readiness"
else
  HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/health}"
fi

detect_service_venv() {
  if ! command -v systemctl >/dev/null 2>&1; then
    return 1
  fi
  for service in $SERVICES; do
    if [ "$service" = "tradingbot-frontend" ]; then
      continue
    fi
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

detected_venv="$(detect_service_venv || true)"
if [ -n "$REQUESTED_VENV_DIR" ] && [ "$REQUESTED_VENV_DIR" != "$SERVICE_VENV_DIR" ]; then
  echo "[deploy] VENV_DIR=$REQUESTED_VENV_DIR does not match the hardened service runtime $SERVICE_VENV_DIR."
  echo "[deploy] Refusing a split-brain deploy where tests and systemd use different Python environments."
  exit 1
fi
VENV_DIR="$SERVICE_VENV_DIR"

if [ -n "$detected_venv" ] && [ "$detected_venv" != "$VENV_DIR" ]; then
  echo "[deploy] Legacy service runtime detected at $detected_venv."
  echo "[deploy] Preparing the hardened runtime at $VENV_DIR before changing systemd units."
fi

if [ -x "$VENV_DIR/bin/python" ]; then
  PYTHON="$VENV_DIR/bin/python"
elif [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "[deploy] Existing service venv at $VENV_DIR is incomplete; refusing to overwrite it."
  exit 1
else
  echo "[deploy] Creating Python venv at $VENV_DIR"
  python3 -m venv "$VENV_DIR"
  PYTHON="$VENV_DIR/bin/python"
fi

echo "[deploy] Python runtime: $PYTHON"

ensure_runtime_dependencies() {
  if [ ! -f requirements.txt ]; then
    echo "[deploy] requirements.txt is missing."
    return 1
  fi
  req_hash="$(sha256sum requirements.txt | awk '{print $1}')"
  req_hash_file="$VENV_DIR/.requirements.sha256"
  installed_hash="$(cat "$req_hash_file" 2>/dev/null || true)"
  if [ "$INSTALL_DEPS" = "always" ] || [ "$INSTALL_DEPS" = "1" ] || [ "$req_hash" != "$installed_hash" ]; then
    echo "[deploy] Installing/updating Python dependencies in the hardened service venv..."
    "$PYTHON" -m pip install --disable-pip-version-check --upgrade pip >/tmp/tradingbot-pip-upgrade.log
    "$PYTHON" -m pip install --disable-pip-version-check -r requirements.txt >/tmp/tradingbot-pip-install.log
    printf '%s\n' "$req_hash" > "$req_hash_file"
  else
    echo "[deploy] Python dependencies already match requirements.txt."
  fi
}

run_source_checks() {
  check_dir="$1"
  check_label="$2"
  check_python="${3:-$PYTHON}"
  echo "[deploy] $check_label compile check..."
  (
    cd "$check_dir"
    PYTHON="$check_python"
    bash -n deploy/safe_deploy.sh deploy/install.sh deploy/verify_commercial_edge.sh
    "$PYTHON" -m compileall -q api.py bg_service.py modules
    "$PYTHON" scripts/verify_frontend_bundle.py

    if "$PYTHON" -m pytest --version >/dev/null 2>&1; then
      if [ "$COMMERCIAL_DEPLOY" = "1" ]; then
        echo "[deploy] $check_label full commercial pytest suite..."
        "$PYTHON" -m pytest -q
      else
        echo "[deploy] $check_label pytest smoke checks..."
        "$PYTHON" -m pytest \
          test_calendar_and_crypto_safety.py \
          test_trading_logic.py \
          test_commerce_hardening.py \
          test_email_alert_config.py \
          -q
      fi
    elif [ "$COMMERCIAL_DEPLOY" = "1" ]; then
      echo "[deploy] pytest is required for a commercial deploy."
      exit 1
    else
      echo "[deploy] pytest not available in venv; skipping non-commercial smoke checks."
    fi
  )
}

frontend_bundle_revision() {
  local bundle_path="${1:-$APP_DIR/frontend/app.bundle.js}"
  "$PYTHON" - "$bundle_path" <<'PY'
import re
import sys
from pathlib import Path

path = Path(sys.argv[1])
try:
    head = path.read_text(encoding="utf-8")[:512]
except OSError as exc:
    raise SystemExit(f"cannot read frontend bundle metadata: {exc}")
match = re.search(r"app-source-sha256:\s*([0-9a-f]{64})", head)
if not match:
    raise SystemExit("frontend bundle source hash is missing")
print(match.group(1)[:12])
PY
}

verify_runtime_revision() {
  local health_file="$1"
  local expected_revision="$2"
  local expected_bundle="$3"
  "$PYTHON" - "$health_file" "$expected_revision" "$expected_bundle" <<'PY'
import json
import sys
from pathlib import Path

health_file = Path(sys.argv[1])
expected_revision = sys.argv[2].strip().lower()
expected_bundle = sys.argv[3].strip().lower()
try:
    payload = json.loads(health_file.read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"cannot parse API build health: {exc}")

actual_revision = str(payload.get("revision") or "").strip().lower()
actual_bundle = str(payload.get("frontend_bundle") or "").strip().lower()
errors = []
if actual_revision != expected_revision:
    errors.append(f"API revision {actual_revision or 'missing'} != {expected_revision}")
if actual_bundle != expected_bundle:
    errors.append(f"API frontend bundle {actual_bundle or 'missing'} != {expected_bundle}")
if errors:
    raise SystemExit("; ".join(errors))
print(f"[deploy] Runtime build matches target: {actual_revision}, frontend {actual_bundle}")
PY
}

read_env_value() {
  local key="$1"
  if [ ! -f "$APP_DIR/.env" ]; then
    return 0
  fi
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value = substr($0, index($0, "=") + 1)
    }
    END {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^\"|\"$/, "", value)
      print value
    }
  ' "$APP_DIR/.env" 2>/dev/null
}

verify_commercial_edge() {
  if [ "$COMMERCIAL_DEPLOY" != "1" ]; then
    return 0
  fi
  echo "[deploy] Verifying commercial HTTPS edge and closed legacy ports..."
  APP_DIR="$APP_DIR" ENV_FILE="$APP_DIR/.env" bash "$APP_DIR/deploy/verify_commercial_edge.sh"
}

verify_frontend_delivery() {
  local served_dir served_index served_bundle served_boot public_app_url public_host public_port
  served_dir="$(mktemp -d /tmp/alphastation-served-frontend.XXXXXX)"
  served_index="$served_dir/index.html"
  served_bundle="$served_dir/app.bundle.js"
  served_boot="$served_dir/boot.js"

  if [ "$LEGACY_FRONTEND_ACTIVE" = "1" ]; then
    if ! curl -fsS --connect-timeout 5 --max-time 20 "http://127.0.0.1:3000/" -o "$served_index" \
      || ! curl -fsS --connect-timeout 5 --max-time 20 "http://127.0.0.1:3000/app.bundle.js" -o "$served_bundle" \
      || ! curl -fsS --connect-timeout 5 --max-time 20 "http://127.0.0.1:3000/boot.js" -o "$served_boot"; then
      rm -rf -- "$served_dir"
      echo "[deploy] Legacy frontend on :3000 is not fully reachable after restart."
      return 1
    fi
  elif command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet nginx 2>/dev/null; then
    public_app_url="$(read_env_value PUBLIC_APP_URL)"
    public_app_url="${public_app_url%/}"
    if [[ "$public_app_url" =~ ^https://([^/:]+)(/)?$ ]]; then
      public_host="${BASH_REMATCH[1]}"
      if ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:443:127.0.0.1" "$public_app_url/" -o "$served_index" \
        || ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:443:127.0.0.1" "$public_app_url/app.bundle.js" -o "$served_bundle" \
        || ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:443:127.0.0.1" "$public_app_url/boot.js" -o "$served_boot"; then
        rm -rf -- "$served_dir"
        echo "[deploy] nginx frontend is not fully reachable at $public_app_url."
        return 1
      fi
    elif [[ "$public_app_url" =~ ^http://([^/:]+)(:([0-9]+))?(/)?$ ]]; then
      public_host="${BASH_REMATCH[1]}"
      public_port="${BASH_REMATCH[3]:-80}"
      if ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:$public_port:127.0.0.1" "$public_app_url/" -o "$served_index" \
        || ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:$public_port:127.0.0.1" "$public_app_url/app.bundle.js" -o "$served_bundle" \
        || ! curl -fsS --connect-timeout 5 --max-time 20 --resolve "$public_host:$public_port:127.0.0.1" "$public_app_url/boot.js" -o "$served_boot"; then
        rm -rf -- "$served_dir"
        echo "[deploy] nginx frontend is not fully reachable at $public_app_url."
        return 1
      fi
    else
      rm -rf -- "$served_dir"
      echo "[deploy] Cannot verify nginx frontend: PUBLIC_APP_URL is missing or invalid."
      return 1
    fi
  else
    rm -rf -- "$served_dir"
    echo "[deploy] No active frontend delivery service found."
    return 1
  fi

  if ! cmp -s "$APP_DIR/frontend/index.html" "$served_index" \
    || ! cmp -s "$APP_DIR/frontend/app.bundle.js" "$served_bundle" \
    || ! cmp -s "$APP_DIR/frontend/boot.js" "$served_boot"; then
    rm -rf -- "$served_dir"
    echo "[deploy] Served frontend files do not match the deployed revision."
    return 1
  fi
  rm -rf -- "$served_dir"
  echo "[deploy] Served index, app bundle and boot script match the deployed revision."
}

configure_api_bind_mode() {
  if [ "$LEGACY_FRONTEND_ACTIVE" = "1" ] && [ "$COMMERCIAL_DEPLOY" != "1" ]; then
    install -d -m 0755 "$API_BIND_OVERRIDE_DIR"
    printf '%s\n' \
      '[Service]' \
      'Environment="API_BIND_HOST=0.0.0.0"' \
      'ExecStart=' \
      'ExecStart=/home/tradingbot/app/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000' \
      > "$API_BIND_OVERRIDE_FILE"
    chmod 0644 "$API_BIND_OVERRIDE_FILE"
    echo "[deploy] Compatibility mode: legacy frontend :3000 -> public API :8000."
    return 0
  fi

  rm -f -- "$API_BIND_OVERRIDE_FILE"
  echo "[deploy] Hardened API bind: localhost :8000 behind nginx."
}

sync_service_units() {
  for unit in tradingbot-api.service tradingbot-bg.service; do
    if [ -f "$APP_DIR/deploy/$unit" ]; then
      install -m 0644 "$APP_DIR/deploy/$unit" "/etc/systemd/system/$unit"
    fi
  done
  configure_api_bind_mode
  systemctl daemon-reload
}

restart_service_bounded() {
  local service="$1"
  echo "[deploy] Restarting $service ..."
  if timeout --signal=TERM --kill-after=5s 45s systemctl restart "$service"; then
    return 0
  fi
  echo "[deploy] Restart of $service exceeded 45s or failed."
  systemctl status "$service" --no-pager -l || true
  return 1
}

prepare_runtime_state() {
  if ! id tradingbot >/dev/null 2>&1; then
    echo "[deploy] Required service account 'tradingbot' does not exist. Run deploy/install.sh first."
    return 1
  fi

  install -d -m 0750 -o tradingbot -g tradingbot "$APP_DIR/data_cache"
  install -d -m 0700 -o tradingbot -g tradingbot "$APP_DIR/data_cache/auth"
  install -d -m 0700 -o tradingbot -g tradingbot "$APP_DIR/data_cache/runtime"

  # Old root-run services may have created mutable state as root. Only runtime
  # data is migrated; source code and deployment files remain root-controlled.
  chown -R tradingbot:tradingbot "$APP_DIR/data_cache"

  # Preserve user-created reminders and cross-process mail dedupe state during
  # the one-time migration from the global /tmp into PrivateTmp/BindPaths.
  for state_file in alphastation_trade_reminders.json alphastation_email_dedupe.json; do
    if [ -f "/tmp/$state_file" ] && [ ! -f "$APP_DIR/data_cache/runtime/$state_file" ]; then
      install -m 0600 -o tradingbot -g tradingbot "/tmp/$state_file" "$APP_DIR/data_cache/runtime/$state_file"
    fi
  done
}

# A newly created app venv must be complete before target preflight and before
# hardened units can replace a legacy /root/venv service definition.
ensure_runtime_dependencies

old_rev="$(git rev-parse --short=12 HEAD)"
old_rev_full="$(git rev-parse HEAD)"
echo "[deploy] Current revision: $old_rev"

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "[deploy] Tracked worktree changes detected; refusing an update that could overwrite server edits."
  exit 1
fi

deployment_changed=0
rollback_in_progress=0

rollback_deployment() {
  if [ "$deployment_changed" != "1" ] || [ "$rollback_in_progress" = "1" ]; then
    return 0
  fi
  rollback_in_progress=1
  trap - ERR
  set +e
  echo "[deploy] FAILURE: rolling back to $old_rev_full ..."

  git reset --hard "$old_rev_full"
  rollback_status=$?
  if [ "$rollback_status" -eq 0 ] && [ -f requirements.txt ]; then
    "$PYTHON" -m pip install --disable-pip-version-check -r requirements.txt \
      >/tmp/tradingbot-pip-rollback.log
    rollback_status=$?
    if [ "$rollback_status" -eq 0 ]; then
      sha256sum requirements.txt | awk '{print $1}' > "$VENV_DIR/.requirements.sha256"
    fi
  fi
  if [ "$rollback_status" -eq 0 ]; then
    sync_service_units
    rollback_status=$?
  fi
  if [ "$rollback_status" -eq 0 ]; then
    for service in $SERVICES; do
      restart_service_bounded "$service" || rollback_status=$?
    done
  fi

  if [ "$rollback_status" -eq 0 ]; then
    rollback_rev="$(git rev-parse --short=12 HEAD)"
    rollback_bundle="$(frontend_bundle_revision "$APP_DIR/frontend/app.bundle.js")"
    rollback_status=$?
  fi

  if [ "$rollback_status" -eq 0 ]; then
    for _ in $(seq 1 20); do
      if curl -fsS "http://127.0.0.1:8000/api/health" >/tmp/tradingbot-rollback-health.json \
        && verify_runtime_revision /tmp/tradingbot-rollback-health.json "$rollback_rev" "$rollback_bundle" \
        && verify_frontend_delivery \
        && verify_commercial_edge; then
        echo "[deploy] Rollback health OK; previous revision restored."
        cat /tmp/tradingbot-rollback-health.json
        set -e
        return 0
      fi
      sleep 2
    done
    rollback_status=1
  fi

  echo "[deploy] CRITICAL: automatic rollback failed. Inspect services immediately."
  for service in $SERVICES; do
    systemctl status "$service" --no-pager -l || true
  done
  set -e
  return "$rollback_status"
}

handle_deploy_error() {
  rc=$?
  trap - ERR
  if [ "$deployment_changed" = "1" ]; then
    rollback_deployment || true
  fi
  exit "$rc"
}

git fetch origin "$BRANCH"
new_rev_full="$(git rev-parse "origin/$BRANCH")"
new_rev="$(git rev-parse --short=12 "origin/$BRANCH")"
echo "[deploy] Target revision:  $new_rev"

if [ -n "$EXPECTED_REVISION" ]; then
  expected_revision="$(printf '%s' "$EXPECTED_REVISION" | tr '[:upper:]' '[:lower:]')"
  if [[ ! "$expected_revision" =~ ^[0-9a-f]{12,40}$ ]]; then
    echo "[deploy] EXPECTED_REVISION is not a valid Git revision: $EXPECTED_REVISION"
    exit 1
  fi
  if [ "${new_rev_full:0:${#expected_revision}}" != "$expected_revision" ]; then
    echo "[deploy] Fetched target $new_rev does not match expected revision ${expected_revision:0:12}."
    exit 1
  fi
  echo "[deploy] Auto-update target binding verified: ${expected_revision:0:12}"
fi

if [ "$old_rev" = "$new_rev" ]; then
  echo "[deploy] Already up to date."
else
  preflight_dir="$(mktemp -d /tmp/alphastation-preflight.XXXXXX)"
  cleanup_preflight() {
    case "$preflight_dir" in
      /tmp/alphastation-preflight.*) rm -rf -- "$preflight_dir" ;;
      *) echo "[deploy] Refusing unsafe preflight cleanup path: $preflight_dir" ;;
    esac
  }
  trap cleanup_preflight EXIT
  echo "[deploy] Exporting target revision for preflight..."
  git archive "origin/$BRANCH" | tar -x -C "$preflight_dir"
  preflight_python="$PYTHON"
  if [ -f "$preflight_dir/requirements.txt" ] && ! cmp -s requirements.txt "$preflight_dir/requirements.txt"; then
    echo "[deploy] Target dependencies changed; creating an isolated preflight venv..."
    python3 -m venv "$preflight_dir/.venv"
    preflight_python="$preflight_dir/.venv/bin/python"
    "$preflight_python" -m pip install --disable-pip-version-check --upgrade pip >/tmp/tradingbot-preflight-pip-upgrade.log
    "$preflight_python" -m pip install --disable-pip-version-check -r "$preflight_dir/requirements.txt" >/tmp/tradingbot-preflight-pip-install.log
  fi
  run_source_checks "$preflight_dir" "Target revision" "$preflight_python"
  cleanup_preflight
  trap - EXIT

  if [ "$(git rev-parse HEAD)" != "$old_rev_full" ]; then
    echo "[deploy] Local revision changed during preflight; refusing deploy."
    exit 1
  fi
  git pull --ff-only origin "$BRANCH"
  if [ "$(git rev-parse HEAD)" != "$old_rev_full" ]; then
    deployment_changed=1
    trap handle_deploy_error ERR
  fi
fi

active_rev_full="$(git rev-parse HEAD)"
active_rev="$(git rev-parse --short=12 HEAD)"
if [ "$active_rev_full" != "$new_rev_full" ]; then
  echo "[deploy] Checked-out revision $active_rev does not match fetched target $new_rev."
  false
fi
active_bundle="$(frontend_bundle_revision "$APP_DIR/frontend/app.bundle.js")"
echo "[deploy] Deploy identity: revision $active_rev, frontend $active_bundle"

ensure_runtime_dependencies

run_source_checks "$APP_DIR" "Deployed revision"

# A paid deploy must already have a working TLS edge. This check happens
# before service changes so a broken nginx/certificate/port migration cannot
# turn an otherwise healthy revision into an outage.
verify_commercial_edge

echo "[deploy] Preparing service-owned persistent and runtime state..."
prepare_runtime_state

echo "[deploy] Synchronizing hardened systemd units..."
sync_service_units

echo "[deploy] Restarting services: $SERVICES"
for service in $SERVICES; do
  restart_service_bounded "$service"
done

echo "[deploy] Waiting for API health..."
for _ in $(seq 1 20); do
  if curl -fsS "$HEALTH_URL" >/tmp/tradingbot-health.json; then
    if grep -Eq '"status"[[:space:]]*:[[:space:]]*"(critical|blocked)"' /tmp/tradingbot-health.json; then
      echo "[deploy] API returned blocking health/readiness:"
      cat /tmp/tradingbot-health.json
      false
    fi
    if [ "$COMMERCIAL_DEPLOY" = "1" ] && ! grep -q '"commercial_ready"[[:space:]]*:[[:space:]]*true' /tmp/tradingbot-health.json; then
      echo "[deploy] Commercial readiness failed:"
      cat /tmp/tradingbot-health.json
      false
    fi
    if ! curl -fsS "http://127.0.0.1:8000/api/health" >/tmp/tradingbot-build-health.json; then
      sleep 2
      continue
    fi
    if ! verify_runtime_revision /tmp/tradingbot-build-health.json "$active_rev" "$active_bundle"; then
      echo "[deploy] API is reachable but still serves a different build; waiting..."
      sleep 2
      continue
    fi
    echo "[deploy] Health OK"
    cat /tmp/tradingbot-health.json
    if [ "$HEALTH_URL" != "http://127.0.0.1:8000/api/health" ]; then
      cat /tmp/tradingbot-build-health.json
    fi
    verify_frontend_delivery
    verify_commercial_edge
    trap - ERR
    exit 0
  fi
  sleep 2
done

echo "[deploy] Health endpoint did not become ready."
for service in $SERVICES; do
  systemctl status "$service" --no-pager -l || true
done
false
