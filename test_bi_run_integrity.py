"""Offline run integrity: isolated bad series, one clock, aggregate-only causes."""
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from modules.bi_market_data import BIAggregateDataError, parse_bi_daily_aggregates
from test_bi_market_data import _lifecycle
from test_bi_diagnostics_integration import _result
from test_bi_deep_fixes_scan import _flat_bars, _to_polygon


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("field,value", [("o", 0), ("v", -1), ("c", None), ("t", 0)])
def test_bad_series_does_not_prevent_other_symbols_but_cannot_publish_final(
    monkeypatch, tmp_path, direction, field, value
):
    valid = {"results": _to_polygon(_flat_bars())}
    invalid = {"results": [dict(valid["results"][0], **{field: value})]}
    scanners, tickers, final, before, calls, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), direction, [valid, invalid, valid]
    )
    with pytest.raises(scanners.ScannerDataError, match="scan_data_incomplete"):
        scanners._bi_background_scan("fixture", direction, tickers)
    progress = json.loads((tmp_path / (direction + "-progress.json")).read_text())
    d = progress["diagnostics"]
    assert len(calls) == d["checked"] == d["total"] == 3
    assert len(analyses) == d["analyzed"] == d["indicator_passed"] == 2
    assert d["quarantined_symbols"] == d["data_failures"] == 1
    assert d["data_error_fields"] == {field: 1}
    assert d["analysis_errors"] == 0 and progress["no_data"] == 0
    assert d["coverage"] == "incomplete" and d["final_results"] is None
    assert final.read_bytes() == before
    # Any intermediate valid rows remain explicitly partial, never a fresh
    # final. The API wrapper deletes this partial on error and sends no mail.
    partial = json.loads((tmp_path / (direction + ".json.partial")).read_text())
    assert partial["partial"] is True
    assert all(r["BI_IndicatorsGreen"] >= 17 for r in partial["results"])


@pytest.mark.parametrize("payload", [{"status": "NOT_AUTHORIZED"}, {"status": "RATE_LIMITED"}, {"results": "bad"}])
def test_systemic_errors_still_abort_immediately(monkeypatch, tmp_path, payload):
    valid = {"results": _to_polygon(_flat_bars())}
    scanners, tickers, final, before, calls, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [payload, valid]
    )
    with pytest.raises(scanners.ScannerDataError):
        scanners._bi_background_scan("fixture", "long", tickers)
    assert len(calls) == 1 and not analyses
    assert final.read_bytes() == before


def test_error_metadata_never_retains_provider_values():
    error = BIAggregateDataError("invalid_bar_value", field="PRIVATE@EXAMPLE.COM")
    assert error.field == "unknown"
    bar = _to_polygon(_flat_bars())[0]
    with pytest.raises(BIAggregateDataError) as caught:
        parse_bi_daily_aggregates({"results": [dict(bar, c="PRIVATE_API_KEY")]})
    assert caught.value.field == "c"
    assert "PRIVATE" not in repr(vars(caught.value))


@pytest.mark.parametrize("short_history", [False, True])
def test_future_bar_cannot_supply_live_price_or_indicators(monkeypatch, tmp_path, short_history):
    valid = {"results": _to_polygon(_flat_bars())}
    future = {"results": [dict(bar) for bar in valid["results"]]}
    future["results"][-1]["t"] = int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp() * 1000)
    if short_history:
        future["results"] = future["results"][-1:]
    scanners, tickers, final, before, calls, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [future, valid]
    )
    with pytest.raises(scanners.ScannerDataError, match="scan_data_incomplete") as caught:
        scanners._bi_background_scan("fixture", "long", tickers)
    d = caught.value.diagnostics
    assert len(calls) == 2 and len(analyses) == 1
    assert d["data_error_counts"] == {"invalid_bar_timestamp": 1}
    assert d["data_error_fields"] == {"t": 1}
    assert final.read_bytes() == before


@pytest.mark.parametrize("kind", ["Timeout", "ConnectionError", "SSLError", "InvalidURL", "InvalidHeader"])
def test_network_failure_is_not_a_quarantined_bar(monkeypatch, tmp_path, kind):
    import requests
    valid = {"results": _to_polygon(_flat_bars())}
    scanners, tickers, final, before, _, analyses, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [valid, valid]
    )
    calls = []

    def broken(*args, **kwargs):
        calls.append(1)
        raise getattr(requests.exceptions, kind)("PRIVATE_TOKEN")

    monkeypatch.setattr(scanners, "rate_limited_get", broken)
    with pytest.raises(scanners.ScannerDataError, match="scan_data_unavailable") as caught:
        scanners._bi_background_scan("fixture", "long", tickers)
    assert caught.value.diagnostics["quarantined_symbols"] == 0
    assert len(calls) == 1 and not analyses
    assert final.read_bytes() == before
    assert "PRIVATE" not in json.dumps(caught.value.diagnostics)


@pytest.mark.parametrize("day,close_utc", [("2026-09-09", 20), ("2026-01-09", 21)])
def test_fixed_analysis_cutoff_handles_dst_and_future_sessions(day, close_utc):
    import modules.scanners as scanners
    date = datetime.fromisoformat(day)
    bars = [{"date": (date + timedelta(days=n)).date().isoformat()} for n in (-1, 0, 1)]
    close = date.replace(hour=close_utc, tzinfo=timezone.utc)
    assert scanners._bi_strip_partial_bar(bars, as_of=close - timedelta(seconds=1)) == bars[:1]
    assert scanners._bi_strip_partial_bar(bars, as_of=close) == bars[:2]
    assert scanners._bi_strip_partial_bar(bars, as_of=close.astimezone(ZoneInfo("Europe/Zurich"))) == bars[:2]
    with pytest.raises(ValueError, match="timezone-aware"):
        scanners._bi_strip_partial_bar(bars, as_of=close.replace(tzinfo=None))


def test_scan_crossing_close_has_one_analysis_clock_and_session(monkeypatch, tmp_path):
    import modules.scanners as scanners
    cutoff = datetime(2026, 9, 9, 19, 41, tzinfo=timezone.utc)
    clock_reads = []

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            clock_reads.append(1)
            value = cutoff if len(clock_reads) == 1 else cutoff + timedelta(hours=2)
            return value.astimezone(tz) if tz else value.replace(tzinfo=None)

    raw = _to_polygon(_flat_bars(n=51))
    eastern = ZoneInfo("America/New_York")
    for i, bar in enumerate(raw):
        day = datetime(2026, 9, 9, tzinfo=eastern) - timedelta(days=50 - i)
        bar["t"] = int(day.timestamp() * 1000)
    scanners, tickers, final, _, _, _, _ = _lifecycle(
        monkeypatch, tmp_path, _result(17), "long", [{"results": raw}, {"results": raw}]
    )
    monkeypatch.setattr(scanners, "datetime", Clock)
    observed = []
    plan_calls = []
    real_plan = scanners.build_bi_trade_plan
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda bars, **kw: observed.append(bars) or _result(17))

    def plan(*args, **kwargs):
        plan_calls.append(kwargs["as_of"])
        return real_plan(*args, **kwargs)

    monkeypatch.setattr(scanners, "build_bi_trade_plan", plan)
    scanners._bi_background_scan("fixture", "long", tickers)
    assert len(observed) == 2 and all(len(bars) == 50 for bars in observed)
    assert all(bars[-1]["date"] == "2026-09-08" for bars in observed)
    assert plan_calls == [cutoff, cutoff]
    d = json.loads(final.read_text())["diagnostics"]
    assert d["run_as_of"] == d["confluence"]["started_at"] == cutoff.isoformat()
    assert d["analysis_session_dates"] == {"2026-09-08": 2}


def test_joint_failures_exclude_unavailable_and_contain_no_watchlist():
    from test_bi_diagnostics import _diagnostics, _result as diagnostic_result, _observe
    d = _diagnostics()
    result = diagnostic_result(16, unavailable=(20,))
    result.consolidation_days = 6
    _observe(d, result)
    assert len(d["failed_pair_counts"]) == 190
    assert {key for key, n in d["failed_pair_counts"].items() if n} == {"17:18", "17:19", "18:19"}
    assert sum(d["failed_pair_counts"].values()) == 3
    assert d["consolidation_days_histogram"]["6"] == 1
    assert "PRIVATE" not in json.dumps(d)
