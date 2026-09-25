"""Offline runtime regressions: no provider, SMTP, orders or real sleeping."""
from copy import deepcopy
import inspect
import json
import threading
from types import SimpleNamespace

import pytest

import api
from modules import data_fetchers as fetchers
from modules import stock_scan_runtime as runtime
from modules.scanners import ScannerDataError
from test_stock_strategy_sweep_isolation import _mock_sweep, _row, STRATEGIES, CODES
from test_stock_bi_scan_data_outcomes import stock_io, snapshot


@pytest.fixture
def clock(monkeypatch):
    state = {"now": 100.0}
    def wait(seconds):
        state["now"] += seconds
    monkeypatch.setattr(runtime, "time", SimpleNamespace(monotonic=lambda: state["now"]))
    monkeypatch.setattr(runtime, "_PROGRESS", {})
    monkeypatch.setattr(runtime, "LEAF_WORK_SECONDS", 10)
    monkeypatch.setattr(runtime, "SWEEP_WORK_SECONDS", 25)
    state["wait"] = wait
    return state


def test_leaf_budget_and_outer_budget_are_distinct_and_cleanup(clock):
    with runtime.scope("sweep", sweep=True):
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.scope("first"):
                clock["now"] += 11
                runtime.checkpoint()
        # A timed-out leaf does not cancel the remaining siblings.
        with runtime.scope("second"):
            runtime.checkpoint()
        clock["now"] += 15
        with pytest.raises(runtime.ScanWorkTimeout):
            with runtime.scope("third"):
                runtime.checkpoint()
        # Mail ownership code is outside the cooperative analysis budget.
        runtime.checkpoint("mail_guard")
    assert runtime.current() is None
    assert runtime.progress("strategy_scan")["running"] is False


def test_budgeted_wait_expires_without_consuming_full_minute(clock):
    with runtime.scope("test"):
        with pytest.raises(runtime.ScanWorkTimeout):
            runtime.budgeted_wait(60, clock["wait"])
    assert clock["now"] == 110


@pytest.mark.parametrize("timeout,expected", [(15, 10), ((15, 3), (10, 3)), (None, 10)])
def test_network_timeout_is_never_larger_than_remaining_work(clock, timeout, expected):
    with runtime.scope("test"):
        assert runtime.request_timeout(timeout) == expected


def test_http_returns_after_deadline_are_closed_and_rejected(clock, monkeypatch):
    monkeypatch.setattr(fetchers, "_acquire_shared_polygon_token", lambda: None)
    monkeypatch.setattr(fetchers, "_api_call_count", 0)
    monkeypatch.setattr(fetchers, "_last_api_call", 0)
    closed = []
    def request(*args, **kwargs):
        clock["now"] += 11
        return SimpleNamespace(close=lambda: closed.append(True))
    monkeypatch.setattr(fetchers.requests, "get", request)
    with runtime.scope("test"), pytest.raises(runtime.ScanWorkTimeout):
        fetchers.rate_limited_get("https://offline.invalid")
    assert closed == [True]


def test_non_stock_requests_keep_existing_timeout_and_no_budget(clock, monkeypatch):
    monkeypatch.setattr(fetchers, "_acquire_shared_polygon_token", lambda: None)
    monkeypatch.setattr(fetchers, "_api_call_count", 0)
    monkeypatch.setattr(fetchers, "_last_api_call", 0)
    seen = []
    monkeypatch.setattr(fetchers.requests, "get", lambda *a, **k: seen.append(k) or "ok")
    assert fetchers.rate_limited_get("https://offline.invalid", timeout=(4, 7)) == "ok"
    assert seen[0]["timeout"] == (4, 7)


def test_shared_limiter_timeout_does_not_disable_limiter_or_allow_http(clock, monkeypatch):
    monkeypatch.setattr(fetchers, "_shared_budget_enabled", lambda: True)
    monkeypatch.setattr(fetchers, "_shared_budget_failed", False)
    monkeypatch.setattr(fetchers, "_fcntl", object())
    monkeypatch.setattr(fetchers, "_shared_budget_try_consume", lambda budget: (False, 60))
    monkeypatch.setattr(fetchers, "time", SimpleNamespace(time=lambda: clock["now"], sleep=clock["wait"]))
    monkeypatch.setattr(fetchers.requests, "get", lambda *a, **k: pytest.fail("No HTTP after work budget"))
    with runtime.scope("test"), pytest.raises(runtime.ScanWorkTimeout):
        fetchers.rate_limited_get("https://offline.invalid")
    assert fetchers._shared_budget_failed is False


def test_cache_is_bounded_immutable_and_does_not_cross_runs(clock, monkeypatch):
    monkeypatch.setattr(runtime, "CACHE_BYTES", 120)
    original = {"bars": [{"close": 10.0}]}
    with runtime.scope("first"):
        runtime.cache_put("one", original)
        original["bars"][0]["close"] = 99
        result = runtime.cache_get("one")
        assert result["bars"][0]["close"] == 10
        result["bars"][0]["close"] = 7
        assert runtime.cache_get("one")["bars"][0]["close"] == 10
        for n in range(100):
            runtime.cache_put(n, {"values": list(range(n, n+20))})
            assert runtime.current()["root"]["cache_bytes"] <= 120
    with runtime.scope("second"):
        assert runtime.cache_get("one") is None


def test_context_and_cache_are_not_shared_with_other_worker_threads(clock):
    seen = []
    with runtime.scope("first"):
        runtime.cache_put("private", [1])
        thread = threading.Thread(target=lambda: seen.append((runtime.current(), runtime.cache_get("private"))))
        thread.start()
        thread.join(timeout=2)
    assert seen == [(None, None)]


def test_leaf_decorator_preserves_keyword_call_signature(clock):
    @runtime.bounded_leaf
    def leaf(strategy_name, send_email=True):
        assert runtime.current()["strategy"] == "momentum_breakout_long"
        return strategy_name, send_email
    assert leaf(strategy_name="Momentum Breakout Long", send_email=False) == ("Momentum Breakout Long", False)
    assert runtime.current() is None


def _history(count):
    return [{"time": 1700000000 + i*86400, "open": 99., "high": 102., "low": 98.,
             "close": 100., "volume": 1000000} for i in range(count)]


def test_same_complete_history_reused_across_leaf_and_special_filter(clock, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **k: calls.append(k) or _history(240))
    with runtime.scope("sweep", sweep=True):
        with runtime.scope("first") as state:
            state["analysis_session"] = "2026-09-15"
            first = api._fetch_strategy_daily_history("TEST", 70, {}, True)
        first[0]["close"] = 999
        with runtime.scope("second") as state:
            state["analysis_session"] = "2026-09-15"
            second = api._fetch_strategy_daily_history("TEST", 180, {}, True)
            assert len(second) == 240 and second[0]["close"] == 100
        assert len(calls) == 1
        with runtime.scope("changed_session") as state:
            state["analysis_session"] = "2026-09-16"
            api._fetch_strategy_daily_history("TEST", 70, {}, True)
        assert len(calls) == 2


def test_short_history_never_satisfies_larger_lookback_or_strict_source(clock, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "fetch_ohlcv_for_chart", lambda *a, **k: calls.append(k) or _history(75 if len(calls) == 1 else 240))
    with runtime.scope("first") as state:
        state["analysis_session"] = "2026-09-15"
        assert len(api._fetch_strategy_daily_history("TEST", 70, {}, False)) == 75
        assert len(api._fetch_strategy_daily_history("TEST", 180, {}, False)) == 240
        api._fetch_strategy_daily_history("TEST", 70, {}, True)
    assert len(calls) == 3


def test_grouped_source_shared_only_within_same_completed_session(clock, monkeypatch):
    from test_stock_starter_swing import payload
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")
    sessions = ["2026-09-15", "2026-09-14"]
    monkeypatch.setattr(api.stock_swing, "completed_sessions", lambda *a: sessions)
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=200, json=lambda: payload(url[-10:]))
    monkeypatch.setattr(api, "rate_limited_get", request)
    with runtime.scope("sweep", sweep=True):
        a = api._fetch_strategy_snapshot_universe(STRATEGIES[0])
        a[0]["day"]["c"] = 999
        b = api._fetch_strategy_snapshot_universe(STRATEGIES[1])
        assert b[0]["day"]["c"] == 100
        assert len(calls) == 2
        sessions[:] = ["2026-09-16", "2026-09-15"]
        api._fetch_strategy_snapshot_universe(STRATEGIES[2])
    assert len(calls) == 4


def test_expired_real_leaf_keeps_last_good_cache_and_reports_timeout(clock, monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    final, generic, before = stock_io(monkeypatch, tmp_path, [snapshot()])
    def feed(*a):
        clock["now"] += 11
        return [snapshot()]
    monkeypatch.setattr(api, "_fetch_strategy_snapshot_universe", feed)
    with pytest.raises(runtime.ScanWorkTimeout):
        api._strategy_scan_wrapper(STRATEGIES[0], send_email=False)
    assert final.read_bytes() == before
    assert not generic.exists()
    attempt = json.loads((tmp_path / "stock_strategy_momentum_breakout_long_attempt.json").read_text())
    assert attempt["status"] == "error" and attempt["error_code"] == "scan_timeout"
    assert attempt["result_count"] is None


def test_slow_cup_cannot_hold_ready_siblings_or_discard_their_candidate_reserve(clock, monkeypatch, tmp_path):
    monkeypatch.setenv("ALPHA_RUNTIME_TMP_DIR", str(tmp_path))
    monkeypatch.setattr(api.time, "sleep", lambda *a: None)
    cache, before, recorded = _mock_sweep(monkeypatch, tmp_path, {})
    calls = []
    @runtime.bounded_leaf
    def leaf(name, **kwargs):
        assert kwargs == {"send_email": False, "publish_generic_cache": False}
        calls.append(name)
        if name == STRATEGIES[-1]:
            clock["now"] += 11
        runtime.checkpoint()
        return [_row(f"{name}:{i}", score=100-i) for i in range(40)]
    monkeypatch.setattr(api, "_strategy_scan_wrapper", leaf)
    with pytest.raises(ScannerDataError) as caught:
        api._stock_strategy_alert_sweep_wrapper()
    assert calls == list(STRATEGIES)
    assert len(recorded["mail_calls"]) == 1
    rows = recorded["mail_calls"][0][1]
    assert len(rows) == 120 and all(row["Strategy"] != STRATEGIES[-1] for row in rows)
    assert caught.value.diagnostics["strategy_results"][CODES[-1]]["error_code"] == "scan_timeout"
    assert caught.value.diagnostics["mail_status"] == "guarded"
    assert caught.value.diagnostics["coverage"] == "incomplete"
    assert cache.read_bytes() == before


def test_automatic_and_manual_stock_workers_cannot_overlap(monkeypatch):
    monkeypatch.setattr(api, "_scan_status", {
        "strategy_scan": {"running": True}, "strat_test": {"running": False},
    })
    monkeypatch.setattr(api, "_scan_threads", {})
    assert api._run_scan_safe("strat_test", lambda: pytest.fail("overlap")) is False
    api._scan_status["strategy_scan"]["running"] = False
    api._scan_status["strat_test"]["running"] = True
    assert api._run_scan_safe("strategy_scan", lambda: pytest.fail("overlap")) is False


def test_starter_has_no_redundant_ten_minute_opening_sweep(monkeypatch):
    monkeypatch.setattr(api, "_opening_window_active", lambda *a: True)
    monkeypatch.setitem(api._scan_status, "strategy_scan", {"interval_min": 60})
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")
    assert api._effective_scan_interval_min("strategy_scan") == 60
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "realtime")
    assert api._effective_scan_interval_min("strategy_scan") == 10


def test_strategy_is_scheduled_as_heavy_and_watchdog_limits_not_raised():
    source = inspect.getsource(api._scheduler_loop)
    light, heavy = source.split("heavy_scans =", 1)
    assert '("strategy_scan", _stock_strategy_alert_sweep_wrapper)' not in light
    assert '("strategy_scan", _stock_strategy_alert_sweep_wrapper)' in heavy
    assert api._SCAN_TIMEOUTS["strategy_scan"] == 35
    assert api._stuck_hard_cap_sec("strategy_scan") == 105*60
