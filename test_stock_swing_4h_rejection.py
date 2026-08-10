from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import api
from modules.stock_execution import (
    aggregate_regular_session_4h_bars,
    stock_swing_4h_execution_state,
    stock_swing_4h_short_execution_state,
)


def _bar(open_, high, low, close, volume=100_000):
    return {
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _stable_bars():
    return [
        _bar(199.5, 201.0, 199.0, 200.0, 100_000 + index * 1_000)
        for index in range(8)
    ]


def _ophc_like_high_base():
    return [
        _bar(4.20, 4.40, 4.10, 4.30),
        _bar(4.30, 4.70, 4.20, 4.60),
        _bar(4.60, 5.00, 4.50, 4.90),
        _bar(4.90, 5.50, 4.80, 5.40),
        _bar(5.40, 6.20, 5.30, 6.10),
        _bar(6.10, 7.40, 6.00, 7.20),
        _bar(7.20, 8.80, 7.10, 8.60),
        _bar(8.60, 9.15, 8.25, 8.90),
        _bar(8.90, 9.20, 8.35, 8.75),
        _bar(8.75, 9.10, 8.40, 8.95),
        _bar(8.95, 9.18, 8.38, 9.00),
    ]


def test_tel_like_4h_rejection_waits_for_reclaim():
    bars = _stable_bars() + [
        _bar(210.0, 212.0, 192.0, 195.0, 650_000),
        _bar(195.2, 201.0, 190.9, 195.5, 250_000),
    ]

    state = stock_swing_4h_execution_state(bars)

    assert state["Swing_4H_Execution_Status"] == "WAIT_RECLAIM"
    assert state["Swing_4H_Failed_Breakout"] is True
    assert state["Swing_4H_Reclaim_Level"] >= 201.0
    assert state["Swing_4H_Rejection_Range_Ratio"] >= 1.5
    assert state["Swing_4H_Rejection_Volume_Ratio"] >= 1.8


def test_ordinary_red_4h_pullback_does_not_block_swing():
    bars = _stable_bars() + [
        _bar(201.0, 202.0, 199.0, 200.0, 115_000),
        _bar(200.0, 201.0, 199.5, 200.5, 105_000),
    ]

    state = stock_swing_4h_execution_state(bars)

    assert state["Swing_4H_Execution_Status"] == "CLEAR"


def test_reclaimed_4h_rejection_releases_swing_gate():
    bars = _stable_bars() + [
        _bar(210.0, 212.0, 192.0, 195.0, 650_000),
        _bar(195.0, 204.0, 194.0, 203.0, 300_000),
    ]

    state = stock_swing_4h_execution_state(bars)

    assert state["Swing_4H_Execution_Status"] == "RECLAIMED"
    assert state["Swing_4H_Reclaim_Level"] >= 201.0


def test_swing_rule_maps_unreclaimed_4h_rejection_to_wait_retest():
    row = {
        "Signal_Direction": "LONG",
        "Strategy": "Momentum Breakout Long",
        "change_pct": 3.0,
        "close_pos": 0.8,
        "open_to_current_pct": 1.0,
        "extension_atr": 1.0,
        "upper_wick_pct": 10.0,
        "RVOL": 2.0,
        "Swing_4H_Execution_Status": "WAIT_RECLAIM",
    }

    reasons = api._stock_swing_rule_reasons(row)
    decision = api._alert_decision_from_reasons("stock_strategy", reasons)

    assert "swing_4h_rejection_wait_reclaim" in reasons
    assert decision["decision"] == "WAIT_RETEST"


def test_missing_4h_state_cannot_be_sent_as_trade_now():
    reasons = api._stock_swing_rule_reasons({
        "Signal_Direction": "LONG",
        "Strategy": "Momentum Breakout Long",
        "change_pct": 3.0,
        "close_pos": 0.8,
        "open_to_current_pct": 1.0,
        "extension_atr": 1.0,
        "upper_wick_pct": 10.0,
        "RVOL": 2.0,
        "Swing_4H_Execution_Status": "DATA_UNAVAILABLE",
    })
    decision = api._alert_decision_from_reasons("stock_strategy", reasons)

    assert "swing_4h_state_missing_wait_trigger" in reasons
    assert decision["decision"] == "WAIT_TRIGGER"


def test_regular_session_30m_bars_aggregate_to_two_execution_bars():
    timezone_et = ZoneInfo("America/New_York")
    start = datetime(2026, 7, 20, 9, 30, tzinfo=timezone_et)
    raw_bars = []
    for index in range(13):
        timestamp = start + timedelta(minutes=30 * index)
        raw_bars.append({
            "t": int(timestamp.timestamp() * 1000),
            "o": 100 + index,
            "h": 101 + index,
            "l": 99 + index,
            "c": 100.5 + index,
            "v": 1_000 + index,
        })

    bars = aggregate_regular_session_4h_bars(raw_bars, timezone_et, limit=24)

    assert len(bars) == 2
    assert [bar["source_bar_count"] for bar in bars] == [8, 5]
    assert [bar["partial_source_bar"] for bar in bars] == [False, False]


def test_swing_mail_enrichment_uses_4h_not_5m(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_long_latest_intraday_state",
        lambda ticker: (_ for _ in ()).throw(AssertionError("Swing mail must not fetch 5m bars")),
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda ticker: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "WAIT_RECLAIM",
        },
    )
    row = {
        "Ticker": "TEL",
        "grade": "A",
        "score": 90,
        "Signal_Direction": "LONG",
    }

    enriched = api._enrich_stock_alert_5m_state(
        "stock_strategy",
        row,
        "Momentum Breakout Long",
    )

    assert enriched["Swing_4H_Execution_Status"] == "WAIT_RECLAIM"
    assert "latest_bar_change_pct" not in enriched


def test_unreclaimed_4h_rejection_cannot_be_sent_as_trade_now(monkeypatch):
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda: ({"TEL"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda row, scanner: 90)
    monkeypatch.setattr(api, "_structural_barrier_alert_reason", lambda row: None)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda row, scanner: [])
    monkeypatch.setattr(
        api,
        "_alert_trade_levels",
        lambda row: {
            "valid": True,
            "estimated": False,
            "entry": 195.5,
            "stop": 190.0,
            "tp1": 207.0,
            "tp2": 218.0,
        },
    )
    monkeypatch.setattr(api, "_alert_trade_plan_ok", lambda row: True)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *args, **kwargs: 0)
    row = {
        "Ticker": "TEL",
        "Strategy": "Momentum Breakout Long",
        "grade": "A",
        "score": 90,
        "RVOL": 2.0,
        "Signal_Direction": "LONG",
        "change_pct": 0.2,
        "close_pos": 0.8,
        "open_to_current_pct": 0.1,
        "extension_atr": 0.5,
        "upper_wick_pct": 10.0,
        "Swing_4H_Execution_Status": "WAIT_RECLAIM",
        "trade_setup": {
            "direction": "LONG",
            "entry": 195.5,
            "stop": 190.0,
            "tp1": 207.0,
            "tp2": 218.0,
        },
    }

    state = api._classify_alert_candidate("stock_strategy", row, 1_000_000.0)

    assert state["alertable_now"] is False
    assert "swing_4h_rejection_wait_reclaim" in state["suppression_reasons"]
    assert state["decision"] == "WAIT_RETEST"


def test_low_score_swing_candidate_skips_4h_api_call(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda ticker: (_ for _ in ()).throw(AssertionError("Low-score row must not fetch 4H bars")),
    )
    row = {
        "Ticker": "LOW",
        "grade": "A",
        "score": 79,
        "Signal_Direction": "LONG",
    }

    enriched = api._enrich_stock_alert_5m_state(
        "stock_strategy",
        row,
        "Momentum Breakout Long",
    )

    assert "Swing_4H_Execution_Status" not in enriched


def test_ophc_like_live_red_4h_bar_is_not_a_confirmed_short_breakdown():
    partial = _bar(9.11, 9.19, 8.40, 8.40, 400_000)
    partial["partial_source_bar"] = True

    state = stock_swing_4h_short_execution_state(_ophc_like_high_base() + [partial])

    assert state["Swing_Short_4H_Post_Parabolic"] is True
    assert state["Swing_Short_4H_Latest_Partial"] is True
    assert state["Swing_Short_4H_Execution_Status"] == "WAIT_BREAKDOWN"
    assert state["Swing_Short_4H_Stop_Floor"] > 9.19


def test_post_pump_short_requires_completed_break_and_failed_reclaim():
    first_break = _bar(9.00, 9.05, 8.10, 8.20, 450_000)
    failed_reclaim = _bar(8.20, 8.38, 7.90, 8.00, 300_000)

    first_state = stock_swing_4h_short_execution_state(
        _ophc_like_high_base() + [first_break]
    )
    confirmed_state = stock_swing_4h_short_execution_state(
        _ophc_like_high_base() + [first_break, failed_reclaim]
    )

    assert first_state["Swing_Short_4H_Execution_Status"] == "WAIT_RETEST"
    assert confirmed_state["Swing_Short_4H_Execution_Status"] == "CONFIRMED"
    assert confirmed_state["Swing_Short_4H_Failed_Reclaim"] is True


def test_stock_swing_short_enrichment_reprices_stop_and_blocks_live_break(monkeypatch):
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda ticker: {
            "Swing_Short_4H_Execution_Checked": True,
            "Swing_Short_4H_Execution_Status": "WAIT_BREAKDOWN",
            "Swing_Short_4H_Execution_Reason": "live_4h_candle_not_a_completed_breakdown",
            "Swing_Short_4H_Post_Parabolic": True,
            "Swing_Short_4H_Stop_Floor": 9.2825,
        },
    )
    row = {
        "Ticker": "OPHC",
        "Strategy": "Gap Momentum Short",
        "Signal_Direction": "SHORT",
        "grade": "A",
        "score": 80,
        "change_pct": -6.7,
        "close_pos": 0.02,
        "open_to_current_pct": -7.7,
        "RVOL": 2.4,
        "Entry": 8.50,
        "StopLoss": 8.99,
        "TP1": 7.476,
        "TP2": 7.125,
        "trade_setup": {
            "direction": "SHORT",
            "entry": 8.50,
            "stop": 8.99,
            "tp1": 7.476,
            "tp2": 7.125,
        },
    }

    enriched = api._enrich_stock_alert_5m_state(
        "stock_strategy",
        row,
        "Gap Momentum Short",
    )

    assert enriched["StopLoss"] == 9.29
    assert enriched["trade_setup"]["stop"] == 9.29
    assert enriched["RiskReward"] == 1.52
    assert enriched["Swing_Short_4H_Stop_Geometry_Valid"] is True
    assert "swing_short_4h_wait_breakdown" in enriched["short_block_reasons"]
    assert enriched["alertable_short"] is False


def test_ophc_like_short_is_wait_trigger_and_cannot_pass_mail_gate():
    row = {
        "Strategy": "Gap Momentum Short",
        "Signal_Direction": "SHORT",
        "Swing_Short_4H_Execution_Checked": True,
        "Swing_Short_4H_Execution_Status": "WAIT_BREAKDOWN",
        "Swing_Short_4H_Post_Parabolic": True,
        "Swing_Short_4H_Stop_Geometry_Valid": True,
        "Swing_Short_4H_Required_Stop": 9.29,
        "Entry": 8.50,
        "StopLoss": 9.29,
        "TP1": 7.476,
        "TP2": 7.125,
    }

    mail_ok, reason = api._stock_strategy_mail_quality_state(row)
    decision = api._alert_decision_from_reasons(
        "stock_strategy",
        ["swing_short_4h_wait_breakdown"],
    )

    assert mail_ok is False
    assert reason == "stock_swing_short_mail_blocked_wait_4h_breakdown"
    assert decision["decision"] == "WAIT_TRIGGER"


def test_ophc_like_bars_are_blocked_end_to_end(monkeypatch):
    partial = _bar(9.11, 9.19, 8.40, 8.40, 400_000)
    partial["partial_source_bar"] = True
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_4h_bars",
        lambda ticker, limit=24: _ophc_like_high_base() + [partial],
    )
    row = {
        "Ticker": "OPHC",
        "Strategy": "Gap Momentum Short",
        "Signal_Direction": "SHORT",
        "grade": "A",
        "score": 80,
        "change_pct": -6.7,
        "close_pos": 0.02,
        "open_to_current_pct": -7.7,
        "RVOL": 2.4,
        "Entry": 8.50,
        "StopLoss": 8.99,
        "TP1": 7.476,
        "TP2": 7.125,
    }

    enriched = api._enrich_stock_alert_5m_state(
        "stock_strategy",
        row,
        "Gap Momentum Short",
    )
    mail_ok, reason = api._stock_strategy_mail_quality_state(enriched)
    decision = api._alert_decision_from_reasons(
        "stock_strategy",
        enriched["short_block_reasons"],
    )

    assert enriched["Swing_Short_4H_Execution_Status"] == "WAIT_BREAKDOWN"
    assert enriched["Swing_Short_4H_Latest_Partial"] is True
    assert enriched["StopLoss"] == 9.29
    assert enriched["RiskReward"] == 1.52
    assert mail_ok is False
    assert reason == "stock_swing_short_mail_blocked_wait_4h_breakdown"
    assert decision["decision"] == "WAIT_TRIGGER"
