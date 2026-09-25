"""Dependency-light regression tests for presentation-only breakout evidence."""
from copy import deepcopy

import pytest

from modules import scanner_visibility as visibility
from modules.breakout_warnings import (
    BREAKOUT_WITHOUT_RETEST_CODE,
    BREAKOUT_WITHOUT_RETEST_WARNING,
    breakout_warning_fields,
)


CONFIRMED = "breakout_confirmed_retest_pending"
NEUTRAL = "retest_not_confirmed"


def bundle(direction="LONG"):
    return {"direction": direction, **breakout_warning_fields(confirmed_close=True)}


def codes(row, **kwargs):
    return [warning["code"] for warning in visibility.present(row, **kwargs)["visibility_warnings"]]


@pytest.mark.parametrize("direction,reason", [
    ("LONG", "crossed_resistance_unconfirmed"), ("SHORT", "crossed_support_unconfirmed"),
])
@pytest.mark.parametrize("nested", [False, True])
def test_missing_retest_never_confirms_an_unconfirmed_directional_break(direction, reason, nested):
    source = {"direction": direction, "native_plan_reason": reason}
    if nested:
        source["trade_setup"] = {"retest_status": "not_confirmed"}
    else:
        source["retest_status"] = "not_confirmed"
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "candidate_warning"
    assert not actual["visibility_is_trade_signal"]
    assert reason in codes(source) and NEUTRAL in codes(source)
    assert CONFIRMED not in codes(source)
    assert visibility.LABELS[CONFIRMED] not in [w["label"] for w in actual["visibility_warnings"]]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("nested", [False, True])
def test_complete_same_container_producer_bundle_keeps_warning_without_blocking_release(direction, nested):
    source = {"direction": direction, "trade_setup": bundle(direction)} if nested else bundle(direction)
    source.update(score=99, grade="S", trade_action=f"{direction}_NOW", trade_decision="TRADEABLE")
    before = deepcopy(source)
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "released" and actual["visibility_is_trade_signal"]
    assert codes(source, released=True).count(CONFIRMED) == 1
    assert NEUTRAL not in codes(source)
    assert source == before
    assert {key: actual[key] for key in before} == before


@pytest.mark.parametrize("field", ["breakout_confirmation", "warning_codes", "retest_warning"])
@pytest.mark.parametrize("nested", [False, True])
def test_incomplete_producer_bundle_cannot_create_confirmation(field, nested):
    value = bundle()
    value.pop(field)
    source = {"trade_setup": value} if nested else value
    assert CONFIRMED not in codes(source)
    assert NEUTRAL in codes(source)


@pytest.mark.parametrize("field,value", [
    ("breakout_confirmation", True), ("breakout_confirmation", "pending"),
    ("warning_codes", BREAKOUT_WITHOUT_RETEST_CODE), ("warning_codes", None),
    ("warning_codes", {BREAKOUT_WITHOUT_RETEST_CODE: True}),
    ("retest_warning", "  "), ("retest_warning", 42),
])
def test_malformed_producer_evidence_does_not_manufacture_a_confirmed_break(field, value):
    source = bundle()
    source[field] = value
    assert CONFIRMED not in codes(source)
    assert NEUTRAL in codes(source)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("reverse", [False, True])
def test_split_row_and_plan_fields_are_never_combined_into_proof(direction, reverse):
    proof = bundle(direction)
    first = {key: value for key, value in proof.items() if key in {"breakout_confirmation", "retest_status"}}
    second = {key: value for key, value in proof.items() if key not in first}
    source = {**(second if reverse else first), "trade_setup": first if reverse else second}
    assert CONFIRMED not in codes(source)
    assert NEUTRAL in codes(source)


@pytest.mark.parametrize("patch", [
    {"native_plan_reason": "crossed_resistance_unconfirmed"},
    {"native_plan_diagnostics": {"reason": "crossed_support_unconfirmed"}},
    {"scanner_suppression_reasons": ["breakout_failed"]},
    {"risk_flags": ["orb_current_breakout_lost"]},
    {"failed_breakout": True}, {"breakout_failed": True}, {"breakout_invalidated": True},
    {"break_state": "invalidated"}, {"structure_state": "invalidated"},
    {"entry_state": "expired"}, {"entry_trigger": {"state": "expired"}},
    {"trigger_expired": True}, {"trigger_expiry_reason": "trigger_not_current"},
    {"breakout_confirmation": "failed"}, {"breakout_confirmation": "unconfirmed"},
])
def test_failed_invalidated_or_unconfirmed_break_overrides_old_complete_nested_bundle(patch):
    source = {"direction": "LONG", "trade_setup": bundle(), **patch}
    assert CONFIRMED not in codes(source)
    assert NEUTRAL in codes(source)


@pytest.mark.parametrize("patch", [
    {"direction": "SHORT"}, {"BI_Direction": "SHORT"}, {"Direction": "SHORT"},
    {"direction": "UNKNOWN"}, {"breakout_confirmation": "invalidated"},
    {"native_plan_reason": "crossed_resistance_unconfirmed"},
    {"native_plan_diagnostics": {"reason": "crossed_support_unconfirmed"}},
    {"structure_reason": "breakout_failed"}, {"risk_flags": ["orb_current_breakout_lost"]},
    {"break_state": "intact"}, {"break_state": "pending"},
])
def test_conflicting_nested_evidence_cannot_be_bypassed_by_complete_top_level_bundle(patch):
    assert CONFIRMED not in codes({**bundle(), "trade_setup": patch})


@pytest.mark.parametrize("confirmed", [{"retest_status": "confirmed"}, {"retest_confirmed": True}])
@pytest.mark.parametrize("reverse", [False, True])
def test_completed_retest_does_not_remain_pending_due_to_stale_other_container(confirmed, reverse):
    source = {**(confirmed if reverse else bundle()), "trade_setup": bundle() if reverse else confirmed}
    assert not {CONFIRMED, NEUTRAL}.intersection(codes(source))


@pytest.mark.parametrize("field", ["risk_flags", "scanner_suppression_reasons", "exclusion_reasons"])
def test_stale_owned_warning_copies_cannot_bypass_bundle_validation(field):
    source = {"retest_status": "not_confirmed", field: [CONFIRMED, BREAKOUT_WITHOUT_RETEST_CODE,
              BREAKOUT_WITHOUT_RETEST_WARNING, "unrelated_risk"], "_quality": {
                  "warnings": [BREAKOUT_WITHOUT_RETEST_WARNING, "Keep this independent warning"]}}
    actual = visibility.present(source)
    warning_codes = [w["code"] for w in actual["visibility_warnings"]]
    assert CONFIRMED not in warning_codes and BREAKOUT_WITHOUT_RETEST_CODE not in warning_codes
    assert "unrelated_risk" in warning_codes and NEUTRAL in warning_codes
    assert any(w["label"] == "Keep this independent warning" for w in actual["visibility_warnings"])
    assert not any(w["label"] == BREAKOUT_WITHOUT_RETEST_WARNING for w in actual["visibility_warnings"])


def test_genuine_break_without_retest_does_not_erase_independent_trade_plan_rejections():
    source = {**bundle(), "native_plan_reason": "first_opposing_barrier_before_minimum_rr",
              "score": 95, "grade": "S"}
    actual = visibility.present(source, released=True)
    assert CONFIRMED in codes(source)
    assert "first_opposing_barrier_before_minimum_rr" in codes(source)
    assert actual["visibility_status"] == "candidate_warning"
    assert not actual["visibility_is_trade_signal"]


def test_warning_code_or_label_alone_does_not_invent_either_break_or_missing_retest():
    source = {"risk_flags": [CONFIRMED, BREAKOUT_WITHOUT_RETEST_WARNING]}
    assert not {CONFIRMED, NEUTRAL}.intersection(codes(source))


@pytest.mark.parametrize("direction,reason", [
    ("LONG", "crossed_resistance_unconfirmed"), ("SHORT", "crossed_support_unconfirmed"),
])
def test_actual_visibility_output_and_ui_do_not_reintroduce_raw_contradictory_confirmation(direction, reason):
    from test_frontend_candidate_visibility import render_candidate

    source = {**bundle(direction), "native_plan_reason": reason}
    presented = visibility.present(source, released=True)
    # Preserve raw producer evidence for audit; presentation must not restore it
    # as a current confirmed statement after the authoritative neutralization.
    assert presented["retest_warning"] == BREAKOUT_WITHOUT_RETEST_WARNING
    tree = render_candidate(presented)
    assert tree["statuses"] == ["candidate_warning"]
    assert "Schlusskursbestätigung fehlt" in tree["visible"]
    assert BREAKOUT_WITHOUT_RETEST_WARNING not in tree["full"]
    assert visibility.LABELS[CONFIRMED] not in tree["full"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_actual_confirmed_break_with_independent_near_target_keeps_ui_warning(direction):
    from test_frontend_candidate_visibility import render_candidate

    source = {**bundle(direction), "native_plan_reason": "first_opposing_barrier_before_minimum_rr"}
    tree = render_candidate(visibility.present(source, released=True))
    assert tree["statuses"] == ["candidate_warning"]
    assert tree["statusPanels"] == 1 and tree["retestPanels"] == 0
    assert "Rücktest offen" in tree["visible"]
    assert visibility.LABELS[CONFIRMED] in tree["full"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("patch", [
    {"breakout_invalidated": True}, {"failed_breakout": True}, {"breakout_failed": True},
    {"breakout_confirmation": "unconfirmed"}, {"breakout_confirmation": "invalidated"},
    {"break_state": "intact"}, {"break_state": "pending"}, {"break_state": "expired"},
    {"structure_state": "failed"}, {"entry_state": "invalid"},
    {"entry_trigger": {"state": "expired"}}, {"retest_status": "failed"},
])
def test_explicit_negative_breakout_metadata_cannot_keep_a_released_display(direction, nested, patch):
    source = bundle(direction)
    if nested:
        source["trade_setup"] = patch
    else:
        source.update(patch)
    before = deepcopy(source)
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "candidate_warning"
    assert not actual["visibility_is_trade_signal"]
    assert "breakout_evidence_conflict" in codes(source)
    assert CONFIRMED not in codes(source)
    assert source == before
    assert {key: actual[key] for key in before} == before


@pytest.mark.parametrize("reason", ["crossed_resistance_unconfirmed", "crossed_support_unconfirmed",
                                    "breakout_failed", "breakout_invalidated", "trigger_not_current"])
@pytest.mark.parametrize("field", ["native_plan_reason", "structure_reason", "risk_flags"])
def test_nested_explicit_breakout_rejection_is_preserved_and_downgrades_display(reason, field):
    source = {**bundle(), "trade_setup": {field: [reason] if field == "risk_flags" else reason}}
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "candidate_warning"
    assert reason in codes(source)
    assert CONFIRMED not in codes(source)


@pytest.mark.parametrize("direction,other", [("LONG", "SHORT"), ("SHORT", "LONG")])
@pytest.mark.parametrize("optional_bundle", [False, True])
def test_explicit_direction_conflict_downgrades_display_without_mutating_trade_fields(direction, other, optional_bundle):
    source = bundle(direction) if optional_bundle else {"direction": direction}
    source["trade_setup"] = {"direction": other}
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "candidate_warning"
    assert "breakout_evidence_conflict" in codes(source)
    assert actual["direction"] == direction and actual["trade_setup"] == source["trade_setup"]


@pytest.mark.parametrize("source", [{}, {"direction": "LONG"}, {"direction": "SHORT"},
                                   {"direction": "NEUTRAL"}, {"retest_status": "not_confirmed"},
                                   {"trade_setup": {"entry": 100, "stop": 95, "target": 110}}])
def test_absent_optional_breakout_bundle_does_not_create_a_new_release_requirement(source):
    actual = visibility.present(source, released=True)
    assert actual["visibility_status"] == "released"
    assert actual["visibility_is_trade_signal"]
    assert "breakout_evidence_conflict" not in codes(source)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_explicit_invalidated_breakout_uses_one_compact_warning_and_no_stale_ui_confirmation(direction):
    from test_frontend_candidate_visibility import render_candidate

    source = {**bundle(direction), "breakout_invalidated": True}
    tree = render_candidate(visibility.present(source, released=True))
    assert tree["statuses"] == ["candidate_warning"]
    assert tree["statusPanels"] == 1 and tree["retestPanels"] == 0
    assert "Ausbruch nicht gültig bestätigt" in tree["visible"]
    assert BREAKOUT_WITHOUT_RETEST_WARNING not in tree["full"]
