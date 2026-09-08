"""Offline BI lifecycle diagnostics. Only fixture market data; no mail or DB."""
import copy
import json

import pytest

import modules.scanners as scanners
from modules.patterns import BI_STOCK_INDICATORS
from test_bi_deep_fixes_scan import _contract_result, _flat_bars, _patch_scan_io


def _result(green=16, hard=(), unavailable=()):
    result = _contract_result(
        (green >= 17 and not hard and not unavailable, 90, 173, ["ok"], "high", "A", 3, 3),
        green=green,
    )
    for check, spec in zip(result.indicator_checks, BI_STOCK_INDICATORS):
        check["key"] = spec[1]
        check["available"] = check["id"] not in unavailable
        check["passed"] = check["passed"] and check["available"]
    result.green_count = sum(c["passed"] for c in result.indicator_checks)
    result.available_count = sum(c["available"] for c in result.indicator_checks)
    result.indicator_contract_ok = result.available_count == 20
    result.hard_gate_failures = hard
    return result


def _io(monkeypatch, tmp_path, result, count=1):
    cache_save = scanners._bi_cache_save
    progress_write = scanners._bi_progress_write
    tickers = [f"T{i:03}" for i in range(count)]
    _patch_scan_io(monkeypatch, {ticker: _flat_bars() for ticker in tickers})
    monkeypatch.setattr(scanners, "_bi_cache_path", lambda direction="long": str(tmp_path / (direction + ".json")))
    monkeypatch.setattr(scanners, "_bi_progress_path", lambda direction="long": str(tmp_path / (direction + "-progress.json")))
    monkeypatch.setattr(scanners, "_bi_cache_save", cache_save)
    snapshots = []

    def progress(*args, **kwargs):
        snapshots.append((args[1], copy.deepcopy(kwargs)))
        progress_write(*args, **kwargs)

    monkeypatch.setattr(scanners, "_bi_progress_write", progress)
    monkeypatch.setattr(scanners, "_detect_code_revision", lambda: "123456789abc-dirty")
    monkeypatch.setattr(scanners, "analyze_breakout_imminent", lambda *a, **kw: result)
    return tickers, snapshots


def _read(tmp_path, direction="long", progress=False):
    return json.loads((tmp_path / (direction + ("-progress" if progress else "") + ".json")).read_text())


@pytest.mark.parametrize("direction", ["long", "short"])
def test_zero_final_has_confluence_and_running_snapshot(monkeypatch, tmp_path, direction):
    tickers, snapshots = _io(monkeypatch, tmp_path, _result(), count=10)
    scanners._bi_background_scan("fixture", direction, tickers)
    cache = _read(tmp_path, direction)
    progress = _read(tmp_path, direction, progress=True)
    d = cache["diagnostics"]["confluence"]
    assert cache["results"] == [] and cache["count"] == 0
    assert d == progress["diagnostics"]["confluence"]
    assert d["evaluated"] == d["below_required"] == 10
    assert d["schema_invalid"] == d["observation_errors"] == 0
    assert d["green_count_histogram"]["16"] == 10
    assert d["available_count_histogram"]["20"] == d["bar_count_histogram"]["50"] == 10
    assert d["code_revision"] == "123456789abc-dirty"
    assert len(d["run_id"]) == 32
    assert all(kw["diagnostics"]["confluence"]["run_id"] == d["run_id"] for _, kw in snapshots)
    running = [kw for status, kw in snapshots if status == "running" and kw.get("checked") == 10]
    assert running[0]["diagnostics"]["confluence"]["evaluated"] == 9
    assert cache["diagnostics"]["final_results"] == 0
    assert not (tmp_path / (direction + ".json.partial")).exists()


def test_hard_gate_is_not_a_schema_failure_or_signal(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17, hard=("range_breakdown",)))
    scanners._bi_background_scan("fixture", "long", tickers)
    cache = _read(tmp_path)
    d = cache["diagnostics"]["confluence"]
    assert cache["results"] == []
    assert d["schema_invalid"] == d["below_required"] == d["core_valid_count"] == 0
    assert d["pre_hard_gate_qualified"] == d["payload_accepted_count"] == 1
    assert d["first_hard_gate_counts"]["range_breakdown"] == 1


def test_unavailable_is_not_red(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17, unavailable=(20,)))
    scanners._bi_background_scan("fixture", "long", tickers)
    d = _read(tmp_path)["diagnostics"]["confluence"]
    assert d["incomplete"] == 1 and d["below_required"] == d["schema_invalid"] == 0
    assert d["available_count_histogram"]["19"] == 1
    assert d["factor_counts"]["candle_body_compression"] == dict(evaluated=0, green=0, red=0, unavailable=1)


@pytest.mark.parametrize("observer_broken", [False, True])
def test_accepted_plan_unchanged_even_if_observer_fails(monkeypatch, tmp_path, observer_broken):
    tickers, _ = _io(monkeypatch, tmp_path, _result(17))
    if observer_broken:
        def broken(*args, **kwargs):
            raise RuntimeError("private diagnostic exception")
        monkeypatch.setattr(scanners, "observe_bi_analysis", broken)
    scanners._bi_background_scan("fixture", "long", tickers)
    cache = _read(tmp_path)
    assert len(cache["results"]) == 1
    assert cache["results"][0]["BI_Score"] == 90
    assert cache["results"][0]["BI_Grade"] == "A"
    assert cache["diagnostics"]["indicator_passed"] == 1
    assert cache["diagnostics"]["analysis_errors"] == 0
    d = cache["diagnostics"]["confluence"]
    assert d["observation_errors"] == int(observer_broken)
    assert "private diagnostic exception" not in json.dumps(cache)


@pytest.mark.parametrize("green", [16, 17])
def test_stop_keeps_last_final_and_reports_partial_observation(monkeypatch, tmp_path, green):
    tickers, _ = _io(monkeypatch, tmp_path, _result(green), count=2)
    cache_path = tmp_path / "long.json"
    cache_path.write_text("previous-final", encoding="utf-8")
    calls = []
    original_get = scanners.rate_limited_get

    def get(*args, **kwargs):
        calls.append(1)
        return original_get(*args, **kwargs)

    monkeypatch.setattr(scanners, "rate_limited_get", get)
    monkeypatch.setattr(scanners, "_bi_should_stop", lambda direction: bool(calls))
    scanners._bi_background_scan("fixture", "long", tickers)
    assert cache_path.read_text() == "previous-final"
    p = _read(tmp_path, progress=True)
    assert p["status"] == "stopped"
    assert p["diagnostics"]["final_results"] is None
    assert p["diagnostics"]["coverage"] == "incomplete"
    assert p["diagnostics"]["confluence"]["evaluated"] == 1
    partial_path = tmp_path / "long.json.partial"
    assert partial_path.exists() == (green == 17)
    if green == 17:
        partial = json.loads(partial_path.read_text())
        assert partial["diagnostics"] == p["diagnostics"]
        assert partial["partial"] is True and len(partial["results"]) == 1


def test_provider_error_keeps_completed_observations_and_old_cache(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result(), count=2)
    (tmp_path / "long.json").write_text("previous-final", encoding="utf-8")
    original_get = scanners.rate_limited_get
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            return type("Unauthorized", (), {"status_code": 401})()
        return original_get(*args, **kwargs)

    monkeypatch.setattr(scanners, "rate_limited_get", get)
    with pytest.raises(scanners.ScannerDataError, match="scan_provider_unauthorized"):
        scanners._bi_background_scan("fixture", "long", tickers)
    assert (tmp_path / "long.json").read_text() == "previous-final"
    p = _read(tmp_path, progress=True)
    assert p["status"] == "error" and p["checked"] == 2
    assert p["diagnostics"]["confluence"]["evaluated"] == 1
    assert p["diagnostics"]["final_results"] is None


def test_malformed_analysis_return_counts_once(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, (False,))
    with pytest.raises(scanners.ScannerDataError, match="scan_data_incomplete"):
        scanners._bi_background_scan("fixture", "long", tickers)
    p = _read(tmp_path, progress=True)
    d = p["diagnostics"]["confluence"]
    assert d["evaluated"] == d["schema_invalid"] == 1
    assert p["diagnostics"]["analysis_errors"] == 1
    assert p["diagnostics"]["final_results"] is None


def test_generic_failure_has_current_identity_without_replacing_final(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result())
    (tmp_path / "long.json").write_text("previous-final", encoding="utf-8")

    def broken(candidates):
        raise RuntimeError("fixture")

    monkeypatch.setattr(scanners, "_bi_interleave_candidates_by_symbol", broken)
    with pytest.raises(RuntimeError, match="fixture"):
        scanners._bi_background_scan("fixture", "long", tickers)
    assert (tmp_path / "long.json").read_text() == "previous-final"
    p = _read(tmp_path, progress=True)
    assert p["status"] == "error"
    assert p["diagnostics"]["confluence"]["evaluated"] == 0
    assert p["diagnostics"]["final_results"] is None


def test_progress_replace_failure_keeps_previous_complete_json(monkeypatch, tmp_path):
    path = tmp_path / "progress.json"
    path.write_text('{"status":"previous"}', encoding="utf-8")
    monkeypatch.setattr(scanners, "_bi_progress_path", lambda direction: str(path))

    def broken(source, destination):
        raise OSError("fixture")

    monkeypatch.setattr(scanners.os, "replace", broken)
    scanners._bi_progress_write("long", "running", diagnostics={"test": 1})
    assert json.loads(path.read_text()) == {"status": "previous"}
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize("green", [16, 17])
def test_constructor_failure_is_unknown_not_a_changed_trading_gate(monkeypatch, tmp_path, green):
    tickers, _ = _io(monkeypatch, tmp_path, _result(green))

    def broken(**kwargs):
        raise RuntimeError("private init exception")

    def observer_must_not_run(*args, **kwargs):
        pytest.fail("No per-ticker observations after failed initialization")

    monkeypatch.setattr(scanners, "create_bi_diagnostics", broken)
    monkeypatch.setattr(scanners, "observe_bi_analysis", observer_must_not_run)
    scanners._bi_background_scan("fixture", "long", tickers)
    cache = _read(tmp_path)
    assert len(cache["results"]) == int(green == 17)
    assert cache["diagnostics"]["confluence"] == {
        "available": False, "reason": "initialization_failed", "initialization_errors": 1,
    }
    assert _read(tmp_path, progress=True)["status"] == "done"
    assert "private init exception" not in json.dumps(cache)


def test_final_publish_failure_keeps_last_final_and_marks_progress_incomplete(monkeypatch, tmp_path):
    tickers, _ = _io(monkeypatch, tmp_path, _result())
    cache_path = tmp_path / "long.json"
    cache_path.write_text("previous-final", encoding="utf-8")
    original_replace = scanners.os.replace

    def broken(source, destination):
        if str(destination) == str(cache_path):
            raise OSError("fixture publish failure")
        return original_replace(source, destination)

    monkeypatch.setattr(scanners.os, "replace", broken)
    with pytest.raises(RuntimeError, match="fixture publish failure"):
        scanners._bi_background_scan("fixture", "long", tickers)
    assert cache_path.read_text() == "previous-final"
    p = _read(tmp_path, progress=True)
    assert p["status"] == "error"
    assert p["diagnostics"]["coverage"] == "incomplete"
    assert p["diagnostics"]["final_results"] is None
    assert p["diagnostics"]["confluence"]["evaluated"] == 1
