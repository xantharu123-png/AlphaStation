"""Wyckoff adapters must preserve the canonical causal/readiness contract."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import sys
from types import ModuleType

import pytest

from modules import analysis, patterns


AS_OF = datetime(2026, 9, 20, 22, tzinfo=timezone.utc)


def _bars(count=80):
    start = AS_OF - timedelta(days=count + 1)
    return [
        {"time": int((start + timedelta(days=i)).timestamp()),
         "open_time": (start + timedelta(days=i)).isoformat(),
         "close_time": (start + timedelta(days=i, hours=6)).isoformat(),
         "open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0,
         "volume": 1000000.0}
        for i in range(count)
    ]


def _pattern(*, ready=False, direction="LONG", score=90):
    bars = _bars()
    return {
        "type": "Accumulation" if direction == "LONG" else "Distribution",
        "direction": direction, "phase": "D" if ready else "B",
        "variant": "no_spring", "score": score,
        "score_kind": "quality_not_probability", "trade_ready": ready,
        "signal_state": "confirmed" if ready else "context",
        "range_low": 98.1234567, "range_high": 102.7654321,
        "range_start_time": bars[20]["time"], "range_end_time": bars[-1]["time"],
        "range_confirmed_at": bars[26]["close_time"],
        "signal_confirmed_at": bars[-1]["close_time"] if ready else None,
        "latest_completed_at": bars[-1]["close_time"],
        "events": [{"name": "SC" if direction == "LONG" else "BC", "index": 20,
                    "time": bars[20]["time"], "observed_at": bars[20]["close_time"],
                    "confirmation_time": bars[21]["time"],
                    "confirmed_at": bars[21]["close_time"], "price": 98.1234567,
                    "volume_ratio": 2.0}],
        "trade": ({"entry": 103.0, "stop": 101.0, "tp1": 107.0,
                   "tp2": 110.0, "rr": 2.0, "direction": direction,
                   "target_basis": "measured_range_projection",
                   "fill_evidence_verified": False} if ready else None),
    }


@pytest.fixture
def engine(monkeypatch):
    calls = []
    response = {"model": "causal_wyckoff_v1", "status": "ok", "reason": "",
                "timeframe": "1D", "as_of": AS_OF.isoformat(), "bars_used": 80,
                "latest_completed_at": _bars()[-1]["close_time"], "patterns": []}
    module = ModuleType("modules.wyckoff")

    def analyze(bars, **kwargs):
        calls.append((deepcopy(bars), kwargs))
        return deepcopy(response)

    module.analyze_wyckoff = analyze
    monkeypatch.setitem(sys.modules, "modules.wyckoff", module)
    return calls, response


@pytest.mark.parametrize("kind", ["wyckoff_accumulation", "wyckoff_distribution"])
def test_multi_day_without_explicit_time_context_cannot_be_signal(kind):
    # The former additive score accepts both directions for the same flat tape.
    bars = [{"open": 100, "close": 100,
             "high": 102 if i < 15 else 100.5,
             "low": 98 if i < 15 else 99,
             "volume": 1000000 if i < 15 else 500000} for i in range(30)]
    valid, score, details = analysis.analyze_multi_day_pattern(bars, kind)
    assert valid is False
    assert score == 0
    assert details


@pytest.mark.parametrize("kind,direction", [("wyckoff_accumulation", "LONG"),
                                          ("wyckoff_distribution", "SHORT")])
@pytest.mark.parametrize("ready", [False, True])
def test_multi_day_uses_full_canonical_history_and_readiness(engine, kind, direction, ready):
    calls, result = engine
    result["patterns"] = [_pattern(ready=ready, direction=direction)]
    valid, score, details = analysis.analyze_multi_day_pattern(
        _bars(), kind, as_of=AS_OF, timeframe="1D")
    assert valid is ready
    assert score == 90
    assert len(calls) == 1 and len(calls[0][0]) == 80
    assert calls[0][1]["direction"] == direction
    assert calls[0][1]["as_of"] == AS_OF
    assert calls[0][1]["timeframe"] == "1D"
    assert any("keine" in str(item).lower() or "qualit" in str(item).lower()
               for item in details)


def test_multi_day_rejects_invalid_data_even_with_ready_pattern(engine):
    _, result = engine
    result.update(status="invalid_data", reason="invalid_volume", patterns=[_pattern(ready=True)])
    valid, score, details = analysis.analyze_multi_day_pattern(
        _bars(), "wyckoff_accumulation", as_of=AS_OF, timeframe="1D")
    assert (valid, score) == (False, 0)
    assert "invalid_volume" in str(details)


def test_chart_wrapper_requires_explicit_timeframe_and_cutoff(engine):
    calls, result = engine
    result["patterns"] = [_pattern()]
    assert analysis.find_wyckoff_for_chart(_bars()) == []
    assert calls == []


def test_chart_wrapper_preserves_context_without_inventing_trade(engine):
    _, result = engine
    result["patterns"] = [_pattern()]
    actual = analysis.find_wyckoff_for_chart(_bars(), as_of=AS_OF, timeframe="1D")
    assert len(actual) == 1
    assert actual[0]["trade_ready"] is False and actual[0]["trade"] is None
    assert actual[0]["model"] == "causal_wyckoff_v1"
    assert actual[0]["timeframe"] == "1D"
    assert actual[0]["events"] == result["patterns"][0]["events"]
    assert actual[0]["range_low"] == 98.1234567


@pytest.mark.parametrize("ready", [False, True])
def test_chart_detector_uses_context_bars_and_real_event_times(engine, ready):
    calls, result = engine
    result["patterns"] = [_pattern(ready=ready)]
    prepared = _bars()
    prepared[0]["close_time"] = "2026-07-01T20:00:00+00:00"
    actual = patterns.detect_chart_patterns(
        _bars(), lookback=50,
        wyckoff_context={"as_of": AS_OF, "timeframe": "1D", "bars": prepared})
    rows = [row for row in actual if row.get("model") == "causal_wyckoff_v1"]
    assert len(rows) == 1
    assert len(calls[0][0]) == 80
    assert calls[0][0][0]["close_time"] == prepared[0]["close_time"]
    row = rows[0]
    assert row["index_basis"] == "time"
    assert row["trade_ready"] is ready
    assert row["score_kind"] == "quality_not_probability"
    assert row["time"] == result["patterns"][0]["events"][-1]["confirmation_time"]
    assert "detect_index" not in row
    assert row["draw_points"][0]["time"] == result["patterns"][0]["events"][0]["time"]
    assert all("index" not in point for point in row["draw_points"])
    if ready:
        assert row["target"] == 107.0
        assert row["target_basis"] == "measured_range_projection"
    else:
        assert row.get("target") is None and row.get("trade") is None
        assert "kein" in row["description"].lower()


def test_chart_without_context_does_not_invoke_canonical_engine(engine):
    calls, _ = engine
    actual = patterns.detect_chart_patterns(_bars(), lookback=80)
    assert not any("Wyckoff" in row.get("pattern", "") for row in actual)
    assert calls == []


class _Response:
    def __init__(self, bars):
        self.bars = bars

    def json(self):
        return {"status": "OK", "results": self.bars}


def _provider_bars():
    from datetime import time as daytime
    from zoneinfo import ZoneInfo
    from modules.stock_swing_contract import session_close
    days = []
    current = AS_OF.date() - timedelta(days=1)
    while len(days) < 80:
        if session_close(current.isoformat()) is not None:
            days.append(current)
        current -= timedelta(days=1)
    eastern = ZoneInfo("America/New_York")
    return [{"t": int(datetime.combine(day, daytime(0), eastern).timestamp() * 1000),
             "o": row["open"], "h": row["high"],
             "l": row["low"], "c": row["close"], "v": row["volume"]}
            for row, day in zip(_bars(), reversed(days))]


@pytest.mark.parametrize("legacy_tf,canonical_tf", [("day", "1D"), ("hour", "4H"),
                                                   ("1hour", "1H")])
def test_standalone_delegates_validated_readiness_and_fixed_time(engine, monkeypatch, legacy_tf, canonical_tf):
    calls, result = engine
    result["patterns"] = [_pattern(ready=True)]
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response(_provider_bars()))
    actual = patterns.scan_wyckoff_single("TEST", "unused", timeframe=legacy_tf, as_of=AS_OF)
    assert actual["ticker"] == "TEST" and actual["trade_ready"] is True
    assert actual["entry"] == 103.0 and actual["tp1"] == 107.0
    assert actual["score_kind"] == "quality_not_probability"
    assert len(calls[0][0]) == 80
    assert calls[0][1]["timeframe"] == canonical_tf
    assert calls[0][1]["as_of"] == AS_OF
    if legacy_tf == "day":
        assert "close_time" in calls[0][0][-1]


def test_standalone_does_not_filter_bad_provider_rows_before_core_validation(engine, monkeypatch):
    calls, result = engine
    rows = _provider_bars()
    rows[30]["v"] = 0
    result.update(status="invalid_data", reason="invalid_volume")
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response(rows))
    assert patterns.scan_wyckoff_single("TEST", "unused", as_of=AS_OF) is None
    assert len(calls[0][0]) == 80 and calls[0][0][30]["v"] == 0


def test_standalone_context_is_not_a_scanner_trade(engine, monkeypatch):
    _, result = engine
    result["patterns"] = [_pattern(ready=False, score=99)]
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response(_provider_bars()))
    assert patterns.scan_wyckoff_single("TEST", "unused", as_of=AS_OF) is None


def test_batch_passes_direction_before_selecting_best_pattern(monkeypatch):
    calls = []

    def single(ticker, api_key, days, timeframe, **kwargs):
        calls.append(kwargs)
        return {"ticker": ticker, "direction": kwargs["direction"], "score": 80}

    monkeypatch.setattr(patterns, "scan_wyckoff_single", single)
    actual = patterns.scan_wyckoff_batch(["TEST"], "unused", direction="SHORT", as_of=AS_OF)
    assert len(actual) == 1 and actual[0]["direction"] == "SHORT"
    assert calls == [{"direction": "SHORT", "as_of": AS_OF}]


def test_ohlc_only_accumulation_helper_never_invents_volume_or_wyckoff_phase(monkeypatch):
    rows = [[row["time"] * 1000, row["open"], row["high"], row["low"], row["close"]]
            for row in _bars(20)]
    monkeypatch.setattr(analysis, "fetch_historical_data_stocks", lambda *a, **k: rows)
    actual = analysis.calculate_accumulation_score("TEST", "Aktien", "unused")
    assert actual["data_available"] is True
    assert actual["wyckoff_phase"] == "Unknown"
    assert actual["score"] is None and actual["obv_trend"] is None
    assert actual["volume_trend"] is None
    assert actual["trade_ready"] is False
    assert actual["analysis_status"] == "unavailable"
    assert "BUY" not in actual["interpretation"] and "Smart Money" not in actual["interpretation"]
    display, _ = analysis.get_accumulation_display("TEST", "Aktien", "unused")
    assert display["score_label"] == "NICHT BEWERTBAR"
    assert display["obv_text"] == "Nicht verfuegbar"


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
@pytest.mark.parametrize("timeframe,minutes", [("1D", 1440), ("4H", 240), ("15M", 15)])
def test_real_engine_scanner_chart_and_drawing_share_same_event_proof(direction, timeframe, minutes):
    from modules.wyckoff import analyze_wyckoff
    from test_wyckoff_engine import BASE, textbook_bars

    bars = textbook_bars(direction)
    for index, bar in enumerate(bars):
        bar.update(open_time=BASE + timedelta(minutes=minutes * index),
                   close_time=BASE + timedelta(minutes=minutes * (index + 1)),
                   time=int((BASE + timedelta(minutes=minutes * index)).timestamp()))
    cutoff = BASE + timedelta(minutes=minutes * len(bars))
    expected = analyze_wyckoff(bars, as_of=cutoff, timeframe=timeframe, direction=direction)
    proof = next(row for row in expected["patterns"] if row["direction"] == direction)
    assert proof["trade_ready"] is True
    pattern_type = "wyckoff_accumulation" if direction == "LONG" else "wyckoff_distribution"
    valid, score, _ = analysis.analyze_multi_day_pattern(bars, pattern_type, as_of=cutoff, timeframe=timeframe)
    assert valid is True and score == proof["score"]
    chart = analysis.find_wyckoff_for_chart(bars, as_of=cutoff, timeframe=timeframe)
    selected = next(row for row in chart if row["direction"] == direction)
    assert selected["events"] == proof["events"]
    assert selected["trade"] == proof["trade"]
    plotted = patterns.detect_chart_patterns(
        bars, lookback=50, wyckoff_context={"bars": bars, "as_of": cutoff, "timeframe": timeframe})
    projected = next(row for row in plotted if row.get("model") == "causal_wyckoff_v1"
                     and row["direction"] == direction)
    assert projected["event_evidence"] == proof["events"]
    assert projected["target"] == proof["trade"]["tp1"]
    assert projected["timeframe"] == timeframe
    assert [point["time"] for point in projected["draw_points"]] == [event["time"] for event in proof["events"]]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_daily_standalone_matches_core_with_new_york_session_closes(monkeypatch, direction):
    from datetime import time as daytime
    from zoneinfo import ZoneInfo
    from modules.wyckoff import analyze_wyckoff
    from modules.stock_swing_contract import session_close
    from test_wyckoff_engine import BASE, textbook_bars

    eastern = ZoneInfo("America/New_York")
    prepared = textbook_bars(direction)
    raw = []
    session = BASE.date()
    for bar in prepared:
        while session_close(session.isoformat()) is None:
            session += timedelta(days=1)
        opened = datetime.combine(session, daytime(0), eastern)
        closed = session_close(session.isoformat())
        bar.update(open_time=opened, close_time=closed)
        raw.append({"t": int(opened.timestamp() * 1000), "o": bar["open"],
                    "h": bar["high"], "l": bar["low"], "c": bar["close"], "v": bar["volume"]})
        session += timedelta(days=1)
    cutoff = prepared[-1]["close_time"].astimezone(timezone.utc)
    expected = analyze_wyckoff(prepared, as_of=cutoff, timeframe="1D", direction=direction)
    proof = next(row for row in expected["patterns"] if row["direction"] == direction)
    assert proof["trade_ready"] is True
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response(raw))
    actual = patterns.scan_wyckoff_single(
        "TEST", "unused", timeframe="day", direction=direction, as_of=cutoff)
    assert actual is not None
    assert actual["events"] == proof["events"]
    assert actual["trade"] == proof["trade"]
    assert actual["latest_completed_at"] == expected["latest_completed_at"]


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_real_phase_b_is_chart_context_but_not_scanner_signal(direction):
    from test_wyckoff_engine import BASE, textbook_bars

    bars = textbook_bars(direction, spring=False)[:70]
    cutoff = BASE + timedelta(days=70)
    pattern_type = "wyckoff_accumulation" if direction == "LONG" else "wyckoff_distribution"
    valid, score, _ = analysis.analyze_multi_day_pattern(bars, pattern_type, as_of=cutoff, timeframe="1D")
    assert valid is False and score > 0
    chart = analysis.find_wyckoff_for_chart(bars, as_of=cutoff, timeframe="1D")
    selected = next(row for row in chart if row["direction"] == direction)
    assert selected["phase"] == "B" and selected["trade"] is None


def test_real_chart_uses_canonical_atr_on_ohlcv_dictionaries(monkeypatch):
    import modules.wyckoff as core
    from test_wyckoff_engine import BASE, textbook_bars

    seen = []
    original = core.calculate_atr_14

    def observed(bars):
        seen.append(deepcopy(bars))
        return original(bars)

    monkeypatch.setattr(core, "calculate_atr_14", observed)
    rows = analysis.find_wyckoff_for_chart(
        textbook_bars(), as_of=BASE + timedelta(days=100), timeframe="1D")
    assert rows and seen
    assert all(isinstance(bar, dict) and {"open", "high", "low", "close", "volume"} <= set(bar)
               for bar in seen[0])


def test_standalone_daily_uses_exchange_early_close(engine, monkeypatch):
    calls, result = engine
    result["patterns"] = [_pattern(ready=True)]
    # Thanksgiving Friday closes at 13:00 EST, not the normal 16:00.
    raw = {"t": int(datetime(2026, 11, 27, 5, tzinfo=timezone.utc).timestamp() * 1000),
           "o": 100., "h": 102., "l": 98., "c": 100., "v": 100000.}
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response([raw]))
    cutoff = datetime(2026, 11, 27, 18, 30, tzinfo=timezone.utc)
    assert patterns.scan_wyckoff_single("TEST", "unused", timeframe="day", as_of=cutoff)
    actual_close = datetime.fromisoformat(calls[0][0][0]["close_time"])
    assert actual_close == datetime(2026, 11, 27, 18, tzinfo=timezone.utc)


def test_standalone_daily_rejects_unknown_exchange_session(engine, monkeypatch):
    calls, result = engine
    result["patterns"] = [_pattern(ready=True)]
    raw = {"t": int(datetime(2026, 9, 20, 4, tzinfo=timezone.utc).timestamp() * 1000),
           "o": 100., "h": 102., "l": 98., "c": 100., "v": 100000.}
    monkeypatch.setattr(patterns, "rate_limited_get", lambda *a, **k: _Response([raw]))
    assert patterns.scan_wyckoff_single("TEST", "unused", timeframe="day", as_of=AS_OF) is None
    assert calls == []
