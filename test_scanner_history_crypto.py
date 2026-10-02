"""Causality/plumbing controls for the measured crypto spot technical study."""
from datetime import datetime, timedelta, timezone
import json
from types import SimpleNamespace

import pytest

from scripts import scanner_history_crypto as history


def _bars(start, count):
    return [{"timestamp": start + 300 * index, "open": 100.0,
             "high": 101.0, "low": 99.0, "close": 100.0 + index / 10000,
             "volume": 10.0 + index} for index in range(count)]


def test_crypto_4h_aggregate_requires_exact_completed_native_bar_groups():
    start = 1782950400 // 14400 * 14400
    bars = _bars(start, 96)
    assert len(history.aggregate_completed_4h(bars)) == 2
    incomplete = history.aggregate_completed_4h(bars[:-1])
    assert len(incomplete) == 1
    assert incomplete[0]["close"] == bars[47]["close"]
    assert incomplete[0]["volume"] == sum(row["volume"] for row in bars[:48])
    gap = history.aggregate_completed_4h(bars[:20] + bars[21:])
    assert len(gap) == 1 and gap[0]["timestamp"] == start + 14400


@pytest.mark.parametrize("corruption", ["venue", "hash", "boolean", "clock", "conflict"])
def test_crypto_source_contract_never_repairs_corrupt_market_observations(tmp_path, corruption):
    start = 1782950400000
    rows = [{"t": start, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 10.0}]
    payload = {"symbol": "BTCUSDT", "venue": "binance_spot", "quote_currency": "USDT",
               "timeframe": "5m", "bars": rows, "bars_sha256": history.fingerprint(rows)}
    if corruption == "venue":
        payload["venue"] = "binance_perpetual"
    elif corruption == "hash":
        payload["bars_sha256"] = "incorrect"
    elif corruption == "boolean":
        rows[0]["c"] = True
        payload["bars_sha256"] = history.fingerprint(rows)
    elif corruption == "clock":
        rows[0]["t"] += 1
        payload["bars_sha256"] = history.fingerprint(rows)
    else:
        rows.append(dict(rows[0], c=100.5))
    path = tmp_path / "source.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        history.load_spot_source(path, "BTCUSDT")


def test_crypto_source_numeric_string_measurements_keep_canonical_fingerprint(tmp_path):
    rows = [{"t": 1782950400000.0, "o": 100.0, "h": 101.0, "l": 99.0, "c": 100.0, "v": 10.0}]
    source = {"symbol": "BTCUSDT", "venue": "binance_spot", "quote_currency": "USDT",
              "timeframe": "5m", "bars": [{key: str(value) for key, value in rows[0].items()}],
              "bars_sha256": history.fingerprint(rows)}
    path = tmp_path / "source.json"
    path.write_text(json.dumps(source), encoding="utf-8")
    bars, metadata = history.load_spot_source(path, "BTCUSDT")
    assert bars[0]["close"] == 100.0 and metadata["source_bar_count"] == 1


def test_crypto_hourly_study_never_passes_future_bars_and_spaces_all_events():
    start = datetime(2026, 7, 2, tzinfo=timezone.utc)
    end = start + timedelta(days=2)
    bars = _bars(int((start - timedelta(days=10)).timestamp()), 12 * 24 * 12)
    calls = []
    clock = SimpleNamespace(time=lambda: -1)

    def prefix_check(row, prefix, timeframe, profile=None):
        now = clock.time()
        interval = 300 if timeframe == "5m" else 14400
        assert all(item["timestamp"] + interval <= now for item in prefix)
        assert all(right["timestamp"] - left["timestamp"] == interval
                   for left, right in zip(prefix, prefix[1:]))
        assert row["Price"] == next(item["close"] for item in bars if item["timestamp"] + 300 == now)
        calls.append((now, timeframe))
        return {"ok": True, "pre_breakout_ok": True, "armed_ok": True,
                "matched": ["actual_prefix_spy"], "reason": "spy_valid"}

    api = SimpleNamespace(time=clock, _early_mover_trigger_profile=lambda row: {"source": "measured_return"},
                          _score_early_mover_trigger_bars=prefix_check,
                          _early_mover_htf_execution_context=prefix_check,
                          _early_mover_htf_armed_context=prefix_check)
    result = history.study_asset(api, "BTCUSDT", bars, start=start, end=end)
    assert result["scheduled_observations"] == 48
    assert result["skipped_observations"] == {}
    assert len(calls) == 48 * 3
    for family in result["families"].values():
        assert family["eligible_hourly_observations"] == 48
        assert family["spaced_event_count"] == 12
        assert family["first_three_examples"] == family["all_events"][:3]
        actual = [datetime.fromisoformat(event["observed_at"].replace("Z", "+00:00"))
                  for event in family["all_events"]]
        assert all(right - left == timedelta(hours=4) for left, right in zip(actual, actual[1:]))
        assert family["directional_outcomes"]["24"]["unresolved"] == 6


def test_crypto_gap_is_reported_as_missing_not_zero_signal():
    start = datetime(2026, 7, 2, tzinfo=timezone.utc)
    bars = _bars(int((start - timedelta(days=10)).timestamp()), 10 * 24 * 12 - 1)
    api = SimpleNamespace()
    result = history.study_asset(api, "BTCUSDT", bars, start=start, end=start + timedelta(hours=3))
    assert result["skipped_observations"]["missing_or_incomplete_5m_prefix"] == 3
    assert all(item["spaced_event_count"] == 0 for item in result["families"].values())


def test_crypto_outcomes_distinguish_missing_and_window_end():
    outcomes = history._forward_outcomes({3600: 102.0}, 0, 100.0, 20000)
    assert outcomes["1"]["gross_directional_return_pct"] == pytest.approx(2.0)
    assert outcomes["4"]["status"] == "unresolved_missing_close"
    assert outcomes["24"]["status"] == "unresolved_window_end"


def test_crypto_no_resolved_observations_are_not_reported_as_zero_percent():
    summary = history._summarize([])
    assert all(item["positive_gross_directional_pct"] is None for item in summary.values())
