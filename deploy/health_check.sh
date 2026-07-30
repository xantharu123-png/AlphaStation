#!/usr/bin/env bash
# health_check.sh — Alpha Station Gesundheitscheck (Ampel-Ausgabe, ein Befehl)
# Aufruf:  bash /home/tradingbot/app/deploy/health_check.sh
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

APP=/home/tradingbot/app
ok=0; warn=0; fail=0

green()  { printf '  \033[32mOK\033[0m     %s\n' "$1"; ok=$((ok+1)); }
yellow() { printf '  \033[33mWARN\033[0m   %s\n' "$1"; warn=$((warn+1)); }
red()    { printf '  \033[31mFEHLER\033[0m %s\n' "$1"; fail=$((fail+1)); }

dow=$(date +%u)   # 6=Samstag, 7=Sonntag

echo "==============================================================="
echo " Alpha Station Gesundheitscheck — $(date '+%Y-%m-%d %H:%M %Z')"
echo "==============================================================="

echo
echo "[1] Dienste"
for svc in tradingbot-api tradingbot-bg; do
  state=$(systemctl is-active "$svc" 2>/dev/null)
  if [ "$state" = "active" ]; then
    green "$svc laeuft"
  else
    red "$svc ist '$state'  →  systemctl restart $svc"
  fi
  en=$(systemctl is-enabled "$svc" 2>/dev/null)
  if [ "$en" = "enabled" ]; then
    green "$svc startet nach Reboot automatisch"
  else
    yellow "$svc ist nicht 'enabled'  →  systemctl enable $svc"
  fi
done

echo
echo "[2] API erreichbar"
code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 http://127.0.0.1:8000/ 2>/dev/null || echo 000)
if [ "$code" = "000" ]; then
  red "API antwortet nicht auf Port 8000  →  journalctl -u tradingbot-api -n 50"
else
  green "API antwortet (HTTP $code)"
fi
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
# Dienste schreiben ggf. in PrivateTmp-Namespaces; juengste Sicht auf die
# Datei gewinnt (direktes /tmp, api-Namespace, bg-Namespace).
resolve_cache() {
  local base="$1" newest="" f
  for f in "/tmp/$base" \
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
if crontab -l 2>/dev/null | grep -q 'auto_update.sh'; then
  green "Cron-Eintrag vorhanden (alle 10 min)"
else
  red "Cron fehlt  →  ( crontab -l; echo '*/10 * * * * $APP/deploy/auto_update.sh >> /var/log/alpha_autoupdate.log 2>&1' ) | crontab -"
fi
if [ -s /var/log/alpha_autoupdate.log ]; then
  green "Letzter Auto-Update-Lauf: $(tail -1 /var/log/alpha_autoupdate.log)"
else
  yellow "Auto-Update-Log leer — seit Einrichtung kein Push (normal)"
fi
if [ -d "$APP/.git" ]; then
  behind=$(git -C "$APP" rev-list HEAD..origin/main --count 2>/dev/null || echo '?')
  if [ "$behind" = "0" ]; then
    green "Code ist aktuell (HEAD = origin/main)"
  elif [ "$behind" = "?" ]; then
    yellow "origin/main nicht vergleichbar (kein Fetch moeglich)"
  else
    red "Server hinkt $behind Commit(s) hinterher — Auto-Update greift beim naechsten Tick"
  fi
fi

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

echo
echo "==============================================================="
echo " Ergebnis: $ok OK | $warn Warnung(en) | $fail Fehler"
echo "==============================================================="
if [ "$fail" -gt 0 ]; then
  echo " → Rot zuerst beheben. Anleitung: $APP/deploy/SERVER_WARTUNG.md"
  exit 1
fi
if [ "$warn" -gt 0 ]; then
  echo " → Laeuft. Gelb = pruefen, meist harmlos (Wochenende/Nacht)."
  exit 0
fi
echo " → Alles gruen."
exit 0
