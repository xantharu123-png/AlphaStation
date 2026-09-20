"""Existing BI scan state survives a cooperative pause; no provider or SMTP."""
import threading

import pytest

from modules import scanners
from modules import scan_control as control
from test_scan_control import clean_controls, register, start_worker, wait_state
from test_bi_deep_fixes_scan import _flat_bars, _patch_scan_io


def test_bi_parks_between_candidates_and_continues_without_duplicate_analysis(monkeypatch):
    saved = _patch_scan_io(monkeypatch, {"ONE": _flat_bars(), "TWO": _flat_bars()},
        analyze_result=(True, 90, 188, ["ok"], "high", "A", 3, 3))
    original_get = scanners.rate_limited_get
    calls = []
    def get(*args, **kwargs):
        calls.append(args[0])
        result = original_get(*args, **kwargs)
        if len(calls) == 1:
            control.request_pause("bi_long", "bi-run", auto_resume=False)
        return result
    monkeypatch.setattr(scanners, "rate_limited_get", get)
    monkeypatch.setattr(scanners, "_bi_request_stop", lambda *a: pytest.fail("parking must not use stop files"))
    register(key="bi_long", run="bi-run")
    worker, errors = start_worker(lambda: scanners._bi_background_scan("fixture", "long", ["ONE", "TWO"]),
                                  key="bi_long", run="bi-run")
    wait_state("paused", key="bi_long")
    assert worker.is_alive() and len(calls) == 1
    assert saved.get("meta", {}).get("partial") is not False
    control.request_resume("bi_long", "bi-run")
    worker.join(timeout=3)
    assert not worker.is_alive() and not errors
    assert len(calls) == 2 and saved["meta"]["partial"] is False
    assert {row["ticker"] for row in saved["results"]} == {"ONE", "TWO"}


def test_bi_changed_epoch_after_pause_never_publishes_complete_cache(monkeypatch):
    saved = _patch_scan_io(monkeypatch, {"ONE": _flat_bars(), "TWO": _flat_bars()},
        analyze_result=(True, 90, 188, ["ok"], "high", "A", 3, 3))
    token = ["old"]
    register(key="bi_long", run="bi-run", token=("old",), current=lambda: (token[0],))
    control.request_pause("bi_long", "bi-run", auto_resume=False)
    worker, errors = start_worker(lambda: scanners._bi_background_scan("fixture", "long", ["ONE", "TWO"]),
                                  key="bi_long", run="bi-run")
    wait_state("paused", key="bi_long")
    token[0] = "new"
    control.request_resume("bi_long", "bi-run")
    worker.join(timeout=3)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], control.ScanRestartRequired)
    assert not saved


def test_bi_universe_page_can_park_before_any_network_operation(monkeypatch):
    _patch_scan_io(monkeypatch, {})
    requests = []
    monkeypatch.setattr(scanners, "rate_limited_get", lambda *a, **kw: requests.append(a) or pytest.fail("no provider before resume"))
    token = ["old"]
    register(key="bi_short", run="bi-run", token=("old",), current=lambda: (token[0],))
    control.request_pause("bi_short", "bi-run", auto_resume=False)
    worker, errors = start_worker(lambda: scanners._bi_background_scan("fixture", "short", None),
                                  key="bi_short", run="bi-run")
    wait_state("paused", key="bi_short")
    assert worker.is_alive() and requests == []
    token[0] = "new"
    control.request_resume("bi_short", "bi-run")
    worker.join(timeout=3)
    assert not worker.is_alive() and len(errors) == 1
    assert isinstance(errors[0], control.ScanRestartRequired) and not requests
