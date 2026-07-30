#!/usr/bin/env bash
# os_maintenance.sh — Ubuntu-Pflege in EINEM Befehl:
#   bash /home/tradingbot/app/deploy/os_maintenance.sh
#
# Ablauf: apt update/upgrade/autoremove -> Reboot wenn noetig (mit
# automatischer Nach-Verifikation via @reboot-Einmal-Cron) -> alles protokolliert
# in /var/log/alpha_os_maintenance.log
#
# Modus "--post": laeuft nach dem Reboot automatisch (@reboot-Cron), verifiziert
# Dienste + Gesundheit und entfernt sich selbst aus der Crontab.
set -u
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive

APP=/home/tradingbot/app
LOG=/var/log/alpha_os_maintenance.log
SELF="$APP/deploy/os_maintenance.sh"

say() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*" | tee -a "$LOG"; }

if [ "${1:-}" = "--post" ]; then
  say "=== POST-REBOOT-Verifikation (warte 90s auf Dienste) ==="
  sleep 90
  {
    echo "--- is-active / is-enabled ---"
    systemctl is-active tradingbot-api tradingbot-bg
    systemctl is-enabled tradingbot-api tradingbot-bg
    echo "--- health_check ---"
    bash "$APP/deploy/health_check.sh"
  } >> "$LOG" 2>&1
  ( crontab -l 2>/dev/null | grep -v 'os_maintenance.sh --post' ) | crontab -
  say "=== POST-REBOOT-Verifikation fertig — Einmal-Cron entfernt ==="
  exit 0
fi

say "=== OS-Wartung gestartet ==="
say "Commit: $(git -C "$APP" log --oneline -1 2>/dev/null || echo '?')"

say "apt update ..."
if ! apt update >> "$LOG" 2>&1; then
  say "WARN: apt update meldete Fehler (Details im Log) — versuche Upgrade trotzdem"
fi
say "apt upgrade laeuft (kann einige Minuten dauern) ..."
if apt upgrade -y >> "$LOG" 2>&1; then
  say "apt upgrade OK"
else
  say "FEHLER: apt upgrade fehlgeschlagen — Abbruch VOR dem Reboot (Log pruefen)"
  exit 1
fi
apt autoremove -y >> "$LOG" 2>&1 || true

if [ -f /var/run/reboot-required ]; then
  say "Reboot erforderlich — richte Post-Reboot-Verifikation ein (@reboot-Einmal-Cron)"
  ( crontab -l 2>/dev/null | grep -v 'os_maintenance.sh --post' ; \
    echo "@reboot /bin/bash $SELF --post >> $LOG 2>&1" ) | crontab -
  say "REBOOT in 10s — SSH bricht jetzt ab. Ergebnis spaeter: cat $LOG"
  sleep 10
  systemctl reboot
else
  say "Kein Reboot noetig — starte Bot-Dienste sauber neu"
  systemctl restart tradingbot-api tradingbot-bg
  sleep 20
  bash "$APP/deploy/health_check.sh" >> "$LOG" 2>&1
  say "=== OS-Wartung fertig (ohne Reboot) — Health-Check im Log ==="
fi
