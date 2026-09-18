"""Plan-building diagnostics explain rejection without changing trading gates."""
from datetime import datetime, timezone
import json

import pytest
import api
from modules.level_zones import StructureSnapshot
from test_stock_momentum_confirmed_contract import _wrapper_fixture, NAME


@pytest.mark.parametrize("changes,reason", [
    ({"entry": 0}, "invalid_entry_or_direction"),
    ({"direction": "PRIVATE_TOKEN"}, "invalid_entry_or_direction"),
    ({"require_causal_structure": True}, "causal_structure_missing"),
    ({"structure_snapshot": StructureSnapshot(
        symbol="PRIVATE_TICKER", asset_class="stock", horizon="swing",
        as_of=datetime(2026, 9, 1, tzinfo=timezone.utc), current_price=100.,
        zones=(), atr_by_timeframe={}, completed_bar_counts={},
        quality_flags=("no_completed_bars",))}, "causal_structure_unavailable"),
])
def test_builder_explains_fail_closed_return_without_private_values(changes, reason):
    args = dict(direction="LONG", entry=100., atr=2., support_1=95.,
                resistance_1=110., high_20d=112., low_20d=94.)
    args.update(changes)
    diagnostic = {}
    assert api._build_structured_trade_setup(**args, diagnostics=diagnostic) is None
    assert diagnostic == {"status": "unavailable", "reason": reason}
    assert "PRIVATE" not in json.dumps(diagnostic)


def test_native_but_low_room_plan_still_waits_with_explanation():
    diagnostic = {}
    args = dict(direction="LONG", entry=9.27, atr=.28, support_1=8.97,
                resistance_1=9.57, high_20d=9.6, low_20d=8.35, range_pos=73)
    expected = api._build_structured_trade_setup(**args)
    result = api._build_structured_trade_setup(**args, diagnostics=diagnostic)
    assert result == expected
    assert result["trade_action"] == "WAIT_FOR_BREAK_RECLAIM"
    assert result["tp1"] == 9.57
    assert diagnostic == {"status": "built", "reason": "first_opposing_barrier_before_minimum_rr"}


def test_scan_persists_missing_plan_reason_without_creating_levels(monkeypatch):
    written = _wrapper_fixture(monkeypatch)
    rows = api._strategy_scan_wrapper(NAME, send_email=False)
    assert len(rows) == 1
    assert "trade_setup" not in rows[0]
    assert rows[0]["native_plan_reason"] == "causal_structure_missing"
    assert written[0][1]["metadata"]["diagnostics"]["plan_build_counts"] == {
        "causal_structure_missing": 1}


def test_public_diagnostics_keep_only_fixed_nonnegative_integer_plan_counts():
    result = api._stock_strategy_attempt_diagnostics({"plan_build_counts": {
        "causal_structure_missing": 2, "native_structure_plan": 3,
        "PRIVATE_TICKER_TOKEN": 42, "direction_missing": True,
        "invalid_trade_geometry": -1, "invalid_stop_risk": "PRIVATE"}}, sweep=False)
    assert result["plan_build_counts"] == {"causal_structure_missing": 2, "native_structure_plan": 3}
    assert "PRIVATE" not in json.dumps(result)
