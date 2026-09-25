"""A new daily extreme cannot erase proof at an unchanged breakout edge."""
from datetime import datetime, timedelta, timezone

import pytest

from modules.level_zones import LevelEvidence, build_structure_snapshot, classify_for_trade


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_signal_session_opposite_extreme_preserves_historical_break(direction):
    cutoff = datetime(2026, 9, 24, 20, tzinfo=timezone.utc)
    long = direction == "LONG"
    entry = 102.0 if long else 98.0
    old = LevelEvidence(
        "horizontal_swing", "confirmed_swing_high" if long else "confirmed_swing_low",
        "1D", 100.0, 100.0, cutoff - timedelta(days=8),
        cutoff - timedelta(days=6), cutoff,
        provenance={"role_hint": "resistance" if long else "support"},
    )
    bars = [{"open_time": cutoff - timedelta(hours=6, minutes=30),
             "close_time": cutoff, "open": 100.0,
             "high": 103.0 if long else 100.1,
             "low": 99.9 if long else 97.0, "close": entry,
             "volume": 1_000_000, "is_closed": True}]

    def snapshot(sessions):
        return build_structure_snapshot(
            {"1D": bars}, symbol="SYNTHETIC", asset_class="stock", horizon="swing",
            as_of=cutoff, current_price=entry, tick_size=0.01,
            atr_by_timeframe={"1D": 2.0}, external_evidence=[old],
            include_session_levels=sessions,
        )

    before, after = snapshot(False), snapshot(True)
    original = next(z for z in before.zones if old.source_name in z.source_names)
    merged = next(z for z in after.zones if old.source_name in z.source_names)
    assert (original.lower, original.upper) == pytest.approx((99.8, 100.2))
    assert (merged.lower, merged.upper) == pytest.approx(
        (99.7, 100.2) if long else (99.8, 100.3))
    assert merged.break_state == original.break_state == "break_confirmed"
    proof = merged.break_reclaim_evidence
    assert proof.boundary == original.break_reclaim_evidence.boundary
    assert proof.zone_confirmed_at == old.confirmed_at
    assert proof.break_closed_at == proof.last_completed_at == cutoff
    assert proof.completed_bars_used == 1
    assert not proof.retest_observed  # The breakout bar itself is not a retest.
    assert merged.confirmed_at == cutoff  # Full geometry is still dated today.
    assert proof.reclaim_history.to_dict() == merged.reclaim_history.to_dict()
    assert proof.reclaim_history.to_dict()["model"] == "connected_role_boundary_v2"
    structure = classify_for_trade(after, entry=entry, direction=direction)
    assert merged in structure.invalidation_candidates
    assert merged not in structure.opposing_barriers
    # This correction does not waive separate genuine/current-session barriers.
    assert structure.opposing_barriers[0].source_names == ("PDH" if long else "PDL",)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("real_retest", [False, True])
def test_later_opposite_width_cannot_manufacture_an_old_retest(direction, real_retest):
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    long = direction == "LONG"
    old = LevelEvidence("horizontal_swing", "original", "1D", 100, 100,
                        base, base, base,
                        provenance={"role_hint": "resistance" if long else "support"})
    extra = LevelEvidence("horizontal_swing", "later_opposite", "1D",
                          99.9 if long else 100.1, 99.9 if long else 100.1,
                          base + timedelta(days=3), base + timedelta(days=3),
                          base + timedelta(days=3),
                          provenance={"role_hint": "support" if long else "resistance"})
    rows = []
    for day, near in enumerate([0.5, 0.25 if real_retest else 0.29, 0.4], 1):
        rows.append({"open_time": base + timedelta(days=day-1),
                     "close_time": base + timedelta(days=day),
                     "open": 100.8 if long else 99.2, "close": 100.8 if long else 99.2,
                     "high": 101.0 if long else 100-near,
                     "low": 100+near if long else 99.0, "volume": 1000})

    def zone(evidence):
        snapshot = build_structure_snapshot(
            {"1D": rows}, symbol="TEST", asset_class="stock", horizon="swing",
            as_of=base + timedelta(days=3), current_price=100.8 if long else 99.2,
            tick_size=0.01, atr_by_timeframe={"1D": 2},
            include_session_levels=False, external_evidence=evidence,
            pivot_left=100, pivot_right=100,
        )
        return snapshot.zones[0]

    before, after = zone([old]), zone([old, extra])
    assert after.upper - after.lower > before.upper - before.lower
    assert dict(after.reclaim_retest_widths)[direction] == pytest.approx(0.4)
    assert after.break_state == before.break_state == ("reclaimed" if real_retest else "break_confirmed")
    assert after.break_reclaim_evidence.retest_observed is real_retest
