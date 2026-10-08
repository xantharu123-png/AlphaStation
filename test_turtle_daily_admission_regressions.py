"""Real Turtle daily admission against synthetic HTTP and isolated cache state.

Breaks caught: a legitimate empty symbol must not abort its valid sibling;
OHLCV outside the completed-session cutoff must not poison yesterday's plan;
that cutoff must never conceal broken envelope or timestamp chronology.
No plan builder, scoring, calendar or data validator is replaced.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
from zoneinfo import ZoneInfo

import pytest

import api
from test_stock_audit_repair_contracts import daily_bars, freeze, Response


NOW = datetime(2026, 9, 30, 14, 0, tzinfo=timezone.utc)
NY = ZoneInfo("America/New_York")


def _real_producer_fixture(monkeypatch, tmp_path, *, payloads=None, now=NOW):
    """Only the external provider/reference boundary and clock are synthetic."""
    freeze(monkeypatch, now)
    bars = daily_bars(30, session="2026-09-29")
    bars[-1].update(o=99.8, h=100.12, l=99.8, c=100.1, v=3_000_000.)
    default = {"status": "OK", "adjusted": True, "queryCount": 30,
               "resultsCount": 30, "results": deepcopy(bars)}
    payloads = {"GOOD": default} if payloads is None else payloads(deepcopy(default))
    symbols = tuple(payloads)
    cached = tmp_path / "turtle-cache.json"
    monkeypatch.setattr(api, "TURTLE_CACHE", str(cached))
    api.save_cache_file(str(cached), [{"marker": "previous_good_cache"}])

    def provider(url, **kwargs):
        if "/aggs/grouped/" in url:
            session = url.rsplit("/", 1)[-1]
            matches = [bar for bar in bars if datetime.fromtimestamp(
                bar["t"] / 1000, NY).date().isoformat() == session]
            return Response({"status": "OK", "adjusted": True,
                "results": [dict(bar, T=symbol) for symbol in symbols for bar in matches]})
        if "/aggs/ticker/" in url:
            symbol = url.split("/aggs/ticker/", 1)[1].split("/", 1)[0]
            payload = payloads[symbol]
            if isinstance(payload, tuple):
                status, body = payload
                return Response(deepcopy(body), status)
            return Response(deepcopy(payload))
        if url.endswith("/tickers"):
            return Response({"status": "OK", "tickers": [{"ticker": symbol,
                "day": {"o": 100.1, "h": 100.2, "l": 100., "c": 100.15,
                        "v": 100_000.},
                "prevDay": {"o": 99.8, "h": 100.12, "l": 99.8,
                            "c": 100.1, "v": 3_000_000.},
                "lastTrade": {"p": 100.15}} for symbol in symbols]})
        if url.endswith(("/gainers", "/losers")):
            return Response({"status": "OK", "tickers": []})
        raise AssertionError("Unexpected provider endpoint in isolated Turtle fixture")

    monkeypatch.setattr(api, "rate_limited_get", provider)
    monkeypatch.setattr(api, "_load_common_stock_universe",
                        lambda **kwargs: (set(symbols), "verified_offline_reference"))
    return cached


def _rows(cached):
    return json.loads(cached.read_text(encoding="utf-8"))["results"]


def _assert_daily_plan(rows):
    assert [row["Ticker"] for row in rows] == ["GOOD"]
    row = rows[0]
    # Hand-checked 29 prior highs at100 and signal close100.1: native long
    # Donchian entry100. No absent or forming price may replace that reference.
    assert row["swing_analysis_session"] == "2026-09-29"
    assert row["swing_reference_close"] == row["Preis"] == 100.1
    assert row["DC_High_20"] == row["Entry"] == 100.
    assert row["StopLoss"] < row["Entry"] < row["TP1"] < row["TP2"]
    assert row["Trade_Setup_Source"] == "turtle_r_multiple"
    assert row["fill_evidence_verified"] is False


@pytest.mark.parametrize("empty", [
    {"status": "OK", "adjusted": True, "resultsCount": 0, "queryCount": 0},
    {"status": "DELAYED", "adjusted": True, "resultsCount": 0,
     "queryCount": 0, "results": []},
], ids=["optional-results-array-absent", "explicit-empty-array"])
def test_successful_empty_turtle_history_excludes_symbol_not_valid_sibling(monkeypatch, tmp_path, empty):
    cached = _real_producer_fixture(monkeypatch, tmp_path,
        payloads=lambda good: {"GOOD": good, "EMPTY": deepcopy(empty)})
    api._turtle_scan_wrapper()
    _assert_daily_plan(_rows(cached))


@pytest.mark.parametrize("bad_values", [
    {"o": 0.}, {"c": None}, {"v": -1.}, {"h": 90.},
])
@pytest.mark.parametrize("hour,minute", [(14, 0), (20, 14)],
                         ids=["forming-daily-session", "closed-but-provider-delay-not-elapsed"])
def test_unavailable_daily_ohlcv_cannot_poison_completed_turtle_reference(monkeypatch, tmp_path, bad_values, hour, minute):
    now = NOW.replace(hour=hour, minute=minute)

    def future_only(good):
        later = {"t": int(datetime(2026, 9, 30, tzinfo=NY).timestamp() * 1000),
                 "o": 101., "h": 102., "l": 100., "c": 101., "v": 100_000.}
        later.update(bad_values)
        good["results"].append(later)
        good["resultsCount"] = good["queryCount"] = 31
        return {"GOOD": good}

    cached = _real_producer_fixture(monkeypatch, tmp_path, payloads=future_only, now=now)
    api._turtle_scan_wrapper()
    _assert_daily_plan(_rows(cached))


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_turtle_provider_failures_still_preserve_entire_last_good_cache(monkeypatch, tmp_path, status):
    cached = _real_producer_fixture(monkeypatch, tmp_path,
        payloads=lambda good: {"GOOD": good, "BLOCKED": (status, {"status": "ERROR"})})
    with pytest.raises(api.ScannerDataError):
        api._turtle_scan_wrapper()
    assert _rows(cached) == [{"marker": "previous_good_cache"}]


@pytest.mark.parametrize("bad_closed", [{"o": 0.}, {"h": 90.}, {"c": None}])
def test_malformed_required_completed_bar_still_preserves_last_good_cache(monkeypatch, tmp_path, bad_closed):
    def required_history(good):
        good["results"][-1].update(bad_closed)
        return {"GOOD": good}

    cached = _real_producer_fixture(monkeypatch, tmp_path, payloads=required_history)
    with pytest.raises(api.ScannerDataError):
        api._turtle_scan_wrapper()
    assert _rows(cached) == [{"marker": "previous_good_cache"}]


@pytest.mark.parametrize("fault", ["future-timestamp", "duplicate-timestamp", "missing-results", "count-mismatch"])
def test_completed_cutoff_must_not_hide_response_or_timestamp_failure(monkeypatch, tmp_path, fault):
    def corrupt(good):
        if fault == "future-timestamp":
            good["results"].append({"t": int(NOW.timestamp() * 1000) + 1,
                "o": 101., "h": 102., "l": 100., "c": 101., "v": 100_000.})
            good["resultsCount"] = good["queryCount"] = 31
        elif fault == "duplicate-timestamp":
            good["results"].append(deepcopy(good["results"][-1]))
            good["resultsCount"] = good["queryCount"] = 31
        elif fault == "missing-results":
            good.pop("results")
        else:
            good["resultsCount"] = 29
        return {"GOOD": good}

    cached = _real_producer_fixture(monkeypatch, tmp_path, payloads=corrupt)
    with pytest.raises(api.ScannerDataError):
        api._turtle_scan_wrapper()
    assert _rows(cached) == [{"marker": "previous_good_cache"}]
