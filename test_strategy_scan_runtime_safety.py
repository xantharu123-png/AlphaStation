import inspect
import json
import threading
import time
from pathlib import Path

import api
from modules.trade_levels import trade_geometry


def _wait_for_scan(scan_key: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with api._scan_lock:
            state = dict(api._scan_status.get(scan_key, {}))
        if state and not state.get("running"):
            return state
        time.sleep(0.01)
    raise AssertionError(f"scan {scan_key} did not finish within {timeout}s")


def test_generic_strategy_wrappers_use_their_defined_turnover_variables():
    stock_source = inspect.getsource(api._strategy_scan_wrapper)
    crypto_source = inspect.getsource(api._crypto_strategy_scan_wrapper)

    assert "rvol_min, rvol_max = filters.get" in stock_source
    assert '"rvol": [rvol_min, rvol_max]' in stock_source
    assert "turnover_min, turnover_max = filters.get" in crypto_source
    assert "turnover_min <= turnover_intensity <= turnover_max" in crypto_source


def test_failed_scan_keeps_last_success_and_exposes_error():
    scan_key = "unit_strategy_failure"
    previous_success = "2026-01-02T03:04:05"

    with api._scan_lock:
        api._scan_status[scan_key] = {
            "running": False,
            "last_run": previous_success,
            "next_run": None,
        }

    def fail_scan():
        raise RuntimeError("unit scan failure")

    try:
        api._run_scan_safe(scan_key, fail_scan)
        state = _wait_for_scan(scan_key)

        assert state["last_run"] == previous_success
        assert state["last_attempt_at"]
        assert state["last_error"] == "RuntimeError: unit scan failure"
    finally:
        with api._scan_lock:
            api._scan_status.pop(scan_key, None)


def test_successful_scan_refreshes_last_run_and_clears_old_error():
    scan_key = "unit_strategy_success"

    with api._scan_lock:
        api._scan_status[scan_key] = {
            "running": False,
            "last_run": None,
            "next_run": None,
            "last_error": "old failure",
        }

    try:
        api._run_scan_safe(scan_key, lambda: None)
        state = _wait_for_scan(scan_key)

        assert state["last_run"]
        assert state["last_attempt_at"]
        assert "last_error" not in state
    finally:
        with api._scan_lock:
            api._scan_status.pop(scan_key, None)


def test_timed_out_worker_is_never_started_twice():
    scan_key = "unit_no_overlap"
    worker_started = threading.Event()
    release_worker = threading.Event()
    calls = []

    with api._scan_lock:
        api._scan_status[scan_key] = {
            "running": False,
            "last_run": None,
            "next_run": None,
        }

    def slow_scan():
        calls.append(time.time())
        worker_started.set()
        release_worker.wait(2.0)

    try:
        assert api._run_scan_safe(scan_key, slow_scan, timeout_min=0.01) is True
        assert worker_started.wait(1.0)
        with api._scan_lock:
            api._scan_status[scan_key]["_started_at"] = time.time() - 2.0

        assert api._run_scan_safe(scan_key, slow_scan, timeout_min=0.01) is False
        assert len(calls) == 1
        with api._scan_lock:
            assert "kein Parallelstart" in api._scan_status[scan_key]["last_error"]
    finally:
        release_worker.set()
        _wait_for_scan(scan_key)
        with api._scan_lock:
            api._scan_status.pop(scan_key, None)
            api._scan_threads.pop(scan_key, None)


def test_cache_health_distinguishes_running_from_stuck(tmp_path, monkeypatch):
    scan_key = "unit_runtime_health"
    cache_path = tmp_path / "missing.json"
    monkeypatch.setitem(api.SCAN_CACHE_MAP, scan_key, str(cache_path))
    monkeypatch.setitem(api._SCAN_TIMEOUTS, scan_key, 1)

    running = {
        "running": True,
        "_started_at": time.time() - 5,
        "interval_min": 5,
    }
    stuck = {
        "running": True,
        "_started_at": time.time() - 65,
        "interval_min": 5,
    }

    running_health = api._scan_cache_health(scan_key, running)
    stuck_health = api._scan_cache_health(scan_key, stuck)

    assert running_health["cache_health"] == "running"
    assert running_health["cache_data_health"] == "missing"
    assert stuck_health["cache_health"] == "stuck"
    assert stuck_health["timeout_exceeded"] is True


def test_email_pipeline_summary_separates_send_skip_and_error(monkeypatch):
    monkeypatch.setattr(api, "_EMAIL_SEND_LOG", [])

    api._record_email_event("Stock Signal", "sent")
    api._record_email_event("Crypto Signal", "skipped", "no_active_setup")
    api._record_email_event("SMTP", "error", "connection_failed")

    summary = api._email_pipeline_summary()

    assert summary["sent"] == 1
    assert summary["skipped"] == 1
    assert summary["errors"] == 1
    assert summary["last_event"]["reason"] == "connection_failed"


def test_scheduled_scan_without_fresh_cache_is_a_failure(tmp_path, monkeypatch):
    scan_key = "unit_missing_cache_publish"
    cache_path = tmp_path / "scan.json"
    cache_path.write_text(json.dumps({"cached_at": "2026-01-01T00:00:00", "results": []}), encoding="utf-8")
    monkeypatch.setitem(api.SCAN_CACHE_MAP, scan_key, str(cache_path))

    with api._scan_lock:
        api._scan_status[scan_key] = {"running": False, "last_run": "old", "next_run": None}

    try:
        api._run_scan_safe(scan_key, lambda: None)
        state = _wait_for_scan(scan_key)

        assert state["last_run"] == "old"
        assert "without publishing a fresh cache" in state["last_error"]
    finally:
        with api._scan_lock:
            api._scan_status.pop(scan_key, None)


def test_scheduled_scan_with_fresh_readable_cache_succeeds(tmp_path, monkeypatch):
    scan_key = "unit_fresh_cache_publish"
    cache_path = tmp_path / "scan.json"
    cache_path.write_text(json.dumps({"cached_at": "2026-01-01T00:00:00", "results": []}), encoding="utf-8")
    monkeypatch.setitem(api.SCAN_CACHE_MAP, scan_key, str(cache_path))

    with api._scan_lock:
        api._scan_status[scan_key] = {"running": False, "last_run": None, "next_run": None}

    def publish_scan():
        api.save_cache_file(str(cache_path), [{"ticker": "OK"}])

    try:
        api._run_scan_safe(scan_key, publish_scan)
        state = _wait_for_scan(scan_key)

        assert state["last_run"]
        assert "last_error" not in state
    finally:
        with api._scan_lock:
            api._scan_status.pop(scan_key, None)


def test_frontend_hides_technical_diagnostics_and_surfaces_scan_failures():
    frontend = (Path(__file__).parent / "frontend" / "index.html").read_text(encoding="utf-8")

    assert "<b>Diagnose:</b>" not in frontend
    assert "Datenstand:" in frontend
    assert "data.scan_error && !data.scan_running" in frontend


def test_orb_sort_preserves_zero_r_distance(monkeypatch):
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *_args: rows)
    payload = {
        "breakouts": [
            {
                "ticker": "FAR",
                "trade_decision": "WAIT_FOR_TRIGGER",
                "entry_quality": "GOOD",
                "distance_to_entry_r": 0.2,
                "score": 80,
                "trade_health_score": 80,
                "live_rr_ratio": 2.0,
            },
            {
                "ticker": "AT_ENTRY",
                "trade_decision": "WAIT_FOR_TRIGGER",
                "entry_quality": "GOOD",
                "distance_to_entry_r": 0.0,
                "score": 80,
                "trade_health_score": 80,
                "live_rr_ratio": 2.0,
            },
        ]
    }

    result = api._decorate_orb_results([payload], 0)

    assert [row["ticker"] for row in result[0]["breakouts"]] == ["AT_ENTRY", "FAR"]


def _canonical_trade_row() -> dict:
    return {
        "ticker": "SYNC",
        "direction": "LONG",
        "current_price": 10.0,
        "entry": 10.0,
        "stop_loss": 9.5,
        "tp1": 10.8,
        "tp2": 11.4,
        "trade_decision": "TRADEABLE",
        "trade_decision_label": "Tradeable",
        "trade_signal": "JETZT_TRADEN",
        "trade_action": "LONG_NOW",
        "entry_status": "JETZT_TRADEN",
        "scanner_decision": "TRADE_NOW",
        "scanner_decision_label": "Jetzt traden",
        "trade_health": {"decision": "TRADEABLE", "decision_label": "Tradeable"},
        "trade_setup": {
            "direction": "LONG",
            "entry": 10.0,
            "stop_loss": 9.5,
            "tp1": 10.8,
            "tp2": 11.4,
            "trade_action": "LONG_NOW",
        },
        "risk_flags": [],
        "risk_reasons": [],
    }


def test_canonical_state_makes_scanner_no_trade_override_tradeable_health():
    row = _canonical_trade_row()
    row.update({"scanner_decision": "NO_TRADE", "scanner_decision_label": "Fakeout-Risiko"})

    api._apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_decision"] == "NO_TRADE"
    assert row["trade_signal"] == "NICHT_TRADEN"
    assert row["trade_action"] == "NO_TRADE"
    assert row["trade_health"]["decision"] == "NO_TRADE"
    assert row["trade_setup"]["trade_decision"] == "NO_TRADE"
    assert row["trade_setup"]["trade_action"] == "NO_TRADE"


def test_canonical_state_makes_scanner_wait_override_tradeable_health():
    row = _canonical_trade_row()
    row.update({"scanner_decision": "WAIT_TRIGGER", "scanner_decision_label": "Breakout bestaetigen"})

    api._apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_decision"] == "WAIT_FOR_TRIGGER"
    assert row["trade_signal"] == "WARTEN"
    assert row["trade_action"] == "WAIT_FOR_TRIGGER"
    assert row["trade_health"]["decision"] == "WAIT_FOR_TRIGGER"
    assert row["trade_setup"]["entry_status"] == "WAIT_FOR_TRIGGER"


def test_canonical_state_makes_active_barrier_override_tradeable_layers():
    row = _canonical_trade_row()
    row.update({
        "barrier_gate_active": True,
        "trade_decision_label": "Resistance erst reclaimen",
    })

    api._apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_decision"] == "WAIT_FOR_TRIGGER"
    assert row["trade_decision_source"] == "barrier_gate"
    assert row["trade_signal"] == "WARTEN"


def test_canonical_state_keeps_only_fully_confirmed_native_plan_tradeable():
    row = _canonical_trade_row()

    api._apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_decision"] == "TRADEABLE"
    assert row["trade_decision_source"] == "all_gates"
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["trade_action"] == "LONG_NOW"
    assert row["trade_health"]["decision"] == "TRADEABLE"
    assert row["trade_setup"]["trade_signal"] == "JETZT_TRADEN"


def test_canonical_state_never_promotes_tradeable_health_without_active_intent():
    row = _canonical_trade_row()
    for key in ("scanner_decision", "scanner_decision_label", "trade_signal", "trade_action", "entry_status"):
        row.pop(key, None)

    api._apply_trade_health_final_signal(row, "stock_strategy")

    assert row["trade_decision"] == "WATCH_ONLY"
    assert row["trade_signal"] == "BEOBACHTEN"
    assert row["trade_action"] == "WATCH_ONLY"
    assert row["trade_health"]["decision"] == "WATCH_ONLY"


def test_canonical_state_keeps_confirmed_crypto_trigger_tradeable():
    row = _canonical_trade_row()
    row.update({
        "scanner_decision": "",
        "trade_signal": "",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "LONG_TRIGGER",
        "execution_trigger_ok": True,
        "crypto_entry_ok": True,
        "alertable_crypto": True,
    })

    api._apply_trade_health_final_signal(row, "early_movers")

    assert row["trade_decision"] == "TRADEABLE"
    assert row["trade_decision_source"] == "all_gates"
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["trade_action"] == "LONG_NOW"
    assert row["execution_trigger_ok"] is True


def test_canonical_state_does_not_promote_unconfirmed_crypto_trigger():
    row = _canonical_trade_row()
    row.update({
        "scanner_decision": "",
        "trade_signal": "",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "LONG_TRIGGER",
        "execution_trigger_ok": False,
        "crypto_entry_ok": False,
        "alertable_crypto": False,
    })

    api._apply_trade_health_final_signal(row, "early_movers")

    assert row["trade_decision"] == "WATCH_ONLY"
    assert row["trade_decision_source"] == "missing_active_intent"
    assert row["trade_signal"] == "BEOBACHTEN"
    assert row["trade_action"] == "WATCH_ONLY"


def test_confirmation_status_is_not_misread_as_missing_trigger():
    row = _canonical_trade_row()
    row.update({
        "scanner_decision": "",
        "trade_signal": "",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "TRIGGER_OK",
        "execution_trigger_ok": True,
        "crypto_entry_ok": True,
        "alertable_crypto": True,
    })

    api._apply_trade_health_final_signal(row, "early_movers")

    assert row["trade_decision"] == "TRADEABLE"
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["trade_action"] == "LONG_NOW"


def test_new_listing_publishes_cache_before_sending_alerts(monkeypatch):
    events = []
    payload = {"new_listings": [{"symbol": "SAFE"}]}

    monkeypatch.setattr(api, "HAS_NEW_LISTING_SCANNER", True)
    monkeypatch.setattr(api, "seed_instrument_cache", lambda: events.append("seed"))
    monkeypatch.setattr(api, "run_new_listing_scanner", lambda: payload)
    monkeypatch.setattr(
        api,
        "_flatten_new_listing_pipeline_results",
        lambda _payload: [{"ticker": "SAFE"}],
    )
    monkeypatch.setattr(api, "save_cache_file", lambda *args, **kwargs: events.append("publish"))
    monkeypatch.setattr(
        api,
        "_send_new_listing_pipeline_alerts",
        lambda _payload: events.append("mail"),
    )

    api._new_listing_wrapper()

    assert events == ["seed", "publish", "mail"]


def test_penny_scanner_publishes_final_state_before_trade_mails():
    source = inspect.getsource(api._penny_stock_scanner_wrapper)
    publish_index = source.index("save_cache_file(PENNY_STOCKS_CACHE")

    assert publish_index < source.index("_penny_buy_email(")
    assert publish_index < source.index("_penny_exit_email(")


def test_trade_geometry_rejects_duplicate_or_reversed_targets():
    assert trade_geometry(100, 95, 110, 110, "LONG")["valid"] is False
    assert trade_geometry(100, 95, 110, 105, "LONG")["valid"] is False
    assert trade_geometry(100, 105, 90, 90, "SHORT")["valid"] is False
    assert trade_geometry(100, 105, 90, 95, "SHORT")["valid"] is False


def test_trade_geometry_rejects_non_finite_and_non_positive_levels():
    assert trade_geometry(float("nan"), 95, 110, 120, "LONG")["valid"] is False
    assert trade_geometry(100, float("inf"), 110, 120, "LONG")["valid"] is False
    assert trade_geometry(100, 95, 0, 120, "LONG")["valid"] is False
    assert trade_geometry(-100, -105, -90, -80, "LONG")["valid"] is False


def test_trade_geometry_uses_signed_blended_rr_for_both_directions():
    long_geometry = trade_geometry(100, 95, 110, 120, "LONG")
    short_geometry = trade_geometry(100, 105, 90, 80, "SHORT")

    for geometry in (long_geometry, short_geometry):
        assert geometry["valid"] is True
        assert geometry["risk"] == 5
        assert geometry["rr_tp1"] == 2
        assert geometry["rr_tp2"] == 4
        assert geometry["rr"] == 3


def test_bi_backtest_uses_signed_live_trade_geometry():
    from modules import backtests

    source = inspect.getsource(backtests.run_bi_v2_backtest)
    assert "geometry = trade_geometry(" in source
    assert "risk = abs(est_entry - stop_price)" not in source
    assert "reward_blended = 0.5 * abs" not in source


def test_wolfe_wave_rr_never_uses_absolute_wrong_side_reward():
    from modules import patterns

    source = inspect.getsource(patterns.detect_wolfe_waves)
    assert "reward = target - entry_price" in source
    assert "reward = entry_price - target" in source
    assert "if risk <= 0 or reward <= 0:" in source
    assert "reward = abs(target - entry_price)" not in source
    assert "reward = abs(entry_price - target)" not in source


def test_rule_backtest_rejects_non_positive_stop_percentages():
    source = inspect.getsource(api._run_backtest)
    assert "not math.isfinite(stop_pct) or stop_pct <= 0" in source
    assert "risk = abs(entry_p * stop_pct)" not in source


def test_stock_reminder_rejects_wrong_side_long_stop(monkeypatch):
    bars = [
        {"open": 9.9, "high": 10.2, "low": 9.8, "close": 10.1, "volume": 1000}
        for _ in range(8)
    ]
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda _ticker: bars)

    result = api._evaluate_stock_reminder({
        "ticker": "BAD",
        "row": {"direction": "LONG", "entry": 10.0, "stop_loss": 10.5},
    })

    assert result == {"triggered": False, "reason": "invalid_stock_stop_geometry"}


def test_crypto_trigger_rejects_wrong_side_long_stop(monkeypatch):
    bars = [
        {"open": 9.9, "high": 10.2, "low": 9.8, "close": 10.0, "volume": 1000}
        for _ in range(12)
    ]
    monkeypatch.setattr(api, "_completed_candles_only", lambda rows, _timeframe: rows)
    monkeypatch.setattr(
        api,
        "_crypto_candle_freshness",
        lambda _rows, _timeframe: {"known": True, "fresh": True, "age_seconds": 30},
    )

    result = api._score_early_mover_trigger_bars(
        {"Price": 10.0, "entry": 10.0, "stop_loss": 10.5, "tp1": 11.0},
        bars,
        "5m",
        {},
    )

    assert result["ok"] is False
    assert result["reason"] == "invalid_long_stop_geometry"
