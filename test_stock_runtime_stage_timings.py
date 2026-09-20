"""Stage telemetry is bounded, leaf-local and cannot relax work/error guards."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules import stock_scan_runtime as runtime

_SPEC = importlib.util.spec_from_file_location(
    "timing_evidence", Path(__file__).parent / "scripts" / "collect_server_evidence.py")
collector = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(collector)

LABELS = {"history", "structure", "execution_history", "plan", "cache_publish", "special_filter"}
SEMANTICS = "per_leaf_inclusive_elapsed_ms_not_additive"


def _clock(monkeypatch):
    value = {"now": 100.0}
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: value["now"]))
    return value


def test_stage_measurements_accumulate_and_report_only_known_labels(monkeypatch):
    clock = _clock(monkeypatch)
    with runtime.scope("leaf"):
        for label in sorted(LABELS):
            with runtime.measure(label):
                clock["now"] += 1.0
        with runtime.measure("history"):
            clock["now"] += .5
        with runtime.measure("PRIVATE_API_SECRET"):
            clock["now"] += 9
        result = runtime.diagnostics()
    assert result["stage_elapsed_ms"] == {label: 1500 if label == "history" else 1000 for label in LABELS}
    assert result["stage_timing_semantics"] == SEMANTICS
    assert "PRIVATE" not in json.dumps(result)


def test_nested_stage_measurements_are_explicitly_inclusive(monkeypatch):
    clock = _clock(monkeypatch)
    with runtime.scope("leaf"):
        with runtime.measure("structure"):
            clock["now"] += 1
            with runtime.measure("history"):
                clock["now"] += 2
        result = runtime.diagnostics()
    assert result["stage_elapsed_ms"] == {"structure": 3000, "history": 2000}
    assert result["stage_timing_semantics"] == SEMANTICS


@pytest.mark.parametrize("failure", [RuntimeError("PRIVATE_ERROR"), runtime.ScanWorkTimeout()])
def test_failure_duration_is_recorded_without_swallowing_or_replacing_error(monkeypatch, failure):
    clock = _clock(monkeypatch)
    with runtime.scope("leaf"):
        with pytest.raises(type(failure)) as caught:
            with runtime.measure("execution_history"):
                clock["now"] += 12
                raise failure
        assert caught.value is failure
        result = runtime.diagnostics()
    assert result["stage_elapsed_ms"] == {"execution_history": 12000}
    assert "PRIVATE" not in json.dumps(result)


def test_telemetry_does_not_change_deadline_or_skip_timeout(monkeypatch):
    clock = _clock(monkeypatch)
    with runtime.scope("leaf") as state:
        deadline = state["deadline"]
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.measure("history"):
                clock["now"] = deadline
                runtime.checkpoint()
        assert state["deadline"] == deadline
        assert runtime.diagnostics()["stage_elapsed_ms"] == {"history": 1200000}


def test_timings_are_leaf_local_and_snapshot_is_detached(monkeypatch):
    clock = _clock(monkeypatch)
    with runtime.scope("sweep", sweep=True):
        with runtime.scope("first"):
            with runtime.measure("history"):
                clock["now"] += 20
            saved = runtime.diagnostics()
            saved["stage_elapsed_ms"]["history"] = 999
            assert runtime.diagnostics()["stage_elapsed_ms"] == {"history": 20000}
        with runtime.scope("second"):
            assert runtime.diagnostics()["stage_elapsed_ms"] == {}
            with runtime.measure("plan"):
                clock["now"] += 1
            assert runtime.diagnostics()["stage_elapsed_ms"] == {"plan": 1000}
        assert runtime.diagnostics()["stage_elapsed_ms"] == {}


def test_measurement_outside_runtime_and_unknown_label_are_noop(monkeypatch):
    monkeypatch.setattr(runtime, "current", lambda: None)
    def forbidden():
        raise AssertionError("No clock outside opted-in runtime")
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=forbidden))
    with runtime.measure("history"):
        pass
    assert runtime.diagnostics() == {}


def test_submillisecond_samples_are_accumulated_before_rounding(monkeypatch):
    clock = _clock(monkeypatch)
    with runtime.scope("leaf"):
        for _ in range(10):
            with runtime.measure("plan"):
                clock["now"] += .00075
        assert runtime.diagnostics()["stage_elapsed_ms"] == {"plan": 7}


def _attempt_payload(diagnostics):
    return {"schema_version": 1, "strategy_slug": "cup_and_handle_breakout",
            "attempt_kind": "stock_strategy", "run_id": "a" * 32,
            "code_revision": "c65028801657", "started_at": "2026-09-19T20:00:00Z",
            "updated_at": "2026-09-19T20:20:00Z", "status": "error",
            "result_count": None, "error_code": "scan_timeout", "results": [],
            "diagnostics": diagnostics}


@pytest.mark.parametrize("attempt", [False, True])
def test_collector_exports_only_bounded_fixed_stage_timings(tmp_path, attempt):
    diagnostics = {"stage_elapsed_ms": {"history": 1200, "structure": 300,
        "plan": 0, "PRIVATE_NAME": 5}, "stage_timing_semantics": "PRIVATE_FORGED_LABEL"}
    payload = _attempt_payload(diagnostics) if attempt else {"results": [], "diagnostics": diagnostics}
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    before = path.read_bytes()
    result = collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout") if attempt else collector.safe_cache_summary(path)
    assert result["stage_elapsed_ms"] == {"history": 1200, "structure": 300, "plan": 0, "_omitted_categories": 1}
    assert result["stage_timing_semantics"] == SEMANTICS
    assert "PRIVATE" not in json.dumps(result)
    assert path.read_bytes() == before


@pytest.mark.parametrize("value", [True, -1, 1.2, "9", None, {}, [], float("nan"), 2**63])
@pytest.mark.parametrize("attempt", [False, True])
def test_collector_does_not_coerce_invalid_stage_durations(tmp_path, value, attempt):
    diagnostics = {"stage_elapsed_ms": {"history": value, "plan": 12}}
    payload = _attempt_payload(diagnostics) if attempt else {"results": [], "diagnostics": diagnostics}
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout") if attempt else collector.safe_cache_summary(path)
    assert result["stage_elapsed_ms"] == {"plan": 12, "_omitted_categories": 1}
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize("attempt", [False, True])
@pytest.mark.parametrize("diagnostics", [{}, {"stage_elapsed_ms": "PRIVATE"}, {"stage_elapsed_ms": None}])
def test_collector_does_not_invent_zero_for_legacy_or_invalid_telemetry(tmp_path, attempt, diagnostics):
    payload = _attempt_payload(diagnostics) if attempt else {"results": [], "diagnostics": diagnostics}
    path = tmp_path / "cache.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout") if attempt else collector.safe_cache_summary(path)
    assert "stage_elapsed_ms" not in result
    assert "stage_timing_semantics" not in result


@pytest.fixture
def api_attempt_io(monkeypatch, tmp_path):
    import api
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_stock_attempt_run_ids", {})
    def forbidden(*args, **kwargs):
        pytest.fail("Timing evidence must never invoke a provider or mail sender")
    monkeypatch.setattr(api, "rate_limited_get", forbidden)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", forbidden)
    return api, tmp_path / "stock_strategy_cup_and_handle_breakout_attempt.json"


@pytest.mark.parametrize("status", ["running", "complete", "error"])
def test_api_attempt_publication_persists_real_runtime_stage_timings(
        monkeypatch, api_attempt_io, status):
    api, path = api_attempt_io
    clock = _clock(monkeypatch)
    attempt = _attempt_payload({})
    api._stock_attempt_run_ids["cup_and_handle_breakout"] = attempt["run_id"]
    diagnostics = {"coverage": "complete" if status == "complete" else "incomplete",
                   "final_results": 0 if status == "complete" else None,
                   "stage_elapsed_ms": {"PRIVATE_LABEL": 987654},
                   "stage_timing_semantics": "PRIVATE_FORGED_SEMANTICS"}
    with runtime.scope("Cup and Handle Breakout"):
        with runtime.measure("history"):
            clock["now"] += 2.5
        with runtime.measure("structure"):
            clock["now"] += .75
        assert api._publish_stock_strategy_attempt(
            attempt, status, diagnostics=diagnostics,
            result_count=0 if status == "complete" else None,
            error="scan_timeout" if status == "error" else None)
    persisted = json.loads(path.read_text())
    assert persisted["diagnostics"]["stage_elapsed_ms"] == {"history": 2500, "structure": 750}
    assert persisted["diagnostics"]["stage_timing_semantics"] == SEMANTICS
    assert "PRIVATE" not in json.dumps(persisted)
    assert persisted["results"] == []
    readback = api._read_stock_strategy_attempt("Cup and Handle Breakout")
    assert readback["available"] is True
    assert readback["status"] == status
    assert readback["diagnostics"]["stage_elapsed_ms"] == {"history": 2500, "structure": 750}
    assert readback["diagnostics"]["stage_timing_semantics"] == SEMANTICS


@pytest.mark.parametrize("sweep", [False, True])
def test_api_attempt_projection_and_readback_keep_only_fixed_bounded_timings(api_attempt_io, sweep):
    api, path = api_attempt_io
    diagnostics = {"stage_elapsed_ms": {
        "history": 1200, "structure": 0, "execution_history": True,
        "plan": -1, "cache_publish": 1.2, "special_filter": 10**9 + 1,
        "PRIVATE_PROVIDER": 7}, "stage_timing_semantics": "PRIVATE_LABEL"}
    projected = api._stock_strategy_attempt_diagnostics(diagnostics, sweep=sweep)
    assert projected["stage_elapsed_ms"] == {"history": 1200, "structure": 0}
    assert projected["stage_timing_semantics"] == SEMANTICS
    assert "PRIVATE" not in json.dumps(projected)
    path.write_text(json.dumps(_attempt_payload(diagnostics)), encoding="utf8")
    before = path.read_bytes()
    readback = api._read_stock_strategy_attempt("Cup and Handle Breakout")
    assert readback["available"] is True
    assert readback["diagnostics"]["stage_elapsed_ms"] == projected["stage_elapsed_ms"]
    assert readback["diagnostics"]["stage_timing_semantics"] == SEMANTICS
    assert "PRIVATE" not in json.dumps(readback)
    assert path.read_bytes() == before


@pytest.mark.parametrize("diagnostics", [{}, {"stage_elapsed_ms": None}, {"stage_elapsed_ms": "PRIVATE"}])
def test_api_legacy_attempt_projection_and_readback_preserve_absent_timings(api_attempt_io, diagnostics):
    api, path = api_attempt_io
    for sweep in (False, True):
        projected = api._stock_strategy_attempt_diagnostics(diagnostics, sweep=sweep)
        assert "stage_elapsed_ms" not in projected
        assert "stage_timing_semantics" not in projected
    path.write_text(json.dumps(_attempt_payload(diagnostics)), encoding="utf8")
    readback = api._read_stock_strategy_attempt("Cup and Handle Breakout")
    assert readback["available"] is True
    assert "stage_elapsed_ms" not in readback["diagnostics"]
    assert "stage_timing_semantics" not in readback["diagnostics"]
