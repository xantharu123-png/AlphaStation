import api


def _candidate(symbol="COIN", score=86, entry_score=76, action="LONG_TRIGGER", signal="WARTEN"):
    return {
        "Symbol": symbol,
        "grade": "S",
        "score": score,
        "setup_score": score,
        "entry_score": entry_score,
        "direction": "LONG",
        "trade_action": action,
        "trade_signal": signal,
        "signal_quality": "wait_trigger" if signal == "WARTEN" else "tradeable",
        "risk_level": "LOW",
        "risk_flags": [],
        "live_rr_ratio": 2.2,
        "distance_to_entry_r": 0.18,
        "btc_context": {"tailwind": True},
    }


def test_early_mover_high_quality_trigger_candidate_is_visible():
    row = _candidate()
    assert api._scanner_row_is_trade_signal(row, "early_movers") is True


def test_early_mover_generic_watch_row_is_still_suppressed():
    row = _candidate(action="BEOBACHTEN", signal="BEOBACHTEN")
    row["signal_quality"] = "observe"
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False


def test_early_mover_visible_candidates_are_limited_not_empty_watchlist():
    rows = [_candidate(symbol=f"C{i}", score=90 - (i % 10), entry_score=85 - (i % 8)) for i in range(45)]
    payload = {"coins": rows, "stats": {}}

    filtered = api._apply_signal_only_policy("early_movers", [payload])
    stats = filtered[0]["stats"]

    assert len(filtered[0]["coins"]) == api._EARLY_MOVER_VISIBLE_LIMIT
    assert stats["coins_visible_before_limit"] == 45
    assert stats["coins_trimmed_signal_rows"] == 5
    assert stats["coins_suppressed_watch_rows"] == 0


def test_early_mover_hard_liquidity_risk_stays_hidden():
    row = _candidate()
    row["risk_flags"] = ["thin_orderbook", "market_impact_risk"]
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False
