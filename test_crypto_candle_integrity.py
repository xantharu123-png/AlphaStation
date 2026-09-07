"""Completed execution candles: causal prefixes and physical data integrity."""

from datetime import datetime, timezone
from itertools import permutations

import pytest

import api
from test_crypto_explosion_scanner import _bars, _btc_context, _candidate


NOW = 1_800_000_000


@pytest.fixture(autouse=True)
def offline_fixed_clock(monkeypatch):
    monkeypatch.setattr(api.time, "time", lambda: NOW)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("No network is permitted in candle-integrity regressions")

    monkeypatch.setattr(api.req, "get", forbidden)


def candle(timestamp=NOW - 300, **overrides):
    return {"timestamp": timestamp, "open": 100, "high": 102, "low": 99,
            "close": 101, "volume": 1000, **overrides}


def completed(rows, timeframe="5m", now=NOW):
    return api._ce_completed_bars(rows, timeframe, now_ts=now)


def test_every_open_future_bar_is_removed_not_only_the_final_array_item():
    past = candle(NOW - 600)
    at_close = candle(NOW - 300)
    future = [candle(NOW - 299), candle(NOW), candle(NOW + 300)]
    expected = completed([past, at_close])
    for rows in ([past, at_close, *future], list(reversed([past, *future, at_close]))):
        assert completed(rows) == expected


@pytest.mark.parametrize("multiplier", [1, 1000, 1_000_000, 1_000_000_000])
def test_exchange_epoch_units_are_normalized(multiplier):
    result = completed([candle((NOW - 300) * multiplier)])
    assert result == [candle()]


@pytest.mark.parametrize("stamp", [
    datetime.fromtimestamp(NOW - 300, tz=timezone.utc),
    datetime.fromtimestamp(NOW - 300, tz=timezone.utc).isoformat(),
    datetime.fromtimestamp(NOW - 300, tz=timezone.utc).isoformat().replace("+00:00", "Z"),
])
def test_aware_datetime_and_iso_open_times_are_supported(stamp):
    assert completed([candle(stamp)]) == [candle()]


@pytest.mark.parametrize("stamp", [None, True, False, "", "bad", "nan", float("inf"), -1, 0,
                                  datetime(2026, 1, 1), "2026-01-01T00:00:00"])
def test_missing_invalid_or_ambiguous_open_time_is_not_invented(stamp):
    assert completed([candle(stamp)]) == []


@pytest.mark.parametrize("timeframe", ["", "daily-ish", None, True, "0m", "-5m"])
def test_unknown_timeframe_cannot_claim_completion(timeframe):
    assert completed([candle()], timeframe=timeframe) == []


@pytest.mark.parametrize("cutoff", [True, False, 0, -1, "bad", float("nan"), float("inf"), NOW * 1000])
def test_invalid_cutoff_fails_closed(cutoff):
    assert completed([candle()], now=cutoff) == []


@pytest.mark.parametrize("flag", ["is_closed", "complete", "completed", "final", "is_final", "confirm"])
@pytest.mark.parametrize("value", [False, "false", 0])
def test_explicit_incomplete_flags_are_preserved_until_filtering(flag, value):
    assert completed([candle(**{flag: value})]) == []


@pytest.mark.parametrize("flag", ["partial_source_bar", "partial", "is_partial"])
def test_partial_source_flags_block_completion(flag):
    assert completed([candle(**{flag: True})]) == []
    assert completed([candle(**{flag: False})]) == [candle()]


def test_closed_true_never_overrides_future_bar_clock_or_explicit_close():
    assert completed([candle(NOW, is_closed=True)]) == []
    assert completed([candle(is_closed=True, close_time=NOW + 1)]) == []
    assert completed([candle(is_closed=True, close_time=NOW)]) == [candle()]
    # Inclusive exchange close timestamps can be one millisecond before the
    # interval boundary; they must not make the bar eligible early.
    assert completed([candle(T=(NOW * 1000) - 1)], now=NOW - 0.0005) == []
    assert completed([candle(T=(NOW * 1000) - 1)]) == [candle()]


def test_conflicting_timestamp_aliases_cannot_choose_a_convenient_clock():
    assert completed([candle(t=(NOW - 600) * 1000)]) == []
    assert completed([candle(t=(NOW - 300) * 1000)]) == [candle()]


@pytest.mark.parametrize("field,value", [
    ("open", 98), ("close", 200), ("high", 98), ("low", 103),
    ("open", 0), ("low", -1), ("volume", -1), ("volume", True),
    ("close", float("nan")), ("high", float("inf")), ("open", "bad"),
    ("close", False), ("volume", None),
])
def test_impossible_or_nonfinite_ohlcv_never_enters_explosion(field, value):
    assert completed([candle(**{field: value})]) == []


@pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
def test_missing_ohlcv_is_not_fabricated_from_close_or_zero(field):
    row = candle()
    row.pop(field)
    assert completed([row]) == []


def test_sparse_close_only_temporal_adapter_remains_sparse_not_executable():
    rows = [{"t": 900, "close": 30}, {"t": 600, "close": 20}, {"t": 300, "close": 10}]
    result = api._completed_candles_only(rows, "5m", now_ts=1000)
    assert [row["close"] for row in result] == [10, 20]
    assert all("open" not in row and "volume" not in row for row in result)
    assert completed(rows, now=1000) == []


def test_zero_volume_and_micro_prices_remain_actual_valid_measurements():
    row = candle(open=1e-9, high=1.2e-9, low=.9e-9, close=1.1e-9, volume=0)
    assert completed([row]) == [row]


def test_exact_duplicates_and_equivalent_provider_aliases_collapse_once():
    row = candle()
    polygon = {"t": row["timestamp"] * 1000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 1000}
    for rows in permutations([row, dict(row), polygon]):
        assert completed(list(rows)) == [row]


@pytest.mark.parametrize("conflict", [
    {"close": 100}, {"volume": 1001}, {"close": 200}, {"volume": None}, {"is_closed": False},
])
def test_conflicting_or_invalid_duplicate_poisons_the_timestamp(conflict):
    row = candle()
    bad = candle(**conflict)
    for rows in permutations([row, bad, dict(row)]):
        assert completed(list(rows)) == []


def test_duplicate_metadata_selection_is_deterministic_and_does_not_mutate_input():
    first, second = candle(source="z"), candle(source="a")
    forward = api._completed_candles_only([first, second], "5m", now_ts=NOW)
    reverse = api._completed_candles_only([second, first], "5m", now_ts=NOW)
    assert forward == reverse
    assert first["source"] == "z" and second["source"] == "a"


def test_explosion_result_is_invariant_to_future_htf_extremes_and_duplicates(monkeypatch):
    monkeypatch.setattr(api, "_get_crypto_btc_context", lambda symbol, change: _btc_context(change))
    bars5 = _bars(90, start=9.48, step=.004, volume=1000,
                  last={"open": 9.93, "high": 9.97, "low": 9.90, "close": 9.95, "volume": 1200})
    bars15 = _bars(60, start=9.42, step=.009, volume=3000, interval=900,
                   last={"open": 9.92, "high": 9.98, "low": 9.88, "close": 9.95, "volume": 3600})
    bars4h = _bars(60, start=9.4, step=.006, volume=5000, interval=14400)
    baseline = api._score_crypto_explosion_candidate(_candidate(), bars5, bars15, bars4h)
    assert baseline is not None and baseline["trade_signal"] == "EXPLOSION_ARMED"
    future = [candle(NOW + i * 900, open=10, high=100, low=9, close=10) for i in (1, 2, 3)]
    assert api._score_crypto_explosion_candidate(_candidate(), bars5, bars15 + future, bars4h) == baseline
    assert api._score_crypto_explosion_candidate(
        _candidate(), list(reversed(bars5 + bars5)), list(reversed(bars15 + bars15)), bars4h + bars4h
    ) == baseline


def test_stock_5m_adapter_retains_its_clock_through_breakout_confirmation(monkeypatch):
    rows = [
        {"t": (NOW - 900) * 1000, "o": 99, "h": 100, "l": 98, "c": 99.5, "v": 1000},
        {"t": (NOW - 600) * 1000, "o": 99.5, "h": 101, "l": 99, "c": 100.2, "v": 1100},
        {"t": (NOW - 300) * 1000, "o": 100.2, "h": 100.4, "l": 100, "c": 100.3, "v": 1200},
    ]

    class Response:
        status_code = 200

        def json(self):
            return {"results": list(reversed(rows + [rows[-1]]))}

    monkeypatch.setattr(api, "POLYGON_KEY", "offline-fixture")
    monkeypatch.setattr(api, "rate_limited_get", lambda *_args, **_kwargs: Response())
    cutoff = datetime.fromtimestamp(NOW, tz=timezone.utc)
    result = api._stock_breakout_freshness_state(
        {"ticker": "TEST", "direction": "LONG", "Breakout_Level": 100}, as_of=cutoff
    )
    assert result["Breakout_Freshness_Status"] == "FRESH_CROSS"
    assert result["Breakout_Confirmation_Close"] == 100.3
    assert result["Breakout_Confirmation_Age_Seconds"] == 0
    assert result["Breakout_Confirmation_Closed_At"] == cutoff.isoformat().replace("+00:00", "Z")
