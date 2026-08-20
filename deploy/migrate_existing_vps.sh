#!/usr/bin/env bash
# One-time in-place migration for the known Alpha Station VPS. This script
# deliberately does not call install.sh and never writes nginx/KeyHero state.
#
# Bootstrap from a reviewed, full 40-character origin/main revision:
#   EXPECTED_REVISION='<FULL_SHA>'
#   EXPECTED_HOME_SECRET_SHA256='<64_LOWERCASE_HEX_FROM_SERVER_EVIDENCE>'
#   source_dir="$(mktemp -d /root/alpha-migration-source.XXXXXX)"
#   git clone --single-branch --branch main \
#     https://github.com/xantharu123-png/AlphaStation.git "$source_dir"
#   test "$(git -C "$source_dir" rev-parse HEAD)" = "$EXPECTED_REVISION"
#   EXPECTED_REVISION="$EXPECTED_REVISION" \
#     EXPECTED_HOME_SECRET_SHA256="$EXPECTED_HOME_SECRET_SHA256" \
#     /bin/bash "$source_dir/deploy/migrate_existing_vps.sh"
set -Eeuo pipefail
umask 077
export PATH=/usr/sbin:/usr/bin:/sbin:/bin
export LC_ALL=C

EXPECTED_REVISION="${EXPECTED_REVISION:-}"
EXPECTED_HOME_SECRET_SHA256="${EXPECTED_HOME_SECRET_SHA256:-}"
ORIGIN='https://github.com/xantharu123-png/AlphaStation.git'
APP='/home/tradingbot/app'
TRUSTED_HOME='/home/tradingbot'
GLOBAL_SECRET='/home/tradingbot/.streamlit/secrets.toml'
GLOBAL_SECRET_DIR='/home/tradingbot/.streamlit'
RUNTIME_HOME='/var/lib/alpha-station-runtime'
RUNTIME_SECRET="$RUNTIME_HOME/.streamlit/secrets.toml"
CRON_FILE='/etc/cron.d/alpha-station-auto-update'
LAUNCHER='/usr/local/sbin/alpha-station-auto-update'
LOG_FILE='/var/log/alpha_autoupdate.log'
MIN_FREE_KIB=$((8 * 1024 * 1024))
MAX_ROLLBACK_FRONTEND_BYTES=$((512 * 1024 * 1024))
MAX_ROLLBACK_FRONTEND_ENTRIES=20000
MAINTENANCE_TZ='Europe/Zurich'
SCRIPT_PATH="$(readlink -f -- "${BASH_SOURCE[0]}")"
CONTROL_REPO=''

die() {
  echo "ABBRUCH: $*" >&2
  exit 1
}

is_real() {
  [ -e "$1" ] && [ ! -L "$1" ]
}

is_not_mountpoint() {
  local path="$1" rc=0
  if mountpoint -q -- "$path"; then
    echo "Pfad ist ein Mountpoint: $path" >&2
    return 1
  else
    rc=$?
  fi
  if [ "$rc" != 32 ]; then
    echo "Mountpoint-Abfrage fehlgeschlagen (rc=$rc): $path" >&2
    return 1
  fi
  return 0
}

require_safe_root_file() {
  local path="$1" metadata uid mode links
  is_real "$path" && [ -f "$path" ] || die "keine reale Datei: $path"
  metadata="$(stat -c '%u %a %h' -- "$path")"
  read -r uid mode links <<< "$metadata"
  [ "$uid" = 0 ] && [ "$links" = 1 ] \
    || die "unsichere Datei-Metadaten: $path"
  [[ "$mode" =~ ^[0-7]+$ ]] \
    || die "ungueltiger Dateimodus: $path"
  (( (8#$mode & 8#22) == 0 )) \
    || die "Datei ist fuer Gruppe/Andere schreibbar: $path"
}

safe_root_file_matches_digest() {
  local path="$1" expected_digest="$2" metadata uid mode links digest_line digest
  is_real "$path" && [ -f "$path" ] || return 1
  metadata="$(stat -c '%u %a %h' -- "$path")" || return 1
  read -r uid mode links <<< "$metadata"
  [ "$uid" = 0 ] && [ "$links" = 1 ] && [[ "$mode" =~ ^[0-7]+$ ]] \
    && (( (8#$mode & 8#22) == 0 )) || return 1
  digest_line="$(sha256sum -- "$path")" || return 1
  digest="${digest_line%% *}"
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]] && [ "$digest" = "$expected_digest" ]
}

require_safe_root_dir() {
  local path="$1" metadata uid mode
  is_real "$path" && [ -d "$path" ] || die "kein reales Verzeichnis: $path"
  metadata="$(stat -c '%u %a' -- "$path")"
  read -r uid mode <<< "$metadata"
  [ "$uid" = 0 ] || die "Verzeichnis ist nicht root-eigen: $path"
  [[ "$mode" =~ ^[0-7]+$ ]] || die "ungueltiger Verzeichnismodus: $path"
  (( (8#$mode & 8#22) == 0 )) \
    || die "Verzeichnis ist fuer Gruppe/Andere schreibbar: $path"
}

trusted_home_ancestor_contract() {
  local path metadata device_inode uid gid mode canonical
  for path in / /home "$TRUSTED_HOME"; do
    is_real "$path" && [ -d "$path" ] || return 1
    canonical="$(readlink -f -- "$path")" || return 1
    [ "$canonical" = "$path" ] || return 1
    metadata="$(stat -c '%d:%i %u %g %a' -- "$path")" || return 1
    read -r device_inode uid gid mode <<< "$metadata"
    [ "$uid" = 0 ] && [[ "$mode" =~ ^[0-7]+$ ]] \
      && (( (8#$mode & 8#22) == 0 )) \
      || return 1
    printf '%s|%s|%s|%s|%s\n' \
      "$path" "$device_inode" "$uid" "$gid" "$mode"
  done
}

frontend_tree_is_safe() {
  local root="$1" require_root_owned="$2" list_file root_device entry
  local metadata device links size uid mode count=0 total_bytes=0 rc=0
  is_real "$root" && [ -d "$root" ] && is_not_mountpoint "$root" \
    || return 1
  root_device="$(stat -c %d -- "$root")" || return 1
  [[ "$root_device" =~ ^[0-9]+$ ]] || return 1
  if [ "$require_root_owned" = 1 ]; then
    metadata="$(stat -c '%u %a' -- "$root")" || return 1
    read -r uid mode <<< "$metadata"
    [ "$uid" = 0 ] && [[ "$mode" =~ ^[0-7]+$ ]] \
      && (( (8#$mode & 8#22) == 0 )) || return 1
  fi
  list_file="$(mktemp /root/alpha-frontend-tree.XXXXXX)" || return 1
  find "$root" -xdev -mindepth 1 -print0 > "$list_file" || rc=1
  while [ "$rc" = 0 ] && IFS= read -r -d '' entry; do
    count=$((count + 1))
    if (( count > MAX_ROLLBACK_FRONTEND_ENTRIES )); then
      rc=1
      break
    fi
    is_real "$entry" || {
      rc=1
      break
    }
    device="$(stat -c %d -- "$entry")" || {
      rc=1
      break
    }
    [ "$device" = "$root_device" ] || {
      rc=1
      break
    }
    if [ -d "$entry" ]; then
      is_not_mountpoint "$entry" || {
        rc=1
        break
      }
      metadata="$(stat -c '%u %a' -- "$entry")" || {
        rc=1
        break
      }
      read -r uid mode <<< "$metadata"
    elif [ -f "$entry" ]; then
      metadata="$(stat -c '%h %s %u %a' -- "$entry")" || {
        rc=1
        break
      }
      read -r links size uid mode <<< "$metadata"
      [ "$links" = 1 ] && [[ "$size" =~ ^[0-9]+$ ]] || {
        rc=1
        break
      }
      total_bytes=$((total_bytes + size))
      if (( total_bytes > MAX_ROLLBACK_FRONTEND_BYTES )); then
        rc=1
        break
      fi
    else
      rc=1
      break
    fi
    if [ "$require_root_owned" = 1 ]; then
      [ "$uid" = 0 ] && [[ "$mode" =~ ^[0-7]+$ ]] \
        && (( (8#$mode & 8#22) == 0 )) || {
          rc=1
          break
        }
    fi
  done < "$list_file"
  rm -f -- "$list_file" || rc=1
  [ "$rc" = 0 ] && (( count > 0 ))
}

frontend_tree_manifest_digest() {
  python3 -I - "$1" <<'PY'
import hashlib
import os
import stat
import sys

root = os.fsencode(sys.argv[1])
root_stat = os.lstat(root)
if not stat.S_ISDIR(root_stat.st_mode):
    raise SystemExit("frontend root is not a directory")
root_device = root_stat.st_dev
manifest = hashlib.sha256()

for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    directories.sort()
    files.sort()
    for name in directories:
        path = os.path.join(current, name)
        value = os.lstat(path)
        if not stat.S_ISDIR(value.st_mode) or value.st_dev != root_device:
            raise SystemExit("unsafe frontend directory")
        relative = os.path.relpath(path, root)
        manifest.update(b"D\0" + relative + b"\0")
    for name in files:
        path = os.path.join(current, name)
        before_path = os.lstat(path)
        if (
            not stat.S_ISREG(before_path.st_mode)
            or before_path.st_dev != root_device
            or before_path.st_nlink != 1
        ):
            raise SystemExit("unsafe frontend file")
        fd = os.open(
            path,
            os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
        )
        try:
            before_fd = os.fstat(fd)
            if (
                not stat.S_ISREG(before_fd.st_mode)
                or before_fd.st_dev != root_device
                or before_fd.st_nlink != 1
            ):
                raise SystemExit("unsafe opened frontend file")
            payload_digest = hashlib.sha256()
            while True:
                chunk = os.read(fd, 1024 * 1024)
                if not chunk:
                    break
                payload_digest.update(chunk)
            after_fd = os.fstat(fd)
            after_path = os.lstat(path)
        finally:
            os.close(fd)
        fingerprint = lambda value: (
            value.st_dev,
            value.st_ino,
            value.st_mode,
            value.st_uid,
            value.st_gid,
            value.st_nlink,
            value.st_size,
            value.st_mtime_ns,
            value.st_ctime_ns,
        )
        if not (
            fingerprint(before_path)
            == fingerprint(before_fd)
            == fingerprint(after_fd)
            == fingerprint(after_path)
        ):
            raise SystemExit("frontend file changed while hashing")
        relative = os.path.relpath(path, root)
        manifest.update(
            b"F\0"
            + relative
            + b"\0"
            + str(before_fd.st_size).encode("ascii")
            + b"\0"
            + payload_digest.hexdigest().encode("ascii")
            + b"\0"
        )

print(manifest.hexdigest())
PY
}

unit_matches() {
  local unit="$1" expected_active="$2" expected_enabled="$3"
  local load_state active_state enabled_state
  load_state="$(systemctl show "$unit" -p LoadState --value)" || return 1
  active_state="$(systemctl show "$unit" -p ActiveState --value)" || return 1
  enabled_state="$(systemctl show "$unit" -p UnitFileState --value)" || return 1
  [ "$load_state" = loaded ] \
    && [ "$active_state" = "$expected_active" ] \
    && [ "$enabled_state" = "$expected_enabled" ]
}

unit_is() {
  local unit="$1" expected_active="$2" expected_enabled="$3"
  unit_matches "$unit" "$expected_active" "$expected_enabled" \
    || die "Unit-Zustand weicht ab: $unit (erwartet loaded/$expected_active/$expected_enabled)"
}

systemctl_property_value() {
  local unit="$1" property="$2" value
  value="$(systemctl show "$unit" -p "$property" --value)" || return 1
  printf '%s\n' "$value"
}

require_systemctl_property() {
  local unit="$1" property="$2" expected="$3" message="$4" actual
  actual="$(systemctl_property_value "$unit" "$property")" \
    || die "systemd-Abfrage fehlgeschlagen: $unit/$property"
  [ "$actual" = "$expected" ] || die "$message"
}

require_empty_systemctl_property() {
  require_systemctl_property "$1" "$2" '' "$3"
}

require_api_bg_execution_contract() {
  local unit
  for unit in tradingbot-api.service tradingbot-bg.service; do
    require_systemctl_property "$unit" User tradingbot \
      "unerwarteter Service-User: $unit"
    require_systemctl_property "$unit" Group tradingbot \
      "unerwartete Service-Gruppe: $unit"
    require_systemctl_property "$unit" WorkingDirectory "$APP" \
      "unerwartetes WorkingDirectory: $unit"
  done
}

normalized_exec_start_property() {
  local unit="$1" raw normalized
  raw="$(systemctl_property_value "$unit" ExecStart)" || return 1
  normalized="$(python3 -I - "$raw" <<'PY'
import re
import sys

raw = sys.argv[1]
if not raw or "\n" in raw or raw.count("{ path=") != 1:
    raise SystemExit("ExecStart is empty, multiline, or contains multiple commands")

marker = " ; start_time="
if marker in raw:
    prefix, volatile = raw.split(marker, 1)
    if not re.fullmatch(
        r"[^;]* ; stop_time=[^;]* ; pid=[0-9]+ ; code=[^;]* ; status=[^}]* }",
        volatile,
    ):
        raise SystemExit("unexpected volatile ExecStart suffix")
    raw = prefix + " }"

if not re.fullmatch(
    r"\{ path=[^ ;{}]+ ; argv\[\]=.+ ; ignore_errors=(?:yes|no) \}", raw
):
    raise SystemExit("unexpected ExecStart structure")
print(raw)
PY
)" || return 1
  printf '%s\n' "$normalized"
}

normalized_environment_property() {
  local unit="$1" raw normalized
  raw="$(systemctl_property_value "$unit" Environment)" || return 1
  normalized="$(python3 -I - "$raw" <<'PY'
import shlex
import sys

items = shlex.split(sys.argv[1], posix=True)
values = {}
for item in items:
    if "=" not in item:
        raise SystemExit("environment entry lacks equals sign")
    key, value = item.split("=", 1)
    if not key or key in values:
        raise SystemExit("environment contains empty or duplicate key")
    values[key] = value
print("\n".join(f"{key}={values[key]}" for key in sorted(values)))
PY
)" || return 1
  printf '%s\n' "$normalized"
}

unit_security_contract() {
  local unit="$1" property value
  printf 'unit=%q\n' "$unit"
  for property in LoadState NeedDaemonReload FragmentPath DropInPaths User Group \
      WorkingDirectory ExecStart Environment EnvironmentFiles ExecCondition \
      ExecStartPre ExecStartPost ExecReload ExecStop ExecStopPost RootDirectory \
      RootImage; do
    if [ "$property" = ExecStart ]; then
      value="$(normalized_exec_start_property "$unit")" || return 1
    elif [ "$property" = Environment ]; then
      value="$(normalized_environment_property "$unit")" || return 1
    else
      value="$(systemctl_property_value "$unit" "$property")" || return 1
    fi
    printf '%s=%q\n' "$property" "$value"
  done
}

unit_security_contract_matches() {
  local unit="$1" expected="$2" current
  current="$(unit_security_contract "$unit")" || return 1
  [ "$current" = "$expected" ]
}

unit_need_daemon_reload_is_no() {
  local value
  value="$(systemctl_property_value "$1" NeedDaemonReload)" || return 1
  [ "$value" = no ]
}

require_no_pending_unit_reload() {
  local unit
  for unit in tradingbot-api.service tradingbot-bg.service tradingbot.service \
      tradingbot-frontend.service cron.service; do
    unit_need_daemon_reload_is_no "$unit" \
      || die "systemd Disk/Loaded-State driftet: $unit/NeedDaemonReload"
  done
}

nginx_manifest_digest() {
  local entry relative target file_digest_line file_digest digest_line digest=''
  local rc=0 list_file manifest_file
  list_file="$(mktemp /root/alpha-nginx-list.XXXXXX)" || return 1
  manifest_file="$(mktemp /root/alpha-nginx-manifest.XXXXXX)" || {
    rm -f -- "$list_file"
    return 1
  }
  (
    cd /etc/nginx || exit 1
    find . -xdev \( -type f -o -type l \) -print0 | sort -z > "$list_file"
  ) || rc=1
  if [ "$rc" = 0 ]; then
    (
      cd /etc/nginx || exit 1
      while IFS= read -r -d '' entry; do
        relative="${entry#./}"
        if [ -L "$entry" ]; then
          target="$(readlink -- "$entry")" || exit 1
          printf 'L\0%s\0%s\0' "$relative" "$target" || exit 1
        elif [ -f "$entry" ]; then
          file_digest_line="$(sha256sum -- "$entry")" || exit 1
          file_digest="${file_digest_line%% *}"
          [[ "$file_digest" =~ ^[0-9a-f]{64}$ ]] || exit 1
          printf 'F\0%s\0%s\0' "$relative" "$file_digest" || exit 1
        else
          exit 1
        fi
      done < "$list_file"
    ) > "$manifest_file" || rc=1
  fi
  if [ "$rc" = 0 ]; then
    digest_line="$(sha256sum -- "$manifest_file")" || rc=1
    digest="${digest_line%% *}"
    [[ "$digest" =~ ^[0-9a-f]{64}$ ]] || rc=1
  fi
  rm -f -- "$list_file" "$manifest_file" || rc=1
  [ "$rc" = 0 ] || return 1
  printf '%s\n' "$digest"
}

git_control() {
  env HOME=/root GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c "safe.directory=$CONTROL_REPO" -c core.hooksPath=/dev/null \
    -C "$CONTROL_REPO" "$@"
}

git_final() {
  env HOME=/root GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    git -c "safe.directory=$final_clone" -c core.hooksPath=/dev/null \
    -C "$final_clone" "$@"
}

require_git_control_clean() {
  local status
  status="$(git_control status --porcelain=v1 --untracked-files=all)" \
    || die "Kontroll-Checkout-Status konnte nicht abgefragt werden"
  [ -z "$status" ] || die "Kontroll-Checkout ist nicht vollstaendig sauber"
}

require_git_final_clean() {
  local status ignored context="$1"
  status="$(git_final status --porcelain=v1 --untracked-files=all)" \
    || die "Final-Checkout-Statusabfrage fehlgeschlagen ($context)"
  ignored="$(git_final status --porcelain=v1 --untracked-files=all \
    --ignored=matching)" \
    || die "Final-Checkout-Ignored-Abfrage fehlgeschlagen ($context)"
  [ -z "$status" ] && [ -z "$ignored" ] \
    || die "Final-Clone ist nicht sauber/artefaktfrei ($context)"
}

pgrep_no_match() {
  local rc=0
  if pgrep "$@" >/dev/null 2>&1; then
    return 1
  else
    rc=$?
  fi
  if [ "$rc" != 1 ]; then
    echo "pgrep-Abfrage fehlgeschlagen (rc=$rc): $*" >&2
    return 2
  fi
  return 0
}

maintenance_window_open() {
  local weekday="$1" hhmm="$2"
  [[ "$weekday" =~ ^[1-7]$ ]] || return 1
  [[ "$hhmm" =~ ^[0-2][0-9][0-5][0-9]$ ]] || return 1
  [ "$((10#${hhmm:0:2}))" -le 23 ] || return 1
  case "$weekday" in
    1|2|3|4) [ "$((10#$hhmm))" -ge 2230 ] ;;
    5) return 1 ;;
    6|7) return 0 ;;
    *) return 1 ;;
  esac
}

require_maintenance_window() {
  local snapshot weekday hhmm stamp
  snapshot="$(TZ="$MAINTENANCE_TZ" date '+%u %H%M %Y-%m-%d_%H:%M_%Z')" \
    || die "Wartungszeit konnte nicht ermittelt werden"
  read -r weekday hhmm stamp <<< "$snapshot"
  maintenance_window_open "$weekday" "$hhmm" \
    || die "Wartungsfenster geschlossen ($stamp): erlaubt Sa/So ganztags oder Mo-Do ab 22:30; Freitag gesperrt"
  echo "Wartungsfenster offen: ${stamp//_/ }"
}

assert_root_crontab_empty() {
  local output
  output="$(crontab -l 2>/dev/null)" \
    || die "Root-crontab konnte nicht sicher abgefragt werden"
  [ -z "$output" ] || die "Root-crontab ist nicht leer"
}

assert_no_legacy_cron_bytes() {
  local needle='/home/tradingbot/app/deploy/auto_update.sh'
  local list_file source entry grep_rc rc=0
  list_file="$(mktemp /root/alpha-cron-scan.XXXXXX)" || return 1
  is_real /etc/crontab && [ -f /etc/crontab ] \
    || rc=1
  if [ "$rc" = 0 ]; then
    printf '%s\0' /etc/crontab > "$list_file" || rc=1
  fi
  for source in /etc/cron.d /var/spool/cron/crontabs; do
    if [ "$rc" != 0 ]; then
      break
    fi
    is_real "$source" && [ -d "$source" ] && is_not_mountpoint "$source" \
      || {
        rc=1
        break
      }
    find "$source" -xdev -mindepth 1 -maxdepth 1 -print0 \
      >> "$list_file" || {
        rc=1
        break
      }
  done
  while [ "$rc" = 0 ] && IFS= read -r -d '' entry; do
    is_real "$entry" && [ -f "$entry" ] || {
      echo "Unsicherer Cron-Scan-Eintrag: $entry" >&2
      rc=1
      break
    }
    if grep -Fq -- "$needle" "$entry"; then
      echo "Legacy-Root-Updater steht noch in Cron-Datei: $entry" >&2
      rc=1
      break
    else
      grep_rc=$?
    fi
    if [ "$grep_rc" != 1 ]; then
      echo "Cron-Datei konnte nicht sicher gelesen werden (rc=$grep_rc): $entry" >&2
      rc=1
      break
    fi
  done < "$list_file"
  rm -f -- "$list_file" || rc=1
  [ "$rc" = 0 ]
}

require_free_space() {
  local path="$1" free_kib
  free_kib="$(df -Pk "$path" | awk 'NR == 2 { print $4 }')" \
    || die "freier Speicher konnte fuer $path nicht abgefragt werden"
  [[ "$free_kib" =~ ^[0-9]+$ ]] \
    || die "freier Speicher konnte fuer $path nicht ermittelt werden"
  [ "$free_kib" -ge "$MIN_FREE_KIB" ] \
    || die "zu wenig freier Speicher auf $path: ${free_kib} KiB, mindestens ${MIN_FREE_KIB} KiB erforderlich"
  echo "Speicher-Gate OK ($path): ${free_kib} KiB frei"
}

health_identity() {
  local health_file="$1"
  python3 -I - "$health_file" <<'PY'
import json
import re
import sys
from pathlib import Path

payload = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
status = str(payload.get("status") or "").strip().lower()
revision = str(payload.get("revision") or "").strip().lower()
bundle = str(payload.get("frontend_bundle") or "").strip().lower()
if status != "healthy":
    raise SystemExit(f"health status is {status or 'missing'}")
if not re.fullmatch(r"[0-9a-f]{12}", revision):
    raise SystemExit(f"invalid revision: {revision or 'missing'}")
if not re.fullmatch(r"[0-9a-f]{12}", bundle):
    raise SystemExit(f"invalid frontend bundle: {bundle or 'missing'}")
print(revision, bundle)
PY
}

# Bindet eine erhaltene root:root/0600-Datei an genau denselben offenen
# regulären Inode und dessen SHA-256. O_NOFOLLOW/O_NONBLOCK verhindert, dass
# ein waehrend des Live-Preflights eingeschleuster Link/FIFO verfolgt bzw.
# blockierend gelesen wird; lstat/fstat vor und nach dem Hash schliessen einen
# einfachen Rename-Tausch aus.
capture_regular_file_contract() {
  local path="$1" expected_uid="$2" expected_gid="$3"
  local expected_mode="$4" owner_label="$5"
  python3 -I - "$path" "$expected_uid" "$expected_gid" \
    "$expected_mode" "$owner_label" <<'PY'
import hashlib
import os
import stat
import sys

path = sys.argv[1]
required_uid = int(sys.argv[2])
required_gid = int(sys.argv[3])
required_mode = int(sys.argv[4], 8)
owner_label = sys.argv[5]

def fingerprint(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_nlink,
        st.st_size,
        st.st_mtime_ns,
        st.st_ctime_ns,
    )

before_path = os.lstat(path)
flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK
fd = os.open(path, flags)
try:
    before_fd = os.fstat(fd)
    if fingerprint(before_path) != fingerprint(before_fd):
        raise RuntimeError("path changed before open")
    if not stat.S_ISREG(before_fd.st_mode):
        raise RuntimeError("not a regular file")
    if (
        before_fd.st_uid != required_uid
        or before_fd.st_gid != required_gid
        or stat.S_IMODE(before_fd.st_mode) != required_mode
        or before_fd.st_nlink != 1
    ):
        raise RuntimeError("metadata contract mismatch")

    digest = hashlib.sha256()
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)

    after_fd = os.fstat(fd)
    after_path = os.lstat(path)
    if fingerprint(before_fd) != fingerprint(after_fd):
        raise RuntimeError("opened inode changed while hashing")
    if fingerprint(after_fd) != fingerprint(after_path):
        raise RuntimeError("path changed after hashing")
finally:
    os.close(fd)

print(
    f"regular file|{owner_label}:{required_mode:o}|1|"
    f"{after_fd.st_dev}:{after_fd.st_ino}|{digest.hexdigest()}"
)
PY
}

capture_local_directory_contract() {
  local path="$1" expected_device="$2" contract
  is_not_mountpoint "$path" || return 1
  contract="$(python3 -I - "$path" "$expected_device" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
expected_device = int(sys.argv[2])

def fingerprint(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_gid,
        value.st_nlink,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )

before_path = os.lstat(path)
fd = os.open(
    path,
    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY,
)
try:
    before_fd = os.fstat(fd)
    after_fd = os.fstat(fd)
    after_path = os.lstat(path)
finally:
    os.close(fd)

if not stat.S_ISDIR(before_fd.st_mode):
    raise SystemExit("not a directory")
if before_fd.st_dev != expected_device:
    raise SystemExit("directory crosses APP filesystem")
if not (
    fingerprint(before_path)
    == fingerprint(before_fd)
    == fingerprint(after_fd)
    == fingerprint(after_path)
):
    raise SystemExit("directory path/inode changed")
print(
    f"{before_fd.st_dev}:{before_fd.st_ino}|{before_fd.st_uid}|"
    f"{before_fd.st_gid}|{stat.S_IMODE(before_fd.st_mode):o}"
)
PY
)" || return 1
  is_not_mountpoint "$path" || return 1
  printf '%s\n' "$contract"
}

root_controlled_directory_contract() {
  local contract="$1" device_inode uid gid mode
  IFS='|' read -r device_inode uid gid mode <<< "$contract"
  [[ "$device_inode" =~ ^[0-9]+:[0-9]+$ ]] \
    && [ "$uid" = 0 ] && [[ "$gid" =~ ^[0-9]+$ ]] \
    && [[ "$mode" =~ ^[0-7]+$ ]] \
    && (( (8#$mode & 8#22) == 0 ))
}

capture_preserved_file_contract() {
  capture_regular_file_contract "$1" 0 0 600 root:root
}

capture_global_secret_contract() {
  capture_regular_file_contract "$GLOBAL_SECRET" \
    "$(id -u tradingbot)" "$(id -g tradingbot)" 644 tradingbot:tradingbot
}

capture_runtime_secret_contract() {
  capture_regular_file_contract "$RUNTIME_SECRET" \
    0 "$(id -g tradingbot)" 640 root:tradingbot
}

# Bash haelt das ausgefuehrte Skript unter Linux auf FD 255 offen. pread liest
# genau diesen Inode ab Offset 0, ohne Bashs aktuellen Parser-Offset zu bewegen.
# Damit wird nicht nur ein spaeter austauschbarer Pfad, sondern der tatsaechlich
# ausgefuehrte Byte-Stream an den erwarteten Git-Blob gebunden.
executing_script_contract() {
  python3 -I - "$$" <<'PY'
import hashlib
import os
import stat
import sys

fd_path = f"/proc/{int(sys.argv[1])}/fd/255"
fd = os.open(fd_path, os.O_RDONLY | os.O_CLOEXEC)
try:
    before = os.fstat(fd)
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("Bash script FD is not a regular file")
    fingerprint = (
        before.st_dev,
        before.st_ino,
        before.st_mode,
        before.st_uid,
        before.st_gid,
        before.st_nlink,
        before.st_size,
        before.st_mtime_ns,
        before.st_ctime_ns,
    )
    chunks = []
    offset = 0
    while offset < before.st_size:
        chunk = os.pread(fd, min(1024 * 1024, before.st_size - offset), offset)
        if not chunk:
            raise RuntimeError("short read from Bash script FD")
        chunks.append(chunk)
        offset += len(chunk)
    payload = b"".join(chunks)
    after = os.fstat(fd)
    after_fingerprint = (
        after.st_dev,
        after.st_ino,
        after.st_mode,
        after.st_uid,
        after.st_gid,
        after.st_nlink,
        after.st_size,
        after.st_mtime_ns,
        after.st_ctime_ns,
    )
    if fingerprint != after_fingerprint:
        raise RuntimeError("Bash script FD changed while hashing")
finally:
    os.close(fd)

header = f"blob {len(payload)}\0".encode("ascii")
print(hashlib.sha1(header + payload).hexdigest(), f"{after.st_dev}:{after.st_ino}")
PY
}

[ "$(id -u)" = 0 ] || die "als root ausfuehren"
cd /root

for tool in git python3 systemctl stat find mountpoint mv cp install sha256sum \
            awk grep pgrep runuser curl cmp timeout sort xargs crontab readlink \
            dirname seq sleep tr date df du tar getent usermod loginctl cut \
            journalctl tail touch flock id chmod chown cat mktemp rm; do
  command -v "$tool" >/dev/null || die "Werkzeug fehlt: $tool"
done

[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]] \
  || die "EXPECTED_REVISION muss als voller 40-stelliger lowercase Git-SHA gesetzt sein"
[[ "$EXPECTED_HOME_SECRET_SHA256" =~ ^[0-9a-f]{64}$ ]] \
  || die "EXPECTED_HOME_SECRET_SHA256 muss als serverseitig belegter 64-stelliger lowercase SHA-256 gesetzt sein"
require_maintenance_window

CONTROL_REPO="$(git -C "$(dirname "$SCRIPT_PATH")" rev-parse --show-toplevel 2>/dev/null)" \
  || die "Migrationsskript laeuft nicht aus einem Git-Checkout"
case "$CONTROL_REPO" in
  /root/alpha-migration-source.*) ;;
  *) die "Kontroll-Checkout muss aus /root/alpha-migration-source.* laufen" ;;
esac
require_safe_root_dir "$CONTROL_REPO"
require_safe_root_dir "$CONTROL_REPO/.git"
require_safe_root_dir "$CONTROL_REPO/deploy"
require_safe_root_file "$SCRIPT_PATH"
[ "$SCRIPT_PATH" = "$CONTROL_REPO/deploy/migrate_existing_vps.sh" ] \
  || die "unerwarteter Skriptpfad: $SCRIPT_PATH"
git_control config --local core.hooksPath /dev/null
git_control fetch --force origin main
[ "$(git_control remote get-url origin)" = "$ORIGIN" ] \
  || die "Kontroll-Origin weicht ab"
[ "$(git_control rev-parse HEAD)" = "$EXPECTED_REVISION" ] \
  || die "Skript-Checkout entspricht nicht EXPECTED_REVISION"
[ "$(git_control rev-parse origin/main)" = "$EXPECTED_REVISION" ] \
  || die "origin/main entspricht nicht EXPECTED_REVISION"
expected_script_blob="$(git_control rev-parse \
  "$EXPECTED_REVISION:deploy/migrate_existing_vps.sh")"
read -r executing_script_blob executing_script_inode \
  <<< "$(executing_script_contract)" \
  || die "ausgefuehrter Bash-Skript-FD konnte nicht gebunden werden"
[ "$executing_script_inode" = "$(stat -c '%d:%i' -- "$SCRIPT_PATH")" ] \
  && [ "$executing_script_blob" = "$expected_script_blob" ] \
  && [ "$(git_control hash-object "$SCRIPT_PATH")" = "$expected_script_blob" ] \
  || die "ausgefuehrter Skript-FD/Pfad weicht vom erwarteten Git-Blob ab"
read -r executing_script_blob_after executing_script_inode_after \
  <<< "$(executing_script_contract)" \
  || die "ausgefuehrter Skript-FD driftete waehrend Selbstpruefung"
[ "$executing_script_blob_after:$executing_script_inode_after" = \
  "$executing_script_blob:$executing_script_inode" ] \
  && [ "$executing_script_inode_after" = \
    "$(stat -c '%d:%i' -- "$SCRIPT_PATH")" ] \
  || die "Skript-FD/Pfad wurde waehrend Selbstpruefung ausgetauscht"
require_git_control_clean

require_free_space /root
require_free_space "$TRUSTED_HOME"

# Die folgenden Schranken muessen vor Clone/Tests und erst recht vor Downtime
# exakt den zuvor erhobenen Live-Zustand wiederfinden.
trusted_home_ancestors="$(trusted_home_ancestor_contract)" \
  || die "Root-Vertrauenskette / -> /home -> $TRUSTED_HOME ist unsicher"
require_safe_root_dir "$TRUSTED_HOME"
is_real "$APP" && [ -d "$APP" ] && ! mountpoint -q "$APP" \
  || die "APP ist Symlink/Mount/fehlt"
original_app_device="$(stat -c %d -- "$APP")" \
  || die "APP-Dateisystem konnte nicht gebunden werden"
[[ "$original_app_device" =~ ^[0-9]+$ ]] \
  || die "APP-Dateisystem-ID ist ungueltig"
original_app_dir_contract="$(capture_local_directory_contract \
  "$APP" "$original_app_device")" \
  || die "urspruenglicher APP-Inode konnte nicht stabil gebunden werden"
app_streamlit_dir_contract="$(capture_local_directory_contract \
  "$APP/.streamlit" "$original_app_device")" \
  || die "App-.streamlit-Parent ist kein stabiles lokales reales Verzeichnis"

require_safe_root_file "$APP/.env"
[ "$(stat -c %a -- "$APP/.env")" = 600 ] || die ".env-Modus ist nicht 600"
require_safe_root_file "$APP/.streamlit/secrets.toml"
[ "$(stat -c %a -- "$APP/.streamlit/secrets.toml")" = 600 ] \
  || die "App-secrets-Modus ist nicht 600"
env_preserved_contract="$(capture_preserved_file_contract "$APP/.env")" \
  || die ".env konnte nicht stabil an Inode/Hash gebunden werden"
app_secret_preserved_contract="$(capture_preserved_file_contract \
  "$APP/.streamlit/secrets.toml")" \
  || die "App-secret konnte nicht stabil an Inode/Hash gebunden werden"

global_secret_contract="$(capture_global_secret_contract)" \
  || die "Home-secrets ist kein stabiles regulaeres Single-Link-File"
[ "${global_secret_contract##*|}" = "$EXPECTED_HOME_SECRET_SHA256" ] \
  || die "Home-secrets hat sich seit der Evidenz geaendert"
is_real "$GLOBAL_SECRET_DIR" && [ -d "$GLOBAL_SECRET_DIR" ] \
  && is_not_mountpoint "$GLOBAL_SECRET_DIR" \
  && [ "$(readlink -f -- "$GLOBAL_SECRET_DIR")" = "$GLOBAL_SECRET_DIR" ] \
  || die "Home-secrets-Parent ist kein reales lokales Verzeichnis"
global_secret_dir_identity="$(stat -c '%d:%i:%U:%G:%a' -- "$GLOBAL_SECRET_DIR")"

is_real "$APP/data_cache" && [ -d "$APP/data_cache" ] \
  && ! mountpoint -q "$APP/data_cache" \
  || die "data_cache ist Symlink/Mount/fehlt"

[ ! -e "$RUNTIME_SECRET" ] && [ ! -L "$RUNTIME_SECRET" ] \
  || die "Runtime-secret existiert inzwischen; nicht ueberschreiben"
[ ! -e "$CRON_FILE" ] && [ ! -L "$CRON_FILE" ] \
  || die "Cron-Datei existiert inzwischen; Zustand neu pruefen"

# Nur den nicht geheimen Modusschalter lesen; .env wird niemals gesourct.
strict="$(awk -F= '
  $0 !~ /^[[:space:]]*#/ && $1 == "COMMERCIAL_STRICT_MODE" {
    value=substr($0,index($0,"=")+1)
  }
  END {
    gsub(/^[[:space:]]+|[[:space:]]+$/,"",value)
    gsub(/^"|"$/,"",value)
    print tolower(value)
  }' "$APP/.env")"
case "$strict" in
  ""|0|false|no|off) ;;
  *) die "COMMERCIAL_STRICT_MODE ist aktiv; Legacy-Frontend darf nicht erzwungen werden" ;;
esac

unit_is tradingbot-api.service active enabled
unit_is tradingbot-bg.service active enabled
unit_is tradingbot.service inactive disabled
unit_is tradingbot-frontend.service active enabled
unit_is cron.service active enabled
require_api_bg_execution_contract
require_no_pending_unit_reload
api_unit_security_before="$(unit_security_contract tradingbot-api.service)" \
  || die "API-Unit-Sicherheitsvertrag konnte nicht gebunden werden"
bg_unit_security_before="$(unit_security_contract tradingbot-bg.service)" \
  || die "BG-Unit-Sicherheitsvertrag konnte nicht gebunden werden"
streamlit_unit_security_before="$(unit_security_contract tradingbot.service)" \
  || die "Legacy-Streamlit-Unit-Sicherheitsvertrag konnte nicht gebunden werden"
frontend_unit_security_before="$(unit_security_contract \
  tradingbot-frontend.service)" \
  || die "Legacy-Frontend-Unit-Sicherheitsvertrag konnte nicht gebunden werden"
cron_unit_security_before="$(unit_security_contract cron.service)" \
  || die "Cron-Unit-Sicherheitsvertrag konnte nicht gebunden werden"

api_pid="$(systemctl_property_value tradingbot-api.service MainPID)" \
  || die "API MainPID-Abfrage fehlgeschlagen"
[[ "$api_pid" =~ ^[1-9][0-9]*$ ]] || die "API MainPID fehlt"
api_home="$(tr '\0' '\n' < "/proc/$api_pid/environ" \
  | awk -F= '$1 == "HOME" { print substr($0,index($0,"=")+1); exit }')"
[ "$api_home" = /home/tradingbot ] \
  || die "effektives API-HOME hat sich geaendert: ${api_home:-fehlt}"

require_systemctl_property tradingbot-api.service FragmentPath \
  /etc/systemd/system/tradingbot-api.service "API FragmentPath unerwartet"
require_systemctl_property tradingbot-bg.service FragmentPath \
  /etc/systemd/system/tradingbot-bg.service "BG FragmentPath unerwartet"
require_systemctl_property tradingbot-frontend.service FragmentPath \
  /etc/systemd/system/tradingbot-frontend.service "Frontend FragmentPath unerwartet"
require_systemctl_property tradingbot.service FragmentPath \
  /etc/systemd/system/tradingbot.service "Streamlit FragmentPath unerwartet"
cron_fragment_path="$(systemctl_property_value cron.service FragmentPath)" \
  || die "Cron FragmentPath-Abfrage fehlgeschlagen"
case "$cron_fragment_path" in
  /usr/lib/systemd/system/cron.service|/lib/systemd/system/cron.service) ;;
  *) die "Cron FragmentPath unerwartet: $cron_fragment_path" ;;
esac
require_systemctl_property tradingbot-api.service DropInPaths \
  /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
  "API Drop-in unerwartet"
require_empty_systemctl_property tradingbot-bg.service DropInPaths \
  "unerwartete BG-Drop-ins"
require_empty_systemctl_property tradingbot-frontend.service DropInPaths \
  "unerwartete Frontend-Drop-ins"
require_systemctl_property tradingbot-frontend.service User root \
  "Legacy-Frontend laeuft nicht als erwarteter root-Dienst"
require_systemctl_property tradingbot-frontend.service WorkingDirectory \
  /home/tradingbot/app/frontend "Legacy-Frontend WorkingDirectory weicht ab"
frontend_exec="$(systemctl_property_value \
  tradingbot-frontend.service ExecStart)" \
  || die "Legacy-Frontend ExecStart-Abfrage fehlgeschlagen"
case "$frontend_exec" in
  *"/usr/bin/python3 -m http.server 3000"*) ;;
  *) die "Legacy-Frontend ExecStart weicht ab" ;;
esac
require_empty_systemctl_property tradingbot-frontend.service EnvironmentFiles \
  "Legacy-Frontend hat unerwartete EnvironmentFiles"

for unit in tradingbot-api.service tradingbot-bg.service tradingbot-frontend.service; do
  for property in ExecStartPre ExecStartPost ExecStop ExecStopPost; do
    require_empty_systemctl_property "$unit" "$property" \
      "unerwarteter Hook: $unit/$property"
  done
done

for path in \
  /etc/systemd/system/tradingbot-api.service \
  /etc/systemd/system/tradingbot-bg.service \
  /etc/systemd/system/tradingbot.service \
  /etc/systemd/system/tradingbot-frontend.service \
  "$cron_fragment_path" \
  /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf; do
  require_safe_root_file "$path"
done

assert_root_crontab_empty
assert_no_legacy_cron_bytes \
  || die "Cron-Quellen konnten nicht fail-closed als Legacy-frei belegt werden"
pgrep_no_match -f -- \
  '(/usr/local/sbin/alpha-station-auto-update|/home/tradingbot/app/deploy/(auto_update|safe_deploy)\.sh|/tmp/alpha-safe-deploy)' \
  || die "Updater/Deploy laeuft bereits"

if [ -e "$LOG_FILE" ] || [ -L "$LOG_FILE" ]; then
  require_safe_root_file "$LOG_FILE"
fi
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  require_safe_root_file "$LAUNCHER"
fi
if [ -e /run/alpha-station ] || [ -L /run/alpha-station ]; then
  require_safe_root_dir /run/alpha-station
fi

systemctl is-active --quiet nginx || die "nginx ist nicht aktiv"
nginx_pid="$(systemctl_property_value nginx.service MainPID)" \
  || die "nginx MainPID-Abfrage fehlgeschlagen"
[[ "$nginx_pid" =~ ^[1-9][0-9]*$ ]] || die "nginx MainPID fehlt"
nginx_before="$(nginx_manifest_digest)" \
  || die "nginx-Manifest konnte vor Backup/Preflight nicht ermittelt werden"

backup="$(mktemp -d /root/alpha-inplace-backup.XXXXXX)"
preflight_source="$(mktemp -d "$backup/preflight-source.XXXXXX")"
final_clone="$(mktemp -d "$TRUSTED_HOME/.alpha-final.XXXXXX")"
quarantine="$(mktemp -d "$TRUSTED_HOME/.alpha-quarantine.XXXXXX")"
rollback_frontend_snapshot="$backup/rollback-frontend-snapshot"
rollback_frontend_unit="$backup/tradingbot-frontend-rollback-safe.service"
expected_api_override="$backup/legacy-direct-frontend.expected.conf"
chmod 0700 "$backup" "$preflight_source" "$final_clone" "$quarantine"
quarantine_dir_contract="$(capture_local_directory_contract \
  "$quarantine" "$original_app_device")" \
  || die "Quarantaene-Inode konnte nicht stabil gebunden werden"
root_controlled_directory_contract "$quarantine_dir_contract" \
  || die "Quarantaene ist nicht root-kontrolliert"
printf '%s\n' "$env_preserved_contract" \
  > "$backup/env.inode-sha256.before"
printf '%s\n' "$app_secret_preserved_contract" \
  > "$backup/app-secret.inode-sha256.before"
printf '%s\n' "$api_unit_security_before" > "$backup/api-unit.loaded.before"
printf '%s\n' "$bg_unit_security_before" > "$backup/bg-unit.loaded.before"
printf '%s\n' "$streamlit_unit_security_before" \
  > "$backup/streamlit-unit.loaded.before"
printf '%s\n' "$frontend_unit_security_before" \
  > "$backup/frontend-unit.loaded.before"
printf '%s\n' "$cron_unit_security_before" \
  > "$backup/cron-unit.loaded.before"
printf '%s\n' \
  '[Unit]' \
  'Description=Alpha Station rollback frontend from root-owned snapshot' \
  'After=network.target' \
  '' \
  '[Service]' \
  'Type=simple' \
  'User=root' \
  'Group=root' \
  'WorkingDirectory=/' \
  "ExecStart=/usr/bin/python3 -I -S -m http.server 3000 --bind 0.0.0.0 --directory $rollback_frontend_snapshot" \
  'Restart=always' \
  'RestartSec=5' \
  'NoNewPrivileges=true' \
  'PrivateTmp=true' \
  'ProtectSystem=strict' \
  'ProtectHome=read-only' \
  'ProtectKernelTunables=true' \
  'ProtectKernelModules=true' \
  'ProtectControlGroups=true' \
  'RestrictSUIDSGID=true' \
  'LockPersonality=true' \
  'CapabilityBoundingSet=' \
  'AmbientCapabilities=' \
  '' \
  '[Install]' \
  'WantedBy=multi-user.target' \
  > "$rollback_frontend_unit"
chmod 0644 "$rollback_frontend_unit"
require_safe_root_file "$rollback_frontend_unit"
rollback_frontend_unit_digest_line="$(sha256sum -- "$rollback_frontend_unit")"
rollback_frontend_unit_digest="${rollback_frontend_unit_digest_line%% *}"
[[ "$rollback_frontend_unit_digest" =~ ^[0-9a-f]{64}$ ]] \
  || die "Rollback-Frontend-Unit-Digest ist ungueltig"
printf '%s\n' \
  '[Service]' \
  'Environment="API_BIND_HOST=0.0.0.0"' \
  'ExecStart=' \
  'ExecStart=/home/tradingbot/app/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000' \
  > "$expected_api_override"
chmod 0644 "$expected_api_override"
require_safe_root_file "$expected_api_override"
rollback_frontend_snapshot_ready=0
echo "Backup:          $backup"
echo "Preflight:       $preflight_source"
echo "Finaler Clone:   $final_clone"
echo "Quarantaene:     $quarantine"

cp --preserve=all -- /etc/systemd/system/tradingbot-api.service \
  "$backup/api.service.before"
cp --preserve=all -- /etc/systemd/system/tradingbot-bg.service \
  "$backup/bg.service.before"
cp --preserve=all -- /etc/systemd/system/tradingbot-frontend.service \
  "$backup/frontend.service.before"
cp --preserve=all -- /etc/systemd/system/tradingbot.service \
  "$backup/streamlit.service.before"
cp --preserve=all -- "$cron_fragment_path" \
  "$backup/cron.service.before"
cp --preserve=all -- \
  /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
  "$backup/api-override.before"
crontab -l > "$backup/root-crontab.before"

old_service_shell="$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')"
[ -n "$old_service_shell" ] && [ -x "$old_service_shell" ] \
  || die "aktueller Login-Shell-Zustand von tradingbot ist nicht sicher ermittelbar"
old_linger="$(loginctl show-user tradingbot -p Linger --value)" \
  || die "Linger-Zustand von tradingbot konnte nicht gelesen werden"
case "$old_linger" in yes|no) ;; *) die "unerwarteter Linger-Zustand: $old_linger" ;; esac
printf '%s\n' "$old_service_shell" > "$backup/tradingbot-shell.before"
printf '%s\n' "$old_linger" > "$backup/tradingbot-linger.before"
home_secret_snapshot_ready=0

launcher_existed=0
if [ -e "$LAUNCHER" ] || [ -L "$LAUNCHER" ]; then
  cp --preserve=all -- "$LAUNCHER" "$backup/launcher.before"
  launcher_existed=1
fi

runtime_home_inode=''
runtime_streamlit_inode=''
# Der alte belegte Dienst nutzt HOME=/home/tradingbot. Ein bereits vorhandener
# neuer StateDirectory-Baum waere unbekannter, spaeter von API/BG mutierbarer
# Zustand und koennte ohne kompletten atomaren Snapshot nicht exakt
# zurueckgerollt werden. Deshalb ist fuer diese Einmalmigration der gesamte
# neue Runtime-Pfad (nicht nur secrets.toml) ein harter Abwesenheitsvertrag.
[ ! -e "$RUNTIME_HOME" ] && [ ! -L "$RUNTIME_HOME" ] \
  || die "Runtime-HOME existiert bereits; fuer exakten Rollback separat auditieren"

log_existed=0
if [ -e "$LOG_FILE" ] || [ -L "$LOG_FILE" ]; then
  cp --preserve=all -- "$LOG_FILE" "$backup/auto-update.log.before"
  log_existed=1
fi
lock_dir_existed=0
lock_file_existed=0
if [ -e /run/alpha-station ] || [ -L /run/alpha-station ]; then
  lock_dir_existed=1
  stat -c '%U:%G:%a' -- /run/alpha-station \
    > "$backup/lock-dir.metadata.before"
  if [ -e /run/alpha-station/auto-update.lock ] \
    || [ -L /run/alpha-station/auto-update.lock ]; then
    require_safe_root_file /run/alpha-station/auto-update.lock
    cp --preserve=all -- /run/alpha-station/auto-update.lock \
      "$backup/auto-update.lock.before"
    lock_file_existed=1
  fi
fi

# Der Volltest laeuft ausschliesslich in einem git-archive-Export. Dadurch
# bleibt der spaeter separat geklonte Produktionsbaum frei von Testartefakten.
git_control archive --format=tar "$EXPECTED_REVISION" \
  | tar -x -C "$preflight_source"
[ -f "$preflight_source/deploy/migrate_existing_vps.sh" ] \
  || die "Migrationsskript fehlt im gepinnten Archive"
cmp -s "$SCRIPT_PATH" "$preflight_source/deploy/migrate_existing_vps.sh" \
  || die "Archive-Skript stimmt nicht mit dem laufenden Skript ueberein"
archive_link="$(find "$preflight_source" -xdev -type l -print -quit)" \
  || die "Preflight-Archive-Symlinkabfrage fehlgeschlagen"
[ -z "$archive_link" ] || die "Symlink im Preflight-Archive: $archive_link"
archive_special="$(find "$preflight_source" -xdev -mindepth 1 \
  ! -type f ! -type d -print -quit)" \
  || die "Preflight-Archive-Typabfrage fehlgeschlagen"
[ -z "$archive_special" ] \
  || die "Spezialdatei im Preflight-Archive: $archive_special"
install -d -o root -g root -m 0700 "$backup/wheels" "$backup/pycache"
python3 -I -m venv "$preflight_source/venv"
"$preflight_source/venv/bin/python" -m pip wheel --disable-pip-version-check \
  --wheel-dir "$backup/wheels" -r "$preflight_source/requirements.txt"
"$preflight_source/venv/bin/python" -m pip install --disable-pip-version-check \
  --no-index --find-links "$backup/wheels" -r "$preflight_source/requirements.txt"

(
  cd "$preflight_source"
  bash -n deploy/safe_deploy.sh deploy/install.sh deploy/install_auto_update.sh \
    deploy/auto_update.sh deploy/health_check.sh deploy/runtime_state_guard.sh \
    deploy/verify_commercial_edge.sh deploy/migrate_existing_vps.sh
  PYTHONPYCACHEPREFIX="$backup/pycache" \
    "$preflight_source/venv/bin/python" -m compileall -q api.py bg_service.py modules
  "$preflight_source/venv/bin/python" scripts/verify_frontend_bundle.py
  # Zwei bestehende Metadaten-Tests fragen Git ab. Sie erhalten nur den
  # gepinnten Kontroll-Index und den Archive-Export als Worktree.
  GIT_DIR="$CONTROL_REPO/.git" GIT_WORK_TREE="$preflight_source" \
    GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null HOME=/root \
    PYTHONDONTWRITEBYTECODE=1 \
    "$preflight_source/venv/bin/python" -m pytest -q -p no:cacheprovider
)

# Erst nach gruenem Archive-Preflight entsteht der finale Checkout.
env -i HOME=/root PATH="$PATH" GIT_CONFIG_NOSYSTEM=1 \
  GIT_CONFIG_GLOBAL=/dev/null \
  git -c core.hooksPath=/dev/null clone --no-checkout \
  --single-branch --branch main "$ORIGIN" "$final_clone"
git_final config --local core.hooksPath /dev/null
git_final fetch --force origin main
[ "$(git_final remote get-url origin)" = "$ORIGIN" ] || die "Final-Origin weicht ab"
[ "$(git_final rev-parse origin/main)" = "$EXPECTED_REVISION" ] \
  || die "origin/main driftete vor Final-Clone"
git_final checkout --force -B main "$EXPECTED_REVISION"
git_final branch --set-upstream-to=origin/main main
[ "$(git_final rev-parse HEAD)" = "$EXPECTED_REVISION" ] \
  || die "finaler Checkout entspricht nicht EXPECTED_REVISION"
git_final fsck --full
[ ! -e "$final_clone/.gitmodules" ] || die "unerwartete Submodule"
require_git_final_clean "nach Checkout"
[ ! -e "$final_clone/venv" ] && [ ! -e "$final_clone/.venv" ] \
  && [ ! -e "$final_clone/.env" ] && [ ! -e "$final_clone/data_cache" ] \
  && [ ! -e "$final_clone/.streamlit/secrets.toml" ] \
  || die "finaler Clone enthaelt unerwartete Laufzeit-/Secret-Artefakte"
chmod -R a+rX,go-w "$final_clone"
chown -hR root:root "$final_clone"
unsafe_stage="$(find "$final_clone" -xdev \
  \( -type f -o -type d \) \( ! -uid 0 -o -perm /022 \) \
  -print -quit)" || die "Final-Clone-Permissionsabfrage fehlgeschlagen"
[ -z "$unsafe_stage" ] || die "unsicherer Final-Clone-Eintrag: $unsafe_stage"
special_stage="$(find "$final_clone" -xdev -mindepth 1 \
  ! -type f ! -type d ! -type l -print -quit)" \
  || die "Final-Clone-Typabfrage fehlgeschlagen"
[ -z "$special_stage" ] || die "unerwarteter Spezialdatei-Eintrag: $special_stage"
stage_link="$(find "$final_clone" -xdev -type l -print -quit)" \
  || die "Final-Clone-Symlinkabfrage fehlgeschlagen"
[ -z "$stage_link" ] || die "unerwarteter Symlink im finalen Clone: $stage_link"
require_git_final_clean "nach Ownership-/Typvalidierung"
for target_unit in tradingbot-api.service tradingbot-bg.service; do
  cp --preserve=all -- "$final_clone/deploy/$target_unit" \
    "$backup/expected-$target_unit"
  require_safe_root_file "$backup/expected-$target_unit"
  target_unit_metadata="$(stat -c '%F|%U:%G:%a|%h|%s' -- \
    "$final_clone/deploy/$target_unit")" \
    || die "Target-Unit-Metadatenabfrage fehlgeschlagen: $target_unit"
  snapshot_unit_metadata="$(stat -c '%F|%U:%G:%a|%h|%s' -- \
    "$backup/expected-$target_unit")" \
    || die "Target-Unit-Snapshot-Metadatenabfrage fehlgeschlagen: $target_unit"
  [ "$snapshot_unit_metadata" = \
    "regular file|root:root:644|1|${snapshot_unit_metadata##*|}" ] \
    || die "Target-Unit-Modus ist nicht 0644: $target_unit"
  [ "$target_unit_metadata" = "$snapshot_unit_metadata" ] \
    && cmp -s -- "$final_clone/deploy/$target_unit" \
      "$backup/expected-$target_unit" \
    || die "Target-Unit-Snapshot ist nicht byte-/metadatengleich: $target_unit"
done
new_app_dir_contract="$(capture_local_directory_contract \
  "$final_clone" "$original_app_device")" \
  || die "Final-Clone-APP-Inode ist nicht stabil/lokal/real"
root_controlled_directory_contract "$new_app_dir_contract" \
  || die "Final-Clone-APP-Inode ist nicht root-kontrolliert"
new_app_streamlit_dir_contract="$(capture_local_directory_contract \
  "$final_clone/.streamlit" "$original_app_device")" \
  || die "Final-Clone-.streamlit-Parent ist nicht stabil/lokal/real"
root_controlled_directory_contract "$new_app_streamlit_dir_contract" \
  || die "Final-Clone-.streamlit-Parent ist nicht root-kontrolliert"
echo "PRECHECK OK: Archive-Volltest, Wheels und separater sauberer Final-Clone."

# Letzter Drift-Check unmittelbar vor der Unterbrechung. Preflight kann lange
# dauern; Wartungsfenster, Speicher, Remote und Live-Vertrag werden neu belegt.
require_maintenance_window
require_free_space /root
require_free_space "$TRUSTED_HOME"
git_control fetch --force origin main
[ "$(git_control rev-parse HEAD)" = "$EXPECTED_REVISION" ] \
  && [ "$(git_control rev-parse origin/main)" = "$EXPECTED_REVISION" ] \
  || die "Kontroll-Checkout/Remote driftete waehrend Preflight"
git_final fetch --force origin main
[ "$(git_final rev-parse HEAD)" = "$EXPECTED_REVISION" ] \
  && [ "$(git_final rev-parse origin/main)" = "$EXPECTED_REVISION" ] \
  || die "Final-Checkout/Remote driftete waehrend Preflight"
require_git_final_clean "direkt vor Mutation"
current_new_app_contract="$(capture_local_directory_contract \
  "$final_clone" "$original_app_device")" \
  || die "Final-Clone-APP-Inode driftete vor Mutation"
[ "$current_new_app_contract" = "$new_app_dir_contract" ] \
  || die "Final-Clone-APP-Vertrag driftete vor Mutation"
current_quarantine_contract="$(capture_local_directory_contract \
  "$quarantine" "$original_app_device")" \
  || die "Quarantaene-Inode driftete vor Mutation"
[ "$current_quarantine_contract" = "$quarantine_dir_contract" ] \
  || die "Quarantaene-Vertrag driftete vor Mutation"

unit_is tradingbot-api.service active enabled
unit_is tradingbot-bg.service active enabled
unit_is tradingbot.service inactive disabled
unit_is tradingbot-frontend.service active enabled
unit_is cron.service active enabled
require_api_bg_execution_contract
require_no_pending_unit_reload
unit_security_contract_matches tradingbot-api.service \
  "$api_unit_security_before" \
  || die "API-Unit-Sicherheitsvertrag driftete waehrend Preflight"
unit_security_contract_matches tradingbot-bg.service \
  "$bg_unit_security_before" \
  || die "BG-Unit-Sicherheitsvertrag driftete waehrend Preflight"
unit_security_contract_matches tradingbot.service \
  "$streamlit_unit_security_before" \
  || die "Legacy-Streamlit-Unit-Sicherheitsvertrag driftete waehrend Preflight"
unit_security_contract_matches tradingbot-frontend.service \
  "$frontend_unit_security_before" \
  || die "Legacy-Frontend-Unit-Sicherheitsvertrag driftete waehrend Preflight"
unit_security_contract_matches cron.service \
  "$cron_unit_security_before" \
  || die "Cron-Unit-Sicherheitsvertrag driftete waehrend Preflight"
current_new_streamlit_contract="$(capture_local_directory_contract \
  "$final_clone/.streamlit" "$original_app_device")" \
  || die "Final-Clone-.streamlit-Parent driftete vor Mutation"
[ "$current_new_streamlit_contract" = "$new_app_streamlit_dir_contract" ] \
  || die "Final-Clone-.streamlit-Inode/Metadaten drifteten vor Mutation"
assert_root_crontab_empty
assert_no_legacy_cron_bytes \
  || die "Cron-Quellen drifteten waehrend des Live-Preflights"
crontab -l > "$backup/root-crontab.pre-mutation"
cmp -s "$backup/root-crontab.before" "$backup/root-crontab.pre-mutation" \
  || die "Root-crontab driftete waehrend Preflight"
for pair in \
  '/etc/systemd/system/tradingbot-api.service:api.service.before' \
  '/etc/systemd/system/tradingbot-bg.service:bg.service.before' \
  '/etc/systemd/system/tradingbot-frontend.service:frontend.service.before' \
  '/etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf:api-override.before'; do
  live_path="${pair%%:*}"
  backup_name="${pair#*:}"
  cmp -s "$live_path" "$backup/$backup_name" \
    || die "Unit-/Drop-in-Bytes drifteten: $live_path"
done
for pair in \
  '/etc/systemd/system/tradingbot.service:streamlit.service.before' \
  "$cron_fragment_path:cron.service.before"; do
  live_path="${pair%%:*}"
  backup_name="${pair#*:}"
  require_safe_root_file "$live_path"
  cmp -s "$live_path" "$backup/$backup_name" \
    || die "Streamlit-/Cron-Unit-Bytes drifteten: $live_path"
done
pgrep_no_match -f -- \
  '(/usr/local/sbin/alpha-station-auto-update|/home/tradingbot/app/deploy/(auto_update|safe_deploy)\.sh|/tmp/alpha-safe-deploy)' \
  || die "Updater/Deploy erschien waehrend Preflight"

curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
  > "$backup/old-health.json" \
  || die "alte API ist unmittelbar vor der Migration nicht erreichbar"
read -r old_revision old_bundle <<< "$(health_identity "$backup/old-health.json")"
[ -n "$old_revision" ] && [ -n "$old_bundle" ] \
  || die "alte Health-Identitaet fehlt"
for frontend_file in index.html app.bundle.js boot.js; do
  is_real "$APP/frontend/$frontend_file" && [ -f "$APP/frontend/$frontend_file" ] \
    && [ "$(stat -c %h -- "$APP/frontend/$frontend_file")" = 1 ] \
    || die "altes Frontend-Artefakt ist unsicher: $frontend_file"
  curl -fsS --max-time 15 \
    "http://127.0.0.1:3000/$frontend_file" \
    -o "$backup/old-frontend-$frontend_file" \
    || die "altes Frontend-Artefakt nicht erreichbar: $frontend_file"
  cmp -s "$APP/frontend/$frontend_file" "$backup/old-frontend-$frontend_file" \
    || die "altes Frontend liefert andere Bytes: $frontend_file"
done

api_pid="$(systemctl_property_value tradingbot-api.service MainPID)" \
  || die "API MainPID-Abfrage driftete"
[[ "$api_pid" =~ ^[1-9][0-9]*$ ]] || die "API MainPID driftete"
api_home="$(tr '\0' '\n' < "/proc/$api_pid/environ" \
  | awk -F= '$1 == "HOME" { print substr($0,index($0,"=")+1); exit }')"
[ "$api_home" = /home/tradingbot ] \
  || die "effektives API-HOME driftete waehrend Preflight"
require_systemctl_property tradingbot-frontend.service User root \
  "Legacy-Frontend-User driftete waehrend Preflight"
require_systemctl_property tradingbot-frontend.service WorkingDirectory \
  /home/tradingbot/app/frontend \
  "Legacy-Frontend-WorkingDirectory driftete waehrend Preflight"
require_empty_systemctl_property tradingbot-frontend.service EnvironmentFiles \
  "Legacy-Frontend-EnvironmentFiles drifteten waehrend Preflight"
frontend_exec="$(systemctl_property_value \
  tradingbot-frontend.service ExecStart)" \
  || die "Legacy-Frontend ExecStart-Abfrage driftete"
case "$frontend_exec" in
  *"/usr/bin/python3 -m http.server 3000"*) ;;
  *) die "Legacy-Frontend ExecStart driftete waehrend Preflight" ;;
esac

nginx_pre_mutation="$(nginx_manifest_digest)" \
  || die "nginx-Manifest konnte vor Mutation nicht fail-closed ermittelt werden"
[ "$nginx_pre_mutation" = "$nginx_before" ] \
  || die "nginx-Dateien/Symlink-Topologie drifteten waehrend Preflight"
require_systemctl_property nginx.service MainPID "$nginx_pid" \
  "nginx wechselte waehrend Preflight"
systemctl is-active --quiet nginx || die "nginx ist nicht aktiv"

service_uid="$(id -u tradingbot)"
[[ "$service_uid" =~ ^[0-9]+$ ]] || die "tradingbot-UID fehlt"
user_unit="user@${service_uid}.service"
nologin_bin="$(command -v nologin)"
require_safe_root_file "$nologin_bin"
mutation_started=0
old_app_quarantined=0
new_app_secret_moved=0
env_inode=''
app_secret_inode=''
cache_inode=''
preserved_runtime_contract_ready=0

stash_path() {
  local source="$1" destination="$2"
  if [ -e "$source" ] || [ -L "$source" ]; then
    [ ! -e "$destination" ] && [ ! -L "$destination" ] || return 1
    mv -T -- "$source" "$destination"
  fi
}

assert_no_tree_references() {
  local forbidden process link target
  for process in /proc/[0-9]*; do
    [ -d "$process" ] || continue
    for link in "$process/cwd" "$process/exe" "$process"/fd/*; do
      [ -L "$link" ] || continue
      target="$(readlink -- "$link" 2>/dev/null)" || continue
      target="${target% (deleted)}"
      for forbidden in "$@"; do
        [ -n "$forbidden" ] || continue
        case "$target" in
          "$forbidden"|"$forbidden"/*)
            echo "Prozess referenziert gesperrten Baum: $process -> $target" >&2
            return 1
            ;;
        esac
      done
    done
  done
  return 0
}

assert_no_updater_processes() {
  pgrep_no_match -f -- \
    '(/usr/local/sbin/alpha-station-auto-update|/home/tradingbot/app/deploy/(auto_update|safe_deploy)\.sh|/tmp/alpha-safe-deploy)'
}

assert_quiescent() {
  local user_load user_active
  account_is_closed || return 1
  unit_matches cron.service inactive enabled || return 1
  unit_matches tradingbot-api.service inactive enabled || return 1
  unit_matches tradingbot-bg.service inactive enabled || return 1
  unit_matches tradingbot.service inactive disabled || return 1
  unit_matches tradingbot-frontend.service inactive enabled || return 1
  user_load="$(systemctl show "$user_unit" -p LoadState --value)" || return 1
  user_active="$(systemctl show "$user_unit" -p ActiveState --value)" || return 1
  case "$user_load:$user_active" in
    not-found:|not-found:inactive|loaded:inactive|loaded:failed) ;;
    *)
      echo "User-Manager ist nicht sicher inaktiv: $user_unit ($user_load/$user_active)" >&2
      return 1
      ;;
  esac
  pgrep_no_match -u "$service_uid" || return 1
  pgrep_no_match -x cron || return 1
  pgrep_no_match -x crond || return 1
  assert_no_updater_processes || return 1
  assert_no_tree_references "$APP" "$quarantine/old-app" || return 1
}

# Nach dem Swap duerfen die neuen Dienste im aktuellen APP-Baum laufen. Vor
# jeder weiteren Root-Mutation bleibt aber der alte Quarantaenebaum prozessfrei.
assert_no_old_app_activity() {
  assert_no_updater_processes || return 1
  assert_no_tree_references "$quarantine/old-app" || return 1
}

validate_preserved_runtime_state() {
  local metadata kind owner group mode links device path
  local current_streamlit_contract current_env_contract current_secret_contract
  local app_device cache_list="$backup/data-cache-before-swap.nul"

  is_real "$APP" && [ -d "$APP" ] || return 1
  metadata="$(stat -c '%F|%U|%G|%a|%h|%d' -- "$APP")" || return 1
  IFS='|' read -r kind owner group mode links app_device <<< "$metadata"
  [ "$kind" = directory ] && [ "$owner:$group:$mode" = tradingbot:tradingbot:755 ] \
    && is_not_mountpoint "$APP" || return 1

  current_streamlit_contract="$(capture_local_directory_contract \
    "$APP/.streamlit" "$app_device")" || return 1
  [ "$current_streamlit_contract" = "$app_streamlit_dir_contract" ] \
    || return 1

  current_env_contract="$(capture_preserved_file_contract "$APP/.env")" \
    || return 1
  current_secret_contract="$(capture_preserved_file_contract \
    "$APP/.streamlit/secrets.toml")" || return 1
  [ "$current_env_contract" = "$env_preserved_contract" ] \
    && [ "$current_secret_contract" = "$app_secret_preserved_contract" ] \
    || return 1

  for path in "$APP/.env" "$APP/.streamlit/secrets.toml"; do
    metadata="$(stat -c '%F|%U|%G|%a|%h|%d' -- "$path")" || return 1
    IFS='|' read -r kind owner group mode links device <<< "$metadata"
    [ "$kind" = 'regular file' ] && [ "$owner:$group:$mode" = root:root:600 ] \
      && [ "$links" = 1 ] && [ "$device" = "$app_device" ] || return 1
  done

  metadata="$(stat -c '%F|%U|%G|%a|%h|%d' -- "$APP/data_cache")" \
    || return 1
  IFS='|' read -r kind owner group mode links device <<< "$metadata"
  [ "$kind" = directory ] \
    && [ "$owner:$group:$mode" = tradingbot:tradingbot:750 ] \
    && [ "$device" = "$app_device" ] \
    && is_not_mountpoint "$APP/data_cache" || return 1

  : > "$cache_list"
  find "$APP/data_cache" -xdev -mindepth 1 -print0 > "$cache_list" \
    || return 1
  while IFS= read -r -d '' path; do
    metadata="$(stat -c '%F|%U|%G|%a|%h|%d' -- "$path")" || return 1
    IFS='|' read -r kind owner group mode links device <<< "$metadata"
    [ "$device" = "$app_device" ] || return 1
    case "$kind" in
      directory)
        is_not_mountpoint "$path" || return 1
        ;;
      'regular file')
        [ "$links" = 1 ] || return 1
        ;;
      *)
        echo "Unerlaubter data_cache-Eintrag ($kind): $path" >&2
        return 1
        ;;
    esac
  done < "$cache_list"

  # Nach der laengeren Cache-Baumpruefung beide Secret-Dateien erneut an exakt
  # denselben Start-Inode und Hash binden; data_cache bleibt bewusst der
  # neueste, nun quiescente und vollständig validierte Zustand.
  current_streamlit_contract="$(capture_local_directory_contract \
    "$APP/.streamlit" "$app_device")" || return 1
  current_env_contract="$(capture_preserved_file_contract "$APP/.env")" \
    || return 1
  current_secret_contract="$(capture_preserved_file_contract \
    "$APP/.streamlit/secrets.toml")" || return 1
  [ "$current_streamlit_contract" = "$app_streamlit_dir_contract" ] \
    && [ "$current_env_contract" = "$env_preserved_contract" ] \
    && [ "$current_secret_contract" = "$app_secret_preserved_contract" ] \
    && [ "$(stat -c '%F|%U:%G:%a|%d' -- "$APP/data_cache")" = \
      "directory|tradingbot:tradingbot:750|$app_device" ]
}

linger_matches() {
  local expected="$1" actual=''
  case "$expected" in
    yes)
      [ -e /var/lib/systemd/linger/tradingbot ] \
        && actual="$(loginctl show-user tradingbot -p Linger --value 2>/dev/null)" \
        && [ "$actual" = yes ]
      ;;
    no)
      [ ! -e /var/lib/systemd/linger/tradingbot ] || return 1
      if actual="$(loginctl show-user tradingbot -p Linger --value 2>/dev/null)"; then
        [ "$actual" = no ]
      else
        return 0
      fi
      ;;
    *) return 1 ;;
  esac
}

account_is_closed() {
  [ "$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')" = \
    "$nologin_bin" ] && linger_matches no
}

restore_service_login() {
  usermod --shell "$old_service_shell" tradingbot || return 1
  case "$old_linger" in
    yes) loginctl enable-linger tradingbot >/dev/null || return 1 ;;
    no) loginctl disable-linger tradingbot >/dev/null || return 1 ;;
    *) return 1 ;;
  esac
  [ "$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')" = \
    "$old_service_shell" ] || return 1
  linger_matches "$old_linger"
}

restore_runtime_metadata() {
  local metadata owner group mode path="$1" metadata_file="$2"
  is_real "$metadata_file" && [ -f "$metadata_file" ] || return 1
  is_real "$path" && [ -d "$path" ] \
    && is_not_mountpoint "$path" || return 1
  metadata="$(cat "$metadata_file")" || return 1
  IFS=: read -r owner group mode <<< "$metadata"
  [ -n "$owner" ] && [ -n "$group" ] && [[ "$mode" =~ ^[0-7]+$ ]] \
    || return 1
  chown "$owner:$group" "$path" && chmod "$mode" "$path"
}

global_secret_parent_matches() {
  is_real "$GLOBAL_SECRET_DIR" && [ -d "$GLOBAL_SECRET_DIR" ] \
    && is_not_mountpoint "$GLOBAL_SECRET_DIR" \
    && [ "$(readlink -f -- "$GLOBAL_SECRET_DIR")" = "$GLOBAL_SECRET_DIR" ] \
    && [ "$(stat -c '%d:%i:%U:%G:%a' -- "$GLOBAL_SECRET_DIR")" = \
      "$global_secret_dir_identity" ]
}

app_streamlit_parent_matches_at() {
  local path="$1" expected="$2" current
  [ -n "$expected" ] || return 1
  current="$(capture_local_directory_contract \
    "$path" "$original_app_device")" || return 1
  [ "$current" = "$expected" ]
}

directory_contract_matches_at() {
  local path="$1" expected="$2" current
  [ -n "$expected" ] || return 1
  current="$(capture_local_directory_contract \
    "$path" "$original_app_device")" || return 1
  [ "$current" = "$expected" ]
}

original_app_directory_matches_at() {
  directory_contract_matches_at "$1" "$original_app_dir_contract"
}

new_app_directory_matches_at() {
  directory_contract_matches_at "$1" "$new_app_dir_contract"
}

quarantine_directory_matches() {
  directory_contract_matches_at "$quarantine" "$quarantine_dir_contract"
}

app_directory_state() {
  local current
  if path_absent "$APP"; then
    printf '%s\n' absent
    return 0
  fi
  current="$(capture_local_directory_contract \
    "$APP" "$original_app_device")" || return 1
  if [ "$current" = "$original_app_dir_contract" ]; then
    printf '%s\n' original
  elif [ "$current" = "$new_app_dir_contract" ]; then
    printf '%s\n' new
  else
    return 1
  fi
}

quarantined_old_app_state() {
  local path="$quarantine/old-app" current
  if path_absent "$path"; then
    printf '%s\n' absent
    return 0
  fi
  current="$(capture_local_directory_contract \
    "$path" "$original_app_device")" || return 1
  [ "$current" = "$original_app_dir_contract" ] || return 1
  printf '%s\n' original
}

# Die beiden APP-Renames sind atomar, die unmittelbar folgenden Bash-Flags aber
# nicht. Deshalb wird die reale Lage des gebundenen Alt-Inodes stets aus beiden
# sicheren Pfaden rekonstruiert. Genau ein Ort muss passen; Flagwerte sind nur
# Protokollzustand und niemals Restore-Autoritaet.
original_app_directory_location() {
  local app_state old_state
  quarantine_directory_matches || return 1
  app_state="$(app_directory_state)" || return 1
  old_state="$(quarantined_old_app_state)" || return 1
  case "$app_state:$old_state" in
    original:absent) printf '%s\n' app ;;
    absent:original|new:original) printf '%s\n' quarantine ;;
    *) return 1 ;;
  esac
}

rollback_app_layout_state() {
  local app_state old_state
  quarantine_directory_matches || return 1
  path_absent "$quarantine/failed-new-app" || return 1
  app_state="$(app_directory_state)" || return 1
  old_state="$(quarantined_old_app_state)" || return 1
  case "$app_state:$old_state" in
    original:absent) printf '%s\n' old-at-app ;;
    absent:original) printf '%s\n' old-at-quarantine-app-absent ;;
    new:original) printf '%s\n' old-at-quarantine-new-at-app ;;
    *) return 1 ;;
  esac
}

original_app_streamlit_parent_matches() {
  local location parent
  location="$(original_app_directory_location)" || return 1
  case "$location" in
    app) parent="$APP/.streamlit" ;;
    quarantine) parent="$quarantine/old-app/.streamlit" ;;
    *) return 1 ;;
  esac
  app_streamlit_parent_matches_at "$parent" "$app_streamlit_dir_contract"
}

new_app_streamlit_parent_matches_at() {
  app_streamlit_parent_matches_at "$1" "$new_app_streamlit_dir_contract"
}

preserved_secret_path_state() {
  local path="$1" current
  if path_absent "$path"; then
    printf '%s\n' absent
    return 0
  fi
  current="$(capture_preserved_file_contract "$path")" || return 1
  [ "$current" = "$app_secret_preserved_contract" ] || return 1
  printf '%s\n' original
}

derive_new_app_secret_milestone() {
  local layout="$1" old_path old_state new_state
  case "$layout" in
    old-at-app)
      old_path="$APP/.streamlit/secrets.toml"
      old_state="$(preserved_secret_path_state "$old_path")" || return 1
      [ "$old_state" = original ] || return 1
      printf '%s\n' 0
      ;;
    old-at-quarantine-app-absent)
      old_path="$quarantine/old-app/.streamlit/secrets.toml"
      old_state="$(preserved_secret_path_state "$old_path")" || return 1
      [ "$old_state" = original ] || return 1
      printf '%s\n' 0
      ;;
    old-at-quarantine-new-at-app)
      old_path="$quarantine/old-app/.streamlit/secrets.toml"
      new_app_streamlit_parent_matches_at "$APP/.streamlit" || return 1
      old_state="$(preserved_secret_path_state "$old_path")" || return 1
      new_state="$(preserved_secret_path_state \
        "$APP/.streamlit/secrets.toml")" || return 1
      case "$old_state:$new_state" in
        original:absent) printf '%s\n' 0 ;;
        absent:original) printf '%s\n' 1 ;;
        *) return 1 ;;
      esac
      ;;
    *) return 1 ;;
  esac
}

runtime_directory_matches_inode() {
  local path="$1" expected_inode="$2"
  if [ -z "$expected_inode" ]; then
    [ ! -e "$path" ] && [ ! -L "$path" ]
    return
  fi
  is_real "$path" && [ -d "$path" ] && is_not_mountpoint "$path" \
    && [ "$(stat -c '%d:%i' -- "$path")" = "$expected_inode" ]
}

secure_directory_contract_matches() {
  local path="$1" expected_inode="$2" expected_uid="$3"
  local expected_gid="$4" expected_mode="$5"
  python3 -I - "$path" "$expected_inode" "$expected_uid" \
    "$expected_gid" "$expected_mode" <<'PY'
import os
import stat
import sys

path = sys.argv[1]
expected_dev, expected_ino = (int(value) for value in sys.argv[2].split(":"))
expected_uid = int(sys.argv[3])
expected_gid = int(sys.argv[4])
expected_mode = int(sys.argv[5], 8)

def fingerprint(st: os.stat_result) -> tuple[int, ...]:
    return (
        st.st_dev,
        st.st_ino,
        st.st_mode,
        st.st_uid,
        st.st_gid,
        st.st_nlink,
        st.st_ctime_ns,
    )

before_path = os.lstat(path)
fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_DIRECTORY)
try:
    before_fd = os.fstat(fd)
    after_fd = os.fstat(fd)
    after_path = os.lstat(path)
finally:
    os.close(fd)

if not stat.S_ISDIR(before_fd.st_mode):
    raise SystemExit("not a directory")
if not (
    fingerprint(before_path)
    == fingerprint(before_fd)
    == fingerprint(after_fd)
    == fingerprint(after_path)
):
    raise SystemExit("directory path/inode changed")
if (before_fd.st_dev, before_fd.st_ino) != (expected_dev, expected_ino):
    raise SystemExit("directory inode mismatch")
if (
    before_fd.st_uid != expected_uid
    or before_fd.st_gid != expected_gid
    or stat.S_IMODE(before_fd.st_mode) != expected_mode
):
    raise SystemExit("directory metadata mismatch")
PY
}

runtime_secret_state_matches() {
  local current_contract=''
  secure_directory_contract_matches "$RUNTIME_HOME" "$runtime_home_inode" \
    "$(id -u tradingbot)" "$(id -g tradingbot)" 700 || return 1
  secure_directory_contract_matches "$RUNTIME_HOME/.streamlit" \
    "$runtime_streamlit_inode" 0 "$(id -g tradingbot)" 750 || return 1
  current_contract="$(capture_runtime_secret_contract)" || return 1
  [ "$current_contract" = "$runtime_secret_contract" ] || return 1
  secure_directory_contract_matches "$RUNTIME_HOME/.streamlit" \
    "$runtime_streamlit_inode" 0 "$(id -g tradingbot)" 750 || return 1
  secure_directory_contract_matches "$RUNTIME_HOME" "$runtime_home_inode" \
    "$(id -u tradingbot)" "$(id -g tradingbot)" 700
}

metadata_matches_snapshot() {
  local path="$1" snapshot="$2"
  is_real "$path" && [ -d "$path" ] && is_not_mountpoint "$path" \
    && is_real "$snapshot" && [ -f "$snapshot" ] \
    && [ "$(stat -c '%U:%G:%a' -- "$path")" = "$(cat "$snapshot")" ]
}

regular_file_matches_snapshot() {
  local path="$1" snapshot="$2" path_metadata snapshot_metadata
  is_real "$path" && [ -f "$path" ] \
    && is_real "$snapshot" && [ -f "$snapshot" ] || return 1
  path_metadata="$(stat -c '%F|%U:%G:%a|%h|%s' -- "$path")" || return 1
  snapshot_metadata="$(stat -c '%F|%U:%G:%a|%h|%s' -- "$snapshot")" \
    || return 1
  [ "$path_metadata" = "$snapshot_metadata" ] \
    && case "$path_metadata" in 'regular file|'*'|1|'*) true ;; *) false ;; esac \
    && cmp -s -- "$path" "$snapshot"
}

systemctl_property_is() {
  local unit="$1" property="$2" expected="$3" actual
  actual="$(systemctl_property_value "$unit" "$property")" || return 1
  [ "$actual" = "$expected" ]
}

success_unit_disk_contract_matches() {
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-api.service \
    "$backup/expected-tradingbot-api.service" || return 1
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-bg.service \
    "$backup/expected-tradingbot-bg.service" || return 1
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
    "$expected_api_override" || return 1
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-frontend.service \
    "$backup/frontend.service.before" || return 1
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot.service \
    "$backup/streamlit.service.before" || return 1
  regular_file_matches_snapshot "$cron_fragment_path" \
    "$backup/cron.service.before"
}

runtime_service_home_contract_matches() {
  local runtime_unit runtime_pid runtime_home
  for runtime_unit in tradingbot-api.service tradingbot-bg.service; do
    runtime_pid="$(systemctl_property_value "$runtime_unit" MainPID)" \
      || return 1
    [[ "$runtime_pid" =~ ^[1-9][0-9]*$ ]] || return 1
    runtime_home="$(tr '\0' '\n' < "/proc/$runtime_pid/environ" \
      | awk -F= '$1 == "HOME" { print substr($0,index($0,"=")+1); exit }')" \
      || return 1
    [ "$runtime_home" = "$RUNTIME_HOME" ] || return 1
  done
}

success_unit_contract() {
  local unit property expected_environment current_environment
  local expected_exec current_exec
  success_unit_disk_contract_matches || return 1
  unit_matches tradingbot-api.service active enabled || return 1
  unit_matches tradingbot-bg.service active enabled || return 1
  unit_matches tradingbot.service inactive disabled || return 1
  unit_matches tradingbot-frontend.service active enabled || return 1
  unit_matches cron.service active enabled || return 1
  for unit in tradingbot-api.service tradingbot-bg.service tradingbot.service \
      tradingbot-frontend.service cron.service; do
    unit_need_daemon_reload_is_no "$unit" || return 1
  done

  for unit in tradingbot-api.service tradingbot-bg.service; do
    systemctl_property_is "$unit" LoadState loaded || return 1
    systemctl_property_is "$unit" User tradingbot || return 1
    systemctl_property_is "$unit" Group tradingbot || return 1
    systemctl_property_is "$unit" WorkingDirectory "$APP" || return 1
    systemctl_property_is "$unit" FragmentPath \
      "/etc/systemd/system/$unit" || return 1
    systemctl_property_is "$unit" EnvironmentFiles \
      "$APP/.env (ignore_errors=yes)" || return 1
    for property in ExecCondition ExecStartPre ExecStartPost ExecReload \
        ExecStop ExecStopPost RootDirectory RootImage; do
      systemctl_property_is "$unit" "$property" '' || return 1
    done
  done

  systemctl_property_is tradingbot-api.service DropInPaths \
    /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
    || return 1
  systemctl_property_is tradingbot-bg.service DropInPaths '' || return 1
  expected_environment="API_BIND_HOST=0.0.0.0
HOME=$RUNTIME_HOME
PATH=$APP/venv/bin:/usr/local/bin:/usr/bin:/bin
XDG_CACHE_HOME=$RUNTIME_HOME/cache"
  current_environment="$(normalized_environment_property \
    tradingbot-api.service)" || return 1
  [ "$current_environment" = "$expected_environment" ] || return 1
  expected_environment="HOME=$RUNTIME_HOME
PATH=$APP/venv/bin:/usr/local/bin:/usr/bin:/bin
XDG_CACHE_HOME=$RUNTIME_HOME/cache"
  current_environment="$(normalized_environment_property \
    tradingbot-bg.service)" || return 1
  [ "$current_environment" = "$expected_environment" ] || return 1

  expected_exec="{ path=$APP/venv/bin/uvicorn ; argv[]=$APP/venv/bin/uvicorn api:app --host 0.0.0.0 --port 8000 ; ignore_errors=no }"
  current_exec="$(normalized_exec_start_property \
    tradingbot-api.service)" || return 1
  [ "$current_exec" = "$expected_exec" ] || return 1
  expected_exec="{ path=$APP/venv/bin/python3 ; argv[]=$APP/venv/bin/python3 bg_service.py ; ignore_errors=no }"
  current_exec="$(normalized_exec_start_property \
    tradingbot-bg.service)" || return 1
  [ "$current_exec" = "$expected_exec" ] || return 1

  unit_security_contract_matches tradingbot.service \
    "$streamlit_unit_security_before" || return 1
  unit_security_contract_matches tradingbot-frontend.service \
    "$frontend_unit_security_before" || return 1
  unit_security_contract_matches cron.service \
    "$cron_unit_security_before" || return 1
  runtime_service_home_contract_matches || return 1

  for unit in tradingbot-api.service tradingbot-bg.service tradingbot.service \
      tradingbot-frontend.service cron.service; do
    unit_security_contract "$unit" || return 1
  done
}

rollback_frontend_snapshot_matches() {
  local current_manifest frontend_file
  [ "$rollback_frontend_snapshot_ready" = 1 ] || return 1
  safe_root_file_matches_digest "$rollback_frontend_unit" \
    "$rollback_frontend_unit_digest" || return 1
  frontend_tree_is_safe "$rollback_frontend_snapshot" 1 || return 1
  current_manifest="$(frontend_tree_manifest_digest \
    "$rollback_frontend_snapshot")" || return 1
  [ "$current_manifest" = "$rollback_frontend_manifest" ] || return 1
  for frontend_file in index.html app.bundle.js boot.js; do
    cmp -s -- "$rollback_frontend_snapshot/$frontend_file" \
      "$backup/old-frontend-$frontend_file" || return 1
  done
}

rollback_frontend_execution_contract_matches() {
  local value property frontend_exec
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-frontend.service \
    "$rollback_frontend_unit" || return 1
  path_absent /etc/systemd/system/tradingbot-frontend.service.d || return 1
  value="$(systemctl_property_value tradingbot-frontend.service LoadState)" \
    || return 1
  [ "$value" = loaded ] || return 1
  unit_need_daemon_reload_is_no tradingbot-frontend.service || return 1
  value="$(systemctl_property_value tradingbot-frontend.service FragmentPath)" \
    || return 1
  [ "$value" = /etc/systemd/system/tradingbot-frontend.service ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service DropInPaths)" \
    || return 1
  [ -z "$value" ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service User)" \
    || return 1
  [ "$value" = root ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service Group)" \
    || return 1
  [ "$value" = root ] || return 1
  value="$(systemctl_property_value \
    tradingbot-frontend.service WorkingDirectory)" || return 1
  [ "$value" = / ] || return 1
  frontend_exec="$(systemctl_property_value \
    tradingbot-frontend.service ExecStart)" || return 1
  case "$frontend_exec" in
    *"/usr/bin/python3 -I -S -m http.server 3000 --bind 0.0.0.0 --directory $rollback_frontend_snapshot"*) ;;
    *) return 1 ;;
  esac
  case "$frontend_exec" in *"$APP"*) return 1 ;; esac
  for property in Environment EnvironmentFiles ExecCondition ExecStartPre \
      ExecStartPost ExecReload ExecStop ExecStopPost RootDirectory RootImage; do
    value="$(systemctl_property_value \
      tradingbot-frontend.service "$property")" || return 1
    [ -z "$value" ] || return 1
  done
  value="$(systemctl_property_value \
    tradingbot-frontend.service NoNewPrivileges)" || return 1
  [ "$value" = yes ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service PrivateTmp)" \
    || return 1
  [ "$value" = yes ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service ProtectSystem)" \
    || return 1
  [ "$value" = strict ] || return 1
  value="$(systemctl_property_value tradingbot-frontend.service ProtectHome)" \
    || return 1
  [ "$value" = read-only ]
}

path_absent() {
  [ ! -e "$1" ] && [ ! -L "$1" ]
}

rollback_leave_closed() {
  systemctl stop cron.service tradingbot-api.service tradingbot-bg.service \
    tradingbot.service tradingbot-frontend.service >/dev/null 2>&1 || true
  systemctl stop "$user_unit" >/dev/null 2>&1 || true
  usermod --shell "$nologin_bin" tradingbot >/dev/null 2>&1 || true
  loginctl disable-linger tradingbot >/dev/null 2>&1 || true
  echo "KRITISCH: Rollback bleibt fail-closed: Dienste/Cron gestoppt, tradingbot nologin/Linger aus. Pruefen: $backup" >&2
  return 1
}

rollback_filesystem_restore_is_exact() {
  local pair live_path backup_name current_global_contract snapshot_global_contract
  local current_nginx nginx_main_pid active_state
  local check_crontab="$backup/root-crontab.restore-gate"

  is_real "$APP" && [ -d "$APP" ] && is_not_mountpoint "$APP" || return 1
  original_app_streamlit_parent_matches || return 1
  [ "$(capture_preserved_file_contract "$APP/.env")" = \
    "$env_preserved_contract" ] || return 1
  [ "$(capture_preserved_file_contract \
    "$APP/.streamlit/secrets.toml")" = \
    "$app_secret_preserved_contract" ] || return 1
  validate_preserved_runtime_state || return 1
  if [ "$preserved_runtime_contract_ready" = 1 ]; then
    [ -n "$env_inode" ] && [ -n "$app_secret_inode" ] \
      && [ -n "$cache_inode" ] || return 1
    [ "$(stat -c '%d:%i' -- "$APP/.env")" = "$env_inode" ] || return 1
    [ "$(stat -c '%d:%i' -- "$APP/.streamlit/secrets.toml")" = \
      "$app_secret_inode" ] || return 1
    [ "$(stat -c '%d:%i' -- "$APP/data_cache")" = "$cache_inode" ] || return 1
  fi

  for pair in \
    '/etc/systemd/system/tradingbot-api.service:api.service.before' \
    '/etc/systemd/system/tradingbot-bg.service:bg.service.before' \
    '/etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf:api-override.before'; do
    live_path="${pair%%:*}"
    backup_name="${pair#*:}"
    regular_file_matches_snapshot "$live_path" "$backup/$backup_name" \
      || return 1
  done
  regular_file_matches_snapshot \
    /etc/systemd/system/tradingbot-frontend.service \
    "$rollback_frontend_unit" || return 1
  path_absent /etc/systemd/system/tradingbot-frontend.service.d || return 1
  rollback_frontend_snapshot_matches || return 1

  path_absent "$CRON_FILE" || return 1
  if [ "$launcher_existed" = 1 ]; then
    regular_file_matches_snapshot "$LAUNCHER" "$backup/launcher.before" \
      || return 1
  else
    path_absent "$LAUNCHER" || return 1
  fi
  if [ "$log_existed" = 1 ]; then
    regular_file_matches_snapshot "$LOG_FILE" "$backup/auto-update.log.before" \
      || return 1
  else
    path_absent "$LOG_FILE" || return 1
  fi
  crontab -l > "$check_crontab" || return 1
  cmp -s -- "$backup/root-crontab.before" "$check_crontab" || return 1

  global_secret_parent_matches || return 1
  current_global_contract="$(capture_global_secret_contract)" || return 1
  if [ "$home_secret_snapshot_ready" = 1 ]; then
    snapshot_global_contract="$(capture_regular_file_contract \
      "$backup/home-secrets.before" "$(id -u tradingbot)" \
      "$(id -g tradingbot)" 644 tradingbot:tradingbot)" || return 1
    [ "${current_global_contract##*|}" = "$EXPECTED_HOME_SECRET_SHA256" ] \
      && [ "${snapshot_global_contract##*|}" = \
        "$EXPECTED_HOME_SECRET_SHA256" ] || return 1
  else
    [ "$current_global_contract" = "$global_secret_contract" ] || return 1
  fi

  path_absent "$RUNTIME_SECRET" || return 1
  path_absent "$RUNTIME_HOME" || return 1

  if [ "$lock_dir_existed" = 1 ]; then
    metadata_matches_snapshot /run/alpha-station \
      "$backup/lock-dir.metadata.before" || return 1
  else
    path_absent /run/alpha-station || return 1
  fi
  if [ "$lock_file_existed" = 1 ]; then
    regular_file_matches_snapshot /run/alpha-station/auto-update.lock \
      "$backup/auto-update.lock.before" || return 1
  elif [ "$lock_dir_existed" = 1 ]; then
    path_absent /run/alpha-station/auto-update.lock || return 1
  fi

  for live_path in cron.service tradingbot-api.service tradingbot-bg.service \
    tradingbot.service tradingbot-frontend.service; do
    active_state="$(systemctl show "$live_path" -p ActiveState --value)" \
      || return 1
    [ "$active_state" = inactive ] || return 1
  done
  account_is_closed || return 1
  pgrep_no_match -u "$service_uid" || return 1
  assert_no_updater_processes || return 1
  assert_no_tree_references "$APP" "$quarantine/old-app" || return 1

  current_nginx="$(nginx_manifest_digest)" || return 1
  nginx_main_pid="$(systemctl_property_value nginx.service MainPID)" || return 1
  [ "$current_nginx" = "$nginx_before" ] \
    && [ "$nginx_main_pid" = "$nginx_pid" ] \
    && systemctl is-active --quiet nginx
}

rollback() {
  local original_rc="$1" rollback_failed=0 can_restore_app=1
  local failed_app="$quarantine/failed-new-app" rel current_identity=''
  local current_nginx='' current_nginx_pid='' rollback_health_ok=0 frontend_ok=0
  local lock_metadata='' lock_owner='' lock_mode=''
  local rollback_snapshot_contract='' current_global_contract=''
  local parent_contracts_safe=1 rollback_layout=''
  local rollback_trusted_home_ancestors=''
  local current_original_location='' restored_original_location=''
  local rollback_secret_moved=''
  echo "FEHLER rc=$original_rc: automatischer Rollback startet." >&2
  cd /root || {
    echo "KRITISCH: Rollback konnte /root nicht als sicheren CWD setzen; keine Restore-Mutation ausgefuehrt. Pruefen: $backup" >&2
    return 1
  }

  # Auch ein Fehler waehrend des allerletzten Success-Restore kann hier
  # landen, nachdem die alte Shell bereits kurz wieder offen war. Account und
  # Linger deshalb vor jeder weiteren Aktion sofort erneut fail-closed setzen.
  usermod --shell "$nologin_bin" tradingbot >/dev/null 2>&1 \
    || rollback_failed=1
  loginctl disable-linger tradingbot >/dev/null 2>&1 \
    || rollback_failed=1
  systemctl stop "$user_unit" >/dev/null 2>&1 || rollback_failed=1
  systemctl stop cron.service tradingbot-api.service tradingbot-bg.service \
    tradingbot.service tradingbot-frontend.service >/dev/null 2>&1 \
    || rollback_failed=1

  # Falls ein Cron-Lauf gerade gestartet war, erst nach dessen Lock-Freigabe
  # fortfahren. Kein Checkout wird parallel zu einem Updater bewegt.
  rollback_lock_held=0
  if [ -e /run/alpha-station ] || [ -L /run/alpha-station ]; then
    if is_real /run/alpha-station && [ -d /run/alpha-station ] \
      && lock_metadata="$(stat -c '%u %a' -- /run/alpha-station)"; then
      read -r lock_owner lock_mode <<< "$lock_metadata"
    else
      rollback_failed=1
    fi
    if [ "$lock_owner" = 0 ] && [[ "$lock_mode" =~ ^[0-7]+$ ]] \
      && (( (8#$lock_mode & 8#22) == 0 )); then
      exec 8>>/run/alpha-station/auto-update.lock
      if flock -w 120 8; then
        rollback_lock_held=1
      else
        rollback_failed=1
      fi
    else
      rollback_failed=1
    fi
  fi
  # Ein Updater, dessen Lock wir gerade abgewartet haben, kann waehrenddessen
  # Units neu gestartet haben. Deshalb nach Lock-Erwerb alle bekannten
  # Prozesse erneut stoppen und erst danach die harte Quiesce-Schranke ziehen.
  usermod --shell "$nologin_bin" tradingbot >/dev/null 2>&1 \
    || rollback_failed=1
  loginctl disable-linger tradingbot >/dev/null 2>&1 \
    || rollback_failed=1
  systemctl stop cron.service tradingbot-api.service tradingbot-bg.service \
    tradingbot.service tradingbot-frontend.service >/dev/null 2>&1 \
    || rollback_failed=1
  systemctl stop "$user_unit" >/dev/null 2>&1 || rollback_failed=1
  systemctl reset-failed cron.service tradingbot-api.service \
    tradingbot-bg.service tradingbot.service tradingbot-frontend.service \
    >/dev/null 2>&1 || rollback_failed=1
  if [ "$rollback_failed" != 0 ] || ! assert_quiescent; then
    if [ "$rollback_lock_held" = 1 ]; then
      flock -u 8 >/dev/null 2>&1 || true
    fi
    exec 8>&- || true
    echo "KRITISCH: Rollback-Quiesce nicht beweisbar; keine APP-/Runtime-/Unit-/Secret-Mutation ausgefuehrt. Pruefen: $backup" >&2
    return 1
  fi
  rollback_trusted_home_ancestors="$(trusted_home_ancestor_contract)" || {
    if [ "$rollback_lock_held" = 1 ]; then
      flock -u 8 >/dev/null 2>&1 || true
    fi
    exec 8>&- || true
    echo "KRITISCH: Root-Vertrauenskette ist im Rollback nicht abfragbar; keine Restore-Mutation. Pruefen: $backup" >&2
    return 1
  }
  [ "$rollback_trusted_home_ancestors" = "$trusted_home_ancestors" ] \
    && rollback_layout="$(rollback_app_layout_state)" || {
      if [ "$rollback_lock_held" = 1 ]; then
        flock -u 8 >/dev/null 2>&1 || true
      fi
      exec 8>&- || true
      echo "KRITISCH: APP-/Quarantaene-Inodes sind im Rollback mehrdeutig oder drifteten; keine Restore-Mutation. Pruefen: $backup" >&2
      return 1
    }
  case "$rollback_layout" in
    old-at-app)
      old_app_quarantined=0
      ;;
    old-at-quarantine-app-absent|old-at-quarantine-new-at-app)
      [ "$preserved_runtime_contract_ready" = 1 ] || {
        if [ "$rollback_lock_held" = 1 ]; then
          flock -u 8 >/dev/null 2>&1 || true
        fi
        exec 8>&- || true
        echo "KRITISCH: APP wurde bewegt, bevor der Runtime-Vertrag vollstaendig war; keine Restore-Mutation. Pruefen: $backup" >&2
        return 1
      }
      old_app_quarantined=1
      ;;
    *)
      if [ "$rollback_lock_held" = 1 ]; then
        flock -u 8 >/dev/null 2>&1 || true
      fi
      exec 8>&- || true
      echo "KRITISCH: unbekannter APP-Rollbackzustand; keine Restore-Mutation. Pruefen: $backup" >&2
      return 1
      ;;
  esac
  # Auch bereits vor dem Stop vertauschte Runtime-Parents duerfen niemals via
  # [ -d ], chown oder chmod dereferenziert werden. Nur exakt dieselben realen
  # Inodes wie im Live-/Erstellungsvertrag sind restaurierbar. Der
  # service-eigene Home-secret-Parent muss ebenfalls inodegleich geblieben sein.
  original_app_streamlit_parent_matches || parent_contracts_safe=0
  if [ "$rollback_layout" = old-at-quarantine-new-at-app ]; then
    new_app_directory_matches_at "$APP" \
      && new_app_streamlit_parent_matches_at "$APP/.streamlit" \
      || parent_contracts_safe=0
  fi
  if [ "$parent_contracts_safe" = 1 ]; then
    rollback_secret_moved="$(derive_new_app_secret_milestone \
      "$rollback_layout")" || parent_contracts_safe=0
  fi
  if [ "$parent_contracts_safe" != 1 ] \
    || ! global_secret_parent_matches \
    || ! runtime_directory_matches_inode "$RUNTIME_HOME" "$runtime_home_inode" \
    || ! runtime_directory_matches_inode "$RUNTIME_HOME/.streamlit" \
      "$runtime_streamlit_inode"; then
    if [ "$rollback_lock_held" = 1 ]; then
      flock -u 8 >/dev/null 2>&1 || true
    fi
    exec 8>&- || true
    echo "KRITISCH: Global-/Runtime-Parent driftete; vor jeder Restore-Mutation fail-closed abgebrochen. Pruefen: $backup" >&2
    return 1
  fi
  if [ "$home_secret_snapshot_ready" = 1 ]; then
    rollback_snapshot_contract="$(capture_regular_file_contract \
      "$backup/home-secrets.before" "$(id -u tradingbot)" \
      "$(id -g tradingbot)" 644 tradingbot:tradingbot 2>/dev/null)" || {
        if [ "$rollback_lock_held" = 1 ]; then
          flock -u 8 >/dev/null 2>&1 || true
        fi
        exec 8>&- || true
        echo "KRITISCH: Home-secret-Snapshot ist unsicher; keine Restore-Mutation. Pruefen: $backup" >&2
        return 1
      }
    if [ "${rollback_snapshot_contract##*|}" != \
      "$EXPECTED_HOME_SECRET_SHA256" ]; then
      if [ "$rollback_lock_held" = 1 ]; then
        flock -u 8 >/dev/null 2>&1 || true
      fi
      exec 8>&- || true
      echo "KRITISCH: Home-secret-Snapshot-Hash driftete; keine Restore-Mutation. Pruefen: $backup" >&2
      return 1
    fi
  fi

  # Ein Signal kann direkt nach einem atomaren Rename, aber vor dem naechsten
  # Bash-Assignment eintreffen. Die Restore-Entscheidung folgt daher allein dem
  # zuvor O_NOFOLLOW-klassifizierten Alt-/Neu-Inode-Layout, niemals den Flags.
  if [ "$rollback_layout" != old-at-app ] && [ "$can_restore_app" = 1 ]; then
    if [ "$rollback_layout" = old-at-quarantine-new-at-app ]; then
      if ! new_app_directory_matches_at "$APP" \
        || ! mv -T -- "$APP" "$failed_app" \
        || ! new_app_directory_matches_at "$failed_app"; then
        rollback_failed=1
        can_restore_app=0
      fi
    elif [ "$rollback_layout" != old-at-quarantine-app-absent ]; then
      rollback_failed=1
      can_restore_app=0
    fi
    if [ "$can_restore_app" = 1 ] \
      && [ "$rollback_layout" = old-at-quarantine-new-at-app ]; then
      # data_cache wird inodegleich zurueckverschoben. Die vom gepinnten
      # Runtime-Guard bereits vollzogene Security-Normalisierung
      # (Owner/Mode/ACL) und ein ggf. erzeugtes auth-Verzeichnis sind bewusst
      # irreversibel und werden nicht geloescht: erhalten werden Nutzdatenbytes
        # und Inodes, nicht unsichere Alt-Metadaten oder Auth-Abwesenheit.
      for rel in .env .streamlit/secrets.toml data_cache; do
        if [ "$rel" = .streamlit/secrets.toml ]; then
          if [ "$rollback_secret_moved" != 1 ]; then
            # Vor dem erfolgreichen Forward-Rename liegt das Secret nachweislich
            # noch im bereits gebundenen Alt-Parent. Den neuen Parent dann weder
            # lesen noch fuer die sichere Alt-App-Restauration voraussetzen.
            continue
          fi
          original_app_streamlit_parent_matches \
            && new_app_streamlit_parent_matches_at \
              "$failed_app/.streamlit" || {
                rollback_failed=1
                can_restore_app=0
                break
              }
        fi
        if { [ -e "$failed_app/$rel" ] || [ -L "$failed_app/$rel" ]; } \
          && [ ! -e "$quarantine/old-app/$rel" ] \
          && [ ! -L "$quarantine/old-app/$rel" ]; then
          if [ -d "$(dirname "$quarantine/old-app/$rel")" ]; then
            if ! mv -T -- "$failed_app/$rel" "$quarantine/old-app/$rel"; then
              rollback_failed=1
              can_restore_app=0
            elif [ "$rel" = .streamlit/secrets.toml ]; then
              rollback_secret_moved=0
              new_app_secret_moved=0
            fi
          else
            rollback_failed=1
            can_restore_app=0
          fi
        fi
      done
    fi
    if [ "$can_restore_app" = 1 ]; then
      current_original_location="$(original_app_directory_location)" || {
        rollback_failed=1
        can_restore_app=0
      }
    fi
    if [ "$can_restore_app" = 1 ] \
      && [ "$current_original_location" = quarantine ] \
      && path_absent "$APP"; then
      if ! mv -T -- "$quarantine/old-app" "$APP"; then
        rollback_failed=1
        can_restore_app=0
      else
        restored_original_location="$(original_app_directory_location)" || {
          rollback_failed=1
          can_restore_app=0
        }
        if [ "$can_restore_app" = 1 ] \
          && original_app_directory_matches_at "$APP" \
          && [ "$restored_original_location" = app ]; then
          old_app_quarantined=0
        else
          rollback_failed=1
          can_restore_app=0
        fi
      fi
    else
      rollback_failed=1
    fi
  elif [ "$rollback_layout" = old-at-app ]; then
    old_app_quarantined=0
    new_app_secret_moved=0
  fi

  # Der harte Startvertrag war vollstaendige Abwesenheit. Ein waehrend der
  # Migration erzeugter Runtime-Baum wird nur inodegeprueft gestasht; danach
  # muss wieder der belegte Zustand "Pfad fehlt" gelten.
  if [ -n "$runtime_home_inode" ]; then
    runtime_directory_matches_inode "$RUNTIME_HOME" "$runtime_home_inode" \
      && stash_path "$RUNTIME_HOME" "$backup/failed-runtime-home" \
      || rollback_failed=1
  elif ! path_absent "$RUNTIME_HOME"; then
    rollback_failed=1
  fi

  # API/BG und API-Drop-in erhalten exakt ihre alten Bytes. Die alte
  # Root-Frontend-Unit darf dagegen niemals wieder mit dem service-eigenen APP
  # als Python-CWD starten: sie bleibt im Backup erhalten, live wird nur die
  # vorab root-kontrolliert konstruierte Snapshot-Unit installiert.
  stash_path /etc/systemd/system/tradingbot-api.service \
    "$backup/api.service.failed" || rollback_failed=1
  stash_path /etc/systemd/system/tradingbot-bg.service \
    "$backup/bg.service.failed" || rollback_failed=1
  stash_path /etc/systemd/system/tradingbot-frontend.service \
    "$backup/frontend.service.failed" || rollback_failed=1
  stash_path /etc/systemd/system/tradingbot-frontend.service.d \
    "$backup/frontend-dropins.failed" || rollback_failed=1
  stash_path \
    /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
    "$backup/api-override.failed" || rollback_failed=1
  cp --preserve=all -- "$backup/api.service.before" \
    /etc/systemd/system/tradingbot-api.service || rollback_failed=1
  cp --preserve=all -- "$backup/bg.service.before" \
    /etc/systemd/system/tradingbot-bg.service || rollback_failed=1
  cp --preserve=all -- "$rollback_frontend_unit" \
    /etc/systemd/system/tradingbot-frontend.service || rollback_failed=1
  install -d -o root -g root -m 0755 \
    /etc/systemd/system/tradingbot-api.service.d || rollback_failed=1
  cp --preserve=all -- "$backup/api-override.before" \
    /etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf \
    || rollback_failed=1

  # Installer-Artefakte, Log, Lock und Root-crontab exakt restaurieren.
  stash_path "$CRON_FILE" "$backup/auto-cron.failed" || rollback_failed=1
  stash_path "$LAUNCHER" "$backup/launcher.failed" || rollback_failed=1
  if [ "$launcher_existed" = 1 ]; then
    cp --preserve=all -- "$backup/launcher.before" "$LAUNCHER" \
      || rollback_failed=1
  fi
  stash_path "$LOG_FILE" "$backup/auto-update.log.failed" || rollback_failed=1
  if [ "$log_existed" = 1 ]; then
    cp --preserve=all -- "$backup/auto-update.log.before" "$LOG_FILE" \
      || rollback_failed=1
  fi
  if [ "$rollback_lock_held" = 1 ]; then
    flock -u 8 || rollback_failed=1
  fi
  exec 8>&- || rollback_failed=1
  if [ "$lock_dir_existed" = 0 ] && [ -d /run/alpha-station ]; then
    stash_path /run/alpha-station "$backup/failed-lock-dir" \
      || rollback_failed=1
  elif [ "$lock_file_existed" = 0 ] \
    && [ -e /run/alpha-station/auto-update.lock ]; then
    stash_path /run/alpha-station/auto-update.lock \
      "$backup/failed-auto-update.lock" || rollback_failed=1
  elif [ "$lock_file_existed" = 1 ]; then
    stash_path /run/alpha-station/auto-update.lock \
      "$backup/auto-update.lock.failed" || rollback_failed=1
    cp --preserve=all -- "$backup/auto-update.lock.before" \
      /run/alpha-station/auto-update.lock || rollback_failed=1
  fi
  if [ "$lock_dir_existed" = 1 ] && [ -d /run/alpha-station ]; then
    restore_runtime_metadata /run/alpha-station \
      "$backup/lock-dir.metadata.before" || rollback_failed=1
  fi
  crontab "$backup/root-crontab.before" || rollback_failed=1

  # Home-secret nur restaurieren, wenn der quiescente Snapshot vollstaendig
  # und an den bekannten Hash gebunden wurde. Der aktuelle Pfad wird zuerst
  # O_NOFOLLOW/O_NONBLOCK gelstatet; FIFO/Link/Spezialdatei wird niemals gelesen
  # oder chown/chmod-dereferenziert, sondern im stabilen Parent atomar gestasht.
  if [ "$home_secret_snapshot_ready" = 1 ]; then
    current_global_contract="$(capture_global_secret_contract 2>/dev/null)" \
      || current_global_contract=''
    if [ "$current_global_contract" != "$global_secret_contract" ]; then
      if stash_path "$GLOBAL_SECRET" "$backup/home-secrets.failed"; then
        cp --preserve=all -- "$backup/home-secrets.before" "$GLOBAL_SECRET" \
          || rollback_failed=1
      else
        rollback_failed=1
      fi
    fi
  fi

  # Zweite harte Commit-Schranke: Solange auch nur ein Restore-Schritt oder
  # dessen Byte-/Metadatenbeweis fehlschlaegt, bleiben Cron/Dienste gestoppt
  # und der Service-Account geschlossen. Kein partieller Hybrid geht live.
  if [ "$rollback_failed" != 0 ] || [ "$can_restore_app" != 1 ] \
    || ! rollback_filesystem_restore_is_exact; then
    rollback_leave_closed
    return 1
  fi

  systemctl daemon-reload || rollback_failed=1
  systemctl enable tradingbot-api.service tradingbot-bg.service \
    tradingbot-frontend.service cron.service >/dev/null 2>&1 \
    || rollback_failed=1
  systemctl disable tradingbot.service >/dev/null 2>&1 \
    || rollback_failed=1
  unit_security_contract_matches tradingbot-api.service \
    "$api_unit_security_before" || rollback_failed=1
  unit_security_contract_matches tradingbot-bg.service \
    "$bg_unit_security_before" || rollback_failed=1
  unit_security_contract_matches tradingbot.service \
    "$streamlit_unit_security_before" || rollback_failed=1
  rollback_frontend_execution_contract_matches || rollback_failed=1
  rollback_frontend_snapshot_matches || rollback_failed=1
  for live_path in tradingbot-api.service tradingbot-bg.service \
      tradingbot.service tradingbot-frontend.service cron.service; do
    unit_need_daemon_reload_is_no "$live_path" || rollback_failed=1
  done
  unit_matches tradingbot-api.service inactive enabled || rollback_failed=1
  unit_matches tradingbot-bg.service inactive enabled || rollback_failed=1
  unit_matches tradingbot.service inactive disabled || rollback_failed=1
  unit_matches tradingbot-frontend.service inactive enabled || rollback_failed=1
  unit_matches cron.service inactive enabled || rollback_failed=1
  if [ "$rollback_failed" != 0 ]; then
    rollback_leave_closed
    return 1
  fi

  systemctl start tradingbot-api.service tradingbot-bg.service \
    tradingbot-frontend.service cron.service >/dev/null 2>&1 \
    || rollback_failed=1
  systemctl stop tradingbot.service >/dev/null 2>&1 || rollback_failed=1
  if [ "$rollback_failed" != 0 ]; then
    rollback_leave_closed
    return 1
  fi

  for _ in $(seq 1 20); do
    if curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
      > "$backup/rollback-health.json"; then
      current_identity="$(health_identity "$backup/rollback-health.json" 2>/dev/null)" \
        || current_identity=''
      if [ "$current_identity" = "$old_revision $old_bundle" ]; then
        rollback_health_ok=1
        break
      fi
    fi
    sleep 2
  done
  [ "$rollback_health_ok" = 1 ] || rollback_failed=1

  frontend_ok=1
  for frontend_file in index.html app.bundle.js boot.js; do
    if ! curl -fsS --max-time 15 \
      "http://127.0.0.1:3000/$frontend_file" \
      -o "$backup/rollback-frontend-$frontend_file" \
      || ! cmp -s "$backup/old-frontend-$frontend_file" \
        "$backup/rollback-frontend-$frontend_file"; then
      frontend_ok=0
    fi
  done
  [ "$frontend_ok" = 1 ] || rollback_failed=1
  unit_matches tradingbot-api.service active enabled || rollback_failed=1
  unit_matches tradingbot-bg.service active enabled || rollback_failed=1
  unit_matches tradingbot.service inactive disabled || rollback_failed=1
  unit_matches tradingbot-frontend.service active enabled || rollback_failed=1
  unit_matches cron.service active enabled || rollback_failed=1
  for pair in \
    '/etc/systemd/system/tradingbot-api.service:api.service.before' \
    '/etc/systemd/system/tradingbot-bg.service:bg.service.before' \
    '/etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf:api-override.before'; do
    live_path="${pair%%:*}"
    backup_name="${pair#*:}"
    regular_file_matches_snapshot "$live_path" "$backup/$backup_name" \
      || rollback_failed=1
  done
  unit_security_contract_matches tradingbot-api.service \
    "$api_unit_security_before" || rollback_failed=1
  unit_security_contract_matches tradingbot-bg.service \
    "$bg_unit_security_before" || rollback_failed=1
  unit_security_contract_matches tradingbot.service \
    "$streamlit_unit_security_before" || rollback_failed=1
  rollback_frontend_execution_contract_matches || rollback_failed=1
  rollback_frontend_snapshot_matches || rollback_failed=1
  if ! crontab -l > "$backup/root-crontab.after-rollback" \
    || ! cmp -s "$backup/root-crontab.before" \
      "$backup/root-crontab.after-rollback"; then
    rollback_failed=1
  fi
  current_nginx="$(nginx_manifest_digest 2>/dev/null)" || current_nginx=''
  [ "$current_nginx" = "$nginx_before" ] || rollback_failed=1
  current_nginx_pid="$(systemctl_property_value nginx.service MainPID 2>/dev/null)" \
    || current_nginx_pid=''
  [ "$current_nginx_pid" = "$nginx_pid" ] || rollback_failed=1
  systemctl is-active --quiet nginx || rollback_failed=1
  current_global_contract="$(capture_global_secret_contract 2>/dev/null)" \
    || current_global_contract=''
  [ "${current_global_contract##*|}" = "$EXPECTED_HOME_SECRET_SHA256" ] \
    || rollback_failed=1
  [ "$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')" = \
    "$nologin_bin" ] || rollback_failed=1
  linger_matches no || rollback_failed=1

  if [ "$rollback_failed" != 0 ]; then
    rollback_leave_closed
    return 1
  fi

  # Login-Shell/Linger sind der allerletzte Rollback-Schritt, nachdem alte API,
  # BG, sicherem Snapshot-Frontend, Cron, Bytes, Health und nginx vollstaendig
  # gruen sind.
  if ! restore_service_login; then
    rollback_leave_closed
    return 1
  fi
  [ "$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')" = \
    "$old_service_shell" ] && linger_matches "$old_linger" || {
      rollback_leave_closed
      return 1
    }
  echo "ROLLBACK OK: alte API/BG plus root-eigenes Snapshot-Frontend und Cron sind sicher aktiv; Login/Linger zuletzt restauriert. Beweise: $backup" >&2
}

finish() {
  local rc=$?
  trap - EXIT INT TERM
  if [ "$rc" -ne 0 ] && [ "$mutation_started" = 1 ]; then
    set +e
    rollback "$rc"
  fi
  exit "$rc"
}
trap finish EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# Ab hier beginnt die Unterbrechung. Login/Linger schliessen neue
# service-eigene Prozesse aus; Root-cron und alle bekannten Units werden
# quiescent. Jeder kritische Rename hat unmittelbar davor dieselbe Schranke.
current_trusted_home_ancestors="$(trusted_home_ancestor_contract)" \
  || die "Root-Vertrauenskette konnte direkt vor Mutation nicht abgefragt werden"
[ "$current_trusted_home_ancestors" = "$trusted_home_ancestors" ] \
  || die "Root-Vertrauenskette driftete direkt vor Mutation"
mutation_started=1
usermod --shell "$nologin_bin" tradingbot
loginctl disable-linger tradingbot >/dev/null
systemctl stop "$user_unit" >/dev/null 2>&1 || true
systemctl stop cron.service
systemctl stop tradingbot-api.service tradingbot-bg.service \
  tradingbot.service tradingbot-frontend.service
[ "$(getent passwd tradingbot | awk -F: 'NR == 1 { print $7 }')" = \
  "$nologin_bin" ] || die "tradingbot-Shell wurde nicht auf nologin gesetzt"
[ ! -e /var/lib/systemd/linger/tradingbot ] \
  || die "tradingbot-Linger blieb aktiv"
assert_quiescent || die "Dienste/Prozesse sind nicht vollstaendig quiescent"
assert_no_legacy_cron_bytes \
  || die "Cron-Quellen sind nach Quiesce nicht sicher Legacy-frei"

# Erst jetzt ist die bisher service-eigene Secret-Datei race-frei. Snapshot,
# Live-Datei und bekannter Evidenzhash werden dreifach aneinander gebunden.
global_secret_parent_matches \
  || die "Home-secret-Parent driftete vor dem quiescenten Snapshot"
[ "$(capture_global_secret_contract)" = "$global_secret_contract" ] \
  || die "Home-secret-Inode/Bytes/Metadaten drifteten vor Snapshot"
cp --preserve=all -- "$GLOBAL_SECRET" "$backup/home-secrets.before"
global_secret_snapshot_contract="$(capture_regular_file_contract \
  "$backup/home-secrets.before" "$(id -u tradingbot)" \
  "$(id -g tradingbot)" 644 tradingbot:tradingbot)" \
  || die "Home-secret-Snapshot ist keine sichere regulaere Datei"
[ "${global_secret_contract##*|}" = "$EXPECTED_HOME_SECRET_SHA256" ] \
  && [ "${global_secret_snapshot_contract##*|}" = \
       "$EXPECTED_HOME_SECRET_SHA256" ] \
  || die "Home-secret-Snapshot ist nicht exakt"
home_secret_snapshot_ready=1

assert_quiescent || die "Quiesce-Schranke vor Runtime-Revalidierung fehlgeschlagen"
runtime_directory_matches_inode "$RUNTIME_HOME" "$runtime_home_inode" \
  && runtime_directory_matches_inode "$RUNTIME_HOME/.streamlit" \
    "$runtime_streamlit_inode" \
  || die "Runtime-Parents drifteten waehrend des Live-Preflights"
validate_preserved_runtime_state \
  || die ".env/App-secret/data_cache drifteten waehrend Live-Preflight"
captured_env_inode="$(stat -c '%d:%i' -- "$APP/.env")" \
  || die ".env-Inode konnte nicht gebunden werden"
captured_app_secret_inode="$(stat -c '%d:%i' -- \
  "$APP/.streamlit/secrets.toml")" \
  || die "App-secret-Inode konnte nicht gebunden werden"
captured_cache_inode="$(stat -c '%d:%i' -- "$APP/data_cache")" \
  || die "data_cache-Inode konnte nicht gebunden werden"
[[ "$captured_env_inode" =~ ^[0-9]+:[0-9]+$ ]] \
  && [[ "$captured_app_secret_inode" =~ ^[0-9]+:[0-9]+$ ]] \
  && [[ "$captured_cache_inode" =~ ^[0-9]+:[0-9]+$ ]] \
  || die "Runtime-Inode-Dreiervertrag ist ungueltig"
env_inode="$captured_env_inode"
app_secret_inode="$captured_app_secret_inode"
cache_inode="$captured_cache_inode"
preserved_runtime_contract_ready=1

# Der alte Root-Frontend-Dienst darf bei einem Rollback niemals Python aus dem
# service-eigenen Altbaum starten. Nach vollstaendiger Quiesce wird deshalb der
# komplette statische Baum typ-/mount-/hardlink-/groessenvalidiert, bytegenau in
# das root-private Backup kopiert und mit einem deterministischen Manifest
# gebunden. Nur dieser Snapshot darf spaeter als Rollback-DocumentRoot dienen.
assert_quiescent || die "Quiesce-Schranke vor Frontend-Snapshot fehlgeschlagen"
frontend_tree_is_safe "$APP/frontend" 0 \
  || die "Legacy-Frontend-Baum ist fuer sicheren Snapshot ungeeignet"
for frontend_file in index.html app.bundle.js boot.js; do
  cmp -s "$APP/frontend/$frontend_file" \
    "$backup/old-frontend-$frontend_file" \
    || die "Legacy-Frontend driftete zwischen HTTP-Beweis und Quiesce"
done
frontend_source_manifest_before="$(frontend_tree_manifest_digest \
  "$APP/frontend")" \
  || die "Legacy-Frontend-Manifest konnte nicht erstellt werden"
[[ "$frontend_source_manifest_before" =~ ^[0-9a-f]{64}$ ]] \
  || die "Legacy-Frontend-Manifest ist ungueltig"
install -d -o root -g root -m 0755 "$rollback_frontend_snapshot"
cp -a -- "$APP/frontend/." "$rollback_frontend_snapshot/"
chown -hR root:root "$rollback_frontend_snapshot"
chmod -R u=rwX,go=rX "$rollback_frontend_snapshot"
frontend_tree_is_safe "$APP/frontend" 0 \
  && frontend_tree_is_safe "$rollback_frontend_snapshot" 1 \
  || die "Frontend-Quelle/Snapshot verletzen den sicheren Baumvertrag"
frontend_source_manifest_after="$(frontend_tree_manifest_digest \
  "$APP/frontend")" \
  || die "Legacy-Frontend driftete nach Snapshot"
rollback_frontend_manifest="$(frontend_tree_manifest_digest \
  "$rollback_frontend_snapshot")" \
  || die "Rollback-Frontend-Manifest konnte nicht erstellt werden"
[ "$frontend_source_manifest_before" = "$frontend_source_manifest_after" ] \
  && [ "$frontend_source_manifest_before" = "$rollback_frontend_manifest" ] \
  || die "Rollback-Frontend-Snapshot ist nicht bytegenau"
for frontend_file in index.html app.bundle.js boot.js; do
  cmp -s "$rollback_frontend_snapshot/$frontend_file" \
    "$backup/old-frontend-$frontend_file" \
    || die "Rollback-Frontend-Snapshot weicht vom HTTP-Beweis ab"
done
printf '%s\n' "$rollback_frontend_manifest" \
  > "$backup/rollback-frontend.manifest.sha256"
rollback_frontend_snapshot_ready=1

# Alle Rename-Ziele liegen auf demselben Dateisystem; beide APP-Renames sind
# daher atomar. Der alte Checkout wird nie geloescht.
[ "$(stat -c %d -- "$APP")" = "$(stat -c %d -- "$quarantine")" ] \
  && [ "$(stat -c %d -- "$APP")" = "$(stat -c %d -- "$final_clone")" ] \
  || die "Final-Clone/APP/Quarantaene liegen nicht auf demselben Dateisystem"
assert_quiescent || die "Quiesce-Schranke direkt vor Alt-App-Rename fehlgeschlagen"
validate_preserved_runtime_state \
  || die "Runtime-Artefaktvertrag driftete direkt vor Alt-App-Rename"
quarantine_directory_matches \
  && original_app_directory_matches_at "$APP" \
  || die "APP-/Quarantaene-Inodevertrag driftete vor Alt-App-Rename"
forward_app_layout="$(rollback_app_layout_state)" \
  || die "APP-Layout konnte vor Alt-App-Rename nicht klassifiziert werden"
[ "$forward_app_layout" = old-at-app ] \
  || die "APP-Layout driftete vor Alt-App-Rename"
mv -T -- "$APP" "$quarantine/old-app"
old_app_quarantined=1
quarantine_directory_matches \
  || die "Quarantaene driftete nach Alt-App-Rename"
forward_app_layout="$(rollback_app_layout_state)" \
  || die "Alt-App-Rename ergab keinen klassifizierbaren Inodezustand"
[ "$forward_app_layout" = old-at-quarantine-app-absent ] \
  || die "Alt-App-Rename ergab keinen eindeutigen Inodezustand"
original_app_streamlit_parent_matches \
  || die "App-.streamlit-Parent driftete nach Quarantaene-Rename"
assert_quiescent || die "Quiesce-Schranke direkt vor Final-App-Rename fehlgeschlagen"
new_app_directory_matches_at "$final_clone" \
  || die "Final-Clone-Inode driftete vor Final-App-Rename"
forward_app_layout="$(rollback_app_layout_state)" \
  || die "APP-Layout konnte vor Final-App-Rename nicht klassifiziert werden"
[ "$forward_app_layout" = old-at-quarantine-app-absent ] \
  || die "Final-Clone/APP-Layout driftete vor Final-App-Rename"
mv -T -- "$final_clone" "$APP"
new_app_directory_matches_at "$APP" \
  || die "neuer App-.streamlit-Parent driftete beim Final-Rename"
forward_app_layout="$(rollback_app_layout_state)" \
  || die "Final-App-Rename ergab keinen klassifizierbaren Inodezustand"
[ "$forward_app_layout" = old-at-quarantine-new-at-app ] \
  && new_app_streamlit_parent_matches_at "$APP/.streamlit" \
  || die "neuer App-.streamlit-Parent driftete beim Final-Rename"
final_clone=''

# Produktions-venv am finalen Pfad, offline aus dem waehrend des Livebetriebs
# gebauten Wheelhouse. Absolute Shebangs zeigen dadurch sofort auf den Zielpfad.
assert_quiescent || die "Quiesce-Schranke vor finalem venv fehlgeschlagen"
python3 -I -m venv "$APP/venv"
"$APP/venv/bin/python" -m pip install --disable-pip-version-check \
  --no-index --find-links "$backup/wheels" -r "$APP/requirements.txt"
sha256sum "$APP/requirements.txt" | awk '{print $1}' \
  > "$APP/venv/.requirements.sha256"
chmod -R a+rX,go-w "$APP/venv"
chown -hR root:root "$APP/venv"

[ ! -e "$APP/.env" ] && [ ! -L "$APP/.env" ] \
  && [ ! -e "$APP/data_cache" ] && [ ! -L "$APP/data_cache" ] \
  && [ ! -e "$APP/.streamlit/secrets.toml" ] \
  && [ ! -L "$APP/.streamlit/secrets.toml" ] \
  || die "frischer Clone enthaelt unerwartete Runtime-Artefakte"

# Rohdaten und beide App-Dateien werden nie kopiert/geparst, sondern inodegleich
# verschoben. Vor jedem Rename wird der Altbaum erneut auf Prozesse geprueft.
assert_quiescent || die "Quiesce-Schranke vor .env-Rename fehlgeschlagen"
mv -T -- "$quarantine/old-app/.env" "$APP/.env"
assert_quiescent || die "Quiesce-Schranke vor App-secret-Rename fehlgeschlagen"
quarantine_directory_matches \
  && original_app_streamlit_parent_matches \
  && new_app_streamlit_parent_matches_at "$APP/.streamlit" \
  || die "App-secret-Quell-/Zielparent driftete vor Rename"
forward_secret_milestone="$(derive_new_app_secret_milestone \
  old-at-quarantine-new-at-app)" \
  || die "App-secret-Ort konnte vor Rename nicht klassifiziert werden"
[ "$forward_secret_milestone" = 0 ] \
  || die "App-secret lag unerwartet bereits im neuen Baum"
mv -T -- "$quarantine/old-app/.streamlit/secrets.toml" \
  "$APP/.streamlit/secrets.toml"
new_app_secret_moved=1
new_app_streamlit_parent_matches_at "$APP/.streamlit" \
  || die "neuer App-.streamlit-Parent driftete nach Secret-Rename"
forward_secret_milestone="$(derive_new_app_secret_milestone \
  old-at-quarantine-new-at-app)" \
  || die "App-secret-Ort konnte nach Rename nicht klassifiziert werden"
[ "$forward_secret_milestone" = 1 ] \
  || die "App-secret-Rename ergab keinen eindeutigen Inodezustand"
assert_quiescent || die "Quiesce-Schranke vor data_cache-Rename fehlgeschlagen"
mv -T -- "$quarantine/old-app/data_cache" "$APP/data_cache"
[ "$(stat -c '%d:%i' -- "$APP/.env")" = "$env_inode" ] \
  && [ "$(stat -c '%d:%i' -- "$APP/.streamlit/secrets.toml")" = \
       "$app_secret_inode" ] \
  && [ "$(stat -c '%d:%i' -- "$APP/data_cache")" = "$cache_inode" ] \
  || die "Runtime-Daten wurden nicht inodegleich verschoben"

# Neue Units setzen HOME=/var/lib/alpha-station-runtime. Das bekannte Home-
# Secret wird bytegleich, root-kontrolliert und fuer die Dienstgruppe lesbar
# installiert. Der StateDirectory-Parent bleibt systemd/service-eigen; diese
# bekannte Rename-Restunsicherheit wird durch Quiesce und Unit-Sandbox begrenzt.
assert_quiescent || die "Quiesce-Schranke vor Runtime-secret fehlgeschlagen"
[ "$(capture_global_secret_contract)" = "$global_secret_contract" ] \
  || die "Home-secret driftete nach Snapshot"
path_absent "$RUNTIME_HOME" \
  || die "Runtime-HOME erschien nach dem quiescenten Abwesenheitsvertrag"
install -d -o tradingbot -g tradingbot -m 0700 "$RUNTIME_HOME"
chown tradingbot:tradingbot "$RUNTIME_HOME"
chmod 0700 "$RUNTIME_HOME"
runtime_home_inode="$(stat -c '%d:%i' -- "$RUNTIME_HOME")" \
  || die "Runtime-HOME-Inode konnte nicht gebunden werden"
path_absent "$RUNTIME_HOME/.streamlit" \
  || die "Runtime-.streamlit erschien unerwartet"
install -d -o root -g tradingbot -m 0750 "$RUNTIME_HOME/.streamlit"
runtime_streamlit_inode="$(stat -c '%d:%i' -- "$RUNTIME_HOME/.streamlit")" \
  || die "Runtime-.streamlit-Inode konnte nicht gebunden werden"
[ ! -e "$RUNTIME_SECRET" ] && [ ! -L "$RUNTIME_SECRET" ] \
  || die "Runtime-secret erschien waehrend Migration"
install -o root -g tradingbot -m 0640 "$GLOBAL_SECRET" "$RUNTIME_SECRET"
runtime_secret_contract="$(capture_runtime_secret_contract)" \
  || die "Runtime-secret konnte nicht O_NOFOLLOW-gebunden werden"
runtime_secret_state_matches \
  && [ "${runtime_secret_contract##*|}" = \
       "$EXPECTED_HOME_SECRET_SHA256" ] \
  || die "Runtime-secret-Vertrag ist nicht exakt"
runuser -u tradingbot -- test -r "$RUNTIME_SECRET" \
  || die "Runtime-secret ist fuer Dienst nicht lesbar"
assert_quiescent || die "Dienstprozess blieb nach Runtime-Leseprobe aktiv"

# Den frisch verschobenen Cache mit dem gepinnten Guard validieren und
# normalisieren, bevor irgendein neuer Dienst ihn sieht. safe_deploy wiederholt
# dieselbe Schranke spaeter transaktional. Preservation bedeutet hier bewusst:
# bestehende Datei-Inodes/-Bytes bleiben erhalten; Owner/Mode/ACL werden
# dauerhaft gehaertet und das Guard-auth-Verzeichnis darf neu hinzukommen. Auch
# ein Rollback behaelt diese Haertung und loescht keine neuen Auth-Artefakte.
install -d -o root -g root -m 0700 "$backup/runtime-guard-prestart"
assert_quiescent || die "Quiesce-Schranke vor Runtime-State-Guard fehlgeschlagen"
current_trusted_home_ancestors="$(trusted_home_ancestor_contract)" \
  || die "Root-Vertrauenskette konnte vor Runtime-State-Guard nicht abgefragt werden"
[ "$current_trusted_home_ancestors" = "$trusted_home_ancestors" ] \
  || die "Root-Vertrauenskette driftete vor Runtime-State-Guard"
env APP_DIR="$APP" SERVICE_USER=tradingbot \
  RUNTIME_WORK_DIR="$backup/runtime-guard-prestart" \
  /bin/bash "$APP/deploy/runtime_state_guard.sh"
assert_quiescent || die "Runtime-State-Guard liess Prozesse/Dienste aktiv"

# Nur der unveraenderte Root-Frontend-Dienst wird vor safe_deploy gestartet,
# damit dieser den :3000-Kompatibilitaetsweg erkennt. API/BG bleiben bis nach
# Guard, Unit-Sync und gepinntem safe_deploy bewusst inaktiv.
systemctl start tradingbot-frontend.service
unit_is tradingbot-frontend.service active enabled
unit_is tradingbot-api.service inactive enabled
unit_is tradingbot-bg.service inactive enabled
unit_is tradingbot.service inactive disabled

for frontend_file in index.html app.bundle.js boot.js; do
  curl -fsS --max-time 5 "http://127.0.0.1:3000/$frontend_file" \
    -o "$backup/new-before-safe-deploy-$frontend_file" \
    && cmp -s "$APP/frontend/$frontend_file" \
      "$backup/new-before-safe-deploy-$frontend_file" \
    || die "Legacy-Frontend liefert vor safe_deploy falsche Bytes: $frontend_file"
done

# Ausschliesslich der frisch geklonte, gepinnte Vertrag wird als root
# ausgefuehrt. deploy/install.sh wird bewusst NICHT ausgefuehrt; nginx/KeyHero
# bleiben byte- und prozessidentisch.
assert_no_old_app_activity \
  || die "Altbaum-/Updater-Schranke vor safe_deploy fehlgeschlagen"
(
  cd "$APP"
  env HOME=/root GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=/dev/null \
    APP_DIR="$APP" TRUSTED_HOME="$TRUSTED_HOME" BRANCH=main \
    EXPECTED_REVISION="$EXPECTED_REVISION" COMMERCIAL_DEPLOY=auto INSTALL_DEPS=auto \
    /bin/bash "$APP/deploy/safe_deploy.sh"
)

[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" = \
  "$EXPECTED_REVISION" ] || die "aktiver Checkout ist nicht EXPECTED_REVISION"
[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse origin/main)" = \
  "$EXPECTED_REVISION" ] || die "origin/main driftete waehrend safe_deploy"
unit_is tradingbot-api.service active enabled
unit_is tradingbot-bg.service active enabled
unit_is tradingbot.service inactive disabled
unit_is tradingbot-frontend.service active enabled
require_systemctl_property tradingbot-frontend.service User root \
  "Legacy-Frontend-User wurde durch safe_deploy veraendert"
require_systemctl_property tradingbot-frontend.service WorkingDirectory \
  /home/tradingbot/app/frontend \
  "Legacy-Frontend-WorkingDirectory wurde durch safe_deploy veraendert"
post_safe_deploy_unit_contract="$(success_unit_contract)" \
  || die "vollstaendiger Effective-Unit-Vertrag nach safe_deploy weicht ab"
printf '%s\n' "$post_safe_deploy_unit_contract" \
  > "$backup/success-units.after-safe-deploy"
/bin/bash "$APP/deploy/health_check.sh" --runtime-build-only
curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
  > "$backup/final-runtime-health.json"
read -r final_revision final_bundle \
  <<< "$(health_identity "$backup/final-runtime-health.json")"
[ "$final_revision" = "${EXPECTED_REVISION:0:12}" ] \
  || die "finale API-Revision weicht ab"
runtime_secret_state_matches \
  || die "Runtime-secret wurde durch Unit-Start veraendert"
runuser -u tradingbot -- test -r "$RUNTIME_SECRET" \
  || die "Runtime-secret ist nach safe_deploy nicht lesbar"

# Auto-Updater erst nach Runtime-/Frontend-Nachweis installieren. Cron wird fuer
# den manuellen Probe kurz gestoppt, damit der danach beobachtete Logzuwachs und
# Journal-CMD eindeutig von einem echten Scheduler-Takt stammen.
assert_no_old_app_activity \
  || die "Altbaum-/Updater-Schranke vor Auto-Update-Installer fehlgeschlagen"
/bin/bash "$APP/deploy/install_auto_update.sh"
unit_is cron.service active enabled
assert_root_crontab_empty
crontab -l > "$backup/root-crontab.after-install"
cmp -s "$backup/root-crontab.before" "$backup/root-crontab.after-install" \
  || die "Auto-Update-Installer veraenderte die leere Root-crontab"
systemctl stop cron.service
unit_is cron.service inactive enabled
assert_no_updater_processes || die "Updater laeuft vor manueller Probe"
/bin/bash "$LAUNCHER" --probe >> "$LOG_FILE" 2>&1
manual_probe_line="$(tail -n 1 "$LOG_FILE" | tr -d '\r')"
printf '%s\n' "$manual_probe_line" | grep -Eq \
  "alpha-auto-update status=ok action=probe revision=${EXPECTED_REVISION:0:12} local=${EXPECTED_REVISION:0:12} remote=${EXPECTED_REVISION:0:12}$" \
  || die "manuelle Auto-Update-Probe ist nicht exakt"
probe_size="$(stat -c %s -- "$LOG_FILE")"
[[ "$probe_size" =~ ^[0-9]+$ ]] || die "Probe-Loggroesse ungueltig"
touch -r "$LOG_FILE" "$backup/probe-log.timestamp"
cron_wait_epoch="$(date +%s)"
systemctl start cron.service
unit_is cron.service active enabled
/bin/bash "$APP/deploy/health_check.sh" --auto-update-only

cron_deadline=$((cron_wait_epoch + 750))
cron_tick_proven=0
next_progress=$((cron_wait_epoch + 60))
cron_command="/bin/bash $LAUNCHER >> $LOG_FILE 2>&1"
while [ "$(date +%s)" -lt "$cron_deadline" ]; do
  sleep 15
  now_epoch="$(date +%s)"
  current_size="$(stat -c %s -- "$LOG_FILE")"
  if [ "$LOG_FILE" -nt "$backup/probe-log.timestamp" ] \
    && [[ "$current_size" =~ ^[0-9]+$ ]] \
    && [ "$current_size" -gt "$probe_size" ]; then
    tail -c "+$((probe_size + 1))" "$LOG_FILE" \
      > "$backup/cron-log.after-probe"
    cron_terminal_line="$(tail -n 1 "$backup/cron-log.after-probe" | tr -d '\r')"
    journalctl -u cron.service --since "@$cron_wait_epoch" --no-pager -o cat \
      > "$backup/cron-journal.after-probe"
    if printf '%s\n' "$cron_terminal_line" | grep -Eq \
      "^[-0-9T:Z]+ alpha-auto-update status=ok action=current revision=${EXPECTED_REVISION:0:12}$" \
      && grep -F "CMD ($cron_command)" \
        "$backup/cron-journal.after-probe" >/dev/null; then
      cron_tick_proven=1
      break
    fi
  fi
  if [ "$now_epoch" -ge "$next_progress" ]; then
    echo "Cron-Nachweis wartet weiter; maximal $((cron_deadline - now_epoch)) Sekunden."
    next_progress=$((now_epoch + 60))
  fi
done
[ "$cron_tick_proven" = 1 ] \
  || die "kein echter erfolgreicher Cron-Takt innerhalb von 750 Sekunden belegt"
/bin/bash "$APP/deploy/health_check.sh" --auto-update-only
[ "$(git -c safe.directory="$APP" -C "$APP" rev-parse HEAD)" = \
  "$EXPECTED_REVISION" ] \
  && [ "$(git -c safe.directory="$APP" -C "$APP" rev-parse origin/main)" = \
       "$EXPECTED_REVISION" ] \
  || die "Checkout/Remote weicht nach Cron-Takt ab"
assert_root_crontab_empty
crontab -l > "$backup/root-crontab.final"
cmp -s "$backup/root-crontab.before" "$backup/root-crontab.final" \
  || die "Root-crontab ist nicht bytegleich leer geblieben"
unit_is cron.service active enabled
unit_is tradingbot-api.service active enabled
unit_is tradingbot-bg.service active enabled
unit_is tradingbot.service inactive disabled
unit_is tradingbot-frontend.service active enabled
curl -fsS --max-time 15 http://127.0.0.1:8000/api/health \
  > "$backup/final-after-cron-health.json"
[ "$(health_identity "$backup/final-after-cron-health.json")" = \
  "${EXPECTED_REVISION:0:12} $final_bundle" ] \
  || die "finale Health-Identitaet driftete nach Cron-Nachweis"
for frontend_file in index.html app.bundle.js boot.js; do
  curl -fsS --max-time 15 "http://127.0.0.1:3000/$frontend_file" \
    -o "$backup/final-after-cron-$frontend_file" \
    && cmp -s "$APP/frontend/$frontend_file" \
      "$backup/final-after-cron-$frontend_file" \
    || die "Legacy-Frontend driftete nach Cron-Nachweis: $frontend_file"
done
runtime_secret_state_matches \
  || die "Runtime-secret driftete nach Cron-Nachweis"

# KeyHero/nginx wurde weder neu geladen noch konfiguriert.
final_nginx_manifest="$(nginx_manifest_digest)" \
  || die "finales nginx-Manifest konnte nicht fail-closed ermittelt werden"
[ "$final_nginx_manifest" = "$nginx_before" ] \
  || die "nginx-Dateien oder Symlink-Topologie wurden veraendert"
require_systemctl_property nginx.service MainPID "$nginx_pid" \
  "nginx/KeyHero wurde neu gestartet oder veraendert"
systemctl is-active --quiet nginx || die "nginx ist nicht aktiv"

# Das alte service-eigene Home-secret nach dem Dienststart nur noch lesend
# pruefen. Eine root-Metadatenmutation in seinem service-eigenen Parent waere
# TOCTOU-anfaellig. Aktiv ist bereits die root-kontrollierte Runtime-Kopie;
# die Legacy-Datei bleibt fuer diese Migration exakt im belegten Vorzustand.
final_global_secret_contract="$(capture_global_secret_contract)" \
  || die "Legacy-Home-secret ist kein sicher lesbares regulaeres File mehr"
[ "$final_global_secret_contract" = "$global_secret_contract" ] \
  || die "Legacy-Home-secret hat sich veraendert"
final_success_unit_contract="$(success_unit_contract)" \
  || die "finaler Effective-Unit-/Disk-/Runtime-HOME-Vertrag weicht ab"
[ "$final_success_unit_contract" = "$post_safe_deploy_unit_contract" ] \
  || die "Effective-Unit-Vertrag driftete zwischen safe_deploy und Cron-Beweis"
printf '%s\n' "$final_success_unit_contract" \
  > "$backup/success-units.final"
account_is_closed \
  || die "tradingbot Account/Linger war vor dem letzten Restore nicht mehr geschlossen"
restore_service_login \
  || die "tradingbot Login-/Linger-Zustand konnte nicht exakt restauriert werden"

trap - EXIT INT TERM
echo "MIGRATION OK"
echo "Revision:      $EXPECTED_REVISION"
echo "Frontend:      $final_bundle"
echo "Cron-Beweis:   $backup/cron-journal.after-probe"
echo "Quarantaene:   $quarantine"
echo "Rollbackdaten: $backup"
echo "Alter Checkout, Preflight-venv und alle Sicherungen wurden NICHT geloescht."
