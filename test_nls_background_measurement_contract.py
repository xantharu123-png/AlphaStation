"""Offline production-path counterexamples: missing data, units and context truth."""

import time
from datetime import datetime, timezone

import pytest

import api
import bg_service as bg
from modules import data_fetchers
from modules import new_listing_scanner as nls


@pytest.fixture(autouse=True)
def no_live_work(monkeypatch):
    def blocked(*_args, **_kwargs):
        raise AssertionError("No external request permitted in regression tests")

    monkeypatch.setattr(nls, "_api_get", blocked)
    monkeypatch.setattr(nls.req, "get", blocked)
    monkeypatch.setattr(nls, "_FUNDING_MEASUREMENT_CACHE", {})
    monkeypatch.setattr(nls, "_BINANCE_FUNDING_INFO_CACHE", {"ts": 0.0, "data": None})
    monkeypatch.setattr(bg, "_update_status", lambda *_a, **_kw: None)
    monkeypatch.setattr(bg, "cache_write", lambda *_a, **_kw: None)
    monkeypatch.setattr(bg, "_atomic_write_json", blocked)


@pytest.mark.parametrize("rate", [None, "", "NaN", float("inf"), True, False])
def test_unknown_funding_never_means_zero(rate):
    fields = nls._funding_fields(rate, 8)
    assert fields["funding_rate"] is None
    assert fields["funding_available"] is False
    assert fields["funding_data_status"] == "missing_rate"


@pytest.mark.parametrize("interval", [None, 0, -1, "NaN", float("inf"), True, False])
def test_unknown_interval_is_not_eight_hours(interval):
    fields = nls._funding_fields(0, interval)
    assert fields["funding_rate"] == 0
    assert fields["funding_rate_available"] is True
    assert fields["funding_interval_hours"] is None
    assert fields["funding_available"] is False


@pytest.mark.parametrize("venue, symbol, payload", [
    ("mexc", "TEST_USDT", {"success": True, "data": {"symbol": "TEST_USDT", "fundingRate": 0, "collectCycle": 4, "timestamp": 123}}),
    ("bitget", "TESTUSDT", {"code": "00000", "requestTime": 123, "data": [{"symbol": "TESTUSDT", "fundingRate": "0", "fundingRateInterval": "4"}]}),
])
def test_actual_zero_and_venue_interval_are_measurements(monkeypatch, venue, symbol, payload):
    calls = []
    monkeypatch.setattr(nls, "_api_get", lambda url, *_a, **_kw: calls.append(url) or payload)
    value = nls.fetch_funding_measurement(venue, symbol)
    assert value["funding_rate"] == 0
    assert value["funding_available"] is True
    assert value["funding_interval_hours"] == 4
    assert value["funding_source_timestamp"] == 123
    assert value["funding_retrieved_at"]
    again = nls.fetch_funding_measurement(venue, symbol)
    assert again == value
    assert len(calls) == 1


@pytest.mark.parametrize("info, expected", [(None, None), ([], None), ({"code": -1}, None), ([{"symbol": "TESTUSDT", "fundingIntervalHours": 1}], 1)])
def test_binance_interval_needs_explicit_matching_metadata(monkeypatch, info, expected):
    monkeypatch.setattr(nls, "_api_get", lambda url, *_a, **_kw: info if url.endswith("fundingInfo") else {"symbol": "TESTUSDT", "lastFundingRate": "0.001", "time": 123})
    value = nls.fetch_funding_measurement("binance", "TESTUSDT")
    assert value["funding_rate"] == 0.001
    assert value["funding_interval_hours"] == expected
    assert value["funding_available"] is (expected is not None)


def test_mexc_contract_volume_without_quote_amount_stays_unknown(monkeypatch):
    monkeypatch.setattr(nls, "_api_get", lambda *_a, **_kw: {"success": True, "data": {
        "time": [1], "open": [10], "high": [11], "low": [9], "close": [10], "vol": [500000],
    }})
    candle = nls.fetch_mexc_candles("TEST_USDT")[0]
    assert candle["volume"] == 500000
    assert candle["volume_usd"] is None
    assert candle["volume_usd_available"] is False


def test_missing_zero_funding_cannot_increase_listing_score(monkeypatch):
    as_of = datetime(2026, 9, 4, 12, tzinfo=timezone.utc)
    now = int(as_of.timestamp())
    candles = [{"timestamp": now - (20 - i) * 3600, "open": 100 + i, "high": 125,
                "low": 99 + i, "close": 101 + i, "volume_usd": 100000} for i in range(20)]
    monkeypatch.setattr(nls, "fetch_binance_candles", lambda *_a, **_kw: [])
    measured, _, known = nls.calculate_listing_exhaustion(candles, {"funding_rate": 0, "funding_interval_hours": 8, "funding_available": True}, as_of=as_of)
    missing, _, unknown = nls.calculate_listing_exhaustion(candles, {}, as_of=as_of)
    assert missing <= measured
    assert known["normalization_denominator"] == unknown["normalization_denominator"] == 165
    assert "funding" in unknown["missing_dimensions"]


class Response:
    def __init__(self, data, status=200):
        self.data, self.status_code = data, status

    def json(self):
        return self.data


def test_bg_crash_flat_spy_not_oversold_and_ten_proxy_bars_not_twenty(monkeypatch):
    spy = [{"c": 100.0, "h": 100.0, "l": 100.0, "v": 1000} for _ in range(200)]
    def fetch(url, *_a, **_kw):
        if "/SPY/" in url:
            return Response({"results": spy})
        if "/UVXY/" in url or "/VIXY/" in url:
            return Response({"results": [{"c": 10} for _ in range(10)]})
        return Response({"tickers": [{"todaysChangePerc": 0}]})
    monkeypatch.setattr(data_fetchers, "rate_limited_get", fetch)
    result = bg._fetch_crash_monitor("test-key")
    assert result["spy"]["rsi"] == 50
    assert result["vix"] == {}
    assert result["partial_fear_score"] == 0
    assert result["fear_score"] is None
    assert result["data_status"] == "partial"


def test_bg_crash_no_sources_never_reports_zero_fear(monkeypatch):
    monkeypatch.setattr(data_fetchers, "rate_limited_get", lambda *_a, **_kw: Response({}, 503))
    result = bg._fetch_crash_monitor("test-key")
    assert result["fear_score"] is None
    assert result["partial_fear_score"] is None
    assert result["data_status"] == "unavailable"


def test_btc_missing_benchmark_never_becomes_flat_weak_btc(monkeypatch):
    calls = []
    coins = [{"id": "bitcoin", "symbol": "btc", "current_price": 100000}]
    monkeypatch.setattr(nls.req, "get", lambda *_a, **_kw: Response(coins))
    monkeypatch.setattr(bg.time, "sleep", lambda *_a: None)
    monkeypatch.setattr(bg, "_atomic_write_json", lambda path, value: calls.append((path, value)))
    bg._run_btc_divergence()
    result = next(value for path, value in calls if path.endswith("div_scan_results.json"))
    assert result["results"] == []
    assert result["data_status"] == "missing_btc_benchmark"
    assert result["btc"]["change_7d"] is None


def test_btc_measured_zero_is_not_replaced_by_secondary_value():
    result = bg._btc_change_fields({"price_change_percentage_7d_in_currency": 0, "price_change_percentage_7d": 50})
    assert result["change_7d"] == 0
    assert result["change_14d"] is None


def test_coingecko_rsi_neutral_flat_with_real_4h_cadence():
    now = 1_800_000_000
    bars = [[(now - (19 - i) * 14400) * 1000, 100, 101, 99, 100] for i in range(20)]
    assert bg._coingecko_ohlc_rsi(bars, now_ts=now) == (50.0, "4H")
    assert bg._coingecko_ohlc_rsi(bars[:8] + bars[9:], now_ts=now) == (None, None)
    assert bg._coingecko_ohlc_rsi(bars + [bars[-1]], now_ts=now) == (None, None)
    future = [(now + 14400) * 1000, 100, 100000, 99, 100000]
    assert bg._coingecko_ohlc_rsi(bars + [future], now_ts=now) == (50.0, "4H")


def test_oi_delta_cannot_claim_24h_from_thirty_minutes():
    assert bg._timestamped_oi_delta(130, 100, 0.5) == (30, False)
    assert bg._timestamped_oi_delta(130, 100, 24) == (30, True)
    assert bg._timestamped_oi_delta(130, 100, None) == (None, False)
    assert bg._timestamped_oi_delta(0, 100, 24) == (-100, True)


def test_oi_history_retains_and_selects_measured_24h_reference():
    now = 200000
    payload = {"history": [{"timestamp": now - 86400, "values": {"BTC": 100}}, {"timestamp": now - 1800, "values": {"BTC": 120}}]}
    history, previous, hours = bg._oi_history_reference(payload, now)
    assert len(history) == 2
    assert previous == {"BTC": 100}
    assert hours == 24
    assert bg._oi_history_reference({"BTC": 100}, now) == ([], {}, None)


def test_context_without_trade_plan_never_says_short_now():
    text, quality, gate = bg._btc_div_signal_status(80, 0.9, -2, 5, True)
    assert quality == 5 and gate
    assert "SHORT-KONTEXT" in text
    assert "kein Einstiegssignal" in text
    assert "JETZT" not in text


def test_api_crash_no_data_is_not_neutral_or_extreme_fear():
    score, label, details = api._calculate_fear_score({}, {}, [])
    assert score is None
    assert label == "DATEN FEHLEN"
    assert details["data_status"] == "unavailable"
    assert details["partial_score"] is None


def test_api_crash_proxy_never_gets_vix_level_or_trend_weights():
    score, _, details = api._calculate_fear_score({"ticker": "UVXY", "price": 30, "change_5d": 10}, {}, [])
    assert score is None
    assert "vix_level" not in details
    assert "vix_trend" not in details
    assert details["coverage_weight"] == 0


def test_api_crash_complete_real_measurements_score_and_continuity():
    indices = [{"ticker": t, "change_5d": 0, "change_20d": 0} for t in ("SPY", "QQQ", "IWM", "DIA")]
    vix = {"ticker": "I:VIX", "price": 12, "change_5d": 0}
    score, _, details = api._calculate_fear_score(vix, {"ad_ratio": 1}, indices)
    assert score == 60
    assert details["coverage_weight"] == 100
    _, _, just_above = api._calculate_fear_score({**vix, "price": 12.001}, {"ad_ratio": 1}, indices)
    assert abs(details["vix_level"] - just_above["vix_level"]) < 0.1
    incomplete = api._calculate_fear_score({**vix, "change_5d": None}, {"ad_ratio": 1}, indices)
    assert incomplete[0] is None
    assert incomplete[2]["coverage_weight"] == 90


def test_api_crash_wrapper_does_not_invent_vix_from_uvxy(monkeypatch):
    stored = []
    def fetch(url, *_a, **_kw):
        if "/UVXY/" in url:
            return Response({"results": [{"c": 10} for _ in range(10)]})
        return Response({}, 503)
    monkeypatch.setattr(api, "rate_limited_get", fetch)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda: (set(), "test"))
    monkeypatch.setattr(api, "save_cache_file", lambda path, rows: stored.extend(rows))
    api._crash_monitor_wrapper()
    result = stored[-1]
    assert result["fear_score"] is None
    assert result["vix"] == {}
    assert result["vix_proxy"]["price"] == 10
    assert result["vix_proxy"]["is_vix_index"] is False
    assert result["breadth"]["breadth_signal"] == "DATEN FEHLEN"
    assert result["breadth"]["ad_ratio"] is None
    assert result["data_status"] == "unavailable"
