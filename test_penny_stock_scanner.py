import time
from datetime import datetime, timezone
from pathlib import Path

from modules.penny_stock_scanner import (
    analyze_penny_intraday,
    build_penny_trade_plan,
    evaluate_penny_candidate,
    evaluate_penny_signal_outcome,
    score_broad_penny_candidate,
)


ROOT = Path(__file__).resolve().parent


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
            "volume": 1_000,
            "timestamp": start + idx * 300,
        })
    bars.append({
        "open": 1.005,
        "high": 1.018,
        "low": 1.000,
        "close": 1.012,
        "volume": 1_150,
        "timestamp": start + 14 * 300,
    })
    bars.append({
        "open": 1.015,
        "high": 1.10 if upper_wick else 1.05,
        "low": 1.012,
        "close": 1.025 if upper_wick else 1.045,
        "volume": 2_700,
        "timestamp": now_ts - 300,
    })
    if stale:
        for bar in bars:
            bar["timestamp"] -= 3_600
    return bars


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
        "bid": 1.044,
        "ask": 1.046,
    }


def _details():
    return {
        "name": "Pump Test Inc",
        "shares_millions": 18,
        "market_cap_millions": 45,
        "news_context": {"status": "ok", "risk_flags": [], "positive_catalysts": []},
        "sec_filing_context": {"status": "ok", "risk_flags": []},
    }


def _targets():
    return [
        {"price": 1.090, "source": "4H/5m VRVP resistance", "weight": 1.8},
        {"price": 1.145, "source": "Daily swing high", "weight": 2.0},
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
    now_ts = time.time()
    row = evaluate_penny_candidate(
        _snapshot(),
        _bars(now_ts),
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
    now_ts = time.time()
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
    now_ts = time.time()
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
    now_ts = time.time()
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
    now_ts = time.time()
    bars = _bars(now_ts)
    for bar in bars[2:12]:
        bar["volume"] = 0

    intraday = analyze_penny_intraday(bars, now_ts=now_ts)

    assert intraday["data_ok"] is False
    assert intraday["trigger_confirmed"] is False
    assert intraday["warnings"] == ["insufficient_5m_volume_baseline"]


def test_recent_offering_or_reverse_split_news_blocks_buy_mail_state():
    now_ts = time.time()
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
    now_ts = time.time()
    intraday = analyze_penny_intraday(_bars(now_ts), now_ts=now_ts)
    plan = build_penny_trade_plan(
        intraday,
        [],
        extra_resistances=[{"price": 1.085, "source": "single resistance", "weight": 2.0}],
    )
    assert plan["valid"] is False
    assert "no_distinct_structural_tp2_at_acceptable_reward" in plan["blockers"]


def test_live_trade_plan_uses_cost_adjusted_not_gross_reward():
    now_ts = time.time()
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
    assert expensive["valid"] is False
    assert "net_effective_rr_below_cost_adjusted_minimum" in expensive["blockers"]


def test_active_position_gets_exit_when_vwap_breaks_on_heavy_red_bar():
    now_ts = time.time()
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


def test_active_position_does_not_emit_duplicate_buy_signal():
    now_ts = time.time()
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


def test_penny_scanner_is_wired_to_scheduler_api_mail_and_pro_ui():
    api_source = (ROOT / "api.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend" / "index.html").read_text(encoding="utf-8")
    auth_source = (ROOT / "modules" / "auth.py").read_text(encoding="utf-8")

    assert '"penny_stocks": {"running": False' in api_source
    assert '("penny_stocks", _penny_stock_scanner_wrapper)' in api_source
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
    assert '"penny-stocks"' in auth_source


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
            "pump_potential_score": 70,
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

    assert [row["ticker"] for row in payload["data"]] == ["BUY", "HOLD", "EXIT"]
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
    assert [row["ticker"] for row in payload_with_watch["data"]] == ["BUY", "HOLD", "EXIT", "BUILD", "WAIT", "NEAR"]
    assert payload_with_watch["include_watch"] is True


def test_penny_near_entry_rows_reject_hard_liquidity_blocker():
    import api

    now_ts = time.time()
    row = {
        "ticker": "THIN",
        "trade_action": "TRIGGER_WARTEN",
        "trigger_timestamp": now_ts - 360,
        "pump_potential_score": 80,
        "entry_quality_score": 80,
        "dump_risk_score": 20,
        "hard_blockers": ["current_dollar_volume_below_500k"],
        "trade_setup": {"entry": 1.0, "stop_loss": 0.90, "tp1": 1.20, "tp2": 1.40},
    }

    assert api._penny_near_entry_rows([row], cache_age_seconds=60, now_ts=now_ts) == []


def test_penny_results_expire_old_trigger_and_stale_cache(monkeypatch):
    import api

    now_ts = time.time()
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


def test_api_wrapper_builds_buy_signal_and_persists_lifecycle(monkeypatch, tmp_path):
    import api

    now_ts = time.time()
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
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PUMP"}, "test"))
    monkeypatch.setattr(api, "_us_equity_expected_volume_fraction", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "get_ticker_details", lambda *args, **kwargs: _details())
    monkeypatch.setattr(api, "get_ticker_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_fetch_sec_filing_context", lambda *args, **kwargs: {"status": "ok", "risk_flags": []})
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _bars(now_ts))
    monkeypatch.setattr(api, "_penny_fetch_live_spread", lambda *args, **kwargs: {
        "bid": 1.044,
        "ask": 1.046,
        "spread_bps": 19.1,
        "spread_known": True,
        "quote_age_seconds": 1.0,
    })
    monkeypatch.setattr(api, "_penny_fetch_daily_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_vrvp_resistances", lambda *args, **kwargs: _targets())
    # Simulate SMTP failure: the scanner-model lifecycle must still persist.
    monkeypatch.setattr(api, "_penny_buy_email", lambda rows: sent.extend(rows) or False)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *args, **kwargs: None)

    api._penny_stock_scanner_wrapper()

    rows, _ = api.load_cache_file(str(cache))
    assert rows[0]["ticker"] == "PUMP"
    assert rows[0]["trade_action"] == "JETZT_KAUFEN"
    assert len(sent) == 1
    state_payload = api._penny_load_dict(str(state))
    assert state_payload["tickers"]["PUMP"]["active"] is True
    assert state_payload["tickers"]["PUMP"].get("buy_email_sent") is not True
    assert not api._email_dedupe_active(sent[0]["_dedupe_key"], 6 * 3600, now=now_ts + 1)


def test_spread_and_atr_define_real_breakout_clearance():
    now_ts = time.time()
    bars = _bars(now_ts)
    bars[-1].update({"open": 1.018, "high": 1.025, "low": 1.016, "close": 1.024, "volume": 2_700})

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


def test_nearest_overhead_barrier_cannot_be_skipped_for_farther_target():
    now_ts = time.time()
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
    now_ts = time.time()
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
    now_ts = time.time()
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
    assert enriched["rvol_source"] == "projected_volume_vs_20d_median"


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
    assert outcome["net_r"] < -1.0
    assert "stop-first" in outcome["assumption"]


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


def test_recent_sec_registration_form_is_a_hard_risk_source(monkeypatch):
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
    assert "recent_sec_s_3" in context["risk_flags"]


def test_primary_grade_uses_trade_score_not_pump_score():
    now_ts = time.time()
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
    assert payload["result"]["outcome"] == "TP2"
