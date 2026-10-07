"""The compact card names its cached admission blocker, not a retest warning."""
import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import node_run


SOURCE = (Path(__file__).resolve().parent / "frontend/index.html").read_text(encoding="utf-8")
PURE = SOURCE[SOURCE.index("function scannerCandidatePresentation("):SOURCE.index("function ScannerVisibilitySummary(")]


def compact(row):
    expression = "scannerCandidateCompactPresentation(" + json.dumps(row) + ")"
    return json.loads(node_run(PURE + "\nconsole.log(JSON.stringify(" + expression + "));"))


@pytest.mark.parametrize("code,message", [
    ("momentum_mail_blocked_daily_quality_below_threshold", "Tagesqualität reicht nicht aus"),
    ("momentum_mail_blocked_breakout_quality_low", "Tagesqualität reicht nicht aus"),
    ("momentum_mail_blocked_daily_quality_unavailable", "Tagesqualität nicht verfügbar"),
    ("momentum_mail_blocked_daily_quality_unconfirmed", "Tagesqualität noch nicht bestätigt"),
    ("momentum_mail_blocked_daily_target_previously_touched", "Tageshoch bereits nahe am Kursziel"),
    ("momentum_mail_blocked_thin_baseline_liquidity", "Grundliquidität zu niedrig"),
    ("stock_swing_mail_blocked_low_volatility_budget", "Schwankungsbreite zu gering"),
    ("swing_daily_reference_invalid_or_stale", "Scannerstand nicht mehr aktuell"),
    ("swing_reference_price_mismatch", "Kursdaten prüfen"),
    ("momentum_mail_blocked_fakeout_risk", "Qualitätsprüfung nicht erfüllt"),
    ("stock_swing_short_mail_blocked_missing_4h_state", "Qualitätsprüfung nicht erfüllt"),
])
def test_compact_card_explains_selection_blocker_with_retest_as_secondary_warning(code, message):
    row = {
        "visibility_status": "candidate_warning", "visibility_is_trade_signal": False,
        "visibility_warnings": [
            {"code": code, "label": "Ausführlicher serverseitiger Prüfgrund"},
            {"code": "breakout_confirmed_without_retest", "label": "Ausbruch bestätigt; Rücktest offen"},
        ],
    }
    result = compact(row)
    assert result["label"] == "Einstieg nicht freigegeben"
    assert result["message"] == message + " · Rücktest offen"


def test_bad_data_still_takes_precedence_over_selection_quality():
    row = {"visibility_status": "candidate_warning", "visibility_warnings": [
        {"code": "momentum_mail_blocked_daily_quality_below_threshold", "label": "Qualität zu niedrig"},
        {"code": "market_data_invalid", "label": "Kursdaten ungültig"},
    ]}
    assert compact(row)["message"] == "Kursdaten prüfen"


def test_valid_release_with_pending_retest_remains_a_release():
    row = {"visibility_status": "released", "visibility_is_trade_signal": True,
           "visibility_warnings": [{"code": "breakout_confirmed_without_retest", "label": "Rücktest offen"}]}
    assert compact(row) == {"label": "Im Scan freigegeben", "message": "Rücktest offen"}


@pytest.mark.parametrize("code,message", [
    ("momentum_mail_blocked_daily_quality_below_threshold", "Tagesqualität reicht nicht aus"),
    ("swing_reference_price_mismatch", "Kursdaten prüfen"),
])
def test_summary_does_not_lose_a_blocker_after_the_detail_display_limit(code, message):
    row = {"visibility_status": "candidate_warning", "visibility_warnings": [
        {"code": "breakout_confirmed_without_retest", "label": "Rücktest offen"},
        *[{"code": f"generic_warning_{i}", "label": "Weiterer Prüfgrund"} for i in range(11)],
        {"code": code, "label": "Relevante Auswahlblockade"},
    ]}
    assert compact(row)["message"] == message + " · Rücktest offen"
