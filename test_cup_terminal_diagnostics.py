"""Bounded Cup terminal telemetry; no acceptance, mail, or window-count changes."""
from copy import deepcopy
import json

import pytest

import api
from test_collect_server_evidence import _attempt_payload, collector
from test_cup_handle_audit_fixes import _cup_handle_bars, _mk_candidate, _v4_cup_bars
from test_cup_runtime_admission import NAME, _cup_fixture


@pytest.mark.parametrize("case,expected", [
    ("accepted", "special_filter_accepted"),
    ("empty", "invalid_pattern_data"),
    ("bad_confirmation", "invalid_pattern_data"),
    ("short_history", "insufficient_completed_history"),
    ("flat", "pattern_unconfirmed"),
    ("below_rim", "breakout_close_unconfirmed"),
    ("chased", "entry_extension_rejected"),
    ("low_volume", "breakout_volume_unconfirmed"),
    ("handle_distribution", "handle_volume_unconfirmed"),
])
def test_detector_observation_preserves_complete_result_and_inputs(case, expected):
    bars, price = _cup_handle_bars(), 101.7
    if case == "empty":
        bars = []
    elif case == "bad_confirmation":
        bars[-1]["high"] = 0
    elif case == "short_history":
        bars = bars[-60:]
    elif case == "flat":
        bars = [{"open": 100., "high": 101., "low": 99., "close": 100.,
                 "volume": 1_000_000.} for _ in range(100)]
    elif case == "below_rim":
        bars, price = _v4_cup_bars(98.), 98.
    elif case == "chased":
        price = 112.
    elif case == "low_volume":
        bars = _cup_handle_bars(last_volume=200_000)
    elif case == "handle_distribution":
        bars = _cup_handle_bars(handle_vol=2_000_000, last_volume=6_000_000)
    original = deepcopy(bars)
    expected_result = api._detect_cup_handle_breakout(bars, current_price=price)
    diagnostic = {"reason": "PRIVATE_OLD_REASON"}
    observed = api._detect_cup_handle_breakout(bars, current_price=price, diagnostics=diagnostic)
    assert observed == expected_result
    assert bool(observed) is (case == "accepted")
    assert bars == original
    assert diagnostic == {"reason": expected}


@pytest.mark.parametrize("price", [True, float("nan"), float("inf"), 0, "bad"])
def test_invalid_price_has_terminal_label_without_new_acceptance(price):
    diagnostic = {}
    bars = _cup_handle_bars()
    assert api._detect_cup_handle_breakout(bars, price) is None
    assert api._detect_cup_handle_breakout(bars, price, diagnostics=diagnostic) is None
    assert diagnostic == {"reason": "invalid_current_price"}


@pytest.mark.parametrize("stage,expected", [
    ("geometry", "trade_plan_unconfirmed"),
    ("score", "pattern_score_below_threshold"),
])
def test_deepest_rejecting_stage_is_one_reason_not_split_frequencies(monkeypatch, stage, expected):
    if stage == "geometry":
        monkeypatch.setattr(api, "trade_geometry", lambda *a, **kw: {"valid": False})
    else:
        monkeypatch.setattr(api, "_score_cup_handle_breakout_quality", lambda **kw: (79, {}))
    diagnostic = {}
    bars = _cup_handle_bars()
    assert api._detect_cup_handle_breakout(bars, 101.7) is None
    assert api._detect_cup_handle_breakout(bars, 101.7, diagnostics=diagnostic) is None
    assert diagnostic == {"reason": expected}


@pytest.mark.parametrize("case,expected", [
    ("accepted", "special_filter_accepted"),
    ("liquidity", "liquidity_below_floor"),
    ("blended_score", "blended_score_below_threshold"),
    ("entry", "entry_quality_rejected"),
])
def test_filter_optional_telemetry_preserves_rows_and_terminal_stage(monkeypatch, case, expected):
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **kw: {"allowed": False, "session": "UNKNOWN"})
    monkeypatch.setattr(api, "_fetch_long_latest_intraday_state", lambda *a: {})
    monkeypatch.setattr(api, "_long_entry_rule_reasons", lambda row: ["fixture"] if case == "entry" else [])
    candidate = _mk_candidate()
    if case == "liquidity":
        candidate["Dollar_Volume"] = 1
    elif case == "blended_score":
        candidate["base_score"] = candidate["score"] = 0
    original = deepcopy(candidate)
    plain = api._apply_cup_handle_strategy_filter(candidate, {})
    diagnostic = {}
    observed = api._apply_cup_handle_strategy_filter(candidate, {}, diagnostics=diagnostic)
    assert observed == plain
    assert bool(observed) is (case == "accepted")
    assert diagnostic == {"reason": expected}
    assert candidate == original
    if observed:
        assert not any("diagnostic" in key or "terminal" in key for key in observed)
        assert observed["trade_signal"] == "BEOBACHTEN"  # Accepted row is not a trade-mail claim.


def test_complete_163_to_zero_run_explains_pattern_loss_not_native_builder(monkeypatch):
    real_cup_filter = api._apply_cup_handle_strategy_filter
    state = _cup_fixture(monkeypatch, count=163)
    monkeypatch.setattr(api, "_apply_cup_handle_strategy_filter", real_cup_filter)
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    diagnostic = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diagnostic["plan_build_counts"] == {"fixture_no_structure": 163}
    assert diagnostic["cup_terminal_counts"] == {"pattern_unconfirmed": 163}
    assert diagnostic["special_filter_checked_count"] == 163
    assert diagnostic["special_filter_unexamined_count"] == 0
    assert sum(diagnostic["cup_terminal_counts"].values()) == diagnostic["special_filter_checked_count"]
    assert diagnostic["cup_terminal_count_semantics"] == api._CUP_TERMINAL_COUNT_SEMANTICS


def test_missing_special_history_is_one_terminal_outcome(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    original = api._fetch_strategy_daily_history
    monkeypatch.setattr(api, "_fetch_strategy_daily_history", lambda symbol, minimum, *args:
                        [] if minimum == 180 else original(symbol, minimum, *args))
    assert api._strategy_scan_wrapper(NAME, send_email=False) == []
    diagnostic = state["writes"][0][1]["metadata"]["diagnostics"]
    assert diagnostic["cup_terminal_counts"] == {"insufficient_completed_history": 3}
    assert diagnostic["special_filter_checked_count"] == 3


def test_interrupted_candidate_has_no_terminal_success_or_rejection(monkeypatch):
    state = _cup_fixture(monkeypatch, count=3)
    calls = []

    def fail_third(symbol, **kwargs):
        calls.append(symbol)
        if len(calls) == 3:
            raise api.stock_scan_runtime.ScanWorkTimeout()
        return []

    monkeypatch.setattr(api, "_fetch_recent_stock_4h_bars", fail_third)
    with pytest.raises(api.stock_scan_runtime.ScanWorkTimeout):
        api._strategy_scan_wrapper(NAME, send_email=False)
    diagnostic = state["attempts"][-1][1]["diagnostics"]
    assert diagnostic["cup_terminal_counts"] == {"special_filter_accepted": 2}
    assert diagnostic["special_filter_checked_count"] == 2
    assert diagnostic["special_filter_unexamined_count"] == 1
    assert diagnostic["coverage"] == "incomplete"
    assert not state["writes"]


def test_missing_symbol_is_counted_and_empty_run_is_explicit(monkeypatch):
    monkeypatch.setattr(api, "_scan_control_point", lambda: None)
    monkeypatch.setattr(api.stock_scan_runtime, "checkpoint", lambda *a, **kw: None)
    for rows, expected in [([], {}), ([{}], {"missing_symbol": 1})]:
        diagnostic = {}
        assert api._apply_special_strategy_post_filter(rows, {"needs_cup_handle": True}, NAME, diagnostic) == []
        assert diagnostic["cup_terminal_counts"] == expected
        assert diagnostic["special_filter_checked_count"] == len(rows)
        assert diagnostic["special_filter_unexamined_count"] == 0


@pytest.mark.parametrize("kind", ["cache", "attempt"])
def test_export_uses_exact_mirrored_reason_allowlist_and_own_semantics(tmp_path, kind):
    assert collector.CUP_TERMINAL_REASONS == api._CUP_TERMINAL_REASONS
    assert collector.CUP_TERMINAL_COUNT_SEMANTICS == api._CUP_TERMINAL_COUNT_SEMANTICS
    payload = {"results": [], "diagnostics": {}} if kind == "cache" else _attempt_payload("cup_and_handle_breakout")
    payload["diagnostics"].update(cup_terminal_counts={
        "pattern_unconfirmed": 163, "special_filter_accepted": 0,
        "PRIVATE_TICKER": 12, "entry_quality_rejected": True,
        "liquidity_below_floor": -1, "trade_plan_unconfirmed": "1",
    }, cup_terminal_count_semantics="PRIVATE_BODY")
    path = tmp_path / "cup.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    observed = (collector.safe_cache_summary(path) if kind == "cache"
                else collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout"))
    assert observed["available"] is True
    assert observed["cup_terminal_counts"] == {
        "pattern_unconfirmed": 163, "special_filter_accepted": 0, "_omitted_categories": 4,
    }
    assert observed["cup_terminal_count_semantics"] == api._CUP_TERMINAL_COUNT_SEMANTICS
    assert "PRIVATE" not in json.dumps(observed)
    app_projection = api._stock_strategy_attempt_diagnostics(payload["diagnostics"], sweep=False)
    assert app_projection["cup_terminal_counts"] == {"pattern_unconfirmed": 163, "special_filter_accepted": 0}
    assert "PRIVATE" not in json.dumps(app_projection)


def test_old_missing_cup_telemetry_remains_unknown(tmp_path):
    payload = _attempt_payload("cup_and_handle_breakout")
    path = tmp_path / "old.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "cup_and_handle_breakout")
    assert result["available"] is True
    assert "cup_terminal_counts" not in result
    assert "cup_terminal_counts" not in api._stock_strategy_attempt_diagnostics({}, sweep=False)


@pytest.mark.parametrize("status", ["complete", "error"])
def test_sweep_export_preserves_one_timeout_retry_without_raw_errors(tmp_path, status):
    payload = _attempt_payload("stock_strategy_sweep")
    child = {"status": status, "result_count": 0 if status == "complete" else None,
             "error_code": None if status == "complete" else "scan_timeout",
             "aggregate_candidate_count": 0 if status == "complete" else None,
             "timeout_retry_count": 1, "initial_error_code": "scan_timeout",
             "initial_error": "PRIVATE_BODY"}
    payload["diagnostics"].update(strategy_results={"cup_and_handle_breakout": child},
                                  timeout_retries_attempted=1,
                                  timeout_retries_recovered=int(status == "complete"))
    path = tmp_path / "retry.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert result["available"] is True
    assert result["strategy_results"]["cup_and_handle_breakout"]["timeout_retry_count"] == 1
    assert result["strategy_results"]["cup_and_handle_breakout"]["initial_error_code"] == "scan_timeout"
    assert result["numeric_diagnostics"]["timeout_retries_attempted"] == 1
    assert result["numeric_diagnostics"]["timeout_retries_recovered"] == int(status == "complete")
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("extra", [
    {"timeout_retry_count": True, "initial_error_code": "scan_timeout"},
    {"timeout_retry_count": 0, "initial_error_code": "scan_timeout"},
    {"timeout_retry_count": 2, "initial_error_code": "scan_timeout"},
    {"timeout_retry_count": "1", "initial_error_code": "scan_timeout"},
    {"timeout_retry_count": 1, "initial_error_code": "PRIVATE_CODE"},
    {"timeout_retry_count": 1}, {"initial_error_code": "scan_timeout"},
])
def test_sweep_export_rejects_untrusted_retry_metadata(tmp_path, extra):
    payload = _attempt_payload("stock_strategy_sweep")
    payload["diagnostics"]["strategy_results"] = {"cup_and_handle_breakout": {
        "status": "complete", "result_count": 0, "aggregate_candidate_count": 0, **extra,
    }}
    path = tmp_path / "bad-retry.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert result == {"available": False, "reason": "invalid_or_unreadable"}
    assert "PRIVATE" not in json.dumps(result)


@pytest.mark.parametrize("bad", [True, -1, 1.5, "1", None, 2**63])
def test_sweep_export_omits_invalid_retry_counters(tmp_path, bad):
    payload = _attempt_payload("stock_strategy_sweep")
    payload["diagnostics"].update(timeout_retries_attempted=bad, timeout_retries_recovered=bad)
    path = tmp_path / "bad-count.json"
    path.write_text(json.dumps(payload), encoding="utf8")
    result = collector.safe_strategy_attempt_summary(path, "stock_strategy_sweep")
    assert result["available"] is True
    assert "timeout_retries_attempted" not in result["numeric_diagnostics"]
    assert "timeout_retries_recovered" not in result["numeric_diagnostics"]
