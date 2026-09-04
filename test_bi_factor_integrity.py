"""OHLC counterexamples for BI factor evidence, without forced green flags."""
import pytest

from modules.patterns import analyze_breakout_imminent, detect_liquidity_levels
from modules.bi_trade_plan import build_bi_trade_plan, bi_consolidation_days
from test_bi_deep_fixes_patterns import gen_liq_series


def _bar(price, half=1.0):
    return {"open": price, "high": price + half, "low": price - half,
            "close": price, "volume": 1_000_000}


def test_gap_expansion_is_not_atr_squeeze():
    bars = [_bar(100) for _ in range(45)] + [_bar(p, 0.1) for p in [105, 100, 105, 100, 105]]
    check = analyze_breakout_imminent(bars).indicator_checks[0]
    assert check["available"] and not check["passed"]
    assert check["points"] == 0


def test_genuine_true_range_contraction_remains_green():
    bars = [_bar(100, 2) for _ in range(45)] + [_bar(100, 0.4) for _ in range(5)]
    check = analyze_breakout_imminent(bars).indicator_checks[0]
    assert check["passed"] and check["points"] == 6


@pytest.mark.parametrize("mirror", [False, True])
def test_consumed_pool_cannot_reappear_when_price_returns(mirror):
    bars = gen_liq_series(101.5)
    bars[16] = {"open": 100, "high": 103.5, "low": 99.8, "close": 103, "volume": 1_000_000}
    if mirror:
        bars = [{**b, "open": 200-b["open"], "high": 200-b["low"],
                 "low": 200-b["high"], "close": 200-b["close"]} for b in bars]
    pools = detect_liquidity_levels(bars)["sellside" if mirror else "buyside"]
    assert not any(p["level"] == pytest.approx(98.5 if mirror else 101.5) for p in pools)


def test_unconsumed_separate_touches_still_form_pool():
    pools = detect_liquidity_levels(gen_liq_series(101.5))["buyside"]
    assert any(p["level"] == 101.5 and p["touches"] == 2 and p["state"] == "active" for p in pools)


def test_flat_plateau_does_not_manufacture_twenty_touches():
    result = detect_liquidity_levels([_bar(100) for _ in range(50)])
    assert result["buyside"] == result["sellside"] == []


def test_down_leg_retracement_not_renamed_bullish_fibonacci():
    bars = [_bar(99.8, 0.2) for _ in range(50)]
    bars[22] = {**_bar(119), "high": 120}
    bars[35] = {**_bar(90), "low": 80}
    bars[-1] = _bar(104.72, 0.2)
    check = analyze_breakout_imminent(bars).indicator_checks[17]
    assert check["available"] and not check["passed"] and check["points"] == 0


def test_shared_plan_uses_same_adaptive_window_as_analyzer():
    bars = [_bar(90) for _ in range(40)] + [_bar(100) for _ in range(10)]
    result = analyze_breakout_imminent(bars)
    plan = build_bi_trade_plan(bars, direction="long", apply_structure=False)
    assert result.consolidation_days == bi_consolidation_days(bars) == plan["range_days"] == 10
    assert plan["accepted"] and plan["RangeHigh"] == 101 and plan["entry_method"] == "stop_breakout"


def test_shared_plan_requires_explicit_causal_structure_cutoff():
    plan = build_bi_trade_plan([_bar(100) for _ in range(50)], direction="long")
    assert not plan["accepted"] and plan["reason"] == "structure_cutoff_missing"


@pytest.mark.parametrize("gate", [{"structure_status": "WAIT_BREAK_RECLAIM"},
                                  {"structure_status": "REJECT"},
                                  {"barrier_gate_active": True},
                                  {"tp1_is_projection": True}])
def test_shared_plan_cannot_release_numeric_rr_through_structure_block(monkeypatch, gate):
    import modules.bi_trade_plan as core
    monkeypatch.setattr(core, "build_vrvp_structure", lambda *a, **k: {})
    monkeypatch.setattr(core, "apply_vrvp_to_trade_setup", lambda plan, *a, **k: {**plan, **gate})
    plan = core.build_bi_trade_plan([_bar(100) for _ in range(50)], direction="long", as_of="2026-09-04T20:00:00Z")
    assert not plan["accepted"] and plan["reason"] == "structural_barrier_blocked"
