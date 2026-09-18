"""A slow first strategy must not consume the following strategies' shares."""
from types import SimpleNamespace
import pytest
import api
from modules import stock_scan_runtime as runtime
from modules.scanners import ScannerDataError
from test_stock_strategy_sweep_isolation import _mock_sweep, _row, STRATEGIES, CODES


def test_slow_first_leaf_leaves_work_budget_for_every_sibling(monkeypatch, tmp_path):
    clock = {"now": 100.}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    monkeypatch.setattr(runtime, "LEAF_WORK_SECONDS", 20.)
    monkeypatch.setattr(runtime, "SWEEP_WORK_SECONDS", 30.)
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", list(STRATEGIES))
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    allowed = []
    @runtime.bounded_leaf
    def scan(name, **kwargs):
        budget = runtime.current()["deadline"] - clock["now"]
        allowed.append(budget)
        # Each of the first two is slow enough to consume its fair share.
        if name in STRATEGIES[:2]:
            clock["now"] += budget
            runtime.checkpoint()
        clock["now"] += 2.
        runtime.checkpoint()
        return [_row("SYNTHETIC", score=90)]
    monkeypatch.setattr(api, "_strategy_scan_wrapper", scan)
    with pytest.raises(ScannerDataError) as failure:
        api._stock_strategy_alert_sweep_wrapper()
    assert allowed == [7.5, 7.5, 7.5, 13.]
    assert failure.value.diagnostics["strategies_completed"] == 2
    assert failure.value.diagnostics["strategy_results"][CODES[-1]]["status"] == "complete"
    assert len(state["mail_calls"]) == 1
    assert cache.read_bytes() == before  # Partial coverage is still not a full sweep.
    assert clock["now"] == 119.


def test_leaf_elapsed_reports_own_time_not_previous_siblings(monkeypatch):
    clock = {"now": 100.}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: clock["now"]))
    with runtime.scope("sweep", sweep=True):
        clock["now"] += 11.
        with runtime.scope("second"):
            clock["now"] += 3.
            result = runtime.diagnostics()
            assert result["elapsed_seconds"] == 14
            assert result["leaf_elapsed_seconds"] == 3
