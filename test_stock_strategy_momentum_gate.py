from api import (
    STOCK_STRATEGY_CACHE_VERSION,
    _stock_momentum_breakout_gate,
    load_cache_metadata,
    save_cache_file,
)


def test_momentum_breakout_rejects_high_rvol_bounce_below_structure():
    metrics = {
        "history_ok": True,
        "ema20": 89.8,
        "ema50": 96.0,
        "rsi14": 34.0,
        "high_10d": 97.0,
        "high_20d": 98.35,
        "breakout_10d_pct": -14.95,
        "breakout_20d_pct": -16.12,
        "range_pos": 42.0,
        "change_5d": -14.95,
    }

    ok, reasons = _stock_momentum_breakout_gate(
        "Momentum Breakout Long",
        metrics,
        price=82.5,
        change_pct=3.12,
        rvol=36.78,
        close_pos=0.98,
    )

    assert not ok
    assert "price_below_ema20" in reasons
    assert "rsi_too_weak_for_momentum" in reasons
    assert "no_momentum_breakout_structure" in reasons
    assert "bounce_after_recent_selloff" in reasons


def test_momentum_breakout_allows_clean_20d_breakout():
    metrics = {
        "history_ok": True,
        "ema20": 96.0,
        "ema50": 94.0,
        "rsi14": 61.0,
        "high_10d": 100.5,
        "high_20d": 100.0,
        "breakout_10d_pct": 1.49,
        "breakout_20d_pct": 2.0,
        "range_pos": 86.0,
        "change_5d": 5.5,
    }

    ok, reasons = _stock_momentum_breakout_gate(
        "Momentum Breakout Long",
        metrics,
        price=102.0,
        change_pct=4.2,
        rvol=2.1,
        close_pos=0.82,
    )

    assert ok
    assert reasons == []


def test_momentum_breakout_allows_quieter_20d_swing_breakout():
    metrics = {
        "history_ok": True,
        "ema20": 40.0,
        "ema50": 39.2,
        "rsi14": 58.0,
        "high_10d": 41.7,
        "high_20d": 41.5,
        "breakout_10d_pct": 0.48,
        "breakout_20d_pct": 0.96,
        "range_pos": 74.0,
        "change_5d": 2.1,
    }

    ok, reasons = _stock_momentum_breakout_gate(
        "Momentum Breakout Long",
        metrics,
        price=41.9,
        change_pct=1.35,
        rvol=1.12,
        close_pos=0.57,
    )

    assert ok
    assert reasons == []


def test_momentum_breakout_allows_clean_10d_breakout_without_20d_high():
    metrics = {
        "history_ok": True,
        "ema20": 48.0,
        "ema50": 46.5,
        "rsi14": 66.0,
        "high_10d": 50.0,
        "high_20d": 55.0,
        "breakout_10d_pct": 0.8,
        "breakout_20d_pct": -8.4,
        "range_pos": 82.0,
        "change_5d": 3.2,
    }

    ok, reasons = _stock_momentum_breakout_gate(
        "Momentum Breakout Long",
        metrics,
        price=50.4,
        change_pct=4.1,
        rvol=1.7,
        close_pos=0.76,
    )

    assert ok
    assert reasons == []


def test_momentum_gate_does_not_affect_other_strategies():
    ok, reasons = _stock_momentum_breakout_gate(
        "Gap Momentum Long",
        {"history_ok": False},
        price=10.0,
        change_pct=6.0,
        rvol=2.0,
        close_pos=0.8,
    )

    assert ok
    assert reasons == []


def test_strategy_cache_metadata_roundtrip(tmp_path):
    cache_path = tmp_path / "strategy_cache.json"

    save_cache_file(
        str(cache_path),
        [{"ticker": "TEST", "score": 88}],
        metadata={
            "cache_version": STOCK_STRATEGY_CACHE_VERSION,
            "diagnostics": {
                "universe_count": 123,
                "raw_matches_before_special_filter": 4,
                "final_results": 2,
            },
        },
    )

    metadata = load_cache_metadata(str(cache_path))

    assert metadata["cache_version"] == STOCK_STRATEGY_CACHE_VERSION
    assert metadata["diagnostics"]["universe_count"] == 123
    assert metadata["diagnostics"]["final_results"] == 2
