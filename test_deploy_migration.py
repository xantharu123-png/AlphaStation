from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from test_deploy_auto_update import _bash


ROOT = Path(__file__).resolve().parent
MIGRATION = ROOT / "deploy" / "migrate_existing_vps.sh"


@pytest.fixture(scope="module")
def script() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _function(script: str, name: str, next_name: str) -> str:
    start = script.index(f"{name}() {{")
    end = script.index(f"\n\n{next_name}() {{", start)
    return script[start:end]


def test_migration_script_is_lf_and_bash_syntax_valid(script: str) -> None:
    assert b"\r" not in MIGRATION.read_bytes()
    result = subprocess.run(
        [_bash(), "--noprofile", "--norc", "-n", MIGRATION.as_posix()],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr

    for stale in ("git_stage", "$PIN", "nginx_digest", '"$stage"'):
        assert stale not in script
    assert 'mv -T -- "$final_clone" "$APP"' in script
    assert 'EXPECTED_REVISION="$EXPECTED_REVISION"' in script


def test_migration_binds_full_revision_to_script_checkout_and_remote(
    script: str,
) -> None:
    guard = (
        '[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]]'
        in script
    )
    assert guard
    assert "/root/alpha-migration-source.*" in script
    assert 'git_control hash-object "$SCRIPT_PATH"' in script
    assert '"$EXPECTED_REVISION:deploy/migrate_existing_vps.sh"' in script
    assert "executing_script_contract() {" in script
    assert 'fd_path = f"/proc/{int(sys.argv[1])}/fd/255"' in script
    assert "os.pread" in script
    assert "executing_script_inode" in script
    assert '"$executing_script_blob" = "$expected_script_blob"' in script
    assert 'EXPECTED_HOME_SECRET_SHA256="${EXPECTED_HOME_SECRET_SHA256:-}"' in script
    assert '[[ "$EXPECTED_HOME_SECRET_SHA256" =~ ^[0-9a-f]{64}$ ]]' in script
    assert not re.findall(r"(?<![0-9a-f])[0-9a-f]{64}(?![0-9a-f])", script)
    assert script.count('git_control rev-parse origin/main') >= 2
    assert script.count('git_final rev-parse origin/main') >= 2
    assert script.count('git_final rev-parse HEAD') >= 2

    revision_guard = script.index(
        '[[ "$EXPECTED_REVISION" =~ ^[0-9a-f]{40}$ ]]'
    )
    first_window = script.index("require_maintenance_window", revision_guard)
    first_fetch = script.index("git_control fetch --force origin main")
    first_backup = script.index('backup="$(mktemp -d')
    assert revision_guard < first_window < first_fetch < first_backup


@pytest.mark.parametrize(
    ("weekday", "hhmm", "allowed"),
    [
        ("1", "2229", False),
        ("1", "2230", True),
        ("4", "2359", True),
        ("5", "0000", False),
        ("5", "2359", False),
        ("6", "0000", True),
        ("7", "2359", True),
        ("0", "2230", False),
        ("8", "2230", False),
        ("1", "2400", False),
        ("1", "2260", False),
        ("x", "2230", False),
    ],
)
def test_migration_maintenance_window_boundaries(
    tmp_path: Path, script: str, weekday: str, hhmm: str, allowed: bool
) -> None:
    function = _function(
        script, "maintenance_window_open", "require_maintenance_window"
    )
    harness = tmp_path / "maintenance.sh"
    harness.write_text(
        f"#!/usr/bin/env bash\n{function}\nmaintenance_window_open \"$1\" \"$2\"\n",
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [_bash(), "--noprofile", "--norc", harness.as_posix(), weekday, hhmm],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert (result.returncode == 0) is allowed


def test_migration_regates_window_and_disk_immediately_before_mutation(
    script: str,
) -> None:
    assert script.count("require_maintenance_window") == 3  # definition + 2 calls
    assert script.count("require_free_space /root") == 2
    assert script.count('require_free_space "$TRUSTED_HOME"') == 2
    second_window = script.rindex("require_maintenance_window")
    second_disk = script.rindex('require_free_space "$TRUSTED_HOME"')
    mutation = script.index("mutation_started=1")
    assert second_window < second_disk < mutation
    assert "MIN_FREE_KIB=$((8 * 1024 * 1024))" in script
    assert '[[ "$free_kib" =~ ^[0-9]+$ ]]' in script


def test_preflight_archive_is_separate_from_pristine_final_clone(script: str) -> None:
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    symlink_gate = script.index('archive_link="$(find "$preflight_source"')
    preflight_venv = script.index('python3 -I -m venv "$preflight_source/venv"')
    pytest_run = script.index('"$preflight_source/venv/bin/python" -m pytest')
    final_clone = script.index("clone --no-checkout")
    final_move = script.index('mv -T -- "$final_clone" "$APP"')
    assert archive < symlink_gate < preflight_venv < pytest_run < final_clone < final_move
    assert 'GIT_DIR="$CONTROL_REPO/.git" GIT_WORK_TREE="$preflight_source"' in script
    clean_helper = _function(script, "require_git_final_clean", "pgrep_no_match")
    assert "--ignored=matching" in clean_helper
    assert 'ignored="$(git_final status' in clean_helper
    assert script.count("require_git_final_clean \"") >= 3
    assert 'mv -T -- "$preflight_source" "$APP"' not in script
    assert 'git_final status --porcelain=v1 --untracked-files=all' in script


def test_service_user_and_secret_are_quiesced_before_snapshot_and_swap(
    script: str,
) -> None:
    capture_shell = script.index('old_service_shell="$(getent passwd tradingbot')
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    nologin = script.index('usermod --shell "$nologin_bin" tradingbot', mutation)
    stop_cron = script.index("systemctl stop cron.service", nologin)
    first_quiesce = script.index(
        'assert_quiescent || die "Dienste/Prozesse sind nicht vollstaendig quiescent"'
    )
    snapshot = script.index(
        'cp --preserve=all -- "$GLOBAL_SECRET" "$backup/home-secrets.before"'
    )
    old_move = script.index('mv -T -- "$APP" "$quarantine/old-app"')
    assert capture_shell < mutation < nologin < stop_cron < first_quiesce < snapshot < old_move
    assert 'loginctl disable-linger tradingbot' in script
    assert 'pgrep_no_match -u "$service_uid"' in script
    assert 'assert_no_tree_references "$APP" "$quarantine/old-app"' in script
    assert script.count("assert_quiescent || die") >= 8
    assert "validate_preserved_runtime_state() {" in script
    assert 'find "$APP/data_cache" -xdev -mindepth 1 -print0' in script
    assert "Unerlaubter data_cache-Eintrag" in script
    assert script.count("validate_preserved_runtime_state") >= 3
    assert 'home_secret_snapshot_ready=1' in script
    assert (
        '"$EXPECTED_HOME_SECRET_SHA256"' in script[snapshot - 700 : snapshot + 700]
    )


def test_api_bg_effective_execution_contract_is_bound_twice(script: str) -> None:
    helper = _function(
        script, "require_api_bg_execution_contract", "unit_security_contract"
    )
    assert "tradingbot-api.service tradingbot-bg.service" in helper
    assert '"$unit" User tradingbot' in helper
    assert '"$unit" Group tradingbot' in helper
    assert '"$unit" WorkingDirectory "$APP"' in helper
    assert script.count("require_api_bg_execution_contract") == 3
    calls = [
        match.start()
        for match in re.finditer(
            r"(?m)^require_api_bg_execution_contract$", script
        )
    ]
    assert len(calls) == 2
    first_call, second_call = calls
    preflight = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert first_call < preflight < second_call < mutation


def test_root_controlled_trusted_home_ancestor_chain_is_bound_three_times(
    script: str,
) -> None:
    helper = _function(
        script, "trusted_home_ancestor_contract", "frontend_tree_is_safe"
    )
    assert 'for path in / /home "$TRUSTED_HOME"' in helper
    assert 'is_real "$path" && [ -d "$path" ]' in helper
    assert 'readlink -f -- "$path"' in helper
    assert "stat -c '%d:%i %u %g %a'" in helper
    assert '[ "$uid" = 0 ]' in helper
    assert "(8#$mode & 8#22) == 0" in helper

    calls = [
        match.start()
        for match in re.finditer(
            r"(?m)^\s*(?:trusted_home_ancestors|current_trusted_home_ancestors)="
            r'"\$\(trusted_home_ancestor_contract\)"',
            script,
        )
    ]
    assert len(calls) == 3
    initial, before_mutation, before_guard = calls
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    guard = script.index('/bin/bash "$APP/deploy/runtime_state_guard.sh"')
    assert initial < archive < before_mutation < mutation < before_guard < guard
    assert script.count(
        '[ "$current_trusted_home_ancestors" = "$trusted_home_ancestors" ]'
    ) == 2


def test_trusted_home_ancestor_readlink_failure_is_not_masked(
    tmp_path: Path, script: str
) -> None:
    helper = _function(
        script, "trusted_home_ancestor_contract", "frontend_tree_is_safe"
    ).replace(
        'for path in / /home "$TRUSTED_HOME"; do',
        'for path in "$TRUSTED_HOME"; do',
    )
    harness = tmp_path / "ancestor-contract.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        'TRUSTED_HOME="$1"\n'
        'is_real() { [ -e "$1" ] && [ ! -L "$1" ]; }\n'
        'readlink() { printf "%s\\n" "${@: -1}"; return "${FAKE_RC:-0}"; }\n'
        'stat() { printf "1:2 0 0 755\\n"; }\n'
        f"{helper}\n"
        "trusted_home_ancestor_contract >/dev/null\n",
        encoding="utf-8",
        newline="\n",
    )
    good = subprocess.run(
        [_bash(), harness.as_posix(), tmp_path.as_posix()],
        env={**os.environ, "FAKE_RC": "0"},
        text=True,
        capture_output=True,
        check=False,
    )
    masked_failure = subprocess.run(
        [_bash(), harness.as_posix(), tmp_path.as_posix()],
        env={**os.environ, "FAKE_RC": "77"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert masked_failure.returncode != 0


def test_unit_disk_loaded_contract_is_bound_before_preflight_and_rollback_start(
    tmp_path: Path, script: str
) -> None:
    helper = _function(script, "unit_security_contract", "unit_security_contract_matches")
    for property_name in (
        "NeedDaemonReload",
        "FragmentPath",
        "DropInPaths",
        "User",
        "Group",
        "WorkingDirectory",
        "ExecStart",
        "EnvironmentFiles",
        "ExecStartPre",
        "ExecStartPost",
        "ExecStop",
        "ExecStopPost",
    ):
        assert property_name in helper
    assert 'value="$(systemctl_property_value "$unit" "$property")" || return 1' in helper
    assert script.count("require_no_pending_unit_reload") == 3

    initial = script.index("api_unit_security_before=")
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    pre_mutation = script.index(
        "unit_security_contract_matches tradingbot-api.service", initial
    )
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert initial < archive < pre_mutation < mutation

    rollback = script.index("rollback() {")
    daemon_reload = script.index("systemctl daemon-reload", rollback)
    rollback_contract = script.index(
        "unit_security_contract_matches tradingbot-api.service", daemon_reload
    )
    rollback_frontend = script.index(
        "rollback_frontend_execution_contract_matches", rollback_contract
    )
    service_start = script.index(
        "systemctl start tradingbot-api.service tradingbot-bg.service", rollback
    )
    assert daemon_reload < rollback_contract < rollback_frontend < service_start

    need_helper = _function(
        script, "unit_need_daemon_reload_is_no", "require_no_pending_unit_reload"
    )
    harness = tmp_path / "need-daemon-reload.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        'systemctl_property_value() { printf "no\\n"; return "${FAKE_RC:-0}"; }\n'
        f"{need_helper}\n"
        "unit_need_daemon_reload_is_no demo.service\n",
        encoding="utf-8",
        newline="\n",
    )
    good = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "0"},
        text=True,
        capture_output=True,
        check=False,
    )
    masked_failure = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "77"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert masked_failure.returncode != 0


def test_success_units_are_byte_pinned_and_effective_contract_is_checked_twice(
    script: str,
) -> None:
    exec_normalizer = _function(
        script, "normalized_exec_start_property", "normalized_environment_property"
    )
    assert 'raw="$(systemctl_property_value "$unit" ExecStart)" || return 1' in exec_normalizer
    assert 'raw.count("{ path=") != 1' in exec_normalizer
    assert '"\\n" in raw' in exec_normalizer
    assert 'marker = " ; start_time="' in exec_normalizer
    assert "unexpected volatile ExecStart suffix" in exec_normalizer
    assert 'argv\\[\\]=.+' in exec_normalizer

    environment_normalizer = _function(
        script, "normalized_environment_property", "unit_security_contract"
    )
    assert 'raw="$(systemctl_property_value "$unit" Environment)" || return 1' in environment_normalizer
    assert "shlex.split" in environment_normalizer
    assert "key in values" in environment_normalizer
    assert "sorted(values)" in environment_normalizer

    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    target_snapshots = script.index(
        "for target_unit in tradingbot-api.service tradingbot-bg.service", archive
    )
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert archive < target_snapshots < mutation
    for required in (
        '"$backup/expected-$target_unit"',
        'regular file|root:root:644|1|',
        'cmp -s -- "$final_clone/deploy/$target_unit"',
        'expected_api_override="$backup/legacy-direct-frontend.expected.conf"',
        'Environment="API_BIND_HOST=0.0.0.0"',
        "streamlit.service.before",
        "frontend.service.before",
        "cron.service.before",
    ):
        assert required in script

    success_start = script.index("success_unit_disk_contract_matches() {")
    success_end = script.index("\nrollback_frontend_snapshot_matches() {", success_start)
    success = script[success_start:success_end]
    for pinned in (
        "/etc/systemd/system/tradingbot-api.service",
        "/etc/systemd/system/tradingbot-bg.service",
        "/etc/systemd/system/tradingbot-api.service.d/legacy-direct-frontend.conf",
        "/etc/systemd/system/tradingbot-frontend.service",
        "/etc/systemd/system/tradingbot.service",
        '"$cron_fragment_path"',
    ):
        assert pinned in success
    assert success.count("unit_need_daemon_reload_is_no") >= 1
    assert "tradingbot-api.service tradingbot-bg.service tradingbot.service" in success
    for property_name in (
        "FragmentPath",
        "DropInPaths",
        "User",
        "Group",
        "WorkingDirectory",
        "EnvironmentFiles",
        "ExecCondition",
        "ExecStartPre",
        "ExecStartPost",
        "ExecReload",
        "ExecStop",
        "ExecStopPost",
        "RootDirectory",
        "RootImage",
    ):
        assert property_name in success
    assert "normalized_exec_start_property" in success
    assert "normalized_environment_property" in success
    assert "runtime_service_home_contract_matches" in success
    assert 'unit_security_contract_matches tradingbot.service' in success
    assert 'unit_security_contract_matches tradingbot-frontend.service' in success
    assert 'unit_security_contract_matches cron.service' in success

    safe_deploy = script.index('/bin/bash "$APP/deploy/safe_deploy.sh"')
    post_safe = script.index(
        'post_safe_deploy_unit_contract="$(success_unit_contract)"', safe_deploy
    )
    cron_proof = script.index('[ "$cron_tick_proven" = 1 ]', post_safe)
    final_gate = script.index(
        'final_success_unit_contract="$(success_unit_contract)"', cron_proof
    )
    exact_compare = script.index(
        '[ "$final_success_unit_contract" = "$post_safe_deploy_unit_contract" ]',
        final_gate,
    )
    account_closed = script.index("account_is_closed \\", exact_compare)
    login_restore = script.index("restore_service_login \\", account_closed)
    assert safe_deploy < post_safe < cron_proof < final_gate < exact_compare
    assert exact_compare < account_closed < login_restore


def test_rollback_frontend_uses_only_root_owned_isolated_snapshot(
    script: str,
) -> None:
    tree_guard = _function(
        script, "frontend_tree_is_safe", "frontend_tree_manifest_digest"
    )
    assert 'find "$root" -xdev -mindepth 1 -print0 > "$list_file"' in tree_guard
    assert 'is_not_mountpoint "$entry"' in tree_guard
    assert '[ "$links" = 1 ]' in tree_guard
    assert "MAX_ROLLBACK_FRONTEND_BYTES" in tree_guard
    assert "MAX_ROLLBACK_FRONTEND_ENTRIES" in tree_guard
    assert '(8#$mode & 8#22) == 0' in tree_guard

    manifest = _function(
        script, "frontend_tree_manifest_digest", "unit_matches"
    )
    assert "os.O_NOFOLLOW" in manifest
    assert "os.O_NONBLOCK" in manifest
    assert "before_fd.st_nlink != 1" in manifest
    assert 'manifest.update(b"D\\0"' in manifest
    assert 'manifest.update(\n            b"F\\0"' in manifest

    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    snapshot = script.index(
        'frontend_tree_is_safe "$APP/frontend" 0', mutation
    )
    manifest_bind = script.index("rollback_frontend_snapshot_ready=1", snapshot)
    old_move = script.index('mv -T -- "$APP" "$quarantine/old-app"')
    assert mutation < snapshot < manifest_bind < old_move
    assert 'cp -a -- "$APP/frontend/." "$rollback_frontend_snapshot/"' in script
    assert '[ "$frontend_source_manifest_before" = "$frontend_source_manifest_after" ]' in script
    assert '[ "$frontend_source_manifest_before" = "$rollback_frontend_manifest" ]' in script

    assert "WorkingDirectory=/" in script
    assert (
        "ExecStart=/usr/bin/python3 -I -S -m http.server 3000 --bind 0.0.0.0 "
        "--directory $rollback_frontend_snapshot"
    ) in script
    assert "ProtectSystem=strict" in script
    assert "ProtectHome=read-only" in script

    rollback_start = script.index("rollback() {")
    rollback = script[rollback_start : script.index("finish() {", rollback_start)]
    assert (
        'cp --preserve=all -- "$backup/frontend.service.before"'
        not in rollback
    )
    unit_install = rollback.index(
        'cp --preserve=all -- "$rollback_frontend_unit"'
    )
    daemon_reload = rollback.index("systemctl daemon-reload")
    effective_gate = rollback.index(
        "rollback_frontend_execution_contract_matches", daemon_reload
    )
    service_start = rollback.index(
        "systemctl start tradingbot-api.service tradingbot-bg.service"
    )
    assert unit_install < daemon_reload < effective_gate < service_start
    effective_helper = _function(
        script,
        "rollback_frontend_execution_contract_matches",
        "path_absent",
    )
    assert '[ "$value" = / ]' in effective_helper
    assert 'case "$frontend_exec" in *"$APP"*) return 1' in effective_helper
    assert "EnvironmentFiles" in effective_helper
    assert "ExecStartPre" in effective_helper
    assert "NeedDaemonReload" not in effective_helper or (
        "unit_need_daemon_reload_is_no" in effective_helper
    )


def test_login_linger_restore_exists_on_success_and_rollback(script: str) -> None:
    restore_function = script.index("restore_service_login() {")
    rollback = script.index("rollback() {")
    rollback_restore = script.index("if ! restore_service_login; then", rollback)
    rollback_health = script.index("rollback_health_ok", rollback_restore - 8000)
    rollback_nginx = script.index('"$current_nginx" = "$nginx_before"', rollback_health)
    success_closed = script.rindex("account_is_closed", rollback_restore)
    success_restore = script.rindex("restore_service_login \\")
    trap_off = script.rindex("trap - EXIT INT TERM")
    assert restore_function < rollback < rollback_health < rollback_nginx
    assert rollback_nginx < rollback_restore < success_closed < success_restore < trap_off
    assert 'usermod --shell "$old_service_shell" tradingbot' in script
    assert "loginctl enable-linger tradingbot" in script
    assert "loginctl disable-linger tradingbot" in script


def test_rollback_is_swap_milestone_guarded_and_proves_old_identity(
    script: str,
) -> None:
    rollback = script.index("rollback() {")
    rollback_cwd = script.index("cd /root || {", rollback)
    rollback_reclose = script.index(
        'usermod --shell "$nologin_bin" tradingbot', rollback_cwd
    )
    rollback_stop = script.index("systemctl stop cron.service", rollback)
    updater_lock = script.index("flock -w 120 8", rollback_stop)
    rollback_reclose_after_lock = script.index(
        'usermod --shell "$nologin_bin" tradingbot', updater_lock
    )
    rollback_restop = script.index("systemctl stop cron.service", updater_lock)
    hard_gate = script.index(
        'if [ "$rollback_failed" != 0 ] || ! assert_quiescent; then', rollback
    )
    fail_closed_return = script.index("return 1", hard_gate)
    layout_gate = script.index(
        'rollback_layout="$(rollback_app_layout_state)"', hard_gate
    )
    secret_location_gate = script.index(
        'rollback_secret_moved="$(derive_new_app_secret_milestone', layout_gate
    )
    milestone = script.index(
        'if [ "$rollback_layout" != old-at-app ]', secret_location_gate
    )
    failed_new_move = script.index('mv -T -- "$APP" "$failed_app"', milestone)
    restore_old = script.index('mv -T -- "$quarantine/old-app" "$APP"', milestone)
    first_runtime_restore = script.index('if [ -n "$runtime_home_inode" ]', rollback)
    first_unit_restore = script.index(
        "stash_path /etc/systemd/system/tradingbot-api.service", rollback
    )
    assert rollback < rollback_cwd < rollback_reclose < rollback_stop < updater_lock
    assert updater_lock < rollback_reclose_after_lock < rollback_restop < hard_gate
    assert hard_gate < fail_closed_return < layout_gate < secret_location_gate < milestone
    assert fail_closed_return < failed_new_move < restore_old
    assert fail_closed_return < first_runtime_restore < first_unit_restore
    assert "keine APP-/Runtime-/Unit-/Secret-Mutation" in script[hard_gate:layout_gate]
    quiescent_start = script.index("assert_quiescent() {")
    quiescent_end = script.index("# Nach dem Swap", quiescent_start)
    quiescent = script[quiescent_start:quiescent_end]
    assert "account_is_closed || return 1" in quiescent
    assert '"$current_identity" = "$old_revision $old_bundle"' in script
    assert 'old-frontend-$frontend_file' in script
    rollback_body = script[rollback : script.index("finish() {", rollback)]
    assert 'git -c safe.directory="$APP"' not in rollback_body
    for contract in (
        "unit_matches tradingbot-api.service active enabled",
        "unit_matches tradingbot-bg.service active enabled",
        "unit_matches tradingbot.service inactive disabled",
        "unit_matches tradingbot-frontend.service active enabled",
        "unit_matches cron.service active enabled",
    ):
        assert contract in script[rollback:]
    assert 'root-crontab.after-rollback' in script
    assert '"$current_nginx" = "$nginx_before"' in script


def test_root_crontab_is_failclosed_and_byte_identical(script: str) -> None:
    helper = _function(
        script, "assert_root_crontab_empty", "assert_no_legacy_cron_bytes"
    )
    assert 'output="$(crontab -l 2>/dev/null)"' in helper
    assert '|| die "Root-crontab konnte nicht sicher abgefragt werden"' in helper
    assert "rc=1" not in helper
    assert '[ -z "$output" ]' in helper
    assert 'root-crontab.after-install' in script
    assert 'root-crontab.final' in script
    assert script.count('cmp -s "$backup/root-crontab.before"') >= 4


def test_real_cron_tick_requires_new_log_and_scheduler_journal(script: str) -> None:
    manual_probe = script.index('/bin/bash "$LAUNCHER" --probe >> "$LOG_FILE"')
    marker = script.index('touch -r "$LOG_FILE" "$backup/probe-log.timestamp"')
    cron_start = script.index("systemctl start cron.service", marker)
    wait_loop = script.index('cron_deadline=$((cron_wait_epoch + 750))')
    journal = script.index("journalctl -u cron.service", wait_loop)
    proof = script.index('cron_tick_proven=1', journal)
    final_health = script.index(
        '/bin/bash "$APP/deploy/health_check.sh" --auto-update-only', proof
    )
    assert manual_probe < marker < cron_start < wait_loop < journal < proof < final_health
    assert '[ "$LOG_FILE" -nt "$backup/probe-log.timestamp" ]' in script
    assert 'tail -c "+$((probe_size + 1))"' in script
    assert "action=current" in script[wait_loop:proof]
    assert 'grep -F "CMD ($cron_command)"' in script
    assert "maximal $((cron_deadline - now_epoch)) Sekunden" in script


def test_runtime_secret_is_root_controlled_and_source_files_move_inode_exact(
    script: str,
) -> None:
    assert 'install -o root -g tradingbot -m 0640 "$GLOBAL_SECRET" "$RUNTIME_SECRET"' in script
    assert 'install -d -o root -g tradingbot -m 0750 "$RUNTIME_HOME/.streamlit"' in script
    assert '"$runtime_streamlit_inode" 0 "$(id -g tradingbot)" 750' in script
    assert 'chown tradingbot:tradingbot "$RUNTIME_HOME"' in script
    assert 'chmod 0700 "$RUNTIME_HOME"' in script
    assert '640 root:tradingbot' in script
    assert 'mv -T -- "$quarantine/old-app/.env" "$APP/.env"' in script
    assert (
        'mv -T -- "$quarantine/old-app/.streamlit/secrets.toml"'
        in script
    )
    assert 'mv -T -- "$quarantine/old-app/data_cache" "$APP/data_cache"' in script
    assert script.count("inodegleich") >= 2
    assert 'chown root:tradingbot "$GLOBAL_SECRET"' not in script
    assert 'chmod 0640 "$GLOBAL_SECRET"' not in script
    assert "capture_global_secret_contract" in script
    assert "tradingbot:tradingbot" in script
    assert "Legacy-Home-secret hat sich veraendert" in script
    assert 'runtime_secret_contract="$(capture_runtime_secret_contract)"' in script
    runtime_helper = _function(
        script, "runtime_secret_state_matches", "metadata_matches_snapshot"
    )
    assert runtime_helper.count("secure_directory_contract_matches") >= 4
    assert '"$runtime_home_inode"' in runtime_helper
    assert '"$runtime_streamlit_inode"' in runtime_helper
    assert 'capture_runtime_secret_contract' in runtime_helper
    safe_deploy_start = script.index('/bin/bash "$APP/deploy/safe_deploy.sh"')
    success_tail = script[safe_deploy_start:]
    assert success_tail.count("runtime_secret_state_matches") >= 2
    assert 'stat -c \'%U:%G:%a\' -- "$RUNTIME_SECRET"' not in success_tail
    assert 'cmp -s "$backup/home-secrets.before" "$RUNTIME_SECRET"' not in success_tail
    absence_gate = script.index(
        '[ ! -e "$RUNTIME_HOME" ] && [ ! -L "$RUNTIME_HOME" ]'
    )
    preflight_archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    assert absence_gate < preflight_archive
    assert "runtime_dir_existed" not in script
    assert "runtime_streamlit_existed" not in script
    assert 'path_absent "$RUNTIME_HOME" || return 1' in script
    assert "Owner/Mode/ACL" in script
    assert "Rollback behaelt diese Haertung" in script
    assert "loescht keine neuen Auth-Artefakte" in script


def test_runtime_guard_runs_before_only_legacy_frontend_starts(script: str) -> None:
    guard = script.index('/bin/bash "$APP/deploy/runtime_state_guard.sh"')
    frontend_start = script.index(
        "systemctl start tradingbot-frontend.service", guard
    )
    safe_deploy = script.index('/bin/bash "$APP/deploy/safe_deploy.sh"', frontend_start)
    between_guard_and_safe = script[guard:safe_deploy]
    assert guard < frontend_start < safe_deploy
    assert "systemctl start tradingbot-api.service" not in between_guard_and_safe
    assert "systemctl start tradingbot-bg.service" not in between_guard_and_safe
    assert "unit_is tradingbot-api.service inactive enabled" in between_guard_and_safe
    assert "unit_is tradingbot-bg.service inactive enabled" in between_guard_and_safe
    safe_prefix = script[safe_deploy - 350 : safe_deploy]
    assert '(\n  cd "$APP"' in safe_prefix
    assert '  env HOME=/root GIT_CONFIG_NOSYSTEM=1' in safe_prefix


def test_migration_never_executes_installer_or_mutates_nginx(script: str) -> None:
    assert '/bin/bash "$APP/deploy/install.sh"' not in script
    assert "/bin/bash deploy/install.sh" not in script
    assert "setup_tls.sh" not in script
    assert not re.search(
        r"systemctl\s+(?:restart|reload|stop|start)\s+(?:nginx|nginx\.service)",
        script,
    )
    assert "nginx -s" not in script
    assert "certbot" not in script
    assert "find . -xdev \\( -type f -o -type l \\) -print0" in script
    assert 'readlink -- "$entry"' in script
    nginx_helper = _function(script, "nginx_manifest_digest", "git_control")
    assert "< <(" not in nginx_helper
    assert 'sort -z > "$list_file"' in nginx_helper
    assert ') || rc=1' in nginx_helper
    assert 'file_digest_line="$(sha256sum -- "$entry")" || exit 1' in nginx_helper
    assert 'digest_line="$(sha256sum -- "$manifest_file")" || rc=1' in nginx_helper
    assert 'rm -f -- "$list_file" "$manifest_file" || rc=1' in nginx_helper
    assert script.count("nginx_manifest_digest") >= 4
    initial_manifest = script.index('nginx_before="$(nginx_manifest_digest)"')
    backup = script.index('backup="$(mktemp -d')
    preflight = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    late_manifest = script.index('nginx_pre_mutation="$(nginx_manifest_digest)"')
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert initial_manifest < backup < preflight < late_manifest < mutation
    assert '[ "$nginx_pre_mutation" = "$nginx_before" ]' in script
    assert script.count(
        'require_systemctl_property nginx.service MainPID "$nginx_pid"'
    ) >= 2
    assert script.count("systemctl_property_value nginx.service MainPID") >= 3


def test_unit_state_queries_propagate_nonzero_status_even_with_expected_payload(
    tmp_path: Path, script: str
) -> None:
    function = _function(script, "unit_matches", "unit_is")
    harness = tmp_path / "unit-state.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        "systemctl() {\n"
        "  case \"$*\" in\n"
        "    *LoadState*) printf 'loaded\\n' ;;\n"
        "    *ActiveState*) printf 'active\\n' ;;\n"
        "    *UnitFileState*) printf 'enabled\\n' ;;\n"
        "  esac\n"
        "  return \"${FAKE_RC:-0}\"\n"
        "}\n"
        f"{function}\n"
        "unit_matches demo.service active enabled\n",
        encoding="utf-8",
        newline="\n",
    )
    good = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "0"},
        text=True,
        capture_output=True,
        check=False,
    )
    masked_failure = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "77"},
        text=True,
        capture_output=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert masked_failure.returncode != 0


def test_empty_systemd_property_query_cannot_mask_nonzero_status(
    tmp_path: Path, script: str
) -> None:
    function = _function(
        script, "systemctl_property_value", "require_systemctl_property"
    )
    harness = tmp_path / "empty-property.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        "systemctl() { printf '%s' \"${FAKE_PAYLOAD:-}\"; "
        "return \"${FAKE_RC:-0}\"; }\n"
        f"{function}\n"
        "value=\"$(systemctl_property_value demo.service DropInPaths)\"\n"
        "test -z \"$value\"\n",
        encoding="utf-8",
        newline="\n",
    )
    good = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "0", "FAKE_PAYLOAD": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    masked_failure = subprocess.run(
        [_bash(), harness.as_posix()],
        env={**os.environ, "FAKE_RC": "77", "FAKE_PAYLOAD": ""},
        text=True,
        capture_output=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert masked_failure.returncode != 0

    assert script.count("require_empty_systemctl_property") >= 5
    assert "for property in ExecStartPre ExecStartPost ExecStop ExecStopPost" in script
    assert '[ -z "$(systemctl show' not in script


def test_preserved_secrets_bind_start_inode_and_hash_before_live_preflight(
    script: str,
) -> None:
    helper = _function(
        script, "capture_regular_file_contract", "capture_local_directory_contract"
    )
    assert "os.O_NOFOLLOW" in helper
    assert "os.O_NONBLOCK" in helper
    assert "os.lstat(path)" in helper
    assert helper.count("os.fstat(fd)") == 2
    assert "st.st_dev" in helper and "st.st_ino" in helper
    assert "st.st_mtime_ns" in helper and "st.st_ctime_ns" in helper
    assert "hashlib.sha256()" in helper
    assert "stat.S_ISREG" in helper
    assert "before_fd.st_nlink != 1" in helper

    env_capture = script.index(
        'env_preserved_contract="$(capture_preserved_file_contract "$APP/.env")"'
    )
    secret_capture = script.index("app_secret_preserved_contract=", env_capture)
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    post_quiesce = script.index("validate_preserved_runtime_state", mutation)
    old_move = script.index('mv -T -- "$APP" "$quarantine/old-app"')
    assert env_capture < secret_capture < archive < post_quiesce < old_move
    assert '"$backup/env.inode-sha256.before"' in script
    assert '"$backup/app-secret.inode-sha256.before"' in script

    validator = _function(
        script, "validate_preserved_runtime_state", "linger_matches"
    )
    assert validator.count(
        'capture_preserved_file_contract "$APP/.env"'
    ) >= 2
    assert validator.count(
        '"$APP/.streamlit/secrets.toml"'
    ) >= 3
    assert validator.count('"$env_preserved_contract"') >= 2
    assert validator.count('"$app_secret_preserved_contract"') >= 2


def test_app_streamlit_parent_is_inode_bound_across_forward_and_rollback(
    script: str,
) -> None:
    helper = _function(
        script, "capture_local_directory_contract", "root_controlled_directory_contract"
    )
    assert "os.lstat(path)" in helper
    assert "os.O_DIRECTORY" in helper
    assert "os.O_NOFOLLOW" in helper
    assert helper.count("os.fstat(fd)") == 2
    assert "before_fd.st_dev != expected_device" in helper
    assert "is_not_mountpoint" in helper
    assert "st_uid" in helper and "st_gid" in helper
    assert "stat.S_IMODE" in helper

    initial = script.index("app_streamlit_dir_contract=")
    secret_capture = script.index("app_secret_preserved_contract=", initial)
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    final_parent = script.index("new_app_streamlit_dir_contract=", archive)
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert initial < secret_capture < archive < final_parent < mutation

    validator = _function(
        script, "validate_preserved_runtime_state", "linger_matches"
    )
    assert validator.count("capture_local_directory_contract") >= 2
    assert validator.count('"$app_streamlit_dir_contract"') >= 2

    old_move = script.index('mv -T -- "$APP" "$quarantine/old-app"')
    quarantine_parent_gate = script.index(
        "original_app_streamlit_parent_matches", old_move
    )
    final_move = script.index('mv -T -- "$final_clone" "$APP"')
    final_parent_gate = script.index(
        'new_app_streamlit_parent_matches_at "$APP/.streamlit"', final_move
    )
    secret_move = script.index(
        'mv -T -- "$quarantine/old-app/.streamlit/secrets.toml"'
    )
    forward_source_gate = script.rfind(
        "original_app_streamlit_parent_matches", final_parent_gate, secret_move
    )
    forward_target_gate = script.rfind(
        'new_app_streamlit_parent_matches_at "$APP/.streamlit"',
        final_parent_gate,
        secret_move,
    )
    assert old_move < quarantine_parent_gate < final_move < final_parent_gate
    assert final_parent_gate < forward_source_gate < forward_target_gate < secret_move

    rollback = script.index("rollback() {")
    rollback_first_mutation = script.index(
        'if [ "$rollback_layout" != old-at-app ]', rollback
    )
    rollback_parent_gate = script.index(
        "original_app_streamlit_parent_matches", rollback
    )
    rollback_loop_gate = script.index(
        'if [ "$rel" = .streamlit/secrets.toml ]', rollback
    )
    assert rollback_parent_gate < rollback_first_mutation < rollback_loop_gate
    assert "new_app_streamlit_parent_matches_at" in script[
        rollback_loop_gate : rollback_loop_gate + 500
    ]


@pytest.mark.skipif(os.name == "nt", reason="Linux O_DIRECTORY/O_NOFOLLOW contract")
def test_app_streamlit_parent_contract_rejects_symlink(
    tmp_path: Path, script: str
) -> None:
    helper = _function(
        script, "capture_local_directory_contract", "root_controlled_directory_contract"
    )
    harness = tmp_path / "directory-contract.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        "is_not_mountpoint() { return 0; }\n"
        f"{helper}\n"
        'capture_local_directory_contract "$1" "$2" >/dev/null\n',
        encoding="utf-8",
        newline="\n",
    )
    real_parent = tmp_path / "real-parent"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    device = str(os.stat(real_parent).st_dev)
    good = subprocess.run(
        [_bash(), harness.as_posix(), real_parent.as_posix(), device],
        text=True,
        capture_output=True,
        check=False,
    )
    unsafe = subprocess.run(
        [_bash(), harness.as_posix(), linked_parent.as_posix(), device],
        text=True,
        capture_output=True,
        check=False,
    )
    assert good.returncode == 0, good.stderr
    assert unsafe.returncode != 0


def test_final_clone_streamlit_contract_is_prebound_for_post_swap_failure(
    script: str,
) -> None:
    prebind = script.index("new_app_streamlit_dir_contract=")
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    final_move = script.index('mv -T -- "$final_clone" "$APP"')
    post_move_gate = script.index(
        'new_app_streamlit_parent_matches_at "$APP/.streamlit"', final_move
    )
    assert prebind < mutation < final_move < post_move_gate
    new_app_prebind = script.index("new_app_dir_contract=")
    assert new_app_prebind < mutation < final_move
    assert "new_app_streamlit_dir_contract=''" not in script
    assert 'new_app_secret_moved=0' in script[:mutation]
    rollback = script[script.index("rollback() {") : script.index("finish() {")]
    physical_layout_gate = rollback.index(
        'rollback_layout="$(rollback_app_layout_state)"'
    )
    physical_secret_gate = rollback.index(
        'rollback_secret_moved="$(derive_new_app_secret_milestone',
        physical_layout_gate,
    )
    first_restore_mutation = rollback.index(
        'if [ "$rollback_layout" != old-at-app ]'
    )
    assert physical_layout_gate < physical_secret_gate < first_restore_mutation
    rollback_secret_branch = rollback.index(
        'if [ "$rel" = .streamlit/secrets.toml ]'
    )
    milestone_skip = rollback.index(
        'if [ "$rollback_secret_moved" != 1 ]', rollback_secret_branch
    )
    source_parent_read = rollback.index(
        "new_app_streamlit_parent_matches_at", milestone_skip
    )
    assert rollback_secret_branch < milestone_skip < source_parent_read


def test_app_and_secret_rename_flags_are_telemetry_not_restore_authority(
    script: str,
) -> None:
    initial_old_contract = script.index("original_app_dir_contract=")
    initial_new_contract = script.index("new_app_dir_contract=")
    initial_quarantine_contract = script.index("quarantine_dir_contract=")
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    assert initial_old_contract < mutation
    assert initial_new_contract < mutation
    assert initial_quarantine_contract < mutation

    rollback = script[script.index("rollback() {") : script.index("finish() {")]
    layout = rollback.index('rollback_layout="$(rollback_app_layout_state)"')
    derive_secret = rollback.index(
        'rollback_secret_moved="$(derive_new_app_secret_milestone', layout
    )
    first_restore = rollback.index(
        'if [ "$rollback_layout" != old-at-app ]', derive_secret
    )
    assert layout < derive_secret < first_restore
    assert 'if [ "$old_app_quarantined" = 1 ]' not in rollback
    assert 'if [ "$new_app_secret_moved"' not in rollback[:first_restore]
    assert 'if [ "$rollback_secret_moved" != 1 ]' in rollback[first_restore:]
    assert 'if [ -d "$quarantine/old-app" ]' not in rollback
    assert 'new_app_directory_matches_at "$APP"' in rollback[first_restore:]

    old_move = script.index('mv -T -- "$APP" "$quarantine/old-app"')
    old_flag = script.index("old_app_quarantined=1", old_move)
    old_physical_gate = script.index(
        'forward_app_layout="$(rollback_app_layout_state)"', old_flag
    )
    new_move = script.index('mv -T -- "$final_clone" "$APP"', old_physical_gate)
    new_physical_gate = script.index(
        'forward_app_layout="$(rollback_app_layout_state)"', new_move
    )
    secret_move = script.index(
        'mv -T -- "$quarantine/old-app/.streamlit/secrets.toml"',
        new_physical_gate,
    )
    secret_flag = script.index("new_app_secret_moved=1", secret_move)
    secret_physical_gate = script.index(
        'forward_secret_milestone="$(derive_new_app_secret_milestone',
        secret_flag,
    )
    assert old_move < old_flag < old_physical_gate < new_move < new_physical_gate
    assert new_physical_gate < secret_move < secret_flag < secret_physical_gate


@pytest.mark.skipif(os.name == "nt", reason="Linux atomic rename/O_NOFOLLOW harness")
def test_physical_layout_recovers_both_app_rename_and_secret_flag_windows(
    tmp_path: Path, script: str
) -> None:
    regular_helper = _function(
        script, "capture_regular_file_contract", "capture_local_directory_contract"
    )
    local_dir_helper = _function(
        script, "capture_local_directory_contract", "root_controlled_directory_contract"
    )
    layout_start = script.index("app_streamlit_parent_matches_at() {")
    layout_end = script.index("\nruntime_directory_matches_inode() {", layout_start)
    layout_helpers = script[layout_start:layout_end]
    harness = tmp_path / "rename-window-contract.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\numask 077\n"
        "is_not_mountpoint() { return 0; }\n"
        "path_absent() { [ ! -e \"$1\" ] && [ ! -L \"$1\" ]; }\n"
        f"{regular_helper}\n{local_dir_helper}\n"
        "capture_preserved_file_contract() {\n"
        "  capture_regular_file_contract \"$1\" \"$(id -u)\" \"$(id -g)\" 600 test:test\n"
        "}\n"
        f"{layout_helpers}\n"
        "base=\"$1\"\nAPP=\"$base/app\"\nquarantine=\"$base/quarantine\"\n"
        "final_clone=\"$base/final\"\n"
        "mkdir -p \"$APP/.streamlit\" \"$quarantine\" \"$final_clone/.streamlit\"\n"
        "printf secret-bytes > \"$APP/.streamlit/secrets.toml\"\n"
        "chmod 600 \"$APP/.streamlit/secrets.toml\"\n"
        "original_app_device=\"$(stat -c %d -- \"$APP\")\"\n"
        "original_app_dir_contract=\"$(capture_local_directory_contract \"$APP\" \"$original_app_device\")\"\n"
        "new_app_dir_contract=\"$(capture_local_directory_contract \"$final_clone\" \"$original_app_device\")\"\n"
        "quarantine_dir_contract=\"$(capture_local_directory_contract \"$quarantine\" \"$original_app_device\")\"\n"
        "app_streamlit_dir_contract=\"$(capture_local_directory_contract \"$APP/.streamlit\" \"$original_app_device\")\"\n"
        "new_app_streamlit_dir_contract=\"$(capture_local_directory_contract \"$final_clone/.streamlit\" \"$original_app_device\")\"\n"
        "app_secret_preserved_contract=\"$(capture_preserved_file_contract \"$APP/.streamlit/secrets.toml\")\"\n"
        "[ \"$(rollback_app_layout_state)\" = old-at-app ]\n"
        "mv -T -- \"$APP\" \"$quarantine/old-app\"\n"
        "[ \"$(rollback_app_layout_state)\" = old-at-quarantine-app-absent ]\n"
        "[ \"$(original_app_directory_location)\" = quarantine ]\n"
        "mv -T -- \"$final_clone\" \"$APP\"\n"
        "[ \"$(rollback_app_layout_state)\" = old-at-quarantine-new-at-app ]\n"
        "[ \"$(derive_new_app_secret_milestone old-at-quarantine-new-at-app)\" = 0 ]\n"
        "mv -T -- \"$quarantine/old-app/.streamlit/secrets.toml\" \"$APP/.streamlit/secrets.toml\"\n"
        "[ \"$(derive_new_app_secret_milestone old-at-quarantine-new-at-app)\" = 1 ]\n"
        "mv -T -- \"$APP\" \"$quarantine/failed-new-app\"\n"
        "[ \"$(original_app_directory_location)\" = quarantine ]\n"
        "mv -T -- \"$quarantine/failed-new-app/.streamlit/secrets.toml\" \"$quarantine/old-app/.streamlit/secrets.toml\"\n"
        "mv -T -- \"$quarantine/old-app\" \"$APP\"\n"
        "[ \"$(original_app_directory_location)\" = app ]\n"
        "original_app_directory_matches_at \"$APP\"\n",
        encoding="utf-8",
        newline="\n",
    )
    result = subprocess.run(
        [_bash(), harness.as_posix(), tmp_path.as_posix()],
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


def test_cron_source_scan_is_failclosed_and_repeated(
    tmp_path: Path, script: str
) -> None:
    helper = _function(
        script, "assert_no_legacy_cron_bytes", "require_free_space"
    )
    assert "grep -RFl" not in script
    assert 'if grep -Fq -- "$needle" "$entry"; then' in helper
    assert 'if [ "$grep_rc" != 1 ]' in helper
    assert 'find "$source" -xdev -mindepth 1 -maxdepth 1 -print0' in helper
    assert script.count("assert_no_legacy_cron_bytes") == 4
    first_call = script.index("assert_no_legacy_cron_bytes", script.index("}", script.index("assert_no_legacy_cron_bytes()")))
    archive = script.index('git_control archive --format=tar "$EXPECTED_REVISION"')
    second_call = script.index("assert_no_legacy_cron_bytes", first_call + 1)
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    third_call = script.index("assert_no_legacy_cron_bytes", mutation)
    assert first_call < archive < second_call < mutation < third_call

    cron_file = tmp_path / "crontab"
    cron_dir = tmp_path / "cron.d"
    spool_dir = tmp_path / "spool"
    cron_file.write_text("# empty\n", encoding="utf-8")
    cron_dir.mkdir()
    spool_dir.mkdir()
    transformed = (
        helper.replace("/etc/crontab", '"$CRON_FILE_TEST"')
        .replace("/etc/cron.d", '"$CRON_DIR_TEST"')
        .replace("/var/spool/cron/crontabs", '"$SPOOL_DIR_TEST"')
        .replace(
            "mktemp /root/alpha-cron-scan.XXXXXX",
            'mktemp "$TMPDIR/alpha-cron-scan.XXXXXX"',
        )
    )
    harness = tmp_path / "cron-scan.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        'export PATH="${BASH%/*}:$PATH"\n'
        'CRON_FILE_TEST="$1"\nCRON_DIR_TEST="$2"\nSPOOL_DIR_TEST="$3"\n'
        'is_real() { [ -e "$1" ] && [ ! -L "$1" ]; }\n'
        "is_not_mountpoint() { return 0; }\n"
        'grep() { return "${FAKE_GREP_RC:-1}"; }\n'
        f"{transformed}\n"
        "assert_no_legacy_cron_bytes\n",
        encoding="utf-8",
        newline="\n",
    )
    args = [
        _bash(),
        harness.as_posix(),
        cron_file.as_posix(),
        cron_dir.as_posix(),
        spool_dir.as_posix(),
    ]
    clean = subprocess.run(
        args,
        env={**os.environ, "FAKE_GREP_RC": "1", "TMPDIR": tmp_path.as_posix()},
        text=True,
        capture_output=True,
        check=False,
    )
    query_error = subprocess.run(
        args,
        env={**os.environ, "FAKE_GREP_RC": "2", "TMPDIR": tmp_path.as_posix()},
        text=True,
        capture_output=True,
        check=False,
    )
    match = subprocess.run(
        args,
        env={**os.environ, "FAKE_GREP_RC": "0", "TMPDIR": tmp_path.as_posix()},
        text=True,
        capture_output=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stderr
    assert query_error.returncode != 0
    assert match.returncode != 0


def test_runtime_inode_state_is_initialized_and_committed_atomically(
    script: str,
) -> None:
    mutation = script.index("mutation_started=1", script.index("trap finish EXIT"))
    for initialization in (
        "env_inode=''",
        "app_secret_inode=''",
        "cache_inode=''",
        "preserved_runtime_contract_ready=0",
    ):
        assert script.index(initialization) < mutation
    captures = [
        script.index("captured_env_inode="),
        script.index("captured_app_secret_inode="),
        script.index("captured_cache_inode="),
    ]
    assignments = [
        script.index('env_inode="$captured_env_inode"'),
        script.index('app_secret_inode="$captured_app_secret_inode"'),
        script.index('cache_inode="$captured_cache_inode"'),
    ]
    ready = script.index("preserved_runtime_contract_ready=1")
    assert max(captures) < min(assignments) < max(assignments) < ready
    restore_gate = _function(
        script, "rollback_filesystem_restore_is_exact", "rollback"
    )
    guarded = restore_gate.index(
        'if [ "$preserved_runtime_contract_ready" = 1 ]'
    )
    first_inode_use = restore_gate.index('"$app_secret_inode"')
    assert guarded < first_inode_use


@pytest.mark.skipif(os.name == "nt", reason="Linux O_NOFOLLOW/FIFO contract")
def test_regular_file_contract_rejects_symlink_fifo_and_hardlink(
    tmp_path: Path, script: str
) -> None:
    function = _function(
        script, "capture_regular_file_contract", "capture_local_directory_contract"
    )
    harness = tmp_path / "capture-contract.sh"
    harness.write_text(
        "#!/usr/bin/env bash\nset -Eeuo pipefail\n"
        f"{function}\n"
        'capture_regular_file_contract "$1" "$2" "$3" 600 test:test\n',
        encoding="utf-8",
        newline="\n",
    )
    regular = tmp_path / "regular"
    regular.write_bytes(b"secret-bytes")
    regular.chmod(0o600)
    uid, gid = str(os.getuid()), str(os.getgid())
    good = subprocess.run(
        [_bash(), harness.as_posix(), regular.as_posix(), uid, gid],
        text=True,
        capture_output=True,
        check=False,
        timeout=3,
    )
    assert good.returncode == 0, good.stderr

    symlink = tmp_path / "symlink"
    symlink.symlink_to(regular)
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo, mode=0o600)
    hardlink = tmp_path / "hardlink"
    os.link(regular, hardlink)
    for unsafe in (symlink, fifo, hardlink):
        result = subprocess.run(
            [_bash(), harness.as_posix(), unsafe.as_posix(), uid, gid],
            text=True,
            capture_output=True,
            check=False,
            timeout=3,
        )
        assert result.returncode != 0, unsafe


def test_rollback_lstats_unsafe_paths_and_never_dereferences_them(
    script: str,
) -> None:
    runtime_restore = _function(
        script, "restore_runtime_metadata", "global_secret_parent_matches"
    )
    assert 'is_real "$path" && [ -d "$path" ]' in runtime_restore
    assert 'is_not_mountpoint "$path"' in runtime_restore
    assert runtime_restore.index('is_real "$path"') < runtime_restore.index("chown")

    rollback = script[script.index("rollback() {") : script.index("finish() {")]
    first_restore_mutation = rollback.index(
        'if [ "$rollback_layout" != old-at-app ]'
    )
    parent_gate = rollback.index("global_secret_parent_matches")
    runtime_gate = rollback.index("runtime_directory_matches_inode")
    assert parent_gate < first_restore_mutation
    assert runtime_gate < first_restore_mutation
    assert 'cmp -s "$GLOBAL_SECRET"' not in rollback
    assert 'chown --reference="$backup/home-secrets.before"' not in rollback
    global_lstat = rollback.index("capture_global_secret_contract")
    global_stash = rollback.index(
        'stash_path "$GLOBAL_SECRET" "$backup/home-secrets.failed"'
    )
    global_copy = rollback.index(
        'cp --preserve=all -- "$backup/home-secrets.before" "$GLOBAL_SECRET"'
    )
    assert global_lstat < global_stash < global_copy
    assert 'if stash_path "$GLOBAL_SECRET"' in rollback


def test_rollback_has_restore_commit_gate_and_reopens_account_last(
    script: str,
) -> None:
    rollback_start = script.index("rollback() {")
    rollback_end = script.index("finish() {")
    rollback = script[rollback_start:rollback_end]
    restore_gate = rollback.index("rollback_filesystem_restore_is_exact")
    daemon_reload = rollback.index("systemctl daemon-reload")
    service_start = rollback.index(
        "systemctl start tradingbot-api.service tradingbot-bg.service"
    )
    health_proof = rollback.index("rollback_health_ok", service_start)
    nginx_proof = rollback.index('"$current_nginx" = "$nginx_before"', health_proof)
    restore_login = rollback.index("if ! restore_service_login; then")
    assert restore_gate < daemon_reload < service_start < health_proof
    assert health_proof < nginx_proof < restore_login
    assert 'if [ "$rollback_failed" != 0 ] || [ "$can_restore_app" != 1 ]' in rollback
    assert rollback.count("rollback_leave_closed") >= 4
    assert '"$nologin_bin" ] || rollback_failed=1' in rollback
    assert "linger_matches no || rollback_failed=1" in rollback
    assert "restore_service_login || rollback_failed=1" not in rollback
