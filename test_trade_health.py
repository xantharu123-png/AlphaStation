import pytest

from modules.trade_health import calculate_trade_health


def test_clean_long_breakout_is_tradeable():
    row = {
        "ticker": "TEST",
        "direction": "LONG",
        "current_price": 10.05,
        "entry": 10.00,
        "stop": 9.50,
        "target1": 11.00,
        "target2": 12.00,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.86,
        "dollar_volume": 8_000_000,
    }

    health = calculate_trade_health(row, "orb")

    assert health["decision"] == "TRADEABLE"
    assert health["entry_quality"] == "GOOD"
    assert health["fakeout_risk"] == "LOW"
    assert health["chase_risk"] == "LOW"
    assert health["metrics"]["live_rr"] >= 2.0


def test_chased_long_after_tp1_is_no_trade():
    row = {
        "ticker": "FOMO",
        "direction": "LONG",
        "current_price": 11.20,
        "entry": 10.00,
        "stop": 9.50,
        "target1": 11.00,
        "target2": 12.00,
        "rvol": 1.8,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.70,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "orb")

    assert health["decision"] == "NO_TRADE"
    assert health["entry_quality"] == "CHASE"
    assert health["chase_risk"] in {"HIGH", "CRITICAL"}
    assert any("TP1" in warning for warning in health["warnings"])


def test_strong_momentum_after_tp1_waits_for_continuation_instead_of_no_trade():
    row = {
        "ticker": "TREND",
        "direction": "LONG",
        "current_price": 11.20,
        "entry": 10.00,
        "stop": 9.50,
        "target1": 11.00,
        "target2": 12.80,
        "rvol": 3.4,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.88,
        "dollar_volume": 25_000_000,
    }

    health = calculate_trade_health(row, "orb")

    assert health["decision"] == "WAIT_FOR_CONTINUATION"
    assert health["continuation_watch"] is True
    assert health["entry_quality"] == "CHASE"
    assert not health["exclusion_reasons"]
    assert health["tactical_reasons"]


def test_live_rr_recomputes_instead_of_trusting_planned_rr():
    row = {
        "ticker": "LATE",
        "direction": "LONG",
        "current_price": 10.80,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.00,
        "tp2": 12.00,
        "risk_reward": 4.0,
        "rvol": 2.0,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "dollar_volume": 8_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["metrics"]["live_rr"] < 1.0
    assert health["decision"] == "NO_TRADE"


def test_live_rr_and_distance_ignore_stale_provided_values_when_levels_exist():
    row = {
        "ticker": "STALE",
        "direction": "LONG",
        "current_price": 10.80,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.00,
        "tp2": 12.00,
        "live_rr_ratio": 5.0,
        "distance_to_entry_r": 0.0,
        "rvol": 2.0,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "dollar_volume": 8_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["metrics"]["live_rr"] < 1.0
    assert health["metrics"]["distance_to_entry_r"] == 1.6


def test_duplicate_targets_are_never_tradeable_even_with_stale_live_rr():
    row = {
        "ticker": "DUP",
        "direction": "LONG",
        "current_price": 10.0,
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 11.0,
        "tp2": 11.0,
        "live_rr_ratio": 4.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["decision"] == "NO_TRADE"
    assert health["trade_geometry_valid"] is False
    assert "invalid_trade_geometry" in health["exclusion_reasons"]
    assert health["metrics"]["live_rr"] == 0.0


def test_wrong_side_target_is_never_tradeable():
    row = {
        "ticker": "WRONG",
        "direction": "SHORT",
        "current_price": 20.0,
        "entry": 20.0,
        "stop": 20.5,
        "tp1": 20.2,
        "tp2": 19.0,
        "risk_reward": 5.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.15,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "bi_short")

    assert health["decision"] == "NO_TRADE"
    assert health["trade_geometry_valid"] is False
    assert "invalid_trade_geometry" in health["exclusion_reasons"]
    assert health["metrics"]["live_rr"] == 0.0


def test_missing_tp2_waits_for_trigger_instead_of_claiming_tradeable():
    row = {
        "ticker": "PARTIAL",
        "direction": "LONG",
        "current_price": 10.0,
        "entry": 10.0,
        "stop": 9.5,
        "tp1": 11.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["decision"] == "WAIT_FOR_TRIGGER"
    assert health["trade_geometry_valid"] is False
    assert not health["exclusion_reasons"]
    assert any("Entry/Stop/TP" in warning for warning in health["warnings"])


def test_short_distance_uses_short_math():
    row = {
        "ticker": "DOWN",
        "direction": "SHORT",
        "current_price": 19.85,
        "entry": 20.00,
        "stop": 20.50,
        "tp1": 19.00,
        "tp2": 18.50,
        "rvol": 1.2,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.20,
        "dollar_volume": 7_000_000,
    }

    health = calculate_trade_health(row, "bi_short")

    assert health["direction"] == "SHORT"
    assert health["metrics"]["distance_to_entry_r"] == 0.3
    assert health["entry_quality"] == "GOOD"


def test_biotech_negative_flags_block_trade():
    row = {
        "ticker": "BIOX",
        "price": 3.25,
        "risk_flag": "high",
        "negative_flags": "offering|dilution",
        "rvol": 3.0,
        "dollar_volume": 4_000_000,
    }

    health = calculate_trade_health(row, "biotech")

    assert health["decision"] == "NO_TRADE"
    assert health["fakeout_risk"] in {"HIGH", "CRITICAL"}
    assert health["exclusion_reasons"]


def test_scanner_without_levels_waits_for_trigger():
    row = {
        "ticker": "WATCH",
        "price": 12.0,
        "rvol": 2.2,
        "dollar_volume": 9_000_000,
        "risk_flag": "low",
    }

    health = calculate_trade_health(row, "biotech")

    assert health["decision"] == "WAIT_FOR_TRIGGER"
    assert any("Entry/Stop" in warning for warning in health["warnings"])


def test_near_entry_does_not_claim_not_chased_when_entry_is_extended():
    row = {
        "ticker": "NNE",
        "direction": "LONG",
        "current_price": 26.55,
        "entry": 26.20,
        "stop": 22.00,
        "tp1": 31.00,
        "tp2": 35.00,
        "entry_quality": "EXTENDED",
        "distance_to_entry_r": 0.06,
        "rvol": 1.1,
        "vol_confirmed": False,
        "dollar_volume": 12_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    positives = " | ".join(health["positives"])
    assert "nicht gechased" not in positives
    assert health["chase_risk"] in {"HIGH", "MEDIUM"}
    assert any("Chase-Risiko bleibt" in warning for warning in health["warnings"])


def test_high_rvol_does_not_contradict_unconfirmed_breakout_volume():
    row = {
        "ticker": "VOLX",
        "direction": "LONG",
        "current_price": 20.05,
        "entry": 20.00,
        "stop": 19.00,
        "tp1": 22.00,
        "tp2": 24.00,
        "rvol": 3.1,
        "vol_confirmed": False,
        "vwap_aligned": True,
        "close_pos": 0.80,
        "dollar_volume": 20_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    positives = " | ".join(health["positives"])
    warnings = " | ".join(health["warnings"])
    assert "relative Volumenbestaetigung" not in positives
    assert "Breakout-Volumen nicht bestaetigt" in warnings
    assert "RVOL 3.1x hoch" in warnings


def test_late_orb_session_does_not_pollute_fakeout_risk():
    row = {
        "ticker": "ORB",
        "direction": "LONG",
        "current_price": 10.05,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.00,
        "tp2": 12.00,
        "rvol": 2.4,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.82,
        "recent_hold_pct": 1.0,
        "late_session": True,
        "dollar_volume": 12_000_000,
    }

    health = calculate_trade_health(row, "orb")

    assert health["fakeout_risk"] == "LOW"
    assert any("kein Fakeout-Signal" in warning for warning in health["warnings"])


def test_market_context_does_not_pollute_fakeout_risk():
    row = {
        "ticker": "NEWS",
        "direction": "LONG",
        "current_price": 10.05,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.00,
        "tp2": 12.00,
        "rvol": 2.4,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.82,
        "dollar_volume": 12_000_000,
    }
    market_context = {
        "summary": {
            "regime": "NEUTRAL",
            "trade_mode": "SELECTIVE",
            "headline_level": "HIGH",
            "event_level": "LOW",
        }
    }

    health = calculate_trade_health(row, "orb", market_context=market_context)

    assert health["fakeout_risk"] == "LOW"
    assert health["chase_risk"] in {"LOW", "MEDIUM"}


def test_orb_recent_hold_and_wick_drive_real_fakeout_risk():
    row = {
        "ticker": "WICK",
        "direction": "LONG",
        "current_price": 10.05,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.00,
        "tp2": 12.00,
        "rvol": 2.4,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.42,
        "upper_wick_pct": 48,
        "recent_hold_pct": 0.0,
        "dollar_volume": 12_000_000,
    }

    health = calculate_trade_health(row, "orb")

    assert health["fakeout_risk"] in {"HIGH", "CRITICAL"}
    assert any("Recent-Hold" in warning for warning in health["warnings"])


def test_non_tradeable_health_does_not_show_trade_now_positives():
    row = {
        "ticker": "CHASED",
        "direction": "LONG",
        "current_price": 10.85,
        "entry": 10.00,
        "stop": 9.50,
        "tp1": 11.80,
        "tp2": 12.80,
        "rvol": 2.8,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.82,
        "dollar_volume": 15_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    positives = " | ".join(health["positives"])
    assert health["decision"] != "TRADEABLE"
    assert health["chase_risk"] in {"HIGH", "CRITICAL"}
    assert "Live R:R" not in positives
    assert "relative Volumenbestaetigung" not in positives
    assert "Breakout-Volumen bestaetigt" not in positives


# ── S-2 Audit-Fix: Stop-Breach-Erkennung ──

def test_long_stop_breach_invalidates_setup():
    # Audit-Repro: LONG Entry 100 / Stop 95 / Preis 92 war vorher TRADEABLE/health=100
    row = {
        "ticker": "BREACH",
        "direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 110.0,
        "TP2": 120.0,
        "current_price": 92.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 9_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]
    assert health["health_score"] <= 15
    assert health["risk_level"] == "CRITICAL"
    assert health["metrics"]["live_rr"] == 0.0


def test_short_stop_breach_invalidates_setup():
    row = {
        "ticker": "SBREACH",
        "direction": "SHORT",
        "Entry": 100.0,
        "StopLoss": 105.0,
        "TP1": 90.0,
        "TP2": 80.0,
        "current_price": 107.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.15,
        "dollar_volume": 9_000_000,
    }

    health = calculate_trade_health(row, "bi_short")

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]
    assert health["health_score"] <= 15
    assert health["risk_level"] == "CRITICAL"
    assert health["metrics"]["live_rr"] == 0.0


def test_price_exactly_at_stop_is_breach():
    row = {
        "ticker": "ATSTOP",
        "direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 110.0,
        "current_price": 95.0,
        "rvol": 2.0,
        "dollar_volume": 9_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert health["decision"] == "NO_TRADE"
    assert "setup_invalidated_stop_breached" in health["exclusion_reasons"]


def test_pullback_between_stop_and_entry_is_not_breach():
    row = {
        "ticker": "PULL",
        "direction": "LONG",
        "Entry": 100.0,
        "StopLoss": 95.0,
        "TP1": 110.0,
        "TP2": 120.0,
        "current_price": 97.0,
        "rvol": 2.5,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.85,
        "dollar_volume": 9_000_000,
    }

    health = calculate_trade_health(row, "bi_long")

    assert "setup_invalidated_stop_breached" not in health["exclusion_reasons"]
    assert health["decision"] != "NO_TRADE"
    assert health["health_score"] >= 65
    assert any("Richtung Stop" in w for w in health["warnings"])


def test_swing_stop_inside_normal_noise_is_hard_blocked():
    row = {
        "ticker": "TIGHT",
        "direction": "LONG",
        "current_price": 100.0,
        "entry": 100.0,
        "stop": 99.0,
        "target1": 103.0,
        "target2": 105.0,
        "trade_horizon": "swing",
        "asset_class": "stock",
        "rvol": 2.0,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.8,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "stock_strategy")

    assert health["decision"] == "NO_TRADE"
    assert "stop_distance_below_noise_floor" in health["exclusion_reasons"]
    assert health["metrics"]["min_stop_distance"] == pytest.approx(1.5)


def test_swing_stop_outside_noise_floor_is_not_blocked_by_distance():
    row = {
        "ticker": "WIDE",
        "direction": "LONG",
        "current_price": 100.0,
        "entry": 100.0,
        "stop": 98.0,
        "target1": 104.0,
        "target2": 106.0,
        "trade_horizon": "swing",
        "asset_class": "stock",
        "rvol": 2.0,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.8,
        "dollar_volume": 10_000_000,
    }

    health = calculate_trade_health(row, "stock_strategy")

    assert "stop_distance_below_noise_floor" not in health["exclusion_reasons"]
