"""Structure-first mail projection: absent evidence must remain absent.

API imports must run through the repository's isolated offline QA harness.
These regressions do not contact providers, send mail or relax release gates.
"""
import pytest
from datetime import datetime, timedelta, timezone

import api
from modules.trade_levels import normalize_alert_trade_levels
from modules.level_zones import LevelEvidence, build_structure_snapshot


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_mail_projection_does_not_invent_entry_stop_or_targets_from_current_price(direction):
    levels = api._alert_trade_levels({"Preis": 100.0, "direction": direction})
    assert levels["entry"] is None
    assert levels["stop"] is None
    assert levels["tp1"] is None and levels["tp2"] is None
    assert levels["valid"] is False
    assert levels["estimated"] is False
    assert levels["source"] == "incomplete"


@pytest.mark.parametrize("direction,stop,tp1", [("LONG", 95.0, 110.0), ("SHORT", 105.0, 90.0)])
def test_mail_projection_preserves_real_partial_levels_without_fabricating_missing_runner(direction, stop, tp1):
    row = {"Preis": 100.0, "direction": direction, "Entry": 100.0,
           "StopLoss": stop, "TP1": tp1}
    levels = api._alert_trade_levels(row)
    assert (levels["entry"], levels["stop"], levels["tp1"]) == (100.0, stop, tp1)
    assert levels["tp2"] is None
    assert levels["estimated"] is False
    assert levels["source"] == "incomplete"
    assert api._alert_trade_plan_ok(row, require_native_levels=True) is False


def test_strict_normalizer_flag_also_blocks_price_fallback_not_only_stop_targets():
    levels = normalize_alert_trade_levels({"direction": "LONG"}, price_fallback=100., allow_estimated=False)
    assert levels["entry"] is None
    assert levels["estimated"] is False
    assert levels["sources"]["entry"] is None


@pytest.mark.parametrize("direction,stop,tp1", [("LONG", 95., 110.), ("SHORT", 105., 90.)])
def test_background_mail_normalization_also_keeps_missing_fields_absent(direction, stop, tp1):
    import bg_service
    bare = bg_service._alert_trade_levels({"Preis": 100., "direction": direction})
    assert all(bare[field] is None for field in ("entry", "stop", "tp1", "tp2"))
    partial = bg_service._alert_trade_levels({"Preis": 100., "Entry": 100.,
        "StopLoss": stop, "TP1": tp1, "direction": direction})
    assert (partial["entry"], partial["stop"], partial["tp1"]) == (100., stop, tp1)
    assert partial["tp2"] is None and partial["estimated"] is False


@pytest.mark.parametrize("direction,stop,tp1,tp2", [("LONG", 95., 110., 120.), ("SHORT", 105., 90., 80.)])
def test_complete_structural_prices_survive_strict_mail_projection_unchanged(direction, stop, tp1, tp2):
    levels = api._alert_trade_levels({"direction": direction, "Entry": 100., "StopLoss": stop, "TP1": tp1, "TP2": tp2})
    assert (levels["entry"], levels["stop"], levels["tp1"], levels["tp2"]) == (100., stop, tp1, tp2)
    assert levels["native"] is True and levels["estimated"] is False
    assert levels["valid"] is True


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_trigger_rejection_keeps_recognized_causal_levels_without_fallback_plan(monkeypatch, direction):
    base = datetime(2026, 4, 1, tzinfo=timezone.utc)
    cutoff = base + timedelta(days=1)
    mirror = lambda value: 200 - value if direction == "SHORT" else value
    evidence = []
    for value, origin in [(95., "support"), (101., "resistance"), (115., "resistance"), (125., "resistance")]:
        role = ("resistance" if origin == "support" else "support") if direction == "SHORT" else origin
        price = mirror(value)
        evidence.append(LevelEvidence("horizontal_swing", f"confirmed_{role}_{price}", "1D",
            price, price, base, base, cutoff, provenance={"role_hint": role}))
    bars = [{"open_time": base, "close_time": cutoff, "open": 100., "high": 100.5,
             "low": 99.5, "close": 100., "volume": 1000.}]
    price = mirror(102.)
    snapshot = build_structure_snapshot({"1D": bars}, symbol="TEST", asset_class="stock",
        horizon="swing", as_of=cutoff, current_price=price, tick_size=.01,
        external_evidence=evidence, include_session_levels=False)
    # These collaborators perform provider I/O; retain the real native-plan
    # builder and level adapter while supplying verified offline evidence.
    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_build_stock_level_snapshot", lambda *args, **kwargs: snapshot)
    row = {"ticker": "TEST", "Preis": price, "direction": direction}
    context = {"ticker": "TEST", "daily_bars": bars, "history_metrics": {"atr14": 2.},
        "price": price, "prev_atr_pct": 2., "day_high": 100.5, "day_low": 99.5,
        "close_pos": .5, "bid": price - .01, "ask": price + .01,
        "analysis_as_of": cutoff, "direction": direction}
    api._enrich_stock_strategy_native_plan(row, context, {})
    assert row["Level_Structure"] == snapshot.to_dict()
    assert len(row["Level_Structure"]["zones"]) == 4
    assert row["native_plan_reason"] == ("crossed_resistance_unconfirmed" if direction == "LONG" else "crossed_support_unconfirmed")
    assert not row.get("trade_setup")
    assert not any(field in row for field in ("Entry", "StopLoss", "TP1", "TP2"))
    levels = api._alert_trade_levels(row)
    assert levels["estimated"] is False
    assert levels["valid"] is False
    assert levels["entry"] is None and levels["stop"] is None
    assert api._alert_trade_plan_ok(row) is False
