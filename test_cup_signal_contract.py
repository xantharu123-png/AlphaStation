"""No-I/O tests for current Cup provenance across persistence boundaries."""

from copy import deepcopy
import json
import math

import pytest

from modules.cup_signal_contract import (
    CUP_PATTERN_CONTRACT_VERSION,
    cup_signal_contract_reason,
    cup_signal_contract_valid,
    is_cup_signal,
)


NAME = "Cup and Handle Breakout"


def _row(**changes):
    return {
        "Ticker": "CUP", "pattern_type": "cup_handle_breakout",
        "pattern_timeframe": "1D", "direction": "LONG",
        "cup_pattern_version": CUP_PATTERN_CONTRACT_VERSION,
        "cup_confirmation_close": 101.0 * 1.002,
        "cup_confirmation_level": 101.0, "cup_rim_level": 100.0,
        "Breakout_Level": 100.0, **changes,
    }


def test_exact_raw_threshold_is_valid_and_round_trips_json():
    row = _row()
    assert cup_signal_contract_valid(row, strategy_name=NAME)
    assert cup_signal_contract_reason(json.loads(json.dumps(row))) is None
    assert CUP_PATTERN_CONTRACT_VERSION == "cup_bowl_close_v2"


def test_one_float_below_threshold_does_not_confirm_even_if_display_is_identical():
    row = _row()
    row["cup_confirmation_close"] = math.nextafter(row["cup_confirmation_close"], -math.inf)
    assert round(row["cup_confirmation_close"], 2) == round(101 * 1.002, 2)
    assert cup_signal_contract_reason(row) == "cup_contract_close_not_confirmed"


def test_wick_live_quote_and_good_close_position_cannot_replace_daily_close():
    row = _row(cup_confirmation_close=100.1, high=150., close_pos=0.99,
               Price=150., price=150., daily_close_confirmed=True,
               entry_status="BREAKOUT_CONFIRMED", pattern_score=100)
    assert not cup_signal_contract_valid(row)


@pytest.mark.parametrize("field", [
    "cup_confirmation_close", "cup_confirmation_level", "cup_rim_level",
])
@pytest.mark.parametrize("value", [
    None, True, False, 0, -1, float("nan"), float("inf"), -float("inf"),
    "101.202", "NaN", [], {}, 10 ** 1000,
])
def test_invalid_raw_prices_fail_closed(field, value):
    assert cup_signal_contract_reason(_row(**{field: value})) == "cup_contract_invalid_raw_prices"


@pytest.mark.parametrize("version", [None, False, True, "", "cup_bowl_close_v1", "cup_bowl_close_v2 ", 2, [], {}])
def test_legacy_or_malformed_versions_are_rejected(version):
    assert cup_signal_contract_reason(_row(cup_pattern_version=version)) == "cup_contract_legacy_or_missing_version"


@pytest.mark.parametrize("timeframe", [None, True, 1, "1d", "D", "5m", "1D ", [], {}])
def test_exact_daily_timeframe_is_required(timeframe):
    assert cup_signal_contract_reason(_row(pattern_timeframe=timeframe)) == "cup_contract_invalid_timeframe"


@pytest.mark.parametrize("field", [
    "cup_pattern_version", "cup_confirmation_close", "cup_confirmation_level",
    "cup_rim_level", "pattern_timeframe",
])
def test_missing_provenance_rejects_old_watch_or_cache_rows(field):
    row = _row()
    row.pop(field)
    assert not cup_signal_contract_valid(row)


@pytest.mark.parametrize("field,value", [
    ("pattern", NAME), ("pattern", "Cup & Handle"), ("Pattern", "Cup-and-Handle"),
    ("pattern_type", "cup_handle_breakout"), ("source", "cup_handle_1d_breakout"),
    ("strategy", NAME), ("Strategy", NAME), ("Strategie", NAME),
    ("strategy_name", "cup_and_handle_breakout"), ("cup_pattern_version", None),
    ("cup_pattern_evidence", {}), ("cup_rim_level", 100.), ("CupDepth%", 20.),
    ("needs_cup_handle", True),
])
def test_any_cup_identity_enables_gate_even_if_other_labels_are_stripped(field, value):
    legacy = {"Ticker": "OLD", field: value}
    assert is_cup_signal(legacy)
    assert not cup_signal_contract_valid(legacy)


def test_nested_trade_setup_source_identifies_legacy_cup():
    row = {"trade_setup": {"source": "polygon_completed_5m_cup_handle_next_session_trigger"}}
    assert is_cup_signal(row)
    assert not cup_signal_contract_valid(row)


@pytest.mark.parametrize("context", [
    {"strategy_name": NAME}, {"strategy": {"needs_cup_handle": True}},
    {"strategy": {"name": NAME}}, {"strategy": {"strategy_name": NAME}},
])
def test_external_context_rejects_completely_nameless_legacy_cup(context):
    row = {"Ticker": "OLD", "grade": "S", "daily_close_confirmed": True}
    assert is_cup_signal(row, **context)
    assert not cup_signal_contract_valid(row, **context)


def test_all_display_labels_may_be_removed_but_current_provenance_remains_gated():
    row = _row()
    row.pop("pattern_type")
    assert is_cup_signal(row)
    assert cup_signal_contract_valid(row)
    row["cup_confirmation_close"] = 99.
    assert not cup_signal_contract_valid(row)


@pytest.mark.parametrize("context", [
    {"strategy_name": "Momentum Breakout Long"},
    {"strategy": {"name": "Momentum Breakout Long"}},
])
def test_valid_cup_cannot_be_served_as_other_strategy(context):
    assert cup_signal_contract_reason(_row(), **context) == "cup_contract_strategy_mismatch"


def test_effective_resistance_must_be_at_least_raw_rim():
    assert cup_signal_contract_reason(_row(cup_confirmation_level=99.)) == "cup_contract_confirmation_below_rim"
    assert cup_signal_contract_valid(_row(cup_confirmation_level=100., cup_confirmation_close=100.2))


@pytest.mark.parametrize("rim,display", [(100.004, 100.), (1.1234, 1.123), (.123456, .12346), (.00123456789, .00123457)])
def test_display_rim_is_bound_using_existing_price_rounding(rim, display):
    row = _row(cup_rim_level=rim, cup_confirmation_level=rim,
               cup_confirmation_close=rim * 1.002, Breakout_Level=display)
    assert cup_signal_contract_valid(row)
    row["Breakout_Level"] = math.nextafter(display, math.inf)
    assert cup_signal_contract_reason(row) == "cup_contract_display_rim_mismatch"


@pytest.mark.parametrize("level", [None, True, False, "100", 101., float("nan"), float("inf"), 0])
def test_unbound_or_malformed_display_rim_is_rejected(level):
    assert cup_signal_contract_reason(_row(Breakout_Level=level)) == "cup_contract_display_rim_mismatch"


def test_normalized_breakout_level_alias_is_bound_too():
    assert not cup_signal_contract_valid(_row(breakout_level=101.))


def test_optional_display_rim_is_not_needed_for_raw_close_proof():
    row = _row()
    row.pop("Breakout_Level")
    assert cup_signal_contract_valid(row)


@pytest.mark.parametrize("direction", [None, True, "SHORT", "NEUTRAL", "", 1, []])
@pytest.mark.parametrize("location", ["direction", "Signal_Direction", "trade_setup"])
def test_cup_direction_must_remain_long_if_present(direction, location):
    changes = {location: direction if location != "trade_setup" else {"direction": direction}}
    assert cup_signal_contract_reason(_row(**changes)) == "cup_contract_invalid_direction"


@pytest.mark.parametrize("row", [None, True, False, [], "Cup and Handle", 1])
def test_non_dictionary_rows_never_pass(row):
    assert not cup_signal_contract_valid(row)


@pytest.mark.parametrize("row", [
    {}, {"strategy": "Momentum Breakout Long"}, {"pattern": "Double Bottom"},
    {"source": "cupboard_inventory"}, {"strategy": "World Cup"},
])
def test_unrelated_rows_are_unchanged(row):
    assert not is_cup_signal(row)
    assert cup_signal_contract_valid(row)


def test_raw_multiplication_overflow_fails_closed():
    row = _row(cup_confirmation_close=1.7976931348623157e308,
               cup_confirmation_level=1.7976931348623157e308)
    assert cup_signal_contract_reason(row) == "cup_contract_close_not_confirmed"


def test_validation_is_pure():
    row = _row(trade_setup={"direction": "LONG", "source": "cup_handle_1d_breakout"})
    original = deepcopy(row)
    assert cup_signal_contract_valid(row, strategy={"needs_cup_handle": True})
    assert row == original
