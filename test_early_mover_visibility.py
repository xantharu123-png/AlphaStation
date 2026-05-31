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


def test_early_mover_pre_breakout_coil_is_visible_without_daily_score_overfit():
    row = _candidate(symbol="COIL", score=58, entry_score=8, action="LONG_TRIGGER", signal="WARTEN")
    row.update({
        "Change24h": 4.4,
        "BtcRelative24h": 3.2,
        "risk_flags": ["btc_caution", "requires_5m_trigger"],
        "risk_level": "MEDIUM",
        "live_rr_ratio": 3.1,
        "distance_to_entry_r": 0.14,
        "btc_context": {"tailwind": False, "btc_24h": 0.2, "btc_7d": -4.2, "alpha_24h": 3.2},
        "target_quality": "STRUCTURAL",
    })
    trigger = {
        "ok": False,
        "reason": "no_fresh_5m_trigger",
        "timeframe": "5m",
        "pre_breakout_score": 96,
        "pre_breakout_reason": "5m_coil_near_breakout",
        "pre_breakout_reasons": ["vwap_hold", "higher_lows", "compression", "near_range_high"],
        "execution_score": 48,
    }

    api._apply_early_mover_signal_state(row, trigger)

    assert row["trade_signal"] == "EXPLOSION_ARMED"
    assert row["explosion_score"] >= api._ALERT_MIN_SCORE
    assert api._scanner_row_is_trade_signal(row, "early_movers") is True


def test_early_mover_hard_btc_dump_still_blocks_long_visibility():
    row = _candidate(symbol="DUMP", score=92, entry_score=90, action="LONG_TRIGGER", signal="WARTEN")
    row.update({
        "Change24h": 5.2,
        "BtcRelative24h": 8.8,
        "risk_flags": ["btc_headwind"],
        "live_rr_ratio": 3.5,
        "distance_to_entry_r": 0.05,
        "btc_context": {"tailwind": False, "btc_24h": -3.4, "btc_7d": -8.1, "alpha_24h": 8.8},
    })
    trigger = {"ok": False, "timeframe": "5m", "pre_breakout_score": 98, "execution_score": 55}

    api._apply_early_mover_signal_state(row, trigger)

    assert row["trade_signal"] != "EXPLOSION_ARMED"
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False
