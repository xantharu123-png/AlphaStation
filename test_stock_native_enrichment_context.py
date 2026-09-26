"""The deferred native builder must retain causal inputs and admission rank."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest

import api


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("has_snapshot", [False, True])
@pytest.mark.parametrize("has_plan", [False, True])
@pytest.mark.parametrize("daily_signal", [False, True])
def test_native_enrichment_preserves_exact_inputs_and_never_changes_rank(
    monkeypatch, direction, has_snapshot, has_plan, daily_signal,
):
    cutoff = datetime(2026, 9, 18, 20, 0, tzinfo=timezone.utc)
    history = [{"date": "2026-09-18", "close": 101.71234567}]
    execution = [{"timestamp": 1789750800, "close": 101.61234567}]
    metrics = {"atr14": 2.314159265, "support_1": 93.123456789,
               "resistance_1": 109.123456789, "high_20d": 108.123456789,
               "low_20d": 92.123456789, "range_pos": 72.123456789}
    context = {"ticker": "EXACT", "daily_bars": history, "history_metrics": metrics,
               "direction": direction, "price": 101.71234567, "prev_atr_pct": 2.91,
               "day_high": 102.923456789, "day_low": 96.987654321, "close_pos": .87234567,
               "bid": 101.70123456, "ask": 101.72123456, "analysis_as_of": cutoff}
    row = {"ticker": "EXACT", "score": 87, "grade": "A", "base_score": 86,
           "base_grade": "A", "Change_Pct": 4.123, "Support_1": 97.123456789,
           "Resistance_1": 110.123456789}
    if daily_signal:
        row.update(api.stock_swing.metadata("2026-09-18", context["price"]))
    ranking = {key: row[key] for key in ("score", "grade", "base_score", "base_grade", "Change_Pct")}
    before = deepcopy(context)
    calls = []

    class Snapshot:
        model = "causal_fixture"

        def to_dict(self):
            return {"model": self.model, "as_of": cutoff.isoformat()}

    snapshot = Snapshot() if has_snapshot else None

    def fetch(ticker, *, limit):
        assert ticker == "EXACT" and limit == 40
        calls.append("execution")
        return execution

    def build(bars, **kwargs):
        assert bars is history
        assert kwargs == {"symbol": "EXACT", "current_price": context["price"],
                          "direction": direction.upper(), "atr14": metrics["atr14"],
                          "as_of": cutoff, "four_hour_bars": execution,
                          "spread": context["ask"] - context["bid"],
                          "signal_session": "2026-09-18" if daily_signal else None}
        calls.append("structure")
        return snapshot

    def plan(*args, **kwargs):
        assert args[:3] == (direction.upper(), context["price"], metrics["atr14"])
        expected_support = api._round_trade_price(99.876543219) if has_snapshot else 97.123456789
        expected_resistance = api._round_trade_price(110.987654321) if has_snapshot else 110.123456789
        assert args[3:] == (expected_support, expected_resistance, metrics["high_20d"],
                            metrics["low_20d"], metrics["range_pos"])
        assert kwargs["structure_snapshot"] is snapshot
        assert kwargs["require_causal_structure"] is True
        kwargs["diagnostics"].update(status="accepted" if has_plan else "blocked",
                                      reason="fixture_ready" if has_plan else "first_opposing_barrier_before_minimum_rr")
        calls.append("plan")
        return {"entry": context["price"], "stop": 98.1, "tp1": 110.1,
                "tp2": 115.1, "barrier_gate": "RECLAIM_REQUIRED"} if has_plan else None

    def vrvp(bars, price, actual_direction, **kwargs):
        assert bars is history and price == context["price"] and actual_direction == direction.upper()
        assert kwargs == {"timeframe": "1D", "num_bins": 24, "min_bars": 30,
                          "lookback": 90, "as_of": cutoff, "date_session_context": "us_equity_regular"}
        calls.append("vrvp")
        return {"fixture": True}

    def completed_atr(bars, **kwargs):
        assert bars is history and kwargs == {"as_of": cutoff, "period": 14, "lookback": 90}
        return 2.718281828

    def apply(setup, structure, **kwargs):
        assert structure == {"fixture": True}
        assert kwargs == {"direction": direction.upper(), "asset_type": "stock_swing", "atr": 2.718281828}
        calls.append("apply_vrvp")
        return dict(setup, vrvp_applied=True)

    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", fetch)
    monkeypatch.setattr(api, "_build_stock_level_snapshot", build)
    monkeypatch.setattr(api, "legacy_level_adapter", lambda *a, **kw: {
        "supports": [{"price": 99.876543219}], "resistances": [{"price": 110.987654321}]})
    monkeypatch.setattr(api, "_build_structured_trade_setup", plan)
    monkeypatch.setattr(api, "build_vrvp_structure", vrvp)
    monkeypatch.setattr(api, "_completed_stock_daily_atr", completed_atr)
    monkeypatch.setattr(api, "apply_vrvp_to_trade_setup", apply)
    diagnostics = {}

    api._enrich_stock_strategy_native_plan(row, context, diagnostics)

    assert context == before
    assert {key: row[key] for key in ranking} == ranking
    assert calls == ["execution", "structure", "plan"] + (["vrvp", "apply_vrvp"] if has_plan else [])
    assert diagnostics["plan_build_counts"] == {row["native_plan_reason"]: 1}
    if has_plan:
        assert row["Entry"] == context["price"]
        assert row["barrier_gate"] == "RECLAIM_REQUIRED" and row["barrier_gate_active"] is True
    else:
        assert "trade_setup" not in row
        assert row["native_plan_reason"] == "first_opposing_barrier_before_minimum_rr"
