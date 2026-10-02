"""Causal level clusters need shared local overlap, not a long price chain."""
from datetime import datetime, timezone
from itertools import permutations

import pytest

from modules.level_zones import LevelEvidence, build_level_zones


NOW = datetime(2026, 7, 30, 20, tzinfo=timezone.utc)


def evidence(price, *, lower=None, upper=None, name=None):
    return LevelEvidence(source_family="horizontal_swing", source_name=name or str(price),
        timeframe="1D", lower=price if lower is None else lower,
        upper=price if upper is None else upper, observed_at=NOW, confirmed_at=NOW,
        data_cutoff_at=NOW, provenance={"role_hint": "support"})


@pytest.mark.parametrize("mirror", [False, True])
def test_transitive_chain_cannot_absorb_distant_supports_or_resistances(mirror):
    centers = [100+i*1.5 for i in range(21)]
    if mirror:
        centers = [250-p for p in centers]
    rows = [evidence(p) for p in centers]
    zones = build_level_zones(rows, reference_price=140, atr_by_timeframe={"1D": 10})
    assert len(zones) > 1
    assert sum(len(z.evidence) for z in zones) == len(rows)
    # A common intersection mathematically limits the union to at most twice
    # the widest individual interval, here 2 * (2 * 0.1 ATR). No outcome/quote
    # optimization or new arbitrary risk tolerance is introduced.
    assert all(z.upper-z.lower <= 4+1e-12 for z in zones)
    for zone in zones:
        assert max(e.midpoint-1 for e in zone.evidence) <= min(e.midpoint+1 for e in zone.evidence)


def test_proximal_overlap_remains_one_zone_without_clipping_any_observed_level():
    rows = [evidence(100), evidence(101.5), evidence(102)]
    zones = build_level_zones(rows, reference_price=110, atr_by_timeframe={"1D": 10})
    assert len(zones) == 1
    assert zones[0].lower == 99 and zones[0].upper == 103
    assert len(zones[0].evidence) == 3


def test_local_cluster_geometry_and_membership_are_input_order_and_quote_invariant():
    rows = [evidence(100), evidence(101.5), evidence(103)]
    signatures = set()
    for ordered in permutations(rows):
        for quote in (90, 102, 110):
            zones = build_level_zones(ordered, reference_price=quote, atr_by_timeframe={"1D": 10})
            signatures.add(tuple((z.zone_id, z.lower, z.upper) for z in zones))
    assert len(signatures) == 1
    assert len(next(iter(signatures))) == 2


def test_genuinely_wide_observed_zone_is_not_narrowed_to_improve_a_trade():
    rows = [evidence(105, lower=90, upper=120, name="actual_range"), evidence(100), evidence(110)]
    zones = build_level_zones(rows, reference_price=130, atr_by_timeframe={"1D": 10})
    actual = next(z for z in zones if any(e.source_name == "actual_range" for e in z.evidence))
    assert actual.lower <= 90 and actual.upper >= 120
    assert sum(len(z.evidence) for z in zones) == 3


def test_old_transitive_zone_model_cannot_create_a_new_structure_reminder():
    from modules.scanner_reminders import anchor_from_row
    from test_scanner_structure_reminders import server_row, epoch
    row = server_row()
    row["level_structure"]["model"] = "causal_level_zones_v1"
    with pytest.raises(ValueError, match="server_daily_structure_missing"):
        anchor_from_row(row, ticker="XYZ", direction="LONG", condition="retest",
                        now=epoch("2026-09-16T21:00:00Z"))
