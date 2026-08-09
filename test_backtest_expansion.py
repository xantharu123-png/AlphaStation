from datetime import datetime, timedelta

import api
import pytest


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


def test_public_stock_strategy_backtest_aliases_match_internal_rules():
    assert api.BACKTEST_STRATEGY_ALIASES["Momentum Breakout Long"] == "Breakout Long"
    assert api.BACKTEST_STRATEGY_ALIASES["Gap Momentum Long"] == "Gap Up Momentum"
    assert api.BACKTEST_STRATEGY_ALIASES["Gap Momentum Short"] == "Gap Down Short"
    assert api.BACKTEST_STRATEGY_ALIASES["Momentum Breakout Long"] in api.BACKTEST_RULES


def test_scanner_backtest_normalization_keeps_key_metrics():
    raw = {
        "summary": {
            "total_signals": 3,
            "no_fill": 1,
            "unresolved": 1,
            "n_tickers": 25,
            "methodology": "technical_proxy",
            "methodology_warnings": ["not_a_historical_catalyst_test"],
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
            {
                "ticker": "OPEN",
                "grade": "A",
                "outcome": "UNRESOLVED",
                "pnl_pct": 0,
                "r_multiple": 0,
            },
            {"ticker": "MISS", "grade": "B", "outcome": "NO_FILL", "pnl_pct": 0},
        ],
    }

    result = api._normalize_scanner_backtest(raw, "scanner_bi_long", api.ADVANCED_SCANNER_BACKTESTS["scanner_bi_long"], 6)

    assert result["total_signals"] == 3
    assert result["total_filled"] == 2
    assert result["total_trades"] == 1
    assert result["total_decided"] == 1
    assert result["no_fill"] == 1
    assert result["unresolved"] == 1
    assert result["n_tickers"] == 25
    assert result["win_rate"] == 100.0
    assert result["trades"][0]["ticker"] == "TEST"
    assert result["trades"][0]["r_multiple"] == 1.4
    assert "A" in result["stats_by_grade"]
    assert result["methodology"] == "technical_proxy"
    assert result["methodology_warnings"] == ["not_a_historical_catalyst_test"]


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
    assert trade["pnl_pct"] == 20.0
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


def test_crypto_trade_simulation_keeps_tp1_partial_when_runner_trails_out():
    bars = [
        {"date": "2026-01-01", "open": 10.0, "high": 11.6, "low": 9.8, "close": 11.2},
        {"date": "2026-01-02", "open": 10.8, "high": 11.0, "low": 10.2, "close": 10.3},
    ]

    trade = api._simulate_crypto_trade(
        bars=bars,
        entry_idx=0,
        direction="long",
        entry=10.0,
        stop=9.0,
        tp1=11.5,
        tp2=12.5,
        max_hold=2,
        fee_pct=0.0,
    )

    assert trade is not None
    assert trade["outcome"] == "TRAIL_STOP"
    assert trade["tp1_hit"] is True
    assert trade["exit_price"] == 10.875
    assert trade["r_multiple"] == pytest.approx(0.875)


def test_crypto_backtest_enters_after_confirmation_close_not_before(monkeypatch):
    bars = []
    start_date = datetime(2026, 1, 1)
    for idx in range(46):
        close = 9.0
        if 24 <= idx <= 29:
            close = 9.4 + (idx - 24) * 0.1
        elif idx == 30:
            close = 10.1
        elif idx == 31:
            close = 10.2
        elif idx >= 32:
            close = 10.25
        bars.append({
            "date": (start_date + timedelta(days=idx)).date().isoformat(),
            "open": close - 0.05,
            "high": close + 0.1,
            "low": close - 0.2,
            "close": close,
            "volume": 200.0 if idx == 30 else 100.0,
        })
    bars[31]["open"] = 10.1
    bars[32]["open"] = 10.25

    captured = []

    def fake_simulator(_bars, entry_idx, direction, entry, stop, tp1, tp2, max_hold):
        captured.append((entry_idx, entry, direction))
        return {
            "entry_date": _bars[entry_idx]["date"],
            "actual_entry": entry,
            "exit_date": _bars[entry_idx]["date"],
            "exit_price": entry,
            "outcome": "MAX_HOLD",
            "tp1_hit": False,
            "pnl_pct": 0.0,
            "r_multiple": 0.0,
            "is_winner": False,
        }

    monkeypatch.setattr(api, "_crypto_backtest_universe", lambda _max: [{"id": "test", "symbol": "tst"}])
    monkeypatch.setattr(
        api,
        "_validated_exchange_daily_crypto_bars",
        lambda _coin, days: (bars, "binance"),
    )
    monkeypatch.setattr(api, "_simulate_crypto_trade", fake_simulator)

    api._run_crypto_backtest(api.BacktestRequest(
        strategy="crypto_early_mover_long",
        months=1,
        max_tickers=5,
        job_id="crypto_no_lookahead",
    ))

    assert captured
    assert captured[0] == (32, 10.25, "long")


def test_backtest_result_sorts_trades_chronologically_before_drawdown():
    trades = [
        {"ticker": "LATE", "entry_date": "2026-01-10", "pnl_pct": -10.0, "r_multiple": -1, "outcome": "STOP"},
        {"ticker": "EARLY", "entry_date": "2026-01-01", "pnl_pct": 10.0, "r_multiple": 1, "outcome": "TP1"},
    ]

    result = api._build_backtest_result("x", "Test", "long", 1, trades)

    assert [t["ticker"] for t in result["trades"]] == ["EARLY", "LATE"]
    assert result["sum_pnl"] == 0.0
    assert result["compounded_return"] == -1.0
    assert result["max_drawdown"] == 10.0


def test_crypto_trade_simulation_rejects_invalid_long_levels():
    bars = [
        {"date": "2026-01-01", "open": 10.0, "high": 10.5, "low": 9.9, "close": 10.2},
        {"date": "2026-01-02", "open": 10.0, "high": 13.0, "low": 10.0, "close": 12.5},
    ]

    trade = api._simulate_crypto_trade(
        bars=bars,
        entry_idx=1,
        direction="long",
        entry=10.0,
        stop=10.5,
        tp1=11.5,
        tp2=12.5,
        max_hold=1,
        fee_pct=0.0,
    )

    assert trade is None


def test_crypto_trade_simulation_rejects_wrong_side_and_duplicate_targets():
    bars = [
        {"date": "2026-01-01", "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0},
        {"date": "2026-01-02", "open": 10.0, "high": 13.0, "low": 7.0, "close": 10.0},
    ]

    assert api._simulate_crypto_trade(
        bars=bars,
        entry_idx=1,
        direction="long",
        entry=10.0,
        stop=9.0,
        tp1=9.5,
        tp2=12.0,
        max_hold=1,
        fee_pct=0.0,
    ) is None
    assert api._simulate_crypto_trade(
        bars=bars,
        entry_idx=1,
        direction="short",
        entry=10.0,
        stop=11.0,
        tp1=8.5,
        tp2=8.5,
        max_hold=1,
        fee_pct=0.0,
    ) is None


def test_backtest_progress_lifecycle():
    job_id = "unit_progress_job"

    api._backtest_progress_update(job_id, "running", 0.42, "Halbzeit", total_items=10, done_items=4)
    progress = api.get_backtest_progress(job_id)

    assert progress["job_id"] == job_id
    assert progress["status"] == "running"
    assert progress["pct"] == 0.42
    assert progress["percent"] == 42.0
    assert progress["message"] == "Halbzeit"
    assert progress["total_items"] == 10
    assert progress["done_items"] == 4


def test_backtest_verdict_blocks_unprofitable_strategy():
    trades = [
        {"ticker": "A", "entry_date": "2026-01-01", "pnl_pct": -8.0, "r_multiple": -1.0, "outcome": "STOP"},
        {"ticker": "B", "entry_date": "2026-01-02", "pnl_pct": -6.0, "r_multiple": -1.0, "outcome": "STOP"},
        {"ticker": "C", "entry_date": "2026-01-03", "pnl_pct": 2.0, "r_multiple": 0.3, "outcome": "TP1"},
    ] * 8

    result = api._build_backtest_result("x", "Weak Strategy", "long", 6, trades)

    assert result["profit_factor"] < 1
    assert result["verdict"]["status"] == "blocked"
    assert result["verdict"]["tradable"] is False
    assert "nicht live" in result["verdict"]["summary"].lower()
