"""Confirmed closes may qualify without inventing retests or bypassing risk gates."""
from copy import deepcopy
from datetime import timedelta

import pytest

from modules.breakout_warnings import (
    BREAKOUT_WITHOUT_RETEST_CODE, BREAKOUT_WITHOUT_RETEST_WARNING, breakout_warning_fields, apply_breakout_warning,
)
from modules.wyckoff_contract import validate_entry_trigger
from test_wyckoff_engine import BASE, analyze, selected, textbook_bars
from test_wyckoff_robustness import mirror_bar_update
from test_wyckoff_v3_engine import range_bars


@pytest.mark.parametrize("confirmed", [False, None, 1, "true", {}, []])
def test_warning_helper_never_infers_confirmation(confirmed):
    assert breakout_warning_fields(confirmed) == {}


@pytest.mark.parametrize("retest", [True, None, 0, "false", {}, []])
def test_warning_helper_never_marks_a_confirmed_or_unknown_retest_missing(retest):
    assert breakout_warning_fields(True, retest) == {}


def test_warning_contract_is_explicit_and_each_result_has_its_own_codes():
    warning = breakout_warning_fields(True, False)
    assert warning == {"breakout_confirmation": "confirmed_close", "retest_status": "not_confirmed",
                       "retest_warning": BREAKOUT_WITHOUT_RETEST_WARNING,
                       "warning_codes": [BREAKOUT_WITHOUT_RETEST_CODE]}
    warning["warning_codes"].append("mutated")
    assert breakout_warning_fields(True)["warning_codes"] == [BREAKOUT_WITHOUT_RETEST_CODE]


@pytest.mark.parametrize("accepted,retested", [(False, False), (True, True), (None, False)])
def test_warning_updater_clears_only_owned_stale_fields_and_preserves_other_warnings(accepted, retested):
    row = {"ticker": "KEEP", "warning_codes": ["liquidity_warning", BREAKOUT_WITHOUT_RETEST_CODE],
           **{key: value for key, value in breakout_warning_fields(True).items() if key != "warning_codes"},
           "warnings": ["market_warning", BREAKOUT_WITHOUT_RETEST_WARNING],
           "notes": [BREAKOUT_WITHOUT_RETEST_WARNING, "plan_reference"]}
    assert apply_breakout_warning(row, accepted, retested) is row
    assert row == {"ticker": "KEEP", "warning_codes": ["liquidity_warning"],
                   "warnings": ["market_warning"], "notes": ["plan_reference"]}


def test_warning_updater_is_idempotent_and_does_not_overwrite_other_codes():
    row = {"warning_codes": ["existing"], "retest_warning": "unrelated diagnostic"}
    apply_breakout_warning(row, False)
    assert row["retest_warning"] == "unrelated diagnostic"
    apply_breakout_warning(row, True)
    apply_breakout_warning(row, True)
    assert row["warning_codes"] == ["existing", BREAKOUT_WITHOUT_RETEST_CODE]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("spring", [False, True])
def test_completed_volume_breakout_is_ready_with_warning_without_fake_retest(direction, spring):
    row = selected(analyze(textbook_bars(direction, spring=spring)[:80], direction, count=80), direction)
    trigger = row["entry_trigger"]
    assert row["trade_ready"] and row["phase"] == "D"
    assert trigger["trigger_mode"] == "confirmed_breakout"
    assert set(trigger["event_ids"]) == {"origin", "reaction", "test", "breakout"}
    assert not any(event["name"] in {"LPS", "LPSY", "EFollowThrough"} for event in row["events"])
    assert row["signal_confirmed_at"] == trigger["confirmed_at"] == row["latest_completed_at"]
    assert row["trade"]["fill_evidence_verified"] is False
    for key, value in breakout_warning_fields(True).items():
        assert row[key] == value
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=80), model="causal_wyckoff_v3") is trigger


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("continuation", [False, True])
def test_nonclimactic_and_continuation_breakouts_retain_own_range_requirements(direction, continuation):
    row = selected(analyze(range_bars(direction, continuation=continuation)[:66], direction, count=66), direction)
    assert row["trade_ready"] and row["entry_trigger"]["trigger_mode"] == "confirmed_breakout"
    assert row["origin_kind"] == ("continuation" if continuation else "nonclimactic")
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=66), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("damage", ["wick_only", "weak_volume", "wrong_body", "unfinished"])
def test_no_warning_or_entry_without_original_volume_and_completed_close_proof(direction, damage):
    bars = textbook_bars(direction)[:80]
    if damage == "wick_only":
        mirror_bar_update(bars, 79, direction, open=104., high=109., low=103.8, close=105., volume=3000.)
    elif damage == "weak_volume":
        bars[79]["volume"] = 1000.
    elif damage == "wrong_body":
        mirror_bar_update(bars, 79, direction, open=108.5, high=109., low=103.8, close=108., volume=3000.)
    else:
        bars[79]["is_closed"] = False
    result = analyze(bars, direction, count=80)
    assert not any(row["trade_ready"] for row in result["patterns"])
    assert not any(row.get("retest_warning") for row in result["patterns"])


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("damage,state", [("failed", "expired"), ("stop", "stopped"),
                                          ("target", "target_passed"), ("both", "ambiguous")])
def test_breakout_warning_never_bypasses_later_failure_stop_consumed_target_or_ambiguity(direction, damage, state):
    bars = textbook_bars(direction)[:85]
    updates = {"open": 108., "high": 109., "low": 107., "close": 108.}
    if damage == "failed": updates.update(low=104., close=105.)
    elif damage == "stop": updates.update(low=101.)
    elif damage == "target": updates.update(high=116.)
    else: updates.update(low=101., high=116.)
    mirror_bar_update(bars, 84, direction, **updates)
    row = selected(analyze(bars, direction, count=85), direction)
    assert not row["trade_ready"] and row["trade"] is None
    assert row["entry_state"] == state and "retest_warning" not in row
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=85), model="causal_wyckoff_v3") is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_later_retest_has_its_own_five_anchor_trigger_and_removes_missing_retest_warning(direction):
    bars = textbook_bars(direction)
    breakout = selected(analyze(bars[:80], direction, count=80), direction)
    retested = selected(analyze(bars, direction), direction)
    assert retested["entry_trigger"]["trigger_mode"] == "confirmed_retest"
    assert retested["entry_trigger"]["trigger_id"] != breakout["entry_trigger"]["trigger_id"]
    assert set(retested["entry_trigger"]["event_ids"]) == {"origin", "reaction", "test", "breakout", "retest"}
    assert "retest_warning" not in retested and "warning_codes" not in retested
    assert validate_entry_trigger(retested, as_of=BASE + timedelta(days=100), model="causal_wyckoff_v3")


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_close_exactly_back_on_boundary_permanently_expires_old_breakout(direction):
    bars = textbook_bars(direction)[:85]
    original = selected(analyze(bars[:80], direction, count=80), direction)
    # The mirrored fixture's original range upper boundary is 106. A wick
    # alone may touch it, but a completed close on it no longer holds outside.
    boundary = original["range_high"] if direction == "LONG" else 200. - original["range_low"]
    mirror_bar_update(bars, 81, direction, open=108., high=109., low=boundary - .1, close=boundary)
    mirror_bar_update(bars, 82, direction, open=108., high=109., low=107., close=108.)
    result = selected(analyze(bars, direction, count=85), direction)
    old = next(t for t in result["entry_triggers"] if t["trigger_id"] == original["entry_trigger"]["trigger_id"])
    assert old["state"] == "expired" and old["reason"] == "breakout_failed"
    assert old["terminal_at"] == bars[81]["close_time"].isoformat().replace("+00:00", "Z")
    assert not result["trade_ready"] and "retest_warning" not in result


@pytest.mark.parametrize("codes", [None, [], ["unrelated"], "breakout_confirmed_without_retest"])
def test_tracker_does_not_invent_warning_code_from_half_metadata(codes):
    from modules.signal_tracker import extract_execution_context
    pattern = selected(analyze(textbook_bars()[:80], "LONG", count=80), "LONG")
    pattern["warning_codes"] = codes
    row = {"wyckoff": pattern, **breakout_warning_fields(True), "warning_codes": codes}
    context = extract_execution_context(row)["confirmation"]
    assert "warning_codes" not in context and "retest_warning" not in context
    assert "warning_codes" not in context["wyckoff"] and "retest_warning" not in context["wyckoff"]


@pytest.mark.parametrize("damage", ["mode_missing", "unknown_mode", "mode_type", "fake_retest",
    "missing_test", "bad_volume", "inside_range", "unconfirmed_close", "unknown_boundary", "future"])
def test_new_four_anchor_contract_fails_closed_without_explicit_mode_and_proof(damage):
    row = selected(analyze(textbook_bars()[:80], "LONG", count=80), "LONG")
    trigger = row["entry_trigger"]
    event = next(event for event in row["events"] if event["event_id"] == trigger["event_ids"]["breakout"])
    if damage == "mode_missing": trigger.pop("trigger_mode")
    elif damage == "unknown_mode": trigger["trigger_mode"] = "anything"
    elif damage == "mode_type": trigger["trigger_mode"] = []
    elif damage == "fake_retest": trigger["event_ids"]["retest"] = event["event_id"]
    elif damage == "missing_test": trigger["event_ids"].pop("test")
    elif damage == "bad_volume": event["volume_ratio"] = 1.49
    elif damage == "inside_range": event["price"] = row["range_high"]
    elif damage == "unconfirmed_close": event["observed_at"] = (BASE + timedelta(days=79)).isoformat()
    elif damage == "unknown_boundary": row.pop("range_high")
    else: event["confirmed_at"] = (BASE + timedelta(days=100)).isoformat()
    assert validate_entry_trigger(row, as_of=BASE + timedelta(days=80), model="causal_wyckoff_v3") is None


def test_penny_accepted_breakout_already_does_not_need_retest_and_warning_does_not_change_scores():
    from modules.penny_stock_scanner import evaluate_penny_candidate
    from test_penny_stock_scanner import _market_now, _snapshot, _compressed_breakout_bars, _details, _targets
    now = _market_now()
    row = evaluate_penny_candidate(_snapshot(), _compressed_breakout_bars(now), [],
                                   details=_details(), extra_resistances=_targets(), now_ts=now)
    assert row["trade_action"] == "JETZT_KAUFEN" and row["hard_blockers"] == []
    assert row["execution_trigger_ok"] is True and row["trigger_type"] == "5m_breakout"
    for key, value in breakout_warning_fields(True).items():
        assert row[key] == value and row["trade_setup"][key] == value
    assert BREAKOUT_WITHOUT_RETEST_WARNING in row["warnings"]
    assert BREAKOUT_WITHOUT_RETEST_WARNING in row["trade_setup"]["notes"]


@pytest.mark.parametrize("damage", ["stale", "spread", "no_rr", "chase"])
def test_penny_execution_veto_stays_a_veto_and_never_receives_accepted_breakout_marker(damage):
    from modules.penny_stock_scanner import evaluate_penny_candidate
    from test_penny_stock_scanner import _market_now, _snapshot, _compressed_breakout_bars, _details, _targets
    now = _market_now()
    snapshot, bars, targets = _snapshot(), _compressed_breakout_bars(now), _targets()
    if damage == "stale":
        for bar in bars: bar["timestamp"] -= 3600
    elif damage == "spread": snapshot["spread_bps"] = 300
    elif damage == "no_rr": targets = [{"price": 1.047, "source": "unbroken resistance", "weight": 2.0}]
    else: snapshot.update(price=1.15, ask=1.151, bid=1.149)
    row = evaluate_penny_candidate(snapshot, bars, [], details=_details(), extra_resistances=targets, now_ts=now)
    assert row["trade_action"] != "JETZT_KAUFEN" and row["hard_blockers"]
    assert "retest_warning" not in row and "retest_warning" not in row["trade_setup"]


def test_tracker_keeps_explicit_four_role_mode_and_safe_warning_not_arbitrary_fields():
    from modules.signal_tracker import extract_execution_context
    pattern = selected(analyze(textbook_bars()[:80], "LONG", count=80), "LONG")
    pattern["private_key"] = "NEVER-COPY"
    pattern["entry_trigger"]["event_ids"]["private_key"] = "NEVER-COPY"
    row = {"wyckoff": pattern, **breakout_warning_fields(True)}
    context = extract_execution_context(row)["confirmation"]
    assert context["warning_codes"] == [BREAKOUT_WITHOUT_RETEST_CODE]
    proof = context["wyckoff"]
    assert proof["trigger_mode"] == "confirmed_breakout"
    assert set(proof["event_ids"]) == {"origin", "reaction", "test", "breakout"}
    assert proof["retest_warning"] == BREAKOUT_WITHOUT_RETEST_WARNING
    assert "NEVER-COPY" not in str(context)


def test_chart_projection_keeps_warning_and_no_fake_lps_for_confirmed_breakout():
    from modules.patterns import detect_chart_patterns
    bars = textbook_bars()[:80]
    rows = detect_chart_patterns(bars, wyckoff_context={"bars": bars, "as_of": BASE + timedelta(days=80), "timeframe": "1D"})
    row = next(row for row in rows if row.get("model") == "causal_wyckoff_v3" and row["direction"] == "LONG")
    assert row["trade_ready"] and row["retest_warning"] == BREAKOUT_WITHOUT_RETEST_WARNING
    assert "LPS" not in row["events"] and "retest" not in row["entry_trigger"]["event_ids"]


def micro_proof():
    return {"micro_trigger_ok": True, "micro_support_break": True,
            "micro_breakout_confirmation": "confirmed_close", "micro_candle_closed_at": 1000}


def test_new_listing_warning_requires_explicit_producer_support_break_close():
    from modules.new_listing_scanner import _accepted_micro_breakout_warning
    assert _accepted_micro_breakout_warning(micro_proof(), True, now_ts=1300) == breakout_warning_fields(True)
    assert _accepted_micro_breakout_warning(micro_proof(), False, now_ts=1300) == {}


@pytest.mark.parametrize("change", [
    {"micro_trigger_ok": False}, {"micro_trigger_ok": "true"}, {"micro_support_break": False},
    {"micro_breakout_confirmation": None}, {"micro_candle_closed_at": None},
    {"micro_candle_closed_at": 500}, {"micro_candle_closed_at": 1400}, {"micro_candle_closed_at": True},
])
def test_new_listing_warning_does_not_infer_proof_from_short_name_rejection_or_stale_clock(change):
    from modules.new_listing_scanner import _accepted_micro_breakout_warning
    assert _accepted_micro_breakout_warning({**micro_proof(), **change}, True, now_ts=1300) == {}


def test_new_listing_lower_high_trigger_is_not_mislabeled_as_support_breakout():
    from modules.new_listing_scanner import calculate_micro_crack_trigger
    from test_new_listing_deep_audit import _micro_crack_candles
    micro = calculate_micro_crack_trigger(_micro_crack_candles(), {"ath": 130})
    assert micro["micro_trigger_ok"] is True and micro["micro_support_break"] is False
    assert micro["micro_breakout_confirmation"] is None


def test_new_listing_final_accepted_signal_carries_warning_without_relaxing_gates():
    import time
    from modules.new_listing_scanner import generate_short_signal
    from test_new_listing_deep_audit import _with_causal_listing_vrvp
    pump = _with_causal_listing_vrvp({"ath": 100, "current_price": 96, "pump_pct": 70,
        "from_ath_pct": 4.0, "momentum_recent": -.2, "current_red_streak": 1, "avg_upper_wick_pct": 30,
        "recent_rejection_high": 100, "recent_crack_depth_pct": 4, "prior_3_low_broken": True,
        "lower_high_confirmed": True, "micro_score": 75, "micro_stop_loss": 100,
        "listing_source": "new_listing", "listing_age_hours": 24,
        **micro_proof(), "micro_candle_closed_at": int(time.time()) - 30})
    args = dict(exh_score=50, exh_details=[], safety_ok=True, safety_warnings=[])
    row = generate_short_signal("OFFLINE", pump, **args)
    assert row["trade_action"] == "SHORT_NOW" and row["retest_warning"] == BREAKOUT_WITHOUT_RETEST_WARNING
    assert row["trade_setup"]["warning_codes"] == [BREAKOUT_WITHOUT_RETEST_CODE]
    blocked = generate_short_signal("OFFLINE", pump, **{**args, "safety_ok": False})
    assert blocked["trade_action"] != "SHORT_NOW" and "retest_warning" not in blocked


def test_replay_separates_causal_breakout_and_later_retest_without_fabricated_evidence():
    from scripts import evaluate_wyckoff as replay
    from test_wyckoff_replay import dataset
    doc = dataset()
    doc["cutoffs"] = [doc["bars"][index]["close_time"] for index in (79, 80, 85, 86, 99)]
    report = replay.evaluate(doc)
    assert report["distinct_signal_count"] == 2 and report["distinct_structure_count"] == 1
    by_mode = {signal["trigger_mode"]: signal for signal in report["signals"]}
    breakout, retest = by_mode["confirmed_breakout"], by_mode["confirmed_retest"]
    assert {event["role"] for event in breakout["events"]} == {"origin", "reaction", "test", "breakout"}
    assert {event["role"] for event in retest["events"]} == {"origin", "reaction", "test", "breakout", "retest"}
    assert breakout["warning_codes"] == [BREAKOUT_WITHOUT_RETEST_CODE] and "retest_warning" not in retest
    assert breakout["first_seen_at"] < retest["first_seen_at"]


@pytest.mark.parametrize("mode", [None, "guessed", [], "confirmed_retest"])
def test_replay_four_role_identity_needs_explicit_breakout_mode(mode):
    from scripts import evaluate_wyckoff as replay
    row = selected(analyze(textbook_bars()[:80], "LONG", count=80), "LONG")
    row["entry_trigger"]["trigger_mode"] = mode
    with pytest.raises(ValueError):
        replay._signal_identity(row)


def test_changed_entry_policy_is_bound_to_replay_parameters():
    from modules.wyckoff import PARAMETERS
    assert PARAMETERS["version"] == "wyckoff_v3_rules_2"
    assert PARAMETERS["entry_policy"] == "confirmed_breakout_optional_retest"
