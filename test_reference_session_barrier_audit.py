"""Informational close labels may never mutate actual support/resistance.

This does not exempt the signal candle's real PDH/PDL from the conservative
first-barrier policy. It only makes adding a price label structurally neutral.
"""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json

import pytest

from modules.level_zones import (
    LevelEvidence, build_level_zones, build_structure_snapshot,
    classify_for_trade, select_trade_structure,
)


BASE = datetime(2026, 9, 20, 20, tzinfo=timezone.utc)


def _evidence(name, price, *, role, family="horizontal_swing", timeframe="1D", day=0):
    stamp = BASE + timedelta(days=day)
    return LevelEvidence(
        family, name, timeframe, price, price, stamp, stamp, stamp,
        provenance={"role_hint": role},
    )


def _reference(name, price, *, day=1):
    return _evidence(name, price, role="reference", family="session",
                     timeframe="1W" if name == "PWC" else "1D", day=day)


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("name", ["PDC", "PWC"])
@pytest.mark.parametrize("retest", [False, True])
def test_close_reference_cannot_erase_break_or_retest_certificate(direction, name, retest):
    sign = 1 if direction == "LONG" else -1
    role = "resistance" if direction == "LONG" else "support"
    price = 100 + sign * 0.035
    end_day = 2 if retest else 1
    bars = [{
        "open_time": BASE + timedelta(days=day - 1, hours=18),
        "close_time": BASE + timedelta(days=day),
        "open": 100, "high": 100.1, "low": 99.9,
        "close": price, "volume": 1000,
    } for day in range(1, end_day + 1)]
    real = _evidence("actual_boundary", 100, role=role)

    def build(evidence):
        return build_structure_snapshot(
            {"1D": bars}, symbol="SYNTH", asset_class="stock", horizon="swing",
            as_of=BASE + timedelta(days=end_day), current_price=price,
            tick_size=.01, external_evidence=evidence, include_session_levels=False,
            pivot_left=100, pivot_right=100,
        )

    before = build([real])
    after = build([real, _reference(name, price, day=end_day)])
    original = next(zone for zone in before.zones if "actual_boundary" in zone.source_names)
    retained = next(zone for zone in after.zones if "actual_boundary" in zone.source_names)
    assert original.break_state == ("reclaimed" if retest else "break_confirmed")
    # Identity, bounds, source/touch counts, strength, causality and the bound
    # proof are all invariant. Serialization cannot reintroduce a mixed zone.
    assert retained.to_dict() == original.to_dict()
    assert json.loads(json.dumps(retained.to_dict())) == json.loads(json.dumps(original.to_dict()))
    references = [zone for zone in after.zones if name in zone.source_names]
    assert len(references) == 1
    assert references[0].source_names == (name,)
    directional = classify_for_trade(after, entry=price, direction=direction)
    assert references[0] not in directional.opposing_barriers
    assert references[0] not in directional.invalidation_candidates


@pytest.mark.parametrize("name", ["PDC", "PWC"])
def test_reference_cannot_bridge_disjoint_structural_zones(name):
    real = [_evidence("left", 99.9, role="support"),
            _evidence("right", 100.1, role="resistance")]
    reference = _reference(name, 100)
    # A larger reference-timeframe noise width must not bridge real evidence.
    real = [replace(item, timeframe="4H") for item in real]
    atr = {reference.timeframe: 1, "4H": .1}
    before = build_level_zones(real, reference_price=100, tick_size=.01, atr_by_timeframe=atr)
    after = build_level_zones([*real, reference], reference_price=100, tick_size=.01, atr_by_timeframe=atr)
    actual = [zone for zone in after if "left" in zone.source_names or "right" in zone.source_names]
    assert [zone.to_dict() for zone in actual] == [zone.to_dict() for zone in before]
    assert len(actual) == 2
    assert any(name in zone.source_names for zone in after)


@pytest.mark.parametrize("name", ["PDC", "PWC"])
def test_reference_cannot_inflate_real_touch_strength_or_confirmation(name):
    real = _evidence("actual_boundary", 100, role="resistance")
    reference = replace(_reference(name, 100), strength=99,
                        provenance={"role_hint": "reference", "touch_count": 999})
    before = build_level_zones([real], reference_price=101, tick_size=.01)[0]
    after = build_level_zones([real, reference], reference_price=101, tick_size=.01)
    actual = next(zone for zone in after if "actual_boundary" in zone.source_names)
    assert actual.to_dict() == before.to_dict()


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_latest_real_session_extreme_remains_a_hard_barrier(direction):
    cutoff = BASE + timedelta(days=1)
    bar = {"open_time": cutoff - timedelta(hours=6, minutes=30), "close_time": cutoff,
           "open": 100, "high": 100 if direction == "LONG" else 110,
           "low": 90 if direction == "LONG" else 100, "close": 100, "volume": 1000}
    snapshot = build_structure_snapshot({"1D": [bar]}, symbol="SYNTH",
        asset_class="stock", horizon="swing", as_of=cutoff, current_price=100, tick_size=.01)
    directional = classify_for_trade(snapshot, entry=100, direction=direction)
    decision = select_trade_structure(directional, stop=95 if direction == "LONG" else 105)
    assert decision.status == "WAIT_BREAK_RECLAIM"
    assert decision.barrier_r == 0
    assert decision.nearest_barrier.source_names == (("PDH",) if direction == "LONG" else ("PDL",))
    assert any(zone.source_names == ("PDC",) for zone in snapshot.zones)


@pytest.mark.parametrize("real_name,role", [("PDH", "resistance"), ("PDL", "support")])
def test_session_extreme_is_not_exempt_even_with_reference_hint(real_name, role):
    # A contradictory reference hint must never disguise an actual PDH/PDL.
    real = _evidence("actual_boundary", 100, role=role)
    mislabeled = _evidence(real_name, 100.03, role="reference", family="session", day=1)
    zones = build_level_zones([real, mislabeled], reference_price=101, tick_size=.01)
    assert len(zones) == 1
    assert set(zones[0].source_names) == {"actual_boundary", real_name}
    assert zones[0].upper > 100.02


def test_reference_partition_is_permutation_and_quote_invariant():
    evidence = [_evidence("real", 100, role="resistance"),
                _reference("PDC", 100.03), _reference("PWC", 100.02)]
    first = build_level_zones(evidence, reference_price=101, tick_size=.01)
    reversed_input = build_level_zones(reversed(evidence), reference_price=101, tick_size=.01)
    assert first == reversed_input
    other_quote = build_level_zones(evidence, reference_price=99, tick_size=.01)
    assert [(z.zone_id, z.lower, z.upper, z.evidence) for z in first] == [
        (z.zone_id, z.lower, z.upper, z.evidence) for z in other_quote]
