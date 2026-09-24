"""Execute display-only candidate/reminder contracts without API or network."""
import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import evaluate, node_run

SOURCE = (Path(__file__).resolve().parent / "frontend/index.html").read_text(encoding="utf-8")
PURE = SOURCE[SOURCE.index("function scannerCandidatePresentation("):SOURCE.index("function ScannerVisibilitySummary(")]
NOTIFY = SOURCE[SOURCE.index("function tradeReminderNotification("):SOURCE.index("function TradeReminderPoller(")]


def run(expression):
    return json.loads(node_run(PURE + NOTIFY + "\nconsole.log(JSON.stringify(" + expression + "));"))


def test_score_or_action_cannot_manufacture_display_release():
    assert run('scannerCandidatePresentation({score:100,trade_action:"LONG_NOW"})') is None
    row = {"visibility_status": "candidate_warning", "visibility_is_trade_signal": False,
           "score": 100, "grade": "S", "trade_action": "LONG_NOW"}
    assert run(f"scannerCandidatePresentation({json.dumps(row)})")["status"] == "candidate_warning"
    row["visibility_status"] = "released"
    assert run(f"scannerCandidatePresentation({json.dumps(row)})")["status"] == "candidate_warning"
    row["visibility_is_trade_signal"] = True
    assert run(f"scannerCandidatePresentation({json.dumps(row)})")["status"] == "released"


def test_warning_preserves_real_zero_distance_without_numeric_coercion():
    row = {"visibility_status": "candidate_warning", "visibility_warnings": [
        {"code": "near_barrier", "label": "Widerstand nahe", "price": 120.5,
         "distance_pct": 0, "distance_r": 0, "timeframe": "1D"},
        {"code": "missing", "label": "Fehlender Abstand", "price": None,
         "distance_r": "0", "distance_pct": False, "timeframe": "SECRET"},
        None, "wrong", {"code": "empty", "label": ""},
    ]}
    view = run(f"scannerCandidatePresentation({json.dumps(row)})")
    assert len(view["warnings"]) == 2
    assert "0.00%" in view["warnings"][0]["detail"]
    assert "0.00R" in view["warnings"][0]["detail"]
    assert view["warnings"][1]["detail"] == ""


def test_context_never_appears_as_release_even_with_true_boolean():
    view = run('scannerCandidatePresentation({visibility_status:"context",visibility_is_trade_signal:true})')
    assert view["status"] == "context"
    assert "kein Handelssignal" in view["label"]


def test_specialized_summary_counts_only_real_display_rows_and_keeps_context_separate():
    rows = [{"visibility_status": "released", "visibility_is_trade_signal": True},
            {"visibility_status": "candidate_warning", "score": 100},
            {"visibility_status": "context"}, {"trade_action": "LONG_NOW"}]
    assert run(f"scannerVisibleRowsSummary({json.dumps(rows)})") == {
        "released": 1, "candidate_warning": 1, "context": 1, "unclassified": 1, "total": 4}
    assert run('scannerVisibleRowsSummary([{score:100}])') is None
    assert run('scannerVisibleRowsSummary([])') is None
    assert SOURCE.count("<ScannerVisibilitySummary rows=") == 9


@pytest.mark.parametrize("field,value", [("data_invalid", True), ("partial_data", True),
                                         ("data_partial", True), ("isCrypto", True),
                                         ("visibility_status", "context"), ("direction", "bad")])
def test_bad_data_never_offers_daily_structure_reminder(field, value):
    row = {"stock_swing_mode": "completed_daily_swing", "direction": "LONG", "level_structure": {"zones": []}}
    row[field] = value
    assert run(f"stockStructureReminderSource({json.dumps(row)})") is None


def test_daily_structure_reminder_needs_no_fabricated_entry_stop_or_trade_permission():
    row = {"stock_swing_mode": "completed_daily_swing", "direction": "SHORT",
           "level_structure": {"zones": []}, "trade_action": "NO_TRADE", "native_plan_status": "unavailable"}
    result = run(f"stockStructureReminderSource({json.dumps(row)})")
    assert result == {"mode": "structure_1d", "direction": "SHORT", "timeframe": "1D"}
    row.pop("level_structure")
    assert run(f"stockStructureReminderSource({json.dumps(row)})") is None


def test_daily_source_uses_native_structure_not_selected_chart_timeframe():
    row = {"direction": "LONG", "chart_timeframe": "5m", "level_structure": {
        "asset_class": "stock", "horizon": "swing", "completed_bar_counts": {"1D": 160}}}
    assert run(f"stockStructureReminderSource({json.dumps(row)})")["timeframe"] == "1D"
    row["level_structure"]["asset_class"] = "crypto"
    assert run(f"stockStructureReminderSource({json.dumps(row)})") is None


def test_structure_notification_is_not_new_trade_signal():
    result = run('tradeReminderNotification({mode:"structure_1d",condition:"retest",ticker:"ACME"})')
    assert "1D-Ruecktest bestaetigt" in result["title"]
    assert "Kein neues Handelssignal" in result["body"]
    assert "Trigger bereit" not in result["title"]


def _early_mover_reminder_row():
    return {"scanner": "early_movers", "direction": "LONG", "trade_action": "WAIT_FOR_RETEST",
            "PerpChartSymbol": "ACMEUSDT", "PerpChartExchange": "binance",
            "entry": 100, "stop_loss": 95, "tp1": 110}


@pytest.mark.parametrize("condition", ["trigger", "retest", "trigger_or_retest"])
def test_existing_early_mover_long_execution_reminders_remain_supported(condition):
    row = _early_mover_reminder_row()
    assert run(f"cryptoExecutionReminderCapability({json.dumps(row)},{json.dumps(condition)})")["supported"] is True


@pytest.mark.parametrize("patch,condition", [
    ({"direction": "SHORT"}, "retest"),
    ({"trade_action": "SHORT_NOW"}, "trigger"),
    ({"trade_setup": {"direction": "SHORT"}}, "trigger"),
    ({}, "continuation"),
    ({"trade_action": "WAIT_FOR_CONTINUATION"}, "retest"),
    ({"scanner": "new_listing"}, "trigger"),
    ({"scanner": "crypto_explosion"}, "trigger"),
    ({"scanner": ""}, "trigger"),
    ({"PerpChartSymbol": None}, "trigger"),
    ({"stop_loss": 105}, "trigger"),
    ({"tp1": None}, "trigger"),
    ({"partial_data": True}, "trigger"),
])
def test_unsupported_crypto_reminder_modes_are_explicitly_not_offered(patch, condition):
    row = {**_early_mover_reminder_row(), **patch}
    capability = run(f"cryptoExecutionReminderCapability({json.dumps(row)},{json.dumps(condition)})")
    assert capability["supported"] is False
    assert capability["reason"]
    assert "(!isCrypto || cryptoReminderCapability.supported)" in SOURCE
    assert 'data-testid="reminder-unsupported"' in SOURCE
    assert "...item, scanner: 'early_movers'" in SOURCE


def test_visibility_summary_distinguishes_candidates_from_release():
    payload = {"info": {"cached_at": "2026-09-24T12:00:00Z", "diagnostics": {"coverage": "complete"},
                        "data_quality": {"visibility_counts": {"released": 0, "candidate_warning": 10,
                        "context": 0, "total": 10, "warning_count": 10}}}, "hasLoaded": True, "count": 10}
    result = evaluate(f"scannerEvidenceState({json.dumps(payload)})")
    assert result["tone"] == "candidates"
    assert "0 freigegeben" in result["text"]
    assert "10 Kandidaten mit Warnungen" in result["text"]
    payload["info"]["data_quality"]["visibility_counts"]["released"] = 9
    assert evaluate(f"scannerVisibilityCounts({json.dumps(payload['info'])})") is None


def test_real_snapshot_data_flow_keeps_visibility_counts_until_evidence_rendering():
    payload = {"cached_at": "2026-09-24T12:00:00Z", "diagnostics": {"coverage": "complete"},
               "data": [{"visibility_status": "candidate_warning"}] * 2,
               "data_quality": {"visibility_counts": {"released": 0, "candidate_warning": 2,
                                 "context": 0, "total": 2, "warning_count": 2}}}
    result = evaluate(f"scannerEvidenceState({{info:scannerSnapshotInfo({json.dumps(payload)}),hasLoaded:true,count:2}})")
    assert result["tone"] == "candidates"
    assert "0 freigegeben" in result["text"]
    assert "2 Kandidaten mit Warnungen" in result["text"]
    assert "2 gepruefte Signale" not in result["text"]
    assert "Scan abgeschlossen: ${data.data.length} Signale" not in SOURCE


@pytest.mark.parametrize("malformed", [None, [], "secret", 7, False])
def test_snapshot_rejects_malformed_quality_payload(malformed):
    assert evaluate(f"scannerSnapshotInfo({{data_quality:{json.dumps(malformed)}}})")["data_quality"] is None


def test_shared_status_reaches_all_existing_stock_crypto_and_detail_warning_surfaces():
    warning = SOURCE[SOURCE.index("function BreakoutRetestWarning("):SOURCE.index("function StockIdentity(")]
    assert warning.count("<ScannerCandidateStatus row={row} compact={compact} />") == 2
    for start, end in [("function ScannerTab(", "function BIScannerTab("),
                       ("function BIScannerTab(", "function BiotechTab("),
                       ("function NewListingTab(", "function observedMetricNumber(")]:
        area = SOURCE[SOURCE.index(start):SOURCE.index(end)]
        assert "StockIdentity row={item}" in area or "BreakoutRetestWarning row={item}" in area
    assert 'data-visibility-status={view.status}' in SOURCE
    assert ':has([data-visibility-status="candidate_warning"]) .grade-badge' in SOURCE


def test_personal_reminder_buttons_are_opt_in_long_lived_and_not_hidden_by_missing_trade_plan():
    assert "const reminderEligible = isStructureReminder || Boolean(" in SOURCE
    assert "{ reminder_mode: 'structure_1d' }" in SOURCE
    for duration in ("{ hours: 168, label: '7 Tage' }", "{ hours: 336, label: '14 Tage' }", "{ hours: 720, label: '30 Tage' }"):
        assert duration in SOURCE
    assert "!activeTradeSetup && !isCrypto && (reminderEligible || activeReminder)" in SOURCE
    assert "onClick={() => createTradeReminder(option.hours, 'email_browser')}" in SOURCE
    assert "if (nativePlanUnavailable) return null;" in SOURCE
    assert "const finalTradeable = !nativePlanUnavailable" in SOURCE


def test_penny_defaults_to_candidates_and_orb_main_view_includes_warnings():
    assert "const [showWatchRows, setShowWatchRows] = useState(true);" in SOURCE
    assert "['breakouts', 'rejected'].includes(subView)" in SOURCE
    assert "['breakouts', 'candidates'].includes(subView)" in SOURCE
