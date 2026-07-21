from datetime import datetime, timezone

import api
from modules import data_fetchers as df


def test_daily_crypto_history_does_not_silently_cap_above_supported_range(monkeypatch):
    called = False

    def fake_get(*args, **kwargs):
        nonlocal called
        called = True
        raise AssertionError("provider must not be called for an unsupported range")

    monkeypatch.setattr(df, "rate_limited_get", fake_get)

    assert df.fetch_daily_candles_crypto("bitcoin", days=91) == []
    assert df.fetch_historical_data_crypto("bitcoin", days=91) is None
    assert called is False


def test_price_only_crypto_response_is_marked_and_not_cached(monkeypatch):
    start_ms = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp() * 1000)
    prices = [[start_ms + hour * 3_600_000, 1.0 + hour / 1000] for hour in range(72)]
    volumes = [[timestamp, 0] for timestamp, _ in prices]
    calls = 0

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"prices": prices, "total_volumes": volumes}

    def fake_get(*args, **kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr(df, "rate_limited_get", fake_get)
    monkeypatch.setattr(df, "_CANDLE_ANALYSIS_CACHE", {})

    first = df.fetch_daily_candles_crypto("price-only", days=3)
    second = df.fetch_daily_candles_crypto("price-only", days=3)

    assert first and second
    assert first[-1]["volume_available"] is False
    assert first[-1]["data_quality"] == "aggregated_intraday_price_only"
    assert calls == 2


def test_crypto_chart_reports_history_limit_instead_of_returning_partial_history(monkeypatch):
    monkeypatch.setattr(
        api,
        "fetch_daily_candles_crypto",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("must not fetch")),
    )

    result = api.get_crypto_chart("bitcoin", days=180)

    assert result["error"] == "history_limit"
    assert result["requested_days"] == 180
    assert result["max_supported_days"] == 90
    assert result["partial_history"] is False


def test_crypto_chart_preserves_tiny_coin_price_precision(monkeypatch):
    bars = [
        {
            "t": 1_700_000_000_000,
            "o": 0.000000012345,
            "h": 0.000000013456,
            "l": 0.000000011234,
            "c": 0.000000012999,
            "v": 1000,
            "volume_available": True,
            "data_quality": "aggregated_intraday_ohlcv_estimate",
            "source_sample_interval_hours": 1.0,
        },
        {
            "t": 1_700_086_400_000,
            "o": 0.000000012999,
            "h": 0.000000014567,
            "l": 0.000000012100,
            "c": 0.000000014000,
            "v": 1200,
            "volume_available": True,
            "data_quality": "aggregated_intraday_ohlcv_estimate",
            "source_sample_interval_hours": 1.0,
        },
    ]
    monkeypatch.setattr(api, "fetch_daily_candles_crypto", lambda *args, **kwargs: bars)
    monkeypatch.setattr(api, "_CRYPTO_CHART_CACHE", {})

    result = api.get_crypto_chart("tiny-coin", days=2)

    assert result["candles"][0]["open"] == 0.000000012345
    assert result["candles"][1]["close"] == 0.000000014
    assert result["volume_available"] is True
    assert result["data_quality"] == "aggregated_intraday_ohlcv_estimate"
