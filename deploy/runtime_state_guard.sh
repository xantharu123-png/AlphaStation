#!/usr/bin/env bash
# Quiesce the dedicated service user, then validate and normalize data_cache
# without ever following service-controlled links or crossing filesystems.
set -Eeuo pipefail

APP_DIR="${APP_DIR:-/home/tradingbot/app}"
SERVICE_USER="${SERVICE_USER:-tradingbot}"
RUNTIME_WORK_DIR="${RUNTIME_WORK_DIR:-}"
RUNTIME_SYSTEMCTL_BIN="${RUNTIME_SYSTEMCTL_BIN:-/usr/bin/systemctl}"
RUNTIME_PGREP_BIN="${RUNTIME_PGREP_BIN:-/usr/bin/pgrep}"
RUNTIME_ID_BIN="${RUNTIME_ID_BIN:-/usr/bin/id}"
RUNTIME_STAT_BIN="${RUNTIME_STAT_BIN:-/usr/bin/stat}"
RUNTIME_FIND_BIN="${RUNTIME_FIND_BIN:-/usr/bin/find}"
RUNTIME_MOUNTPOINT_BIN="${RUNTIME_MOUNTPOINT_BIN:-/usr/bin/mountpoint}"
RUNTIME_CHOWN_BIN="${RUNTIME_CHOWN_BIN:-/usr/bin/chown}"
RUNTIME_CHMOD_BIN="${RUNTIME_CHMOD_BIN:-/usr/bin/chmod}"
RUNTIME_SETFACL_BIN="${RUNTIME_SETFACL_BIN:-/usr/bin/setfacl}"
RUNTIME_INSTALL_BIN="${RUNTIME_INSTALL_BIN:-/usr/bin/install}"
RUNTIME_PROC_ROOT="${RUNTIME_PROC_ROOT:-/proc}"

CACHE_DIR="$APP_DIR/data_cache"
SERVICE_UNITS=(
  tradingbot-api.service
  tradingbot-bg.service
  tradingbot.service
  tradingbot-frontend.service
)

runtime_fail() {
  echo "[deploy] Unsafe runtime state: $*" >&2
  return 1
}

metadata_for() {
  "$RUNTIME_STAT_BIN" -c '%F|%d|%h|%u|%g|%a' -- "$1" 2>/dev/null
}

require_runtime_tools() {
  local tool
  for tool in \
    "$RUNTIME_SYSTEMCTL_BIN" "$RUNTIME_PGREP_BIN" "$RUNTIME_ID_BIN" \
    "$RUNTIME_STAT_BIN" "$RUNTIME_FIND_BIN" "$RUNTIME_MOUNTPOINT_BIN" \
    "$RUNTIME_CHOWN_BIN" "$RUNTIME_CHMOD_BIN" "$RUNTIME_SETFACL_BIN" \
    "$RUNTIME_INSTALL_BIN"; do
    if [ ! -x "$tool" ]; then
      runtime_fail "required runtime tool is missing or not executable: $tool"
      return 1
    fi
  done
}

query_runtime_unit_state() {
  local unit="$1" context="$2" load_state="" active_state=""

  if ! load_state="$("$RUNTIME_SYSTEMCTL_BIN" show \
      --property=LoadState --value "$unit" 2>/dev/null)"; then
    runtime_fail "unit state query failed ($context, LoadState): $unit"
    return 1
  fi
  load_state="${load_state%$'\r'}"
  case "$load_state" in
    loaded) ;;
    not-found)
      RUNTIME_UNIT_LOAD_STATE="$load_state"
      RUNTIME_UNIT_ACTIVE_STATE=""
      return 0
      ;;
    *)
      runtime_fail "unexpected LoadState ($context): $unit (${load_state:-missing})"
      return 1
      ;;
  esac

  if ! active_state="$("$RUNTIME_SYSTEMCTL_BIN" show \
      --property=ActiveState --value "$unit" 2>/dev/null)"; then
    runtime_fail "unit state query failed ($context, ActiveState): $unit"
    return 1
  fi
  active_state="${active_state%$'\r'}"
  RUNTIME_UNIT_LOAD_STATE="$load_state"
  RUNTIME_UNIT_ACTIVE_STATE="$active_state"
}

runtime_pid_is_current_or_ancestor() {
  local candidate="$1" current="$$" parent="" key="" value="" rest=""

  while [[ "$current" =~ ^[0-9]+$ ]] && [ "$current" -gt 0 ]; do
    [ "$current" = "$candidate" ] && return 0
    parent=""
    [ -r "$RUNTIME_PROC_ROOT/$current/status" ] || break
    while read -r key value rest; do
      if [ "$key" = "PPid:" ]; then
        parent="$value"
        break
      fi
    done < "$RUNTIME_PROC_ROOT/$current/status"
    [ -n "$parent" ] || break
    current="$parent"
  done
  return 1
}

assert_no_service_user_processes() {
  local service_uid="$1" context="$2" matches="" rc=0

  if matches="$("$RUNTIME_PGREP_BIN" -u "$service_uid" 2>/dev/null)"; then
    runtime_fail "service-user process remains $context: $SERVICE_USER"
    return 1
  else
    rc=$?
  fi
  if [ "$rc" -ne 1 ]; then
    runtime_fail "service-user process query failed $context (rc=$rc): $SERVICE_USER"
    return 1
  fi
}

assert_no_legacy_root_processes() {
  local context="$1" matches="" rc=0 pid=""
  local pattern="/usr/local/sbin/alpha-station-auto-update|$APP_DIR/deploy/(auto_update|safe_deploy)\\.sh|/tmp/alpha-safe-deploy"

  if matches="$("$RUNTIME_PGREP_BIN" -f -- "$pattern" 2>/dev/null)"; then
    rc=0
  else
    rc=$?
  fi
  case "$rc" in
    1) return 0 ;;
    0) ;;
    *)
      runtime_fail "root process query failed $context (rc=$rc)"
      return 1
      ;;
  esac

  while IFS= read -r pid; do
    [ -n "$pid" ] || continue
    if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
      runtime_fail "root process query failed $context (invalid pid: $pid)"
      return 1
    fi
    if ! runtime_pid_is_current_or_ancestor "$pid"; then
      runtime_fail "legacy root process remains $context (pid=$pid)"
      return 1
    fi
  done <<< "$matches"
}

assert_runtime_processes_quiesced() {
  local service_uid="$1" context="$2"
  assert_no_service_user_processes "$service_uid" "$context" || return 1
  assert_no_legacy_root_processes "$context" || return 1
}

quiesce_service_user() {
  local unit service_uid
  if ! service_uid="$("$RUNTIME_ID_BIN" -u "$SERVICE_USER" 2>/dev/null)" \
    || [[ ! "$service_uid" =~ ^[0-9]+$ ]]; then
    runtime_fail "dedicated service user is missing: $SERVICE_USER"
    return 1
  fi

  for unit in "${SERVICE_UNITS[@]}"; do
    query_runtime_unit_state "$unit" "before stop" || return 1
    if [ "$RUNTIME_UNIT_LOAD_STATE" = "not-found" ]; then
      continue
    fi
    case "$RUNTIME_UNIT_ACTIVE_STATE" in
      active|inactive|failed) ;;
      *)
        runtime_fail "unit is not safely inactive/active before stop: $unit (ActiveState=${RUNTIME_UNIT_ACTIVE_STATE:-missing})"
        return 1
        ;;
    esac
    if ! "$RUNTIME_SYSTEMCTL_BIN" stop "$unit" >/dev/null 2>&1; then
      runtime_fail "service stop failed: $unit"
      return 1
    fi
    query_runtime_unit_state "$unit" "after stop" || return 1
    if [ "$RUNTIME_UNIT_LOAD_STATE" != "loaded" ]; then
      runtime_fail "unexpected LoadState after stop: $unit (${RUNTIME_UNIT_LOAD_STATE:-missing})"
      return 1
    fi
    case "$RUNTIME_UNIT_ACTIVE_STATE" in
      inactive|failed) ;;
      *)
        runtime_fail "service remains active or is not safely inactive after stop: $unit (ActiveState=${RUNTIME_UNIT_ACTIVE_STATE:-missing})"
        return 1
        ;;
    esac
  done
  if ! assert_runtime_processes_quiesced "$service_uid" "after unit stop"; then
      return 1
  fi
  printf '%s\n' "$service_uid"
}

validate_entry() {
  local path="$1" base_dev="$2" metadata kind dev links owner group mode
  if ! metadata="$(metadata_for "$path")"; then
    runtime_fail "entry disappeared during validation: $path"
    return 1
  fi
  IFS='|' read -r kind dev links owner group mode <<< "$metadata"
  case "$kind" in
    directory) ;;
    "regular file")
      if [ "$links" != "1" ]; then
        runtime_fail "regular file has a hard link: $path"
        return 1
      fi
      ;;
    "symbolic link")
      runtime_fail "symbolic link is forbidden in data_cache: $path"
      return 1
      ;;
    *)
      runtime_fail "special file is forbidden in data_cache ($kind): $path"
      return 1
      ;;
  esac
  if [ "$dev" != "$base_dev" ]; then
    runtime_fail "entry is on a different filesystem: $path"
    return 1
  fi
  if [ "$kind" = "directory" ] \
    && "$RUNTIME_MOUNTPOINT_BIN" -q -- "$path" 2>/dev/null; then
    runtime_fail "mount point is forbidden in data_cache: $path"
    return 1
  fi
}

main() {
  local service_uid base_metadata base_kind base_dev base_links base_owner base_group base_mode
  local path metadata kind dev links owner group mode path_list

  require_runtime_tools || return 1
  service_uid="$(quiesce_service_user)"

  if ! base_metadata="$(metadata_for "$CACHE_DIR")"; then
    "$RUNTIME_INSTALL_BIN" -d -m 0700 -o root -g root "$CACHE_DIR"
    base_metadata="$(metadata_for "$CACHE_DIR")" \
      || { runtime_fail "data_cache could not be created safely"; return 1; }
  fi
  IFS='|' read -r base_kind base_dev base_links base_owner base_group base_mode <<< "$base_metadata"
  if [ "$base_kind" != "directory" ]; then
    runtime_fail "data_cache itself must be a real directory, got $base_kind"
    return 1
  fi
  if "$RUNTIME_MOUNTPOINT_BIN" -q -- "$CACHE_DIR" 2>/dev/null; then
    runtime_fail "data_cache itself must not be a mount point"
    return 1
  fi

  # APP_DIR is root-controlled, so the service user cannot rename data_cache.
  # Lock its traversal first; after all service processes are gone this closes
  # the race window before inspecting descendants.
  "$RUNTIME_CHOWN_BIN" --no-dereference root:root "$CACHE_DIR"
  "$RUNTIME_CHMOD_BIN" 0700 "$CACHE_DIR"
  assert_runtime_processes_quiesced "$service_uid" \
    "after data_cache lock" || return 1

  [ -n "$RUNTIME_WORK_DIR" ] \
    || { runtime_fail "root-private RUNTIME_WORK_DIR is required"; return 1; }
  path_list="$RUNTIME_WORK_DIR/runtime-state-paths.nul"
  : > "$path_list"
  "$RUNTIME_FIND_BIN" "$CACHE_DIR" -xdev -mindepth 1 -print0 > "$path_list" \
    || { runtime_fail "data_cache tree cannot be enumerated"; return 1; }

  while IFS= read -r -d '' path; do
    validate_entry "$path" "$base_dev" || return 1
  done < "$path_list"
  assert_runtime_processes_quiesced "$service_uid" \
    "during data_cache validation" || return 1

  # Only after every existing entry has passed lstat/type/link/device/mount
  # validation may root create the persistent auth component. Shared /tmp and
  # HOME state live in systemd's StateDirectory under root-controlled /var/lib;
  # data_cache/runtime is deliberately not a privileged bind source.
  "$RUNTIME_INSTALL_BIN" -d -m 0700 -o root -g root "$CACHE_DIR/auth"
  : > "$path_list"
  "$RUNTIME_FIND_BIN" "$CACHE_DIR" -xdev -mindepth 1 -print0 > "$path_list" \
    || { runtime_fail "data_cache tree cannot be re-enumerated"; return 1; }
  while IFS= read -r -d '' path; do
    validate_entry "$path" "$base_dev" || return 1
  done < "$path_list"

  # The tree is now immutable to the stopped service account, single-filesystem,
  # and contains only real directories plus single-link regular files.
  while IFS= read -r -d '' path; do
    metadata="$(metadata_for "$path")" \
      || { runtime_fail "entry disappeared before ownership change: $path"; return 1; }
    IFS='|' read -r kind dev links owner group mode <<< "$metadata"
    if [ "$kind" = "directory" ]; then
      "$RUNTIME_SETFACL_BIN" -b -k -- "$path"
      "$RUNTIME_CHMOD_BIN" 0700 "$path"
    else
      "$RUNTIME_SETFACL_BIN" -b -- "$path"
      "$RUNTIME_CHMOD_BIN" 0600 "$path"
    fi
    "$RUNTIME_CHOWN_BIN" --no-dereference "$SERVICE_USER:$SERVICE_USER" "$path"
  done < "$path_list"

  "$RUNTIME_SETFACL_BIN" -b -k -- "$CACHE_DIR"
  "$RUNTIME_CHOWN_BIN" --no-dereference "$SERVICE_USER:$SERVICE_USER" "$CACHE_DIR"
  "$RUNTIME_CHMOD_BIN" 0750 "$CACHE_DIR"
  echo "[deploy] Runtime state validated; dedicated service user remains quiesced."
}

main "$@"
