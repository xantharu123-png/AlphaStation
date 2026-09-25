import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import node_run


ROOT = Path(__file__).resolve().parent
SOURCE = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
WARNING_HELPER = SOURCE[
    SOURCE.index("const BREAKOUT_WITHOUT_RETEST_CODE"):
    SOURCE.index("function BreakoutRetestWarning(")
]
WYCKOFF_HELPERS = SOURCE[
    SOURCE.index("function isWyckoffPattern("):
    SOURCE.index("function usePublicPlans(")
]


def evaluate_warning(row):
    script = WARNING_HELPER + "\nconsole.log(JSON.stringify(breakoutRetestWarningEvidence(" + json.dumps(row) + ")));"
    return json.loads(node_run(script))


def wyckoff_state(trigger_mode, include_retest=False):
    roles = {
        "origin": "origin-1",
        "reaction": "reaction-1",
        "test": "test-1",
        "breakout": "breakout-1",
    }
    if include_retest:
        roles["retest"] = "retest-1"
    events = [{"event_id": event_id} for event_id in roles.values()]
    pattern = {
        "model": "causal_wyckoff_v3",
        "structure_state": "confirmed",
        "entry_state": "ready",
        "trade_ready": True,
        "event_evidence": events,
        "entry_trigger": {
            "trigger_mode": trigger_mode,
            "trigger_id": "trigger-1",
            "confirmed_at": "2026-09-23T14:30:00Z",
            "event_ids": roles,
        },
    }
    script = WYCKOFF_HELPERS + "\nconsole.log(JSON.stringify(wyckoffStates(" + json.dumps(pattern) + ")));"
    return json.loads(node_run(script))


def confirmed_warning(direction="LONG"):
    return {
        "breakout_confirmation": "confirmed_close",
        "retest_status": "not_confirmed",
        "retest_warning": "Ausbruch bestaetigt; Ruecktest noch nicht bestaetigt.",
        "warning_codes": ["breakout_confirmed_without_retest"],
        "direction": direction,
    }


def test_warning_requires_explicit_confirmation_status_code_and_message():
    evidence = evaluate_warning(confirmed_warning())
    assert evidence == {
        "code": "breakout_confirmed_without_retest",
        "warning": "Ausbruch bestaetigt; Ruecktest noch nicht bestaetigt.",
        "direction": "LONG",
    }

    for missing in (
        "breakout_confirmation",
        "retest_status",
        "retest_warning",
        "warning_codes",
    ):
        row = confirmed_warning()
        row.pop(missing)
        assert evaluate_warning(row) is None

    assert evaluate_warning({"retest_warning": confirmed_warning()["retest_warning"]}) is None


def test_warning_accepts_complete_nested_trade_setup_but_never_merges_partial_sources():
    assert evaluate_warning({"trade_setup": confirmed_warning("SHORT")})["direction"] == "SHORT"

    split = {
        "breakout_confirmation": "confirmed_close",
        "retest_status": "not_confirmed",
        "trade_setup": {
            "retest_warning": confirmed_warning()["retest_warning"],
            "warning_codes": ["breakout_confirmed_without_retest"],
        },
    }
    assert evaluate_warning(split) is None
    assert evaluate_warning({"trade_setup": None}) is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("code", ["retest_not_confirmed", "crossed_resistance_unconfirmed",
                                  "crossed_support_unconfirmed", "trigger_not_current",
                                  "market_data_invalid", "display_metadata_invalid",
                                  "breakout_evidence_conflict", "breakout_failed", "breakout_invalidated"])
def test_current_visibility_contradiction_suppresses_old_raw_confirmed_marker(direction, code):
    row = {**confirmed_warning(direction), "visibility_status": "candidate_warning",
           "visibility_warnings": [{"code": code, "label": "Current diagnostic"}]}
    assert evaluate_warning(row) is None
    assert evaluate_warning({"trade_setup": confirmed_warning(direction),
                             "visibility_status": row["visibility_status"],
                             "visibility_warnings": row["visibility_warnings"]}) is None


@pytest.mark.parametrize("status", ["released", "candidate_warning"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_valid_optional_retest_warning_survives_independent_plan_or_target_risk(status, direction):
    row = {**confirmed_warning(direction), "visibility_status": status,
           "visibility_warnings": [{"code": "near_structural_barrier", "label": "Next barrier near"}]}
    assert evaluate_warning(row)["direction"] == direction


@pytest.mark.parametrize("completed", [{"retest_status": "confirmed"}, {"retest_confirmed": True}])
@pytest.mark.parametrize("reverse", [False, True])
def test_completed_retest_overrides_stale_pending_copy_for_warning_only(completed, reverse):
    row = {**(completed if reverse else confirmed_warning()),
           "trade_setup": confirmed_warning() if reverse else completed}
    assert evaluate_warning(row) is None


def test_shared_warning_is_present_in_stock_crypto_sidebar_and_chart_surfaces():
    stock_identity = SOURCE[SOURCE.index("function StockIdentity("):SOURCE.index("function vwapDisplayLabel(")]
    detail_sidebar = SOURCE[SOURCE.index("function DetailSidebar("):SOURCE.index("// Main App")]
    chart_tab = SOURCE[SOURCE.index("function ChartAnalyseTab("):SOURCE.index("function AdminTab(")]

    assert "<BreakoutRetestWarning row={row} compact />" in stock_identity
    assert "<BreakoutRetestWarning row={scannerData} />" in detail_sidebar
    assert "<BreakoutRetestWarning row={retestWarningRow} />" in detail_sidebar
    assert "<BreakoutRetestWarning row={d} />" in chart_tab
    assert SOURCE.count("<BreakoutRetestWarning row={item} compact />") >= 5
    assert 'data-warning-code={evidence.code}' in SOURCE
    assert 'role="note"' in SOURCE


def test_confirmed_breakout_uses_four_real_roles_without_inventing_lps_or_retest():
    state = wyckoff_state("confirmed_breakout", include_retest=False)
    assert state["ready"] is True
    assert state["requiredTriggerRoles"] == ["origin", "reaction", "test", "breakout"]
    assert "Ein LPS/LPSY- oder Retest-Ereignis wird nicht erfunden." in SOURCE


def test_confirmed_retest_still_requires_the_fifth_retest_role():
    assert wyckoff_state("confirmed_retest", include_retest=False)["ready"] is False
    state = wyckoff_state("confirmed_retest", include_retest=True)
    assert state["ready"] is True
    assert state["requiredTriggerRoles"] == ["origin", "reaction", "test", "breakout", "retest"]
