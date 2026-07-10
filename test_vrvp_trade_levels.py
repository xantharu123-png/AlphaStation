from modules.vrvp_levels import build_vrvp_structure, apply_vrvp_to_trade_setup


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
