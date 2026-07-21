import api
from modules import data_fetchers as df
from datetime import datetime, timezone


def test_polygon_intraday_chart_fetches_newest_bars_first_then_returns_chronological(monkeypatch):
    class FakeResponse:
        status_code = 200

        def json(self):
            return {
                "results": [
                    {"t": 2000, "o": 9.5, "h": 10.5, "l": 9.0, "c": 10.0, "v": 200},
                    {"t": 1000, "o": 4.5, "h": 5.5, "l": 4.0, "c": 5.0, "v": 100},
                ]
            }

    seen = {}

    def fake_get(url, params=None, timeout=None):
        seen["sort"] = params.get("sort")
        return FakeResponse()

    monkeypatch.setattr(df, "rate_limited_get", fake_get)

    bars = df._fetch_ohlcv_polygon("AQST", "test-key", "1H")

    assert seen["sort"] == "desc"
    assert [bar["close"] for bar in bars] == [5.0, 10.0]


def test_four_hour_aggregation_never_bridges_exchange_sessions():
    def ts(year, month, day, hour, minute=0):
        return int(datetime(year, month, day, hour, minute, tzinfo=timezone.utc).timestamp() * 1000)

    bars = [
        {"t": ts(2026, 7, 20, 13, 30), "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100},
        {"t": ts(2026, 7, 20, 14, 30), "o": 10.5, "h": 12, "l": 10, "c": 11, "v": 110},
        {"t": ts(2026, 7, 20, 15, 30), "o": 11, "h": 13, "l": 10.5, "c": 12, "v": 120},
        {"t": ts(2026, 7, 20, 16, 30), "o": 12, "h": 14, "l": 11.5, "c": 13, "v": 130},
        {"t": ts(2026, 7, 21, 13, 30), "o": 20, "h": 21, "l": 19, "c": 20.5, "v": 200},
    ]

    aggregated = df._aggregate_session_bars(
        bars,
        timestamp_in_ms=True,
        timezone_name="America/New_York",
    )

    assert len(aggregated) == 2
    assert aggregated[0]["source_bar_count"] == 4
    assert aggregated[0]["h"] == 14
    assert aggregated[0]["c"] == 13
    assert aggregated[0]["partial_source_bar"] is False
    assert aggregated[1]["source_bar_count"] == 1
    assert aggregated[1]["o"] == 20
    assert aggregated[1]["partial_source_bar"] is True


def test_polygon_four_hour_chart_uses_session_safe_aggregation(monkeypatch):
    def ts(day, hour):
        return int(datetime(2026, 7, day, hour, tzinfo=timezone.utc).timestamp() * 1000)

    chronological = [
        {"t": ts(20, 14), "o": 1, "h": 2, "l": 0.5, "c": 1.5, "v": 10},
        {"t": ts(20, 15), "o": 1.5, "h": 3, "l": 1, "c": 2.5, "v": 20},
        {"t": ts(21, 14), "o": 5, "h": 6, "l": 4, "c": 5.5, "v": 30},
    ]

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"results": list(reversed(chronological))}

    monkeypatch.setattr(df, "rate_limited_get", lambda *args, **kwargs: FakeResponse())

    result = df._fetch_ohlcv_polygon("TEST", "key", "4H")

    assert len(result) == 2
    assert result[0]["open"] == 1
    assert result[0]["close"] == 2.5
    assert result[1]["open"] == 5


def test_completed_candles_sorts_reverse_feed_before_dropping_open_bar():
    bars = [
        {"t": 900, "close": 30},
        {"t": 600, "close": 20},
        {"t": 300, "close": 10},
    ]

    completed = api._completed_candles_only(bars, "5m", now_ts=1000)

    assert [bar["close"] for bar in completed] == [10, 20]
