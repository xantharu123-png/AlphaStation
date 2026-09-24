"""Approved symbol-local exclusions and bounded, non-imputing history repair."""
import copy
import json

import pytest
from requests.exceptions import Timeout

from modules import scanners
from modules.bi_market_data import BIAggregateDataError, bi_symbol_local_price_error
from test_bi_diagnostics_integration import _result
from test_bi_transport_recovery import Reply, setup, valid, progress
from test_bi_deep_fixes_scan import _flat_bars, _to_polygon
from modules import scan_control as control
from test_scan_control import clean_controls, register, start_worker, wait_state


def bad(field="o", value=0):
    reply = valid()
    reply.payload["results"][0][field] = value
    return reply


@pytest.mark.parametrize("direction", ["long", "short"])
@pytest.mark.parametrize("green,hard,count", [(16, (), 0), (17, (), 1), (17, ("range_breakdown",), 0)])
def test_same_query_retry_recovers_without_changing_data_or_signal_contract(monkeypatch, tmp_path, direction, green, hard, count):
    broken, restored = bad(), valid()
    original = copy.deepcopy(broken.payload)
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [broken, restored], direction=direction)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **k: _result(green, hard))
    scanners._bi_background_scan("fixture", direction, tickers)
    cache = json.loads(final.read_text())
    d = cache["diagnostics"]
    assert calls[0] == calls[1] and len(calls) == 2
    assert broken.payload == original
    assert d["coverage"] == "complete" and cache["count"] == count
    assert d["checked"] == d["total"] == d["analyzed"] == d["valid_data_symbols"] == 1
    assert d["data_retry_attempts"] == d["data_retry_recovered"] == d["transport_retries"] == 1
    assert d["data_retry_failed"] == d["data_failures"] == d["excluded_data_symbols"] == 0


@pytest.mark.parametrize("field,value", [("o", 0), ("h", None), ("l", True), ("c", "PRIVATE_TOKEN"), ("v", -1), ("h", 1)])
def test_persistent_bad_series_never_enters_analysis_or_rows_other_symbol_can_finish(monkeypatch, tmp_path, field, value):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [bad(field, value), bad(field, value), valid()], count=2)
    analyzed = []
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda bars, **k: analyzed.append(bars) or _result(17))
    scanners._bi_background_scan("fixture", "long", tickers)
    cache = json.loads(final.read_text())
    d = cache["diagnostics"]
    assert len(calls) == 3 and calls[0] == calls[1] and calls[1] != calls[2]
    assert len(analyzed) == 1
    assert d["coverage"] == "complete_with_exclusions"
    assert d["checked"] == d["total"] == 2
    assert d["excluded_data_symbols"] == d["data_failures"] == d["quarantined_symbols"] == 1
    assert d["data_retry_attempts"] == d["data_retry_failed"] == 1 and d["data_retry_recovered"] == 0
    assert d["valid_data_symbols"] == 1 and cache["partial"] is False
    assert [r["Ticker"] for r in cache["results"]] == [tickers[1]]
    assert cache["results"][0]["BI_IndicatorsGreen"] >= 17
    assert progress(tmp_path)["status"] == "done"
    assert "PRIVATE" not in json.dumps(d)


@pytest.mark.parametrize("replacement", ["empty", "shortened", "changed_timestamp"])
def test_retry_cannot_hide_bad_observation_by_losing_or_relabeling_history(monkeypatch, tmp_path, replacement):
    retry = valid()
    if replacement == "empty":
        retry.payload = {"status": "OK", "resultsCount": 0}
    else:
        if replacement == "shortened":
            retry.payload["results"] = retry.payload["results"][1:]
        else:
            retry.payload["results"][0]["t"] += 1000
        retry.payload["resultsCount"] = retry.payload["queryCount"] = len(retry.payload["results"])
    tickers, final, _, _, _ = setup(monkeypatch, tmp_path, [bad(), retry])
    scanners._bi_background_scan("fixture", "long", tickers)
    cache = json.loads(final.read_text())
    d = cache["diagnostics"]
    assert d["data_retry_observation_mismatches"] == d["excluded_data_symbols"] == 1
    assert d["data_retry_recovered"] == d["analyzed"] == d["valid_data_symbols"] == 0
    assert d["coverage"] == "complete_with_exclusions" and cache["results"] == []


@pytest.mark.parametrize("retry", [Reply(401), Reply(429), Reply(payload={"status": "OK"}), Reply(payload={"results": "PRIVATE"})])
def test_retry_global_failure_is_not_converted_to_allowed_symbol_exclusion(monkeypatch, tmp_path, retry):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [bad(), retry])
    with pytest.raises(scanners.ScannerDataError):
        scanners._bi_background_scan("fixture", "long", tickers)
    assert final.read_text() == "previous-final" and len(calls) == 2
    d = progress(tmp_path)["diagnostics"]
    assert d["coverage"] == "incomplete" and d["excluded_data_symbols"] == 0
    assert d["data_retry_failed"] == 1 and "PRIVATE" not in json.dumps(d)


def test_global_retry_budget_bounds_data_retries_for_large_bad_universe(monkeypatch, tmp_path):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [bad() for _ in range(41)], count=21)
    scanners._bi_background_scan("fixture", "long", tickers)
    d = json.loads(final.read_text())["diagnostics"]
    assert len(calls) == 41 and d["checked"] == d["total"] == d["excluded_data_symbols"] == 21
    assert d["transport_retries"] == d["data_retry_attempts"] == d["data_retry_failed"] == 20
    assert d["data_retry_budget_exhausted"] == 1
    assert d["coverage"] == "complete_with_exclusions" and d["valid_data_symbols"] == 0


def test_data_retries_share_budget_with_transport_recovery(monkeypatch, tmp_path):
    replies = [reply for _ in range(10) for reply in (Timeout("PRIVATE"), bad(), bad())] + [bad()]
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, replies, count=11)
    scanners._bi_background_scan("fixture", "long", tickers)
    d = json.loads(final.read_text())["diagnostics"]
    assert len(calls) == 31 and d["transport_retries"] == 20
    assert d["data_retry_attempts"] == d["data_retry_failed"] == 10
    assert d["data_retry_budget_exhausted"] == 1 and d["excluded_data_symbols"] == 11


@pytest.mark.parametrize("history_size", [50, 90, 220, 230])
def test_old_first_bar_is_not_silently_truncated_at_any_consumer_boundary(monkeypatch, tmp_path, history_size):
    bars = _to_polygon(_flat_bars(n=history_size))
    bars[0]["o"] = 0
    malformed = Reply(payload={"results": bars})
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [malformed, malformed], direction="short")
    scanners._bi_background_scan("fixture", "short", tickers)
    d = json.loads(final.read_text())["diagnostics"]
    assert d["excluded_data_symbols"] == 1 and d["valid_data_symbols"] == d["analyzed"] == 0
    assert d["data_error_positions"] == {"first": 1}
    assert len(bars) == history_size and bars[0]["o"] == 0
    assert len(calls) == 2 and calls[0] == calls[1]


def test_stop_before_data_retry_preserves_prior_final(monkeypatch, tmp_path):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [bad(), valid()])
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: bool(calls))
    scanners._bi_background_scan("fixture", "long", tickers)
    assert final.read_text() == "previous-final" and len(calls) == 1
    p = progress(tmp_path)
    assert p["status"] == "stopped" and p["diagnostics"]["data_retry_attempts"] == 0
    assert p["diagnostics"]["coverage"] == "incomplete"


@pytest.mark.parametrize("reason,field", [("invalid_bar_timestamp", "t"), ("invalid_bar_value", "t"), ("invalid_bar_type", "bar"), ("invalid_data_conversion", "unknown"), ("invalid_payload", "o")])
def test_unknown_or_nonprice_faults_are_not_covered_by_exclusion_approval(reason, field):
    assert not bi_symbol_local_price_error(BIAggregateDataError(reason, field=field))


def test_unknown_failure_after_exclusion_still_preserves_prior_final(monkeypatch, tmp_path):
    tickers, final, _, _, _ = setup(monkeypatch, tmp_path, [bad(), bad(), valid()], count=2)
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **k: (False,))
    with pytest.raises(scanners.ScannerDataError, match="scan_data_incomplete"):
        scanners._bi_background_scan("fixture", "long", tickers)
    d = progress(tmp_path)["diagnostics"]
    assert d["excluded_data_symbols"] == d["analysis_errors"] == 1
    assert d["coverage"] == "incomplete" and final.read_text() == "previous-final"


@pytest.mark.parametrize("epoch_changed", [False, True])
def test_pause_before_retry_keeps_same_owner_and_clock_or_requires_fresh_restart(monkeypatch, tmp_path, epoch_changed):
    tickers, final, calls, _, _ = setup(monkeypatch, tmp_path, [bad(), valid()])
    original_get = scanners.rate_limited_get
    token = ["same"]
    register(key="bi_long", run="retry-run", token=("same",), current=lambda: (token[0],))
    def get(*args, **kwargs):
        result = original_get(*args, **kwargs)
        if len(calls) == 1:
            control.request_pause("bi_long", "retry-run", auto_resume=False)
        return result
    monkeypatch.setattr(scanners, "rate_limited_get", get)
    worker, errors = start_worker(lambda: scanners._bi_background_scan("fixture", "long", tickers),
                                  key="bi_long", run="retry-run")
    wait_state("paused", key="bi_long")
    assert worker.is_alive() and len(calls) == 1 and final.read_text() == "previous-final"
    if epoch_changed:
        token[0] = "new-session"
    control.request_resume("bi_long", "retry-run")
    worker.join(timeout=3)
    assert not worker.is_alive()
    if epoch_changed:
        assert len(errors) == 1 and isinstance(errors[0], control.ScanRestartRequired)
        assert len(calls) == 1 and final.read_text() == "previous-final"
    else:
        assert errors == [] and len(calls) == 2 and calls[0] == calls[1]
        d = json.loads(final.read_text())["diagnostics"]
        assert d["data_retry_recovered"] == 1 and d["coverage"] == "complete"
