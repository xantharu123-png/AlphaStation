from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
from configparser import ConfigParser
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parent


def _bash() -> str:
    for name in ("bash", "sh"):
        candidate = shutil.which(name)
        if candidate:
            return candidate

    git = shutil.which("git")
    if git:
        git_root = Path(git).resolve().parent.parent
        for relative in (Path("usr/bin/bash.exe"), Path("usr/bin/sh.exe")):
            candidate = git_root / relative
            if candidate.exists():
                return str(candidate)
    pytest.skip("Bash is required for deploy-script behavior tests")


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8", newline="\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_bash(
    script: Path,
    *args: str,
    env: dict[str, str] | None = None,
    timeout: int = 30,
) -> subprocess.CompletedProcess[str]:
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    return subprocess.run(
        [_bash(), "--noprofile", "--norc", script.as_posix(), *args],
        cwd=ROOT,
        env=run_env,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _run_installer(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return _run_bash(
        ROOT / "deploy/install_auto_update.sh",
        *args,
        env=env,
        timeout=90,
    )


def _fake_git_bin(tmp_path: Path, *, local: str, remote: str) -> tuple[Path, Path]:
    fake_bin = tmp_path / "fake-bin"
    fake_bin.mkdir()
    calls = tmp_path / "git-calls.txt"
    _write_executable(
        fake_bin / "git",
        f"""#!/bin/sh
printf '%s\\n' "$*" >> "$FAKE_GIT_CALLS"
if [ "$1" != "-c" ] || [ "$2" != "safe.directory=$APP_DIR" ] || [ "$3" != "-C" ] || [ "$4" != "$APP_DIR" ]; then
  echo "missing command-scoped safe.directory" >&2
  exit 91
fi
shift 4
case "$1 $2" in
  "fetch origin") [ "${{FAKE_GIT_FETCH_FAIL:-0}}" = 1 ] && exit 94 || exit 0 ;;
  "rev-parse HEAD") printf '%s\\n' '{local}'; exit 0 ;;
  "rev-parse origin/main") printf '%s\\n' '{remote}'; exit 0 ;;
  "rev-parse --short=12")
    if [ "$3" = "HEAD" ]; then printf '%s\\n' '{local[:12]}'; else printf '%s\\n' '{remote[:12]}'; fi
    exit 0
    ;;
  "show {remote}:deploy/safe_deploy.sh")
    cat <<'TARGET'
#!/bin/sh
printf 'expected=%s\\n' "$EXPECTED_REVISION" > "$FAKE_DEPLOY_ENV"
printf 'count=%s\\n' "$GIT_CONFIG_COUNT" >> "$FAKE_DEPLOY_ENV"
eval "printf 'key=%s\\n' \"\\$GIT_CONFIG_KEY_$((GIT_CONFIG_COUNT - 1))\"" >> "$FAKE_DEPLOY_ENV"
eval "printf 'value=%s\\n' \"\\$GIT_CONFIG_VALUE_$((GIT_CONFIG_COUNT - 1))\"" >> "$FAKE_DEPLOY_ENV"
exit "${{FAKE_DEPLOY_EXIT:-0}}"
TARGET
    exit 0
    ;;
esac
echo "unexpected git call: $*" >&2
exit 92
""",
    )
    _write_executable(fake_bin / "flock", "#!/bin/sh\nexit 0\n")
    _write_executable(fake_bin / "logger", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "systemctl",
        """#!/bin/sh
[ -z "${FAKE_SYSTEMCTL_CALLS:-}" ] || printf '%s\n' "$*" >> "$FAKE_SYSTEMCTL_CALLS"
state_file="${FAKE_SYSTEMCTL_STATE_FILE:-}"
enabled_file="${state_file}.enabled"
query_failure_rc="${FAKE_SYSTEMCTL_QUERY_FAILURE_RC:-88}"
case "$1" in
  show)
    case "$2" in
      --property=LoadState)
        [ "${FAKE_SYSTEMCTL_QUERY_FAIL:-}" != load ] || exit "$query_failure_rc"
        printf '%s\n' "${FAKE_SYSTEMCTL_LOAD_STATE:-loaded}"
        ;;
      --property=ActiveState)
        [ "${FAKE_SYSTEMCTL_QUERY_FAIL:-}" != active ] || exit "$query_failure_rc"
        if [ "${FAKE_SYSTEMCTL_QUERY_FAIL_AFTER_STOP:-}" = active ] \
          && [ -f "${state_file}.stopped" ] \
          && [ ! -f "${state_file}.post-stop-query-failed" ]; then
          : > "${state_file}.post-stop-query-failed"
          exit "$query_failure_rc"
        fi
        if [ "${FAKE_SYSTEMCTL_ACTIVE_STATE+x}" = x ]; then
          printf '%s\n' "$FAKE_SYSTEMCTL_ACTIVE_STATE"
        elif [ -n "$state_file" ] && [ -f "$state_file" ]; then
          cat "$state_file"
        else
          printf 'inactive\n'
        fi
        ;;
      --property=UnitFileState)
        [ "${FAKE_SYSTEMCTL_QUERY_FAIL:-}" != enabled ] || exit "$query_failure_rc"
        if [ "${FAKE_SYSTEMCTL_UNIT_FILE_STATE+x}" = x ]; then
          printf '%s\n' "$FAKE_SYSTEMCTL_UNIT_FILE_STATE"
        elif [ -n "$state_file" ] && [ -f "$enabled_file" ]; then
          printf 'enabled\n'
        else
          printf 'disabled\n'
        fi
        ;;
      *) exit 90 ;;
    esac
    ;;
  is-active)
    [ "${FAKE_SYSTEMCTL_QUERY_FAIL:-}" != active ] || exit "$query_failure_rc"
    if [ "${FAKE_SYSTEMCTL_QUERY_FAIL_AFTER_STOP:-}" = active ] \
      && [ -f "${state_file}.stopped" ] \
      && [ ! -f "${state_file}.post-stop-query-failed" ]; then
      : > "${state_file}.post-stop-query-failed"
      exit "$query_failure_rc"
    fi
    [ "${FAKE_SYSTEMCTL_IS_ACTIVE_FORCE_SUCCESS:-0}" = 1 ] \
      || { [ -n "$state_file" ] && [ -f "$state_file" ] && [ "$(cat "$state_file")" = active ]; }
    ;;
  is-enabled)
    [ "${FAKE_SYSTEMCTL_QUERY_FAIL:-}" != enabled ] || exit "$query_failure_rc"
    [ -n "$state_file" ] && [ -f "$enabled_file" ]
    ;;
  stop)
    [ "${FAKE_SYSTEMCTL_STOP_FAIL:-0}" = 0 ] || exit "$FAKE_SYSTEMCTL_STOP_FAIL"
    [ -z "$state_file" ] || printf 'inactive\n' > "$state_file"
    [ -z "$state_file" ] || : > "${state_file}.stopped"
    ;;
  enable)
    [ "${FAKE_SYSTEMCTL_ENABLE_FAIL:-0}" = 1 ] && exit 96
    [ -z "$state_file" ] || : > "$enabled_file"
    ;;
  start)
    if [ "${FAKE_SYSTEMCTL_START_FAIL_ONCE:-0}" = 1 ] \
      && [ ! -f "${state_file}.start-failed" ]; then
      : > "${state_file}.start-failed"
      exit 97
    fi
    [ "${FAKE_SYSTEMCTL_START_FAIL:-0}" = 0 ] || exit "$FAKE_SYSTEMCTL_START_FAIL"
    if [ "${FAKE_SYSTEMCTL_START_WRONG_ONCE:-0}" = 1 ] \
      && [ ! -f "${state_file}.start-wrong" ]; then
      : > "${state_file}.start-wrong"
      [ -z "$state_file" ] || printf '%s\n' "${FAKE_SYSTEMCTL_WRONG_START_STATE:-reloading}" > "$state_file"
    else
      [ -z "$state_file" ] || printf 'active\n' > "$state_file"
    fi
    ;;
  disable)
    [ "${FAKE_SYSTEMCTL_DISABLE_NOOP:-0}" = 1 ] \
      || { [ -z "$state_file" ] || rm -f -- "$enabled_file"; }
    ;;
  *) exit 0 ;;
esac
""",
    )
    _write_executable(fake_bin / "chmod", "#!/bin/sh\nexit 0\n")
    _write_executable(
        fake_bin / "cmp",
        "#!/bin/sh\n[ \"${1:-}\" = '-s' ] && shift\n"
        "[ \"$(cat \"$1\")\" = \"$(cat \"$2\")\" ]\n",
    )
    _write_executable(
        fake_bin / "install",
        r"""#!/bin/sh
case " $* " in
  *" -d "*) eval "dest=\${$#}"; mkdir -p "$dest"; exit 0 ;;
esac
while [ $# -gt 2 ]; do shift; done
cp "$1" "$2"
""",
    )
    _write_executable(fake_bin / "bash", '#!/bin/sh\nexec "$TEST_BASH_BIN" "$@"\n')
    _write_executable(
        fake_bin / "crontab",
        """#!/bin/sh
if [ "$1" = "-l" ]; then
  [ -n "${FAKE_CRONTAB_FILE:-}" ] && [ -f "$FAKE_CRONTAB_FILE" ] || exit 1
  cat "$FAKE_CRONTAB_FILE"
  exit 0
fi
[ -n "${FAKE_CRONTAB_FILE:-}" ] || exit 93
[ "${FAKE_CRONTAB_INSTALL_FAIL:-0}" = 1 ] && exit 95
cp "$1" "$FAKE_CRONTAB_FILE"
""",
    )
    _write_executable(
        fake_bin / "trust-stat",
        r"""#!/bin/sh
eval "path=\${$#}"
if [ "${FAKE_TRUST_FAILURE_PATH:-}" = "$path" ]; then
  case "${FAKE_TRUST_FAILURE_KIND:-mode}" in
    owner) printf '1000 755\n' ;;
    mode) printf '0 775\n' ;;
  esac
else
  printf '0 755\n'
fi
""",
    )
    _write_executable(
        fake_bin / "trust-find",
        """#!/bin/sh
case " $* " in
  *" -type l "*)
    [ -n "${FAKE_TRUST_SYMLINK_PATH:-}" ] && printf '%s\\n' "$FAKE_TRUST_SYMLINK_PATH"
    ;;
  *)
    [ -n "${FAKE_TRUST_UNSAFE_PATH:-}" ] && printf '%s\\n' "$FAKE_TRUST_UNSAFE_PATH"
    ;;
esac
exit 0
""",
    )
    _write_executable(
        fake_bin / "trust-readlink",
        "#!/bin/sh\nprintf '%s\\n' \"$FAKE_TRUST_SYMLINK_TARGET\"\n",
    )
    return fake_bin, calls


def _script_env(fake_bin: Path, **values: str) -> dict[str, str]:
    normalized_values = {
        key: value.replace("\\", "/") if isinstance(value, str) else value
        for key, value in values.items()
    }
    return {
        "PATH": os.pathsep.join(
            (str(fake_bin), str(Path(_bash()).resolve().parent), os.environ.get("PATH", ""))
        ),
        "TEST_BASH_BIN": _bash(),
        "TRUST_STAT_BIN": str(fake_bin / "trust-stat"),
        "TRUST_FIND_BIN": str(fake_bin / "trust-find"),
        "TRUST_READLINK_BIN": str(fake_bin / "trust-readlink"),
        "CMP_BIN": str(fake_bin / "cmp"),
        "GIT_BIN": str(fake_bin / "git"),
        "FAKE_SYSTEMCTL_STATE_FILE": str(fake_bin / "systemctl.state"),
        **normalized_values,
    }


def _prepare_trust_fixture(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    trusted_home = tmp_path / "home" / "tradingbot"
    app = trusted_home / "app"
    deploy_dir = app / "deploy"
    deploy_dir.mkdir(parents=True)
    (app / ".git").mkdir()
    (app / "venv").mkdir()
    for script_name in ("auto_update.sh", "install_auto_update.sh", "safe_deploy.sh"):
        shutil.copy2(ROOT / "deploy" / script_name, deploy_dir / script_name)
    launcher = tmp_path / "usr-local-sbin" / "alpha-station-auto-update"
    launcher.parent.mkdir()
    lock_dir = tmp_path / "run" / "alpha-station"
    lock_dir.mkdir(parents=True)
    return trusted_home, app, launcher, lock_dir


def test_default_runtime_lock_avoids_world_writable_run_lock_parent() -> None:
    for script_name in ("auto_update.sh", "install_auto_update.sh"):
        script = (ROOT / "deploy" / script_name).read_text(encoding="utf-8")
        assert 'AUTO_UPDATE_LOCK_DIR="${AUTO_UPDATE_LOCK_DIR:-/run/alpha-station}"' in script
        assert "/run/lock/alpha-station" not in script

    docs = "\n".join(
        (ROOT / "deploy" / name).read_text(encoding="utf-8")
        for name in ("DEPLOY_ANLEITUNG.md", "SERVER_WARTUNG.md")
    )
    assert "/run/alpha-station" in docs
    assert "/run/lock/alpha-station" not in docs


def test_auto_update_probe_uses_command_scoped_safe_directory(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    local = "1" * 40
    remote = "2" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=remote)

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        "--probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "status=ok action=probe" in result.stdout
    assert "revision=" + local[:12] in result.stdout
    assert "local=" + local[:12] in result.stdout
    assert "remote=" + remote[:12] in result.stdout
    git_calls = calls.read_text(encoding="utf-8")
    assert "-c safe.directory=" + app.as_posix() in git_calls
    assert "config --global" not in git_calls


def test_auto_update_safely_recreates_missing_runtime_lock_directory(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    lock_dir.rmdir()
    fake_bin, calls = _fake_git_bin(tmp_path, local="0" * 40, remote="0" * 40)

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        "--probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert lock_dir.is_dir()
    assert "status=ok action=probe" in result.stdout


def test_auto_update_rejects_existing_unsafe_runtime_lock_directory(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="0" * 40, remote="0" * 40)

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        "--probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_TRUST_FAILURE_PATH=str(lock_dir),
            FAKE_TRUST_FAILURE_KIND="mode",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert lock_dir.as_posix() in (result.stdout + result.stderr)
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


def test_auto_update_rejects_symlink_runtime_lock_directory(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    lock_dir.rmdir()
    link_target = tmp_path / "service-writable-lock"
    link_target.mkdir()
    try:
        lock_dir.symlink_to(link_target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable on this Windows host: {exc}")
    fake_bin, calls = _fake_git_bin(tmp_path, local="0" * 40, remote="0" * 40)

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        "--probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert "symlink" in (result.stdout + result.stderr).lower()
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("script_name", "args"),
    [
        ("auto_update.sh", ("--probe",)),
        ("install_auto_update.sh", ("--no-probe",)),
        ("safe_deploy.sh", ("--trust-check-only",)),
    ],
)
def test_deploy_entrypoints_reject_untrusted_home_ancestor_before_repo_code(
    tmp_path: Path, script_name: str, args: tuple[str, ...]
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="1" * 40, remote="1" * 40)
    cron_file = tmp_path / "cron.d-entry"

    result = _run_bash(
        ROOT / "deploy" / script_name,
        *args,
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_TRUST_FAILURE_PATH=str(trusted_home.parent),
            FAKE_TRUST_FAILURE_KIND="mode",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert trusted_home.parent.as_posix() in (result.stdout + result.stderr)
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""
    assert not cron_file.exists()


def test_auto_update_passes_safe_directory_to_target_deploy(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    local = "3" * 40
    remote = "4" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=remote)
    deploy_env = tmp_path / "deploy-env.txt"

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
            FAKE_DEPLOY_ENV=str(deploy_env),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    observed = deploy_env.read_text(encoding="utf-8")
    assert f"expected={remote}" in observed
    assert "key=safe.directory" in observed
    assert f"value={app.as_posix()}" in observed


def test_trusted_launcher_refreshes_itself_after_successful_deploy(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    shutil.copy2(ROOT / "deploy/auto_update.sh", launcher)
    source_updater = app / "deploy" / "auto_update.sh"
    source_updater.write_text(
        source_updater.read_text(encoding="utf-8") + "\n# target updater\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local="e" * 40, remote="f" * 40)

    result = _run_bash(
        launcher,
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
            FAKE_DEPLOY_ENV=str(tmp_path / "deploy-env.txt"),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert launcher.read_bytes() == source_updater.read_bytes()


def test_failed_deploy_does_not_publish_target_launcher(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    shutil.copy2(ROOT / "deploy/auto_update.sh", launcher)
    previous_launcher = launcher.read_bytes()
    source_updater = app / "deploy" / "auto_update.sh"
    source_updater.write_text(
        source_updater.read_text(encoding="utf-8") + "\n# rejected updater\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local="e" * 40, remote="f" * 40)

    result = _run_bash(
        launcher,
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_GIT_CALLS=str(calls),
            FAKE_DEPLOY_ENV=str(tmp_path / "deploy-env.txt"),
            FAKE_DEPLOY_EXIT="1",
        ),
    )

    assert result.returncode != 0
    assert launcher.read_bytes() == previous_launcher


def test_auto_update_installer_is_idempotent_and_probes_via_bash(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)

    local = "5" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)
    cron_file = tmp_path / "alpha-station-auto-update"
    log_file = tmp_path / "alpha_autoupdate.log"
    env = _script_env(
        fake_bin,
        APP_DIR=str(app),
        AUTO_UPDATE_CRON_FILE=str(cron_file),
        AUTO_UPDATE_LOG=str(log_file),
        AUTO_UPDATE_LAUNCHER=str(launcher),
        AUTO_UPDATE_LOCK_DIR=str(lock_dir),
        TRUSTED_HOME=str(trusted_home),
        BASH_BIN=_bash(),
        FAKE_GIT_CALLS=str(calls),
    )

    first = _run_installer(env=env)
    second = _run_installer(env=env)

    assert first.returncode == 0, first.stdout + first.stderr
    assert second.returncode == 0, second.stdout + second.stderr
    active_lines = [
        line
        for line in cron_file.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith("#")
    ]
    assert active_lines == [
        f"*/10 * * * * root /bin/bash {launcher.as_posix()} >> {log_file.as_posix()} 2>&1"
    ]
    assert launcher.read_bytes() == (app / "deploy/auto_update.sh").read_bytes()
    assert log_file.exists()
    assert "status=ok action=probe" in first.stdout
    assert "status=ok action=probe" in second.stdout


def test_health_check_rejects_legacy_cron_and_permission_denied_log(tmp_path: Path) -> None:
    app = tmp_path / "app"
    deploy_dir = app / "deploy"
    deploy_dir.mkdir(parents=True)
    updater = deploy_dir / "auto_update.sh"
    _write_executable(updater, "#!/bin/sh\nexit 0\n")
    (app / ".git").mkdir()

    cron_file = tmp_path / "alpha-station-auto-update"
    cron_file.write_text(
        f"*/10 * * * * root {updater} >> {tmp_path / 'alpha.log'} 2>&1\n",
        encoding="utf-8",
    )
    log_file = tmp_path / "alpha.log"
    log_file.write_text("/bin/sh: auto_update.sh: Permission denied\n", encoding="utf-8")
    local = "6" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--auto-update-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(log_file),
            SYSTEMCTL_BIN=str(fake_bin / "systemctl"),
            FAKE_SYSTEMCTL_ACTIVE_STATE="active",
            FAKE_SYSTEMCTL_UNIT_FILE_STATE="enabled",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 1
    output = result.stdout + result.stderr
    assert "Cron-Aufruf weicht vom sicheren /bin/bash-Vertrag ab" in output
    assert "Permission denied" in output


def test_health_check_rejects_stale_root_launcher(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    _write_executable(launcher, "#!/bin/sh\n# stale launcher\nexit 0\n")
    cron_file = tmp_path / "alpha-station-auto-update"
    log_file = tmp_path / "alpha.log"
    cron_file.write_text(
        f"*/10 * * * * root /bin/bash {launcher.as_posix()} >> {log_file.as_posix()} 2>&1\n",
        encoding="utf-8",
        newline="\n",
    )
    log_file.write_text(
        "2026-08-14T00:00:00Z alpha-auto-update status=ok action=current\n",
        encoding="utf-8",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local="6" * 40, remote="6" * 40)

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--auto-update-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(log_file),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            SYSTEMCTL_BIN=str(fake_bin / "systemctl"),
            FAKE_SYSTEMCTL_ACTIVE_STATE="active",
            FAKE_SYSTEMCTL_UNIT_FILE_STATE="enabled",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 1
    assert "Root-Launcher weicht vom versionierten Updater ab" in (
        result.stdout + result.stderr
    )


@pytest.mark.parametrize(
    "terminal_line",
    [
        "pytest output: 42 passed",
        "2026-08-14T00:00:00Z alpha-auto-update status=ok action=deploy-start revision=666666666666",
        "2026-08-14T00:00:00Z alpha-auto-update status=ok action=current",
    ],
)
def test_health_check_rejects_fresh_nonterminal_auto_update_log(
    tmp_path: Path, terminal_line: str
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    shutil.copy2(app / "deploy" / "auto_update.sh", launcher)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    cron_file = tmp_path / "alpha-station-auto-update"
    log_file = tmp_path / "alpha.log"
    cron_file.write_text(
        f"*/10 * * * * root /bin/bash {launcher.as_posix()} >> {log_file.as_posix()} 2>&1\n",
        encoding="utf-8",
        newline="\n",
    )
    log_file.write_text(terminal_line + "\n", encoding="utf-8", newline="\n")
    local = "6" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--auto-update-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(log_file),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            SYSTEMCTL_BIN=str(fake_bin / "systemctl"),
            FAKE_SYSTEMCTL_ACTIVE_STATE="active",
            FAKE_SYSTEMCTL_UNIT_FILE_STATE="enabled",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 1
    assert "kein terminaler erfolgreicher Status" in (result.stdout + result.stderr)


def test_health_check_accepts_terminal_auto_update_log_bound_to_checkout(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    shutil.copy2(app / "deploy" / "auto_update.sh", launcher)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    cron_file = tmp_path / "alpha-station-auto-update"
    log_file = tmp_path / "alpha.log"
    cron_file.write_text(
        f"*/10 * * * * root /bin/bash {launcher.as_posix()} >> {log_file.as_posix()} 2>&1\n",
        encoding="utf-8",
        newline="\n",
    )
    local = "6" * 40
    log_file.write_text(
        "2026-08-14T00:00:00Z alpha-auto-update status=ok "
        f"action=current revision={local[:12]}\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--auto-update-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(log_file),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            SYSTEMCTL_BIN=str(fake_bin / "systemctl"),
            FAKE_SYSTEMCTL_ACTIVE_STATE="active",
            FAKE_SYSTEMCTL_UNIT_FILE_STATE="enabled",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Auto-Update-Log endet terminal erfolgreich" in result.stdout


@pytest.mark.parametrize(
    (
        "load_state",
        "active_state",
        "unit_file_state",
        "query_failure",
        "expected_success",
    ),
    [
        ("loaded", "active", "enabled", "", True),
        ("error", "active", "enabled", "", False),
        ("not-found", "active", "enabled", "", False),
        ("masked", "active", "enabled", "", False),
        ("loaded", "inactive", "enabled", "", False),
        ("loaded", "active", "disabled", "", False),
        ("loaded", "active", "enabled", "load", False),
        ("loaded", "active", "enabled", "active", False),
        ("loaded", "active", "enabled", "enabled", False),
    ],
)
def test_auto_update_health_requires_loaded_active_enabled_cron_service(
    tmp_path: Path,
    load_state: str,
    active_state: str,
    unit_file_state: str,
    query_failure: str,
    expected_success: bool,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    shutil.copy2(app / "deploy" / "auto_update.sh", launcher)
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
    cron_file = tmp_path / "alpha-station-auto-update"
    log_file = tmp_path / "alpha.log"
    cron_file.write_text(
        f"*/10 * * * * root /bin/bash {launcher.as_posix()} >> {log_file.as_posix()} 2>&1\n",
        encoding="utf-8",
        newline="\n",
    )
    local = "6" * 40
    log_file.write_text(
        "2026-08-14T00:00:00Z alpha-auto-update status=ok "
        f"action=current revision={local[:12]}\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--auto-update-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(log_file),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            SYSTEMCTL_BIN=str(fake_bin / "systemctl"),
            FAKE_SYSTEMCTL_LOAD_STATE=load_state,
            FAKE_SYSTEMCTL_ACTIVE_STATE=active_state,
            FAKE_SYSTEMCTL_UNIT_FILE_STATE=unit_file_state,
            FAKE_SYSTEMCTL_QUERY_FAIL=query_failure,
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    output = result.stdout + result.stderr
    assert (result.returncode == 0) is expected_success, output
    if expected_success:
        assert "cron.service ist aktiv und fuer Autostart aktiviert" in output
    else:
        assert "cron.service" in output


@pytest.mark.parametrize(
    (
        "load_state",
        "active_state",
        "unit_file_state",
        "query_failure",
        "query_rc",
        "expected_counts",
    ),
    [
        ("loaded", "active", "enabled", "", "0", "COUNTS 4 0 0"),
        ("not-found", "active", "enabled", "", "0", "COUNTS 0 0 2"),
        ("loaded", "inactive", "enabled", "", "0", "COUNTS 0 0 2"),
        ("loaded", "active", "disabled", "", "0", "COUNTS 0 0 2"),
        ("loaded", "active", "enabled", "load", "88", "COUNTS 0 0 2"),
        ("loaded", "active", "enabled", "active", "88", "COUNTS 0 0 2"),
        ("loaded", "active", "enabled", "enabled", "89", "COUNTS 0 0 2"),
    ],
)
def test_general_service_health_is_failclosed_on_state_or_query_failure(
    tmp_path: Path,
    load_state: str,
    active_state: str,
    unit_file_state: str,
    query_failure: str,
    query_rc: str,
    expected_counts: str,
) -> None:
    health = (ROOT / "deploy/health_check.sh").read_text(encoding="utf-8")
    definitions = "green()  {" + health.split("green()  {", 1)[1].split(
        "\ndow=$(date +%u)", 1
    )[0]
    service_block = health.split('echo "[1] Dienste"', 1)[1].split(
        'echo "[2] API-/Build-Identitaet"', 1
    )[0]
    script = tmp_path / "general-service-health.sh"
    script.write_text(
        "#!/usr/bin/env bash\n"
        "set -u\n"
        "ok=0; warn=0; fail=0\n"
        "SYSTEMCTL_BIN=systemctl\n"
        "systemctl() {\n"
        "  case \"$1\" in\n"
        "    show)\n"
        "      case \"$2\" in\n"
        "        --property=LoadState) value=\"$FAKE_LOAD_STATE\"; kind=load ;;\n"
        "        --property=ActiveState) value=\"$FAKE_ACTIVE_STATE\"; kind=active ;;\n"
        "        --property=UnitFileState) value=\"$FAKE_UNIT_FILE_STATE\"; kind=enabled ;;\n"
        "        *) return 90 ;;\n"
        "      esac\n"
        "      printf '%s\\n' \"$value\"\n"
        "      [ \"$FAKE_QUERY_FAILURE\" != \"$kind\" ] || return \"$FAKE_QUERY_RC\"\n"
        "      ;;\n"
        "    is-active)\n"
        "      printf '%s\\n' \"$FAKE_ACTIVE_STATE\"\n"
        "      [ \"$FAKE_QUERY_FAILURE\" != active ] || return \"$FAKE_QUERY_RC\"\n"
        "      ;;\n"
        "    is-enabled)\n"
        "      printf '%s\\n' \"$FAKE_UNIT_FILE_STATE\"\n"
        "      [ \"$FAKE_QUERY_FAILURE\" != enabled ] || return \"$FAKE_QUERY_RC\"\n"
        "      ;;\n"
        "  esac\n"
        "}\n"
        + definitions
        + '\necho "[1] Dienste"'
        + service_block
        + "\nprintf 'COUNTS %s %s %s\\n' \"$ok\" \"$warn\" \"$fail\"\n",
        encoding="utf-8",
        newline="\n",
    )

    result = _run_bash(
        script,
        env={
            "FAKE_LOAD_STATE": load_state,
            "FAKE_ACTIVE_STATE": active_state,
            "FAKE_UNIT_FILE_STATE": unit_file_state,
            "FAKE_QUERY_FAILURE": query_failure,
            "FAKE_QUERY_RC": query_rc,
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert expected_counts in result.stdout


@pytest.mark.parametrize(
    ("api_revision", "api_bundle", "expected_success"),
    [
        ("7" * 12, "a" * 12, True),
        ("8" * 12, "a" * 12, False),
        ("7" * 12, "b" * 12, False),
    ],
)
def test_health_check_binds_api_health_to_checkout_and_frontend_bundle(
    tmp_path: Path, api_revision: str, api_bundle: str, expected_success: bool
) -> None:
    app = tmp_path / "app"
    (app / ".git").mkdir(parents=True)
    (app / "frontend").mkdir()
    (app / "frontend" / "app.bundle.js").write_text(
        "/* app-source-sha256: " + ("a" * 64) + " */\n",
        encoding="utf-8",
        newline="\n",
    )
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    fake_curl = fake_bin / "health-curl"
    _write_executable(
        fake_curl,
        "#!/bin/sh\nprintf '%s\\n' "
        + repr(
            '{"status":"healthy","revision":"'
            + api_revision
            + '","frontend_bundle":"'
            + api_bundle
            + '"}'
        )
        + "\n",
    )

    result = _run_bash(
        ROOT / "deploy/health_check.sh",
        "--runtime-build-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            GIT_BIN=str(fake_bin / "git"),
            CURL_BIN=str(fake_curl),
            # Use the interpreter running pytest. A copied virtualenv launcher
            # can retain an absolute reference to a Python installation from
            # another PC and silently break this shell-level health probe.
            PYTHON_BIN=sys.executable,
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    output = result.stdout + result.stderr
    assert (result.returncode == 0) is expected_success, output
    if expected_success:
        assert "API-Revision und Frontend-Bundle stimmen exakt" in output
    else:
        assert "API-Build stimmt nicht mit dem Checkout ueberein" in output


def test_auto_update_installer_removes_only_legacy_user_cron(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    local = "7" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)
    legacy_crontab = tmp_path / "root.crontab"
    updater = f"{app.as_posix()}/deploy/auto_update.sh"
    log_file = tmp_path / "alpha.log"
    preserved = (
        "MAILTO=ops@example.invalid\n"
        f"# disabled {updater}\n"
        f"7 * * * * /usr/bin/wrapper --label={updater}.backup\n"
        "5 * * * * /opt/other-service/auto_update.sh\n"
        f"11 * * * * {updater} && /usr/local/sbin/nightly-backup\n"
        f"12 * * * * /bin/bash {updater}; /usr/local/sbin/nightly-backup\n"
        f"13 * * * * {updater} # operator annotation\n"
        f"14 * * * * {updater} >> /var/log/not-alpha.log 2>&1\n"
    )
    legacy_crontab.write_text(
        preserved
        + f"*/10 * * * * {updater}\n"
        + f"*/11 * * * * /bin/bash {updater}\n"
        + f"*/12 * * * * {updater} >> {log_file.as_posix()} 2>&1\n"
        + f"*/13 * * * * /bin/bash {updater} >> {log_file.as_posix()} 2>&1\n",
        encoding="utf-8",
    )

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            AUTO_UPDATE_CRON_FILE=str(tmp_path / "cron.d-entry"),
            AUTO_UPDATE_LOG=str(log_file),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            TRUSTED_HOME=str(trusted_home),
            BASH_BIN=_bash(),
            FAKE_GIT_CALLS=str(calls),
            FAKE_CRONTAB_FILE=str(legacy_crontab),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert legacy_crontab.read_text(encoding="utf-8") == preserved


def test_installer_does_not_publish_cron_or_launcher_when_probe_fails(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    legacy_crontab = tmp_path / "root.crontab"
    legacy_line = f"*/10 * * * * {app.as_posix()}/deploy/auto_update.sh\n"
    legacy_crontab.write_text(legacy_line, encoding="utf-8")

    result = _run_installer(
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            BASH_BIN=_bash(),
            FAKE_GIT_CALLS=str(calls),
            FAKE_GIT_FETCH_FAIL="1",
            FAKE_CRONTAB_FILE=str(legacy_crontab),
        ),
    )

    assert result.returncode != 0
    assert not cron_file.exists()
    assert not launcher.exists()
    assert legacy_crontab.read_text(encoding="utf-8") == legacy_line


def test_installer_does_not_publish_when_legacy_migration_fails(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    legacy_crontab = tmp_path / "root.crontab"
    legacy_crontab.write_text(
        f"*/10 * * * * {app.as_posix()}/deploy/auto_update.sh\n", encoding="utf-8"
    )

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_CRONTAB_FILE=str(legacy_crontab),
            FAKE_CRONTAB_INSTALL_FAIL="1",
        ),
    )

    assert result.returncode != 0
    assert not cron_file.exists()
    assert not launcher.exists()


def test_installer_rolls_back_legacy_cron_when_post_migration_commit_fails(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    legacy_crontab = tmp_path / "root.crontab"
    legacy_line = f"*/10 * * * * {app.as_posix()}/deploy/auto_update.sh\n"
    legacy_crontab.write_text(legacy_line, encoding="utf-8", newline="\n")
    systemctl_calls = tmp_path / "systemctl-calls.txt"
    (fake_bin / "systemctl.state").write_text(
        "active\n", encoding="utf-8", newline="\n"
    )
    (fake_bin / "systemctl.state.enabled").write_text("", encoding="utf-8")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_CRONTAB_FILE=str(legacy_crontab),
            FAKE_SYSTEMCTL_ENABLE_FAIL="1",
            FAKE_SYSTEMCTL_CALLS=str(systemctl_calls),
        ),
    )

    assert result.returncode != 0
    assert legacy_crontab.read_text(encoding="utf-8") == legacy_line
    assert not cron_file.exists()
    assert not launcher.exists()
    installer = (ROOT / "deploy/install_auto_update.sh").read_text(encoding="utf-8")
    assert installer.index('mv -f -- "$cron_candidate" "$CRON_FILE"') < installer.rindex(
        "systemctl enable cron"
    )
    observed = systemctl_calls.read_text(encoding="utf-8")
    assert observed.index("stop cron") < observed.index("enable cron")


def test_installer_rollback_restores_previous_files_and_active_cron_state(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    previous_launcher = b"#!/bin/sh\n# previous trusted launcher\nexit 0\n"
    launcher.write_bytes(previous_launcher)
    launcher.chmod(0o700)
    cron_file = tmp_path / "cron.d-entry"
    previous_cron = "# previous managed cron\n"
    cron_file.write_text(previous_cron, encoding="utf-8", newline="\n")
    cron_file.chmod(0o600)
    legacy_crontab = tmp_path / "root.crontab"
    legacy_line = f"*/10 * * * * {app.as_posix()}/deploy/auto_update.sh\n"
    legacy_crontab.write_text(legacy_line, encoding="utf-8", newline="\n")
    state_file = fake_bin / "systemctl.state"
    state_file.write_text("active\n", encoding="utf-8", newline="\n")
    (fake_bin / "systemctl.state.enabled").write_text("", encoding="utf-8")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_CRONTAB_FILE=str(legacy_crontab),
            FAKE_SYSTEMCTL_START_FAIL_ONCE="1",
        ),
    )

    assert result.returncode != 0
    assert launcher.read_bytes() == previous_launcher
    assert cron_file.read_text(encoding="utf-8") == previous_cron
    if os.name != "nt":
        assert stat.S_IMODE(launcher.stat().st_mode) == 0o700
        assert stat.S_IMODE(cron_file.stat().st_mode) == 0o600
    assert legacy_crontab.read_text(encoding="utf-8") == legacy_line
    assert state_file.read_text(encoding="utf-8").strip() == "active"
    assert (fake_bin / "systemctl.state.enabled").exists()


def test_installer_rollback_preserves_backup_metadata_without_fixed_modes() -> None:
    installer = (ROOT / "deploy/install_auto_update.sh").read_text(encoding="utf-8")
    restore = installer.split("restore_managed_file() {", 1)[1].split(
        "\n}\n\nrollback_transaction()", 1
    )[0]
    rollback = installer.split("rollback_transaction() {", 1)[1].split(
        "\n}\n\nfinish_install()", 1
    )[0]

    assert "--preserve=all" in restore
    assert 'mode="$3"' not in restore
    assert ' -m "$mode" ' not in restore
    assert '"$AUTO_UPDATE_LAUNCHER" 0755' not in rollback
    assert '"$CRON_FILE" 0644' not in rollback


@pytest.mark.parametrize(
    ("load_state", "active_state", "unit_file_state", "query_failure", "failure_rc"),
    [
        ("error", "active", "enabled", "", "88"),
        ("not-found", "active", "enabled", "", "88"),
        ("masked", "active", "enabled", "", "88"),
        ("loaded", "activating", "enabled", "", "88"),
        ("loaded", "deactivating", "enabled", "", "88"),
        ("loaded", "reloading", "enabled", "", "88"),
        ("loaded", "active", "masked", "", "89"),
        ("loaded", "active", "static", "", "89"),
        ("loaded", "active", "enabled", "load", "88"),
        ("loaded", "active", "enabled", "active", "88"),
        ("loaded", "active", "enabled", "enabled", "89"),
    ],
)
def test_installer_rejects_ambiguous_cron_snapshot_before_mutation(
    tmp_path: Path,
    load_state: str,
    active_state: str,
    unit_file_state: str,
    query_failure: str,
    failure_rc: str,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    previous_launcher = b"#!/bin/sh\n# keep launcher\n"
    previous_cron = "# keep cron\n"
    launcher.write_bytes(previous_launcher)
    launcher.chmod(0o700)
    cron_file.write_text(previous_cron, encoding="utf-8", newline="\n")
    cron_file.chmod(0o600)
    legacy_crontab = tmp_path / "root.crontab"
    legacy_line = f"*/10 * * * * {app.as_posix()}/deploy/auto_update.sh\n"
    legacy_crontab.write_text(legacy_line, encoding="utf-8", newline="\n")
    systemctl_calls = tmp_path / "systemctl-calls.txt"

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_CRONTAB_FILE=str(legacy_crontab),
            FAKE_SYSTEMCTL_LOAD_STATE=load_state,
            FAKE_SYSTEMCTL_ACTIVE_STATE=active_state,
            FAKE_SYSTEMCTL_UNIT_FILE_STATE=unit_file_state,
            FAKE_SYSTEMCTL_QUERY_FAIL=query_failure,
            FAKE_SYSTEMCTL_QUERY_FAILURE_RC=failure_rc,
            FAKE_SYSTEMCTL_CALLS=str(systemctl_calls),
        ),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert launcher.read_bytes() == previous_launcher
    assert cron_file.read_text(encoding="utf-8") == previous_cron
    assert legacy_crontab.read_text(encoding="utf-8") == legacy_line
    observed = systemctl_calls.read_text(encoding="utf-8")
    assert "stop cron" not in observed
    assert "enable cron" not in observed
    assert "start cron" not in observed
    assert "disable cron" not in observed


def test_installer_post_stop_query_failure_rolls_back_original_state(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    state_file = fake_bin / "systemctl.state"
    state_file.write_text("active\n", encoding="utf-8", newline="\n")
    enabled_file = fake_bin / "systemctl.state.enabled"
    enabled_file.write_text("", encoding="utf-8")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_SYSTEMCTL_QUERY_FAIL_AFTER_STOP="active",
            FAKE_SYSTEMCTL_QUERY_FAILURE_RC="88",
        ),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert not launcher.exists()
    assert not cron_file.exists()
    assert state_file.read_text(encoding="utf-8").strip() == "active"
    assert enabled_file.exists()


def test_installer_rejects_transitional_post_start_state_and_restores_snapshot(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    state_file = fake_bin / "systemctl.state"
    state_file.write_text("active\n", encoding="utf-8", newline="\n")
    enabled_file = fake_bin / "systemctl.state.enabled"
    enabled_file.write_text("", encoding="utf-8")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_SYSTEMCTL_START_WRONG_ONCE="1",
            FAKE_SYSTEMCTL_WRONG_START_STATE="reloading",
            FAKE_SYSTEMCTL_IS_ACTIVE_FORCE_SUCCESS="1",
        ),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert not launcher.exists()
    assert not cron_file.exists()
    assert state_file.read_text(encoding="utf-8").strip() == "active"
    assert enabled_file.exists()


def test_installer_does_not_claim_rollback_when_final_state_differs(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    cron_file = tmp_path / "cron.d-entry"
    state_file = fake_bin / "systemctl.state"
    state_file.write_text("inactive\n", encoding="utf-8", newline="\n")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_SYSTEMCTL_START_FAIL_ONCE="1",
            FAKE_SYSTEMCTL_DISABLE_NOOP="1",
        ),
    )

    output = result.stdout + result.stderr
    assert result.returncode != 0, output
    assert "Vorheriger Cron-/Launcher-Zustand wurde wiederhergestellt" not in output
    assert "KRITISCH" in output


def test_installer_preserves_failed_cron_state_during_rollback(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="7" * 40, remote="7" * 40)
    state_file = fake_bin / "systemctl.state"
    state_file.write_text("failed\n", encoding="utf-8", newline="\n")

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(tmp_path / "cron.d-entry"),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_GIT_CALLS=str(calls),
            FAKE_SYSTEMCTL_ENABLE_FAIL="1",
        ),
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert state_file.read_text(encoding="utf-8").strip() == "failed"
    assert "Vorheriger Cron-/Launcher-Zustand wurde wiederhergestellt" in (
        result.stdout + result.stderr
    )


@pytest.mark.parametrize("failure_kind", ["owner", "mode"])
def test_auto_update_rejects_untrusted_updater_before_git(
    tmp_path: Path, failure_kind: str
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    local = "8" * 40
    fake_bin, calls = _fake_git_bin(tmp_path, local=local, remote=local)
    updater = app / "deploy" / "auto_update.sh"

    result = _run_bash(
        ROOT / "deploy/auto_update.sh",
        "--probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            FAKE_TRUST_FAILURE_PATH=str(updater),
            FAKE_TRUST_FAILURE_KIND=failure_kind,
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert "source trust check failed" in (result.stdout + result.stderr).lower()
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


def test_auto_update_installer_rejects_untrusted_parent_before_cron(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="9" * 40, remote="9" * 40)
    cron_file = tmp_path / "cron.d-entry"

    result = _run_installer(
        "--no-probe",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            AUTO_UPDATE_LAUNCHER=str(launcher),
            AUTO_UPDATE_LOCK_DIR=str(lock_dir),
            AUTO_UPDATE_CRON_FILE=str(cron_file),
            AUTO_UPDATE_LOG=str(tmp_path / "alpha.log"),
            FAKE_TRUST_FAILURE_PATH=str(trusted_home),
            FAKE_TRUST_FAILURE_KIND="owner",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert not cron_file.exists()
    assert not launcher.exists()


def test_safe_deploy_rejects_nested_untrusted_source_before_runtime(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="a" * 40, remote="a" * 40)
    unsafe_source = app / "modules" / "injected.py"

    result = _run_bash(
        ROOT / "deploy/safe_deploy.sh",
        "--trust-check-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            TRUST_STAT_BIN=str(fake_bin / "trust-stat"),
            TRUST_FIND_BIN=str(fake_bin / "trust-find"),
            FAKE_TRUST_UNSAFE_PATH=str(unsafe_source),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert "source trust check failed" in (result.stdout + result.stderr).lower()
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


def test_safe_deploy_secure_fixture_passes_trust_check(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="b" * 40, remote="b" * 40)

    result = _run_bash(
        ROOT / "deploy/safe_deploy.sh",
        "--trust-check-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            TRUST_STAT_BIN=str(fake_bin / "trust-stat"),
            TRUST_FIND_BIN=str(fake_bin / "trust-find"),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "source trust check passed" in result.stdout.lower()
    assert not calls.exists() or calls.read_text(encoding="utf-8") == ""


def test_safe_deploy_allows_root_controlled_venv_python_symlink(tmp_path: Path) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="c" * 40, remote="c" * 40)
    venv_bin = app / "venv" / "bin"
    venv_bin.mkdir()
    link_path = venv_bin / "python"
    safe_target = venv_bin / "python3-real"
    safe_target.write_text("", encoding="utf-8")

    result = _run_bash(
        ROOT / "deploy/safe_deploy.sh",
        "--trust-check-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            FAKE_TRUST_SYMLINK_PATH=str(link_path),
            FAKE_TRUST_SYMLINK_TARGET=str(safe_target),
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode == 0, result.stdout + result.stderr


def test_safe_deploy_rejects_venv_symlink_with_untrusted_target_parent(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="c" * 40, remote="c" * 40)
    venv_bin = app / "venv" / "bin"
    venv_bin.mkdir()
    link_path = venv_bin / "python"
    safe_target = venv_bin / "python3-real"
    safe_target.write_text("", encoding="utf-8")

    result = _run_bash(
        ROOT / "deploy/safe_deploy.sh",
        "--trust-check-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            FAKE_TRUST_SYMLINK_PATH=str(link_path),
            FAKE_TRUST_SYMLINK_TARGET=str(safe_target),
            FAKE_TRUST_FAILURE_PATH=str(venv_bin),
            FAKE_TRUST_FAILURE_KIND="mode",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert venv_bin.as_posix() in (result.stdout + result.stderr)


def test_safe_deploy_rejects_venv_symlink_to_service_writable_data(
    tmp_path: Path,
) -> None:
    trusted_home, app, launcher, lock_dir = _prepare_trust_fixture(tmp_path)
    fake_bin, calls = _fake_git_bin(tmp_path, local="d" * 40, remote="d" * 40)
    link_path = app / "venv" / "bin" / "python"
    unsafe_target = app / "data_cache" / "runtime" / "python"
    unsafe_target.parent.mkdir(parents=True)
    unsafe_target.write_text("", encoding="utf-8")

    result = _run_bash(
        ROOT / "deploy/safe_deploy.sh",
        "--trust-check-only",
        env=_script_env(
            fake_bin,
            APP_DIR=str(app),
            TRUSTED_HOME=str(trusted_home),
            FAKE_TRUST_SYMLINK_PATH=str(link_path),
            FAKE_TRUST_SYMLINK_TARGET=str(unsafe_target),
            FAKE_TRUST_FAILURE_PATH=str(unsafe_target),
            FAKE_TRUST_FAILURE_KIND="owner",
            FAKE_GIT_CALLS=str(calls),
        ),
    )

    assert result.returncode != 0
    assert unsafe_target.as_posix() in (result.stdout + result.stderr)


@pytest.mark.parametrize(
    "unit_name", ["tradingbot-api.service", "tradingbot-bg.service"]
)
def test_service_unit_uses_unreplaceable_systemd_state_for_shared_tmp(unit_name: str) -> None:
    parser = ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    parser.read(ROOT / "deploy" / unit_name, encoding="utf-8")
    service = parser["Service"]

    assert service["ProtectSystem"] == "strict"
    assert service["ReadWritePaths"].split() == ["/home/tradingbot/app/data_cache"]
    assert service["StateDirectory"] == "alpha-station-runtime"
    assert service["StateDirectoryMode"] == "0700"
    assert service["BindPaths"] == "/var/lib/alpha-station-runtime:/tmp"
    unit_text = (ROOT / "deploy" / unit_name).read_text(encoding="utf-8")
    assert "BindPaths=/home/tradingbot/app/data_cache/runtime:/tmp" not in unit_text
    assert 'Environment="HOME=/var/lib/alpha-station-runtime"' in unit_text
    assert 'Environment="XDG_CACHE_HOME=/var/lib/alpha-station-runtime/cache"' in unit_text


def test_deploy_shell_scripts_are_normalized_to_lf() -> None:
    scripts = [
        ROOT / "deploy" / name
        for name in (
            "auto_update.sh",
            "health_check.sh",
            "install.sh",
            "install_auto_update.sh",
            "safe_deploy.sh",
        )
    ]
    for script in scripts:
        assert b"\r" not in script.read_bytes(), f"mixed/non-LF line endings in {script}"

    result = subprocess.run(
        ["git", "check-attr", "eol", "--", *(str(path.relative_to(ROOT)) for path in scripts)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout
    assert all(line.endswith("eol: lf") for line in result.stdout.splitlines())


def test_fresh_bootstrap_never_executes_legacy_updater_or_tmp_lock_workaround() -> None:
    runbook = (ROOT / "deploy" / "SERVER_WARTUNG.md").read_text(encoding="utf-8")
    bootstrap = runbook.split("Wenn ein bestehender Server", 1)[1].split("\n---", 1)[0]

    assert "/home/tradingbot/app/deploy/auto_update.sh" not in bootstrap
    assert "legacy_lock_tmp=" not in bootstrap
    assert "/tmp/alpha_auto_update.lock" not in bootstrap
    assert "quarantine" in bootstrap.lower()
    assert "git clone" in bootstrap


def test_auto_update_is_versioned_executable() -> None:
    mode = subprocess.run(
        ["git", "ls-files", "-s", "--", "deploy/auto_update.sh"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.split()[0]

    assert mode == "100755"
