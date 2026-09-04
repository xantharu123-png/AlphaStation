"""Funding/provenance regressions use mocked HTTP and synthetic candles only."""
from types import SimpleNamespace
import json

import pytest

import api
from test_crypto_explosion_scanner import _bars, _btc_context, _candidate


@pytest.fixture(autouse=True)
def no_unmocked_external_calls(monkeypatch):
    def blocked(*_a, **_kw):
        raise AssertionError("External requests forbidden in crypto contract regressions")
    monkeypatch.setattr(api.req, "get", blocked)


@pytest.mark.parametrize("raw", [None, "", "bad", "nan", "inf", True])
def test_missing_or_invalid_funding_is_not_measured_zero(raw):
    row = api._funding_measurement(raw, source="test")
    assert row["funding_available"] is False
    assert row["funding_rate"] is None
    assert row["funding_rate_pct"] is None
    assert row["funding_interval_hours"] is None
    assert row["funding_interval_known"] is False


def test_true_zero_funding_remains_a_valid_measurement():
    row = api._funding_measurement("0", source="test", interval_hours=4)
    assert row["funding_available"] is True
    assert row["funding_rate_pct"] == 0
    assert row["funding_interval_hours"] == 4


@pytest.mark.parametrize("interval", [True, False, -1, 0, "NaN", "Inf", None])
def test_non_measurement_funding_intervals_cannot_be_normalized(interval):
    row = api._funding_measurement(.001, source="test", interval_hours=interval)
    assert row["funding_interval_hours"] is None
    assert row["funding_interval_known"] is False
    assert row["funding_rate_pct_8h_equivalent"] is None


@pytest.mark.parametrize("premium", [None, [], [{"symbol": "TESTUSDT"}], [{"symbol": "TESTUSDT", "lastFundingRate": "NaN"}]])
def test_binance_unavailable_funding_never_produces_low_risk(monkeypatch, premium):
    def get(url, **kwargs):
        if url.endswith("/ticker/24hr"):
            return SimpleNamespace(status_code=200, json=lambda: [{"symbol": "TESTUSDT", "quoteVolume": "10000000", "lastPrice": "100", "priceChangePercent": "2"}])
        if url.endswith("/premiumIndex"):
            return SimpleNamespace(status_code=503 if premium is None else 200, json=lambda: premium)
        raise AssertionError("unexpected HTTP endpoint")
    monkeypatch.setattr(api.req, "get", get)
    monkeypatch.setattr(api.time, "sleep", lambda _: None)
    row = api.fetch_binance_funding_oi()["TEST"]
    assert row["funding_available"] is False
    risk, _, reasons = api._calculate_risk(2, 4, 20, row["funding_rate"], 1, 10_000_000, True)
    assert risk == "HIGH"
    assert any("Funding" in reason for reason in reasons)


def test_explosion_adapter_retains_unknown_funding():
    row = api._ce_normalized_row(exchange="binance", contract="TESTUSDT", price=10, turnover_usd=60_000_000)
    assert row["funding_available"] is False
    assert row["funding_rate"] is None


@pytest.mark.parametrize("four_hour_count", [0, 12, 60])
def test_explosion_profile_uses_its_actual_source_timeframe(monkeypatch, four_hour_count):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    calls = []
    original = api.build_vrvp_structure
    def build(bars, *args, **kwargs):
        calls.append((len(bars), kwargs["timeframe"]))
        return original(bars, *args, **kwargs)
    monkeypatch.setattr(api, "build_vrvp_structure", build)
    bars5 = _bars(90, start=9.48, step=.004, last={"open":9.93,"high":9.97,"low":9.90,"close":9.95,"volume":1200})
    bars15 = _bars(60, start=9.42, step=.009, interval=900, last={"open":9.92,"high":9.98,"low":9.88,"close":9.95,"volume":3600})
    bars4h = _bars(four_hour_count, start=9.4, step=.006, interval=14400)
    api._score_crypto_explosion_candidate(_candidate(), bars5, bars15, bars4h)
    assert calls
    assert calls[0][1] == ("4H" if four_hour_count >= 20 else "15M")


@pytest.fixture
def executable_explosion(monkeypatch):
    """Keep market/plan facts fixed to isolate the producer's final guards."""
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    setup_flags = {"structure_status": "ACCEPT", "barrier_gate_active": False}
    def confirmed_setup(setup, *_a, **_kw):
        return {**setup, "entry": 10.12, "stop": 9.9, "tp1": 11.0, "tp2": 11.8,
                "tp1_is_projection": False, "tp2_is_projection": True,
                "target_quality": "STRUCTURAL_TP1_PROJECTION_TP2", **setup_flags}
    monkeypatch.setattr(api, "apply_vrvp_to_trade_setup", confirmed_setup)
    bars5 = _bars(90, start=9.5, step=.004, volume=1000, last={"open":10., "high":10.14, "low":9.98, "close":10.12, "volume":3200})
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900, last={"open":9.95, "high":10.02, "low":9.91, "close":9.98, "volume":3000})
    bars4h = _bars(60, start=9.4, step=.006, volume=5000, interval=14400)
    def score(*, price_scale=1.0, **overrides):
        candidate = _candidate(price=10.12, change=8)
        for key in ("Price", "price", "high_24h", "low_24h"):
            candidate[key] *= price_scale
        def scaled(rows):
            return [{**bar, **{key: bar[key] * price_scale for key in ("open", "high", "low", "close")}} for bar in rows]
        return api._score_crypto_explosion_candidate({**candidate, **overrides}, scaled(bars5), scaled(bars15), scaled(bars4h))
    return score, setup_flags


@pytest.mark.parametrize("scale", [1e-9, 1e-13])
def test_explosion_final_response_preserves_micro_price_geometry(executable_explosion, scale):
    score, setup_flags = executable_explosion
    setup_flags.update({key: value * scale for key, value in {"entry": 10.12, "stop": 9.9, "tp1": 11.0, "tp2": 11.8}.items()})
    result = score(price_scale=scale)
    assert result and result["trade_signal"] == "JETZT_TRADEN"
    assert result["alertable_crypto"] is True
    assert 0 < result["stop"] < result["entry"] < result["tp1"] < result["tp2"]
    assert result["stop_loss"] == result["stop"]
    for key in ("entry", "stop", "tp1", "tp2"):
        assert result[key] == result["trade_setup"][key]
        assert result[key] == float(f"{setup_flags[key]:.6g}")
    geometry = api.trade_geometry(*(result[key] for key in ("entry", "stop", "tp1", "tp2")), "LONG")
    assert geometry["valid"] is True
    assert result["rr_tp1"] == round(geometry["rr_tp1"], 2) == 4.0
    assert result["risk_reward"] == round(geometry["rr"], 2)


def test_explosion_rejects_geometry_that_collapses_in_final_response(executable_explosion):
    score, setup_flags = executable_explosion
    setup_flags.update(entry=10.120004, stop=10.120003, tp1=10.12002, tp2=10.12004)
    raw = api.trade_geometry(*(setup_flags[key] for key in ("entry", "stop", "tp1", "tp2")), "LONG")
    assert raw["valid"] and raw["rr_tp1"] > 1.35
    assert score() is None


def test_explosion_final_prices_must_still_pass_minimum_target_rr(executable_explosion):
    score, setup_flags = executable_explosion
    setup_flags.update(entry=10.12051, stop=9.9, tp1=10.41825, tp2=11.8)
    raw = api.trade_geometry(*(setup_flags[key] for key in ("entry", "stop", "tp1", "tp2")), "LONG")
    assert raw["valid"] and raw["rr_tp1"] >= 1.35
    result = score()
    assert result and result["trade_signal"] != "JETZT_TRADEN"
    assert result["alertable_crypto"] is False
    final = api.trade_geometry(*(result[key] for key in ("entry", "stop", "tp1", "tp2")), "LONG")
    assert final["rr_tp1"] < 1.35
    assert result["rr_tp1"] == result["trade_setup"]["rr_tp1"] == round(final["rr_tp1"], 2)


def test_eight_hour_equivalent_funding_is_same_for_four_and_eight_hours(executable_explosion):
    score, _ = executable_explosion
    four = score(funding_rate=.04, funding_interval_hours=4)
    eight = score(funding_rate=.08, funding_interval_hours=8)
    assert four and eight
    assert four["score"] == eight["score"]
    assert four["entry_score"] == eight["entry_score"]
    assert four["risk_level"] == eight["risk_level"] == "HIGH"
    assert "funding crowded" in four["risk_reasons"]
    assert "funding crowded" in eight["risk_reasons"]


@pytest.mark.parametrize("override", [
    {"funding_rate": None}, {"funding_interval_hours": None},
    {"funding_available": False}, {"spread_pct": None},
    {"spread_pct": float("nan")}, {"spread_pct": -1},
])
def test_missing_execution_measurements_block_even_confirmed_breakout(executable_explosion, override):
    score, _ = executable_explosion
    value = score(**override)
    assert value and value["execution_trigger_ok"] is True
    assert value["trade_signal"] != "JETZT_TRADEN"
    assert value["alertable_crypto"] is False
    assert value["risk_level"] == "HIGH"


@pytest.mark.parametrize("flags", [
    {"structure_status": "REJECT", "structure_reason": "invalid_causal_metadata"},
    {"barrier_gate_active": True},
])
def test_explosion_cannot_upgrade_an_explicit_structure_block(executable_explosion, flags):
    score, setup_flags = executable_explosion
    assert score()["trade_signal"] == "JETZT_TRADEN", "positive control must reach the final execution gate"
    setup_flags.update(flags)
    value = score()
    assert value is None or value["trade_signal"] != "JETZT_TRADEN"
    if value:
        assert value["alertable_crypto"] is False


@pytest.mark.parametrize("minutes, hours", [(240, 4), (480, 8), (60, 1), (0, None), (None, None)])
def test_bybit_funding_interval_is_minutes_not_hours(monkeypatch, minutes, hours):
    monkeypatch.setattr(api, "_ce_http_json", lambda *_a, **_kw: {"retCode": 0, "result": {"list": [{"symbol": "TESTUSDT", "fundingInterval": minutes}]}})
    row = {"exchange": "bybit", "contract": "TESTUSDT", **api._funding_measurement(.02, source="bybit", unit="percent", observed_at=123)}
    value = api._refresh_crypto_funding(row)
    assert value["funding_interval_hours"] == hours
    assert value["funding_rate_pct"] == pytest.approx(.02)
    assert value["funding_rate_fraction"] == pytest.approx(.0002)
    assert value["funding_interval_known"] is (hours is not None)
    if hours:
        assert value["funding_rate_pct_8h_equivalent"] == pytest.approx(.02 * 8 / hours)
    else:
        assert value["funding_rate_pct_8h_equivalent"] is None


def test_bybit_mismatching_instrument_cannot_supply_interval(monkeypatch):
    monkeypatch.setattr(api, "_ce_http_json", lambda *_a, **_kw: {"result": {"list": [{"symbol": "OTHERUSDT", "fundingInterval": 480}]}})
    row = {"exchange": "bybit", "contract": "TESTUSDT", **api._funding_measurement(.0001, source="bybit")}
    assert api._refresh_crypto_funding(row)["funding_interval_hours"] is None


def test_refresh_venue_fraction_preserves_percent_consumer_units(monkeypatch):
    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "fetch_funding_measurement", lambda venue, contract: {"funding_rate": .0002, "funding_interval_hours": 4, "funding_source": "measured", "funding_source_timestamp": 123})
    row = api._refresh_crypto_funding({"exchange": "mexc", "contract": "TEST_USDT", "funding_rate_unit": "percent"})
    assert row["funding_rate"] == pytest.approx(.02)
    assert row["funding_rate_fraction"] == pytest.approx(.0002)
    assert row["funding_rate_pct_8h_equivalent"] == pytest.approx(.04)


def test_full_explosion_run_refreshes_funding_before_scoring(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_fetch_crypto_explosion_universe", lambda: ([_candidate()], {}))
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", lambda *_a, **_kw: [])
    monkeypatch.setattr(api, "_refresh_crypto_funding", lambda row: {**row, "funding_rate": None, "funding_available": False})
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", lambda row, *_args: seen.append(dict(row)) or None)
    rows, stats = api._run_crypto_explosion_scan()
    assert rows == [] and stats["chart_checked"] == 1
    assert len(seen) == 1 and seen[0]["funding_available"] is False
    assert seen[0]["funding_rate"] is None


@pytest.mark.parametrize("venue, contract, age, delta", [
    ("binance", "TESTUSDT", 600, 20),
    ("bitget", "TESTUSDT", 600, None),
    ("binance", "1000TESTUSDT", 600, None),
    ("binance", "TESTUSDT", 100, None),
    ("binance", "TESTUSDT", 8000, None),
    ("binance", "TESTUSDT", -600, None),
])
def test_oi_change_requires_same_venue_contract_and_measured_age(monkeypatch, tmp_path, venue, contract, age, delta):
    now = 1_800_000_000
    path = tmp_path / "oi_history.json"
    path.write_text(json.dumps({"TEST": {"oi_usdt": 100, "timestamp": now - age, "venue": venue, "contract": contract}}), encoding="utf-8")
    monkeypatch.setattr(api, "PERP_OI_HISTORY_CACHE", str(path))
    monkeypatch.setattr(api.time, "time", lambda: now)
    value = api._enrich_perp_oi_history({"TEST": {"oi_usdt": 120, "best_chart_exchange": "binance", "best_contract_symbol": "TESTUSDT"}})["TEST"]
    assert value["oi_change_pct"] == delta
    assert value["oi_history_age_seconds"] == (age if delta is not None else None)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), "bad"])
def test_invalid_current_oi_is_unknown_not_nonfinite_delta(monkeypatch, tmp_path, value):
    now = 1_800_000_000
    path = tmp_path / "oi_history.json"
    path.write_text(json.dumps({"TEST": {"oi_usdt": 100, "timestamp": now - 600, "venue": "binance", "contract": "TESTUSDT"}}), encoding="utf-8")
    monkeypatch.setattr(api, "PERP_OI_HISTORY_CACHE", str(path))
    monkeypatch.setattr(api.time, "time", lambda: now)
    result = api._enrich_perp_oi_history({"TEST": {"oi_usdt": value, "best_chart_exchange": "binance", "best_contract_symbol": "TESTUSDT"}})
    assert result["TEST"]["oi_change_pct"] is None
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["TEST"]["oi_usdt"] is None


def _div_coin(cid, symbol, change24=None, change7=None):
    return {"id": cid, "symbol": symbol, "name": cid, "current_price": 10,
            "market_cap": 100_000_000, "total_volume": 10_000_000,
            "price_change_percentage_24h": change24, "price_change_percentage_7d_in_currency": change7}


@pytest.mark.parametrize("missing", ["price_change_percentage_24h", "price_change_percentage_7d_in_currency"])
def test_missing_btc_return_never_becomes_stagnant_regime(monkeypatch, missing):
    btc = _div_coin("bitcoin", "btc", 0, 0)
    btc[missing] = None
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [btc, _div_coin("test", "test", 15, 30)])
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    assert api._build_crypto_btc_divergence_results() == []


def test_true_flat_btc_stays_measured_and_absent_14d_stays_unknown(monkeypatch):
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: [_div_coin("bitcoin", "btc", 0, 0), _div_coin("test", "test", 15, 30)])
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    rows = api._build_crypto_btc_divergence_results()
    assert len(rows) == 1
    assert rows[0]["btc_regime"] == "STAGNANT"
    assert rows[0]["alpha_14d"] is None
    assert rows[0]["context_only"] is True
    assert rows[0]["execution_trigger_ok"] is False


def test_btc_symbol_collision_does_not_override_bitcoin_id(monkeypatch):
    coins = [_div_coin("not-bitcoin", "btc", -10, -20), _div_coin("bitcoin", "btc", 5, 15), _div_coin("test", "test", 15, 30)]
    monkeypatch.setattr(api, "_fetch_coingecko_markets", lambda **_kw: coins)
    monkeypatch.setattr(api, "fetch_multi_exchange_perps", lambda: {})
    rows = api._build_crypto_btc_divergence_results()
    assert rows and rows[0]["btc_24h"] == 5
    assert rows[0]["btc_regime"] == "RISK_ON"


@pytest.mark.parametrize("bid, ask", [(None, 10), (10, None), (True, 10), (10, False), ("NaN", 10), (10, "Inf"), (0, 10), (10, 0), (-1, 10), (11, 10)])
def test_ce_invalid_top_of_book_is_unknown_not_zero_spread(bid, ask):
    result = api._ce_spread_measurement(bid, ask, source="test")
    assert result["spread_pct"] is None
    assert result["spread_available"] is False


def test_ce_locked_book_is_measured_zero_not_missing():
    result = api._ce_spread_measurement("10", "10", source="test", source_timestamp=123)
    assert result["spread_pct"] == 0
    assert result["spread_available"] is True
    assert result["spread_source_timestamp"] == 123
    assert result["spread_source"] == "test"


@pytest.mark.parametrize("bad", [True, False, "NaN", "Inf", "bad"])
def test_ce_adapter_cannot_turn_invalid_spread_into_locked_book(bad):
    result = api._ce_normalized_row(exchange="mexc", contract="TEST_USDT", price=10, turnover_usd=60000000, spread_pct=bad)
    assert result["spread_pct"] is None


@pytest.mark.parametrize("venue", ["mexc", "bitget", "bybit"])
def test_ce_native_ticker_wires_its_actual_bid_ask(monkeypatch, venue):
    if venue == "mexc":
        payload = {"success": True, "data": [{"symbol": "TEST_USDT", "lastPrice": 10, "amount24": 60000000, "bid1": 9.99, "ask1": 10.01, "timestamp": 123}]}
    elif venue == "bitget":
        payload = {"code": "00000", "data": [{"symbol": "TESTUSDT", "lastPr": "10", "usdtVolume": "60000000", "bidPr": "9.99", "askPr": "10.01", "ts": 123}]}
    else:
        payload = {"time": 123, "result": {"list": [{"symbol": "TESTUSDT", "lastPrice": "10", "turnover24h": "60000000", "bid1Price": "9.99", "ask1Price": "10.01"}]}}
    monkeypatch.setattr(api, "_ce_http_json", lambda *_a, **_kw: payload)
    rows = getattr(api, f"_fetch_{venue}_perp_rows")()
    assert len(rows) == 1
    assert rows[0]["spread_pct"] == pytest.approx(.2)
    assert rows[0]["spread_source_timestamp"] == 123
    assert rows[0]["spread_available"] is True


@pytest.mark.parametrize("spread, allowed", [(0, True), (.02, True), (.2, True), (.20001, False), (10, False)])
def test_ce_uses_existing_twenty_bps_crypto_long_execution_limit(executable_explosion, spread, allowed):
    score, _ = executable_explosion
    result = score(spread_pct=spread)
    assert result
    assert result["spread_execution_ok"] is allowed
    assert (result["trade_signal"] == "JETZT_TRADEN") is allowed
    assert result["alertable_crypto"] is allowed
    assert result["max_execution_spread_bps"] == api._EARLY_MOVER_MAX_SPREAD_BPS == 20
    if not allowed:
        assert result["risk_level"] == "HIGH"


@pytest.mark.parametrize("payload", [None, {}, {"symbol": "OTHERUSDT", "bidPrice": "10", "askPrice": "10"}])
def test_binance_book_ticker_missing_or_wrong_contract_is_unknown(monkeypatch, payload):
    monkeypatch.setattr(api, "_ce_http_json", lambda *_a, **_kw: payload)
    result = api._refresh_crypto_explosion_spread({"exchange": "binance", "contract": "TESTUSDT", "spread_pct": .01})
    assert result["spread_pct"] is None
    assert result["spread_source_timestamp"] is None


@pytest.mark.parametrize("qualified", [False, True])
def test_binance_book_ticker_runs_only_for_a_qualified_deep_check(monkeypatch, qualified):
    calls, seen = [], []
    row = {**_candidate(), "exchange": "binance", "spread_pct": None}
    monkeypatch.setattr(api, "_fetch_crypto_explosion_universe", lambda: ([row], {}))
    def bars(_contract, _exchange, timeframe, count):
        return _bars(60, interval={"5m": 300, "15m": 900, "4h": 14400}[timeframe]) if qualified else []
    monkeypatch.setattr(api, "_fetch_exchange_candles_any", bars)
    monkeypatch.setattr(api, "_refresh_crypto_funding", lambda row: row)
    def fetch(url, params):
        calls.append((url, params))
        return {"symbol": "TESTUSDT", "bidPrice": "10", "askPrice": "10", "time": 123}
    monkeypatch.setattr(api, "_ce_http_json", fetch)
    monkeypatch.setattr(api, "_score_crypto_explosion_candidate", lambda row, *_a: seen.append(row) or None)
    api._run_crypto_explosion_scan()
    assert len(calls) == int(qualified)
    if qualified:
        assert calls[0] == ("https://fapi.binance.com/fapi/v1/ticker/bookTicker", {"symbol": "TESTUSDT"})
        assert seen[0]["spread_pct"] == 0
        assert seen[0]["spread_source_timestamp"] == 123
