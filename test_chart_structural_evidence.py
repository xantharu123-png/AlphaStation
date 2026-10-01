"""Actual chart evidence and serializers share a frozen completed-bar prefix.

Run with the project's offline harness: API import cannot read production
credentials, and every provider boundary below is replaced explicitly.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import api
from modules import data_fetchers


UTC = timezone.utc
BASE = datetime(2026, 9, 28, 13, 30, tzinfo=UTC)


def candle(opened=BASE, *, volume=10.125, **changes):
    value = {"time": int(opened.timestamp()), "open": 100.2, "high": 102.0,
             "low": 100.0, "close": 101.8, "volume": volume}
    value.update(changes)
    return value


def evidence(bars, ticker="AAPL", timeframe="1H", as_of=None):
    return api._chart_completed_evidence(
        bars, ticker, timeframe, as_of or BASE + timedelta(hours=5))


def test_us_daily_uses_exchange_close_without_replacing_provider_chart_timestamp():
    raw_time = datetime(2026, 9, 28, 4, tzinfo=UTC)
    raw = candle(raw_time)
    as_of = datetime(2026, 9, 28, 20, tzinfo=UTC)
    adapted, completed, mapping, annotated = evidence([raw], timeframe="1D", as_of=as_of)
    assert len(completed) == 1
    assert completed[0].opened_at == BASE
    assert completed[0].closed_at == as_of
    assert mapping[BASE.timestamp()] == raw["time"]
    assert annotated[0]["time"] == raw["time"]
    assert annotated[0]["is_closed"] is True
    assert annotated[0]["close_time"] == as_of.timestamp()
    assert raw.get("is_closed") is None  # Annotation must not mutate provider payload.


def test_us_daily_remains_open_before_regular_exchange_close():
    raw = candle(datetime(2026, 9, 28, 4, tzinfo=UTC))
    _, completed, _, annotated = evidence(
        [raw], timeframe="1D", as_of=datetime(2026, 9, 28, 19, 59, 59, tzinfo=UTC))
    assert completed == ()
    assert annotated[0]["is_closed"] is False
    assert annotated[0]["close_time"] is None


def test_us_daily_half_day_uses_calendar_close_not_fixed_16_et():
    raw = candle(datetime(2026, 11, 27, 5, tzinfo=UTC))
    early_close = datetime(2026, 11, 27, 18, tzinfo=UTC)
    _, completed, _, annotated = evidence([raw], timeframe="1D", as_of=early_close)
    assert len(completed) == 1
    assert completed[0].closed_at == early_close
    assert annotated[0]["is_closed"] is True
    assert annotated[0]["close_time"] == early_close.timestamp()


@pytest.mark.parametrize("opened", [datetime(2026, 9, 26, 4, tzinfo=UTC),
                                    datetime(2026, 12, 25, 5, tzinfo=UTC)])
def test_us_daily_without_an_exchange_session_cannot_be_confirmed(opened):
    _, completed, _, annotated = evidence([candle(opened)], timeframe="1D",
                                          as_of=opened + timedelta(days=2))
    assert completed == ()
    assert all(not row["is_closed"] and row["close_time"] is None for row in annotated)


@pytest.mark.parametrize("ticker", ["BTC-USD", "X:BTCUSD", "EURUSD=X"])
def test_non_us_daily_retains_provider_duration_not_us_calendar(ticker):
    opened = datetime(2026, 9, 27, 0, tzinfo=UTC)  # Sunday: valid for crypto.
    raw = candle(opened)
    _, before, _, early = evidence([raw], ticker=ticker, timeframe="1D",
                                   as_of=opened + timedelta(hours=20))
    assert before == () and early[0]["is_closed"] is False
    _, completed, _, annotated = evidence([raw], ticker=ticker, timeframe="1D",
                                          as_of=opened + timedelta(days=1))
    assert completed[0].opened_at == opened
    assert completed[0].closed_at == opened + timedelta(days=1)
    assert annotated[0]["time"] == raw["time"]
    assert annotated[0]["close_time"] == (opened + timedelta(days=1)).timestamp()


@pytest.mark.parametrize("flags", [
    {"is_closed": False}, {"complete": False}, {"completed": 0}, {"final": "open"},
    {"is_closed": True, "complete": False},
    {"is_closed": True, "completed": False},
    {"complete": True, "final": False},
])
def test_any_explicit_unfinished_alias_prevents_closed_chart_evidence(flags):
    _, completed, _, annotated = evidence([candle(**flags)])
    assert completed == ()
    assert annotated[0]["is_closed"] is False
    assert annotated[0]["close_time"] is None


@pytest.mark.parametrize("offset,expected", [(1799, False), (1800, True)])
def test_explicit_close_time_is_respected_at_exact_frozen_cutoff(offset, expected):
    close_time = BASE + timedelta(minutes=30)
    _, completed, _, annotated = evidence(
        [candle(close_time=close_time.isoformat(), is_closed=True)],
        as_of=BASE + timedelta(seconds=offset))
    assert bool(completed) is expected
    assert annotated[0]["is_closed"] is expected
    assert annotated[0]["close_time"] == (close_time.timestamp() if expected else None)


def test_nominal_hour_is_not_closed_at_its_open_even_with_positive_provider_flag():
    _, completed, _, annotated = evidence([candle(is_closed=True)], as_of=BASE)
    assert completed == ()
    assert annotated[0]["is_closed"] is False


def test_conflicting_duplicate_close_is_excluded_without_promoting_either_raw_bar():
    first = candle(is_closed=True)
    conflict = candle(is_closed=True, close=100.8)
    _, completed, _, annotated = evidence([first, conflict])
    assert completed == ()
    assert all(not row["is_closed"] and row["close_time"] is None for row in annotated)


@pytest.mark.parametrize("excluded", [{"is_closed": False}, {"high": 99.0}])
def test_valid_duplicate_cannot_promote_excluded_raw_record_by_timestamp(excluded):
    first = candle(is_closed=True)
    bad = candle(**excluded)
    _, completed, _, annotated = evidence([first, bad])
    # Dropping the entire ambiguous instant is also safe: no arbitrary winner.
    assert len(completed) <= 1
    assert len(annotated) <= 1 or (annotated[1]["is_closed"] is False
                                  and annotated[1]["close_time"] is None)
    assert sum(row["is_closed"] for row in annotated) == len(completed)


def test_identical_duplicates_have_only_one_closed_volume_contribution():
    raw = candle(is_closed=True)
    _, completed, _, annotated = evidence([raw, dict(raw)])
    assert len(completed) == 1
    closed = [row for row in annotated if row["is_closed"]]
    assert len(closed) == 1
    assert sum(row["volume"] for row in closed) == raw["volume"]


def test_aggregate_growing_4h_tail_waits_for_nominal_end_not_last_source_close():
    opened = datetime(2026, 9, 28, 8, tzinfo=UTC)
    raw = [{"t": int((opened + timedelta(hours=i)).timestamp()),
            "o": 100.2, "h": 102.0, "l": 100.0, "c": 101.8, "v": 10.125}
           for i in range(2)]
    aggregate = data_fetchers._aggregate_session_bars(raw, bars_per_bucket=4)
    assert len(aggregate) == 1
    bar = aggregate[0]
    assert bar["source_bar_count"] == 2
    assert bar["partial_source_bar"] is True
    assert bar["close_time"] == (opened + timedelta(hours=4)).timestamp()
    chart_bar = {"time": bar["t"], "open": bar["o"], "high": bar["h"],
                 "low": bar["l"], "close": bar["c"], "volume": bar["v"],
                 "close_time": bar["close_time"]}
    _, completed, _, annotated = evidence([chart_bar], timeframe="4H",
                                          as_of=opened + timedelta(hours=2))
    assert completed == ()
    assert annotated[0]["is_closed"] is False


@pytest.mark.parametrize("provider", ["polygon", "yahoo"])
def test_4h_provider_formatters_preserve_nominal_close_and_source_counts(monkeypatch, provider):
    opened = datetime(2026, 9, 28, 8, tzinfo=UTC)
    times = [int((opened + timedelta(hours=i)).timestamp()) for i in range(2)]
    if provider == "polygon":
        payload = {"results": [{"t": value * 1000, "o": 100.2, "h": 102,
                                 "l": 100, "c": 101.8, "v": 10.125}
                                for value in reversed(times)]}
    else:
        payload = {"chart": {"result": [{"timestamp": times,
                    "meta": {"exchangeTimezoneName": "UTC"}, "indicators": {
                        "quote": [{"open": [100.2] * 2, "high": [102] * 2,
                                   "low": [100] * 2, "close": [101.8] * 2,
                                   "volume": [10.125] * 2}]}}]}}
    response = SimpleNamespace(status_code=200, json=lambda: payload)
    monkeypatch.setattr(data_fetchers, "rate_limited_get", lambda *args, **kwargs: response)
    bars = (data_fetchers._fetch_ohlcv_polygon("AAPL", "offline", "4H")
            if provider == "polygon" else data_fetchers._fetch_ohlcv_yahoo("VNA.DE", "4H"))
    assert len(bars) == 1
    assert bars[0]["time"] == times[0]
    assert bars[0]["close_time"] == times[0] + 4 * 3600
    assert bars[0]["source_bar_count"] == 2
    assert bars[0]["partial_source_bar"] is True
    assert bars[0]["volume"] == 20.25


@pytest.fixture
def chart_endpoint(monkeypatch):
    state = SimpleNamespace(now=datetime(2026, 9, 30, 20, tzinfo=UTC), bars=[])

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return state.now if tz else state.now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", FrozenDatetime)
    # Existing tests exercise the undelayed market-clock contract explicitly.
    # Starter availability is a separate endpoint contract below, not a hidden
    # dependency on the test runner's environment.
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: False)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *args, **kwargs: list(state.bars))
    return state


def _availability_boundary_history(timeframe):
    if timeframe == "1D":
        days = (21, 22, 23, 24, 25, 28)
        rows = [candle(datetime(2026, 9, day, 4, tzinfo=UTC)) for day in days]
        return rows, datetime(2026, 9, 28, 20, tzinfo=UTC)
    duration = timedelta(hours=1) if timeframe == "1H" else timedelta(minutes=5)
    closed_at = datetime(2026, 9, 30, 14, tzinfo=UTC)
    return [candle(closed_at-duration*index, is_closed=True) for index in range(6, 0, -1)], closed_at


@pytest.mark.parametrize("timeframe", ["5m", "1H", "1D"])
@pytest.mark.parametrize("after_close,expected", [(899.875, False), (900., True), (900.125, True)])
def test_endpoint_starter_requires_full_delay_after_bar_close(
    monkeypatch, chart_endpoint, timeframe, after_close, expected
):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: True)
    rows, closed_at = _availability_boundary_history(timeframe)
    chart_endpoint.bars = rows
    chart_endpoint.now = closed_at + timedelta(seconds=after_close)
    requested = chart_endpoint.now
    result = api.get_chart_data(ticker="VIAV", timeframe=timeframe, overlays="", direction=None)
    assert result["chart_requested_at"] == requested.timestamp()
    assert result["chart_as_of"] == (requested-timedelta(seconds=api.stock_swing.DELAY_SECONDS)).timestamp()
    assert result["candles"][-1]["is_closed"] is expected
    assert result["candles"][-1]["close_time"] == (closed_at.timestamp() if expected else None)
    assert result["completed_bar_count"] == 5 + int(expected)
    assert all(row["close_time"] <= result["chart_as_of"] for row in result["candles"] if row["is_closed"])


def test_endpoint_viav_hour_at_1401_is_not_provider_closed(monkeypatch, chart_endpoint):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: True)
    rows, closed_at = _availability_boundary_history("1H")
    chart_endpoint.bars = rows
    chart_endpoint.now = closed_at + timedelta(minutes=1, seconds=7, microseconds=123456)
    result = api.get_chart_data(ticker="VIAV", timeframe="1H", overlays="", direction=None)
    assert result["chart_as_of"] == datetime(2026, 9, 30, 13, 46, 7, 123456, tzinfo=UTC).timestamp()
    assert result["candles"][-1]["time"] == (closed_at-timedelta(hours=1)).timestamp()
    assert result["candles"][-1]["is_closed"] is False
    assert result["completed_bar_count"] == 5


@pytest.mark.parametrize("ticker", ["BTC-USD", "X:BTCUSD", "VNA.DE", "EURUSD=X", "GC=F"])
def test_endpoint_starter_does_not_delay_non_us_provider_routes(monkeypatch, chart_endpoint, ticker):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: True)
    chart_endpoint.bars, closed_at = _availability_boundary_history("1H")
    chart_endpoint.now = closed_at + timedelta(seconds=1.125)
    result = api.get_chart_data(ticker=ticker, timeframe="1H", overlays="", direction=None)
    assert result["chart_as_of"] == chart_endpoint.now.timestamp()
    assert result["chart_requested_at"] == chart_endpoint.now.timestamp()
    assert result["candles"][-1]["is_closed"] is True


@pytest.mark.parametrize("timeframe", ["5m", "1H", "1D"])
def test_endpoint_explicit_live_mode_does_not_add_starter_delay(monkeypatch, chart_endpoint, timeframe):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: False)
    chart_endpoint.bars, closed_at = _availability_boundary_history(timeframe)
    chart_endpoint.now = closed_at + timedelta(microseconds=125000)
    result = api.get_chart_data(ticker="VIAV", timeframe=timeframe, overlays="", direction=None)
    assert result["chart_as_of"] == chart_endpoint.now.timestamp()
    assert result["candles"][-1]["is_closed"] is True


def test_endpoint_slow_starter_fetch_cannot_advance_available_cutoff(monkeypatch, chart_endpoint):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: True)
    chart_endpoint.bars, closed_at = _availability_boundary_history("1H")
    chart_endpoint.now = closed_at + timedelta(seconds=api.stock_swing.DELAY_SECONDS-1)
    requested = chart_endpoint.now

    def slow_fetch(*args, **kwargs):
        chart_endpoint.now += timedelta(hours=3)
        return list(chart_endpoint.bars)

    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", slow_fetch)
    result = api.get_chart_data(ticker="VIAV", timeframe="1H", overlays="", direction=None)
    assert result["chart_requested_at"] == requested.timestamp()
    assert result["chart_as_of"] == (closed_at-timedelta(seconds=1)).timestamp()
    assert result["candles"][-1]["is_closed"] is False
    assert result["completed_bar_count"] == 5


def test_endpoint_cache_separates_starter_and_live_availability(monkeypatch, chart_endpoint):
    mode = SimpleNamespace(starter=True)
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: mode.starter)
    chart_endpoint.bars, closed_at = _availability_boundary_history("1H")
    chart_endpoint.now = closed_at + timedelta(minutes=1)
    delayed = api.get_chart_data(ticker="VIAV", timeframe="1H", overlays="", direction=None)
    mode.starter = False
    live = api.get_chart_data(ticker="VIAV", timeframe="1H", overlays="", direction=None)
    assert delayed["candles"][-1]["is_closed"] is False
    assert live["candles"][-1]["is_closed"] is True
    assert len(api._CHART_CACHE) == 2


@pytest.mark.parametrize("after_close", [899.875, 900.125])
def test_endpoint_every_structural_overlay_uses_same_available_cutoff(
    monkeypatch, chart_endpoint, after_close
):
    monkeypatch.setattr(api.stock_swing, "enabled", lambda: True)
    closed_at = datetime(2026, 9, 30, 14, tzinfo=UTC)
    chart_endpoint.now = closed_at + timedelta(seconds=after_close)
    chart_endpoint.bars = [candle(closed_at-timedelta(hours=index), is_closed=True)
                           for index in range(30, 0, -1)]
    cutoff = chart_endpoint.now-timedelta(seconds=api.stock_swing.DELAY_SECONDS)
    expected_count = 29 + int(after_close >= api.stock_swing.DELAY_SECONDS)
    observed = {}

    def sr(bars, current, **kwargs):
        observed["sr"] = kwargs["as_of"]
        return (([], []), {})

    def trends(bars, **kwargs):
        observed["trends"] = kwargs["as_of"]
        return []

    def fib(highs, lows, closes, **kwargs):
        observed["fib"] = kwargs["as_of"]
        assert len(closes) == expected_count
        assert all(value <= cutoff for value in kwargs["times"])
        return None

    def patterns(bars, **kwargs):
        observed["patterns"] = kwargs["wyckoff_context"]["as_of"]
        assert len(bars) == expected_count
        assert all(datetime.fromisoformat(bar["close_time"]) <= cutoff for bar in bars)
        return []

    volume_profile = api.calculate_volume_profile

    def vrvp(bars, **kwargs):
        observed["vrvp_count"] = len(bars)
        assert all(datetime.fromisoformat(bar["closed_at"].replace("Z", "+00:00")) <= cutoff for bar in bars)
        return volume_profile(bars, **kwargs)

    monkeypatch.setattr(api, "HAS_REAL_SR", True)
    monkeypatch.setattr(api, "HAS_PATTERNS", True)
    monkeypatch.setattr(api, "calculate_sr_from_historical", sr)
    monkeypatch.setattr(api, "build_causal_trendlines", trends)
    monkeypatch.setattr(api, "_calculate_directional_fib_levels", fib)
    monkeypatch.setattr(api, "detect_chart_patterns", patterns)
    monkeypatch.setattr(api, "find_harmonic_for_chart", lambda bars: [])
    monkeypatch.setattr(api, "calculate_volume_profile", vrvp)
    result = api.get_chart_data(ticker="VIAV", timeframe="1H", overlays="sr,fib,patterns,vrvp", direction=None)
    assert {key: observed[key] for key in ("sr", "trends", "fib", "patterns")} == dict.fromkeys(
        ("sr", "trends", "fib", "patterns"), cutoff)
    assert observed["vrvp_count"] == result["completed_bar_count"] == expected_count
    assert result["vrvp"]["data_cutoff_at"] == cutoff.isoformat()
    assert result["trendline_meta"]["data_cutoff_at"] == cutoff.isoformat()
    assert result["chart_as_of"] == cutoff.timestamp()


def _hour_history(count=25):
    start = datetime(2026, 9, 29, 12, tzinfo=UTC)
    return [candle(start + timedelta(hours=i), low=100 + i * 0.05,
                   high=102 + i * 0.05, open=100.2 + i * 0.05,
                   close=101.8 + i * 0.05, volume=1.23456789 + i * 0.000123)
            for i in range(count)]


def test_endpoint_freezes_causality_before_a_slow_provider_response(monkeypatch, chart_endpoint):
    frozen = chart_endpoint.now
    chart_endpoint.bars = _hour_history(5) + [candle(frozen, is_closed=True)]

    def slow_fetch(*args, **kwargs):
        chart_endpoint.now += timedelta(hours=3)
        return list(chart_endpoint.bars)

    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", slow_fetch)
    result = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="", direction=None)
    assert result["chart_as_of"] == frozen.timestamp()
    assert result["completed_bar_count"] == 5
    assert result["candles"][-1]["is_closed"] is False
    assert result["candles"][-1]["close_time"] is None
    assert all(row["close_time"] <= result["chart_as_of"] for row in result["candles"] if row["is_closed"])


def test_endpoint_trend_points_use_original_daily_chart_coordinates(monkeypatch, chart_endpoint):
    chart_endpoint.bars = [candle(datetime(2026, 9, 21, 4, tzinfo=UTC) + timedelta(days=i))
                           for i in range(5)]
    chart_endpoint.now = datetime(2026, 9, 30, 20, tzinfo=UTC)
    seen = []

    def trendlines(bars, **kwargs):
        seen.append(kwargs["as_of"])
        return [{"type": "support", "points": [
            {"time": bar["open_time"].timestamp(), "price": bar["low"]}
            for bar in bars]}]

    monkeypatch.setattr(api, "build_causal_trendlines", trendlines)
    result = api.get_chart_data(ticker="AAPL", timeframe="1D", overlays="sr", direction=None)
    assert seen == [chart_endpoint.now]
    assert [point["time"] for point in result["trendlines"][0]["points"]] == [
        row["time"] for row in chart_endpoint.bars]
    assert result["candles"][0]["time"] == chart_endpoint.bars[0]["time"]


def _trend_history(side="support"):
    start = datetime(2026, 9, 21, 0, tzinfo=UTC)
    rows = [candle(start + timedelta(hours=index), low=108 + index * .1,
                   high=110 + index * .1, open=109 + index * .1,
                   close=109 + index * .1, is_closed=True)
            for index in range(80)]
    for index, price in ((10, 100.0), (25, 101.5), (40, 103.0)):
        rows[index]["low"] = price
    if side == "resistance":
        rows = [{**row, "open": 250-row["open"], "high": 250-row["low"],
                 "low": 250-row["high"], "close": 250-row["close"]}
                for row in rows]
    return rows


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_endpoint_interior_unfinished_candle_cannot_shift_confirmed_trend_geometry(
    monkeypatch, chart_endpoint, side
):
    """The model uses completed-bar indices; the chart still displays open bars.

    Two endpoints alone interpolate on the longer chart axis and miss the
    second/third confirmed touches. Every completed coordinate must retain
    the model value, without assigning structural evidence to the open gap.
    """
    rows = _trend_history(side)
    chart_endpoint.bars = rows
    chart_endpoint.now = datetime.fromtimestamp(rows[-1]["time"], tz=UTC) + timedelta(hours=1)
    plain = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="sr", direction=None)
    original = next(line for line in plain["trendlines"] if line["type"] == side)
    interior = {**rows[20], "time": rows[20]["time"]+1800, "is_closed": False,
                "high": 1000.0, "volume": 1e20}
    chart_endpoint.bars = rows[:21]+[interior]+rows[21:]
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    result = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="sr", direction=None)
    line = next(item for item in result["trendlines"] if item["id"] == original["id"])
    assert line["points"] == original["points"]
    assert line["status"] == "active"
    assert result["completed_bar_count"] == 80
    assert len(result["candles"]) == 81
    assert next(row for row in result["candles"] if row["time"] == interior["time"])["is_closed"] is False
    by_time = {point["time"]: point["price"] for point in line["points"]}
    assert interior["time"] not in by_time
    assert len(by_time) == 70  # First confirmed anchor through last completed bar.
    for index, support_price in ((10, 100.0), (25, 101.5), (40, 103.0), (79, 106.9)):
        price = support_price if side == "support" else 250-support_price
        assert by_time[rows[index]["time"]] == pytest.approx(price)
    positions = {bar["time"]: index for index, bar in enumerate(result["candles"])}
    assert positions[rows[25]["time"]] == 26
    assert positions[rows[40]["time"]] == 41


@pytest.mark.parametrize("side", ["support", "resistance"])
def test_endpoint_running_tail_does_not_move_or_extend_confirmed_trend(side, monkeypatch, chart_endpoint):
    rows = _trend_history(side)
    chart_endpoint.bars = rows
    chart_endpoint.now = datetime.fromtimestamp(rows[-1]["time"], tz=UTC) + timedelta(hours=1)
    plain = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="sr", direction=None)
    original = next(line for line in plain["trendlines"] if line["type"] == side)
    tail = candle(chart_endpoint.now, low=1e6, open=1.1e6, close=1.2e6,
                  high=2e6, volume=1e20, is_closed=False)
    chart_endpoint.bars = rows+[tail]
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    result = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="sr", direction=None)
    line = next(item for item in result["trendlines"] if item["id"] == original["id"])
    assert line == original
    assert result["candles"][-1]["is_closed"] is False
    assert result["completed_bar_count"] == 80
    assert line["points"][-1]["time"] == rows[-1]["time"]
    assert tail["time"] not in {point["time"] for point in line["points"]}


def test_endpoint_completed_profile_ignores_open_extremes_and_keeps_fractional_bins(
    monkeypatch, chart_endpoint
):
    chart_endpoint.bars = _hour_history()
    expected_volume = sum(row["volume"] for row in chart_endpoint.bars)
    plain = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="vrvp", direction=None)
    monkeypatch.setattr(api, "_CHART_CACHE", {})
    chart_endpoint.bars.append(candle(chart_endpoint.now, open=1e6, high=2e6,
                                      low=1e6, close=1.8e6, volume=1e20, is_closed=True))
    with_open = api.get_chart_data(ticker="AAPL", timeframe="1H", overlays="vrvp", direction=None)
    assert plain["vrvp"]["bins"] == with_open["vrvp"]["bins"]
    assert plain["vrvp"]["poc"] == with_open["vrvp"]["poc"]
    assert plain["completed_bar_count"] == with_open["completed_bar_count"] == 25
    bins = with_open["vrvp"]["bins"]
    assert sum(item["volume"] for item in bins) == pytest.approx(expected_volume, rel=1e-12)
    assert any(item["volume"] != int(item["volume"]) for item in bins)
    assert with_open["vrvp"]["scope"] == "loaded_completed_candles"
    assert with_open["vrvp"]["data_cutoff_at"] == chart_endpoint.now.isoformat()
