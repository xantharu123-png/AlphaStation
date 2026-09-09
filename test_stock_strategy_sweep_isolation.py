"""Synthetic sweep lifecycle tests: no providers, live scans, SMTP or broker I/O."""
from copy import deepcopy
from datetime import datetime
import importlib.util
import json
from pathlib import Path

import pytest

import api
from modules.scanners import ScannerDataError
from test_stock_bi_scan_data_outcomes import snapshot, stock_io


STRATEGIES = ("Momentum Breakout Long", "Gap Momentum Long", "Gap Momentum Short", "Cup and Handle Breakout")
CODES = ("momentum_breakout_long", "gap_momentum_long", "gap_momentum_short", "cup_and_handle_breakout")


@pytest.fixture(autouse=True)
def _offline_sweep_state(monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api, "_stock_attempt_run_ids", {})
    monkeypatch.setattr(api.time, "sleep", lambda seconds: None)
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", list(STRATEGIES))
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a, **k: None)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **k: pytest.fail("No provider calls in sweep isolation tests"))
    monkeypatch.setattr(api, "_send_email_alert", lambda *a, **k: pytest.fail("No actual mail transport in sweep isolation tests"))


def _row(symbol, score=90, change=3):
    return {"Ticker": symbol, "score": score, "Change_Pct": change,
            "trade_setup": {"entry": 10, "stop": 9, "tp1": 12, "tp2": 13}}


def _mock_sweep(monkeypatch, tmp_path, outcomes):
    cache = tmp_path / "aggregate.json"
    cache.write_text('{"cached_at":"2020-01-01T00:00:00","results":[{"Ticker":"STALE_PRIVATE"}]}', encoding="utf8")
    previous = cache.read_bytes()
    monkeypatch.setattr(api, "STRATEGY_SCAN_CACHE", str(cache))
    state = {"attempts": [], "mail_calls": []}
    def scan(name, send_email=True, *, publish_generic_cache=True):
        assert send_email is False and publish_generic_cache is False
        state["attempts"].append(name)
        result = outcomes.get(name, [])
        if isinstance(result, Exception):
            raise result
        return deepcopy(result)
    def guarded_mail(name, rows, market):
        state["mail_calls"].append((name, deepcopy(rows), market))
    monkeypatch.setattr(api, "_strategy_scan_wrapper", scan)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", guarded_mail)
    return cache, previous, state


@pytest.mark.parametrize("failure", [
    *[ScannerDataError(code, {"provider": "PRIVATE_PROVIDER_BODY"}) for code in sorted(ScannerDataError.CODES)],
    RuntimeError("PRIVATE_PROVIDER_BODY apiKey=PRIVATE_TOKEN"),
])
def test_first_failure_does_not_abort_remaining_strategies_or_reuse_stale_cache(monkeypatch, tmp_path, failure, capsys):
    outcomes = {name: [_row(f"FRESH{index}", score=80 + index)] for index, name in enumerate(STRATEGIES)}
    outcomes[STRATEGIES[0]] = failure
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, outcomes)
    with pytest.raises(ScannerDataError, match="scan_data_incomplete") as caught:
        api._stock_strategy_alert_sweep_wrapper()
    assert state["attempts"] == list(STRATEGIES)
    assert len(state["mail_calls"]) == 1
    name, rows, market = state["mail_calls"][0]
    assert name == "Aktien Auto-Sweep" and market == "stocks"
    assert [row["Ticker"] for row in rows] == ["FRESH3", "FRESH2", "FRESH1"]
    assert cache.read_bytes() == previous
    diagnostics = caught.value.diagnostics
    assert diagnostics["coverage"] == "incomplete" and diagnostics["final_results"] is None
    assert diagnostics["strategies_attempted"] == 4 and diagnostics["strategies_completed"] == 3
    assert diagnostics["strategies_failed"] == 1 and diagnostics["current_result_count"] == 3
    assert diagnostics["strategy_results"][CODES[0]]["result_count"] is None
    expected = failure.code if isinstance(failure, ScannerDataError) else "scan_failed"
    assert diagnostics["strategy_results"][CODES[0]]["error_code"] == expected
    assert diagnostics["mail_status"] == "guarded"
    assert "PRIVATE" not in json.dumps(diagnostics) and "PRIVATE" not in capsys.readouterr().out


@pytest.mark.parametrize("failed_index", range(4))
def test_ready_successes_before_or_after_any_failure_still_enter_single_guard(monkeypatch, tmp_path, failed_index):
    outcomes = {name: [_row(f"CURRENT{index}")] for index, name in enumerate(STRATEGIES)}
    outcomes[STRATEGIES[failed_index]] = ScannerDataError("scan_provider_rate_limited")
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, outcomes)
    with pytest.raises(ScannerDataError):
        api._stock_strategy_alert_sweep_wrapper()
    assert len(state["attempts"]) == 4 and len(state["mail_calls"]) == 1
    assert {row["Ticker"] for row in state["mail_calls"][0][1]} == {
        f"CURRENT{index}" for index in range(4) if index != failed_index
    }
    assert cache.read_bytes() == previous


def test_all_failures_are_individual_unknown_results_without_mail_or_new_aggregate(monkeypatch, tmp_path):
    outcomes = {name: ScannerDataError("scan_provider_unauthorized") for name in STRATEGIES}
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, outcomes)
    with pytest.raises(ScannerDataError) as caught:
        api._stock_strategy_alert_sweep_wrapper()
    assert state["attempts"] == list(STRATEGIES) and state["mail_calls"] == []
    assert cache.read_bytes() == previous
    diagnostics = caught.value.diagnostics
    assert diagnostics["strategies_failed"] == 4 and diagnostics["strategies_completed"] == 0
    assert diagnostics["final_results"] is None and diagnostics["mail_status"] == "no_results"
    assert all(entry["result_count"] is None for entry in diagnostics["strategy_results"].values())


def test_all_successful_zero_is_a_real_complete_sweep_without_guard_call(monkeypatch, tmp_path):
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, {})
    api._stock_strategy_alert_sweep_wrapper()
    payload = json.loads(cache.read_text())
    assert cache.read_bytes() != previous and payload["results"] == []
    assert payload["diagnostics"]["coverage"] == "complete"
    assert payload["diagnostics"]["final_results"] == 0
    assert payload["diagnostics"]["strategies_completed"] == 4
    assert payload["diagnostics"]["strategies_failed"] == 0
    assert state["mail_calls"] == []


def test_all_success_keeps_existing_global_ranking_caps_and_combined_mail_context(monkeypatch, tmp_path):
    outcomes = {name: [_row(f"S{index}R{row}", score=1000 * index - row) for row in range(40)]
                for index, name in enumerate(STRATEGIES)}
    cache, _, state = _mock_sweep(monkeypatch, tmp_path, outcomes)
    api._stock_strategy_alert_sweep_wrapper()
    payload = json.loads(cache.read_text())
    assert len(payload["results"]) == 100
    assert all(sum(row["Strategy"] == name for row in payload["results"]) == 25 for name in STRATEGIES)
    assert len(state["mail_calls"]) == 1
    name, rows, market = state["mail_calls"][0]
    assert name == "Aktien Auto-Sweep" and market == "stocks" and len(rows) == 75
    assert rows == payload["results"][:75]
    assert [row["score"] for row in rows] == sorted((row["score"] for row in rows), reverse=True)
    assert payload["diagnostics"]["final_results"] == 100
    assert all(item["result_count"] == 40 and item["aggregate_candidate_count"] == 25
               for item in payload["diagnostics"]["strategy_results"].values())


@pytest.mark.parametrize("bad_rows", [None, {}, "PRIVATE_BODY", [None], [_row("BAD", score=float("nan"))],
                                      [_row("BAD", change=float("inf"))], [_row("GOOD"), _row("BAD", score="PRIVATE_SCORE")]])
def test_malformed_strategy_output_is_not_zero_or_partially_committed(monkeypatch, tmp_path, bad_rows):
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, {STRATEGIES[0]: bad_rows})
    with pytest.raises(ScannerDataError) as caught:
        api._stock_strategy_alert_sweep_wrapper()
    assert state["attempts"] == list(STRATEGIES) and state["mail_calls"] == []
    assert caught.value.diagnostics["strategy_results"][CODES[0]]["error_code"] == "scan_data_invalid"
    assert cache.read_bytes() == previous


def test_unexpected_guard_exception_has_no_retry_no_new_aggregate_and_no_sensitive_detail(monkeypatch, tmp_path):
    cache, previous, state = _mock_sweep(monkeypatch, tmp_path, {name: [_row(f"R{i}")] for i, name in enumerate(STRATEGIES)})
    calls = []
    def failed_guard(*args):
        calls.append(args)
        raise RuntimeError("PRIVATE_TRANSPORT_BODY")
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", failed_guard)
    with pytest.raises(ScannerDataError) as caught:
        api._stock_strategy_alert_sweep_wrapper()
    assert len(calls) == 1 and state["attempts"] == list(STRATEGIES)
    assert caught.value.diagnostics["mail_status"] == "error"
    assert caught.value.diagnostics["mail_error_code"] == "scan_failed"
    assert "PRIVATE" not in json.dumps(caught.value.diagnostics)
    assert cache.read_bytes() == previous


def test_guard_enrichment_cannot_mutate_the_completed_scan_aggregate(monkeypatch, tmp_path):
    cache, _, _ = _mock_sweep(monkeypatch, tmp_path, {STRATEGIES[1]: [_row("CURRENT")]})
    def mutate_guard(_name, rows, _market):
        rows[0]["trade_setup"]["entry"] = 999
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", mutate_guard)
    api._stock_strategy_alert_sweep_wrapper()
    assert json.loads(cache.read_text())["results"][0]["trade_setup"]["entry"] == 10


@pytest.mark.parametrize("publish_generic", [False, True])
def test_actual_leaf_can_preserve_sweep_aggregate_without_changing_default_behavior(monkeypatch, tmp_path, publish_generic):
    final, generic, _ = stock_io(monkeypatch, tmp_path, [snapshot()])
    generic.write_text('{"results":[{"old":true}]}', encoding="utf8")
    before = generic.read_bytes()
    kwargs = {} if publish_generic else {"publish_generic_cache": False}
    assert api._strategy_scan_wrapper("Momentum Breakout Long", send_email=False, **kwargs) == []
    assert json.loads(final.read_text())["results"] == []
    assert (generic.read_bytes() != before) is publish_generic


def test_existing_combined_guard_preserves_strategy_dedupe_and_next_sweep_cooldown(monkeypatch, tmp_path):
    from test_cluster_warning_mail import _mock_sweep_env, _sweep_row
    rows = {STRATEGIES[1]: [_sweep_row("DUP"), _sweep_row("DUP"), _sweep_row("OTHER")],
            STRATEGIES[3]: [_sweep_row("DUP")]}
    real_guard = api._send_strategy_scan_alerts
    cache, _, state = _mock_sweep(monkeypatch, tmp_path, rows)
    sent = _mock_sweep_env(monkeypatch, {"DUP", "OTHER"})
    monkeypatch.setattr(api, "_adr_ticker_set", lambda: set())
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **k: dict(row))
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda _scanner, row, *a, **k: dict(row))
    monkeypatch.setattr(api, "_ensure_stock_business_quality", lambda row: dict(row))
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", lambda row, **k: (True, ""))
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)
    monkeypatch.setattr(api, "_send_strategy_scan_alerts", real_guard)
    api._stock_strategy_alert_sweep_wrapper()
    assert state["attempts"] == list(STRATEGIES)
    # Existing dedupe is strategy-aware: same-strategy duplicate suppressed,
    # distinct strategy identities retained. This change must not redefine it.
    assert len(sent) == 3
    assert sum(body.count("<b>DUP</b>") for _, body in sent) == 2
    assert sum(body.count("<b>OTHER</b>") for _, body in sent) == 1
    assert json.loads(cache.read_text())["diagnostics"]["mail_status"] == "guarded"
    api._stock_strategy_alert_sweep_wrapper()
    assert len(sent) == 3


def _attempt_file(tmp_path, slug=CODES[0]):
    return tmp_path / ("stock_strategy_sweep_attempt.json" if slug == "stock_strategy_sweep"
                       else f"stock_strategy_{slug}_attempt.json")


def test_failed_leaf_persists_current_safe_attempt_without_false_zero_or_cache_replacement(monkeypatch, tmp_path):
    final, generic, before = stock_io(monkeypatch, tmp_path, [])
    def fail_snapshot(*args):
        raise ScannerDataError("scan_provider_unauthorized", {
            "universe_count": 0, "provider_body": "PRIVATE_PROVIDER_BODY",
            "stage_counts": {"snapshot_universe": 0, "PRIVATE_SYMBOL": 1},
            "rejected": {"missing_price_or_prev_close": 2, "PRIVATE_SYMBOL": 3},
        })
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", fail_snapshot)
    with pytest.raises(ScannerDataError, match="scan_provider_unauthorized"):
        api._strategy_scan_wrapper(STRATEGIES[0], send_email=False)
    payload = json.loads(_attempt_file(tmp_path).read_text())
    assert payload["status"] == "error" and payload["result_count"] is None and payload["results"] == []
    assert payload["error_code"] == "scan_provider_unauthorized"
    assert payload["diagnostics"]["final_results"] is None
    assert payload["diagnostics"]["rejected"] == {"missing_price_or_prev_close": 2}
    assert "PRIVATE" not in json.dumps(payload)
    assert datetime.fromisoformat(payload["started_at"]).utcoffset() is not None
    assert datetime.fromisoformat(payload["updated_at"]) >= datetime.fromisoformat(payload["started_at"])
    assert final.read_bytes() == before and not generic.exists()


def test_successful_leaf_has_distinct_running_then_complete_zero_attempt(monkeypatch, tmp_path):
    stock_io(monkeypatch, tmp_path, [snapshot()])
    written = []
    writer = api.save_cache_file
    def capture(path, data, metadata=None):
        if path.endswith("_attempt.json"):
            written.append(deepcopy(metadata))
        return writer(path, data, metadata)
    monkeypatch.setattr(api, "save_cache_file", capture)
    api._strategy_scan_wrapper(STRATEGIES[0], send_email=False)
    assert [item["status"] for item in written] == ["running", "complete"]
    assert written[0]["result_count"] is None and written[1]["result_count"] == 0
    assert written[0]["run_id"] == written[1]["run_id"]
    assert len(written[0]["run_id"]) == 32
    assert written[1]["diagnostics"]["coverage"] == "complete" and "error_code" not in written[1]


def test_incomplete_sweep_attempt_records_each_current_outcome_and_guard_not_smtp(monkeypatch, tmp_path):
    cache, before, state = _mock_sweep(monkeypatch, tmp_path, {
        STRATEGIES[0]: ScannerDataError("scan_data_unavailable"), STRATEGIES[1]: [_row("CURRENT")],
    })
    with pytest.raises(ScannerDataError):
        api._stock_strategy_alert_sweep_wrapper()
    payload = json.loads(_attempt_file(tmp_path, "stock_strategy_sweep").read_text())
    assert cache.read_bytes() == before and len(state["mail_calls"]) == 1
    assert payload["status"] == "error" and payload["result_count"] is None and payload["results"] == []
    assert payload["error_code"] == "scan_data_incomplete"
    assert payload["diagnostics"]["mail_status"] == "guarded"
    assert payload["diagnostics"]["current_result_count"] == 1
    assert payload["diagnostics"]["final_results"] is None
    assert set(payload["diagnostics"]["strategy_results"]) == set(CODES)
    assert "CURRENT" not in json.dumps(payload) and "STALE" not in json.dumps(payload)


@pytest.mark.parametrize("failure", ["raise", "false"])
def test_attempt_write_failure_cannot_change_actual_leaf_success_or_sweep_guard(monkeypatch, tmp_path, failure):
    final, generic, _ = stock_io(monkeypatch, tmp_path, [snapshot()])
    writer = api.save_cache_file
    def fail_only_attempt(path, data, metadata=None):
        if path.endswith("_attempt.json"):
            if failure == "raise":
                raise OSError("PRIVATE_STORAGE_DETAIL")
            return False
        return writer(path, data, metadata)
    monkeypatch.setattr(api, "save_cache_file", fail_only_attempt)
    assert api._strategy_scan_wrapper(STRATEGIES[0], send_email=False) == []
    assert final.exists() and generic.exists()
    cache, _, state = _mock_sweep(monkeypatch, tmp_path, {STRATEGIES[1]: [_row("CURRENT")]})
    api._stock_strategy_alert_sweep_wrapper()
    assert len(state["mail_calls"]) == 1 and json.loads(cache.read_text())["diagnostics"]["coverage"] == "complete"


@pytest.mark.parametrize("status", ["running", "complete", "error"])
def test_older_worker_cannot_overwrite_newer_attempt_at_any_status(tmp_path, status):
    old = api._new_stock_strategy_attempt(STRATEGIES[0])
    assert api._publish_stock_strategy_attempt(old, "running")
    new = api._new_stock_strategy_attempt(STRATEGIES[0])
    assert api._publish_stock_strategy_attempt(new, "running")
    before = _attempt_file(tmp_path).read_bytes()
    assert api._publish_stock_strategy_attempt(old, status, result_count=0, error="scan_data_invalid") is False
    assert _attempt_file(tmp_path).read_bytes() == before


@pytest.mark.parametrize("bad", [True, -1, 1.5, "7", 10**9 + 1])
def test_attempt_numeric_projection_rejects_invalid_counts_and_unknown_fields(tmp_path, bad):
    attempt = api._new_stock_strategy_attempt(STRATEGIES[0])
    attempt["PRIVATE_EXTRA"] = "PRIVATE_TOKEN"
    assert api._publish_stock_strategy_attempt(attempt, "error", diagnostics={
        "universe_count": bad, "checked": 0,
        "stage_counts": {"priced_snapshot": bad, "snapshot_universe": 10, "PRIVATE_SYMBOL": 7},
        "rejected": {"missing_price_or_prev_close": 2, "PRIVATE_REASON": 10},
        "rows": [{"Ticker": "PRIVATE_SYMBOL"}],
    }, error=RuntimeError("PRIVATE_EXCEPTION"))
    payload = json.loads(_attempt_file(tmp_path).read_text())
    assert "PRIVATE" not in json.dumps(payload)
    assert payload["diagnostics"]["checked"] == 0 and "universe_count" not in payload["diagnostics"]
    assert payload["diagnostics"]["stage_counts"] == {"snapshot_universe": 10}
    assert payload["error_code"] == "scan_failed"


@pytest.mark.parametrize("field,bad", [("schema_version", True), ("run_id", "PRIVATE"),
    ("started_at", "2026-09-09T20:00:00"), ("code_revision", "PRIVATE"),
    ("strategy_slug", "../PRIVATE"), ("attempt_kind", "PRIVATE")])
def test_invalid_attempt_metadata_is_best_effort_validation_failure_only(tmp_path, capsys, field, bad):
    attempt = api._new_stock_strategy_attempt(STRATEGIES[0])
    attempt[field] = bad
    assert api._publish_stock_strategy_attempt(attempt, "running") is False
    assert not _attempt_file(tmp_path).exists()
    assert capsys.readouterr().out.strip() == "[Strategy Attempt] validation_failed"


@pytest.mark.parametrize("kind,outcome", [("leaf", "zero"), ("leaf", "early_error"),
                                        ("sweep", "zero"), ("sweep", "partial_error")])
def test_actual_producer_lifecycle_roundtrips_through_standalone_collector(monkeypatch, tmp_path, kind, outcome):
    spec = importlib.util.spec_from_file_location(
        "standalone_sweep_collector", Path(__file__).parent / "scripts" / "collect_server_evidence.py",
    )
    collector = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(collector)
    seen = []
    writer = api.save_cache_file
    def capture(path, rows, metadata=None):
        result = writer(path, rows, metadata)
        if path.endswith("_attempt.json"):
            seen.append(collector.safe_strategy_attempt_summary(path, metadata["strategy_slug"]))
        return result
    monkeypatch.setattr(api, "save_cache_file", capture)
    if kind == "leaf":
        stock_io(monkeypatch, tmp_path, [snapshot()])
        if outcome == "early_error":
            def fail_early(*args):
                raise ScannerDataError("scan_provider_unauthorized")
            monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", fail_early)
        action = lambda: api._strategy_scan_wrapper(STRATEGIES[0], send_email=False)
    else:
        outcomes = {} if outcome == "zero" else {
            STRATEGIES[0]: ScannerDataError("scan_data_unavailable"), STRATEGIES[1]: [_row("PRIVATE_CURRENT")],
        }
        _mock_sweep(monkeypatch, tmp_path, outcomes)
        action = api._stock_strategy_alert_sweep_wrapper
    if outcome.endswith("error"):
        with pytest.raises(ScannerDataError):
            action()
    else:
        action()
    assert seen and all(item["available"] is True for item in seen)
    assert seen[0]["status"] == "running" and seen[0]["result_count"] is None
    assert seen[-1]["status"] == ("error" if outcome.endswith("error") else "complete")
    assert seen[-1]["result_count"] == (None if outcome.endswith("error") else 0)
    assert all(item["run_id"] == seen[0]["run_id"] for item in seen)
    assert "PRIVATE" not in json.dumps(seen)
    if kind == "sweep":
        assert len(seen) == 6  # Start, four per-strategy observations, final state.
        assert seen[-1]["mail_status"] == ("guarded" if outcome == "partial_error" else "no_results")
        assert seen[-1]["mail_status_semantics"] == "guard_execution_not_delivery_evidence"
