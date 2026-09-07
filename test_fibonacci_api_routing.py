"""Fibonacci endpoint regressions: synthetic quotes only, no provider traffic."""
from datetime import datetime, timedelta, timezone

import pytest

import api
from modules.analysis import calculate_sr_from_historical


BASE = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _bars(*, mirror=False, hours=24, hour_offset=0):
    values = [(102, 98, 100)] * 10 + [
        (102, 99, 101), (101, 98, 100), (100, 90, 95),
        (105, 99, 102), (112, 102, 108), (120, 108, 116),
        (114, 102, 106), (112, 100, 104), (110, 96, 100), (108, 94, 96),
    ]
    if mirror:
        values = [(240-low, 240-high, 240-close) for high, low, close in values]
    return [
        {"time": int((BASE + timedelta(hours=hours*i+hour_offset)).timestamp()),
         "open": close, "high": high, "low": low, "close": close, "volume": 100_000}
        for i, (high, low, close) in enumerate(values)
    ]


def _freeze(monkeypatch, cutoff):
    class FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cutoff.astimezone(tz) if tz else cutoff.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", FrozenDateTime)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **kw: ([], None))
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: pytest.fail("unexpected provider request"))


@pytest.mark.parametrize("ticker", ["BTC", "BTC-USD", "ETH-EUR", "X:BTCUSD"])
@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_crypto_daily_fib_excludes_running_utc_bar_even_after_us_close(monkeypatch, ticker, direction):
    bars = _bars(mirror=direction == "SHORT")
    cutoff = BASE + timedelta(days=20, hours=22)
    _freeze(monkeypatch, cutoff)
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars)
    baseline = api.get_chart_data(ticker, "1D", "fib", direction)
    assert baseline.get("fib"), baseline
    running = {**bars[-1], "time": int((BASE + timedelta(days=20)).timestamp()),
               "open": 100, "high": 1000, "low": 1, "close": 100}
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars + [running])
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    augmented = api.get_chart_data(ticker, "1D", "fib", direction)
    assert augmented["fib"] == baseline["fib"]
    assert augmented["fib_meta"] == baseline["fib_meta"]
    assert baseline["fib_meta"]["asset_class"] == "crypto"
    assert baseline["fib_meta"]["lookback_bars"] == 20


@pytest.mark.parametrize("ticker,expected_count", [("AAPL", 21), ("BTC-USD", 20), ("VNA.DE", 20), ("EURUSD=X", 20)])
def test_daily_completion_uses_market_context_not_us_clock_for_all_assets(monkeypatch, ticker, expected_count):
    bars = _bars(hour_offset=4)
    bars.append({**bars[-1], "time": int((BASE + timedelta(days=20, hours=4)).timestamp())})
    cutoff = BASE + timedelta(days=20, hours=22)
    completed = api.normalize_completed_bars(api._chart_level_input(bars, ticker, "1D"),
                                             timeframe="1D", as_of=cutoff)
    assert len(completed) == expected_count


@pytest.mark.parametrize("ticker", ["AAPL", "BTC-USD"])
def test_explicit_unclosed_daily_bar_cannot_confirm_a_fib(monkeypatch, ticker):
    bars = _bars(hour_offset=4)
    bars[-1] = {**bars[-1], "is_closed": False}
    completed = api.normalize_completed_bars(api._chart_level_input(bars, ticker, "1D"),
                                             timeframe="1D", as_of=BASE + timedelta(days=30))
    assert len(completed) == 19


@pytest.mark.parametrize("tf,hours", [("5m", 1/12), ("15m", 1/4), ("1H", 1), ("4H", 4), ("1D", 24), ("1W", 168)])
def test_chart_fib_keeps_requested_timeframe_and_two_bar_confirmation(monkeypatch, tf, hours):
    bars = _bars(hours=hours)
    cutoff = BASE + timedelta(hours=hours*20)
    _freeze(monkeypatch, cutoff)
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars)
    result = api.get_chart_data("BTC-USD", tf, "fib", "LONG")
    meta = result["fib_meta"]
    assert meta["timeframe"].upper() == tf.upper()
    assert meta["direction"] == "long"
    assert meta["direction_source"] == "requested"
    assert datetime.fromisoformat(meta["confirmed_at"].replace("Z", "+00:00")) == BASE + timedelta(hours=hours*18)
    assert meta["multi_timeframe_confirmation"] is False


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_ticker_detail_fib_respects_requested_direction_and_matches_daily_chart(monkeypatch, direction):
    bars = _bars(mirror=direction == "SHORT", hour_offset=4)
    _freeze(monkeypatch, BASE + timedelta(days=21))

    class Response:
        status_code = 200

        def json(self):
            return {"results": [
                {"t": b["time"]*1000, "o": b["open"], "h": b["high"], "l": b["low"],
                 "c": b["close"], "v": b["volume"]} for b in reversed(bars)
            ]}

    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **kw: Response())
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars)
    detail = api.get_ticker_detail("AAPL", direction=direction)
    chart = api.get_chart_data("AAPL", "1D", "fib", direction)
    assert detail["fib_meta"]["direction"] == direction.lower()
    assert detail["fib_meta"]["direction_source"] == "requested"
    assert detail["fib_levels"] == chart["fib"]
    assert detail["fib_meta"]["leg_id"] == chart["fib_meta"]["leg_id"]


def test_crypto_sr_and_chart_fib_share_same_completed_daily_prefix(monkeypatch):
    bars = _bars()
    running = {**bars[-1], "time": int((BASE + timedelta(days=20)).timestamp()),
               "open": 100, "high": 1000, "low": 1, "close": 100}
    _freeze(monkeypatch, BASE + timedelta(days=20, hours=22))
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars + [running])
    seen = {}

    def capture(rows, price, **kwargs):
        result = calculate_sr_from_historical(rows, price, **kwargs)
        seen.update(result[1])
        return result

    monkeypatch.setattr(api, "HAS_REAL_SR", True)
    monkeypatch.setattr(api, "calculate_sr_from_historical", capture)
    chart = api.get_chart_data("BTC-USD", "1D", "sr,fib", "LONG")
    assert seen["completed_candles"] == chart["fib_meta"]["lookback_bars"] == 20
    assert seen["fibonacci_provenance"]["leg"]["start_price"] == chart["fib_meta"]["anchor_low"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_chart_and_historical_adapter_both_remove_origin_breached_leg(monkeypatch, direction):
    bars = _bars(mirror=direction == "SHORT", hours=4)
    bars.append({**bars[-1], "time": int((BASE + timedelta(hours=80)).timestamp()),
                 "open": 100, "high": 160, "low": 80, "close": 100})
    cutoff = BASE + timedelta(hours=84)
    _freeze(monkeypatch, cutoff)
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **kw: bars)
    chart = api.get_chart_data("BTC-USD", "4H", "fib", direction)
    assert not chart.get("fib")
    _, info = calculate_sr_from_historical(bars, 100, timeframe="4H", as_of=cutoff, direction=direction)
    assert info["fibonacci_provenance"]["available"] is False
