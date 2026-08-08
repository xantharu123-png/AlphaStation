#!/usr/bin/env bash
# Alpha Station — Auto-Update (fuer Cron).
#
# Prueft, ob origin/main neuer ist als der Server-Stand. Wenn ja, uebernimmt
# safe_deploy.sh Preflight, Tests, Pull, Service-Sync, Healthcheck und Rollback.
# Fehlschlag => automatischer Rollback bzw. alter Stand bleibt aktiv.
#
# Einmalig installieren (auf dem Server, als root):
#   chmod +x /home/tradingbot/app/deploy/auto_update.sh
#   ( crontab -l 2>/dev/null | grep -v auto_update.sh ; \
#     echo '*/10 * * * * /home/tradingbot/app/deploy/auto_update.sh >> /var/log/alpha_autoupdate.log 2>&1' ) | crontab -
#
# Pruefen:  crontab -l   und   tail -5 /var/log/alpha_autoupdate.log
# Abklemmen: crontab -l | grep -v auto_update.sh | crontab -

set -Eeuo pipefail
APP_DIR="${APP_DIR:-/home/tradingbot/app}"
LOG_TAG="alpha-auto-update"

# Nicht parallel laufen (Cron-Takt kuerzer als ein Lauf?)
exec 9>/tmp/alpha_auto_update.lock
flock -n 9 || exit 0

cd "$APP_DIR" || { logger -t "$LOG_TAG" -p user.err "APP_DIR $APP_DIR fehlt"; exit 1; }

if ! git fetch origin main --quiet 2>/dev/null; then
    logger -t "$LOG_TAG" -p user.err "git fetch fehlgeschlagen (Netz?) — Deployment nicht gestartet"
    exit 1
fi

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse origin/main)"
[ "$LOCAL" = "$REMOTE" ] && exit 0   # nichts zu tun — Normalfall

logger -t "$LOG_TAG" "Neue Version: $LOCAL -> $REMOTE — starte sicheren Deploy"

TARGET_DEPLOY="$(mktemp /tmp/alpha-safe-deploy.XXXXXX)"
cleanup_target_deploy() {
    rm -f -- "$TARGET_DEPLOY"
}
trap cleanup_target_deploy EXIT

# Run the deploy contract from the fetched target itself. Otherwise the first
# rollout of a deploy-script fix would still be governed by the stale script.
if ! git show "${REMOTE}:deploy/safe_deploy.sh" > "$TARGET_DEPLOY"; then
    logger -t "$LOG_TAG" -p user.err "Ziel-Deployskript konnte nicht aus $REMOTE geladen werden"
    exit 1
fi
chmod 0700 "$TARGET_DEPLOY"

if APP_DIR="$APP_DIR" BRANCH="main" EXPECTED_REVISION="$REMOTE" bash "$TARGET_DEPLOY"; then
    logger -t "$LOG_TAG" "Deploy OK: $(git rev-parse --short HEAD) getestet und aktiv"
    exit 0
else
    logger -t "$LOG_TAG" -p user.err "Sicherer Deploy fehlgeschlagen/rollback — manuell pruefen: cd $APP_DIR && git status"
    exit 1
fi
