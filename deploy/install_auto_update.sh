#!/usr/bin/env bash
# Installiert den Alpha-Station-Auto-Updater idempotent als Root-Cron.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
TRUSTED_HOME="${TRUSTED_HOME:-/home/tradingbot}"
TRUST_STAT_BIN="${TRUST_STAT_BIN:-/usr/bin/stat}"
TRUST_FIND_BIN="${TRUST_FIND_BIN:-/usr/bin/find}"
TRUST_READLINK_BIN="${TRUST_READLINK_BIN:-/usr/bin/readlink}"
CRON_FILE="${AUTO_UPDATE_CRON_FILE:-/etc/cron.d/alpha-station-auto-update}"
LOG_FILE="${AUTO_UPDATE_LOG:-/var/log/alpha_autoupdate.log}"
BASH_BIN="${BASH_BIN:-/bin/bash}"
UPDATER="$APP_DIR/deploy/auto_update.sh"
AUTO_UPDATE_LAUNCHER="${AUTO_UPDATE_LAUNCHER:-/usr/local/sbin/alpha-station-auto-update}"
AUTO_UPDATE_LOCK_DIR="${AUTO_UPDATE_LOCK_DIR:-/run/alpha-station}"
RUN_PROBE=1

trust_fail() {
    echo "Source trust check failed: $*" >&2
    return 1
}

validate_root_controlled_path() {
    local path="$1" owner mode metadata
    if [ ! -e "$path" ] || [ -L "$path" ]; then
        trust_fail "missing or symlink path $path"
        return 1
    fi
    metadata="$("$TRUST_STAT_BIN" -c '%u %a' -- "$path" 2>/dev/null || true)"
    read -r owner mode <<< "$metadata"
    if [ "$owner" != "0" ] || [[ ! "$mode" =~ ^[0-7]+$ ]] \
      || (( (8#$mode & 8#22) != 0 )); then
        trust_fail "$path must be root-owned and not group/world-writable"
        return 1
    fi
}

validate_root_controlled_chain() {
    local current="$1" parent
    case "$current" in
      /*|[A-Za-z]:/*) ;;
      *) trust_fail "path must be absolute: $current"; return 1 ;;
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
            trust_fail "cannot resolve parent of $current"
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
        trust_fail "symlinks cannot be inspected"
        return 1
    fi
    while IFS= read -r link; do
        [ -n "$link" ] || continue
        if [[ "$link" != "$APP_DIR/venv/"* ]]; then
            trust_fail "source symlink is not allowed: $link"
            return 1
        fi
        target="$("$TRUST_READLINK_BIN" -f -- "$link" 2>/dev/null || true)"
        case "$target" in
          "$APP_DIR/venv/"*|/usr/bin/*|/usr/local/bin/*|/lib/*|/lib64/*|/usr/lib/*|/usr/lib64/*) ;;
          *) trust_fail "unsafe symlink target ${target:-missing}"; return 1 ;;
        esac
        validate_root_controlled_chain "$target" || return 1
    done <<< "$symlink_list"
}

verify_source_trust() {
    local path unsafe_path
    if [ "$APP_DIR" != "$TRUSTED_HOME/app" ]; then
        trust_fail "APP_DIR must be $TRUSTED_HOME/app"
        return 1
    fi
    validate_root_controlled_chain "$TRUSTED_HOME" || return 1
    for path in \
      "$APP_DIR" \
      "$APP_DIR/deploy" \
      "$APP_DIR/deploy/auto_update.sh" \
      "$APP_DIR/deploy/install_auto_update.sh" \
      "$APP_DIR/.git" \
      "$APP_DIR/venv"; do
        validate_root_controlled_path "$path" || return 1
    done
    if ! unsafe_path="$("$TRUST_FIND_BIN" "$APP_DIR" -xdev \
      \( -path "$APP_DIR/data_cache" -o -path "$APP_DIR/data_cache/*" \) -prune -o \
      \( -type f -o -type d \) \( ! -uid 0 -o -perm /022 \) \
      -print -quit 2>/dev/null)"; then
        trust_fail "source tree cannot be inspected"
        return 1
    fi
    if [ -n "$unsafe_path" ]; then
        trust_fail "unsafe entry $unsafe_path"
        return 1
    fi
    validate_source_symlinks || return 1
}

ensure_secure_lock_directory() {
    local parent
    if [ -L "$AUTO_UPDATE_LOCK_DIR" ]; then
        trust_fail "lock directory must not be a symlink: $AUTO_UPDATE_LOCK_DIR"
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
    install -d -o root -g root -m 0700 "$AUTO_UPDATE_LOCK_DIR"
    validate_root_controlled_chain "$AUTO_UPDATE_LOCK_DIR"
}

case "${1:-}" in
    "") ;;
    --no-probe) RUN_PROBE=0 ;;
    *)
        echo "Aufruf: $0 [--no-probe]" >&2
        exit 2
        ;;
esac

if [ ! -f "$UPDATER" ]; then
    echo "Auto-Updater fehlt: $UPDATER" >&2
    exit 1
fi
if [ ! -d "$APP_DIR/.git" ]; then
    echo "Auto-Update benoetigt einen Git-Checkout: $APP_DIR/.git fehlt" >&2
    exit 1
fi

verify_source_trust

launcher_dir="${AUTO_UPDATE_LAUNCHER%/*}"
validate_root_controlled_chain "$launcher_dir"
ensure_secure_lock_directory

cron_line="*/10 * * * * root /bin/bash $AUTO_UPDATE_LAUNCHER >> $LOG_FILE 2>&1"
cron_tmp="$(mktemp)"
legacy_cron_tmp="$(mktemp)"
clean_cron_tmp="$(mktemp)"
launcher_candidate="$(mktemp "$launcher_dir/.alpha-station-auto-update.XXXXXX")"
cron_dir="${CRON_FILE%/*}"
cron_candidate=""
rollback_backup_dir="$(mktemp -d "$AUTO_UPDATE_LOCK_DIR/install-rollback.XXXXXX")"
chmod 0700 "$rollback_backup_dir"
validate_root_controlled_path "$rollback_backup_dir"
launcher_backup="$rollback_backup_dir/launcher"
cron_backup="$rollback_backup_dir/cron-file"
launcher_existed=0
cron_file_existed=0
legacy_crontab_present=0
legacy_crontab_change_needed=0
legacy_crontab_restore_needed=0
cron_original_load_state=""
cron_original_active_state=""
cron_original_unit_file_state=""
cron_state_load=""
cron_state_active=""
cron_state_unit_file=""
transaction_started=0
transaction_committed=0
cleanup() {
    rm -f -- "$cron_tmp" "$legacy_cron_tmp" "$clean_cron_tmp" \
      "$launcher_backup" "$cron_backup"
    rmdir -- "$rollback_backup_dir" 2>/dev/null || true
    [ -z "$launcher_candidate" ] || rm -f -- "$launcher_candidate"
    [ -z "$cron_candidate" ] || rm -f -- "$cron_candidate"
}

restore_managed_file() {
    local backup="$1" destination="$2" destination_dir restore_dir restore_candidate
    destination_dir="${destination%/*}"
    restore_dir="$(mktemp -d "$destination_dir/.alpha-station-rollback.XXXXXX")" \
      || return 1
    chmod 0700 "$restore_dir" || { rmdir -- "$restore_dir"; return 1; }
    restore_candidate="$restore_dir/managed-file"
    if ! cp --preserve=all -- "$backup" "$restore_candidate"; then
        rm -f -- "$restore_candidate"
        rmdir -- "$restore_dir" 2>/dev/null || true
        return 1
    fi
    if ! validate_root_controlled_path "$restore_candidate"; then
        rm -f -- "$restore_candidate"
        rmdir -- "$restore_dir" 2>/dev/null || true
        return 1
    fi
    if ! mv -f -- "$restore_candidate" "$destination"; then
        rm -f -- "$restore_candidate"
        rmdir -- "$restore_dir" 2>/dev/null || true
        return 1
    fi
    rmdir -- "$restore_dir" || return 1
    validate_root_controlled_path "$destination"
}

query_cron_state() {
    local context="$1" load_state active_state unit_file_state
    if ! load_state="$(systemctl show --property=LoadState --value cron.service 2>/dev/null)"; then
        echo "cron.service LoadState-Abfrage fehlgeschlagen ($context)" >&2
        return 1
    fi
    if ! active_state="$(systemctl show --property=ActiveState --value cron.service 2>/dev/null)"; then
        echo "cron.service ActiveState-Abfrage fehlgeschlagen ($context)" >&2
        return 1
    fi
    if ! unit_file_state="$(systemctl show --property=UnitFileState --value cron.service 2>/dev/null)"; then
        echo "cron.service UnitFileState-Abfrage fehlgeschlagen ($context)" >&2
        return 1
    fi
    cron_state_load="${load_state%$'\r'}"
    cron_state_active="${active_state%$'\r'}"
    cron_state_unit_file="${unit_file_state%$'\r'}"
}

capture_cron_snapshot() {
    query_cron_state "Snapshot vor Mutation" || return 1
    if [ "$cron_state_load" != "loaded" ]; then
        echo "cron.service hat keinen vertrauenswuerdigen LoadState: ${cron_state_load:-missing}" >&2
        return 1
    fi
    case "$cron_state_active" in
        active|inactive|failed) ;;
        *)
            echo "cron.service hat einen mehrdeutigen ActiveState: ${cron_state_active:-missing}" >&2
            return 1
            ;;
    esac
    case "$cron_state_unit_file" in
        enabled|disabled) ;;
        *)
            echo "cron.service hat einen mehrdeutigen UnitFileState: ${cron_state_unit_file:-missing}" >&2
            return 1
            ;;
    esac
    cron_original_load_state="$cron_state_load"
    cron_original_active_state="$cron_state_active"
    cron_original_unit_file_state="$cron_state_unit_file"
}

assert_cron_state() {
    local context="$1" expected_load="$2" expected_active="$3" expected_unit_file="$4"
    query_cron_state "$context" || return 1
    if [ "$cron_state_load" != "$expected_load" ] \
      || [ "$cron_state_active" != "$expected_active" ] \
      || [ "$cron_state_unit_file" != "$expected_unit_file" ]; then
        echo "cron.service Zustand weicht ab ($context): " \
          "${cron_state_load:-missing}/${cron_state_active:-missing}/${cron_state_unit_file:-missing}, " \
          "erwartet $expected_load/$expected_active/$expected_unit_file" >&2
        return 1
    fi
}

rollback_transaction() {
    local rollback_failed=0 current_state_known=0
    echo "Auto-Update-Installation fehlgeschlagen; vorherigen Cron-Zustand wiederherstellen" >&2

    if query_cron_state "Rollback-Vorbereitung"; then
        current_state_known=1
    fi
    if [ "$current_state_known" -ne 1 ]; then
        systemctl stop cron >/dev/null 2>&1 || rollback_failed=1
    else
        case "$cron_state_active" in
            inactive|failed) ;;
            *) systemctl stop cron >/dev/null 2>&1 || rollback_failed=1 ;;
        esac
    fi

    if [ "$launcher_existed" -eq 1 ]; then
        restore_managed_file "$launcher_backup" "$AUTO_UPDATE_LAUNCHER" \
          || rollback_failed=1
    else
        rm -f -- "$AUTO_UPDATE_LAUNCHER" || rollback_failed=1
    fi
    if [ "$cron_file_existed" -eq 1 ]; then
        restore_managed_file "$cron_backup" "$CRON_FILE" \
          || rollback_failed=1
    else
        rm -f -- "$CRON_FILE" || rollback_failed=1
    fi
    if [ "$legacy_crontab_restore_needed" -eq 1 ]; then
        crontab "$legacy_cron_tmp" || rollback_failed=1
    fi

    case "$cron_original_unit_file_state" in
        enabled) systemctl enable cron >/dev/null 2>&1 || rollback_failed=1 ;;
        disabled) systemctl disable cron >/dev/null 2>&1 || rollback_failed=1 ;;
        *) rollback_failed=1 ;;
    esac
    case "$cron_original_active_state" in
        active) systemctl start cron >/dev/null 2>&1 || rollback_failed=1 ;;
        inactive)
            if query_cron_state "Rollback auf inactive"; then
                [ "$cron_state_active" = "inactive" ] \
                  || systemctl stop cron >/dev/null 2>&1 \
                  || rollback_failed=1
            else
                systemctl stop cron >/dev/null 2>&1 || rollback_failed=1
            fi
            ;;
        failed)
            # Ein failed-Zustand kann nicht sicher synthetisiert werden. Er bleibt
            # exakt erhalten, solange der inaktive Dienst nie gestoppt wurde.
            ;;
        *) rollback_failed=1 ;;
    esac
    assert_cron_state "Rollback-Endzustand" \
      "$cron_original_load_state" "$cron_original_active_state" \
      "$cron_original_unit_file_state" || rollback_failed=1
    if [ "$rollback_failed" -ne 0 ]; then
        echo "KRITISCH: Auto-Update-Rollback war unvollstaendig; Cron manuell gesperrt pruefen" >&2
        return 1
    fi
    echo "Vorheriger Cron-/Launcher-Zustand wurde wiederhergestellt" >&2
}

finish_install() {
    local status=$? rollback_status=0
    trap - EXIT
    set +e
    if [ "$status" -ne 0 ] && [ "$transaction_started" -eq 1 ] \
      && [ "$transaction_committed" -eq 0 ]; then
        rollback_transaction || rollback_status=$?
        [ "$rollback_status" -eq 0 ] || status=1
    fi
    cleanup
    exit "$status"
}
trap finish_install EXIT

install -o root -g root -m 0755 "$UPDATER" "$launcher_candidate"
validate_root_controlled_path "$launcher_candidate"

cat > "$cron_tmp" <<EOF
# Managed by $APP_DIR/deploy/install_auto_update.sh -- do not edit manually.
$cron_line
EOF

if [ "$RUN_PROBE" -eq 1 ]; then
    echo "Auto-Update-Probe (kein Deploy):"
    APP_DIR="$APP_DIR" TRUSTED_HOME="$TRUSTED_HOME" \
      AUTO_UPDATE_LAUNCHER="$launcher_candidate" \
      AUTO_UPDATE_LOCK_DIR="$AUTO_UPDATE_LOCK_DIR" \
      TRUST_STAT_BIN="$TRUST_STAT_BIN" TRUST_FIND_BIN="$TRUST_FIND_BIN" \
      TRUST_READLINK_BIN="$TRUST_READLINK_BIN" \
      "$BASH_BIN" "$launcher_candidate" --probe
fi

# Migration der alten Root-crontab-Zeile. Fremde Eintraege bleiben erhalten;
# so laufen alter Direktaufruf und neuer /etc/cron.d-Vertrag nie parallel.
is_exact_legacy_cron_line() {
    local line="$1" trimmed f1 f2 f3 f4 f5 command
    local -a command_tokens=()
    line="${line%$'\r'}"
    trimmed="${line#"${line%%[![:space:]]*}"}"
    case "$trimmed" in
        ""|\#*) return 1 ;;
    esac
    read -r f1 f2 f3 f4 f5 command <<< "$trimmed"
    [ -n "${command:-}" ] || return 1
    read -r -a command_tokens <<< "$command"
    case "${#command_tokens[@]}" in
        1)
            [ "${command_tokens[0]}" = "$UPDATER" ]
            ;;
        2)
            [ "${command_tokens[0]}" = "/bin/bash" ] \
              && [ "${command_tokens[1]}" = "$UPDATER" ]
            ;;
        4)
            [ "${command_tokens[0]}" = "$UPDATER" ] \
              && [ "${command_tokens[1]}" = ">>" ] \
              && [ "${command_tokens[2]}" = "$LOG_FILE" ] \
              && [ "${command_tokens[3]}" = "2>&1" ]
            ;;
        5)
            [ "${command_tokens[0]}" = "/bin/bash" ] \
              && [ "${command_tokens[1]}" = "$UPDATER" ] \
              && [ "${command_tokens[2]}" = ">>" ] \
              && [ "${command_tokens[3]}" = "$LOG_FILE" ] \
              && [ "${command_tokens[4]}" = "2>&1" ]
            ;;
        *) return 1 ;;
    esac
}

if command -v crontab >/dev/null 2>&1 && crontab -l > "$legacy_cron_tmp" 2>/dev/null; then
    legacy_crontab_present=1
    : > "$clean_cron_tmp"
    while IFS= read -r cron_line_existing || [ -n "$cron_line_existing" ]; do
        if ! is_exact_legacy_cron_line "$cron_line_existing"; then
            printf '%s\n' "$cron_line_existing" >> "$clean_cron_tmp"
        fi
    done < "$legacy_cron_tmp"
    if ! cmp -s "$legacy_cron_tmp" "$clean_cron_tmp"; then
        legacy_crontab_change_needed=1
    fi
fi

validate_root_controlled_chain "$cron_dir"
cron_candidate="$(mktemp "$cron_dir/.alpha-station-auto-update-cron.XXXXXX")"
install -o root -g root -m 0644 "$cron_tmp" "$cron_candidate"
validate_root_controlled_path "$cron_candidate"

if [ -L "$AUTO_UPDATE_LAUNCHER" ]; then
    trust_fail "existing launcher must not be a symlink: $AUTO_UPDATE_LAUNCHER"
    exit 1
elif [ -e "$AUTO_UPDATE_LAUNCHER" ]; then
    validate_root_controlled_path "$AUTO_UPDATE_LAUNCHER"
    cp --preserve=all -- "$AUTO_UPDATE_LAUNCHER" "$launcher_backup"
    validate_root_controlled_path "$launcher_backup"
    launcher_existed=1
fi
if [ -L "$CRON_FILE" ]; then
    trust_fail "existing cron file must not be a symlink: $CRON_FILE"
    exit 1
elif [ -e "$CRON_FILE" ]; then
    validate_root_controlled_path "$CRON_FILE"
    cp --preserve=all -- "$CRON_FILE" "$cron_backup"
    validate_root_controlled_path "$cron_backup"
    cron_file_existed=1
fi

if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl fehlt; Cron-Transaktion wird nicht begonnen" >&2
    exit 1
fi
capture_cron_snapshot
transaction_started=1
if [ "$cron_original_active_state" = "active" ]; then
    if ! systemctl stop cron >/dev/null; then
        echo "cron.service konnte fuer die Transaktion nicht gestoppt werden" >&2
        exit 1
    fi
    assert_cron_state "nach Stop" "loaded" "inactive" \
      "$cron_original_unit_file_state"
else
    assert_cron_state "unmittelbar vor Publikation" \
      "$cron_original_load_state" "$cron_original_active_state" \
      "$cron_original_unit_file_state"
fi

# Cron ist gestoppt. Launcher und /etc/cron.d-Datei werden nun atomar
# veroeffentlicht, bevor der Legacy-Job entfernt oder Cron wieder gestartet wird.
if [ ! -f "$AUTO_UPDATE_LAUNCHER" ] \
  || ! cmp -s "$launcher_candidate" "$AUTO_UPDATE_LAUNCHER"; then
    mv -f -- "$launcher_candidate" "$AUTO_UPDATE_LAUNCHER"
    launcher_candidate=""
else
    rm -f -- "$launcher_candidate"
    launcher_candidate=""
fi
validate_root_controlled_path "$AUTO_UPDATE_LAUNCHER"

if [ -L "$LOG_FILE" ]; then
    trust_fail "auto-update log must not be a symlink: $LOG_FILE"
    exit 1
elif [ ! -e "$LOG_FILE" ]; then
    install -o root -g root -m 0640 /dev/null "$LOG_FILE"
else
    validate_root_controlled_path "$LOG_FILE"
    chmod 0640 "$LOG_FILE"
fi

if [ ! -f "$CRON_FILE" ] || ! cmp -s "$cron_tmp" "$CRON_FILE"; then
    mv -f -- "$cron_candidate" "$CRON_FILE"
    cron_candidate=""
    echo "Auto-Update-Cron installiert: $CRON_FILE"
else
    rm -f -- "$cron_candidate"
    cron_candidate=""
    echo "Auto-Update-Cron bereits aktuell: $CRON_FILE"
fi

if [ "$legacy_crontab_change_needed" -eq 1 ]; then
    legacy_crontab_restore_needed=1
    if ! crontab "$clean_cron_tmp"; then
        echo "Legacy-Auto-Update konnte nicht transaktional entfernt werden" >&2
        exit 1
    fi
    echo "Legacy-Auto-Update aus Root-crontab entfernt"
fi

# Erst jetzt existieren Launcher und Cronfile vollstaendig und der Legacy-Job
# ist entfernt. Cron darf vorher unter keinen Umstaenden gestartet werden.
systemctl enable cron >/dev/null
systemctl start cron >/dev/null
assert_cron_state "nach Start" "loaded" "active" "enabled"
transaction_committed=1

echo "Cron-Vertrag: $cron_line"
