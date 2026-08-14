from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from test_deploy_auto_update import _bash, _write_executable


ROOT = Path(__file__).resolve().parent


def _run_bash(
    script: Path, *, env: dict[str, str], timeout: int = 45
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    run_env.update(env)
    return subprocess.run(
        [_bash(), "--noprofile", "--norc", script.as_posix()],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _runtime_guard_fixture(tmp_path: Path) -> tuple[Path, dict[str, str], Path, Path]:
    app = tmp_path / "home" / "tradingbot" / "app"
    cache = app / "data_cache"
    required = [cache / "auth", cache / "runtime", cache / "runtime" / "cache"]
    for path in required:
        path.mkdir(parents=True, exist_ok=True)

    fake_bin = tmp_path / "runtime-bin"
    fake_bin.mkdir()
    calls = tmp_path / "runtime-calls.txt"
    paths_file = tmp_path / "runtime-paths.txt"
    paths_file.write_text(
        "".join(f"{path.as_posix()}\n" for path in required), encoding="utf-8"
    )
    work_dir = tmp_path / "root-private-work"
    work_dir.mkdir()

    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$FAKE_RUNTIME_CALLS"
case "$1" in
  stop) exit "${FAKE_STOP_FAILURE:-0}" ;;
  show)
    case "$2" in
      --property=LoadState)
        [ "${FAKE_UNIT_QUERY_FAIL:-}" != load ] \
          || { printf '%s\n' "${FAKE_UNIT_LOAD_STATE:-loaded}"; exit "${FAKE_UNIT_QUERY_RC:-88}"; }
        printf '%s\n' "${FAKE_UNIT_LOAD_STATE:-loaded}"
        ;;
      --property=ActiveState)
        active_state="${FAKE_UNIT_ACTIVE_STATE:-inactive}"
        [ "${FAKE_UNIT_STILL_ACTIVE:-0}" = 1 ] && active_state=active
        [ "${FAKE_UNIT_QUERY_FAIL:-}" != active ] \
          || { printf '%s\n' "$active_state"; exit "${FAKE_UNIT_QUERY_RC:-88}"; }
        printf '%s\n' "$active_state"
        ;;
      *) exit 90 ;;
    esac
    ;;
  is-active)
    [ "${FAKE_UNIT_QUERY_FAIL:-}" != active ] \
      || { printf '%s\n' "${FAKE_UNIT_ACTIVE_STATE:-inactive}"; exit "${FAKE_UNIT_QUERY_RC:-88}"; }
    [ "${FAKE_UNIT_STILL_ACTIVE:-0}" = 1 ] && exit 0 || exit 3
    ;;
esac
exit 0
""",
    )
    _write_executable(
        fake_bin / "pgrep",
        """#!/bin/sh
printf 'pgrep %s\n' "$*" >> "$FAKE_RUNTIME_CALLS"
case "$1" in
  -u)
    [ "${FAKE_PGREP_QUERY_FAIL:-}" != service ] \
      || exit "${FAKE_PGREP_QUERY_RC:-88}"
    if [ "${FAKE_REMAINING_SERVICE_PROCESS:-0}" = 1 ]; then printf '4242\n'; exit 0; fi
    ;;
  -f)
    [ "${FAKE_PGREP_QUERY_FAIL:-}" != root ] \
      || exit "${FAKE_PGREP_QUERY_RC:-88}"
    if [ "${FAKE_REMAINING_ROOT_LEGACY_PROCESS:-0}" = 1 ]; then printf '4243\n'; exit 0; fi
    ;;
esac
exit 1
""",
    )
    _write_executable(
        fake_bin / "id",
        """#!/bin/sh
case "$1" in -u|-g) printf '1000\n' ;; *) exit 0 ;; esac
""",
    )
    _write_executable(
        fake_bin / "stat",
        r"""#!/bin/sh
eval "path=\${$#}"
kind=directory
links=1
dev=11
if [ "${FAKE_RUNTIME_UNSAFE_PATH:-}" = "$path" ]; then
  case "${FAKE_RUNTIME_UNSAFE_KIND:-}" in
    symlink) kind='symbolic link' ;;
    hardlink) kind='regular file'; links=2 ;;
    special) kind='fifo' ;;
    cross-device) dev=22 ;;
  esac
fi
printf '%s|%s|%s|1000|1000|700\n' "$kind" "$dev" "$links"
""",
    )
    _write_executable(
        fake_bin / "find",
        """#!/bin/sh
while IFS= read -r path || [ -n "$path" ]; do printf '%s\\0' "$path"; done < "$FAKE_RUNTIME_PATHS_FILE"
""",
    )
    _write_executable(
        fake_bin / "mountpoint",
        r"""#!/bin/sh
eval "path=\${$#}"
[ "${FAKE_RUNTIME_UNSAFE_KIND:-}" = mount ] && [ "$path" = "$FAKE_RUNTIME_UNSAFE_PATH" ]
""",
    )
    for tool in ("chown", "chmod", "setfacl"):
        _write_executable(
            fake_bin / tool,
            f"#!/bin/sh\nprintf '{tool} %s\\n' \"$*\" >> \"$FAKE_RUNTIME_CALLS\"\n",
        )
    _write_executable(
        fake_bin / "install",
        r"""#!/bin/sh
printf 'install %s\n' "$*" >> "$FAKE_RUNTIME_CALLS"
eval "path=\${$#}"
mkdir -p "$path"
""",
    )

    env = {
        "APP_DIR": app.as_posix(),
        "SERVICE_USER": "tradingbot",
        "RUNTIME_WORK_DIR": work_dir.as_posix(),
        "RUNTIME_SYSTEMCTL_BIN": (fake_bin / "systemctl").as_posix(),
        "RUNTIME_PGREP_BIN": (fake_bin / "pgrep").as_posix(),
        "RUNTIME_ID_BIN": (fake_bin / "id").as_posix(),
        "RUNTIME_STAT_BIN": (fake_bin / "stat").as_posix(),
        "RUNTIME_FIND_BIN": (fake_bin / "find").as_posix(),
        "RUNTIME_MOUNTPOINT_BIN": (fake_bin / "mountpoint").as_posix(),
        "RUNTIME_CHOWN_BIN": (fake_bin / "chown").as_posix(),
        "RUNTIME_CHMOD_BIN": (fake_bin / "chmod").as_posix(),
        "RUNTIME_SETFACL_BIN": (fake_bin / "setfacl").as_posix(),
        "RUNTIME_INSTALL_BIN": (fake_bin / "install").as_posix(),
        "FAKE_RUNTIME_CALLS": calls.as_posix(),
        "FAKE_RUNTIME_PATHS_FILE": paths_file.as_posix(),
        "PATH": os.pathsep.join(
            (str(fake_bin), str(Path(_bash()).resolve().parent), os.environ.get("PATH", ""))
        ),
    }
    return cache, env, calls, paths_file


def test_runtime_guard_quiesces_current_and_legacy_services_before_mutation(
    tmp_path: Path,
) -> None:
    cache, env, calls, paths_file = _runtime_guard_fixture(tmp_path)

    result = _run_bash(ROOT / "deploy" / "runtime_state_guard.sh", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    observed = calls.read_text(encoding="utf-8")
    for unit in (
        "tradingbot-api.service",
        "tradingbot-bg.service",
        "tradingbot.service",
        "tradingbot-frontend.service",
    ):
        assert f"systemctl stop {unit}" in observed
    assert observed.index("systemctl stop") < observed.index("chown")
    assert "pgrep -u 1000" in observed


@pytest.mark.parametrize(
    ("unsafe_kind", "unsafe_name", "expected"),
    [
        ("symlink", "runtime", "symbolic link"),
        ("hardlink", "attacker.json", "hard link"),
        ("special", "attacker.fifo", "special file"),
        ("cross-device", "mounted", "different filesystem"),
        ("mount", "mounted", "mount point"),
    ],
)
def test_runtime_guard_rejects_unsafe_tree_before_service_ownership(
    tmp_path: Path, unsafe_kind: str, unsafe_name: str, expected: str
) -> None:
    cache, env, calls, paths_file = _runtime_guard_fixture(tmp_path)
    unsafe_path = cache / unsafe_name
    with paths_file.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(unsafe_path.as_posix() + "\n")
    env.update(
        FAKE_RUNTIME_UNSAFE_PATH=unsafe_path.as_posix(),
        FAKE_RUNTIME_UNSAFE_KIND=unsafe_kind,
    )

    result = _run_bash(ROOT / "deploy" / "runtime_state_guard.sh", env=env)

    assert result.returncode != 0
    output = (result.stdout + result.stderr).lower()
    assert expected in output
    observed = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert f"chown tradingbot:tradingbot {unsafe_path.as_posix()}" not in observed
    assert not any(
        line.startswith("install ") and unsafe_path.as_posix() in line
        for line in observed.splitlines()
    ), "root install must not traverse an unvalidated runtime component"


@pytest.mark.parametrize(
    ("failure_env", "expected"),
    [
        ({"FAKE_UNIT_STILL_ACTIVE": "1"}, "service remains active"),
        ({"FAKE_REMAINING_SERVICE_PROCESS": "1"}, "service-user process remains"),
        (
            {"FAKE_UNIT_QUERY_FAIL": "load", "FAKE_UNIT_QUERY_RC": "88"},
            "unit state query failed",
        ),
        (
            {"FAKE_UNIT_QUERY_FAIL": "active", "FAKE_UNIT_QUERY_RC": "88"},
            "unit state query failed",
        ),
        ({"FAKE_UNIT_LOAD_STATE": "masked"}, "unexpected loadstate"),
        ({"FAKE_UNIT_ACTIVE_STATE": "activating"}, "not safely inactive"),
        ({"FAKE_PGREP_QUERY_FAIL": "service"}, "process query failed"),
        ({"FAKE_REMAINING_ROOT_LEGACY_PROCESS": "1"}, "legacy root process remains"),
        ({"FAKE_PGREP_QUERY_FAIL": "root"}, "root process query failed"),
    ],
)
def test_runtime_guard_fails_before_cache_mutation_when_quiesce_is_incomplete(
    tmp_path: Path, failure_env: dict[str, str], expected: str
) -> None:
    cache, env, calls, paths_file = _runtime_guard_fixture(tmp_path)
    env.update(failure_env)

    result = _run_bash(ROOT / "deploy" / "runtime_state_guard.sh", env=env)

    assert result.returncode != 0
    assert expected in (result.stdout + result.stderr).lower()
    observed = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "chown " not in observed
    assert "install " not in observed


def test_runtime_guard_accepts_explicitly_missing_units_without_stopping_them(
    tmp_path: Path,
) -> None:
    cache, env, calls, paths_file = _runtime_guard_fixture(tmp_path)
    env["FAKE_UNIT_LOAD_STATE"] = "not-found"

    result = _run_bash(ROOT / "deploy" / "runtime_state_guard.sh", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    observed = calls.read_text(encoding="utf-8")
    assert "systemctl stop" not in observed


def test_runtime_guard_requires_acl_tool_before_stopping_services(tmp_path: Path) -> None:
    cache, env, calls, paths_file = _runtime_guard_fixture(tmp_path)
    env["RUNTIME_SETFACL_BIN"] = (tmp_path / "missing-setfacl").as_posix()

    result = _run_bash(ROOT / "deploy" / "runtime_state_guard.sh", env=env)

    assert result.returncode != 0
    assert "required runtime tool is missing" in (result.stdout + result.stderr).lower()
    observed = calls.read_text(encoding="utf-8") if calls.exists() else ""
    assert "systemctl stop" not in observed
    assert "chown " not in observed


def test_safe_deploy_uses_private_tempdir_and_never_migrates_global_tmp_state() -> None:
    deploy = (ROOT / "deploy" / "safe_deploy.sh").read_text(encoding="utf-8")

    assert "DEPLOY_TMP_DIR=" in deploy
    assert "mktemp -d" in deploy
    assert "/tmp/tradingbot-pip" not in deploy
    assert "/tmp/tradingbot-health.json" not in deploy
    assert '"/tmp/$state_file"' not in deploy
    assert "runtime_state_guard.sh" in deploy


def test_runtime_guard_and_health_do_not_use_service_replaceable_bind_source() -> None:
    guard = (ROOT / "deploy" / "runtime_state_guard.sh").read_text(encoding="utf-8")
    health = (ROOT / "deploy" / "health_check.sh").read_text(encoding="utf-8")

    assert '"$RUNTIME_INSTALL_BIN" -d -m 0700 -o root -g root "$CACHE_DIR/runtime"' not in guard
    assert '"$CACHE_DIR/runtime/cache"' not in guard
    assert "/var/lib/alpha-station-runtime/$base" in health


def test_recovery_runbook_quarantines_old_checkout_and_executes_only_fresh_clone() -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    section = runbook.split("Wenn ein bestehender Server", 1)[1].split("\n---", 1)[0]

    assert "systemctl stop tradingbot-api.service tradingbot-bg.service" in section
    assert "tradingbot.service tradingbot-frontend.service" in section
    assert "AlphaStation.git" in section
    assert "git clone" in section
    assert "quarantine" in section.lower()
    assert "python3 -m venv" in section
    assert "/home/tradingbot/app/deploy/auto_update.sh" not in section
    assert "chown -R root:root /home/tradingbot" not in section
    assert "sitecustomize" in section
    assert ".git/config" in section
    assert "allowlist" in section.lower()


def test_recovery_runbook_is_failclosed_before_quarantining_legacy_tree() -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    section = runbook.split("Wenn ein bestehender Server", 1)[1].split("\n---", 1)[0]

    cron_stop = "systemctl stop cron.service || true"
    cron_check = "assert_unit_safely_inactive cron.service"
    unit_barrier = "\nassert_bootstrap_units_inactive\n"
    account_lock = "usermod -L -s /usr/sbin/nologin tradingbot"
    assert cron_stop in section
    assert cron_check in section
    assert section.find(unit_barrier, section.index(cron_stop)) > section.index(cron_stop)
    assert "systemctl is-active --quiet" not in section
    assert "systemctl show --property=LoadState --value" in section
    assert "systemctl show --property=ActiveState --value" in section
    assert "inactive|failed" in section
    assert "not-found" in section
    assert "systemctl-Abfrage fehlgeschlagen" in section
    assert account_lock in section
    assert section.index(account_lock) < section.index('pkill -TERM -u "$service_uid"')
    assert "loginctl disable-linger tradingbot" in section
    assert "/usr/local/sbin/alpha-station-auto-update" in section
    assert "/home/tradingbot/app/deploy/(auto_update|safe_deploy)" in section
    assert "/tmp/alpha-safe-deploy" in section
    assert "wait_for_legacy_root_activity_to_end" in section
    assert 'for proc in /proc/[0-9]*; do' in section
    assert '"$proc"/fd/*' in section
    assert '"$proc/cwd"' in section
    assert 'pgrep_activity_checked -x cron' in section
    assert 'pgrep_activity_checked -x crond' in section
    assert 'pgrep_activity_checked -f -- "$legacy_root_pattern"' in section
    assert 'pgrep_activity_checked -u "$service_uid"' in section
    assert 'pgrep -u "$service_uid" >/dev/null 2>&1 || break' not in section
    assert "Root-Updater werden nicht per pkill" in section
    assert "assert_bootstrap_units_inactive" in section
    assert section.count("assert_bootstrap_quiescent") >= 3
    app_lstat = "app_kind=\"$(LC_ALL=C stat -c '%F' -- /home/tradingbot/app)\""
    assert app_lstat in section
    assert section.rindex("assert_bootstrap_quiescent") < section.index(app_lstat)


@pytest.mark.parametrize(
    ("load_state", "active_state", "failed_query", "expected_success"),
    [
        ("not-found", "active", "", True),
        ("loaded", "inactive", "", True),
        ("loaded", "failed", "", True),
        ("loaded", "active", "", False),
        ("loaded", "inactive", "load", False),
        ("loaded", "inactive", "active", False),
        ("loaded", "unknown", "", False),
        ("error", "inactive", "", False),
        ("masked", "failed", "", False),
        ("unknown", "inactive", "", False),
        ("", "inactive", "", False),
        ("loaded\nnot-found", "inactive", "", False),
    ],
)
def test_recovery_runbook_unit_state_guard_is_failclosed(
    tmp_path: Path,
    load_state: str,
    active_state: str,
    failed_query: str,
    expected_success: bool,
) -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    block = runbook.split("sudo /bin/bash <<'ALPHA_FRESH_BOOTSTRAP'", 1)[1].split(
        "\nALPHA_FRESH_BOOTSTRAP", 1
    )[0]
    guard = block.split("assert_unit_safely_inactive() {", 1)[1].split(
        "\n}\n\nassert_bootstrap_units_inactive()", 1
    )[0]
    script = tmp_path / "unit-state-guard.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "systemctl() {\n"
        "  case \"$2\" in\n"
        "    --property=LoadState)\n"
        "      [ \"${FAKE_FAILED_QUERY:-}\" != load ] || return 71\n"
        "      printf '%s\\n' \"$FAKE_LOAD_STATE\"\n"
        "      ;;\n"
        "    --property=ActiveState)\n"
        "      [ \"${FAKE_FAILED_QUERY:-}\" != active ] || return 72\n"
        "      printf '%s\\n' \"$FAKE_ACTIVE_STATE\"\n"
        "      ;;\n"
        "    *) return 73 ;;\n"
        "  esac\n"
        "}\n"
        "assert_unit_safely_inactive() {"
        + guard
        + "\n}\n"
        "assert_unit_safely_inactive fixture.service\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _run_bash(
        script,
        env={
            "FAKE_LOAD_STATE": load_state,
            "FAKE_ACTIVE_STATE": active_state,
            "FAKE_FAILED_QUERY": failed_query,
        },
    )

    assert (result.returncode == 0) is expected_success, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("pgrep_env", "expected_success"),
    [
        ({}, True),
        ({"FAKE_SERVICE_PGREP_RC": "2", "FAKE_PGREP_PAYLOAD": "4242"}, False),
        ({"FAKE_CRON_PGREP_RC": "2", "FAKE_PGREP_PAYLOAD": "4242"}, False),
        ({"FAKE_CROND_PGREP_RC": "2", "FAKE_PGREP_PAYLOAD": "4242"}, False),
        ({"FAKE_ROOT_PGREP_RC": "2", "FAKE_PGREP_PAYLOAD": "4242"}, False),
    ],
)
def test_recovery_runbook_pgrep_queries_are_failclosed_before_quarantine(
    tmp_path: Path, pgrep_env: dict[str, str], expected_success: bool
) -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    block = runbook.split("sudo /bin/bash <<'ALPHA_FRESH_BOOTSTRAP'", 1)[1].split(
        "\nALPHA_FRESH_BOOTSTRAP", 1
    )[0]
    pgrep_helper = "pgrep_activity_checked() {" + block.split(
        "pgrep_activity_checked() {", 1
    )[1].split("\n}\n\nservice_uid=", 1)[0] + "\n}\n"
    activity_functions = "legacy_app=" + block.split("legacy_app=", 1)[1].split(
        "\n\nassert_bootstrap_quiescent()", 1
    )[0]
    quiescent_function = "assert_bootstrap_quiescent() {" + block.split(
        "assert_bootstrap_quiescent() {", 1
    )[1].split("\n}\nwait_for_legacy_root_activity_to_end", 1)[0] + "\n}\n"
    sentinel = tmp_path / "quarantine-mutation-sentinel"
    script = tmp_path / "pgrep-failclosed-guard.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        "service_uid=1000\n"
        "pgrep() {\n"
        "  local rc=1\n"
        "  case \"$1:${2:-}\" in\n"
        "    -u:*) rc=\"${FAKE_SERVICE_PGREP_RC:-1}\" ;;\n"
        "    -x:cron) rc=\"${FAKE_CRON_PGREP_RC:-1}\" ;;\n"
        "    -x:crond) rc=\"${FAKE_CROND_PGREP_RC:-1}\" ;;\n"
        "    -f:--) rc=\"${FAKE_ROOT_PGREP_RC:-1}\" ;;\n"
        "  esac\n"
        "  [ -z \"${FAKE_PGREP_PAYLOAD:-}\" ] || printf '%s\\n' \"$FAKE_PGREP_PAYLOAD\"\n"
        "  return \"$rc\"\n"
        "}\n"
        "assert_bootstrap_units_inactive() { return 0; }\n"
        + pgrep_helper
        + activity_functions
        + "\n"
        + quiescent_function
        + "wait_for_legacy_root_activity_to_end\n"
        + "assert_bootstrap_quiescent\n"
        + f"printf 'mutated\\n' > '{sentinel.as_posix()}'\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _run_bash(script, env=pgrep_env)

    assert (result.returncode == 0) is expected_success, result.stdout + result.stderr
    assert sentinel.exists() is expected_success
    if not expected_success:
        assert "pgrep" in (result.stdout + result.stderr).lower()


def test_recovery_runbook_never_chmods_a_quarantined_app_symlink() -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    section = runbook.split("Wenn ein bestehender Server", 1)[1].split("\n---", 1)[0]

    assert 'quarantine_root="$(mktemp -d /root/alpha-station-quarantine.XXXXXX)"' in section
    assert '"symbolic link")' in section
    assert 'mv -T -- /home/tradingbot/app "$quarantine_root/rejected-app-symlink"' in section
    assert 'chown --no-dereference root:root "$quarantine_root/rejected-app-symlink"' in section
    assert 'unlink -- "$quarantine_root/rejected-app-symlink"' in section
    assert 'chmod 0700 "$quarantine_root/rejected-app-symlink"' not in section
    assert '"directory")' in section
    assert 'mountpoint -q -- /home/tradingbot/app' in section
    assert 'chmod 0700 "$quarantine_root/app"' in section
    assert 'chmod 0700 "$quarantine"' not in section


def test_recovery_runbook_requires_reprovision_for_possible_root_compromise() -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    section = runbook.split("Wenn ein bestehender Server", 1)[1].split("\n---", 1)[0]

    assert "kein Root-Kompromiss-Indikator" in section
    assert "VPS neu provisionieren" in section
    assert "nicht in-place" in section
    assert "service-schreibbarer Baum als root" in section


def test_fresh_bootstrap_runbook_shell_block_has_valid_bash_syntax(tmp_path: Path) -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    block = runbook.split("sudo /bin/bash <<'ALPHA_FRESH_BOOTSTRAP'", 1)[1].split(
        "\nALPHA_FRESH_BOOTSTRAP", 1
    )[0]
    script = tmp_path / "fresh-bootstrap.sh"
    script.write_text("#!/usr/bin/env bash\n" + block.lstrip("\n"), encoding="utf-8", newline="\n")

    result = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-n", script.as_posix()],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_install_docs_copy_into_root_owned_destination_via_privileged_command() -> None:
    guide = (ROOT / "deploy" / "DEPLOY_ANLEITUNG.md").read_text(encoding="utf-8")

    assert "sudo /bin/bash <<'ALPHA_SOURCE'" in guide
    assert "cp -a" in guide
    git_option = guide.split("**Option A", 1)[1].split("**Option B", 1)[0]
    assert "mktemp -d" in git_option
    assert "cd /tmp" not in git_option
    assert "remote get-url origin" in git_option
    assert "status --porcelain" in git_option
    assert "/tmp/AlphaStation" not in git_option
    scp_option = guide.split("**Option B", 1)[1].split("## Schritt 4", 1)[0]
    assert "root@DEINE_SERVER_IP:/root/" in scp_option
    assert "/tmp/tradingbot-upload" not in scp_option


def test_fresh_install_includes_acl_tool_required_by_runtime_guard() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    apt_line = next(line for line in installer.splitlines() if "apt install" in line)
    assert " acl " in f" {apt_line} "


def test_installer_never_recursively_trusts_service_home_and_uses_isolated_python() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert "chown -R root:root /home/tradingbot" not in installer
    assert 'chown --no-dereference root:root "$SERVICE_HOME"' in installer
    assert "python3 -I -m venv" in installer


def test_installer_accepts_only_pretrusted_fresh_source_without_recursive_chown() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert 'validate_fresh_source_tree "$APP_DIR"' in installer
    assert 'chown -R root:root "$APP_DIR"' not in installer
    assert 'chmod -R go-w "$APP_DIR"' not in installer
    assert "symbolic link" in installer
    assert "hard link" in installer
    assert "special file" in installer
    assert "mount point" in installer
    assert "source path is not root-owned" in installer
    assert "source path is group/world-writable" in installer


def test_installer_checks_home_and_existing_app_before_root_mutation() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    home_guard = 'if [ -L "$SERVICE_HOME" ] || [ ! -d "$SERVICE_HOME" ]; then'
    home_chown = 'chown --no-dereference root:root "$SERVICE_HOME"'
    app_guard = 'if [ -e "$APP_DIR" ] || [ -L "$APP_DIR" ]; then'
    app_create = 'install -d -m 0755 -o root -g root "$APP_DIR"'
    quiesce_call = "\nquiesce_install_source_user\n"
    assert home_guard in installer
    assert home_chown in installer
    assert installer.index(home_guard) < installer.index(home_chown)
    assert installer.index(quiesce_call) < installer.index(home_chown)
    assert installer.index(quiesce_call) < installer.index(app_create)
    assert app_guard in installer
    assert installer.index(app_guard) < installer.index(app_create)


@pytest.mark.parametrize(
    ("failure_env", "expected"),
    [
        (
            {"FAKE_INSTALL_UNIT_QUERY_FAIL": "load", "FAKE_INSTALL_QUERY_RC": "88"},
            "unit state query failed",
        ),
        (
            {"FAKE_INSTALL_UNIT_QUERY_FAIL": "active", "FAKE_INSTALL_QUERY_RC": "88"},
            "unit state query failed",
        ),
        ({"FAKE_INSTALL_UNIT_LOAD_STATE": "masked"}, "unexpected loadstate"),
        ({"FAKE_INSTALL_UNIT_ACTIVE_STATE": "activating"}, "not safely inactive"),
        ({"FAKE_INSTALL_ROOT_PROCESS": "1"}, "legacy root process remains"),
    ],
)
def test_install_quiesce_fails_before_mutation_on_ambiguous_state_or_root_process(
    tmp_path: Path, failure_env: dict[str, str], expected: str
) -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    functions = "quiesce_install_source_user() {" + installer.split(
        "quiesce_install_source_user() {", 1
    )[1].split("\n\nvalidate_fresh_source_tree()", 1)[0]
    fake_bin = tmp_path / "install-quiesce-bin"
    fake_bin.mkdir()
    calls = tmp_path / "install-quiesce-calls.txt"
    sentinel = tmp_path / "root-mutation-sentinel"
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
printf 'systemctl %s\n' "$*" >> "$FAKE_INSTALL_CALLS"
case "$1" in
  stop) exit 0 ;;
  show)
    case "$2" in
      --property=LoadState)
        [ "${FAKE_INSTALL_UNIT_QUERY_FAIL:-}" != load ] \
          || { printf '%s\n' "${FAKE_INSTALL_UNIT_LOAD_STATE:-loaded}"; exit "${FAKE_INSTALL_QUERY_RC:-88}"; }
        printf '%s\n' "${FAKE_INSTALL_UNIT_LOAD_STATE:-loaded}"
        ;;
      --property=ActiveState)
        [ "${FAKE_INSTALL_UNIT_QUERY_FAIL:-}" != active ] \
          || { printf '%s\n' "${FAKE_INSTALL_UNIT_ACTIVE_STATE:-inactive}"; exit "${FAKE_INSTALL_QUERY_RC:-88}"; }
        printf '%s\n' "${FAKE_INSTALL_UNIT_ACTIVE_STATE:-inactive}"
        ;;
    esac
    ;;
  is-active)
    [ "${FAKE_INSTALL_UNIT_QUERY_FAIL:-}" != active ] \
      || { printf '%s\n' "${FAKE_INSTALL_UNIT_ACTIVE_STATE:-inactive}"; exit "${FAKE_INSTALL_QUERY_RC:-88}"; }
    exit 3
    ;;
esac
""",
    )
    _write_executable(
        fake_bin / "pgrep",
        """#!/bin/sh
printf 'pgrep %s\n' "$*" >> "$FAKE_INSTALL_CALLS"
if [ "$1" = -f ] && [ "${FAKE_INSTALL_ROOT_PROCESS:-0}" = 1 ]; then
  printf '4243\n'
  exit 0
fi
exit 1
""",
    )
    _write_executable(
        fake_bin / "id",
        "#!/bin/sh\n[ \"$1\" = -u ] && printf '1000\\n'\n",
    )
    script = tmp_path / "install-quiesce-contract.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -Eeuo pipefail\n"
        f"APP_DIR='{(tmp_path / 'home/tradingbot/app').as_posix()}'\n"
        f"INSTALL_SYSTEMCTL_BIN='{(fake_bin / 'systemctl').as_posix()}'\n"
        f"INSTALL_PGREP_BIN='{(fake_bin / 'pgrep').as_posix()}'\n"
        f"INSTALL_ID_BIN='{(fake_bin / 'id').as_posix()}'\n"
        + functions
        + "\nquiesce_install_source_user\n"
        + f"printf 'mutated\\n' > '{sentinel.as_posix()}'\n",
        encoding="utf-8",
        newline="\n",
    )
    env = os.environ.copy()
    env.update(
        {
            "PATH": os.pathsep.join(
                (str(fake_bin), str(Path(_bash()).resolve().parent), env.get("PATH", ""))
            ),
            "FAKE_INSTALL_CALLS": calls.as_posix(),
            **failure_env,
        }
    )

    result = _run_bash(script, env=env)

    assert result.returncode != 0
    assert expected in (result.stdout + result.stderr).lower()
    assert not sentinel.exists()
