#!/usr/bin/env bash
# health_check.sh — Alpha Station Gesundheitscheck (Ampel-Ausgabe, ein Befehl)
# Aufruf:  bash /home/tradingbot/app/deploy/health_check.sh
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

APP="${APP_DIR:-/home/tradingbot/app}"
AUTO_UPDATE_CRON_FILE="${AUTO_UPDATE_CRON_FILE:-/etc/cron.d/alpha-station-auto-update}"
AUTO_UPDATE_LOG="${AUTO_UPDATE_LOG:-/var/log/alpha_autoupdate.log}"
AUTO_UPDATE_LAUNCHER="${AUTO_UPDATE_LAUNCHER:-/usr/local/sbin/alpha-station-auto-update}"
TRUST_STAT_BIN="${TRUST_STAT_BIN:-/usr/bin/stat}"
CMP_BIN="${CMP_BIN:-/usr/bin/cmp}"
GIT_BIN="${GIT_BIN:-git}"
CURL_BIN="${CURL_BIN:-/usr/bin/curl}"
PYTHON_BIN="${PYTHON_BIN:-/usr/bin/python3}"
SYSTEMCTL_BIN="${SYSTEMCTL_BIN:-/usr/bin/systemctl}"
ok=0; warn=0; fail=0

green()  { printf '  \033[32mOK\033[0m     %s\n' "$1"; ok=$((ok+1)); }
yellow() { printf '  \033[33mWARN\033[0m   %s\n' "$1"; warn=$((warn+1)); }
red()    { printf '  \033[31mFEHLER\033[0m %s\n' "$1"; fail=$((fail+1)); }

finish_check() {
  echo
  echo "==============================================================="
  echo " Ergebnis: $ok OK | $warn Warnung(en) | $fail Fehler"
  echo "==============================================================="
  if [ "$fail" -gt 0 ]; then
    echo " → Rot zuerst beheben. Anleitung: $APP/deploy/SERVER_WARTUNG.md"
    return 1
  fi
  if [ "$warn" -gt 0 ]; then
    echo " → Laeuft. Gelb = pruefen, meist harmlos (Wochenende/Nacht)."
    return 0
  fi
  echo " → Alles gruen."
  return 0
}

git_app() {
  "$GIT_BIN" -c "safe.directory=$APP" -C "$APP" "$@"
}

frontend_bundle_revision() {
  "$PYTHON_BIN" - "$APP/frontend/app.bundle.js" <<'PY'
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

check_runtime_build() {
  local expected_revision="" expected_bundle="" health_json="" verification=""
  if [ ! -d "$APP/.git" ]; then
    red "API-Build nicht pruefbar: Git-Checkout fehlt"
    return
  fi
  if [ ! -x "$PYTHON_BIN" ]; then
    red "API-Build nicht pruefbar: Python fehlt ($PYTHON_BIN)"
    return
  fi
  expected_revision=$(git_app rev-parse --short=12 HEAD 2>/dev/null || true)
  if [[ ! "$expected_revision" =~ ^[0-9a-fA-F]{12}$ ]]; then
    red "API-Build nicht pruefbar: Checkout-Revision fehlt"
    return
  fi
  expected_revision=$(printf '%s' "$expected_revision" | tr '[:upper:]' '[:lower:]')
  expected_bundle=$(frontend_bundle_revision 2>/dev/null || true)
  if [[ ! "$expected_bundle" =~ ^[0-9a-f]{12}$ ]]; then
    red "API-Build nicht pruefbar: versionierte Frontend-Bundle-ID fehlt"
    return
  fi
  if ! health_json=$("$CURL_BIN" -fsS --max-time 5 \
      http://127.0.0.1:8000/api/health 2>/dev/null); then
    red "API antwortet nicht gesund auf /api/health  →  journalctl -u tradingbot-api -n 50"
    return
  fi

  if verification=$("$PYTHON_BIN" -c '
import json
import sys

try:
    payload = json.loads(sys.argv[1])
except (TypeError, json.JSONDecodeError) as exc:
    raise SystemExit(f"invalid health JSON: {exc}")
expected_revision = sys.argv[2]
expected_bundle = sys.argv[3]
actual_status = str(payload.get("status") or "").strip().lower()
actual_revision = str(payload.get("revision") or "").strip().lower()
actual_bundle = str(payload.get("frontend_bundle") or "").strip().lower()
errors = []
if actual_status != "healthy":
    errors.append("status=" + (actual_status or "missing"))
if actual_revision != expected_revision:
    errors.append("revision=" + (actual_revision or "missing") + " expected=" + expected_revision)
if actual_bundle != expected_bundle:
    errors.append("frontend_bundle=" + (actual_bundle or "missing") + " expected=" + expected_bundle)
if errors:
    raise SystemExit("; ".join(errors))
' "$health_json" "$expected_revision" "$expected_bundle" 2>&1); then
    green "API-Revision und Frontend-Bundle stimmen exakt ($expected_revision / $expected_bundle)"
  else
    red "API-Build stimmt nicht mit dem Checkout ueberein: $verification"
  fi
}

query_health_unit_state() {
  local unit="$1" load_state="" active_state="" unit_file_state=""

  HEALTH_UNIT_LOAD_STATE=""
  HEALTH_UNIT_ACTIVE_STATE=""
  HEALTH_UNIT_FILE_STATE=""
  if ! load_state=$("$SYSTEMCTL_BIN" show \
      --property=LoadState --value "$unit" 2>/dev/null); then
    return 1
  fi
  if ! active_state=$("$SYSTEMCTL_BIN" show \
      --property=ActiveState --value "$unit" 2>/dev/null); then
    return 1
  fi
  if ! unit_file_state=$("$SYSTEMCTL_BIN" show \
      --property=UnitFileState --value "$unit" 2>/dev/null); then
    return 1
  fi
  HEALTH_UNIT_LOAD_STATE="${load_state%$'\r'}"
  HEALTH_UNIT_ACTIVE_STATE="${active_state%$'\r'}"
  HEALTH_UNIT_FILE_STATE="${unit_file_state%$'\r'}"
}

check_core_services() {
  local svc

  for svc in tradingbot-api tradingbot-bg; do
    if query_health_unit_state "$svc" \
      && [ "$HEALTH_UNIT_LOAD_STATE" = "loaded" ] \
      && [ "$HEALTH_UNIT_ACTIVE_STATE" = "active" ] \
      && [ "$HEALTH_UNIT_FILE_STATE" = "enabled" ]; then
      green "$svc laeuft"
      green "$svc startet nach Reboot automatisch"
    else
      red "$svc ist nicht sicher loaded/active/enabled (LoadState=${HEALTH_UNIT_LOAD_STATE:-query-failed}, ActiveState=${HEALTH_UNIT_ACTIVE_STATE:-query-failed}, UnitFileState=${HEALTH_UNIT_FILE_STATE:-query-failed})"
    fi
  done
}

check_auto_update() {
  local expected_cron="*/10 * * * * root /bin/bash $AUTO_UPDATE_LAUNCHER >> $AUTO_UPDATE_LOG 2>&1"
  local actual_cron="" age=0 modified=0 last_log_line=""
  local local_rev="" remote_rev="" behind="" launcher_owner="" launcher_mode=""
  local launcher_metadata="" log_action="" log_revision="" log_terminal_fresh=0
  local cron_load_state="" cron_active_state="" cron_unit_file_state=""
  local cron_load_query_ok=0 cron_active_query_ok=0 cron_enabled_query_ok=0

  if [ -f "$AUTO_UPDATE_CRON_FILE" ]; then
    actual_cron=$(grep -Ev '^[[:space:]]*(#|$)' "$AUTO_UPDATE_CRON_FILE" 2>/dev/null || true)
  fi
  if [ "$actual_cron" = "$expected_cron" ]; then
    green "Cron-Aufruf exakt: alle 10 min via root /bin/bash"
  elif [ -z "$actual_cron" ]; then
    red "Auto-Update-Cron fehlt  →  sudo /bin/bash $APP/deploy/install_auto_update.sh"
  else
    red "Cron-Aufruf weicht vom sicheren /bin/bash-Vertrag ab  →  sudo /bin/bash $APP/deploy/install_auto_update.sh"
  fi

  if cron_load_state=$("$SYSTEMCTL_BIN" show --property=LoadState --value cron.service 2>/dev/null); then
    cron_load_query_ok=1
  fi
  if cron_active_state=$("$SYSTEMCTL_BIN" show --property=ActiveState --value cron.service 2>/dev/null); then
    cron_active_query_ok=1
  fi
  if cron_unit_file_state=$("$SYSTEMCTL_BIN" show --property=UnitFileState --value cron.service 2>/dev/null); then
    cron_enabled_query_ok=1
  fi
  if [ "$cron_load_query_ok" -eq 1 ] && [ "$cron_active_query_ok" -eq 1 ] \
    && [ "$cron_enabled_query_ok" -eq 1 ] && [ "$cron_load_state" = "loaded" ] \
    && [ "$cron_active_state" = "active" ] && [ "$cron_unit_file_state" = "enabled" ]; then
    green "cron.service ist aktiv und fuer Autostart aktiviert"
  else
    if [ "$cron_load_query_ok" -ne 1 ]; then
      red "cron.service LoadState konnte nicht sicher abgefragt werden"
    elif [ "$cron_load_state" != "loaded" ]; then
      red "cron.service ist nicht geladen (LoadState=${cron_load_state:-missing})"
    fi
    if [ "$cron_active_query_ok" -ne 1 ]; then
      red "cron.service ActiveState konnte nicht sicher abgefragt werden"
    elif [ "$cron_active_state" != "active" ]; then
      red "cron.service ist nicht aktiv (ActiveState=${cron_active_state:-missing})"
    fi
    if [ "$cron_enabled_query_ok" -ne 1 ]; then
      red "cron.service Enablement konnte nicht sicher abgefragt werden"
    elif [ "$cron_unit_file_state" != "enabled" ]; then
      red "cron.service ist nicht fuer Autostart aktiviert (UnitFileState=${cron_unit_file_state:-missing})"
    fi
  fi

  if [ -x "$AUTO_UPDATE_LAUNCHER" ] && [ ! -L "$AUTO_UPDATE_LAUNCHER" ]; then
    launcher_metadata=$("$TRUST_STAT_BIN" -c '%u %a' -- "$AUTO_UPDATE_LAUNCHER" 2>/dev/null || true)
    read -r launcher_owner launcher_mode <<< "$launcher_metadata"
  fi
  if [ "$launcher_owner" = "0" ] && [[ "$launcher_mode" =~ ^[0-7]+$ ]] \
    && (( (8#$launcher_mode & 8#22) == 0 )); then
    green "Root-Launcher ist ausfuehrbar und nicht service-schreibbar"
    if [ -f "$APP/deploy/auto_update.sh" ] \
      && "$CMP_BIN" -s "$APP/deploy/auto_update.sh" "$AUTO_UPDATE_LAUNCHER"; then
      green "Root-Launcher entspricht dem versionierten Updater"
    else
      red "Root-Launcher weicht vom versionierten Updater ab  →  sudo /bin/bash $APP/deploy/install_auto_update.sh"
    fi
  else
    red "Root-Launcher fehlt/ist unsicher  →  sudo /bin/bash $APP/deploy/install_auto_update.sh"
  fi

  if [ ! -e "$AUTO_UPDATE_LOG" ]; then
    red "Auto-Update-Log fehlt: $AUTO_UPDATE_LOG"
  elif [ ! -s "$AUTO_UPDATE_LOG" ]; then
    modified=$(date -r "$AUTO_UPDATE_LOG" +%s 2>/dev/null || echo 0)
    age=$(( ( $(date +%s) - modified ) / 60 ))
    if [ "$age" -le 15 ]; then
      yellow "Auto-Update-Log wartet auf den ersten Cron-Takt (${age} min seit Einrichtung)"
    else
      red "Auto-Update-Log seit ${age} min leer — Cron wurde nicht erfolgreich gestartet"
    fi
  else
    last_log_line=$(tail -n 1 "$AUTO_UPDATE_LOG" 2>/dev/null | tr -d '\r' || true)
    modified=$(date -r "$AUTO_UPDATE_LOG" +%s 2>/dev/null || echo 0)
    age=$(( ( $(date +%s) - modified ) / 60 ))
    if printf '%s\n' "$last_log_line" | grep -qiE 'permission denied|command not found|no such file|status=error|fatal:|traceback'; then
      red "Auto-Update-Log enthaelt einen Ausfuehrungsfehler (z.B. Permission denied)"
    else
      log_action=$(printf '%s\n' "$last_log_line" \
        | sed -nE 's/^.*alpha-auto-update status=ok action=(current|probe|deploy)([[:space:]].*)$/\1/p')
      log_revision=$(printf '%s\n' "$last_log_line" \
        | sed -nE 's/^.*alpha-auto-update status=ok action=(current|probe|deploy)[[:space:]]+.*revision=([0-9a-fA-F]{12,40})([[:space:]].*)?$/\2/p')
      log_revision=$(printf '%s' "$log_revision" | tr '[:upper:]' '[:lower:]')
      if [ -z "$log_action" ] || [ -z "$log_revision" ]; then
        red "Auto-Update-Log: kein terminaler erfolgreicher Status (current|probe|deploy mit revision)"
      elif [ "$age" -le 20 ]; then
        log_terminal_fresh=1
      else
        red "Auto-Update-Log ist ${age} min alt (Cron-Takt: 10 min)"
      fi
    fi
  fi

  if [ ! -d "$APP/.git" ]; then
    red "Git-Checkout fehlt; Auto-Update kann keinen Stand vergleichen"
  else
    local_rev=$(git_app rev-parse HEAD 2>/dev/null || true)
    local_rev=$(printf '%s' "$local_rev" | tr '[:upper:]' '[:lower:]')
    if [ "$log_terminal_fresh" -eq 1 ]; then
      if [[ "$local_rev" =~ ^[0-9a-f]{40}$ ]] \
        && [ "${local_rev:0:${#log_revision}}" = "$log_revision" ]; then
        green "Auto-Update-Log endet terminal erfolgreich ($log_action, revision ${log_revision:0:12}, ${age} min)"
      else
        red "Auto-Update-Log-Revision passt nicht zum aktiven Checkout"
      fi
    fi

    if ! git_app fetch origin main --quiet 2>/dev/null; then
      yellow "origin/main nicht abrufbar; Git-Stand derzeit nicht live vergleichbar"
    else
      remote_rev=$(git_app rev-parse origin/main 2>/dev/null || true)
      if [ -n "$local_rev" ] && [ "$local_rev" = "$remote_rev" ]; then
        green "Code ist aktuell (HEAD = origin/main, ${local_rev:0:12})"
      elif [ -n "$local_rev" ] && [ -n "$remote_rev" ] \
        && git_app merge-base --is-ancestor "$local_rev" "$remote_rev" 2>/dev/null; then
        behind=$(git_app rev-list "$local_rev..$remote_rev" --count 2>/dev/null || echo '?')
        red "Server hinkt ${behind} Commit(s) hinterher — Auto-Update-Log pruefen"
      else
        red "Server-HEAD und origin/main sind nicht sicher per Fast-Forward vergleichbar"
      fi
    fi
  fi
}

dow=$(date +%u)   # 6=Samstag, 7=Sonntag

echo "==============================================================="

if [ "${1:-}" = "--auto-update-only" ]; then
  echo
  echo "[6] Auto-Update"
  check_auto_update
  finish_check
  exit $?
fi
if [ "${1:-}" = "--runtime-build-only" ]; then
  echo
  echo "[2] API-/Build-Identitaet"
  check_runtime_build
  finish_check
  exit $?
fi
echo " Alpha Station Gesundheitscheck — $(date '+%Y-%m-%d %H:%M %Z')"
echo "==============================================================="

echo
echo "[1] Dienste"
check_core_services

echo
echo "[2] API-/Build-Identitaet"
check_runtime_build
errs=$(journalctl -u tradingbot-api -u tradingbot-bg --since '2 hours ago' --no-pager 2>/dev/null | grep -ciE 'traceback|CRITICAL' || true)
if [ "${errs:-0}" -eq 0 ]; then
  green "Keine Abstuerze in den letzten 2h"
else
  red "$errs Traceback(s) in 2h  →  journalctl -u tradingbot-api -u tradingbot-bg --since '2 hours ago'"
fi

echo
echo "[3] Scanner-Takt"
# Aktien-Scheduler tickt nur Mo-Fr im Fenster 10:00-16:05 ET; ausserhalb ist
# Stillstand Design (Handbuch 3.10). Crypto laeuft 24/7 -> Nachweis ueber [4].
ticks=$(journalctl -u tradingbot-api --since '2 hours ago' --no-pager 2>/dev/null | grep -c '\[Scheduler\]' || true)
et_hm=$((10#$(TZ=America/New_York date +%H%M)))
et_dow=$(TZ=America/New_York date +%u)
if [ "${ticks:-0}" -ge 2 ]; then
  green "Scheduler aktiv ($ticks Log-Zeilen in 2h)"
elif [ "$et_dow" -le 5 ] && [ "$et_hm" -ge 955 ] && [ "$et_hm" -le 1610 ]; then
  red "Kein Scheduler-Lebenszeichen IM Scan-Fenster  →  systemctl restart tradingbot-api tradingbot-bg"
else
  yellow "Keine Ticks — ausserhalb Aktien-Fenster (Mo-Fr 10:00-16:05 ET) normal; 24/7-Nachweis: Crypto-Cache [4]"
fi

echo
echo "[4] Cache-Frische"
# Primaere gemeinsame /tmp-Sicht ist das systemd-StateDirectory unter /var/lib.
# Legacy-Pfade bleiben waehrend der Migration lesbar; die juengste Datei gewinnt.
resolve_cache() {
  local base="$1" newest="" f
  for f in "/var/lib/alpha-station-runtime/$base" \
           "/tmp/$base" \
           /tmp/systemd-private-*-tradingbot-api.service-*/tmp/"$base" \
           /tmp/systemd-private-*-tradingbot-bg.service-*/tmp/"$base"; do
    [ -f "$f" ] || continue
    if [ -z "$newest" ] || [ "$f" -nt "$newest" ]; then newest="$f"; fi
  done
  printf '%s' "$newest"
}
check_cache() {
  # $1 Name, $2 Dateiname, $3 Limit in Minuten, $4 Modus: "fenster" = nur
  # 09:30-15:45 ET, "immer" = 24/7 (kein Wochenend-Nachlass)
  local name="$1" base="$2" maxmin="$3" mode="${4:-}"
  local path; path=$(resolve_cache "$base")
  if [ -z "$path" ]; then
    yellow "$name: Cache fehlt ($base) — wird beim naechsten Lauf gebaut"
    return
  fi
  local age=$(( ( $(date +%s) - $(stat -c %Y "$path") ) / 60 ))
  if [ "$age" -le "$maxmin" ]; then
    green "$name: ${age} min alt (Limit $maxmin)"
  elif [ "$mode" = "fenster" ]; then
    yellow "$name: ${age} min alt — ausserhalb 09:30-15:45 ET normal"
  elif [ "$dow" -ge 6 ] && [ "$mode" != "immer" ]; then
    yellow "$name: ${age} min alt — Wochenende, kein Refresh geplant (normal)"
  else
    red "$name: ${age} min alt (Limit $maxmin) — Scanner haengt?  →  systemctl restart tradingbot-api tradingbot-bg"
  fi
}
check_cache "Aktien-Scanner"  stock_cache.json           75
check_cache "Indizes"         index_cache.json           75
check_cache "Heatmap"         heatmap_cache.json         75
check_cache "A-Share"         a_share_cache.json         75
check_cache "Strategie-Sweep" stock_strategy_cache.json  370
check_cache "Early Movers"    movers_cache.json          370
check_cache "Premarket"       premarket_cache.json       370 fenster
check_cache "Crypto-Signale"  crypto_trade_signals_cache.json 45 immer

echo
echo "[5] Sicherheit"
if grep -q '^JWT_SECRET=.\+' "$APP/.env" 2>/dev/null; then
  green "JWT_SECRET gesetzt (Sessions ueberleben Neustarts)"
else
  jwtwarn=$(journalctl -u tradingbot-api -b --no-pager 2>/dev/null | grep -c 'JWT_SECRET ist nicht gesetzt' || true)
  if [ "${jwtwarn:-0}" -eq 0 ]; then
    green "JWT_SECRET aktiv (keine Warnung seit Boot)"
  else
    red "JWT_SECRET fehlt  →  siehe deploy/SERVER_WARTUNG.md Abschnitt 4"
  fi
fi

echo
echo "[6] Auto-Update"
check_auto_update

echo
echo "[7] Ressourcen"
disk=$(df / --output=pcent 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$disk" ] && [ "$disk" -lt 85 ]; then
  green "Festplatte: ${disk}% belegt"
elif [ -n "$disk" ]; then
  yellow "Festplatte: ${disk}% belegt — aufraeumen"
fi
mem=$(free 2>/dev/null | awk '/Mem:/ {printf "%d", $3/$2*100}')
if [ -n "$mem" ] && [ "$mem" -lt 90 ]; then
  green "RAM: ${mem}% belegt"
elif [ -n "$mem" ]; then
  yellow "RAM: ${mem}% belegt"
fi

finish_check
exit $?
