"""Execute the actual frontend model-evidence guard; no browser/network needed."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parent


def presentation(payload):
    node = shutil.which("node")
    assert node, "Node is required to verify the shipped frontend guard"
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    start = source.index("function backtestEvidencePresentation(")
    end = source.index("function BacktestTab()", start)
    script = source[start:end] + "\nprocess.stdout.write(JSON.stringify(backtestEvidencePresentation(" + json.dumps(payload) + ")));"
    proc = subprocess.run([node, "-e", script], encoding="utf-8", capture_output=True, check=True)
    return json.loads(proc.stdout)


@pytest.mark.parametrize("metadata", [
    {},
    {"live_validation_eligible": False, "paper_autotrade_release_eligible": False},
    {"live_validation_eligible": True, "paper_autotrade_release_eligible": False},
    {"live_validation_eligible": "true", "paper_autotrade_release_eligible": "true"},
    {"live_equivalent": False, "live_validation_eligible": True, "paper_autotrade_release_eligible": True},
])
def test_proxy_or_unversioned_results_cannot_render_release(metadata):
    payload = {"verdict": {"status": "approved", "tradable": True}, **metadata}
    result = presentation(payload)
    assert result["verdict"]["tradable"] is False
    assert result["verdict"]["status"] == "model_limited"
    assert "KEINE LIVE-FREIGABE" in result["verdict"]["label"]


def test_real_explicit_eligibility_is_not_invented_or_removed():
    result = presentation({
        "verdict": {"status": "approved", "tradable": True},
        "live_validation_eligible": True, "paper_autotrade_release_eligible": True,
        "live_equivalent": True,
    })
    assert result["verdict"]["tradable"] is True
    assert result["modelLimited"] is False


def test_existing_data_incomplete_verdict_remains_more_specific():
    verdict = {"status": "data_incomplete", "tradable": False, "label": "DATEN UNVOLLSTÄNDIG"}
    result = presentation({"verdict": verdict, "live_equivalent": False})
    assert result["verdict"] == verdict


def test_methodology_is_tied_to_result_not_current_dropdown_and_filters_non_text():
    result = presentation({"methodology_label": "Daily proxy v1",
                           "methodology_warnings": ["Missing 5m", {}, None, 1]})
    assert result["label"] == "Daily proxy v1"
    assert result["warnings"] == ["Missing 5m"]


def test_unknown_metadata_and_no_result_remain_unknown_not_live():
    result = presentation(None)
    assert result["verdict"] is None
    assert result["modelLimited"] is True
    assert "nicht nachgewiesen" in result["label"]


def test_guides_describe_confirmed_breakout_and_turtle_variant():
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    assert "Reine Range-Stärke oder die Rückeroberung einer Durchschnittslinie reichen nicht" in source
    assert "Turtle-inspirierte Variante" in source
    assert "Der Scanner bildet das historische Regelwerk ab" not in source
    assert 'data-testid="backtest-methodology"' in source


def test_metric_cards_never_replace_missing_observations_with_zero():
    source = (ROOT / "frontend/index.html").read_text(encoding="utf-8")
    for metric in ("win_rate", "avg_pnl", "total_return", "max_drawdown", "best_trade",
                   "worst_trade", "avg_win", "avg_loss", "profit_factor", "no_fill"):
        assert f"results.{metric} ?? 0" not in source
