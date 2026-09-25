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
    assert "if (scannerCandidatePresentation(row)) return <ScannerCandidateStatus" in warning
    assert "retest={evidence}" in warning
    assert "if (!evidence) return null;" in warning
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


def _candidate_row(*codes, **extra):
    return {
        "visibility_status": "candidate_warning",
        "visibility_is_trade_signal": False,
        "direction": "LONG",
        "visibility_warnings": [{"code": code, "label": f"Technical detail: {code}"} for code in codes],
        **extra,
    }


def compact_candidate(row):
    return run(f"scannerCandidateCompactPresentation({json.dumps(row)})")


@pytest.mark.parametrize("code,expected", [
    ("market_data_invalid", "Kursdaten prüfen"),
    ("display_metadata_invalid", "Kursdaten prüfen"),
    ("trigger_not_current", "Einstiegssignal nicht mehr aktuell"),
    ("crossed_resistance_unconfirmed", "Schlusskursbestätigung fehlt"),
    ("crossed_support_unconfirmed", "Schlusskursbestätigung fehlt"),
    ("near_structural_barrier", "Widerstand nah"),
    ("first_opposing_barrier_before_minimum_rr", "Widerstand nah"),
    ("no_structural_invalidation", "Handelsplan noch nicht bestätigt"),
    ("native_trade_levels_missing", "Handelsplan noch nicht bestätigt"),
    ("trade_rr_below_threshold", "Zu wenig Platz bis zum Kursziel"),
    ("WAIT_FOR_TRIGGER", "Einstiegsbestätigung fehlt"),
    ("WAIT_FOR_CONTINUATION", "Fortsetzung noch offen"),
    ("WAIT_FOR_RETEST", "Rücktest offen"),
])
def test_compact_candidate_maps_codes_to_one_plain_language_warning(code, expected):
    assert compact_candidate(_candidate_row(code)) == {
        "label": "Einstieg nicht freigegeben", "message": expected,
    }


def test_compact_candidate_has_one_primary_warning_plus_optional_retest():
    row = _candidate_row("plan_unavailable", "near_structural_barrier", "trade_rr_below_threshold",
                         "breakout_confirmed_retest_pending", "unknown_internal_gate")
    view = compact_candidate(row)
    assert view["message"] == "Widerstand nah · Rücktest offen"
    assert "Technical" not in json.dumps(view)
    assert "unknown_internal_gate" not in json.dumps(view)
    assert len(view["message"].split(" · ")) == 2


def test_compact_candidate_never_invents_breakout_confirmation_from_pending_retest():
    for code in ("WAIT_FOR_RETEST", "breakout_confirmed_retest_pending"):
        view = compact_candidate(_candidate_row(code))
        assert view["message"] == "Rücktest offen"
        assert "bestätigt" not in view["message"]
    row = _candidate_row("crossed_resistance_unconfirmed", "breakout_confirmed_retest_pending")
    assert compact_candidate(row)["message"] == "Schlusskursbestätigung fehlt"


@pytest.mark.parametrize("price", [None, True, False, "0", "123.45", 0, -1])
def test_compact_candidate_does_not_coerce_malformed_or_nonpositive_barrier_price(price):
    row = _candidate_row("near_structural_barrier")
    row["visibility_warnings"][0]["price"] = price
    assert compact_candidate(row)["message"] == "Widerstand nah"


def test_compact_barrier_price_cannot_come_from_a_discarded_malformed_warning():
    row = _candidate_row("near_structural_barrier")
    row["visibility_warnings"].insert(0, {"code": "near_structural_barrier", "price": 777})
    assert compact_candidate(row)["message"] == "Widerstand nah"


def test_compact_short_uses_support_without_changing_release_contract():
    row = _candidate_row("near_underlying_support", direction="SHORT")
    row["visibility_warnings"][0]["price"] = 123.45
    assert compact_candidate(row)["message"] == "Unterstützung bei 123.45 nah"
    row.update(visibility_status="released", visibility_is_trade_signal=True)
    assert compact_candidate(row)["label"] == "Signal freigegeben"
    row["visibility_is_trade_signal"] = False
    assert compact_candidate(row)["label"] == "Einstieg nicht freigegeben"
    row.update(visibility_status="context", visibility_is_trade_signal=True)
    assert compact_candidate(row)["label"] == "Marktkontext · kein Signal"
    assert compact_candidate({"score": 100}) is None


def render_candidate(row, compact=True):
    """Evaluate actual JSX with a tiny React tree; no browser, API, or I/O."""
    component = SOURCE[SOURCE.index("function ScannerCandidateStatus("):SOURCE.index("function StockIdentity(")]
    babel = Path(__file__).resolve().parent / "frontend/vendor/babel.min.js"
    return json.loads(node_run("""
const babel=require(""" + json.dumps(str(babel)) + """);
const source=babel.transform(""" + json.dumps(component) + """,{presets:['react'],sourceType:'script'}).code;
const React={createElement:(type,props,...children)=>typeof type==='function'
 ? type({...props,children}) : ({type,props:props||{},children})};
const render=new Function('React',""" + json.dumps(PURE) + """+source+';return BreakoutRetestWarning;')(React);
function text(n,visible=true){
 if(Array.isArray(n))return n.map(v=>text(v,visible)).join(' ');
 if(n==null||typeof n==='boolean')return '';
 if(typeof n!=='object')return String(n);
 if(visible&&n.type==='details'&&!n.props.open)return text(n.children.filter(c=>c?.type==='summary'),visible);
 return text(n.children,visible);
}
function nodes(n){return Array.isArray(n)?n.flatMap(nodes):!n||typeof n!=='object'?[]:[n,...nodes(n.children)];}
const tree=render({row:""" + json.dumps(row) + ",compact:" + json.dumps(compact) + """});
const all=nodes(tree),details=all.filter(n=>n.type==='details');let stopped=0;
details.forEach(n=>n.props.onClick?.({stopPropagation:()=>stopped++}));
console.log(JSON.stringify({visible:text(tree),full:text(tree,false),
 statusPanels:all.filter(n=>n.props['data-testid']==='scanner-candidate-status').length,
 retestPanels:all.filter(n=>n.props['data-testid']==='breakout-retest-warning').length,
 statuses:all.filter(n=>n.props['data-visibility-status']).map(n=>n.props['data-visibility-status']),
 warningCodes:all.map(n=>n.props['data-warning-code']).filter(Boolean),
 details:details.map(n=>({open:!!n.props.open,testid:n.props['data-testid']||null})),
 stopped,detailCount:details.length}));
"""))


@pytest.mark.parametrize("compact", [True, False])
def test_candidate_details_start_closed_keep_exact_diagnostics_and_do_not_bubble(compact):
    row = _candidate_row("near_structural_barrier", "unknown_internal_gate")
    row["visibility_warnings"][0].update(price=120.5, distance_pct=0, distance_r=0, timeframe="1D")
    row["visibility_warnings"][1]["label"] = "Technical diagnostic retained only inside details"
    row["mail_check"] = {
        "schema_version": 1, "semantics": "read_only_precheck_not_delivery_or_send_permission",
        "status": "blocked", "trade_score": 67, "minimum_trade_score": 80, "setup_score": 94,
        "reasons": [{"code": "score_below_alert_threshold", "label": "Mail score below threshold"}],
    }
    row["momentum_quality"] = {
        "schema_version": 1, "timeframe": "1D", "evidence": "completed_daily", "score": 70,
        "maximum_score": 96, "mail_min_score": 78,
        "semantics": "daily_bar_quality_not_future_continuation_or_retest",
        "components": [{"label": "Volumen", "points": 14, "max_points": 22}], "deductions": [],
    }
    tree = render_candidate(row, compact)
    assert "Einstieg nicht freigegeben" in tree["visible"]
    assert "Widerstand bei 120.5 nah" in tree["visible"]
    assert "Details" in tree["visible"] and len(tree["visible"]) < 150
    for phrase in ("Technical diagnostic", "Mail-/Handelsplan-Score", "67", "94",
                   "0.00R", "0.00%", "kein Zustellnachweis"):
        assert phrase not in tree["visible"]
        assert phrase in tree["full"]
    assert "70/96" not in tree["visible"].replace(" ", "")
    assert "70/96" in tree["full"].replace(" ", "")
    assert tree["statusPanels"] == 1 and tree["retestPanels"] == 0
    assert tree["statuses"] == ["candidate_warning"]
    assert tree["details"] and not any(item["open"] for item in tree["details"])
    assert tree["stopped"] == tree["detailCount"]


def test_unknown_warning_is_retained_in_details_not_promoted_to_summary():
    tree = render_candidate(_candidate_row("unknown_internal_gate"))
    assert "Einstieg nicht freigegeben" in tree["visible"]
    assert "unknown_internal_gate" not in tree["visible"]
    assert "unknown_internal_gate" in tree["full"]


@pytest.mark.parametrize("released", [False, True])
def test_retest_warning_shares_one_candidate_panel_without_changing_release(released):
    from test_frontend_breakout_retest_warning import confirmed_warning

    row = _candidate_row("breakout_confirmed_without_retest", **confirmed_warning())
    row.update(visibility_status="released" if released else "candidate_warning",
               visibility_is_trade_signal=released)
    tree = render_candidate(row)
    assert tree["statusPanels"] == 1 and tree["retestPanels"] == 0
    assert tree["visible"].count("Rücktest offen") == 1
    assert tree["warningCodes"].count("breakout_confirmed_without_retest") == 1
    assert tree["statuses"] == ["released" if released else "candidate_warning"]
    assert ("Signal freigegeben" if released else "Einstieg nicht freigegeben") in tree["visible"]


def test_retest_without_visibility_keeps_only_evidence_backed_fallback_panel():
    from test_frontend_breakout_retest_warning import confirmed_warning

    row = confirmed_warning("SHORT")
    tree = render_candidate(row, compact=False)
    assert tree["statusPanels"] == 0 and tree["retestPanels"] == 1
    assert "Breakdown bestätigt · Rücktest offen" in tree["visible"]
    assert row["retest_warning"] not in tree["visible"]
    assert row["retest_warning"] in tree["full"]
    assert tree["stopped"] == tree["detailCount"] == 1
    row.pop("breakout_confirmation")
    assert render_candidate(row)["visible"] == ""


@pytest.mark.parametrize("code", ["retest_not_confirmed", "crossed_resistance_unconfirmed",
                                  "crossed_support_unconfirmed"])
def test_neutralized_backend_warning_is_not_reinserted_from_stale_raw_evidence(code):
    from test_frontend_breakout_retest_warning import confirmed_warning

    row = _candidate_row(code, **confirmed_warning())
    row["visibility_warnings"][0]["label"] = "Current unconfirmed structural evidence"
    tree = render_candidate(row)
    assert tree["statusPanels"] == 1 and tree["retestPanels"] == 0
    assert row["retest_warning"] not in tree["visible"]
    assert row["retest_warning"] not in tree["full"]
    assert "breakout_confirmed_without_retest" not in tree["warningCodes"]
    assert "Current unconfirmed structural evidence" in tree["full"]
