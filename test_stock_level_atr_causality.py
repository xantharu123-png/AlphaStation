from datetime import datetime, timedelta, timezone

import api


def _daily_polygon_bar(session_day: datetime, *, high: float, low: float, close: float = 100.0):
    return {
        "t": int(session_day.timestamp() * 1000),
        "o": close,
        "h": high,
        "l": low,
        "c": close,
        "v": 100_000,
    }


def test_ticker_detail_atr_and_snapshot_ignore_running_daily_extremes(monkeypatch):
    cutoff = datetime(2026, 4, 21, 19, 0, tzinfo=timezone.utc)

    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz) if tz is not None else cutoff.replace(tzinfo=None)

    completed = [
        _daily_polygon_bar(
            datetime(2026, 4, index + 1, 14, 0, tzinfo=timezone.utc),
            high=101.0 + (index % 3) * 0.1,
            low=99.0 - (index % 3) * 0.1,
        )
        for index in range(20)
    ]
    running_normal = _daily_polygon_bar(
        datetime(2026, 4, 21, 14, 0, tzinfo=timezone.utc),
        high=101.0,
        low=99.0,
    )
    running_extreme = {**running_normal, "h": 1_000.0, "l": 1.0, "v": 9_000_000}
    payload = {"bars": [running_normal, *reversed(completed)]}

    class Response:
        status_code = 200

        def json(self):
            return {"results": payload["bars"]}

    monkeypatch.setattr(api, "datetime", FrozenDateTime)
    monkeypatch.setattr(api, "rate_limited_get", lambda *args, **kwargs: Response())
    monkeypatch.setattr(api, "load_cache_file", lambda *args, **kwargs: ([], None))

    baseline = api.get_ticker_detail("CAUSAL")
    payload["bars"] = [running_extreme, *reversed(completed)]
    augmented = api.get_ticker_detail("CAUSAL")

    assert baseline["atr"] == augmented["atr"]
    assert baseline["level_structure"] == augmented["level_structure"]
    assert baseline["level_structure"]["completed_bar_counts"]["1D"] == 20


def test_stock_level_snapshot_4h_atr_and_zones_ignore_open_and_future_extremes():
    base = datetime(2026, 4, 1, 13, 30, tzinfo=timezone.utc)
    completed = []
    for index in range(20):
        opened_at = base + timedelta(hours=4 * index)
        midpoint = 100.0 + (index % 4) * 0.4
        completed.append({
            "timestamp": int(opened_at.timestamp() * 1000),
            "source_bar_count": 8,
            "open": midpoint,
            "high": midpoint + 1.0,
            "low": midpoint - 1.0,
            "close": midpoint + (0.2 if index % 2 else -0.2),
            "volume": 10_000 + index,
        })
    cutoff = base + timedelta(hours=4 * len(completed))
    baseline = api._build_stock_level_snapshot(
        [],
        symbol="CAUSAL",
        current_price=100.5,
        direction="LONG",
        atr14=None,
        as_of=cutoff,
        four_hour_bars=completed,
    )
    running = {
        "timestamp": int(cutoff.timestamp() * 1000),
        "source_bar_count": 8,
        "open": 100.0,
        "high": 1_000.0,
        "low": 1.0,
        "close": 900.0,
        "volume": 9_000_000,
    }
    future = {
        **running,
        "timestamp": int((cutoff + timedelta(hours=8)).timestamp() * 1000),
    }
    augmented = api._build_stock_level_snapshot(
        [],
        symbol="CAUSAL",
        current_price=100.5,
        direction="LONG",
        atr14=None,
        as_of=cutoff,
        four_hour_bars=[*completed, running, future],
    )

    assert baseline is not None
    assert augmented is not None
    assert baseline.atr_by_timeframe["4H"] == augmented.atr_by_timeframe["4H"]
    assert baseline.completed_bar_counts["4H"] == 20
    assert augmented.completed_bar_counts["4H"] == 20
    assert baseline.to_dict() == augmented.to_dict()
