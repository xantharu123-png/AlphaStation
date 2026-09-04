"""Causal trading-level regression cases from the September 2026 audit."""
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import LevelEvidence, build_level_zones, build_structure_snapshot
from modules.vrvp_levels import apply_vrvp_to_trade_setup, trade_level_quality


@pytest.mark.parametrize("direction,kind,role,quotes,closes,low,high", [
    ("LONG", "high", "resistance", (100.1, 100.3), (100.12, 100.15), 100.05, 100.17),
    ("SHORT", "low", "support", (99.9, 99.7), (99.88, 99.85), 99.83, 99.95),
])
def test_live_quote_cannot_manufacture_reclaim_inside_actual_zone(direction, kind, role, quotes, closes, low, high):
    stamp = datetime(2026, 9, 4, 9, tzinfo=timezone.utc)
    pivot = LevelEvidence(
        source_family="horizontal_swing", source_name="confirmed_swing_" + kind,
        timeframe="1D", lower=100, upper=100, observed_at=stamp,
        confirmed_at=stamp, data_cutoff_at=stamp, strength=1.4,
        provenance={"role_hint": role},
    )
    bars = [
        {"open_time": stamp + timedelta(minutes=i + 1), "open": closes[0],
         "high": high, "low": low, "close": close, "volume": 1000}
        for i, close in enumerate(closes)
    ]
    zones = []
    for quote in quotes:
        snapshot = build_structure_snapshot(
            {"1M": bars}, symbol="AUDIT", asset_class="stock", horizon="swing",
            as_of=stamp + timedelta(minutes=4), current_price=quote,
            atr_by_timeframe={"1D": 2}, external_evidence=[pivot],
            include_session_levels=False,
        )
        zones.append(snapshot.zones[0])
    assert zones[0].zone_id == zones[1].zone_id
    for zone in zones:
        assert (zone.lower, zone.upper) == (99.8, 100.2)
        assert zone.break_state == "intact"
        assert not zone.break_reclaim_evidence or zone.break_reclaim_evidence.state != "RECLAIMED"


def test_clustering_membership_and_bounds_do_not_depend_on_live_quote():
    stamp = datetime(2026, 9, 4, tzinfo=timezone.utc)
    evidence = [LevelEvidence(
        source_family="horizontal_swing", source_name=str(price), timeframe="1D",
        lower=price, upper=price, observed_at=stamp, confirmed_at=stamp, data_cutoff_at=stamp,
    ) for price in (99.99, 100.01)]
    groups = [build_level_zones(evidence, reference_price=price, tick_size=0.01) for price in (99, 100, 101)]
    assert all(len(zones) == 1 for zones in groups)
    assert len({(zones[0].zone_id, zones[0].lower, zones[0].upper) for zones in groups}) == 1
    assert [zones[0].side_at_reference for zones in groups] == ["resistance", "overlap", "support"]


def _causal_level(price, low, high):
    return {
        "price": price, "source": "VRVP POC", "kind": "POC", "source_family": "vrvp",
        "timeframe": "1D", "independence_key": "test-profile", "zone_id": "test-zone",
        "zone_low": low, "zone_high": high, "confirmed_at": "2026-08-01T00:00:00Z",
        "data_cutoff_at": "2026-08-02T00:00:00Z", "causal_structure_validated": True,
    }


@pytest.mark.parametrize("side,price,low,high,old_stop,expected_stop", [
    ("LONG", 96, 95, 97, 95, 94.65), ("SHORT", 104, 103, 105, 105, 105.35),
])
def test_vrvp_stop_invalidates_outer_zone_edge(side, price, low, high, old_stop, expected_stop):
    level = _causal_level(price, low, high)
    profile = {"timeframe": "1D", "supports": [level] if side == "LONG" else [], "resistances": [level] if side == "SHORT" else []}
    result = apply_vrvp_to_trade_setup(
        {"entry": 100, "stop": old_stop, "tp1": 110 if side == "LONG" else 90, "tp2": 120 if side == "LONG" else 80},
        profile, direction=side, atr=1,
    )
    assert result["stop"] == expected_stop
    assert result["stop"] < low if side == "LONG" else result["stop"] > high
    assert result["stop_zone_low"] == low
    assert result["stop_zone_high"] == high
    assert result["stop_timeframe"] == "1D"
    assert result["level_quality"]["stop"]["quality"] == "confirmed_zone"


def test_vrvp_stop_never_tightens_to_zone_overlapping_entry():
    level = _causal_level(99, 97, 101)
    result = apply_vrvp_to_trade_setup(
        {"entry": 100, "stop": 97, "tp1": 110, "tp2": 120},
        {"timeframe": "1D", "supports": [level], "resistances": []}, direction="LONG", atr=1,
    )
    assert result["stop"] == 97
    assert result.get("stop_source") != "VRVP POC invalidation"


def test_vrvp_mixed_target_plan_discloses_projection_per_target():
    result = apply_vrvp_to_trade_setup(
        {"entry": 100, "stop": 98, "tp1": 108, "tp2": 112},
        {"timeframe": "1D", "supports": [], "resistances": [_causal_level(106, 105, 107)]},
        direction="LONG", atr=1,
    )
    assert result["target_quality"] == "STRUCTURAL_TP1_PROJECTION_TP2"
    assert result["tp2_is_projection"] is True
    assert result["level_quality"]["tp1"]["quality"] == "confirmed_zone"
    assert result["level_quality"]["tp2"]["quality"] == "projection"
    assert result["level_quality"]["stop"]["quality"] == "unverified"


def test_projection_flag_overrides_leftover_causal_metadata():
    quality = trade_level_quality({
        "tp2_is_projection": True, "tp2_zone_id": "old", "tp2_timeframe": "1D",
        "tp2_confirmed_at": "2026-08-01", "tp2_causal_structure_validated": True,
    })
    assert quality["tp2"]["quality"] == "projection"


def test_rounded_zero_risk_plan_is_explicitly_rejected():
    result = apply_vrvp_to_trade_setup(
        {"entry": 100, "stop": 99.999, "tp1": 108, "tp2": 112},
        {"timeframe": "1D", "supports": [], "resistances": [_causal_level(106, 105, 107)]},
        direction="LONG", atr=1,
    )
    assert result["structure_status"] == "REJECT"
    assert result["structure_reason"] == "rounded_trade_geometry_invalid"
    assert result["trade_action"] == "NO_TRADE"


def test_micro_price_fibonacci_levels_do_not_collapse_to_zero():
    from modules.fibonacci_levels import ConfirmedSwingLeg, fibonacci_payload_adapter
    stamp = datetime(2026, 9, 4, tzinfo=timezone.utc)
    leg = ConfirmedSwingLeg(
        leg_id="micro", direction="LONG", start_price=8e-11, end_price=12e-11,
        start_at=stamp - timedelta(days=4), end_at=stamp - timedelta(days=2),
        confirmed_at=stamp, data_cutoff_at=stamp, timeframe="1D",
        start_pivot_index=2, end_pivot_index=4, provenance={},
    )
    levels = fibonacci_payload_adapter(leg)["levels"]
    assert levels["0%"] == pytest.approx(12e-11, abs=1e-17)
    assert levels["100%"] == pytest.approx(8e-11, abs=1e-17)
    assert len(set(levels.values())) == len(levels)
    assert all(price > 0 for price in levels.values())
