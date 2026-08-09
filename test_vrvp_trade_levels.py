from pathlib import Path

import pytest

from modules.trade_levels import minimum_stop_distance, trade_geometry
from modules.indicators import calculate_atr_14
from modules.vrvp_levels import (
    apply_vrvp_to_trade_setup,
    build_vrvp_structure,
    calculate_wilder_atr,
)
from modules.volume_analysis import merge_lvn_bins


def _bars_with_nodes(low_node: float = 95.0, high_node: float = 112.0):
    bars = []
    for i in range(24):
        base = low_node + (i % 3) * 0.18
        bars.append({
            "open": base,
            "high": base + 0.35,
            "low": base - 0.35,
            "close": base + 0.05,
            "volume": 40_000,
        })
    for i in range(28):
        base = high_node + (i % 4) * 0.22
        bars.append({
            "open": base,
            "high": base + 0.45,
            "low": base - 0.45,
            "close": base - 0.03,
            "volume": 120_000,
        })
    for i in range(12):
        base = 103 + (i % 4) * 0.35
        bars.append({
            "open": base,
            "high": base + 0.5,
            "low": base - 0.5,
            "close": base,
            "volume": 18_000,
        })
    return bars


def test_vrvp_lifts_long_targets_to_structural_resistance():
    vrvp = build_vrvp_structure(_bars_with_nodes(), 100, "LONG", min_bars=20)
    setup = {
        "entry": 100,
        "stop": 97,
        "tp1": 104,
        "tp2": 106,
        "direction": "LONG",
        "level_model": "structure_first_v2",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="LONG", asset_type="stock_swing", atr=2.0)

    assert enriched["tp1"] > enriched["entry"]
    assert enriched["tp2"] > enriched["tp1"]
    assert enriched["rr_tp1"] >= 1.5
    assert enriched["rr_tp2"] >= 2.4
    assert enriched["rr"] >= 1.95
    assert enriched["vrvp_poc"] is not None
    assert "vrvp" in enriched["level_model"]
    assert trade_geometry(
        enriched["entry"], enriched["stop"], enriched["tp1"], enriched["tp2"], "LONG"
    )["valid"] is True


def test_adjacent_lvn_bins_form_one_void_with_only_outer_edges(monkeypatch):
    raw_lvns = [
        {"low": 101.0, "high": 102.0, "mid": 101.5, "volume": 10, "volume_pct": 20},
        {"low": 102.0, "high": 103.0, "mid": 102.5, "volume": 8, "volume_pct": 16},
        {"low": 103.0, "high": 104.0, "mid": 103.5, "volume": 9, "volume_pct": 18},
    ]
    zones = merge_lvn_bins(raw_lvns)
    assert len(zones) == 1
    assert zones[0]["low"] == 101.0
    assert zones[0]["high"] == 104.0
    assert zones[0]["bin_count"] == 3

    profile = {
        "poc": 98.0,
        "vah": 99.0,
        "val": 96.0,
        "range_high": 110.0,
        "range_low": 90.0,
        "avg_volume": 100.0,
        "hvns": [],
        "lvns": raw_lvns,
    }
    monkeypatch.setattr("modules.vrvp_levels.calculate_volume_profile", lambda *_args, **_kwargs: profile)
    bars = [
        {"open": 99.0, "high": 100.0, "low": 98.0, "close": 99.0, "volume": 1000}
        for _ in range(20)
    ]
    structure = build_vrvp_structure(bars, 100.0, "LONG", min_bars=20)
    lvn_levels = [level for level in structure["levels"] if level["kind"] == "LVN_EDGE"]
    assert [level["price"] for level in lvn_levels] == [101.0, 104.0]
    assert structure["volume_voids"][0]["bin_count"] == 3


def test_vrvp_short_targets_remain_below_entry_and_separate():
    vrvp = build_vrvp_structure(_bars_with_nodes(low_node=88, high_node=105), 100, "SHORT", min_bars=20)
    setup = {
        "entry": 100,
        "stop": 103,
        "tp1": 96,
        "tp2": 94,
        "direction": "SHORT",
        "level_model": "structure_first_v2",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="stock_swing", atr=2.0)

    assert enriched["stop"] > enriched["entry"]
    assert enriched["tp1"] < enriched["entry"]
    assert enriched["tp2"] < enriched["tp1"]
    assert enriched["rr_tp1"] >= 1.5
    assert enriched["rr_tp2"] >= 2.4
    assert enriched["rr"] >= 1.95
    assert enriched["vrvp_poc"] is not None
    assert trade_geometry(
        enriched["entry"], enriched["stop"], enriched["tp1"], enriched["tp2"], "SHORT"
    )["valid"] is True


def test_vrvp_validation_prevents_duplicate_targets():
    setup = {
        "entry": 10,
        "stop": 9.5,
        "tp1": 10.2,
        "tp2": 10.2,
        "direction": "LONG",
    }

    enriched = apply_vrvp_to_trade_setup(setup, None, direction="LONG", asset_type="crypto")

    assert enriched["vrvp_applied"] is False
    assert enriched["tp1"] == 10.2
    assert enriched["tp2"] == 10.2


def test_vrvp_marks_near_overhead_resistance_as_long_gate():
    vrvp = {
        "timeframe": "4H",
        "resistances": [
            {"price": 100.55, "source": "VRVP HVN high", "weight": 2.2},
            {"price": 106.0, "source": "VRVP VAH", "weight": 1.3},
        ],
        "supports": [{"price": 98.8, "source": "VRVP POC", "weight": 1.5}],
    }
    setup = {
        "entry": 100.0,
        "stop": 99.35,
        "tp1": 103.0,
        "tp2": 106.0,
        "direction": "LONG",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="LONG", asset_type="crypto")

    assert enriched["nearest_barrier"]["side"] == "resistance"
    assert enriched["overhead_resistance"]["price"] == 100.55
    assert enriched["barrier_gate"] == "BREAK_RECLAIM_REQUIRED"
    assert "near_overhead_resistance" in enriched["risk_flags"]


def test_vrvp_marks_near_underlying_support_as_short_gate():
    vrvp = {
        "timeframe": "4H",
        "supports": [
            {"price": 99.45, "source": "VRVP HVN low", "weight": 2.0},
            {"price": 94.0, "source": "VRVP VAL", "weight": 1.4},
        ],
        "resistances": [{"price": 101.2, "source": "VRVP POC", "weight": 1.2}],
    }
    setup = {
        "entry": 100.0,
        "stop": 100.65,
        "tp1": 97.0,
        "tp2": 94.0,
        "direction": "SHORT",
    }

    enriched = apply_vrvp_to_trade_setup(setup, vrvp, direction="SHORT", asset_type="crypto")

    assert enriched["nearest_barrier"]["side"] == "support"
    assert enriched["underlying_support"]["price"] == 99.45
    assert enriched["barrier_gate"] == "BREAK_SUPPORT_REQUIRED"
    assert "near_underlying_support" in enriched["risk_flags"]


def test_canonical_wilder_atr_matches_indicator_reference():
    bars = []
    close = 100.0
    for index in range(36):
        open_price = close
        close = open_price + (0.6 if index % 3 else -0.35)
        bars.append({
            "o": open_price,
            "h": max(open_price, close) + 1.2 + index * 0.01,
            "l": min(open_price, close) - 0.8,
            "c": close,
            "v": 0 if index % 5 == 0 else 10_000,
        })

    normalized = [
        {"high": bar["h"], "low": bar["l"], "close": bar["c"]}
        for bar in bars
    ]
    expected, _ = calculate_atr_14(normalized)

    assert round(calculate_wilder_atr(bars), 4) == expected


def test_canonical_wilder_atr_requires_full_period_and_rejects_bad_bars():
    short_history = [
        {"high": 10.5, "low": 9.5, "close": 10.0}
        for _ in range(14)
    ]
    assert calculate_wilder_atr(short_history) == 0.0

    enough_history = short_history + [
        {"high": 11.0, "low": 9.0, "close": 10.5},
        {"high": 1.0, "low": 2.0, "close": 1.5},
    ]
    assert calculate_wilder_atr(enough_history) > 0


def test_structural_level_callsites_use_canonical_atr_before_fallbacks():
    root = Path(__file__).resolve().parent
    api_source = (root / "api.py").read_text(encoding="utf-8")
    scanner_source = (root / "modules" / "scanners.py").read_text(encoding="utf-8")
    listing_source = (root / "modules" / "new_listing_scanner.py").read_text(encoding="utf-8")

    assert api_source.count("calculate_wilder_atr(") >= 5
    assert "calculate_wilder_atr(_session_bars" in scanner_source
    assert "calculate_wilder_atr(\n        pump_data.get(\"vrvp_bars\")" in listing_source
    assert "atr=max(0.00000001, ath - current" not in listing_source


def test_root_scanner_copies_are_isolated_from_production_modules():
    root = Path(__file__).resolve().parent
    listing_shim = (root / "new_listing_scanner.py").read_text(encoding="utf-8")
    volume_stub = (root / "volume_profile.py").read_text(encoding="utf-8")

    assert "from modules.new_listing_scanner import *" in listing_shim
    assert len(listing_shim.splitlines()) < 20
    assert "raise ImportError(" in volume_stub
    assert "modules.vrvp_levels" in volume_stub
    assert len(volume_stub.splitlines()) < 20


@pytest.mark.parametrize(
    ("trade_horizon", "scanner_name", "asset_class", "expected"),
    [
        ("swing", "stock_strategy", "stock", 1.5),
        ("intraday", "orb", "stock", 0.4),
        ("swing", "early_movers", "crypto", 1.2),
        ("position", "turtle", "stock", 2.0),
    ],
)
def test_minimum_stop_distance_uses_horizon_and_asset_noise_floor(
    trade_horizon, scanner_name, asset_class, expected
):
    result = minimum_stop_distance(
        100.0,
        trade_horizon=trade_horizon,
        scanner_name=scanner_name,
        asset_class=asset_class,
    )
    assert result["distance"] == pytest.approx(expected)


def test_minimum_stop_distance_uses_largest_atr_or_spread_floor():
    result = minimum_stop_distance(
        100.0,
        atr=10.0,
        spread_pct=2.0,
        trade_horizon="swing",
        scanner_name="stock_strategy",
        asset_class="stock",
    )
    assert result["distance"] == pytest.approx(4.5)
    assert result["components"]["atr_floor"] == pytest.approx(4.5)
