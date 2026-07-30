#!/usr/bin/env bash
# Alpha Station — Auto-Update (fuer Cron).
#
# Prueft, ob origin/main neuer ist als der Server-Stand. Wenn ja:
# git pull (fast-forward only) + Neustart beider Dienste + Logzeile.
# Fehlschlag => KEIN Neustart, nur Log (Betrieb laeuft auf altem Stand weiter).
#
# Einmalig installieren (auf dem Server, als root):
#   chmod +x /home/tradingbot/app/deploy/auto_update.sh
#   ( crontab -l 2>/dev/null | grep -v auto_update.sh ; \
#     echo '*/10 * * * * /home/tradingbot/app/deploy/auto_update.sh >> /var/log/alpha_autoupdate.log 2>&1' ) | crontab -
#
# Pruefen:  crontab -l   und   tail -5 /var/log/alpha_autoupdate.log
# Abklemmen: crontab -l | grep -v auto_update.sh | crontab -

set -u
APP_DIR="${APP_DIR:-/home/tradingbot/app}"
LOG_TAG="alpha-auto-update"

# Nicht parallel laufen (Cron-Takt kuerzer als ein Lauf?)
exec 9>/tmp/alpha_auto_update.lock
flock -n 9 || exit 0

cd "$APP_DIR" || { logger -t "$LOG_TAG" -p user.err "APP_DIR $APP_DIR fehlt"; exit 1; }

git fetch origin main --quiet 2>/dev/null || { logger -t "$LOG_TAG" "git fetch fehlgeschlagen (Netz?) — naechster Takt"; exit 0; }

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[ "$LOCAL" = "$REMOTE" ] && exit 0   # nichts zu tun — Normalfall

logger -t "$LOG_TAG" "Neue Version: $LOCAL -> $REMOTE — pulle + starte neu"

if git pull --ff-only origin main >>/dev/null 2>&1; then
    systemctl restart tradingbot-api tradingbot-bg
    logger -t "$LOG_TAG" "Deploy OK: $(git rev-parse --short HEAD) aktiv, Dienste neu gestartet"
else
    logger -t "$LOG_TAG" -p user.err "git pull fehlgeschlagen (kein fast-forward?) — Dienste NICHT angefasst, manuell pruefen: cd $APP_DIR && git status"
fi
