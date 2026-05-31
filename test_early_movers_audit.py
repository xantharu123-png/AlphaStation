import json
import time
from datetime import datetime

import api


class _TrendingResponse:
    status_code = 200

    def json(self):
        return {"coins": []}


def _btc(change_24h=1.0, change_7d=2.0):
    return {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 60_000,
        "market_cap": 1_000_000_000_000,
        "total_volume": 10_000_000_000,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": change_7d,
    }


def _volume_coin(symbol="tvol", coin_id="test-volume", change_24h=4.0):
    return {
        "id": coin_id,
        "symbol": symbol,
        "name": "Test Volume",
        "current_price": 1.0,
        "market_cap_rank": 321,
        "market_cap": 20_000_000,
        "total_volume": 12_000_000,
        "price_change_percentage_1h_in_currency": 1.5,
        "price_change_percentage_24h": change_24h,
        "price_change_percentage_7d_in_currency": 12.0,
        "price_change_percentage_14d_in_currency": 8.0,
        "price_change_percentage_30d_in_currency": 10.0,
        "high_24h": 1.05,
        "low_24h": 0.90,
    }


def _perp(symbol="TVOL", volume=10_000_000):
    return {
        symbol.upper(): {
            "funding_rate": 0.0001,
            "oi_ratio": 1.2,
            "oi_usdt": 5_000_000,
            "volume24_usdt": volume,
            "best_exchange": "Bitget",
            "best_contract_symbol": f"{symbol.upper()}USDT",
            "best_chart_exchange": "bitget",
            "exchanges": ["Bitget"],
            "oi_change_pct": 5.0,
        }
    }


def test_early_mover_volume_spike_builds_conditional_long_setup(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), _volume_coin()])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps=_perp())
    row = next(c for c in result["coins"] if c["Symbol"] == "TVOL")

    assert row["direction"] == "LONG"
    assert row["trade_action"] == "LONG_TRIGGER"
    assert row["entry"] > row["stop_loss"]
    assert row["tp1"] > row["entry"]
    assert row["tp2"] > row["tp1"]
    assert row["risk_reward"] >= 1.5
    assert row["btc_context"]["tailwind"] is True


def test_early_mover_levels_are_structure_first_not_r_only():
    setup = api._build_early_mover_long_setup(
        {
            "Price": 1.0,
            "High24h": 1.05,
            "Low24h": 0.90,
            "MCap": 25_000_000,
            "VolMCapRatio": 12,
            "Change24h": 4.0,
            "Change7d": 12.0,
        },
        phase=1,
        score=86,
        btc_24h=1.0,
        btc_7d=2.0,
    )

    assert setup["level_model"] == "crypto_structure_first_v2"
    assert "invalidation" in setup["stop_source"]
    assert setup["tp1_source"] != "measured_move_fallback"
    assert setup["entry"] > setup["stop_loss"]
    assert setup["tp1"] > setup["entry"]
    assert setup["rr_tp1"] >= 1.35


def test_early_mover_extreme_turnover_without_alpha_is_wait_only():
    setup = api._build_early_mover_long_setup(
        {
            "Price": 0.00413199,
            "High24h": 0.00418,
            "Low24h": 0.00392,
            "MCap": 150_000_000,
            "VolMCapRatio": 96.0,
            "Change24h": 0.7,
            "Change7d": 6.0,
            "HasPerp": True,
            "PerpVolume24h": 20_000_000,
        },
        phase=1,
        score=81,
        btc_24h=1.0,
        btc_7d=2.0,
    )

    assert setup["trade_action"] == "WAIT_FOR_RETEST"
    assert setup["entry_quality"] == "CHURN"
    assert "turnover_without_alpha" in setup["risk_flags"]
    assert "extreme_turnover_churn" in setup["risk_flags"]
    assert setup["risk"] >= setup["entry"] * 0.024


def test_early_mover_filters_stables_wrapped_and_liquid_staking(monkeypatch):
    usde = _volume_coin(symbol="usde", coin_id="ethena-usde")
    usde["name"] = "Ethena USDe Stablecoin"
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), usde])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps={})

    assert all(c["Symbol"] != "USDE" for c in result["coins"])
    assert result["stats"]["excluded_assets"] >= 1


def test_early_mover_filters_tokenized_gold_assets(monkeypatch):
    paxg = _volume_coin(symbol="paxg", coin_id="pax-gold")
    paxg["name"] = "PAX Gold"
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), paxg])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps={})

    assert all(c["Symbol"] != "PAXG" for c in result["coins"])
    assert result["stats"]["excluded_assets"] >= 1


def test_early_mover_btc_headwind_blocks_active_long_trigger(monkeypatch):
    weak_btc = _btc(change_24h=-4.0, change_7d=-8.0)
    coin = _volume_coin(change_24h=3.5)
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [weak_btc, coin])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps=_perp())
    row = next(c for c in result["coins"] if c["Symbol"] == "TVOL")

    assert row["trade_action"] == "WAIT_FOR_BTC_CONFIRMATION"
    assert "btc_headwind" in row["risk_flags"]
    assert row["btc_context"]["tailwind"] is False


def test_early_mover_wait_states_keep_specific_timing_label():
    row = {"trade_action": "LONG_TRIGGER"}
    api._apply_early_mover_signal_state(row, {"ok": False, "reason": "no_fresh_5m_trigger"})

    assert row["trade_signal"] == "WARTEN"
    assert row["entry_status"] == "WAIT_FOR_TRIGGER"
    assert "Trigger" in row["signal_label"]
    assert row["entry_score"] < 60
    assert row["entry_score_label"] == "5M WARTEN"

    retest = {"trade_action": "WAIT_FOR_RETEST"}
    api._apply_early_mover_signal_state(retest, {"ok": False, "reason": "no_fresh_5m_trigger"})

    assert retest["trade_signal"] == "WARTEN"
    assert retest["entry_status"] == "WAIT_FOR_RETEST"
    assert "Retest" in retest["signal_label"]
    assert retest["entry_score_label"] == "RETEST WARTEN"


def test_early_mover_targets_are_structural_fib_levels_not_arbitrary_floors():
    setup = api._build_early_mover_long_setup(
        {
            "Price": 6.14,
            "High24h": 6.33,
            "Low24h": 5.95,
            "MCap": 150_000_000,
            "VolMCapRatio": 16,
            "Change24h": 1.0,
            "Change7d": 3.9,
        },
        phase=1,
        score=84,
        btc_24h=0.2,
        btc_7d=0.5,
    )

    assert setup["tp1"] >= round(setup["entry"] * 1.055, 4)
    assert setup["tp2"] >= round(setup["entry"] * 1.095, 4)
    assert setup["target_quality"] == "STRUCTURAL"
    assert "extension" in setup["tp1_source"] or "measured_move" in setup["tp1_source"]
    assert "minimum_momentum_target_floor" not in (setup["tp1_source"], setup["tp2_source"])


def test_early_mover_vrvp_can_upgrade_weak_targets():
    row = {
        "Symbol": "VRVP",
        "Price": 10.0,
        "trade_action": "LONG_TRIGGER",
        "entry": 10.0,
        "stop_loss": 9.50,
        "tp1": 10.2,
        "tp2": 10.35,
        "tp1_source": "24h_high_liquidity_too_close",
        "tp2_source": "breakout_measured_move_127_too_close",
        "target_quality": "WEAK_STRUCTURAL_TARGETS",
        "risk_flags": ["weak_structural_targets"],
        "trade_setup": {
            "target_quality": "WEAK_STRUCTURAL_TARGETS",
            "target_min_pct_required": {"tp1": 5.5, "tp2": 9.5},
        },
    }
    vrvp = {
        "poc": 10.9,
        "vah": 11.4,
        "val": 9.8,
        "levels": [
            {"price": 10.8, "source": "vrvp_poc_acceptance", "weight": 85},
            {"price": 11.4, "source": "vrvp_vah_resistance", "weight": 90},
        ],
    }

    api._apply_early_mover_signal_state(row, {"ok": False, "reason": "no_fresh_5m_trigger", "vrvp": vrvp})

    assert row["tp1"] == 10.8
    assert row["tp2"] == 11.4
    assert row["tp1_source"] == "vrvp_poc_acceptance"
    assert row["target_quality"] == "STRUCTURAL_VRVP"
    assert "weak_structural_targets" not in row["risk_flags"]
    assert "vrvp_target_confirmed" in row["risk_flags"]


def test_early_mover_duplicate_targets_are_downgraded_and_not_alertable():
    row = {
        "Symbol": "ZEN",
        "grade": "S",
        "score": 86,
        "setup_score": 86,
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "trade_signal": "WARTEN",
        "signal_quality": "wait_trigger",
        "risk_level": "LOW",
        "risk_flags": [],
        "entry": 5.84,
        "stop_loss": 5.62,
        "tp1": 6.21,
        "tp2": 6.21,
        "target_quality": "STRUCTURAL",
        "risk_reward": 2.0,
        "live_rr_ratio": 2.0,
        "distance_to_entry_r": 0,
        "btc_context": {"tailwind": True},
        "trade_setup": {
            "entry": 5.84,
            "stop_loss": 5.62,
            "tp1": 6.21,
            "tp2": 6.21,
            "target_quality": "STRUCTURAL",
            "risk_flags": [],
        },
    }

    api._apply_early_mover_signal_state(row, {
        "ok": True,
        "reason": "adaptive_5m_retest_hold",
        "timeframe": "5m",
        "execution_score": 92,
    })

    assert row["trade_signal"] == "WARTEN"
    assert row["trade_action"] == "WAIT_FOR_RETEST"
    assert row["alertable_crypto"] is False
    assert "duplicate_targets" in row["risk_flags"]
    assert "weak_structural_targets" in row["risk_flags"]
    assert api._scanner_row_is_trade_signal(row, "early_movers") is False

    state = api._classify_alert_candidate("early_movers", row, 1_000_000.0)
    assert "early_mover_weak_targets" in state["suppression_reasons"]


def test_early_mover_build_never_returns_identical_tp1_tp2():
    setup = api._build_early_mover_long_setup(
        {
            "Price": 5.84,
            "High24h": 6.21,
            "Low24h": 5.62,
            "MCap": 150_000_000,
            "VolMCapRatio": 9,
            "Change24h": 1.5,
            "Change7d": -4.3,
        },
        phase=1,
        score=86,
        btc_24h=0.5,
        btc_7d=0.2,
    )

    assert setup["tp2"] > setup["tp1"]
    assert setup["tp2"] - setup["tp1"] >= max(setup["entry"] * 0.018, setup["risk"] * 0.45)


def test_early_mover_perp_positioning_marks_snapshot_only(monkeypatch):
    coin = _volume_coin(symbol="whale", coin_id="whale-test", change_24h=2.0)
    coin["market_cap"] = 80_000_000
    coin["total_volume"] = 3_000_000
    coin["price_change_percentage_7d_in_currency"] = 2.0
    perps = {
        "WHALE": {
            "funding_rate": 0.0001,
            "oi_ratio": 2.0,
            "oi_usdt": 5_000_000,
            "volume24_usdt": 2_000_000,
            "best_exchange": "MEXC",
            "best_contract_symbol": "WHALE_USDT",
            "best_chart_exchange": "mexc",
            "exchanges": ["MEXC", "Bitget"],
            "oi_change_pct": None,
        }
    }
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), coin])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps=perps)
    row = next(c for c in result["coins"] if c["Symbol"] == "WHALE")

    assert "Perp Positioning" in row["sources"]
    assert row["oi_snapshot_only"] is True
    assert "oi_snapshot_only" in row["risk_flags"]
    assert row["Rank"] == 321
    assert row["PerpChartSymbol"] == "WHALE_USDT"
    assert row["PerpChartExchange"] == "mexc"


def test_early_mover_nested_coin_rows_receive_quality_payload(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), _volume_coin()])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())
    result = api.fetch_early_movers(_prefetched_perps=_perp())
    for coin in result["coins"]:
        if coin.get("Symbol") == "TVOL":
            coin["alertable_crypto"] = True
            coin["trade_signal"] = "JETZT_TRADEN"
            coin["execution_trigger_ok"] = True
            coin["entry_status"] = "TRIGGER_OK"
            coin["signal_quality"] = "tradeable"
            coin["grade"] = "S"
            coin["score"] = 90

    decorated = api._decorate_early_mover_results([result], cache_age_seconds=15)
    row = decorated[0]["coins"][0]

    assert "_quality" in row
    assert row["_quality"]["why_in"]
    assert row["trade_health"]["metrics"]["entry"] == row["entry"]


def test_early_mover_intraday_trigger_checks_more_than_top_30(monkeypatch):
    coins = [_btc()]
    for idx in range(45):
        coin = _volume_coin(symbol=f"em{idx}", coin_id=f"early-{idx}", change_24h=3.5 + (idx % 3) * 0.2)
        coin["market_cap_rank"] = 300 + idx
        coins.append(coin)

    checked = []
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: coins)
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())
    monkeypatch.setattr(
        api,
        "_verify_early_mover_intraday_trigger",
        lambda row: checked.append(row["Symbol"]) or {"ok": False, "reason": "test_no_trigger"},
    )

    result = api.fetch_early_movers(_prefetched_perps={f"EM{idx}": _perp(f"EM{idx}")[f"EM{idx}"] for idx in range(45)})

    assert len(checked) > 30
    assert result["stats"]["intraday_trigger_scan_limit"] == 1000
    assert result["stats"]["market_universe_target"] == 1000


def test_early_mover_market_sweep_checks_non_special_perp_coin(monkeypatch):
    sleepy = _volume_coin(symbol="sleep", coin_id="sleepy", change_24h=0.4)
    sleepy["market_cap"] = 500_000_000
    sleepy["total_volume"] = 2_000_000
    sleepy["price_change_percentage_7d_in_currency"] = 1.0
    sleepy["price_change_percentage_14d_in_currency"] = 0.5
    sleepy["price_change_percentage_30d_in_currency"] = 1.2

    checked = []
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), sleepy])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())
    monkeypatch.setattr(
        api,
        "_verify_early_mover_intraday_trigger",
        lambda row: checked.append(row["Symbol"]) or {"ok": False, "reason": "test_no_trigger"},
    )

    result = api.fetch_early_movers(_prefetched_perps=_perp("SLEEP"))

    assert "SLEEP" in checked
    assert result["stats"]["market_sweep_candidates"] == 1
    assert result["stats"]["intraday_trigger_checks"] == 1
    assert result["stats"]["intraday_trigger_scope"] == "all_chartable_top_1000"


def test_early_mover_thin_perp_liquidity_blocks_trade_signal(monkeypatch):
    morpho = _volume_coin(symbol="morpho", coin_id="morpho", change_24h=4.0)
    morpho["market_cap"] = 1_300_000_000
    morpho["total_volume"] = 45_000_000
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda pages=8: [_btc(), morpho])
    monkeypatch.setattr(api.req, "get", lambda *args, **kwargs: _TrendingResponse())

    result = api.fetch_early_movers(_prefetched_perps=_perp("MORPHO", volume=1_750_000))
    row = next(c for c in result["coins"] if c["Symbol"] == "MORPHO")

    assert row["trade_action"] == "WAIT_FOR_LIQUIDITY"
    assert row["risk_level"] == "HIGH"
    assert "thin_perp_liquidity" in row["risk_flags"]
    assert row["score"] < 80


def test_early_mover_orderbook_guard_rejects_market_impact(monkeypatch):
    api._EARLY_MOVER_TRIGGER_CACHE.clear()
    row = {
        "Symbol": "THIN",
        "PerpChartSymbol": "THINUSDT",
        "PerpChartExchange": "bitget",
        "tp1": 2.5,
    }
    bars = []
    for i in range(20):
        bars.append({"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000})
    bars.append({"open": 1.0, "high": 1.04, "low": 0.99, "close": 1.035, "volume": 2200})
    monkeypatch.setattr(api, "fetch_candles_for", lambda *args, **kwargs: bars)
    monkeypatch.setattr(api, "fetch_orderbook_for", lambda *args, **kwargs: {
        "bids": [(1.069, 100), (1.068, 200)],
        "asks": [(1.071, 100), (1.072, 200)],
    })

    result = api._verify_early_mover_intraday_trigger(row)

    assert result["ok"] is False
    assert result["reason"] == "thin_orderbook_market_impact"
    assert "thin_book_10bps" in result["liquidity_reasons"]


def test_early_mover_adaptive_trigger_uses_only_5m(monkeypatch):
    api._EARLY_MOVER_TRIGGER_CACHE.clear()
    row = {
        "Symbol": "FAST",
        "PerpChartSymbol": "FASTUSDT",
        "PerpChartExchange": "binance",
        "Change24h": 11.0,
        "VolMCapRatio": 42.0,
        "entry": 1.01,
        "stop_loss": 0.96,
        "tp1": 1.18,
    }
    flat_5m = [{"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000} for _ in range(36)]
    micro_1m = [{"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000} for _ in range(44)]
    micro_1m.append({"open": 1.01, "high": 1.055, "low": 1.008, "close": 1.049, "volume": 2600})
    calls = []

    def fake_fetch(symbol, exchange, timeframe="1h", count=50):
        calls.append(timeframe)
        return micro_1m if timeframe == "1m" else flat_5m

    monkeypatch.setattr(api, "fetch_candles_for", fake_fetch)
    monkeypatch.setattr(api, "fetch_orderbook_for", lambda *args, **kwargs: {
        "bids": [(1.048, 50_000), (1.047, 50_000)],
        "asks": [(1.050, 50_000), (1.051, 50_000)],
    })

    result = api._verify_early_mover_intraday_trigger(row)

    assert result["ok"] is False
    assert calls == ["5m"]
    assert result["adaptive_checks"][0]["timeframe"] == "5m"
    assert result["adaptive_checks"][0]["ok"] is False
    assert result["adaptive_checks"][0]["reason"] == "no_fresh_5m_trigger"


def test_early_mover_1m_retest_is_disabled():
    row = {
        "Symbol": "GALA",
        "Change24h": 4.0,
        "VolMCapRatio": 42.0,
        "entry": 1.0,
        "stop_loss": 0.96,
        "tp1": 1.12,
    }
    bars = [{"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000} for _ in range(18)]
    bars.append({"open": 0.998, "high": 1.001, "low": 0.997, "close": 0.9995, "volume": 2400})

    result = api._score_early_mover_trigger_bars(row, bars, "1m", api._early_mover_trigger_profile(row))

    assert result["ok"] is False
    assert result["reason"] == "execution_timeframe_disabled_use_5m"
    assert "retest_hold" not in result.get("matched", [])


def test_early_mover_5m_retest_hold_uses_adaptive_threshold():
    row = {
        "Symbol": "RETEST",
        "Change24h": 4.0,
        "BtcRelative24h": 2.2,
        "VolMCapRatio": 22.0,
        "entry": 1.0,
        "stop_loss": 0.94,
        "tp1": 1.16,
        "btc_context": {"tailwind": False, "btc_24h": 0.2, "btc_7d": -4.0, "alpha_24h": 2.2},
    }
    bars = [{"open": 1.002, "high": 1.014, "low": 0.992, "close": 1.001, "volume": 1000} for _ in range(35)]
    bars.append({"open": 0.997, "high": 1.012, "low": 0.998, "close": 1.007, "volume": 1200})

    result = api._score_early_mover_trigger_bars(row, bars, "5m", api._early_mover_trigger_profile(row))

    assert result["ok"] is True
    assert result["reason"] == "adaptive_5m_retest_hold"
    assert result["execution_threshold"] <= 64
    assert result["execution_score"] >= result["execution_threshold"]


def test_early_mover_5m_trigger_ignores_unfinished_live_candle():
    row = {
        "Symbol": "RETEST",
        "Change24h": 4.0,
        "BtcRelative24h": 2.2,
        "VolMCapRatio": 22.0,
        "entry": 1.0,
        "stop_loss": 0.94,
        "tp1": 1.16,
        "btc_context": {"tailwind": False, "btc_24h": 0.2, "btc_7d": -4.0, "alpha_24h": 2.2},
    }
    now = int(time.time())
    start = now - 37 * 300 - 20
    bars = [
        {"timestamp": start + i * 300, "open": 1.002, "high": 1.014, "low": 0.992, "close": 1.001, "volume": 1000}
        for i in range(35)
    ]
    bars.append({"timestamp": start + 35 * 300, "open": 0.997, "high": 1.012, "low": 0.998, "close": 1.007, "volume": 1200})
    bars.append({"timestamp": now - 60, "open": 1.035, "high": 1.04, "low": 0.90, "close": 0.91, "volume": 8000})

    result = api._score_early_mover_trigger_bars(row, bars, "5m", api._early_mover_trigger_profile(row))

    assert result["ok"] is True
    assert result["dropped_open_candle"] is True
    assert result["reason"] == "adaptive_5m_retest_hold"


def test_early_mover_turnover_churn_requires_5m_not_1m():
    row = {
        "Symbol": "GALA",
        "Change24h": 0.7,
        "VolMCapRatio": 96.0,
        "BtcRelative24h": -0.3,
        "entry": 1.0,
        "stop_loss": 0.96,
        "tp1": 1.12,
    }
    profile = api._early_mover_trigger_profile(row)

    result = api._score_early_mover_trigger_bars(row, [{"open": 1, "high": 1.01, "low": 0.99, "close": 1, "volume": 1000}] * 45, "1m", profile)

    assert profile["requires_5m_confirmation"] is True
    assert result["ok"] is False
    assert result["reason"] == "execution_timeframe_disabled_use_5m"


def test_early_mover_adaptive_blocks_chased_micro_candle(monkeypatch):
    api._EARLY_MOVER_TRIGGER_CACHE.clear()
    row = {
        "Symbol": "CHASE",
        "PerpChartSymbol": "CHASEUSDT",
        "PerpChartExchange": "binance",
        "Change24h": 14.0,
        "VolMCapRatio": 55.0,
        "entry": 1.00,
        "stop_loss": 0.96,
        "tp1": 1.30,
    }
    flat_5m = [{"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000} for _ in range(36)]
    micro_1m = [{"open": 1.0, "high": 1.01, "low": 0.99, "close": 1.0, "volume": 1000} for _ in range(44)]
    micro_1m.append({"open": 1.01, "high": 1.12, "low": 1.005, "close": 1.115, "volume": 4000})
    calls = []

    def fake_fetch(symbol, exchange, timeframe="1h", count=50):
        calls.append(timeframe)
        return micro_1m if timeframe == "1m" else flat_5m

    monkeypatch.setattr(api, "fetch_candles_for", fake_fetch)

    result = api._verify_early_mover_intraday_trigger(row)

    assert result["ok"] is False
    assert calls == ["5m"]
    assert result["reason"] == "no_fresh_5m_trigger"


def _pre_breakout_bars():
    bars = []
    # Wider prior range, then a tight 5m coil near the highs.
    for i in range(24):
        base = 1.04 + (0.018 if i % 2 else -0.018)
        bars.append({
            "open": base,
            "high": base + 0.035,
            "low": base - 0.035,
            "close": base + (0.006 if i % 3 == 0 else -0.003),
            "volume": 1200,
        })
    for i in range(11):
        base = 1.072 + i * 0.0007
        bars.append({
            "open": base,
            "high": base + 0.006,
            "low": base - 0.004,
            "close": base + 0.002,
            "volume": 760,
        })
    bars.append({"open": 1.081, "high": 1.087, "low": 1.079, "close": 1.084, "volume": 900})
    return bars


def _armed_row():
    return {
        "Symbol": "ARMED",
        "Name": "Armed Coin",
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "setup_score": 90,
        "score": 90,
        "grade": "S",
        "Price": 1.084,
        "Change24h": 5.0,
        "VolMCapRatio": 28.0,
        "PerpChartSymbol": "ARMEDUSDT",
        "PerpChartExchange": "binance",
        "entry": 1.065,
        "stop_loss": 1.00,
        "tp1": 1.18,
        "tp2": 1.26,
        "live_rr_ratio": 2.4,
        "distance_to_entry_r": 0.29,
        "target_quality": "STRUCTURAL",
        "risk_level": "LOW",
        "risk_flags": [],
        "btc_context": {"tailwind": True, "btc_24h": 1.0, "alpha_24h": 4.0},
        "trade_setup": {
            "trade_action": "LONG_TRIGGER",
            "entry": 1.065,
            "stop_loss": 1.00,
            "tp1": 1.18,
            "tp2": 1.26,
            "live_rr": 2.4,
            "distance_to_entry_r": 0.29,
            "target_quality": "STRUCTURAL",
        },
    }


def _with_good_htf_context(trigger):
    trigger = dict(trigger)
    trigger["htf_context"] = {
        "armed_ok": True,
        "reason": "htf_context_ok",
        "timeframe": "4h",
        "recent_change_pct": 1.2,
        "near_recent_high_pct": 0.4,
        "consecutive_green": 1,
    }
    return trigger


def _extended_4h_rebound_bars():
    bars = []
    price = 1.0
    for i in range(22):
        open_ = price
        close = price * (1 + (0.004 if i % 2 else -0.002))
        high = max(open_, close) * 1.006
        low = min(open_, close) * 0.994
        bars.append({"open": open_, "high": high, "low": low, "close": close, "volume": 1000})
        price = close
    for _ in range(10):
        open_ = price
        close = price * 1.012
        high = close * 1.004
        low = open_ * 0.997
        bars.append({"open": open_, "high": high, "low": low, "close": close, "volume": 1300})
        price = close
    bars.append({"open": price, "high": price * 1.003, "low": price * 0.985, "close": price * 0.99, "volume": 1200})
    return bars


def test_early_mover_detects_pre_breakout_coil_without_market_buy():
    row = _armed_row()
    result = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))

    assert result["ok"] is False
    assert result["reason"] == "no_fresh_5m_trigger"
    assert result["pre_breakout_ok"] is True
    assert result["pre_breakout_score"] >= api._EARLY_MOVER_MIN_ARMED_PREBREAKOUT_SCORE
    assert "compression" in result["pre_breakout_reasons"]
    assert result["near_range_high_pct"] <= 0.75


def test_early_mover_armed_is_not_shown_as_trade_signal():
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)

    assert row["trade_signal"] == "EXPLOSION_ARMED"
    assert row["pre_breakout_armed"] is True
    filtered = api._apply_signal_only_policy("early_movers", [{"coins": [row], "stats": {"unified_count": 1}}])

    assert filtered[0]["coins"] == []
    assert filtered[0]["stats"]["explosion_armed_count"] == 0
    assert filtered[0]["stats"]["trade_now_count"] == 0


def test_early_mover_armed_blocks_extended_4h_rebound():
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    htf_context = api._early_mover_htf_armed_context(row, _extended_4h_rebound_bars(), "4h")
    trigger["htf_context"] = htf_context
    if not htf_context["armed_ok"]:
        trigger["pre_breakout_ok"] = False
        trigger["pre_breakout_reason"] = htf_context["reason"]
    api._apply_early_mover_signal_state(row, trigger)

    assert htf_context["armed_ok"] is False
    assert "htf_move_already_extended" in htf_context["reasons"]
    assert row["trade_signal"] != "EXPLOSION_ARMED"
    assert "not_pre_breakout_coil" in row["pre_breakout_block_reasons"]


def test_early_mover_armed_requires_elite_setup_score():
    row = _armed_row()
    row["setup_score"] = 66
    row["score"] = 66
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)

    assert row["trade_signal"] != "EXPLOSION_ARMED"
    assert "setup_score_below_armed_threshold" in row["pre_breakout_block_reasons"]


def test_early_mover_armed_downgrades_when_entry_risk_is_high():
    row = _armed_row()
    row.update({
        "setup_score": 100,
        "score": 100,
        "risk_level": "MEDIUM",
        "risk_flags": ["high_volume_turnover", "perp_liquidity_watch", "oi_snapshot_only"],
        "distance_to_entry_r": 0.55,
    })
    row["trade_setup"]["distance_to_entry_r"] = 0.55
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    trigger["pre_breakout_score"] = 96
    api._apply_early_mover_signal_state(row, trigger)

    assert row["trade_signal"] == "WARTEN"
    assert row["pre_breakout_armed"] is False
    assert row["entry_score"] < api._EARLY_MOVER_VISIBLE_MIN_ENTRY_SCORE
    assert "entry_score_below_armed_threshold" in row["pre_breakout_block_reasons"]


def test_early_mover_armed_digest_disabled_by_default(monkeypatch, tmp_path):
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)
    row["intraday_trigger"] = trigger
    sent = []

    monkeypatch.setattr(api, "_EARLY_MOVER_SEND_ARMED_EMAILS", False)
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "fetch_orderbook_for", lambda *args, **kwargs: {
        "bids": [(1.0839, 500_000), (1.0835, 500_000), (1.0830, 500_000)],
        "asks": [(1.0841, 500_000), (1.0845, 500_000), (1.0850, 500_000)],
    })
    api._EMAIL_COOLDOWN.clear()

    assert api._send_early_mover_armed_alerts({"coins": [row]}) is False
    assert sent == []


def test_early_mover_armed_digest_stays_disabled_even_if_env_flag_enabled(monkeypatch, tmp_path):
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)
    row["intraday_trigger"] = trigger
    sent = []

    monkeypatch.setattr(api, "_EARLY_MOVER_SEND_ARMED_EMAILS", True)
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)

    assert api._send_early_mover_armed_alerts({"coins": [row]}) is False
    assert sent == []


def test_early_mover_armed_digest_blocks_thin_orderbook(monkeypatch, tmp_path):
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)
    row["intraday_trigger"] = trigger
    sent = []

    monkeypatch.setattr(api, "_EARLY_MOVER_SEND_ARMED_EMAILS", True)
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body: sent.append((subject, body)) or True)
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "fetch_orderbook_for", lambda *args, **kwargs: {
        "bids": [(1.0839, 10)],
        "asks": [(1.0841, 10)],
    })
    api._EMAIL_COOLDOWN.clear()

    assert api._send_early_mover_armed_alerts({"coins": [row]}) is False
    assert sent == []


def test_early_mover_alert_audit_reports_armed_watch_but_no_mail_by_default(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "_EARLY_MOVER_SEND_ARMED_EMAILS", False)
    row = _armed_row()
    trigger = api._score_early_mover_trigger_bars(row, _pre_breakout_bars(), "5m", api._early_mover_trigger_profile(row))
    trigger = _with_good_htf_context(trigger)
    api._apply_early_mover_signal_state(row, trigger)
    row["intraday_trigger"] = trigger

    cache_file = tmp_path / "early_movers.json"
    cache_file.write_text(json.dumps({
        "cached_at": datetime.now().isoformat(),
        "results": [{"coins": [row], "stats": {"unified_count": 1}}],
    }))

    audit = api._build_alert_audit_for_cache("early_movers", str(cache_file))
    summary = api._summarize_email_alert_audit({"early_movers": audit})

    assert audit["armed_watch_count"] == 1
    assert audit["armed_watch_preview"][0]["ticker"] == "ARMED"
    assert audit["armed_alertable_now_count"] == 0
    assert audit["armed_mail_status"] == "DISABLED"
    assert summary["total_armed_alertable_now"] == 0
    assert summary["overall_status"] in {"ALL_BLOCKED_BY_GATES", "STARTUP_COOLDOWN", "EMAIL_NOT_CONFIGURED"}


def test_multi_exchange_perps_prefers_binance_execution_liquidity(monkeypatch):
    monkeypatch.setattr(api, "fetch_mexc_funding_oi", lambda: {
        "FET": {
            "contract_symbol": "FET_USDT",
            "chart_exchange": "mexc",
            "funding_rate": 0.0002,
            "oi_usdt": 500_000,
            "volume24": 1_000_000,
            "oi_ratio": 0.5,
        }
    })
    monkeypatch.setattr(api, "fetch_bitget_funding_oi", lambda: {
        "FET": {
            "contract_symbol": "FETUSDT",
            "chart_exchange": "bitget",
            "funding_rate": 0.0001,
            "oi_usdt": 8_000_000,
            "volume24_usdt": 4_000_000,
            "oi_ratio": 2.0,
        }
    })
    monkeypatch.setattr(api, "fetch_binance_funding_oi", lambda: {
        "FET": {
            "contract_symbol": "FETUSDT",
            "chart_exchange": "binance",
            "funding_rate": -0.0003,
            "oi_usdt": 0,
            "volume24_usdt": 32_000_000,
            "oi_ratio": 0,
        }
    })
    monkeypatch.setattr(api, "_enrich_perp_oi_history", lambda data: data)

    result = api.fetch_multi_exchange_perps()
    fet = result["FET"]

    assert fet["best_exchange"] == "Binance"
    assert fet["best_chart_exchange"] == "binance"
    assert fet["best_contract_symbol"] == "FETUSDT"
    assert fet["volume24_usdt"] == 32_000_000
    assert fet["oi_usdt"] == 8_000_000
    assert "Binance" in fet["exchanges"]
