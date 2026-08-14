from pathlib import Path


ROOT = Path(__file__).resolve().parent


def _component_source(source: str, start: str, end: str) -> str:
    return source[source.index(start):source.index(end)]


def test_bi_scanner_uses_one_progress_display():
    source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    component = _component_source(source, "function BIScannerTab", "function BiotechTab")

    assert component.count("<ScanControl") == 1
    assert "<LiveScanStatus" not in component
    assert "scanKey={direction === 'long' ? 'bi_long' : 'bi_short'}" in component


def test_auto_update_runs_transactional_safe_deploy():
    source = (ROOT / "deploy" / "auto_update.sh").read_text(encoding="utf-8")

    assert "set -Eeuo pipefail" in source
    assert 'EXPECTED_REVISION="$REMOTE"' in source
    assert 'git_app show "${REMOTE}:deploy/safe_deploy.sh" > "$TARGET_DEPLOY"' in source
    assert 'bash "$TARGET_DEPLOY"' in source
    assert "trap cleanup_target_deploy EXIT" in source
    assert 'report error fetch "origin/main nicht abrufbar; Deployment nicht gestartet"' in source
    assert source.count("exit 1") >= 3
    assert "systemctl restart tradingbot-api tradingbot-bg" not in source
    assert "git pull --ff-only origin main" not in source
