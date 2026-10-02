"""Actual stock producer -> cached row -> unchanged final mail boundaries.

Synthetic provider/history fixtures only. These isolate evidence transport;
they do not declare the incomplete native fixture a fully released trade.
"""
import pytest

import api
from test_momentum_decision_precision import producer_row
from test_stock_momentum_confirmed_contract import _metrics


@pytest.mark.parametrize("median,expected", [(1_999_999.51, False), (2_000_000., True), (2_000_000.49, True)])
def test_stock_producer_to_mail_liquidity_floor_cannot_round_thin_baseline_up(monkeypatch, median, expected):
    row = producer_row(monkeypatch, metrics=_metrics(median_dollar_vol20=median))
    candidate = dict(row, TP1=110.)
    result = api._stock_strategy_mail_quality_state(candidate, daily_close_confirmed_mode=True)
    assert result[0] is expected, {"input_median": median, "cached_median": row["MedianDollarVol20"], "mail_result": result}
    assert row["MedianDollarVol20"] == row["median_dollar_volume_20d"] == median
    if not expected:
        assert result[1] == "momentum_mail_blocked_thin_baseline_liquidity"


@pytest.mark.parametrize("move,expected", [(14.996, False), (15., True), (15.004, True)])
def test_stock_producer_to_mail_multi_day_extension_does_not_invent_5_atr(monkeypatch, move, expected):
    # Price 101, ATR 3.03 => exactly 3% ATR. The existing 5 ATR boundary is
    # therefore a genuine 15% five-day move, not a formatted 15.00% label.
    row = producer_row(monkeypatch, price=101., previous=98., day_open=99.,
        day_high=101.15, day_low=97., metrics=_metrics(atr14=3.03, atr_pct=3., change_5d=move))
    reasons = api._stock_swing_rule_reasons(row)
    assert ("swing_multi_day_extended_wait_retest" in reasons) is expected, {
        "original_5d": move, "row_5d": row["Change_5D"], "ratio": api._swing_multi_day_move_atr(row), "reasons": reasons}
    assert row["Change_5D"] == move


@pytest.mark.parametrize("gap,expected", [(2.9996, False), (3., True), (3.0004, True)])
def test_stock_producer_to_mail_3_percent_gap_overlap_keeps_actual_open(monkeypatch, gap, expected):
    # A barely <3% opening gap must not acquire the >=3% momentum-gap gate
    # merely because its cached display value was rounded to 3.00.
    row = producer_row(monkeypatch, price=103.1, previous=100., day_open=100.*(1.+gap/100.),
        day_high=103.2, day_low=102.4, metrics=_metrics(change_5d=3.))
    reasons = api._stock_swing_rule_reasons(row)
    assert ("swing_momentum_not_holding_open_wait_retest" in reasons) is expected, {
        "raw_gap": gap, "row_gap": row["Gap_Pct"], "reasons": reasons}
    assert row["Gap_Pct"] == pytest.approx(gap, abs=1e-12)


@pytest.mark.parametrize("metric,field", [
    ("high_20d", "High_20D"), ("high_10d", "High_10D"), ("low_20d", "Low_20D"),
    ("high_50d", "High_50D"), ("low_50d", "Low_50D"),
    ("ema20", "EMA20"), ("ema50", "EMA50"), ("ema200", "EMA200"),
    ("change_20d", "Change_20D"), ("breakout_10d_pct", "Breakout_10D_Pct"),
    ("breakout_20d_pct", "Breakout_20D_Pct"), ("breakout_50d_pct", "Breakout_50D_Pct"),
    ("ema20_distance_pct", "EMA20_Distance_Pct"), ("ema50_distance_pct", "EMA50_Distance_Pct"),
])
def test_stock_analysis_evidence_is_not_order_tick_formatted_in_saved_row(monkeypatch, metric, field):
    # Order prices have a tick grid. Analytical/source observations do not.
    # This preservation contract alone is not proof of a mail misclassification.
    value = 95.004123 if metric.startswith(("high", "low", "ema")) and "distance" not in metric else 3.004123
    row = producer_row(monkeypatch, metrics=_metrics(**{metric: value}))
    assert row[field] == value


def _causal_tick_boundary_snapshot(short):
    from modules.level_zones import LevelZone
    from test_level_zones import BASE, _evidence, _snapshot
    prices = [103.0000004, 94., 90.]
    if not short:
        prices = [200.-value for value in prices]
    zones = [LevelZone(
        zone_id=f"fixture-{index}", lower=value, upper=value,
        reference=value, side_at_reference="resistance" if value > 100 else "support",
        evidence=(_evidence(value),), independent_sources=1,
        independent_structural_sources=1, touch_count=1, confirmed_at=BASE,
        break_state="unbroken", strength=1.)
        for index, value in enumerate(prices)]
    return _snapshot(zones)


@pytest.mark.parametrize("short,expected", [(True, 103.31), (False, 96.69)])
def test_native_causal_invalidation_stop_keeps_raw_level_until_directional_tick(short, expected):
    snapshot = _causal_tick_boundary_snapshot(short)
    setup = api._build_structured_trade_setup(
        "SHORT" if short else "LONG", 100., 1., 90., 110., 110., 90.,
        structure_snapshot=snapshot, require_causal_structure=True)
    assert setup is not None
    # A six-decimal pre-round used to move the buffer edge onto the wrong
    # cent before the actual directional order-price quantization.
    assert setup["stop"] == expected, setup


@pytest.mark.parametrize("short", [False, True])
def test_causal_native_plan_does_not_use_rounded_legacy_support_resistance(short):
    snapshot = _causal_tick_boundary_snapshot(short)
    side = "SHORT" if short else "LONG"
    baseline = api._build_structured_trade_setup(
        side, 100., 1., 90., 110., 110., 90.,
        structure_snapshot=snapshot, require_causal_structure=True)
    deliberately_wrong = api._build_structured_trade_setup(
        side, 100., 1., 99.99, 100.01, 100.02, 99.98,
        structure_snapshot=snapshot, require_causal_structure=True)
    # These legacy parameters are display compatibility, not causal zones.
    for key in ("entry", "stop", "tp1", "tp2", "barrier_gate", "nearest_barrier"):
        assert deliberately_wrong[key] == baseline[key], key
