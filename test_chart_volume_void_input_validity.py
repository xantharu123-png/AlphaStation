"""Offline volume-void geometry checks using genuine synthetic OHLCV only."""
from copy import deepcopy
import json
import math

import pytest

from modules.volume_analysis import calculate_volume_profile, find_volume_voids_for_chart


def _bars(count=20, *, scale=1.0, volume=100.0, legacy=False):
    bars = []
    for index in range(count):
        low, high = (9.0, 10.0) if index % 2 == 0 else (20.0, 21.0)
        row = dict(low=low * scale, high=high * scale, volume=volume)
        if not legacy:
            row.update(open=(low + .2) * scale, close=(low + .8) * scale)
        bars.append(row)
    return bars


@pytest.mark.parametrize("bad", [
    dict(high=30000, low=29000, volume=0),
    dict(high=30000, low=29000, volume=-1),
    dict(high=30000, low=29000, volume=float("nan")),
    dict(high=30000, low=29000, volume=float("inf")),
    dict(high=30000, low=29000, volume=True),
    dict(high=30000, low=29000, volume=None),
    dict(high=30000, low=29000),
    dict(high=float("inf"), low=29000, volume=1000),
    dict(high=30000, low=float("nan"), volume=1000),
    dict(high=True, low=.1, volume=1000),
    dict(high=30000, low=0, volume=1000),
    dict(high=10.0, low=20.0, volume=1e12),
    dict(open=0, high=30000, low=29000, close=29500, volume=1000),
    dict(open=100, high=30000, low=29000, close=29500, volume=1000),
    dict(open=29900, high=30000, low=29000, close=40000, volume=1000),
    dict(open=None, high=30000, low=29000, close=29500, volume=1000),
    dict(open=29900, high=30000, low=29000, close=float("nan"), volume=1000),
    dict(open=True, high=30000, low=29000, close=29500, volume=1000),
    None,
])
def test_noncontributing_and_invalid_bars_cannot_move_chart_void_geometry(bad):
    expected = find_volume_voids_for_chart(_bars(), num_bins=20)
    assert expected
    assert find_volume_voids_for_chart(_bars() + [bad], num_bins=20) == expected


@pytest.mark.parametrize("flags", [
    {"is_closed": False}, {"complete": False}, {"completed": 0},
    {"final": "open"}, {"is_closed": True, "complete": False},
])
def test_explicit_open_bar_cannot_supply_chart_void_bounds_or_volume(flags):
    baseline = find_volume_voids_for_chart(_bars())
    open_bar = dict(open=29500, high=30000, low=29000, close=29900,
                    volume=1e12, **flags)
    assert find_volume_voids_for_chart(_bars() + [open_bar]) == baseline


def test_minimum_ten_counts_genuine_bars_without_inheriting_native_twenty_gate():
    assert find_volume_voids_for_chart(_bars(10, legacy=True))
    assert find_volume_voids_for_chart(_bars(9, legacy=True)) == []
    assert find_volume_voids_for_chart(
        _bars(9, legacy=True) + [dict(high=30000, low=29000, volume=0)]
    ) == []


def test_chart_voids_match_native_profile_lvns_and_keep_valid_doji_volume():
    bars = _bars(20, legacy=True) + [dict(high=15.0, low=15.0, volume=777.123456789)]
    original = deepcopy(bars)
    profile = calculate_volume_profile(bars, num_bins=20)
    assert profile is not None
    assert math.fsum(bin["volume"] for bin in profile["bins"]) == pytest.approx(2777.123456789)
    expected = [dict(price_low=node["low"], price_high=node["high"],
                     strength=round(max(0, min(1, 1 - node["volume"] / profile["avg_volume"])), 2))
                for node in profile["lvns"]]
    assert find_volume_voids_for_chart(bars, num_bins=20) == expected
    assert bars == original


def test_microprices_preserve_real_void_boundaries_without_two_decimal_rounding():
    bars = _bars(scale=1e-7)
    actual = find_volume_voids_for_chart(bars, num_bins=20)
    assert actual
    profile = calculate_volume_profile(bars, num_bins=20)
    assert [(zone["price_low"], zone["price_high"]) for zone in actual] == [
        (node["low"], node["high"]) for node in profile["lvns"]
    ]
    assert all(0 < zone["price_low"] < zone["price_high"] < 1e-5 for zone in actual)


def test_finite_extreme_price_geometry_and_fractional_volume_remain_serializable():
    bars = _bars(scale=7e306, volume=1e290)
    voids = find_volume_voids_for_chart(bars, num_bins=20)
    assert voids
    assert all(math.isfinite(zone[key]) for zone in voids
               for key in ("price_low", "price_high", "strength"))
    json.dumps(voids, allow_nan=False)


def test_unrepresentable_total_volume_does_not_fabricate_finite_voids():
    assert find_volume_voids_for_chart(_bars(volume=1e308)) == []


def test_unresolvable_bin_boundaries_fail_closed_instead_of_zero_width_voids():
    low = 1e308
    high = math.nextafter(low, math.inf)
    bars = [dict(open=low, high=high, low=low, close=high, volume=100)
            for _ in range(20)]
    assert find_volume_voids_for_chart(bars, num_bins=20) == []


def test_all_flat_input_stays_unavailable_without_invented_price_range():
    assert find_volume_voids_for_chart([dict(high=100, low=100, volume=100)
                                       for _ in range(20)]) == []
