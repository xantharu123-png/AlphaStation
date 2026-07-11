import time
from pathlib import Path

from modules.penny_stock_scanner import (
    analyze_penny_intraday,
    build_penny_trade_plan,
    evaluate_penny_candidate,
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
        "timestamp": (now_ts - 3_600) if stale else (now_ts - 300),
    })
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
    }


def _details():
    return {
        "name": "Pump Test Inc",
        "shares_millions": 18,
        "market_cap_millions": 45,
    }


def _targets():
    return [
        {"price": 1.085, "source": "4H/5m VRVP resistance", "weight": 1.8},
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
    assert "recent_dilution_reverse_split_or_company_risk_news" in row["hard_blockers"]
    assert row["catalyst_context"]["risk_flags"] == ["[!!] OFFERING"]


def test_targets_must_be_distinct_verified_structure_levels():
    now_ts = time.time()
    intraday = analyze_penny_intraday(_bars(now_ts), now_ts=now_ts)
    plan = build_penny_trade_plan(
        intraday,
        [],
        extra_resistances=[{"price": 1.085, "source": "single resistance"}],
    )
    assert plan["valid"] is False
    assert "no_distinct_structural_tp2_at_acceptable_reward" in plan["blockers"]


def test_active_position_gets_exit_when_vwap_breaks_on_heavy_red_bar():
    now_ts = time.time()
    bars = _bars(now_ts)
    bars[-2].update({"open": 1.04, "high": 1.045, "low": 0.98, "close": 0.99, "volume": 3_000})
    bars[-1].update({"open": 0.99, "high": 0.995, "low": 0.93, "close": 0.94, "volume": 4_000})
    row = evaluate_penny_candidate(
        dict(_snapshot(), price=0.94, change_pct=-8.0),
        bars,
        [],
        details=_details(),
        extra_resistances=_targets(),
        previous_active=True,
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
        previous_active=True,
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
    assert "Pennystock KAUFEN" in api_source
    assert "Pennystock VERKAUFEN" in api_source
    assert "function PennyStocksTab" in frontend_source
    assert "activeTab === 'penny-stocks'" in frontend_source
    assert "Drei getrennte Bewertungen" not in frontend_source
    assert "Pennystock Signale" in frontend_source
    assert '"penny-stocks"' in auth_source


def test_penny_results_expose_only_active_trade_decisions(monkeypatch):
    import api

    now_ts = time.time()
    legacy_rows = [
        {"ticker": "BUY", "trade_action": "JETZT_KAUFEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "HOLD", "trade_action": "HALTEN"},
        {"ticker": "EXIT", "trade_action": "JETZT_VERKAUFEN", "trigger_timestamp": now_ts - 360},
        {"ticker": "BUILD", "trade_action": "BEOBACHTEN"},
        {"ticker": "WAIT", "trade_action": "TRIGGER_WARTEN"},
        {"ticker": "NO", "trade_action": "NICHT_KAUFEN"},
    ]
    monkeypatch.setattr(api, "load_cache_file", lambda path: (legacy_rows, None))
    monkeypatch.setattr(api, "load_cache_metadata", lambda path: {"diagnostics": {}})

    payload = api.get_penny_stock_results()

    assert [row["ticker"] for row in payload["data"]] == ["BUY", "HOLD", "EXIT"]


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
            "day": {"c": 1.045, "o": 0.99, "h": 1.05, "l": 0.95, "v": 4_000_000},
            "prevDay": {"c": 0.995, "v": 1_250_000},
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
    sent = []

    monkeypatch.setattr(api, "PENNY_STOCKS_CACHE", str(cache))
    monkeypatch.setattr(api, "PENNY_STOCKS_STATE", str(state))
    monkeypatch.setattr(api, "PENNY_STOCKS_REFERENCE_CACHE", str(references))
    monkeypatch.setattr(api, "PENNY_STOCKS_DAILY_CACHE", str(daily))
    monkeypatch.setattr(api, "PENNY_STOCKS_NEWS_CACHE", str(news))
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *args, **kwargs: ({"PUMP"}, "test"))
    monkeypatch.setattr(api, "_us_equity_expected_volume_fraction", lambda *args, **kwargs: 1.0)
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *args, **kwargs: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "get_ticker_details", lambda *args, **kwargs: _details())
    monkeypatch.setattr(api, "get_ticker_news", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *args, **kwargs: _bars(now_ts))
    monkeypatch.setattr(api, "_penny_fetch_daily_bars", lambda *args, **kwargs: [])
    monkeypatch.setattr(api, "_penny_vrvp_resistances", lambda *args, **kwargs: _targets())
    monkeypatch.setattr(api, "_penny_buy_email", lambda rows: sent.extend(rows) or True)
    monkeypatch.setattr(api, "_penny_exit_email", lambda rows: False)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *args, **kwargs: None)

    api._penny_stock_scanner_wrapper()

    rows, _ = api.load_cache_file(str(cache))
    assert rows[0]["ticker"] == "PUMP"
    assert rows[0]["trade_action"] == "JETZT_KAUFEN"
    assert len(sent) == 1
    state_payload = api._penny_load_dict(str(state))
    assert state_payload["tickers"]["PUMP"]["active"] is True
