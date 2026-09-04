"""Regression cases from the September audit; no live data or scanner run."""
from datetime import datetime, timezone

import pytest

from api import _build_structured_trade_setup
from modules.level_zones import LevelEvidence, StructureSnapshot, build_level_zones


def _snapshot():
    stamp = datetime(2026, 8, 30, tzinfo=timezone.utc)
    evidence = [
        LevelEvidence(
            source_family="horizontal_swing", source_name="confirmed_swing_" + kind,
            timeframe="1D", lower=price, upper=price, observed_at=stamp,
            confirmed_at=stamp, data_cutoff_at=stamp, strength=1.4,
            provenance={"role_hint": role},
        )
        for price, kind, role in ((90, "low", "support"), (110, "high", "resistance"))
    ]
    return StructureSnapshot(
        symbol="AUDIT", asset_class="stock", horizon="swing", as_of=stamp,
        current_price=100,
        zones=build_level_zones(evidence, reference_price=100, atr_by_timeframe={"1D": 1}),
        atr_by_timeframe={"1D": 1}, completed_bar_counts={"1D": 60},
    )


@pytest.mark.parametrize("side,stop", [("LONG", 89.6), ("SHORT", 110.4)])
def test_atr_fallback_cannot_beat_existing_causal_invalidation(side, stop):
    plan = _build_structured_trade_setup(
        side, 100, 1, 90, 110, 110, 90,
        structure_snapshot=_snapshot(), require_causal_structure=True,
    )
    assert plan is not None
    assert plan["stop"] == pytest.approx(stop)
    assert not plan["stop_is_projection"]
    assert "ATR" not in plan["stop_source"]
    assert plan["rr_tp1"] == pytest.approx(0.95)
    assert plan["structure_status"] == "WAIT_BREAK_RECLAIM"
    assert plan["target_quality"] == "STRUCTURAL_TP1_PROJECTION_TP2"


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_uncorroborated_legacy_point_does_not_replace_causal_zone(side):
    plan = _build_structured_trade_setup(
        side, 100, 1, 98, 102, 102, 98,
        structure_snapshot=_snapshot(), require_causal_structure=True,
    )
    assert plan is not None
    assert plan["stop"] == pytest.approx(89.6 if side == "LONG" else 110.4)


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_atr_only_legacy_plan_is_not_labelled_structural(side):
    plan = _build_structured_trade_setup(side, 100, 1, None, None, None, None)
    assert plan is not None
    assert plan["stop_is_projection"]
    assert plan["target_quality"] == "PROJECTION_ONLY_NO_CONFIRMED_BARRIER"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1, 0])
def test_invalid_entry_fails_closed(value):
    assert _build_structured_trade_setup("LONG", value, 1, 90, 110, 110, 90) is None


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
def test_reported_risk_and_rr_use_the_returned_prices(side):
    plan = _build_structured_trade_setup(side, 12.003, .417, 11.3, 13.14, 13.56, 11.1)
    assert plan is not None
    risk = abs(plan["entry"] - plan["stop"])
    assert plan["risk"] == pytest.approx(risk, abs=.00001)
    assert plan["rr_tp1"] == pytest.approx(round(abs(plan["tp1"] - plan["entry"]) / risk, 2))


def test_causal_plan_carries_separate_stop_and_target_provenance():
    plan = _build_structured_trade_setup("LONG", 100, 1, 90, 110, 110, 90, structure_snapshot=_snapshot())
    assert plan["level_quality"]["stop"]["quality"] == "confirmed_zone"
    assert plan["level_quality"]["tp1"]["quality"] == "confirmed_zone"
    assert plan["level_quality"]["tp2"]["quality"] == "projection"


@pytest.mark.parametrize("side,support,resistance", [("LONG", 1.1789, 1.3), ("SHORT", 1.1, 1.2211)])
def test_rounding_keeps_stop_outside_invalidation(side, support, resistance):
    plan = _build_structured_trade_setup(side, 1.2, .01, support, resistance, 1.4, 1.0)
    assert plan is not None
    assert plan["stop"] < support if side == "LONG" else plan["stop"] > resistance
    assert plan["tp1"] <= resistance if side == "LONG" else plan["tp1"] >= support
