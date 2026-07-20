#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
ALLOW_LEGACY_FRONTENDS="${ALLOW_LEGACY_FRONTENDS:-0}"

fail() {
  echo "[edge] BLOCKED: $*" >&2
  exit 1
}

read_env_value() {
  local key="$1"
  awk -F= -v key="$key" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value = substr($0, index($0, "=") + 1)
    }
    END {
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^\"|\"$/, "", value)
      print value
    }
  ' "$ENV_FILE"
}

[ -s "$ENV_FILE" ] || fail "$ENV_FILE is missing or empty"
for command_name in systemctl nginx ss openssl curl awk grep; do
  command -v "$command_name" >/dev/null 2>&1 || fail "required command is missing: $command_name"
done

public_app_url="$(read_env_value PUBLIC_APP_URL)"
if [[ ! "$public_app_url" =~ ^https://([^/:]+)(/)?$ ]]; then
  fail "PUBLIC_APP_URL must be a bare HTTPS origin without a custom port or path"
fi
public_host="${BASH_REMATCH[1]}"
case "$public_host" in
  localhost|*.localhost|*.example|*.invalid|*.test) fail "PUBLIC_APP_URL uses a placeholder host" ;;
esac
if [[ "$public_host" =~ ^[0-9.]+$ ]] || [[ "$public_host" == *:* ]]; then
  fail "PUBLIC_APP_URL must use a DNS hostname, not an IP address"
fi

systemctl is-active --quiet nginx || fail "nginx is not active"
nginx -t >/tmp/alphastation-nginx-check.log 2>&1 || {
  cat /tmp/alphastation-nginx-check.log >&2
  fail "nginx configuration is invalid"
}

nginx_site="/etc/nginx/sites-enabled/tradingbot"
[ -e "$nginx_site" ] || fail "$nginx_site is not enabled"
grep -Fq "server_name $public_host;" "$nginx_site" || fail "nginx server_name does not match PUBLIC_APP_URL"
if grep -Fq '${DOMAIN}' "$nginx_site"; then
  fail "nginx configuration still contains the DOMAIN placeholder"
fi

cert_dir="/etc/letsencrypt/live/$public_host"
[ -s "$cert_dir/fullchain.pem" ] || fail "TLS certificate is missing for $public_host"
[ -s "$cert_dir/privkey.pem" ] || fail "TLS private key is missing for $public_host"
openssl x509 -checkend 604800 -noout -in "$cert_dir/fullchain.pem" >/dev/null \
  || fail "TLS certificate expires in less than seven days"

listeners="$(ss -H -ltn)"
if [ "$ALLOW_LEGACY_FRONTENDS" != "1" ]; then
  for legacy_service in tradingbot-frontend.service tradingbot.service; do
    if systemctl is-active --quiet "$legacy_service" 2>/dev/null; then
      fail "legacy service is still active: $legacy_service"
    fi
  done
  if printf '%s\n' "$listeners" | grep -Eq '(:3000|:8501)([[:space:]]|$)'; then
    fail "legacy frontend/Streamlit port 3000 or 8501 is still listening"
  fi
fi
if printf '%s\n' "$listeners" | grep -Eq '(^|[[:space:]])(0\.0\.0\.0|\*|\[::\]):8000([[:space:]]|$)'; then
  fail "FastAPI port 8000 is exposed publicly"
fi
printf '%s\n' "$listeners" | grep -Eq '127\.0\.0\.1:8000([[:space:]]|$)' \
  || fail "FastAPI is not listening on 127.0.0.1:8000"

for service in tradingbot-api.service tradingbot-bg.service; do
  systemctl is-active --quiet "$service" || fail "required service is not active: $service"
done

health_json="$(curl -fsS --connect-timeout 5 --max-time 15 \
  --resolve "$public_host:443:127.0.0.1" "https://$public_host/api/health")" \
  || fail "HTTPS health check failed"
printf '%s\n' "$health_json" | grep -Eq '"status"[[:space:]]*:[[:space:]]*"healthy"' \
  || fail "HTTPS health response is not healthy"

redirect_headers="$(curl -sSI --connect-timeout 5 --max-time 15 \
  --resolve "$public_host:80:127.0.0.1" "http://$public_host/")" \
  || fail "HTTP redirect check failed"
printf '%s\n' "$redirect_headers" | tr -d '\r' | grep -Eq '^HTTP/[^ ]+ (301|308)([[:space:]]|$)' \
  || fail "port 80 does not return a permanent redirect"
printf '%s\n' "$redirect_headers" | tr -d '\r' | grep -Eiq "^location:[[:space:]]*https://$public_host(/|$)" \
  || fail "port 80 does not redirect to the production HTTPS host"

echo "[edge] Commercial HTTPS edge OK: https://$public_host"
