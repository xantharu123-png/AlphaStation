import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.penny_stock_scanner import (
    _penny_technical_exit_reasons,
    analyze_penny_intraday,
    build_penny_trade_plan,
    evaluate_penny_candidate,
    evaluate_penny_signal_outcome,
    score_broad_penny_candidate,
    summarize_penny_rth_volume,
)


ROOT = Path(__file__).resolve().parent


@pytest.fixture(autouse=True)
def _isolate_penny_trigger_pool(monkeypatch, tmp_path):
    """Keep the short-lived production trigger pool out of every unit test."""
    import api

    monkeypatch.setattr(
        api,
        "PENNY_STOCKS_TRIGGER_POOL_CACHE",
        str(tmp_path / "penny_trigger_pool.json"),
    )


def _market_now():
    """Fixed Wednesday 11:30 ET so intraday tests never depend on wall time."""
    return datetime(2026, 7, 22, 15, 30, tzinfo=timezone.utc).timestamp()


def _bars(now_ts, *, upper_wick=False, stale=False):
    bars = []
    start = now_ts - 16 * 300
    for idx in range(14):
        base = 0.99 + (idx % 4) * 0.005
        bars.append({
            "open": base,
            "high": 1.02 if idx == 10 else base + 0.008,
            "low": base - 0.008,
            "close": base + 0.002,
            "volume": 400_000,
            "timestamp": start + idx * 300,
        })
    bars.append({
        "open": 1.005,
        "high": 1.018,
        "low": 1.000,
        "close": 1.012,
        "volume": 460_000,
        "timestamp": start + 14 * 300,
    })
    bars.append({
        "open": 1.015,
        "high": 1.10 if upper_wick else 1.05,
        "low": 1.012,
        "close": 1.025 if upper_wick else 1.045,
        "volume": 1_080_000,
        "timestamp": now_ts - 300,
    })
    if stale:
        for bar in bars:
            bar["timestamp"] -= 3_600
    return bars


def _compressed_breakout_bars(now_ts):
    """A realistic base: broad range, tight compression, then one closed breakout."""
    start = now_ts - 16 * 300
    candles = [
        (0.990, 1.008, 0.980, 0.998, 360_000),
        (0.998, 1.015, 0.985, 1.004, 370_000),
        (1.004, 1.020, 0.972, 0.990, 390_000),
        (0.990, 1.018, 0.978, 1.006, 410_000),
        (1.006, 1.020, 0.984, 0.994, 400_000),
        (0.994, 1.017, 0.982, 1.008, 405_000),
        (1.008, 1.020, 0.988, 0.998, 395_000),
        (0.998, 1.019, 0.990, 1.010, 410_000),
        (1.010, 1.020, 1.000, 1.006, 380_000),
        (1.006, 1.019, 1.001, 1.012, 390_000),
        (1.012, 1.020, 1.003, 1.008, 385_000),
        (1.008, 1.019, 1.004, 1.014, 400_000),
        (1.014, 1.020, 1.005, 1.010, 395_000),
        (1.010, 1.019, 1.006, 1.015, 405_000),
        (1.012, 1.018, 1.006, 1.014, 460_000),
        (1.015, 1.050, 1.013, 1.045, 1_080_000),
    ]
    return [
        {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "timestamp": start + idx * 300,
        }
        for idx, (open_price, high, low, close, volume) in enumerate(candles)
    ]


def _snapshot():
    return {
        "ticker": "PUMP",
        "price": 1.045,
        "change_pct": 5.0,
        "rvol": 3.2,
        "volume": 2_500_000,
        "dollar_volume": 2_600_000,
        "projected_dollar_volume": 9_000_000,
        "close_position": 0.86,
        "spread_bps": 45,
        "spread_known": True,
        "price_reliable": True,
        "price_age_seconds": 1.0,
        "last_trade_age_seconds": 1.0,
        "bid": 1.044,
        "ask": 1.046,
    }


def _details():
    return {
        "name": "Pump Test Inc",
        "shares_millions": 18,
        "float_shares_millions": 12,
        "market_cap_millions": 45,
        "news_context": {
            "status": "ok",
            "risk_flags": [],
            "warning_flags": [],
            "positive_catalysts": ["verified_contract_award"],
            "catalyst_confidence": 1.0,
        },
        "sec_filing_context": {"status": "ok", "risk_flags": [], "warning_flags": []},
    }


def _targets():
    return [
        {"price": 1.120, "source": "4H/5m VRVP resistance", "weight": 1.8},
        {"price": 1.210, "source": "Daily swing high", "weight": 2.0},
    ]


def test_broad_filter_checks_liquidity_and_spread():
    state = score_broad_penny_candidate(_snapshot())
    assert state["eligible"] is True
    thin = dict(_snapshot(), dollar_volume=20_000, projected_dollar_volume=80_000, spread_bps=600)
    blocked = score_broad_penny_candidate(thin)
    assert blocked["eligible"] is False
    assert "current_dollar_volume_too_low" in blocked["blockers"]
    assert "spread_too_wide_for_monitoring" in blocked["blockers"]


def test_closed_5m_breakout_can_become_buy_now_with_structure():
    now_ts = _market_now()
    row = evaluate_penny_candidate(
        _snapshot(),
        _compressed_breakout_bars(now_ts),
        [],
        details=_details(),
        extra_resistances=_targets(),
        now_ts=now_ts,
    )
    assert row["trade_action"] == "JETZT_KAUFEN"
    assert row["execution_trigger_ok"] is True
    assert row["entry_quality_score"] >= 75
    assert row["dump_risk_score"] <= 45
    assert row["trade_setup"]["target_quality"] == "STRUCTURAL"
    assert row["trade_setup"]["tp2"] > row["trade_setup"]["tp1"] > row["trade_setup"]["entry"]
    assert row["trade_setup"]["stop_loss"] < row["trade_setup"]["entry"]


def test_large_wick_is_not_a_buy_signal_even_with_high_setup_score():
    now_ts = _market_now()
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts, upper_wick=True),
        [],
        details=_details(),
        extra_resistances=_targets(),
        now_ts=now_ts,
    )
    assert row["trade_action"] != "JETZT_KAUFEN"
    assert row["execution_trigger_ok"] is False
    assert "fresh_5m_breakout_or_retest_missing" in row["hard_blockers"]
    assert "large_upper_wick" in row["warnings"]


def test_stale_breakout_never_becomes_buy_now():
    now_ts = _market_now()
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts, stale=True),
        [],
        details=_details(),
        extra_resistances=_targets(),
        now_ts=now_ts,
    )
    assert row["trade_action"] != "JETZT_KAUFEN"
    assert "closed_5m_trigger_stale" in row["hard_blockers"]


def test_unfinished_5m_candle_cannot_create_a_breakout_signal():
    now_ts = _market_now()
    bars = _bars(now_ts)
    bars[-1].update({
        "open": 1.008,
        "high": 1.016,
        "low": 1.000,
        "close": 1.010,
        "volume": 900,
    })
    bars.append({
        "open": 1.010,
        "high": 1.180,
        "low": 1.008,
        "close": 1.170,
        "volume": 50_000,
        "timestamp": int((now_ts - 60) * 1000),
    })

    intraday = analyze_penny_intraday(bars, now_ts=now_ts)

    assert intraday["price"] == 1.010
    assert intraday["breakout_confirmed"] is False
    assert intraday["trigger_confirmed"] is False


def test_missing_5m_volume_baseline_cannot_create_a_penny_trigger():
    now_ts = _market_now()
    bars = _bars(now_ts)
    for bar in bars[2:12]:
        bar["volume"] = 0

    intraday = analyze_penny_intraday(bars, now_ts=now_ts)

    assert intraday["data_ok"] is False
    assert intraday["trigger_confirmed"] is False
    assert intraday["warnings"] == ["insufficient_5m_volume_baseline"]


def test_recent_offering_or_reverse_split_news_blocks_buy_mail_state():
    now_ts = _market_now()
    risky_details = {
        **_details(),
        "news_context": {
            "status": "ok",
            "risk_flags": ["[!!] OFFERING"],
            "positive_catalysts": [],
            "headline": "Company announces registered direct offering",
        },
    }
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts),
        [],
        details=risky_details,
        extra_resistances=_targets(),
        now_ts=now_ts,
    )
    assert row["trade_action"] != "JETZT_KAUFEN"
    assert "recent_dilution_reverse_split_or_company_risk_filing" in row["hard_blockers"]
    assert row["catalyst_context"]["risk_flags"] == ["[!!] OFFERING"]


def test_targets_must_be_distinct_verified_structure_levels():
    now_ts = _market_now()
    intraday = analyze_penny_intraday(_bars(now_ts), now_ts=now_ts)
    plan = build_penny_trade_plan(
        intraday,
        [],
        extra_resistances=[{"price": 1.085, "source": "single resistance", "weight": 2.0}],
    )
    assert plan["valid"] is False
    assert "no_distinct_structural_tp2_at_acceptable_reward" in plan["blockers"]


def test_live_trade_plan_uses_cost_adjusted_not_gross_reward():
    now_ts = _market_now()
    intraday = analyze_penny_intraday(_bars(now_ts), now_ts=now_ts)
    liquid = build_penny_trade_plan(
        intraday,
        [],
        extra_resistances=_targets(),
        entry_price=1.046,
        spread_bps=45,
        slippage_bps=15,
    )
    expensive = build_penny_trade_plan(
        intraday,
        [],
        extra_resistances=_targets(),
        entry_price=1.046,
        spread_bps=300,
        slippage_bps=150,
    )

    assert liquid["valid"] is True
    assert liquid["rr"] < liquid["gross_rr"]
    assert liquid["rr_tp1"] < liquid["gross_rr_tp1"]
    assert liquid["round_trip_cost"] > 0
    expected_net_risk = liquid["risk"] + liquid["round_trip_cost"]
    expected_tp1_rr = (
        liquid["tp1"] - liquid["entry"] - liquid["round_trip_cost"]
    ) / expected_net_risk
    assert liquid["net_risk"] == pytest.approx(expected_net_risk, abs=0.0001)
    assert liquid["rr_tp1"] == pytest.approx(expected_tp1_rr, abs=0.01)
    assert expensive["valid"] is False
    assert "net_effective_rr_below_cost_adjusted_minimum" in expensive["blockers"]


def test_active_position_gets_exit_when_vwap_breaks_on_heavy_red_bar():
    now_ts = _market_now()
    bars = _bars(now_ts)
    bars[-2].update({"open": 1.04, "high": 1.045, "low": 0.98, "close": 0.99, "volume": 3_000})
    bars[-1].update({"open": 0.99, "high": 0.995, "low": 0.93, "close": 0.94, "volume": 4_000})
    row = evaluate_penny_candidate(
        dict(_snapshot(), price=0.94, bid=0.94, ask=0.942, change_pct=-8.0),
        bars,
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 0.90, "tp1": 1.09, "tp2": 1.15},
        },
        now_ts=now_ts,
    )
    assert row["trade_action"] == "JETZT_VERKAUFEN"
    assert row["lifecycle"] == "EXIT"
    assert row["technical_exit_confirmed"] is True
    assert row["exit_reasons"]


def test_high_dump_risk_without_structure_break_does_not_force_exit():
    intraday = {
        "price": 1.24,
        "breakout_level": 1.05,
        "ema20": 1.10,
        "vwap_lost": False,
        "heavy_red_bar": False,
        "volume_no_progress": False,
        "failed_highs": 0,
        "upper_wick_pct": 12.0,
    }
    assert _penny_technical_exit_reasons(intraday, 92) == []


def test_stalling_volume_alone_does_not_force_exit_without_failed_highs():
    intraday = {
        "price": 1.08,
        "breakout_level": 1.05,
        "ema20": 1.03,
        "vwap_lost": False,
        "heavy_red_bar": False,
        "volume_no_progress": True,
        "failed_highs": 0,
        "upper_wick_pct": 20.0,
    }
    assert _penny_technical_exit_reasons(intraday, 70) == []


def test_repeated_failed_breakout_with_distribution_confirms_exit():
    intraday = {
        "price": 1.07,
        "breakout_level": 1.05,
        "ema20": 1.03,
        "vwap_lost": False,
        "heavy_red_bar": False,
        "volume_no_progress": True,
        "failed_highs": 2,
        "upper_wick_pct": 48.0,
    }
    reasons = _penny_technical_exit_reasons(intraday, 68)
    assert "repeated_failed_breakout_with_distribution" in reasons
    assert "high_dump_risk_with_structure_break" in reasons


def test_active_position_does_not_emit_duplicate_buy_signal():
    now_ts = _market_now()
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts),
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
        now_ts=now_ts,
    )
    assert row["trade_action"] == "HALTEN"
    assert row["execution_trigger_ok"] is False


def test_legacy_active_position_keeps_one_stable_event_id_across_candles():
    now_ts = _market_now()
    previous = {
        "active": True,
        "trade_setup": {"entry": 1.045, "stop_loss": 0.90, "tp1": 1.09, "tp2": 1.15},
    }
    first = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts),
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position=previous,
        now_ts=now_ts,
    )
    second = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts + 300),
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position=previous,
        now_ts=now_ts + 300,
    )

    assert first["position_event_id"] == second["position_event_id"]
    assert first["position_event_id"].startswith("PUMP:legacy:")
    assert first["decision_timestamp"] != second["decision_timestamp"]


def test_penny_scanner_is_wired_to_scheduler_api_mail_and_pro_ui():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    auth_source = (ROOT / "modules" / "auth.py").read_text(encoding="utf-8")

    assert '"penny_stocks": {"running": False' in api_source
    assert '"penny_positions": {"running": False' in api_source
    assert '("penny_stocks", _penny_stock_scanner_wrapper)' in api_source
    assert '("penny_positions", _penny_position_monitor_wrapper)' in api_source
    assert "PENNY_STOCKS_MONITOR_CACHE" in api_source
    assert '@app.post("/api/penny-stocks-scan")' in api_source
    assert '@app.get("/api/penny-stocks-results")' in api_source
    assert '@app.get("/api/penny-stocks/replay")' in api_source
    assert "Pennystock KAUFEN" in api_source
    assert "Pennystock VERKAUFEN" in api_source
    assert "function PennyStocksTab" in frontend_source
    assert "activeTab === 'penny-stocks'" in frontend_source
    assert "Drei getrennte Bewertungen" not in frontend_source
    assert "Pennystock Signale" in frontend_source
    assert "Pennystock-Vorstufen anzeigen" in frontend_source
    assert "penny_show_watch_rows" in frontend_source
    assert "Boolean(item?.model_position_active)" in frontend_source
    assert "Entry fast bereit" not in frontend_source
    assert "Maximal 5 triggernahe Setups" not in frontend_source
    assert '"penny-stocks"' in auth_source


def test_penny_buy_mail_explains_confirmed_exit_policy(monkeypatch):
    import api

    captured = {}
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda _scanner: (True, "ok"))
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _timestamp: "US_REGULAR")
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda **_kwargs: ({"PUMP"}, "test"))

    def _revalidate(row, **_kwargs):
        validated = dict(row)
        validated.update({
            "price_observed_at": "2026-08-13T14:00:00+00:00",
            "price_session": "US_REGULAR",
            "quote_evidence_verified": True,
        })
        return validated, "ok"

    monkeypatch.setattr(api, "_penny_revalidate_buy_candidate", _revalidate)
    monkeypatch.setattr(api.time, "time", lambda: 1786629600.0)

    def _capture(subject, body, **kwargs):
        captured.update(subject=subject, body=body, kwargs=kwargs)
        return True

    monkeypatch.setattr(api, "_send_email_alert", _capture)
    sent = api._penny_buy_email([{
        "ticker": "PUMP",
        "grade": "A",
        "trade_score": 84,
        "setup_quality_score": 81,
        "entry_quality_score": 87,
        "dump_risk_score": 21,
        "spread_bps": 45,
        "execution_cost_bps": 75,
        "max_order_notional": 500,
        "trigger_type": "breakout",
        "signal_age_seconds": 60,
        "trade_setup": {
            "entry": 1.0,
            "stop_loss": 0.94,
            "tp1": 1.10,
            "tp2": 1.18,
            "rr": 2.0,
        },
    }])

    assert sent is True
    assert "bestaetigten Strukturbruch" in captured["body"]
    assert "Ein einzelnes Warnmerkmal ist noch kein Exit" in captured["body"]


def test_penny_buy_mail_revalidates_immediately_and_fails_closed(monkeypatch):
    import api

    calls = []
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda _scanner: (True, "ok"))
    monkeypatch.setattr(
        api,
        "_penny_revalidate_buy_candidate",
        lambda _row, **_kwargs: (None, "fresh_closed_5m_trigger_missing"),
    )
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda *args, **kwargs: calls.append((args, kwargs)) or True,
    )

    row = {"ticker": "LATE", "_dedupe_key": "late-key"}
    assert api._penny_buy_email([row]) is False
    assert row["mail_revalidation_reason"] == "fresh_closed_5m_trigger_missing"
    assert calls == []


def test_penny_buy_mail_rejects_multi_candidate_batch(monkeypatch):
    import api

    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda _scanner: (True, "ok"))
    monkeypatch.setattr(
        api,
        "_penny_revalidate_buy_candidate",
        lambda row, **_kwargs: (dict(row), "ok"),
    )
    assert api._penny_buy_email([{"ticker": "ONE"}, {"ticker": "TWO"}]) is False


def test_penny_exit_mail_names_only_confirmed_exit_reasons(monkeypatch):
    import api

    captured = {}
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda _scanner: (True, "ok"))

    def _capture(subject, body, **kwargs):
        captured.update(subject=subject, body=body, kwargs=kwargs)
        return True

    monkeypatch.setattr(api, "_send_email_alert", _capture)
    sent = api._penny_exit_email([{
        "ticker": "PUMP",
        "price": 0.98,
        "active_stop": 0.96,
        "exit_reasons": ["confirmed_two_bar_vwap_loss"],
        "warnings": ["volume_no_progress"],
        "trade_setup": {"stop_loss": 0.94},
    }])

    assert sent is True
    assert "VWAP in zwei abgeschlossenen 5m-Kerzen verloren" in captured["body"]
    assert "volume no progress" not in captured["body"]
    assert "Einzelne Warnmerkmale loesen diese Mail nicht aus" in captured["body"]


def test_penny_results_expose_only_active_trade_decisions(monkeypatch):
    import api

    now_ts = time.time()
    legacy_rows = [
        {"ticker": "BUY", "trade_action": "JETZT_KAUFEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "HOLD", "trade_action": "HALTEN"},
        {"ticker": "EXIT", "trade_action": "JETZT_VERKAUFEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "BUILD", "trade_action": "BEOBACHTEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "WAIT", "trade_action": "TRIGGER_WARTEN", "trigger_timestamp": now_ts - 360},
        {
            "ticker": "NEAR",
            "trade_action": "TRIGGER_WARTEN",
            "trigger_timestamp": now_ts - 360,
            "setup_quality_score": 70,
            "entry_quality_score": 72,
            "dump_risk_score": 30,
            "hard_blockers": ["fresh_5m_breakout_or_retest_missing", "trade_score_below_80"],
            "trade_setup": {"entry": 1.0, "stop_loss": 0.90, "tp1": 1.20, "tp2": 1.40},
        },
        {"ticker": "STALE_WAIT", "trade_action": "TRIGGER_WARTEN", "trigger_timestamp": now_ts - 1_500},
        {"ticker": "NO", "trade_action": "NICHT_KAUFEN"},
    ]
    monkeypatch.setattr(api, "load_cache_file", lambda path: (legacy_rows, None))
    monkeypatch.setattr(api, "load_cache_metadata", lambda path: {"diagnostics": {}})

    payload = api.get_penny_stock_results()

    assert [row["ticker"] for row in payload["data"]] == ["EXIT", "BUY", "HOLD"]
    assert [row["ticker"] for row in payload["near_entries"]] == ["NEAR"]
    assert payload["near_entries"][0]["near_entry_label"] == "ENTRY FAST BEREIT - 5M TRIGGER FEHLT"

    analysis_rows = api._penny_active_trade_rows(
        legacy_rows,
        include_watch=True,
        cache_age_seconds=60,
        now_ts=now_ts,
    )
    assert [row["ticker"] for row in analysis_rows] == ["BUY", "HOLD", "EXIT", "BUILD", "WAIT", "NEAR"]

    payload_with_watch = api.get_penny_stock_results(include_watch=True)
    assert [row["ticker"] for row in payload_with_watch["data"]] == [
        "EXIT", "BUY", "HOLD", "WAIT", "NEAR", "BUILD",
    ]
    assert payload_with_watch["include_watch"] is True


def test_penny_results_auto_show_strong_trigger_prep_when_no_active_rows(monkeypatch):
    import api

    now_ts = time.time()
    rows = [
        {
            "ticker": "PREP",
            "trade_action": "TRIGGER_WARTEN",
            "trigger_timestamp": now_ts - 360,
            "trade_score": 76,
            "setup_quality_score": 72,
            "entry_quality_score": 68,
            "dump_risk_score": 34,
            "trade_setup": {"entry": 1.0, "stop_loss": 0.9, "tp1": 1.2, "tp2": 1.45},
        },
        {
            "ticker": "WEAK",
            "trade_action": "TRIGGER_WARTEN",
            "trigger_timestamp": now_ts - 360,
            "trade_score": 49,
            "setup_quality_score": 70,
            "entry_quality_score": 70,
            "dump_risk_score": 20,
            "trade_setup": {"entry": 1.0, "stop_loss": 0.9, "tp1": 1.2, "tp2": 1.45},
        },
    ]
    monkeypatch.setattr(api, "load_cache_file", lambda path: (rows, None))
    monkeypatch.setattr(api, "load_cache_metadata", lambda path: {"diagnostics": {}})

    payload = api.get_penny_stock_results()

    assert payload["include_watch"] is False
    assert payload["auto_show_trigger_prep"] is True
    assert [row["ticker"] for row in payload["data"]] == ["PREP"]
    assert payload["data"][0]["signal_label"] == "TRIGGER ABWARTEN"
    assert payload["diagnostics"]["combined_buy_now"] == 0


def test_active_position_data_gap_remains_visible_without_watch_toggle():
    import api

    row = {
        "ticker": "OPEN",
        "trade_action": "TRIGGER_WARTEN",
        "model_position_active": True,
        "lifecycle": "DATENLUECKE",
    }

    visible = api._penny_active_trade_rows(
        [row],
        include_watch=False,
        cache_age_seconds=90,
        now_ts=_market_now(),
    )

    assert len(visible) == 1
    assert visible[0]["ticker"] == "OPEN"
    assert visible[0]["signal_age_seconds"] == 90


def test_position_monitor_replaces_stale_discovery_row(monkeypatch):
    import api

    cached_at = datetime.now(timezone.utc).isoformat()
    discovery_rows = [{
        "ticker": "OPEN",
        "trade_action": "JETZT_KAUFEN",
        "trigger_timestamp": time.time() - 360,
        "trade_score": 92,
    }]
    monitor_rows = [{
        "ticker": "OPEN",
        "trade_action": "HALTEN",
        "model_position_active": True,
        "trade_score": 80,
    }]

    def load_rows(path):
        if path == api.PENNY_STOCKS_MONITOR_CACHE:
            return monitor_rows, cached_at
        return discovery_rows, cached_at

    monkeypatch.setattr(api, "load_cache_file", load_rows)
    monkeypatch.setattr(api, "load_cache_metadata", lambda path: {"diagnostics": {}})

    payload = api.get_penny_stock_results()

    assert len(payload["data"]) == 1
    assert payload["data"][0]["ticker"] == "OPEN"
    assert payload["data"][0]["trade_action"] == "HALTEN"


def test_penny_near_entry_rows_reject_hard_liquidity_blocker():
    import api

    now_ts = _market_now()
    row = {
        "ticker": "THIN",
        "trade_action": "TRIGGER_WARTEN",
        "trigger_timestamp": now_ts - 360,
        "setup_quality_score": 80,
        "entry_quality_score": 80,
        "dump_risk_score": 20,
        "hard_blockers": ["current_dollar_volume_below_500k"],
        "trade_setup": {"entry": 1.0, "stop_loss": 0.90, "tp1": 1.20, "tp2": 1.40},
    }

    assert api._penny_near_entry_rows([row], cache_age_seconds=60, now_ts=now_ts) == []


def test_penny_results_expire_old_trigger_and_stale_cache(monkeypatch):
    import api

    now_ts = _market_now()
    rows = [
        {"ticker": "FRESH", "trade_action": "JETZT_KAUFEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "OLD", "trade_action": "JETZT_KAUFEN", "trigger_timestamp": now_ts - 1_500},
        {"ticker": "NO_TS", "trade_action": "JETZT_KAUFEN"},
    ]

    fresh = api._penny_active_trade_rows(rows, cache_age_seconds=120, now_ts=now_ts)
    stale = api._penny_active_trade_rows(rows, cache_age_seconds=900, now_ts=now_ts)

    assert [row["ticker"] for row in fresh] == ["FRESH"]
    assert fresh[0]["signal_age_seconds"] == 60
    assert stale == []


def test_fresh_exit_uses_decision_time_not_the_old_entry_trigger():
    import api

    now_ts = _market_now()
    row = {
        "ticker": "EXIT",
        "trade_action": "JETZT_VERKAUFEN",
        "trigger_timestamp": now_ts - 86_400,
        "decision_timestamp": now_ts - 30,
        "position_event_id": "EXIT:position-1",
    }

    visible = api._penny_active_trade_rows([row], cache_age_seconds=30, now_ts=now_ts)

    assert [item["ticker"] for item in visible] == ["EXIT"]
    assert visible[0]["signal_age_seconds"] == 30


def test_api_wrapper_tracks_model_position_when_buy_mail_fails(monkeypatch, tmp_path):
    import api

    now_ts = _market_now()
    snapshot_payload = {
        "tickers": [{
            "ticker": "PUMP",
            "day": {"c": 1.045, "o": 0.99, "h": 1.05, "l": 0.95, "v": 9_000_000},
            "prevDay": {"c": 0.995, "v": 2_812_500},
            "lastTrade": {"p": 1.045},
            "lastQuote": {"p": 1.044, "P": 1.046, "t": int(now_ts * 1_000_000_000)},
        }],
    }

    class Response:
        status_code = 200

        def json(self):
            return snapshot_payload

    cache = tmp_path / "penny.json"
    state = tmp_path / "state.json"
    references = tmp_path / "references.json"
    daily = tmp_path / "daily.json"
    news = tmp_path / "news.json"
    sec = tmp_path / "sec.json"
    dedupe = tmp_path / "email_dedupe.json"
    sent = []

    monkeypatch.setattr(api, "PENNY_STOCKS_CACHE", str(cache))
    monkeypatch.setattr(api, "PENNY_STOCKS_STATE", str(state))
    monkeypatch.setattr(api, "PENNY_STOCKS_REFERENCE_CACHE", str(references))
    monkeypatch.setattr(api, "PENNY_STOCKS_DAILY_CACHE", str(daily))
    monkeypatch.setattr(api, "PENNY_STOCKS_NEWS_CACHE", str(news))
    monkeypatch.setattr(api, "PENNY_STOCKS_SEC_CACHE", str(sec))
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(dedupe))
    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PUMP"}, "test"))
    monkeypatch.setattr(api, "_us_equity_expected_volume_fraction", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "get_ticker_details", lambda *args, **kwargs: _details())
    # Zeitbombe fixen (2026-07-28): _penny_news_context nutzt die ECHTE Uhr
    # (age_factor bis 7 Tage) — fixes Datum altert den Katalysator weg und
    # kippte setup_quality unter 70. Dynamisches Datum = immer frisch,
    # Muster wie Zeilen 1311/1322/1335 in dieser Datei.
    monkeypatch.setattr(api, "get_ticker_news", lambda *args, **kwargs: [{
        "title": "Pump Test wins contract award",
        "published": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sentiment": "positive",
    }])
    monkeypatch.setattr(
        api,
        "_penny_fetch_sec_filing_context",
        lambda *args, **kwargs: {"status": "ok", "risk_flags": [], "warning_flags": []},
    )
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _compressed_breakout_bars(now_ts))
    monkeypatch.setattr(api, "_penny_fetch_live_spread", lambda *args, **kwargs: {
        "bid": 1.044,
        "ask": 1.046,
        "spread_bps": 19.1,
        "spread_known": True,
        "observed_ts": now_ts,
        "receipt_ts": now_ts,
        "quote_age_seconds": 0.0,
    })
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *args, **kwargs: {
            "ok": True,
            "bars": [{"timestamp": now_ts, "high": 1.046, "low": 1.044}],
            "first_timestamp": now_ts,
            "last_timestamp": now_ts,
            "source": "polygon_1m_aggs",
        },
    )
    monkeypatch.setattr(api, "_penny_fetch_daily_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_vrvp_resistances", lambda *args, **kwargs: _targets())
    # Simulate SMTP failure: UI/cache still published a validated entry, so
    # the model position must be tracked for later HOLD/EXIT decisions.
    monkeypatch.setattr(api, "_penny_buy_email", lambda rows: sent.extend(rows) or False)
    monkeypatch.setattr(api, "_penny_management_email", lambda rows: False)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *args, **kwargs: None)

    api._penny_stock_scanner_wrapper()

    rows, _ = api.load_cache_file(str(cache))
    assert rows[0]["ticker"] == "PUMP"
    assert rows[0]["trade_action"] == "JETZT_KAUFEN"
    assert len(sent) == 1
    state_payload = api._penny_load_dict(str(state))
    assert state_payload["tickers"]["PUMP"]["active"] is True
    assert state_payload["tickers"]["PUMP"]["last_action"] == "JETZT_KAUFEN"
    assert state_payload["tickers"]["PUMP"].get("buy_email_sent") is False
    assert state_payload["tickers"]["PUMP"].get("entry_notification_sent") is False
    assert state_payload["tickers"]["PUMP"].get("entry_notification_error") == "buy_mail_failed"
    assert not api._email_dedupe_active(sent[0]["_dedupe_key"], 6 * 3600, now=now_ts + 1)


def test_discovery_defers_active_positions_to_five_minute_monitor(monkeypatch, tmp_path):
    import api

    now_ts = _market_now()

    class Response:
        status_code = 200

        def json(self):
            return {
                "tickers": [{
                    "ticker": "OPEN",
                    "day": {"c": 1.05, "o": 1.00, "h": 1.08, "l": 0.98, "v": 2_000_000},
                    "prevDay": {"c": 1.00, "v": 1_000_000},
                    "lastTrade": {"p": 1.05},
                    "lastQuote": {"p": 1.049, "P": 1.051, "t": int(now_ts * 1_000_000_000)},
                }],
            }

    paths = {
        "PENNY_STOCKS_CACHE": tmp_path / "penny.json",
        "PENNY_STOCKS_STATE": tmp_path / "state.json",
        "PENNY_STOCKS_REFERENCE_CACHE": tmp_path / "references.json",
        "PENNY_STOCKS_DAILY_CACHE": tmp_path / "daily.json",
        "PENNY_STOCKS_NEWS_CACHE": tmp_path / "news.json",
        "PENNY_STOCKS_SEC_CACHE": tmp_path / "sec.json",
        "_EMAIL_DEDUPE_FILE": tmp_path / "email_dedupe.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(api, name, str(path))
    api._penny_save_dict(str(paths["PENNY_STOCKS_STATE"]), {
        "tickers": {
            "OPEN": {
                "active": True,
                "last_seen": now_ts,
                "buy_entry": 1.00,
                "trade_setup": {"entry": 1.00, "stop_loss": 0.94, "tp1": 1.10, "tp2": 1.18},
            },
        },
    })

    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"OPEN"}, "test"))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_penny_buy_email", lambda rows: False)
    monkeypatch.setattr(api, "_penny_management_email", lambda rows: False)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("active position reached discovery")),
    )

    api._penny_stock_scanner_wrapper()

    rows, _ = api.load_cache_file(str(paths["PENNY_STOCKS_CACHE"]))
    metadata = api.load_cache_metadata(str(paths["PENNY_STOCKS_CACHE"]))
    state = api._penny_load_state_tickers()
    assert rows == []
    assert metadata["diagnostics"]["active_deferred_to_position_monitor"] == 1
    assert state["OPEN"]["active"] is True


def test_older_discovery_state_cannot_overwrite_newer_monitor_state(monkeypatch, tmp_path):
    import api

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(api, "PENNY_STOCKS_STATE", str(state_path))
    api._penny_save_dict(str(state_path), {
        "tickers": {
            "OPEN": {
                "active": True,
                "last_seen": 200.0,
                "active_stop": 0.99,
                "last_action": "HALTEN",
            },
        },
    })

    merged = api._penny_merge_state_tickers({
        "OPEN": {
            "active": False,
            "last_seen": 100.0,
            "active_stop": 0.90,
            "last_action": "JETZT_VERKAUFEN",
        },
    }, now_ts=300.0)

    assert merged["OPEN"]["active"] is True
    assert merged["OPEN"]["active_stop"] == 0.99
    assert merged["OPEN"]["last_action"] == "HALTEN"


def test_newer_discovery_cannot_deactivate_emailed_position(monkeypatch, tmp_path):
    import api

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(api, "PENNY_STOCKS_STATE", str(state_path))
    api._penny_save_dict(str(state_path), {
        "tickers": {
            "OPEN": {
                "active": True,
                "last_seen": 100.0,
                "position_event_id": "OPEN:position-1",
                "buy_email_sent": True,
                "last_action": "JETZT_KAUFEN",
            },
        },
    })

    merged = api._penny_merge_state_tickers({
        "OPEN": {
            "active": False,
            "last_seen": 200.0,
            "last_action": "BEOBACHTEN",
        },
    }, now_ts=300.0)

    assert merged["OPEN"]["active"] is True
    assert merged["OPEN"]["position_event_id"] == "OPEN:position-1"
    assert merged["OPEN"]["last_action"] == "JETZT_KAUFEN"


def test_only_matching_successful_exit_can_close_active_position(monkeypatch, tmp_path):
    import api

    state_path = tmp_path / "state.json"
    monkeypatch.setattr(api, "PENNY_STOCKS_STATE", str(state_path))
    api._penny_save_dict(str(state_path), {
        "tickers": {
            "OPEN": {
                "active": True,
                "last_seen": 100.0,
                "position_event_id": "OPEN:position-1",
                "buy_email_sent": True,
            },
        },
    })

    rejected = api._penny_merge_state_tickers({
        "OPEN": {
            "active": False,
            "last_seen": 200.0,
            "position_event_id": "OPEN:wrong-position",
            "exit_email_sent": True,
            "last_action": "JETZT_VERKAUFEN",
        },
    }, now_ts=250.0)
    assert rejected["OPEN"]["active"] is True

    accepted = api._penny_merge_state_tickers({
        "OPEN": {
            "active": False,
            "last_seen": 300.0,
            "position_event_id": "OPEN:position-1",
            "exit_email_sent": True,
            "last_action": "JETZT_VERKAUFEN",
        },
    }, now_ts=350.0)
    assert accepted["OPEN"]["active"] is False
    assert accepted["OPEN"]["exit_email_sent"] is True


def test_reentry_gets_new_position_event_id_after_exit():
    import api

    previous = {
        "active": False,
        "position_event_id": "PUMP:old-position",
        "last_exit_event_id": "PUMP:old-position",
        "exit_email_sent": True,
    }
    new_position_id = api._penny_position_event_id({
        "ticker": "PUMP",
        "trigger_timestamp": 222.0,
    }, previous)

    assert new_position_id == "PUMP:222"
    assert new_position_id != previous["position_event_id"]


def test_position_monitor_keeps_position_active_when_exit_mail_fails(monkeypatch, tmp_path):
    import api

    now_ts = _market_now()

    class Response:
        status_code = 200

        def json(self):
            return {
                "tickers": [{
                    "ticker": "OPEN",
                    "day": {"c": 0.92, "o": 1.00, "h": 1.01, "l": 0.90, "v": 3_000_000},
                    "prevDay": {"c": 1.00, "v": 1_000_000},
                    "lastTrade": {"p": 0.92},
                    "lastQuote": {"p": 0.919, "P": 0.921, "t": int(now_ts * 1_000_000_000)},
                }],
            }

    paths = {
        "PENNY_STOCKS_MONITOR_CACHE": tmp_path / "monitor.json",
        "PENNY_STOCKS_STATE": tmp_path / "state.json",
        "PENNY_STOCKS_REFERENCE_CACHE": tmp_path / "references.json",
        "PENNY_STOCKS_DAILY_CACHE": tmp_path / "daily.json",
        "PENNY_STOCKS_NEWS_CACHE": tmp_path / "news.json",
        "PENNY_STOCKS_SEC_CACHE": tmp_path / "sec.json",
        "_EMAIL_DEDUPE_FILE": tmp_path / "email_dedupe.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(api, name, str(path))
    api._penny_save_dict(str(paths["PENNY_STOCKS_STATE"]), {
        "tickers": {
            "OPEN": {
                "active": True,
                "last_seen": now_ts - 300,
                "buy_entry": 1.00,
                "trade_setup": {"entry": 1.00, "stop_loss": 0.95, "tp1": 1.10, "tp2": 1.18},
            },
        },
    })

    exit_row = {
        "ticker": "OPEN",
        "price": 0.92,
        "trade_action": "JETZT_VERKAUFEN",
        "trigger_timestamp": now_ts - 300,
        "model_position_active": True,
        "dump_risk_score": 90,
        "trade_score": 20,
        "trade_setup": {"entry": 1.00, "stop_loss": 0.95, "tp1": 1.10, "tp2": 1.18},
    }
    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _bars(now_ts))
    monkeypatch.setattr(api, "get_ticker_details", lambda *args, **kwargs: _details())
    monkeypatch.setattr(api, "get_ticker_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(
        api,
        "_penny_fetch_sec_filing_context",
        lambda *args, **kwargs: {"status": "ok", "risk_flags": [], "warning_flags": []},
    )
    monkeypatch.setattr(api, "_penny_fetch_daily_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_vrvp_resistances", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_apply_robust_rvol", lambda snapshot, *args, **kwargs: snapshot)
    monkeypatch.setattr(api, "evaluate_penny_candidate", lambda *args, **kwargs: dict(exit_row))
    monkeypatch.setattr(api, "_penny_management_email", lambda rows: False)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *args, **kwargs: None)

    api._penny_position_monitor_wrapper()

    state = api._penny_load_state_tickers()["OPEN"]
    assert state["active"] is True
    assert state["last_action"] == "EXIT_BESTAETIGT_MAIL_FEHLER"
    assert state["exit_email_sent"] is False


def test_spread_and_atr_define_real_breakout_clearance():
    now_ts = _market_now()
    bars = _bars(now_ts)
    bars[-1].update({"open": 1.018, "high": 1.025, "low": 1.016, "close": 1.024, "volume": 1_080_000})

    liquid = analyze_penny_intraday(bars, spread_bps=20, now_ts=now_ts)
    wide = analyze_penny_intraday(bars, spread_bps=180, now_ts=now_ts)

    assert liquid["breakout_confirmed"] is True
    assert wide["breakout_confirmed"] is False
    assert wide["breakout_confirmation_level"] > liquid["breakout_confirmation_level"]


def test_active_model_position_never_says_hold_when_5m_data_is_missing():
    row = evaluate_penny_candidate(
        _snapshot(),
        [],
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.04, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
    )
    assert row["trade_action"] == "TRIGGER_WARTEN"
    assert row["lifecycle"] == "DATENLUECKE"
    assert "BLINDES HALTEN" in row["signal_label"]


def test_fresh_quote_executes_persisted_stop_when_5m_data_is_missing():
    snapshot = dict(_snapshot(), price=0.99, bid=0.99, ask=0.992, spread_bps=20)
    row = evaluate_penny_candidate(
        snapshot,
        [],
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
    )
    assert row["quote_reliable"] is True
    assert row["intraday_reliable"] is False
    assert row["trade_action"] == "JETZT_VERKAUFEN"
    assert row["lifecycle"] == "EXIT"
    assert row["trade_setup"]["stop_loss"] == 1.00


def test_fresh_last_trade_executes_stop_even_when_spread_is_missing():
    snapshot = dict(
        _snapshot(),
        price=0.99,
        bid=None,
        ask=None,
        spread_known=False,
        spread_bps=250,
        price_reliable=True,
        price_age_seconds=2.0,
    )
    row = evaluate_penny_candidate(
        snapshot,
        [],
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
    )
    assert row["price_reliable"] is True
    assert row["quote_reliable"] is False
    assert row["trade_action"] == "JETZT_VERKAUFEN"
    assert row["lifecycle"] == "EXIT"


def test_stale_last_trade_does_not_execute_protective_levels():
    snapshot = dict(
        _snapshot(),
        price=0.99,
        bid=None,
        ask=None,
        spread_known=False,
        spread_bps=250,
        price_reliable=False,
        price_age_seconds=600.0,
    )
    row = evaluate_penny_candidate(
        snapshot,
        [],
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
    )
    assert row["price_reliable"] is False
    assert row["trade_action"] == "TRIGGER_WARTEN"
    assert row["lifecycle"] == "DATENLUECKE"


def test_stale_price_with_company_risk_exits_without_false_stop_label():
    snapshot = dict(
        _snapshot(),
        price=0.99,
        bid=None,
        ask=None,
        spread_known=False,
        spread_bps=250,
        price_reliable=False,
        price_age_seconds=600.0,
    )
    details = _details()
    details["sec_filing_context"] = {
        "status": "ok",
        "risk_flags": ["recent_sec_424b5"],
        "warning_flags": [],
    }
    row = evaluate_penny_candidate(
        snapshot,
        [],
        [],
        details=details,
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
    )

    assert row["price_reliable"] is False
    assert row["trade_action"] == "JETZT_VERKAUFEN"
    assert row["lifecycle"] == "EXIT"
    assert row["signal_label"] == "HARTES UNTERNEHMENSRISIKO - JETZT VERKAUFEN"
    assert row["exit_reasons"] == ["hard_company_risk_detected"]


def test_nearest_overhead_barrier_cannot_be_skipped_for_farther_target():
    now_ts = _market_now()
    intraday = analyze_penny_intraday(_bars(now_ts), spread_bps=20, now_ts=now_ts)
    plan = build_penny_trade_plan(
        intraday,
        [],
        entry_price=1.046,
        extra_resistances=[
            {"price": 1.078, "source": "near strong VRVP", "weight": 2.0},
            {"price": 1.12, "source": "far daily high", "weight": 2.0},
            {"price": 1.18, "source": "far swing high", "weight": 2.0},
        ],
    )
    assert plan["valid"] is False
    assert "overhead_resistance_too_close" in plan["blockers"]
    assert plan["nearest_barrier"]["distance_r"] < 1.35


def test_live_ask_drift_blocks_chasing_closed_candle_trigger():
    now_ts = _market_now()
    snapshot = dict(_snapshot(), ask=1.07, bid=1.069, price=1.0695, spread_bps=9.4)
    row = evaluate_penny_candidate(
        snapshot,
        _bars(now_ts),
        [],
        details=_details(),
        extra_resistances=[
            {"price": 1.14, "source": "Daily high", "weight": 2.0},
            {"price": 1.22, "source": "Daily swing high", "weight": 2.0},
        ],
        now_ts=now_ts,
    )
    assert row["trade_action"] != "JETZT_KAUFEN"
    assert "live_ask_too_far_above_trigger" in row["hard_blockers"]
    assert row["entry_price_source"] == "live_ask"


def test_original_position_stop_controls_exit_not_recomputed_levels():
    now_ts = _market_now()
    snapshot = dict(_snapshot(), price=0.99, bid=0.99, ask=0.992, spread_bps=20)
    row = evaluate_penny_candidate(
        snapshot,
        _bars(now_ts),
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_position={
            "active": True,
            "trade_setup": {"entry": 1.045, "stop_loss": 1.00, "tp1": 1.09, "tp2": 1.15},
        },
        now_ts=now_ts,
    )
    assert row["trade_action"] == "JETZT_VERKAUFEN"
    assert row["trade_setup"]["stop_loss"] == 1.00


def test_robust_rvol_uses_twenty_day_median_not_single_outlier():
    import api

    now = api.datetime(2026, 7, 10, 16, 0, tzinfo=api.timezone.utc)
    bars = []
    for days_ago in range(25, 0, -1):
        bars.append({
            "volume": 12_000_000 if days_ago == 1 else 1_000_000,
            "timestamp": (now - api.timedelta(days=days_ago)).timestamp() * 1000,
        })
    enriched = api._penny_apply_robust_rvol(
        dict(_snapshot(), volume=2_000_000),
        bars,
        0.5,
        now_utc=now,
    )
    assert enriched["rvol"] == 4.0
    assert enriched["rvol_source"] == "projected_rth_volume_vs_20d_median"


def test_robust_rvol_does_not_replace_missing_recent_days_with_older_volume():
    import api

    now = api.datetime(2026, 7, 10, 16, 0, tzinfo=api.timezone.utc)
    bars = []
    for days_ago in range(40, 20, -1):
        bars.append({
            "volume": 1_000_000,
            "timestamp": (now - api.timedelta(days=days_ago)).timestamp() * 1000,
        })
    for days_ago in range(20, 0, -1):
        bars.append({
            "volume": 1_000_000 if days_ago <= 9 else 0,
            "timestamp": (now - api.timedelta(days=days_ago)).timestamp() * 1000,
        })

    enriched = api._penny_apply_robust_rvol(
        dict(_snapshot(), volume=2_000_000, rvol=1.25),
        bars,
        0.5,
        now_utc=now,
    )

    assert enriched["rvol"] == 1.25
    assert enriched["rvol_history_days"] == 9
    assert enriched["rvol_source"] == "previous_day_volume_fallback"


def test_news_keyword_matching_does_not_flag_software_or_sector():
    import api

    context = api._penny_news_context([{
        "title": "Software sector rallies after second quarter results",
        "published": time.strftime("%Y-%m-%d"),
        "sentiment": "neutral",
    }])
    assert context["risk_flags"] == []


def test_news_shelf_registration_is_warning_not_proven_dilution():
    import api

    context = api._penny_news_context([{
        "title": "Company files shelf registration statement",
        "published": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sentiment": "neutral",
    }])

    assert context["risk_flags"] == []
    assert "shelf_registration_capacity_not_executed" in context["warning_flags"]


def test_news_securities_purchase_agreement_remains_hard_financing_risk():
    import api

    context = api._penny_news_context([{
        "title": "Company enters securities purchase agreement with investors",
        "published": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "sentiment": "neutral",
    }])

    assert "dilutive_financing_agreement" in context["risk_flags"]


def test_replay_is_cost_aware_and_resolves_same_bar_ambiguity_stop_first():
    outcome = evaluate_penny_signal_outcome(
        1.00,
        0.95,
        1.08,
        1.14,
        [{"open": 1.00, "high": 1.15, "low": 0.94, "close": 1.10, "volume": 100_000}],
        spread_bps=100,
        slippage_bps=20,
    )
    assert outcome["outcome"] == "STOP"
    assert outcome["net_r"] == pytest.approx(-1.0)
    assert "stop-first" in outcome["assumption"]


def test_replay_gap_through_stop_is_worse_than_minus_one_net_r():
    outcome = evaluate_penny_signal_outcome(
        1.00,
        0.95,
        1.08,
        1.14,
        [{"open": 0.90, "high": 0.92, "low": 0.88, "close": 0.91, "volume": 100_000}],
        spread_bps=100,
        slippage_bps=20,
    )
    assert outcome["outcome"] == "STOP"
    assert outcome["net_r"] < -1.0


def test_replay_tp1_and_new_breakeven_in_same_bar_uses_adverse_sequence():
    outcome = evaluate_penny_signal_outcome(
        1.00,
        0.95,
        1.08,
        1.14,
        [{"open": 1.02, "high": 1.09, "low": 0.99, "close": 1.06, "volume": 100_000}],
        spread_bps=20,
        slippage_bps=10,
    )
    assert outcome["outcome"] == "BREAKEVEN_STOP"
    assert outcome["tp1_realized"] is True
    assert outcome["remaining_fraction"] == 0.0
    assert outcome["net_r"] > 0
    assert "ambiguous same-bar" in outcome["assumption"]


def test_snapshot_prefers_fresh_last_trade_and_marks_price_reliable(monkeypatch):
    import api

    now_ts = _market_now()
    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    normalized = api._penny_normalize_snapshot({
        "ticker": "PUMP",
        "day": {"c": 1.05, "o": 1.00, "h": 1.08, "l": 0.98, "v": 2_000_000},
        "prevDay": {"c": 1.00, "v": 1_000_000},
        "lastTrade": {"p": 0.99, "t": int((now_ts - 2) * 1_000_000_000)},
        "lastQuote": {},
    }, 1.0)

    assert normalized["price"] == pytest.approx(0.99)
    assert normalized["spread_known"] is False
    assert normalized["price_reliable"] is True
    assert normalized["last_trade_age_seconds"] == pytest.approx(2.0)


def test_snapshot_does_not_mark_undated_day_close_as_reliable(monkeypatch):
    import api

    now_ts = _market_now()
    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    normalized = api._penny_normalize_snapshot({
        "ticker": "PUMP",
        "day": {"c": 1.05, "o": 1.00, "h": 1.08, "l": 0.98, "v": 2_000_000},
        "prevDay": {"c": 1.00, "v": 1_000_000},
        "lastTrade": {},
        "lastQuote": {},
    }, 1.0)

    assert normalized["price"] == pytest.approx(1.05)
    assert normalized["price_reliable"] is False


def test_failed_scan_does_not_refresh_old_cache(monkeypatch, tmp_path):
    import api

    cache = tmp_path / "penny.json"
    monkeypatch.setattr(api, "PENNY_STOCKS_CACHE", str(cache))
    api.save_cache_file(str(cache), [{"ticker": "OLD", "trade_action": "HALTEN"}])
    _, cached_before = api.load_cache_file(str(cache))

    class FailedResponse:
        status_code = 503

    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: FailedResponse())
    try:
        api._penny_stock_scanner_wrapper()
    except RuntimeError:
        pass
    _, cached_after = api.load_cache_file(str(cache))
    assert cached_after == cached_before


def test_deep_selection_includes_every_candidate_and_active_outside_band():
    import api

    candidates = []
    normalized = {}
    for index in range(10):
        snapshot = dict(_snapshot(), ticker=f"T{index}", price=1.0 + index * 0.01)
        normalized[snapshot["ticker"]] = snapshot
        candidates.append((100.0 - index, snapshot, {"eligible": True, "broad_score": 100.0 - index}))
    normalized["ACTIVE"] = dict(_snapshot(), ticker="ACTIVE", price=5.40)

    selected = api._penny_select_deep_candidates(
        candidates,
        normalized,
        {"ACTIVE"},
    )
    assert [item[1]["ticker"] for item in selected] == [
        "ACTIVE", "T0", "T1", "T2", "T3", "T4", "T5", "T6", "T7", "T8", "T9",
    ]


def test_deep_selection_includes_broad_rejected_penny_stocks():
    import api

    eligible = dict(_snapshot(), ticker="LIQUID", price=1.25, dollar_volume=2_000_000)
    rejected = dict(
        _snapshot(),
        ticker="THIN",
        price=0.45,
        dollar_volume=20_000,
        projected_dollar_volume=80_000,
        spread_bps=800,
    )
    eligible_broad = {"eligible": True, "broad_score": 80.0}

    selected = api._penny_select_deep_candidates(
        [(80.0, eligible, eligible_broad)],
        {"LIQUID": eligible, "THIN": rejected},
        set(),
    )

    assert [item[1]["ticker"] for item in selected] == ["LIQUID", "THIN"]
    assert selected[1][2]["eligible"] is False


def test_deep_selection_has_no_hidden_candidate_cap():
    import api

    normalized = {
        f"P{index}": dict(_snapshot(), ticker=f"P{index}", price=1.0)
        for index in range(500)
    }
    selected = api._penny_select_deep_candidates([], normalized, set())

    assert len(selected) == 500
    assert {item[1]["ticker"] for item in selected} == set(normalized)


def test_deep_selection_does_not_drop_lower_ranked_candidates():
    import api

    candidates = []
    normalized = {}
    for index in range(12):
        snapshot = dict(
            _snapshot(),
            ticker=f"T{index}",
            rvol=1.0 + index,
            dollar_volume=100_000 + index * 100_000,
            close_position=0.55 + index * 0.02,
            change_pct=float(index),
        )
        normalized[snapshot["ticker"]] = snapshot
        candidates.append((100.0 - index, snapshot, {"eligible": True, "broad_score": 100.0 - index}))

    selected = api._penny_select_deep_candidates(
        candidates,
        normalized,
        set(),
    )
    symbols = [item[1]["ticker"] for item in selected]

    assert symbols[:2] == ["T0", "T1"]
    assert "T11" in symbols
    assert len(symbols) == len(candidates)


def test_penny_coverage_reports_incomplete_full_scan():
    import api

    stats = api._penny_scan_coverage_stats(
        {"ACTIVE", "TOP1", "TOP2", "ROT1"},
        selected_count=6,
        budget_exhausted=True,
    )

    assert stats == {
        "deep_checked": 4,
        "deep_planned": 6,
        "budget_exhausted": True,
        "deep_completed": False,
        "deep_missing": 2,
    }


def test_penny_coverage_marks_fully_completed_batch():
    import api

    stats = api._penny_scan_coverage_stats(
        {"TOP1", "TOP2", "ROT1", "ROT2"},
        selected_count=4,
        budget_exhausted=False,
    )

    assert stats["deep_checked"] == 4
    assert stats["deep_missing"] == 0
    assert stats["deep_completed"] is True


def test_recent_sec_shelf_registration_is_warning_not_proven_dilution(monkeypatch):
    import api

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    class Response:
        status_code = 200

        def json(self):
            return {
                "filings": {
                    "recent": {
                        "form": ["S-3", "10-Q"],
                        "filingDate": [today, today],
                        "accessionNumber": ["one", "two"],
                    }
                }
            }

    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    context = api._penny_fetch_sec_filing_context("123456")
    assert context["status"] == "ok"
    assert "recent_sec_s_3" not in context["risk_flags"]
    assert "recent_sec_s_3" in context["warning_flags"]


def test_primary_grade_uses_trade_score_not_pump_score():
    now_ts = _market_now()
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts),
        [],
        details=_details(),
        extra_resistances=_targets(),
        now_ts=now_ts,
    )
    assert row["score"] == row["trade_score"]
    assert "not a win probability" in row["score_semantics"]


def test_replay_endpoint_uses_only_post_trigger_bars(monkeypatch):
    import api

    trigger = 1_780_000_000

    class Response:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {"t": trigger * 1000, "o": 1.0, "h": 9.0, "l": 0.1, "c": 1.0, "v": 1},
                    {"t": (trigger + 300) * 1000, "o": 1.0, "h": 1.09, "l": 0.99, "c": 1.07, "v": 1000},
                    {"t": (trigger + 600) * 1000, "o": 1.07, "h": 1.15, "l": 1.06, "c": 1.14, "v": 1000},
                ]
            }

    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    payload = api.replay_penny_stock_signal("TEST", trigger, 1.0, 0.95, 1.08, 1.14)
    assert payload["bars_replayed"] == 2
    assert payload["result"]["outcome"] == "BREAKEVEN_STOP"


def test_replay_reaches_tp2_when_post_trigger_path_is_unambiguous():
    result = evaluate_penny_signal_outcome(
        1.0,
        0.95,
        1.08,
        1.14,
        [
            {"open": 1.0, "high": 1.07, "low": 0.99, "close": 1.06, "volume": 1000},
            {"open": 1.06, "high": 1.15, "low": 1.02, "close": 1.14, "volume": 1000},
        ],
    )
    assert result["outcome"] == "TP2"


def test_trigger_pool_requires_fresh_nearby_structure():
    import api

    intraday = {
        "data_ok": True,
        "fresh": True,
        "warnings": [],
        "price": 1.02,
        "vwap": 1.00,
        "ema9": 1.01,
        "ema20": 1.00,
        "distance_to_breakout_pct": 1.2,
        "volume_ratio": 0.90,
        "close_position": 0.72,
    }
    eligible, reason = api._penny_trigger_pool_eligibility(
        _snapshot(),
        {"eligible": True},
        intraday,
    )
    assert eligible is True
    assert reason == "technical_trigger_neighborhood"

    distributed = dict(intraday, warnings=["large_upper_wick"])
    eligible, reason = api._penny_trigger_pool_eligibility(
        _snapshot(),
        {"eligible": True},
        distributed,
    )
    assert eligible is False
    assert reason == "distribution_or_extension_warning"


def test_trigger_pool_expires_fail_closed():
    import api

    api._penny_save_trigger_pool({
        "pump": {
            "snapshot": _snapshot(),
            "broad": {"eligible": True},
        },
    }, now_ts=100.0)

    assert set(api._penny_load_trigger_pool(now_ts=101.0)) == {"PUMP"}
    assert api._penny_load_trigger_pool(
        now_ts=100.0 + api._PENNY_TRIGGER_POOL_TTL_SECONDS + 1.0,
    ) == {}


@pytest.mark.parametrize("mail_sent", [False, True])
def test_trigger_pool_buy_activation_tracks_model_entry(monkeypatch, tmp_path, mail_sent):
    import api

    now_ts = _market_now()

    class Response:
        status_code = 200

        def json(self):
            return {
                "tickers": [{
                    "ticker": "PUMP",
                    "day": {"c": 1.045, "o": 0.99, "h": 1.05, "l": 0.95, "v": 9_000_000},
                    "prevDay": {"c": 0.995, "v": 2_812_500},
                    "lastTrade": {"p": 1.045},
                    "lastQuote": {"p": 1.044, "P": 1.046, "t": int(now_ts * 1_000_000_000)},
                }],
            }

    paths = {
        "PENNY_STOCKS_MONITOR_CACHE": tmp_path / "monitor.json",
        "PENNY_STOCKS_STATE": tmp_path / "state.json",
        "PENNY_STOCKS_REFERENCE_CACHE": tmp_path / "references.json",
        "PENNY_STOCKS_DAILY_CACHE": tmp_path / "daily.json",
        "PENNY_STOCKS_NEWS_CACHE": tmp_path / "news.json",
        "PENNY_STOCKS_SEC_CACHE": tmp_path / "sec.json",
        "_EMAIL_DEDUPE_FILE": tmp_path / "email_dedupe.json",
    }
    for name, path in paths.items():
        monkeypatch.setattr(api, name, str(path))

    api._penny_save_trigger_pool({
        "PUMP": {
            "snapshot": _snapshot(),
            "broad": {"eligible": True},
            "intraday": {"data_ok": True, "fresh": True},
        },
    }, now_ts=now_ts)

    buy_row = {
        "ticker": "PUMP",
        "price": 1.045,
        "entry": 1.045,
        "trade_action": "JETZT_KAUFEN",
        "trade_signal": "JETZT_KAUFEN",
        "trade_score": 91,
        "setup_quality_score": 86,
        "entry_quality_score": 92,
        "dump_risk_score": 18,
        "trigger_timestamp": now_ts - 300,
        "decision_timestamp": now_ts,
        "trade_setup": {
            "entry": 1.045,
            "stop_loss": 0.99,
            "tp1": 1.13,
            "tp2": 1.22,
        },
    }
    sent_rows = []
    monkeypatch.setattr(api.time, "time", lambda: now_ts)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_penny_expected_volume_fraction", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PUMP"}, "test"))
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _compressed_breakout_bars(now_ts))
    monkeypatch.setattr(api, "_penny_trigger_pool_eligibility", lambda *args, **kwargs: (True, "ok"))
    monkeypatch.setattr(
        api,
        "_penny_evaluate_trigger_pool_symbol",
        lambda *args, **kwargs: (dict(buy_row), _snapshot()),
    )
    monkeypatch.setattr(api, "_penny_revalidate_buy_candidate", lambda row, **kwargs: (dict(row), "ok"))
    monkeypatch.setattr(api, "_penny_buy_email", lambda rows: sent_rows.extend(rows) or mail_sent)
    monkeypatch.setattr(api, "_penny_management_email", lambda rows: False)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *args, **kwargs: None)

    api._penny_position_monitor_wrapper()

    state = api._penny_load_state_tickers()["PUMP"]
    assert len(sent_rows) == 1
    assert state["active"] is True
    assert state["buy_email_sent"] is mail_sent
    assert state["entry_notification_sent"] is mail_sent
    assert state["last_action"] == "JETZT_KAUFEN"
    if not mail_sent:
        assert state["entry_notification_error"] == "buy_mail_failed"
