import api


def _orb_row(ticker, *, current, entry, stop, target1, target2, score=95, entry_quality="GOOD", rvol=2.4):
    return {
        "ticker": ticker,
        "direction": "LONG",
        "current_price": current,
        "entry": entry,
        "stop": stop,
        "target1": target1,
        "target2": target2,
        "score": score,
        "grade": "A",
        "rvol": rvol,
        "vol_confirmed": True,
        "vwap_aligned": True,
        "close_pos": 0.82,
        "recent_hold_pct": 1.0,
        "dollar_volume": 12_000_000,
        "entry_quality": entry_quality,
    }


def test_orb_breakouts_keeps_only_tradeable_signals(monkeypatch):
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {"summary": {}})
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda: ({"GOOD", "WAIT", "NOPE"}, "test"))
    payload = [{
        "breakouts": [
            _orb_row("NOPE", current=11.25, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=99, rvol=1.0),
            _orb_row("WAIT", current=10.10, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=92, entry_quality="EXTENDED"),
            _orb_row("GOOD", current=10.05, entry=10.0, stop=9.5, target1=11.0, target2=12.0, score=90),
        ],
        "failed_breakouts": [],
        "candidates": [],
    }]

    decorated = api._decorate_orb_results(payload, cache_age_seconds=3)[0]
    tickers = [row["ticker"] for row in decorated["breakouts"]]

    assert tickers == ["GOOD"]
    assert decorated["breakout_decision_counts"]["tradeable"] == 1
    assert decorated["breakout_decision_counts"]["wait"] == 1
    assert decorated["breakout_decision_counts"]["no_trade"] == 1
    assert decorated["stats"]["breakouts_raw_count"] == 3
    assert decorated["stats"]["breakouts_suppressed_watch_rows"] == 2
    assert decorated["stats"]["visible_trade_signals"] == 1
