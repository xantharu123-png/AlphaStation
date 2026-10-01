"""Offline regression checks for genuine-volume profile input geometry."""
from copy import deepcopy
import json
import math

import pytest

from modules.volume_analysis import calculate_volume_profile, merge_lvn_bins


def _bars(count=24, *, volume=1000.):
    return [dict(open=101 + i % 3 * .1, high=102 + i % 3 * .1,
                 low=100 + i % 3 * .1, close=101.2 + i % 3 * .1,
                 volume=volume) for i in range(count)]


def _shape(profile):
    return {key: profile[key] for key in ("range_low", "range_high", "poc", "vah", "val", "bins")}


@pytest.mark.parametrize("bad", [
    dict(open=29900, high=30000, low=29000, close=29500, volume=0),
    dict(open=29900, high=30000, low=29000, close=29500, volume=-1),
    dict(open=29900, high=30000, low=29000, close=29500, volume=float("nan")),
    dict(open=29900, high=30000, low=29000, close=29500, volume=float("inf")),
    dict(open=29900, high=float("inf"), low=29000, close=29500, volume=1000),
    dict(open=101, high=102, low=float("nan"), close=101.2, volume=1000),
    dict(open=float("nan"), high=30000, low=29000, close=29500, volume=1000),
    dict(open=0, high=30000, low=29000, close=29500, volume=1000),
    dict(open=100, high=30000, low=29000, close=29500, volume=1000),
    dict(open=29900, high=30000, low=29000, close=float("inf"), volume=1000),
    dict(open=29900, high=29000, low=30000, close=29500, volume=1000),
    None,
])
def test_invalid_or_noncontributing_bar_cannot_warp_physical_profile_bounds(bad):
    baseline = calculate_volume_profile(_bars(), num_bins=24)
    assert baseline is not None
    actual = calculate_volume_profile(_bars() + [bad], num_bins=24)
    assert actual is not None
    assert _shape(actual) == _shape(baseline)
    assert actual["input_bar_count"] == 25
    assert actual["contributing_bar_count"] == 24
    assert actual["excluded_bar_count"] == 1
    assert actual["volume_coverage_ratio"] == pytest.approx(24 / 25)


def test_minimum_twenty_applies_to_genuine_contributing_bars_not_raw_rows():
    assert calculate_volume_profile(_bars(19) + [dict(high=10000, low=9000, volume=0)]) is None
    assert calculate_volume_profile(_bars(19) + [dict(high=10000, low=9000, open=1, close=9500, volume=1000)]) is None
    assert calculate_volume_profile(_bars(20)) is not None


def test_legacy_high_low_volume_only_input_keeps_doji_volume_without_inventing_ohlc():
    bars = [dict(high=101 + i % 5, low=100 + i % 5, volume=100) for i in range(20)]
    bars.append(dict(high=102.5, low=102.5, volume=777))
    before = deepcopy(bars)
    profile = calculate_volume_profile(bars, num_bins=20)
    assert profile is not None
    assert bars == before
    assert profile["contributing_bar_count"] == 21
    assert math.fsum(bin["volume"] for bin in profile["bins"]) == pytest.approx(2777)


def test_extreme_finite_prices_and_volumes_conserve_volume_with_finite_geometry():
    bars = [dict(open=1.2e308, high=1.6e308, low=1.0e308, close=1.4e308, volume=1e300)
            for _ in range(24)]
    profile = calculate_volume_profile(bars, num_bins=24)
    assert profile is not None
    for key in ("poc", "vah", "val", "avg_volume", "bin_width"):
        assert math.isfinite(profile[key]), key
    assert all(math.isfinite(bin[key]) for bin in profile["bins"]
               for key in ("low", "high", "mid", "volume"))
    assert math.fsum(bin["volume"] for bin in profile["bins"]) == pytest.approx(24e300, rel=1e-13)
    json.dumps(profile, allow_nan=False)


def test_unrepresentable_total_volume_fails_closed_instead_of_inf_profile():
    assert calculate_volume_profile(_bars(24, volume=1e308), num_bins=24) is None


def test_distinct_price_range_too_narrow_for_positive_bin_geometry_fails_closed():
    low = 1e308
    high = math.nextafter(low, math.inf)
    bars = [dict(open=low, high=high, low=low, close=high, volume=1000) for _ in range(24)]
    assert calculate_volume_profile(bars, num_bins=24) is None


def test_native_all_flat_range_remains_unavailable():
    bars = [dict(open=100, high=100, low=100, close=100, volume=1000) for _ in range(24)]
    assert calculate_volume_profile(bars, num_bins=24) is None


@pytest.mark.parametrize("flags", [
    {"is_closed": False}, {"complete": False}, {"completed": 0}, {"completed": 0.0}, {"final": "open"},
    {"is_closed": True, "final": False},
])
def test_explicit_unfinished_bar_does_not_contribute_or_warp_native_profile(flags):
    baseline = calculate_volume_profile(_bars(), num_bins=24)
    open_outlier = dict(open=29900, high=30000, low=29000, close=29500,
                        volume=1e20, **flags)
    actual = calculate_volume_profile(_bars() + [open_outlier], num_bins=24)
    assert _shape(actual) == _shape(baseline)
    assert math.fsum(bin["volume"] for bin in actual["bins"]) == math.fsum(
        bin["volume"] for bin in baseline["bins"])
    assert actual["contributing_bar_count"] == 24


def test_microprice_voids_separated_by_a_real_gap_are_not_merged_by_absolute_epsilon():
    nodes = [dict(low=1e-18, high=2e-18, volume=10),
             dict(low=4e-18, high=5e-18, volume=20)]
    actual = merge_lvn_bins(nodes)
    assert len(actual) == 2
    assert [(zone["low"], zone["high"]) for zone in actual] == [
        (1e-18, 2e-18), (4e-18, 5e-18)]


def test_microprice_voids_with_the_same_edge_still_merge():
    actual = merge_lvn_bins([dict(low=1e-18, high=2e-18, volume=10),
                             dict(low=2e-18, high=3e-18, volume=20)])
    assert len(actual) == 1
    assert actual[0]["low"] == 1e-18 and actual[0]["high"] == 3e-18
    assert actual[0]["volume"] == 30


@pytest.mark.parametrize("price_scale", [1.0, 1e-7])
@pytest.mark.parametrize("volume", [.03125, 1000.0])
def test_uniform_profile_uses_lowest_poc_bin_without_rounding_stored_volumes(price_scale, volume):
    bars = [dict(open=100.4 * price_scale, high=102 * price_scale,
                 low=100 * price_scale, close=101.6 * price_scale, volume=volume)
            for _ in range(24)]
    profile = calculate_volume_profile(bars, num_bins=24)
    assert profile is not None
    assert profile["poc"] == profile["bins"][0]["mid"]
    assert all(bin["volume"] == pytest.approx(volume, rel=1e-12) for bin in profile["bins"])
    assert math.fsum(bin["volume"] for bin in profile["bins"]) == pytest.approx(volume * 24, rel=1e-12)


def test_genuine_profile_volume_difference_does_not_become_numeric_tie():
    bars = [dict(open=100.4, high=102, low=100, close=101.6, volume=1000)
            for _ in range(24)]
    bars.append(dict(open=102, high=102, low=102, close=102, volume=.001))
    profile = calculate_volume_profile(bars, num_bins=24)
    assert profile["poc"] == profile["bins"][-1]["mid"]
    assert profile["bins"][-1]["volume"] == pytest.approx(1000.001, rel=1e-12)
