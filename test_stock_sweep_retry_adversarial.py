"""Independent retry fault probes: fake time, no providers, mail or live data."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

import api
from modules import scan_control
from modules import stock_scan_runtime as runtime
from modules.scanners import ScannerDataError
from test_stock_bi_scan_data_outcomes import snapshot, stock_io
from test_stock_strategy_sweep_isolation import (
    CODES, STRATEGIES, _mock_sweep, _offline_sweep_state, _row,
)


@pytest.fixture
def clock(monkeypatch):
    value = {"now": 100.0}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: value["now"]))
    # Keep the real per-leaf cap, but use a shorter synthetic sweep.
    monkeypatch.setattr(runtime, "SWEEP_WORK_SECONDS", 400.0)
    monkeypatch.setattr(api.time, "sleep", lambda _seconds: None)
    return value


def _expire(clock):
    clock["now"] = runtime.current()["deadline"]
    runtime.checkpoint("history")


def _sweep_attempt(tmp_path):
    return json.loads((tmp_path / "stock_strategy_sweep_attempt.json").read_text())


@pytest.mark.parametrize("failure", [
    ScannerDataError("scan_provider_unauthorized"),
    ScannerDataError("scan_data_invalid"),
    ScannerDataError("scan_data_incomplete"),
    RuntimeError("provider timeout PRIVATE_PROVIDER_BODY"),
    runtime.ScanWorkTimeout(),  # Exception type alone, outside a child scope, is insufficient.
])
def test_unregistered_timeout_or_data_failure_never_retries(clock, monkeypatch, tmp_path, failure):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {
        STRATEGIES[0]: failure, STRATEGIES[1]: [_row("CURRENT")],
    })
    with pytest.raises(ScannerDataError):
        api._stock_strategy_alert_sweep_wrapper()
    assert state["attempts"] == list(STRATEGIES)
    assert len(state["mail_calls"]) == 1
    assert [row["Ticker"] for row in state["mail_calls"][0][1]] == ["CURRENT"]
    assert cache.read_bytes() == before
    diagnostics = _sweep_attempt(tmp_path)["diagnostics"]
    assert diagnostics["timeout_retries_attempted"] == 0
    assert diagnostics["timeout_retries_recovered"] == 0
    assert "PRIVATE" not in json.dumps(diagnostics)


@pytest.mark.parametrize("remaining,expected_retry", [(75.0, True), (74.999, False)])
def test_exact_remaining_budget_boundary_and_original_deadline(clock, monkeypatch, tmp_path,
                                                               remaining, expected_retry):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls = []
    retry_deadlines = []

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        current = runtime.current()
        if len(calls) == 1:
            _expire(clock)
        if current["is_timeout_retry"]:
            retry_deadlines.append((current["deadline"], current["root"]["work_deadline"]))
        elif name in STRATEGIES[1:3]:
            clock["now"] += 90.0
        elif name == STRATEGIES[-1]:
            clock["now"] = current["root"]["work_deadline"] - remaining
        runtime.checkpoint()
        return []

    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    if expected_retry:
        api._stock_strategy_alert_sweep_wrapper()
        assert retry_deadlines == [(485.0, 500.0)]
        assert json.loads(cache.read_text())["diagnostics"]["coverage"] == "complete"
    else:
        with pytest.raises(ScannerDataError):
            api._stock_strategy_alert_sweep_wrapper()
        assert cache.read_bytes() == before and retry_deadlines == []
    assert calls == list(STRATEGIES) + ([STRATEGIES[0]] if expected_retry else [])
    assert state["mail_calls"] == []


def test_second_timeout_stops_at_reserved_boundary_without_another_retry(clock, monkeypatch, tmp_path):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls = []
    deadlines = []

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        if name == STRATEGIES[0]:
            deadlines.append(runtime.current()["deadline"])
            _expire(clock)
        return [_row(name)]

    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    with pytest.raises(ScannerDataError) as failure:
        api._stock_strategy_alert_sweep_wrapper()
    assert calls == list(STRATEGIES) + [STRATEGIES[0]]
    assert deadlines == [200.0, 485.0] and clock["now"] == 485.0
    assert cache.read_bytes() == before and len(state["mail_calls"]) == 1
    diagnostics = failure.value.diagnostics
    assert diagnostics["strategies_attempted"] == 4
    assert diagnostics["strategies_completed"] == 3 and diagnostics["strategies_failed"] == 1
    assert diagnostics["timeout_retries_attempted"] == 1
    assert diagnostics["timeout_retries_recovered"] == 0
    assert diagnostics["strategy_results"][CODES[0]]["error_code"] == "scan_timeout"


@pytest.mark.parametrize("retry_rows", [
    [_row("PARTIAL"), _row("INVALID", score=float("nan"))],
    [_row("PARTIAL"), _row("INVALID", change=float("inf"))],
    [_row("PARTIAL"), None],
])
def test_invalid_retry_contributes_nothing_and_preserves_good_siblings(clock, monkeypatch, tmp_path,
                                                                     retry_rows):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls = []
    sibling_rows = {name: _row(name, score=80 + index) for index, name in enumerate(STRATEGIES[1:])}
    original = deepcopy(sibling_rows)

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        if name == STRATEGIES[0]:
            if calls.count(name) == 1:
                _expire(clock)
            return deepcopy(retry_rows)
        return [sibling_rows[name]]

    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    with pytest.raises(ScannerDataError) as failure:
        api._stock_strategy_alert_sweep_wrapper()
    assert sibling_rows == original
    assert cache.read_bytes() == before
    assert calls == list(STRATEGIES) + [STRATEGIES[0]]
    assert len(state["mail_calls"]) == 1
    actual = state["mail_calls"][0][1]
    assert {row["Ticker"] for row in actual} == set(STRATEGIES[1:])
    assert all(row["trade_setup"] == original[row["Ticker"]]["trade_setup"] for row in actual)
    diagnostics = failure.value.diagnostics
    assert diagnostics["current_result_count"] == 3
    assert diagnostics["strategies_completed"] == 3 and diagnostics["strategies_failed"] == 1
    outcome = diagnostics["strategy_results"][CODES[0]]
    assert outcome == {"status": "error", "result_count": None, "error_code": "scan_data_invalid",
                       "timeout_retry_count": 1, "initial_error_code": "scan_timeout"}
    assert _sweep_attempt(tmp_path)["diagnostics"]["strategy_results"][CODES[0]] == outcome


def test_multiple_timeouts_retry_only_first_and_mail_after_final_attempt(clock, monkeypatch, tmp_path):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls, order = [], []

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        order.append(name)
        if name in STRATEGIES[:2] and calls.count(name) == 1:
            _expire(clock)
        return [_row(name)]

    def guard(name, rows, market):
        order.append("mail_guard")
        state["mail_calls"].append((name, deepcopy(rows), market))

    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", guard)
    with pytest.raises(ScannerDataError) as failure:
        api._stock_strategy_alert_sweep_wrapper()
    assert calls == list(STRATEGIES) + [STRATEGIES[0]]
    assert order[-2:] == [STRATEGIES[0], "mail_guard"]
    assert len(state["mail_calls"]) == 1 and cache.read_bytes() == before
    diagnostics = failure.value.diagnostics
    assert diagnostics["timeout_retries_recovered"] == 1
    assert diagnostics["strategies_completed"] == 3 and diagnostics["strategies_failed"] == 1
    assert diagnostics["strategy_results"][CODES[1]]["error_code"] == "scan_timeout"


@pytest.mark.parametrize("restart_at", ["before_retry", "inside_retry"])
def test_data_epoch_restart_propagates_without_mail_or_aggregate(clock, monkeypatch, tmp_path, restart_at):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls = []
    points = []

    def safe_point():
        points.append(True)
        current = runtime.current()
        if ((restart_at == "before_retry" and len(calls) == 4)
                or (restart_at == "inside_retry" and current.get("is_timeout_retry"))):
            clock["now"] += 3600.0
            raise scan_control.ScanRestartRequired(paused_seconds=3600.0)
        return 0.0

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            _expire(clock)
        if runtime.current()["is_timeout_retry"]:
            api._scan_control_point()
        return [_row(name)]

    monkeypatch.setattr(scan_control, "safe_point", safe_point)
    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    with pytest.raises(scan_control.ScanRestartRequired):
        api._stock_strategy_alert_sweep_wrapper()
    assert calls == list(STRATEGIES) + ([STRATEGIES[0]] if restart_at == "inside_retry" else [])
    assert cache.read_bytes() == before and state["mail_calls"] == []
    assert runtime.current() is None


def test_actual_pause_inside_retry_does_not_consume_work_or_extend_active_cap(clock, monkeypatch, tmp_path):
    cache, _, state = _mock_sweep(monkeypatch, tmp_path, {})
    calls, observed = [], []

    def safe_point():
        if runtime.current().get("is_timeout_retry"):
            clock["now"] += 3600.0
            return 3600.0
        return 0.0

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            _expire(clock)
        if runtime.current()["is_timeout_retry"]:
            current = runtime.current()
            assert current["deadline"] == 485.0
            api._scan_control_point()
            assert current["deadline"] == 4085.0 and current["root"]["work_deadline"] == 4100.0
            clock["now"] += 2.0
            runtime.checkpoint()
            observed.append(runtime.diagnostics())
        return []

    monkeypatch.setattr(scan_control, "safe_point", safe_point)
    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    api._stock_strategy_alert_sweep_wrapper()
    assert observed[0]["leaf_elapsed_seconds"] == 2
    assert observed[0]["elapsed_seconds"] == 102
    assert json.loads(cache.read_text())["diagnostics"]["coverage"] == "complete"
    assert state["mail_calls"] == []


@pytest.mark.parametrize("retry_failure", [None, "scan_provider_unauthorized", "scan_data_invalid"])
def test_real_leaf_final_attempt_source_replaces_timeout_evidence(clock, monkeypatch, tmp_path, retry_failure):
    real_leaf = api._strategy_scan_wrapper
    final, _, before_leaf = stock_io(monkeypatch, tmp_path, [snapshot()])
    cache, before_aggregate, state = _mock_sweep(monkeypatch, tmp_path, {})
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "realtime")
    feeds = []
    leaf_attempts = []
    writer = api.save_cache_file

    def capture(path, rows, metadata=None):
        if path.endswith("stock_strategy_momentum_breakout_long_attempt.json"):
            leaf_attempts.append(deepcopy(metadata))
        return writer(path, rows, metadata)

    def feed(*_args):
        feeds.append(True)
        if len(feeds) == 1:
            _expire(clock)
        if retry_failure:
            raise ScannerDataError(retry_failure)
        return [snapshot()]

    @runtime.bounded_leaf
    def empty_sibling(name, **kwargs):
        return []

    def dispatch(name, **kwargs):
        state["attempts"].append(name)
        return real_leaf(name, **kwargs) if name == STRATEGIES[0] else empty_sibling(name, **kwargs)

    monkeypatch.setattr(api, "save_cache_file", capture)
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", feed)
    monkeypatch.setattr(api, "_strategy_scan_wrapper", dispatch)
    if retry_failure:
        with pytest.raises(ScannerDataError):
            api._stock_strategy_alert_sweep_wrapper()
        assert final.read_bytes() == before_leaf and cache.read_bytes() == before_aggregate
    else:
        api._stock_strategy_alert_sweep_wrapper()
        assert json.loads(final.read_text())["results"] == []
        assert json.loads(cache.read_text())["diagnostics"]["coverage"] == "complete"
    assert state["attempts"] == list(STRATEGIES) + [STRATEGIES[0]]
    assert len(feeds) == 2 and state["mail_calls"] == []
    first_error = next(item for item in leaf_attempts if item["status"] == "error")
    assert first_error["error_code"] == "scan_timeout"
    last = leaf_attempts[-1]
    assert last["run_id"] != first_error["run_id"]
    assert last["status"] == ("error" if retry_failure else "complete")
    assert last.get("error_code") == retry_failure
    persisted = api._read_stock_strategy_attempt(STRATEGIES[0])
    assert persisted["available"] is True
    assert persisted["attempt_run_id"] == last["run_id"]
    assert persisted["error_code"] == retry_failure
    assert persisted["result_count"] == (None if retry_failure else 0)


def test_retry_metadata_projection_rejects_spoofed_or_out_of_range_counts():
    raw = {
        "timeout_retries_attempted": True, "timeout_retries_recovered": -1,
        "strategy_results": {
            CODES[0]: {"status": "error", "error_code": "scan_timeout", "result_count": None,
                       "timeout_retry_count": 2, "initial_error_code": "PRIVATE"},
        },
    }
    projected = api._stock_strategy_attempt_diagnostics(raw, sweep=True)
    assert "timeout_retries_attempted" not in projected
    assert "timeout_retries_recovered" not in projected
    assert "timeout_retry_count" not in projected["strategy_results"][CODES[0]]
    assert "initial_error_code" not in projected["strategy_results"][CODES[0]]
    assert "PRIVATE" not in json.dumps(projected)


def test_mail_guard_failure_after_recovery_never_retries_mail_or_replaces_aggregate(clock, monkeypatch, tmp_path):
    cache, before, _ = _mock_sweep(monkeypatch, tmp_path, {})
    calls, mails = [], []

    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        calls.append(name)
        if len(calls) == 1:
            _expire(clock)
        return [_row(name)]

    def failed_guard(*args):
        mails.append(args)
        raise RuntimeError("PRIVATE_SMTP_DETAIL")

    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", failed_guard)
    with pytest.raises(ScannerDataError) as failure:
        api._stock_strategy_alert_sweep_wrapper()
    assert calls == list(STRATEGIES) + [STRATEGIES[0]]
    assert len(mails) == 1 and cache.read_bytes() == before
    diag = failure.value.diagnostics
    assert diag["strategies_completed"] == 4 and diag["strategies_failed"] == 0
    assert diag["timeout_retries_recovered"] == 1 and diag["mail_status"] == "error"
    assert "PRIVATE" not in json.dumps(diag)
