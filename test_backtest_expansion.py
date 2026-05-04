import api


def test_backtest_strategy_list_contains_scanner_and_crypto_profiles():
    strategies = api.list_backtest_strategies()["strategies"]
    ids = {item["id"] for item in strategies}

    assert "scanner_bi_long" in ids
    assert "scanner_bi_short" in ids
    assert "scanner_biotech" in ids
    assert "crypto_early_mover_long" in ids
    assert "crypto_pump_dump_short" in ids

    scanner = next(item for item in strategies if item["id"] == "scanner_bi_long")
    crypto = next(item for item in strategies if item["id"] == "crypto_early_mover_long")
    assert scanner["requires_ticker"] is False
    assert crypto["requires_ticker"] is False


def test_scanner_backtest_normalization_keeps_key_metrics():
    raw = {
        "summary": {
            "total_signals": 2,
            "no_fill": 1,
            "n_tickers": 25,
        },
        "stats_by_grade": {
            "A": {"total": 1, "win_rate": 100.0, "avg_pnl": 4.2, "avg_r": 1.4, "profit_factor": 99.0}
        },
        "trades": [
            {
                "ticker": "TEST",
                "signal_date": "2026-01-02",
                "entry_date": "2026-01-03",
                "actual_entry": 10.0,
                "exit_date": "2026-01-07",
                "exit_price": 10.42,
                "pnl_pct": 4.2,
                "r_multiple": 1.4,
                "direction": "LONG",
                "grade": "A",
                "outcome": "TP1_PARTIAL",
                "tp1_hit": True,
            },
            {"ticker": "MISS", "grade": "B", "outcome": "NO_FILL", "pnl_pct": 0},
        ],
    }

    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", api.ADVANCED_SCANNER_BACKTESTS["scanner_bi_long"], 6)

    assert result["total_signals"] == 2
    assert result["total_trades"] == 1
    assert result["no_fill"] == 1
    assert result["n_tickers"] == 25
    assert result["win_rate"] == 100.0
    assert result["trades"][0]["ticker"] == "TEST"
    assert result["trades"][0]["r_multiple"] == 1.4
    assert "A" in result["stats_by_grade"]


def test_crypto_trade_simulation_long_hits_tp2():
    bars = [
        {"date": "2026-01-01", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2},
        {"date": "2026-01-02", "open": 10.0, "high": 13.0, "low": 10.0, "close": 12.5},
    ]

    trade = api._simulate_crypto_trade(
        bars=bars,
        entry_idx=1,
        direction="long",
        entry=10.0,
        stop=9.0,
        tp1=11.5,
        tp2=12.5,
        max_hold=1,
        fee_pct=0.0,
    )

    assert trade is not None
    assert trade["outcome"] == "TP2"
    assert trade["pnl_pct"] == 25.0
    assert trade["is_winner"] is True


def test_crypto_trade_simulation_short_stops_before_target_when_same_daily_bar():
    bars = [
        {"date": "2026-01-01", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0},
        {"date": "2026-01-02", "open": 10.0, "high": 11.1, "low": 7.0, "close": 8.0},
    ]

    trade = api._simulate_crypto_trade(
        bars=bars,
        entry_idx=1,
        direction="short",
        entry=10.0,
        stop=11.0,
        tp1=8.5,
        tp2=7.5,
        max_hold=1,
        fee_pct=0.0,
    )

    assert trade is not None
    assert trade["outcome"] == "STOP"
    assert trade["pnl_pct"] == -10.0
