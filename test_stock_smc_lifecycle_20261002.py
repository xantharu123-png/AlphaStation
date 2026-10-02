"""Adversarial lifecycle controls for shared stock SMC context evidence."""
from copy import deepcopy

import pytest

from modules import patterns
from test_scanner_reaudit_regressions import candle, mirror


@pytest.mark.parametrize("bear", [False, True])
def test_swept_equal_extremes_do_not_resurrect_when_later_noncontact_volatility_grows(bear):
    # Two separate confirmed touches form a 100 resistance pool. A later
    # 101 sweep invalidates those touches. Much later, an unrelated deep
    # downside wick must not enlarge historic sweep tolerance and undo it.
    bars = [candle(95., 96., 94., 95.) for _ in range(26)]
    for index in (4, 12):
        bars[index] = candle(95., 100., 94., 95.)
    bars[18] = candle(95., 101., 94., 95.)
    suffix = [candle(95., 96., 1., 95.) for _ in range(8)]
    key = "buyside"
    if bear:
        bars, suffix = mirror(bars), mirror(suffix)
        key = "sellside"
    before = patterns.detect_liquidity_levels(bars, max_levels=100)
    assert not before[key], before
    after = patterns.detect_liquidity_levels(bars + suffix, max_levels=100)
    assert not after[key], after


@pytest.mark.parametrize("bear", [False, True])
def test_unswept_distinct_touch_pool_positive_control_and_no_mutation(bear):
    bars = [candle(95., 96., 94., 95.) for _ in range(26)]
    for index in (4, 12):
        bars[index] = candle(95., 100., 94., 95.)
    if bear:
        bars = mirror(bars)
    before = deepcopy(bars)
    key = "sellside" if bear else "buyside"
    report = patterns.detect_liquidity_levels(bars, max_levels=100)
    assert len(report[key]) == 1 and report[key][0]["touches"] == 2
    assert bars == before


@pytest.mark.parametrize("bear", [False, True])
def test_fvg_fill_lifecycle_uses_actual_adjusted_zone_not_four_decimal_display(bear):
    from test_scanner_reaudit_regressions import gap_history
    bars = gap_history()
    for bar in bars:
        for key in ("open", "high", "low", "close"):
            bar[key] += .000049
    # The real zone at index26 is [100.400049, 101.400049]. This bar trades
    # the full interval; rounding its lower edge down to100.4000 beforehand
    # incorrectly leaves an untraded artificial0.00002 slice.
    bars.append(candle(101.600049, 101.700049, 100.40002, 101.600049))
    if bear:
        bars = mirror(bars)
    report = patterns.detect_volume_imbalances(bars, max_zones=1000)
    zone = next(zone for zone in report["zones"] if zone["type"] == "FVG" and zone["bar_idx"] == 26)
    assert zone["filled"] is True, zone
    assert zone["state"] == "filled"


@pytest.mark.parametrize("bear", [False, True])
@pytest.mark.parametrize("low,expected", [(101.2, "partially_filled"),
                                         (100.40002, "partially_filled"),
                                         (100.4, "filled")])
def test_fvg_partial_full_and_exact_boundary_controls_keep_real_zone_edges(bear, low, expected):
    from test_scanner_reaudit_regressions import gap_history
    bars = gap_history() + [candle(101.6, 101.8, low, 101.6)]
    if bear:
        bars = mirror(bars)
    zone = next(zone for zone in patterns.detect_volume_imbalances(bars, max_zones=1000)["zones"]
                if zone["type"] == "FVG" and zone["bar_idx"] == 26)
    assert zone["zone_low"] == pytest.approx(98.6 if bear else 100.4)
    assert zone["zone_high"] == pytest.approx(99.6 if bear else 101.4)
    assert zone["state"] == expected
    assert zone["filled"] is (expected == "filled")
    assert zone["invalidated"] is False


@pytest.mark.parametrize("bear", [False, True])
def test_fvg_multiple_disjoint_partial_trades_merge_only_after_complete_coverage(bear):
    from test_scanner_reaudit_regressions import gap_history
    bars = gap_history()
    upper = candle(101.6, 101.8, 101.0, 101.6)
    lower = candle(100.8, 101.0, 100.4, 100.8)
    before = bars + [upper]
    after = bars + [upper, lower]
    if bear:
        before, after = mirror(before), mirror(after)
    select = lambda items: next(zone for zone in patterns.detect_volume_imbalances(items, max_zones=1000)["zones"]
                               if zone["type"] == "FVG" and zone["bar_idx"] == 26)
    assert select(before)["state"] == "partially_filled"
    assert select(after)["state"] == "filled"


@pytest.mark.parametrize("bear", [False, True])
def test_future_nearby_touch_cannot_move_old_liquidity_sweep_boundary(bear):
    bars = [candle(95, 96, 94, 95) for _ in range(34)]
    for index in (4, 12):
        bars[index] = candle(95, 100, 94, 95)
    # The initial band's tolerance is 0.375. Sweep it once, then make only
    # one new nearby touch. Enlarging the boundary to the last high would
    # falsely restore the two pre-sweep touches into the current pool.
    bars[18] = candle(95, 100.38, 94, 95)
    bars[26] = candle(95, 100.2, 94, 95)
    if bear:
        bars = mirror(bars)
    key = "sellside" if bear else "buyside"
    assert patterns.detect_liquidity_levels(bars, max_levels=100)[key] == []


@pytest.mark.parametrize("bear", [False, True])
def test_two_new_confirmed_touches_after_sweep_can_create_fresh_pool(bear):
    bars = [candle(95, 96, 94, 95) for _ in range(42)]
    for index in (4, 12, 26, 34):
        bars[index] = candle(95, 100, 94, 95)
    bars[18] = candle(95, 101, 94, 95)
    if bear:
        bars = mirror(bars)
    key = "sellside" if bear else "buyside"
    pool = patterns.detect_liquidity_levels(bars, max_levels=100)[key]
    assert len(pool) == 1
    assert pool[0]["touches"] == 2
    assert pool[0]["last_touch_index"] == 34
    assert pool[0]["confirmed_index"] == 37


@pytest.mark.parametrize("bear", [False, True])
def test_liquidity_second_touch_requires_right_side_confirmation(bear):
    bars = [candle(95, 96, 94, 95) for _ in range(26)]
    for index in (4, 12):
        bars[index] = candle(95, 100, 94, 95)
    if bear:
        bars = mirror(bars)
    key = "sellside" if bear else "buyside"
    assert not patterns.detect_liquidity_levels(bars[:15])[key]
    assert patterns.detect_liquidity_levels(bars[:16])[key][0]["confirmed_index"] == 15


@pytest.mark.parametrize("bear", [False, True])
def test_nearest_distinct_liquidity_pool_uses_raw_distance_not_two_decimal_label(bear):
    bars = [candle(95, 95.001, 94.999, 95) for _ in range(330)]
    for index in (150, 160):
        bars[index]["high"] = 100.009
    for index in (260, 270):
        bars[index]["high"] = 100.003
    expected, key = 100.003, "buyside"
    if bear:
        # Reflection around95 preserves the exact distance percentage for
        # the symmetric case, including the deliberately tied display label.
        bars = [dict(bar, open=190-bar["open"], high=190-bar["low"],
                     low=190-bar["high"], close=190-bar["close"]) for bar in bars]
        expected, key = 190-expected, "sellside"
    all_pools = patterns.detect_liquidity_levels(bars, max_levels=100)
    assert len(all_pools[key]) == 2
    assert all_pools[key][0]["dist_pct"] == all_pools[key][1]["dist_pct"]
    assert all_pools[f"nearest_{key}"]["level"] == expected
    assert patterns.detect_liquidity_levels(bars, max_levels=1)[key][0]["level"] == expected


@pytest.mark.parametrize("bear", [False, True])
def test_nearest_orderblock_cannot_sort_by_tied_display_distance(bear):
    from test_scanner_reaudit_regressions import ob_history
    bars = ob_history() + [candle(100.2005, 100.4, 99.8001, 99.8025),
                           candle(99.9, 102.2, 99.8, 102., 3000),
                           candle(102., 102.4, 101.8, 102.2)]
    if bear:
        bars = mirror(bars)
    key, nearest = ("bearish_obs", "nearest_bear_ob") if bear else ("bullish_obs", "nearest_bull_ob")
    blocks = patterns.detect_order_blocks(bars, max_blocks=100)
    assert len(blocks[key]) == 2
    # Display distances tie, but the second block is genuinely nearer.
    assert round(blocks[key][0]["dist_pct"], 2) == round(blocks[key][1]["dist_pct"], 2)
    assert blocks[nearest]["idx"] == 30, blocks
    assert patterns.detect_order_blocks(bars, max_blocks=1)[key][0]["idx"] == 30


@pytest.mark.parametrize("bear", [False, True])
def test_actual_orderblock_to_bi_confluence_keeps_adjusted_raw_zone_edges(bear):
    from test_scanner_reaudit_regressions import ob_history
    bars = ob_history()
    for bar in bars:
        for key in ("open", "high", "low", "close"):
            bar[key] += .000049
    # Real Wilder ATR stabilizes at0.15. No detector/indicator is mocked.
    # The valid adjusted OB is0.299961 from the 15-bar boundary (<2ATR).
    # Four-decimal output rounding invented0.30001, wrongly making BI red.
    bars.extend(candle(100.45, 100.50001, 100.35001, 100.45) for _ in range(180))
    if bear:
        bars = mirror(bars)
    direction = "short" if bear else "long"
    result = patterns.analyze_breakout_imminent(bars, direction=direction)
    check = result.indicator_checks[14]
    assert check["key"] == "order_block_confluence"
    assert check["available"] is True
    assert check["passed"] is True, check
    assert check["points"] == 14
    block_key = "bearish_obs" if bear else "bullish_obs"
    block = next(block for block in patterns.detect_order_blocks(bars)[block_key] if block["idx"] == 27)
    assert block["ob_low" if bear else "ob_high"] == bars[27]["open"]
