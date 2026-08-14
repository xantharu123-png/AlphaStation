#!/usr/bin/env bash
# Alpha Station — Auto-Update (fuer Cron).
#
# Prueft, ob origin/main neuer ist als der Server-Stand. Wenn ja, uebernimmt
# safe_deploy.sh Preflight, Tests, Pull, Service-Sync, Healthcheck und Rollback.
# Fehlschlag => automatischer Rollback bzw. alter Stand bleibt aktiv.
#
# Einmalig installieren (auf dem Server, als root):
#   /bin/bash /home/tradingbot/app/deploy/install_auto_update.sh
#
# Pruefen:  bash deploy/health_check.sh --auto-update-only
# Abklemmen: rm -f /etc/cron.d/alpha-station-auto-update

set -Eeuo pipefail
APP_DIR="${APP_DIR:-/home/tradingbot/app}"
TRUSTED_HOME="${TRUSTED_HOME:-/home/tradingbot}"
TRUST_STAT_BIN="${TRUST_STAT_BIN:-/usr/bin/stat}"
TRUST_FIND_BIN="${TRUST_FIND_BIN:-/usr/bin/find}"
TRUST_READLINK_BIN="${TRUST_READLINK_BIN:-/usr/bin/readlink}"
AUTO_UPDATE_LAUNCHER="${AUTO_UPDATE_LAUNCHER:-/usr/local/sbin/alpha-station-auto-update}"
AUTO_UPDATE_LOCK_DIR="${AUTO_UPDATE_LOCK_DIR:-/run/alpha-station}"
LOG_TAG="alpha-auto-update"
MODE="${1:-update}"

report() {
    local level="$1" action="$2"
    shift 2
    printf '%s %s status=%s action=%s %s\n' \
        "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$LOG_TAG" "$level" "$action" "$*"
    if command -v logger >/dev/null 2>&1; then
        if [ "$level" = "error" ]; then
            logger -t "$LOG_TAG" -p user.err -- "$action: $*" || true
        else
            logger -t "$LOG_TAG" -- "$action: $*" || true
        fi
    fi
}

git_app() {
    git -c "safe.directory=$APP_DIR" -C "$APP_DIR" "$@"
}

validate_root_controlled_path() {
    local path="$1" owner mode metadata
    if [ ! -e "$path" ] || [ -L "$path" ]; then
        report error trust "source trust check failed: missing or symlink path $path"
        return 1
    fi
    metadata="$("$TRUST_STAT_BIN" -c '%u %a' -- "$path" 2>/dev/null || true)"
    read -r owner mode <<< "$metadata"
    if [ "$owner" != "0" ] || [[ ! "$mode" =~ ^[0-7]+$ ]] \
      || (( (8#$mode & 8#22) != 0 )); then
        report error trust "source trust check failed: $path must be root-owned and not group/world-writable"
        return 1
    fi
}

validate_root_controlled_chain() {
    local current="$1" parent
    case "$current" in
      /*|[A-Za-z]:/*) ;;
      *)
        report error trust "source trust check failed: path must be absolute: $current"
        return 1
        ;;
    esac
    while :; do
        validate_root_controlled_path "$current" || return 1
        case "$current" in
          /|[A-Za-z]:/) return 0 ;;
        esac
        parent="${current%/*}"
        [ -n "$parent" ] || parent="/"
        [[ "$parent" =~ ^[A-Za-z]:$ ]] && parent="$parent/"
        if [ "$parent" = "$current" ]; then
            report error trust "source trust check failed: cannot resolve parent of $current"
            return 1
        fi
        current="$parent"
    done
}

validate_source_symlinks() {
    local symlink_list link target
    if ! symlink_list="$("$TRUST_FIND_BIN" "$APP_DIR" -xdev \
      \( -path "$APP_DIR/data_cache" -o -path "$APP_DIR/data_cache/*" \) -prune -o \
      -type l -print 2>/dev/null)"; then
        report error trust "source trust check failed: symlinks cannot be inspected"
        return 1
    fi
    while IFS= read -r link; do
        [ -n "$link" ] || continue
        if [[ "$link" != "$APP_DIR/venv/"* ]]; then
            report error trust "source trust check failed: source symlink is not allowed: $link"
            return 1
        fi
        target="$("$TRUST_READLINK_BIN" -f -- "$link" 2>/dev/null || true)"
        case "$target" in
          "$APP_DIR/venv/"*|/usr/bin/*|/usr/local/bin/*|/lib/*|/lib64/*|/usr/lib/*|/usr/lib64/*) ;;
          *)
            report error trust "source trust check failed: unsafe symlink target ${target:-missing}"
            return 1
            ;;
        esac
        validate_root_controlled_chain "$target" || return 1
    done <<< "$symlink_list"
}

verify_source_trust() {
    local path unsafe_path
    if [ "$APP_DIR" != "$TRUSTED_HOME/app" ]; then
        report error trust "source trust check failed: APP_DIR must be $TRUSTED_HOME/app"
        return 1
    fi
    validate_root_controlled_chain "$TRUSTED_HOME" || return 1
    for path in \
      "$APP_DIR" \
      "$APP_DIR/deploy" \
      "$APP_DIR/deploy/auto_update.sh" \
      "$APP_DIR/.git" \
      "$APP_DIR/venv"; do
        validate_root_controlled_path "$path" || return 1
    done
    if ! unsafe_path="$("$TRUST_FIND_BIN" "$APP_DIR" -xdev \
      \( -path "$APP_DIR/data_cache" -o -path "$APP_DIR/data_cache/*" \) -prune -o \
      \( -type f -o -type d \) \( ! -uid 0 -o -perm /022 \) \
      -print -quit 2>/dev/null)"; then
        report error trust "source trust check failed: source tree cannot be inspected"
        return 1
    fi
    if [ -n "$unsafe_path" ]; then
        report error trust "source trust check failed: unsafe entry $unsafe_path"
        return 1
    fi
    validate_source_symlinks || return 1
    report ok trust "source trust check passed"
}

ensure_secure_lock_directory() {
    local parent
    if [ -L "$AUTO_UPDATE_LOCK_DIR" ]; then
        report error lock "Lock-Verzeichnis darf kein Symlink sein: $AUTO_UPDATE_LOCK_DIR"
        return 1
    fi
    if [ -e "$AUTO_UPDATE_LOCK_DIR" ]; then
        validate_root_controlled_chain "$AUTO_UPDATE_LOCK_DIR"
        return
    fi
    parent="${AUTO_UPDATE_LOCK_DIR%/*}"
    [ -n "$parent" ] || parent="/"
    [[ "$parent" =~ ^[A-Za-z]:$ ]] && parent="$parent/"
    validate_root_controlled_chain "$parent" || return 1
    if ! install -d -o root -g root -m 0700 "$AUTO_UPDATE_LOCK_DIR"; then
        report error lock "Lock-Verzeichnis konnte nicht sicher angelegt werden"
        return 1
    fi
    validate_root_controlled_chain "$AUTO_UPDATE_LOCK_DIR"
}

export_safe_directory_for_child() {
    local config_count="${GIT_CONFIG_COUNT:-0}"
    local key_name value_name
    if ! [[ "$config_count" =~ ^[0-9]+$ ]]; then
        report error config "ungueltiges GIT_CONFIG_COUNT"
        return 1
    fi
    key_name="GIT_CONFIG_KEY_${config_count}"
    value_name="GIT_CONFIG_VALUE_${config_count}"
    printf -v "$key_name" '%s' 'safe.directory'
    printf -v "$value_name" '%s' "$APP_DIR"
    export "$key_name" "$value_name"
    export GIT_CONFIG_COUNT="$((config_count + 1))"
}

refresh_trusted_launcher() {
    local launcher_tmp
    [ "$0" = "$AUTO_UPDATE_LAUNCHER" ] || return 0
    if cmp -s "$APP_DIR/deploy/auto_update.sh" "$AUTO_UPDATE_LAUNCHER"; then
        return 0
    fi
    launcher_tmp="$(mktemp "${AUTO_UPDATE_LAUNCHER}.tmp.XXXXXX")"
    if ! install -o root -g root -m 0755 \
      "$APP_DIR/deploy/auto_update.sh" "$launcher_tmp" \
      || ! mv -f -- "$launcher_tmp" "$AUTO_UPDATE_LAUNCHER"; then
        rm -f -- "$launcher_tmp"
        report error launcher "root-owned Launcher konnte nicht atomar aktualisiert werden"
        return 1
    fi
    validate_root_controlled_path "$AUTO_UPDATE_LAUNCHER"
    report ok launcher "root-owned Launcher aktualisiert"
}

case "$MODE" in
    update|--probe) ;;
    *)
        report error usage "unbekannter Modus; erlaubt: --probe"
        exit 2
        ;;
esac

verify_source_trust
validate_root_controlled_chain "$0"
ensure_secure_lock_directory

# Nicht parallel laufen (Cron-Takt kuerzer als ein Lauf?)
exec 9>"$AUTO_UPDATE_LOCK_DIR/auto-update.lock"
flock -n 9 || exit 0

cd "$APP_DIR" || { report error setup "APP_DIR fehlt: $APP_DIR"; exit 1; }

if ! git_app fetch origin main --quiet 2>/dev/null; then
    report error fetch "origin/main nicht abrufbar; Deployment nicht gestartet"
    exit 1
fi

LOCAL="$(git_app rev-parse HEAD)"
REMOTE="$(git_app rev-parse origin/main)"

if [ "$MODE" = "--probe" ]; then
    report ok probe "revision=${LOCAL:0:12} local=${LOCAL:0:12} remote=${REMOTE:0:12}"
    exit 0
fi

if [ "$LOCAL" = "$REMOTE" ]; then
    report ok current "revision=${LOCAL:0:12}"
    exit 0
fi

report ok deploy-start "local=${LOCAL:0:12} remote=${REMOTE:0:12}"

TARGET_DEPLOY="$(mktemp /tmp/alpha-safe-deploy.XXXXXX)"
cleanup_target_deploy() {
    rm -f -- "$TARGET_DEPLOY"
}
trap cleanup_target_deploy EXIT

# Run the deploy contract from the fetched target itself. Otherwise the first
# rollout of a deploy-script fix would still be governed by the stale script.
if ! git_app show "${REMOTE}:deploy/safe_deploy.sh" > "$TARGET_DEPLOY"; then
    report error deploy-script "Zielskript fuer revision=${REMOTE:0:12} nicht ladbar"
    exit 1
fi
chmod 0700 "$TARGET_DEPLOY"

export_safe_directory_for_child
if APP_DIR="$APP_DIR" BRANCH="main" EXPECTED_REVISION="$REMOTE" bash "$TARGET_DEPLOY"; then
    if ! verify_source_trust || ! refresh_trusted_launcher; then
        report error launcher "Deploy aktiv, aber Launcher-Synchronisierung fehlgeschlagen"
        exit 1
    fi
    report ok deploy "revision=${REMOTE:0:12} getestet und aktiv"
    exit 0
else
    report error deploy "sicherer Deploy fehlgeschlagen oder rollback; Healthcheck ausfuehren"
    exit 1
fi
