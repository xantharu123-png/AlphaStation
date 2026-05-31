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


def test_early_mover_high_quality_wait_candidate_is_visible_as_scored_candidate():
    row = _candidate()
    assert api._scanner_row_is_trade_signal(row, "early_movers") is True


def test_early_mover_confirmed_trade_candidate_is_visible():
    row = _candidate(signal="JETZT_TRADEN")
    row.update({
        "signal_quality": "tradeable",
        "execution_trigger_ok": True,
        "alertable_crypto": True,
        "execution_quality_score": 92,
    })

    assert api._scanner_row_is_trade_signal(row, "early_movers") is True


def test_early_mover_trade_health_no_trade_overrides_trade_now():
    row = _candidate(symbol="HBAR", signal="JETZT_TRADEN", entry_score=83)
    row.update({
        "signal_quality": "tradeable",
        "execution_trigger_ok": True,
        "alertable_crypto": True,
        "trade_decision": "NO_TRADE",
        "trade_decision_label": "No Trade",
        "trade_health": {
            "decision": "NO_TRADE",
            "decision_label": "No Trade",
            "health_score": 62,
        },
        "trade_setup": {
            "trade_action": "LONG_TRIGGER",
            "entry_status": "JETZT_TRADEN",
        },
    })

    api._apply_trade_health_final_signal(row, "early_movers")

    assert row["trade_signal"] == "NICHT_TRADEN"
    assert row["trade_action"] == "NO_TRADE"
    assert row["entry_status"] == "NO_TRADE"
    assert row["alertable_crypto"] is False
    assert row["execution_trigger_ok"] is False
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False


def test_early_mover_confirmed_trade_clears_stale_wait_flags():
    row = _candidate(symbol="ZEN", score=86, entry_score=80, action="LONG_TRIGGER", signal="WARTEN")
    row.update({
        "entry": 10.0,
        "stop_loss": 9.5,
        "tp1": 10.8,
        "tp2": 11.6,
        "target_quality": "STRUCTURAL",
        "risk_flags": ["observe_only_scanner", "no_intraday_execution_trigger", "requires_5m_trigger", "high_volume_turnover"],
        "risk_reasons": ["no_intraday_execution_trigger", "high_volume_turnover"],
        "trade_setup": {
            "entry": 10.0,
            "stop_loss": 9.5,
            "tp1": 10.8,
            "tp2": 11.6,
            "target_quality": "STRUCTURAL",
            "risk_flags": ["requires_5m_trigger"],
            "warnings": ["no_market_entry", "target_note"],
        },
    })

    api._apply_early_mover_signal_state(row, {
        "ok": True,
        "reason": "adaptive_5m_retest_hold",
        "timeframe": "5m",
        "execution_score": 92,
    })

    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["execution_trigger_ok"] is True
    assert "no_intraday_execution_trigger" not in row["risk_flags"]
    assert "requires_5m_trigger" not in row["risk_flags"]
    assert "no_intraday_execution_trigger" not in row["risk_reasons"]
    assert "requires_5m_trigger" not in row["trade_setup"]["risk_flags"]
    assert "no_market_entry" not in row["trade_setup"]["warnings"]
    assert "high_volume_turnover" in row["risk_flags"]


def test_early_mover_generic_watch_row_is_still_suppressed():
    row = _candidate(action="BEOBACHTEN", signal="BEOBACHTEN")
    row["signal_quality"] = "observe"
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False


def test_early_mover_visible_confirmed_trades_are_limited_not_watchlist():
    rows = []
    for i in range(45):
        row = _candidate(symbol=f"C{i}", score=90 - (i % 10), entry_score=85 - (i % 8), signal="JETZT_TRADEN")
        row.update({
            "signal_quality": "tradeable",
            "execution_trigger_ok": True,
            "alertable_crypto": True,
            "execution_quality_score": 90,
        })
        rows.append(row)
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


def test_early_mover_pre_breakout_coil_is_visible_as_candidate_until_trade_trigger():
    row = _candidate(symbol="COIL", score=88, entry_score=8, action="LONG_TRIGGER", signal="WARTEN")
    row.update({
        "Change24h": 4.4,
        "BtcRelative24h": 3.2,
        "risk_flags": ["btc_caution", "requires_5m_trigger"],
        "risk_level": "MEDIUM",
        "live_rr_ratio": 3.1,
        "distance_to_entry_r": 0.14,
        "btc_context": {"tailwind": False, "btc_24h": 0.2, "btc_7d": -4.2, "alpha_24h": 3.2},
        "target_quality": "STRUCTURAL",
        "entry": 1.0,
        "stop_loss": 0.94,
        "tp1": 1.09,
        "tp2": 1.18,
        "trade_setup": {
            "entry": 1.0,
            "stop_loss": 0.94,
            "tp1": 1.09,
            "tp2": 1.18,
            "target_quality": "STRUCTURAL",
        },
    })
    trigger = {
        "ok": False,
        "reason": "no_fresh_5m_trigger",
        "timeframe": "5m",
        "pre_breakout_score": 96,
        "pre_breakout_ok": True,
        "pre_breakout_reason": "5m_coil_near_breakout",
        "pre_breakout_reasons": ["vwap_hold", "higher_lows", "compression", "near_range_high"],
        "execution_score": 48,
        "htf_context": {"armed_ok": True, "reason": "htf_context_ok", "timeframe": "4h"},
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


def test_early_mover_confirmed_5m_trigger_does_not_hide_weak_setup():
    row = _candidate(symbol="LIVE", score=59, entry_score=77, action="LONG_TRIGGER", signal="JETZT_TRADEN")
    row.update({
        "grade": "B",
        "signal_quality": "tradeable",
        "execution_trigger_ok": True,
        "execution_quality_score": 100,
        "risk_flags": ["requires_5m_trigger"],
        "entry": 1.00,
        "stop_loss": 0.94,
        "tp1": 1.13,
        "tp2": 1.22,
        "Price": 1.01,
        "btc_context": {"tailwind": True, "btc_24h": 0.8, "btc_7d": -1.0, "alpha_24h": 3.0},
    })

    assert api._scanner_row_is_trade_signal(row, "early_movers") is False
    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)
    assert state["alertable_now"] is False
    assert "score_below_alert_threshold" in state["suppression_reasons"]


def test_early_mover_confirmed_5m_trigger_needs_setup_entry_and_execution():
    row = _candidate(symbol="LIVEGOOD", score=88, entry_score=86, action="LONG_TRIGGER", signal="JETZT_TRADEN")
    row.update({
        "signal_quality": "tradeable",
        "execution_trigger_ok": True,
        "alertable_crypto": True,
        "execution_quality_score": 92,
        "risk_flags": ["requires_5m_trigger"],
        "entry": 1.00,
        "stop_loss": 0.94,
        "tp1": 1.13,
        "tp2": 1.22,
        "Price": 1.01,
        "btc_context": {"tailwind": True, "btc_24h": 0.8, "btc_7d": -1.0, "alpha_24h": 3.0},
    })

    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)

    assert state["alertable_now"] is True
    assert state["score"] >= api._ALERT_MIN_SCORE
