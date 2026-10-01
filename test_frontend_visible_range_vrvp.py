"""Execute the shipped visible-range volume profile, not a copied algorithm.

Synthetic OHLCV provides volume totals and causal clocks. This verifies bar-
derived volume allocation, not actual transaction-at-price or bid/ask volume.
"""
import ast
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import random
import re
import subprocess
import time
from typing import Optional

import pytest
from fastapi import HTTPException, Query

from test_frontend_scanner_lifecycle import node_run
from modules.volume_analysis import calculate_volume_profile


SOURCE_PATH = Path(__file__).resolve().parent / "frontend" / "index.html"
BASE = 1_790_000_000
AS_OF = BASE + 1_000


def _run_node(program):
    try:
        return node_run(program)
    except subprocess.CalledProcessError as error:
        pytest.fail(error.stderr)


def _function_source(name="calculateVisibleVolumeProfile"):
    """Extract one self-contained actual JS function, allowing nested braces."""
    source = SOURCE_PATH.read_text(encoding="utf-8")
    marker = f"function {name}("
    start = source.find(marker)
    assert start >= 0, "Visible-range profile calculator is absent from shipped source"
    cursor = start + len(marker)
    parens = 1
    while parens:
        char = source[cursor]
        parens += (char == "(") - (char == ")")
        cursor += 1
    body = source.index("{", cursor)
    depth, quote, comment, escaped = 1, None, None, False
    cursor = body + 1
    while depth:
        char = source[cursor]
        next_char = source[cursor + 1] if cursor + 1 < len(source) else ""
        if comment == "line":
            if char == "\n":
                comment = None
        elif comment == "block":
            if char == "*" and next_char == "/":
                comment = None
                cursor += 1
        elif quote:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                quote = None
        elif char == "/" and next_char in ("/", "*"):
            comment = "line" if next_char == "/" else "block"
            cursor += 1
        elif char in ("'", '"', "`"):
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        cursor += 1
    return source[start:cursor]


def _bar(index, low=10.0, high=14.0, volume=100.0, up=True, **changes):
    value = {
        "time": BASE + index * 10,
        "open": low + (high - low) * (0.2 if up else 0.8),
        "high": high, "low": low,
        "close": low + (high - low) * (0.8 if up else 0.2),
        "volume": volume, "is_closed": True,
        "close_time": BASE + index * 10 + 5,
    }
    value.update(changes)
    return value


def _profile(candles, chart_range=None, **options):
    if chart_range is None:
        chart_range = {"from": BASE, "to": BASE + 999}
    settings = {"numBins": 4, "asOf": AS_OF, "timeframe": "1H", **options}
    inputs = json.dumps([candles, chart_range, settings], allow_nan=False)
    program = _function_source() + "\nconst args=" + inputs + ";\n"
    program += "console.log(JSON.stringify(calculateVisibleVolumeProfile(...args)));"
    return json.loads(_run_node(program))


def _volume(profile):
    return sum(item["volume"] for item in profile["bins"])


def test_visible_window_rebuilds_bins_and_poc_instead_of_repainting_full_history():
    bars = [_bar(0, 10, 14, 10), _bar(1, 11, 14, 90),
            _bar(2, 100, 104, 10), _bar(3, 100, 103, 90)]
    first = _profile(bars, {"from": BASE, "to": BASE + 10})
    later = _profile(bars, {"from": BASE + 20, "to": BASE + 30})
    assert first["poc"] < 15 < later["poc"]
    assert first["bins"] != later["bins"]
    assert first["candleCount"] == later["candleCount"] == 2
    assert _volume(first) == pytest.approx(100)
    assert _volume(later) == pytest.approx(100)


def test_logical_visible_range_uses_indices_not_epoch_seconds():
    bars = [_bar(0, 1, 2, 2), _bar(1, 10, 12, 7),
            _bar(2, 10, 14, 13), _bar(3, 100, 110, 17)]
    logical = _profile(bars, {"logicalFrom": 1, "logicalTo": 2})
    temporal = _profile(bars, {"from": BASE + 10, "to": BASE + 20})
    assert logical == temporal
    assert _volume(logical) == pytest.approx(20)
    assert logical["candleCount"] == 2


def test_outside_visible_window_extremes_and_volume_have_no_influence():
    selected = [_bar(1, 10, 14, 3.7), _bar(2, 11, 13, 7.2)]
    chart_range = {"from": BASE + 10, "to": BASE + 20}
    expected = _profile(selected, chart_range)
    actual = _profile([_bar(0, 0.001, 0.002, 1e20), *selected,
                       _bar(3, 1e6, 2e6, 1e20)], chart_range)
    assert actual == expected


@pytest.mark.parametrize("changes", [
    {"is_closed": False}, {"is_closed": None}, {"is_closed": "true"},
    {"close_time": AS_OF + 1}, {"close_time": None},
    {"time": AS_OF + 1, "close_time": AS_OF + 5},
    {"close_time": BASE + 19},
])
def test_open_future_and_unverifiable_completion_cannot_change_profile(changes):
    selected = [_bar(0, 10, 14, 3.7), _bar(1, 11, 13, 7.2)]
    candidate = _bar(2, 1e6, 2e6, 1e20, **changes)
    chart_range = {"from": BASE, "to": AS_OF + 100}
    assert _profile([*selected, candidate], chart_range) == _profile(selected, chart_range)


def test_bar_becomes_available_at_its_exact_confirmed_close_time():
    bars = [_bar(0, 10, 12, 9), _bar(1, 11, 14, 13)]
    before = _profile(bars, asOf=BASE + 14)
    closed = _profile(bars, asOf=BASE + 15)
    assert before["candleCount"] == 1
    assert closed["candleCount"] == 2
    assert _volume(before) == pytest.approx(9)
    assert _volume(closed) == pytest.approx(22)


def test_no_volume_rounding_and_direction_partition_conserve_total():
    bars = [_bar(0, 10, 14, 0.123456789, up=True),
            _bar(1, 10.2, 13.8, 9.876543211, up=False),
            _bar(2, 11.234, 11.234, 0.000000317)]
    profile = _profile(bars, numBins=24)
    expected = sum(item["volume"] for item in bars)
    assert profile["totalVolume"] == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert _volume(profile) == pytest.approx(expected, rel=1e-12, abs=1e-12)
    assert sum(item["upVolume"] for item in profile["bins"]) == pytest.approx(
        bars[0]["volume"] + bars[2]["volume"], rel=1e-12, abs=1e-12)
    assert sum(item["downVolume"] for item in profile["bins"]) == pytest.approx(
        bars[1]["volume"], rel=1e-12, abs=1e-12)
    for item in profile["bins"]:
        assert item["upVolume"] + item["downVolume"] == pytest.approx(
            item["volume"], rel=1e-12, abs=1e-12)


@pytest.mark.parametrize("chart_range", [
    {"from": BASE + 10, "to": BASE}, {"from": None, "to": BASE},
    {"from": BASE, "to": None}, {"logicalFrom": 2, "logicalTo": 1},
    {"logicalFrom": None, "logicalTo": 1},
    {"from": AS_OF + 10, "to": AS_OF + 100}, {},
])
def test_invalid_or_empty_visible_range_does_not_fall_back_to_entire_history(chart_range):
    assert _profile([_bar(0), _bar(1)], chart_range) is None


@pytest.mark.parametrize("bars", [
    [], [_bar(0, volume=0)], [_bar(0, volume=None)], [_bar(0, volume=-1)],
    [_bar(0, volume=True)], [_bar(0, low=12, high=10)],
    [_bar(0, open=20)], [_bar(0, close=20)],
])
def test_missing_unusable_volume_or_price_range_fails_closed(bars):
    assert _profile(bars) is None


def test_flat_price_history_uses_one_exact_price_bin_without_inventing_a_range():
    profile = _profile([_bar(0, 12, 12, 4.7), _bar(1, 12, 12, 3.3)])
    assert profile["candleCount"] == 2
    assert len(profile["bins"]) == 1
    item = profile["bins"][0]
    assert item["low"] == item["high"] == item["mid"] == profile["poc"] == 12
    assert profile["totalVolume"] == item["volume"] == pytest.approx(8)
    assert profile["val"] == profile["vah"] == 12


def test_distinct_prices_with_underflowed_bin_width_never_become_a_flat_profile():
    assert _profile([_bar(0, 5e-324, 1e-323, 10)], numBins=24) is None


def test_finite_extreme_volume_and_prices_do_not_overflow_intermediate_allocations():
    profile = _profile([_bar(0, 1e307, 1e308, 1e308)], numBins=24)
    assert profile is not None
    assert profile["totalVolume"] == pytest.approx(1e308, rel=1e-12)
    assert _volume(profile) == pytest.approx(1e308, rel=1e-12)
    assert all(0 <= item["volume"] <= 1e308 for item in profile["bins"])


def test_unrepresentable_total_volume_is_not_published_as_an_infinite_profile():
    assert _profile([_bar(0, volume=1e308), _bar(1, volume=1e308)]) is None


@pytest.mark.parametrize("seed", [17, 42, 1337, 20261001])
@pytest.mark.parametrize("price_scale", [1.0, 1e-7])
def test_actual_js_and_native_profiles_agree_on_same_closed_visible_ohlcv(seed, price_scale):
    """Cross-stack parity uses both real algorithms, no copied reference math."""
    rng = random.Random(seed)
    bars = []
    for index in range(60):
        low = (7 + rng.uniform(-5, 8)) * price_scale
        high = low if index % 8 == 0 else low + rng.uniform(.01, 4) * price_scale
        volume = (10 ** rng.uniform(-3, 5)) * rng.uniform(.1, 1.9)
        bars.append(_bar(index, low, high, volume, up=index % 3 != 0))
    # Forty genuine bars, including Dojis, ensure the native 20-bar contract.
    selected = bars[8:48]
    native = calculate_volume_profile(selected, num_bins=24, timeframe="1H")
    visible = _profile(bars, {"logicalFrom": 8, "logicalTo": 47}, numBins=24)
    assert native is not None
    assert visible is not None
    assert visible["candleCount"] == native["contributing_bar_count"] == 40
    assert visible["profile_method"] == native["method"]
    assert visible["approximation"] is native["approximation"] is True
    assert visible["tick_data_used"] is native["tick_data_used"] is False
    assert len(visible["bins"]) == len(native["bins"]) == 24
    for js_bin, native_bin in zip(visible["bins"], native["bins"]):
        for key in ("low", "high", "mid", "volume"):
            assert js_bin[key] == pytest.approx(native_bin[key], rel=1e-12, abs=1e-18), key
    for key in ("poc", "vah", "val"):
        assert visible[key] == pytest.approx(native[key], rel=1e-12, abs=1e-18), key
    native_total = math.fsum(bin["volume"] for bin in native["bins"])
    input_total = math.fsum(bar["volume"] for bar in selected)
    assert visible["totalVolume"] == pytest.approx(native_total, rel=1e-12, abs=1e-18)
    assert visible["totalVolume"] == pytest.approx(input_total, rel=1e-12, abs=1e-18)


@pytest.mark.parametrize("seed", [17, 42, 1337, 20261001])
@pytest.mark.parametrize("price_scale", [1.0, 1e-7])
def test_uniform_volume_poc_tie_uses_lowest_bin_in_both_actual_algorithms(seed, price_scale):
    rng = random.Random(seed)
    low, high = 100 * price_scale, 102 * price_scale
    volume = rng.uniform(.001, 10000)
    bars = [_bar(index, low, high, volume) for index in range(24)]
    native = calculate_volume_profile(bars, num_bins=24)
    visible = _profile(bars, numBins=24)
    assert native is not None and visible is not None
    for profile in (native, visible):
        assert profile["poc"] == pytest.approx(profile["bins"][0]["mid"], rel=1e-12)
        assert all(bin["volume"] == pytest.approx(volume, rel=1e-12)
                   for bin in profile["bins"])
    assert native["poc"] == pytest.approx(visible["poc"], rel=1e-12)
    assert native["val"] == pytest.approx(visible["val"], rel=1e-12)
    assert native["vah"] == pytest.approx(visible["vah"], rel=1e-12)
    assert visible["totalVolume"] == pytest.approx(volume * 24, rel=1e-12)


def test_narrow_range_cancellation_obeys_numeric_tie_budget_not_universal_uniform_claim():
    """Retain the real Seed-42 boundary case outside the declared tolerance."""
    rng = random.Random(42)
    low = rng.uniform(10, 500) * 1e-7
    high = low + rng.uniform(1, 7) * 1e-7
    volume = rng.uniform(.001, 10000)
    bars = [_bar(index, low, high, volume) for index in range(24)]
    native = calculate_volume_profile(bars, num_bins=24)
    visible = _profile(bars, numBins=24)
    for profile in (native, visible):
        peak = max(bin["volume"] for bin in profile["bins"])
        # Finite price-boundary cancellation creates a 1.414e-12 difference;
        # a fixed 1e-12 volume policy must not pretend that this is inside it.
        assert not math.isclose(profile["bins"][0]["volume"], peak, rel_tol=1e-12)
        selected = next(index for index, bin in enumerate(profile["bins"])
                        if math.isclose(bin["volume"], peak, rel_tol=1e-12))
        assert selected > 0
        assert profile["poc"] == profile["bins"][selected]["mid"]
        assert math.isclose(profile["bins"][selected]["volume"], peak, rel_tol=1e-12)
        assert not any(math.isclose(bin["volume"], peak, rel_tol=1e-12)
                       for bin in profile["bins"][:selected])
    for key in ("poc", "vah", "val"):
        assert native[key] == pytest.approx(visible[key], rel=1e-12, abs=1e-18)
    assert visible["totalVolume"] == pytest.approx(volume * 24, rel=1e-12)


@pytest.mark.parametrize("price_scale", [1.0, 1e-7])
def test_real_one_part_per_million_volume_advantage_is_not_treated_as_poc_tie(price_scale):
    bars = [_bar(index, 100 * price_scale, 102 * price_scale, 1000)
            for index in range(24)]
    bars.append(_bar(24, 102 * price_scale, 102 * price_scale, .001))
    native = calculate_volume_profile(bars, num_bins=24)
    visible = _profile(bars, numBins=24)
    for profile in (native, visible):
        assert profile["poc"] == pytest.approx(profile["bins"][-1]["mid"], rel=1e-12)
        assert profile["bins"][-1]["volume"] == pytest.approx(1000.001, rel=1e-12)
    assert visible["totalVolume"] == pytest.approx(24000.001, rel=1e-12)


@pytest.mark.parametrize("price_scale", [1.0, 1e-7])
@pytest.mark.parametrize("relative_difference,expected_low,expected_high", [
    (5e-13, 102, 104), (1e-6, 101, 103),
])
def test_value_area_near_tie_prefers_above_but_genuine_difference_prefers_more_volume(
    price_scale, relative_difference, expected_low, expected_high,
):
    bars = []
    volumes = (5, 100, 200, 100 * (1 - relative_difference), 5)
    for repeat in range(4):
        for index, volume in enumerate(volumes):
            price = (100.5 + index) * price_scale
            bars.append(_bar(len(bars), price, price, volume))
        bars.append(_bar(len(bars), 100 * price_scale, 105 * price_scale, 1e-12))
    native = calculate_volume_profile(bars, num_bins=5)
    visible = _profile(bars, numBins=5)
    for profile in (native, visible):
        assert profile["val"] == pytest.approx(expected_low * price_scale, rel=1e-12)
        assert profile["vah"] == pytest.approx(expected_high * price_scale, rel=1e-12)


@pytest.mark.parametrize("component_start", ["function DetailSidebar(", "function ChartAnalyseTab("])
def test_both_chart_consumers_recompute_on_view_changes_without_server_vrvp_fallback(
    component_start,
):
    source = SOURCE_PATH.read_text(encoding="utf-8")
    # Component names are resolved below rather than assuming a copied profile.
    if component_start not in source:
        assert False, f"Chart consumer marker changed: {component_start}"
    start = source.index(component_start)
    next_function = re.search(r"(?m)^function \w+\(", source[start + len(component_start):])
    end = start + len(component_start) + next_function.start() if next_function else len(source)
    section = source[start:end]
    assert "mountVisibleVolumeProfile(" in section
    shared = _function_source("mountVisibleVolumeProfile")
    assert "calculateVisibleVolumeProfile(" in shared
    assert "getVisibleLogicalRange" in shared
    assert "subscribeVisibleLogicalRangeChange" in shared
    assert "const vp = chartData.vrvp;" not in section


def test_real_chart_mount_recomputes_price_lines_clears_old_profile_and_unsubscribes():
    bars = [_bar(0, 10, 14, 10), _bar(1, 11, 14, 90),
            _bar(2, 100, 104, 10), _bar(3, 100, 103, 90)]
    data = {"candles": bars, "chart_as_of": AS_OF, "timeframe": "1H"}
    program = _function_source() + "\n" + _function_source("mountVisibleVolumeProfile")
    program += "\nconst chartData=" + json.dumps(data) + ";\n" + r"""
    const assert=require('node:assert/strict');
    let range={from:0,to:1}, listener, crosshair, removed=false, clearCalls=0;
    const lines=[];
    const ctx={clearRect(){clearCalls++;},fillRect(){}};
    const canvas={style:{},dataset:{},getContext:()=>ctx,remove(){removed=true;}};
    global.document={createElement:()=>canvas};
    const container={clientWidth:1000,clientHeight:300,style:{},querySelector:()=>null,appendChild(){}};
    const scale={getVisibleLogicalRange:()=>range,
      subscribeVisibleLogicalRangeChange(fn){listener=fn;},
      unsubscribeVisibleLogicalRangeChange(fn){assert.equal(fn,listener);listener=null;}};
    const chart={timeScale:()=>scale,subscribeCrosshairMove(fn){crosshair=fn;},
      unsubscribeCrosshairMove(fn){assert.equal(fn,crosshair);crosshair=null;}};
    const series={priceToCoordinate:p=>300-p,
      createPriceLine(options){const line={options,applyOptions(next){this.options=next;}};lines.push(line);return line;},
      removePriceLine(line){lines.splice(lines.indexOf(line),1);}};
    const draw=mountVisibleVolumeProfile({chart,series,container,chartData});
    assert.equal(lines.length,3);
    const firstPoc=Number(canvas.dataset.poc);
    assert.ok(firstPoc<15);
    assert.ok(Math.abs(Number(canvas.dataset.totalVolume)-100)<1e-10);
    range={from:2,to:3};listener();
    assert.equal(lines.length,3);
    assert.ok(Number(canvas.dataset.poc)>100);
    assert.notEqual(Number(canvas.dataset.poc),firstPoc);
    assert.ok(lines.find(line=>line.options.title==='VP POC').options.price>100);
    range={from:10,to:11};listener();
    assert.equal(lines.length,0);
    assert.equal(canvas.dataset.poc,undefined);
    assert.equal(canvas.dataset.candleCount,'0');
    assert.ok(canvas.dataset.totalVolume===undefined || Number(canvas.dataset.totalVolume)===0);
    draw.dispose();
    assert.equal(listener,null);assert.equal(crosshair,null);assert.equal(removed,true);
    assert.ok(clearCalls>=3);
    """
    _run_node(program)


def test_async_chart_mount_waits_for_two_frames_uses_latest_range_and_cancels_on_dispose():
    """Exercise real requestAnimationFrame ordering instead of synchronous fallback."""
    bars = [_bar(0, 10, 14, 10), _bar(1, 11, 14, 90),
            _bar(2, 100, 104, 10), _bar(3, 100, 103, 90)]
    data = {"candles": bars, "chart_as_of": AS_OF, "timeframe": "1H"}
    program = _function_source() + "\n" + _function_source("mountVisibleVolumeProfile")
    program += "\nconst chartData=" + json.dumps(data) + ";\n" + r"""
    const assert=require('node:assert/strict');
    const frames=new Map(), cancelled=[];
    let sequence=0;
    global.requestAnimationFrame=callback=>{const id=++sequence;frames.set(id,callback);return id;};
    global.cancelAnimationFrame=id=>{cancelled.push(id);frames.delete(id);};
    function flushFrame() {
      const batch=[...frames.values()];frames.clear();
      batch.forEach(callback=>callback());
    }
    function mount() {
      let range={from:0,to:1}, listener=null, crosshair=null, removed=false;
      let clearCalls=0, fillCalls=0, coordinateCalls=0, scaleOffset=0;
      const lines=[];
      const ctx={clearRect(){clearCalls++;},fillRect(){fillCalls++;}};
      const canvas={style:{},dataset:{},getContext:()=>ctx,remove(){removed=true;}};
      global.document={createElement:()=>canvas};
      const container={clientWidth:1000,clientHeight:300,style:{},querySelector:()=>null,appendChild(){}};
      const scale={getVisibleLogicalRange:()=>range,
        subscribeVisibleLogicalRangeChange(fn){listener=fn;},
        unsubscribeVisibleLogicalRangeChange(fn){assert.equal(fn,listener);listener=null;}};
      const chart={timeScale:()=>scale,subscribeCrosshairMove(fn){crosshair=fn;},
        unsubscribeCrosshairMove(fn){assert.equal(fn,crosshair);crosshair=null;}};
      const series={priceToCoordinate(p){coordinateCalls++;return 300-p+scaleOffset;},
        createPriceLine(options){const line={options,applyOptions(next){this.options=next;}};lines.push(line);return line;},
        removePriceLine(line){lines.splice(lines.indexOf(line),1);}};
      const draw=mountVisibleVolumeProfile({chart,series,container,chartData});
      return {draw,canvas,lines,
        changeRange(next){range=next;listener();},
        settleScale(value){scaleOffset=value;},
        get clearCalls(){return clearCalls;},get fillCalls(){return fillCalls;},
        get coordinateCalls(){return coordinateCalls;},get removed(){return removed;},
        get listener(){return listener;},get crosshair(){return crosshair;}};
    }
    const view=mount();
    assert.equal(frames.size,1);
    assert.equal(view.lines.length,0);assert.equal(view.clearCalls,0);
    assert.equal(view.fillCalls,0);assert.equal(view.coordinateCalls,0);
    view.changeRange({from:1,to:2});
    assert.equal(frames.size,1,'range events must coalesce before the first frame');
    flushFrame();
    assert.equal(frames.size,1);
    assert.equal(view.lines.length,0);assert.equal(view.fillCalls,0);
    assert.equal(view.coordinateCalls,0,'stale autoscale coordinates must not be sampled');
    view.changeRange({from:2,to:3});
    view.settleScale(50);
    assert.equal(frames.size,1,'newest range is read only after chart autoscaling settles');
    flushFrame();
    assert.equal(frames.size,0);assert.equal(view.lines.length,3);
    assert.ok(Number(view.canvas.dataset.poc)>100);
    assert.equal(view.canvas.dataset.candleCount,'2');
    assert.ok(Math.abs(Number(view.canvas.dataset.totalVolume)-100)<1e-10);
    assert.equal(Number(view.canvas.dataset.plotLowY),250);
    assert.equal(Number(view.canvas.dataset.plotHighY),246);
    assert.ok(view.fillCalls>0);
    assert.ok(view.lines.find(line=>line.options.title==='VP POC').options.price>100);
    view.draw.dispose();
    assert.equal(view.lines.length,0);assert.equal(view.listener,null);
    assert.equal(view.crosshair,null);assert.equal(view.removed,true);

    // Disposal must cancel both possible phases of the pending two-frame job.
    for (const firstFrameAlreadyRan of [false,true]) {
      const pending=mount();
      if (firstFrameAlreadyRan) flushFrame();
      assert.equal(frames.size,1);
      const scheduledId=[...frames.keys()][0];
      pending.draw.dispose();
      assert.ok(cancelled.includes(scheduledId));
      assert.equal(frames.size,0);
      flushFrame();pending.draw();flushFrame();
      assert.equal(pending.lines.length,0);assert.equal(pending.clearCalls,0);
      assert.equal(pending.fillCalls,0);assert.equal(pending.coordinateCalls,0);
      assert.equal(pending.listener,null);assert.equal(pending.crosshair,null);
      assert.equal(pending.removed,true);
      assert.equal(frames.size,0,'disposed chart must never schedule a new paint');
    }
    """
    _run_node(program)


@pytest.mark.parametrize("timeframe", ["8H", "2H", "invalid", "", "1M"])
def test_chart_api_rejects_unsupported_timeframe_before_provider_fetch(timeframe):
    """Compile the actual endpoint without importing API/credentials/threads."""
    path = SOURCE_PATH.parent.parent / "api.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    endpoint = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "get_chart_data")
    endpoint.decorator_list = []
    module = ast.fix_missing_locations(ast.Module(body=[endpoint], type_ignores=[]))

    def forbidden_fetch(*args, **kwargs):
        pytest.fail("Unsupported timeframe reached provider and risks mislabeled 1H data")

    def propagate(error, *args):
        raise error

    namespace = {"Query": Query, "Optional": Optional, "HTTPException": HTTPException,
                 "fetch_ohlcv_for_chart": forbidden_fetch,
                 "_normalize_chart_direction": lambda direction: direction,
                 "_CHART_CACHE": {}, "_CHART_CACHE_TTL": {}, "POLYGON_KEY": "offline",
                 "datetime": datetime, "timezone": timezone, "time": time,
                 "_raise_internal_api_error": propagate}
    exec(compile(module, str(path), "exec"), namespace)
    with pytest.raises(HTTPException) as error:
        namespace["get_chart_data"](ticker="AAPL", timeframe=timeframe,
                                    overlays="vrvp", direction=None)
    assert error.value.status_code == 422
