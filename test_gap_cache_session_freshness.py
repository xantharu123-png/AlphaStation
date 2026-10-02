"""Fixed-slot Gap result freshness; offline cache reads, no scans or transport."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest

import api
from modules import stock_swing_contract as swing


@pytest.fixture
def cache_view(monkeypatch, tmp_path):
    state = {"now": datetime(2026, 10, 1, 14, tzinfo=timezone.utc)}
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "starter_swing")

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            current = state["now"]
            return current.astimezone(tz) if tz else current.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    statuses = deepcopy(api._scan_status)
    statuses["strategy_scan"]["interval_min"] = 60
    monkeypatch.setattr(api, "_scan_status", statuses)
    monkeypatch.setattr(api, "_scan_control_snapshot", lambda *a: None)
    monkeypatch.setattr(api, "_scan_schedule_for", lambda *a: None)
    monkeypatch.setattr(api, "_stock_strategy_result_attempt", lambda name, status, *a, **k: (status, {}))
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    # The boundary under test is cache/session freshness, not scoring or mail.
    monkeypatch.setattr(api, "_decorate_scan_results", lambda rows, *a: rows)
    monkeypatch.setattr(api, "_apply_scanner_visibility_policy", lambda name, rows: rows)
    cache_path = tmp_path / "gap.json"
    cache_path.touch()
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(cache_path))
    state.update(rows=[], cached_at="2026-10-01T10:24:00", partial=False, meta={})
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: (
        deepcopy(state["rows"]), state["cached_at"], deepcopy(state["meta"]), state["partial"]))
    return state


def payload(state, direction="Long", session="2026-09-30", empty=False):
    strategy = "Gap Momentum " + direction
    state["rows"] = [] if empty else [{"Ticker": "TEST", "Strategy": strategy,
        "Preis": 100., "direction": direction.upper(), **swing.metadata(session, 100.)}]
    state["meta"] = {"cache_version": api.STOCK_STRATEGY_CACHE_VERSION, "diagnostics": {
        "strategy": strategy, "coverage": "complete", "data_mode": swing.MODE,
        "analysis_as_of": swing.session_close(session).isoformat()}}
    return strategy


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("empty", [False, True])
def test_current_completed_gap_session_stays_fresh_between_fixed_slots(cache_view, direction, empty):
    strategy = payload(cache_view, direction, empty=empty)
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.cache_age_seconds > 2 * 3600
    assert response.data_quality["cache_status"] == "fresh"
    assert not any(text.startswith("Cache alt:") for text in response.warnings)


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_gap_previous_session_becomes_stale_after_new_delayed_close(cache_view, direction):
    strategy = payload(cache_view, direction)
    cache_view["now"] = datetime(2026, 10, 1, 20, 40, tzinfo=timezone.utc)
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.data_quality["cache_status"] == "stale"
    assert response.data_quality["cache_stale_reason"] == "gap_cache_session_stale"


@pytest.mark.parametrize("mutation", ["version", "missing_analysis", "unknown_mode", "partial", "missing_timestamp", "future_session"])
def test_unverified_gap_cache_does_not_inherit_fixed_slot_freshness(cache_view, mutation):
    strategy = payload(cache_view)
    if mutation == "version":
        cache_view["meta"]["cache_version"] -= 1
    elif mutation == "missing_analysis":
        cache_view["meta"]["diagnostics"].pop("analysis_as_of")
    elif mutation == "unknown_mode":
        cache_view["meta"]["diagnostics"]["data_mode"] = "live_snapshot"
    elif mutation == "partial":
        cache_view["partial"] = True
    elif mutation == "missing_timestamp":
        cache_view["cached_at"] = None
    else:
        payload(cache_view, session="2026-10-01")
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.data_quality["cache_status"] != "fresh"


def test_momentum_retains_existing_hourly_age_contract(cache_view):
    payload(cache_view)
    cache_view["meta"]["diagnostics"]["strategy"] = "Momentum Breakout Long"
    cache_view["rows"] = []
    response = api.get_scan_results(strategy="Momentum Breakout Long", market_type="stocks")
    assert response.data_quality["cache_status"] == "stale"
    assert any(text.startswith("Cache alt:") for text in response.warnings)


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("age_minutes,expected", [(30, "fresh"), (216, "stale")])
def test_explicit_live_gap_preserves_original_hourly_age_policy(cache_view, monkeypatch, direction, age_minutes, expected):
    strategy = payload(cache_view, direction)
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "live")
    cache_view["rows"] = [{"Ticker": "TEST", "Strategy": strategy, "Preis": 100., "direction": direction.upper()}]
    cache_view["meta"]["diagnostics"]["data_mode"] = "live_snapshot"
    cache_view["cached_at"] = "2026-10-01T13:30:00" if age_minutes == 30 else "2026-10-01T10:24:00"
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.data_quality["cache_status"] == expected
    assert (any(text.startswith("Cache alt:") for text in response.warnings)) == (expected == "stale")
    assert response.diagnostics["coverage"] == "complete"


@pytest.mark.parametrize("mutation", ["version", "daily_markers", "incomplete_daily_markers", "malformed_row"])
def test_live_gap_fallback_does_not_bypass_version_or_daily_evidence(cache_view, monkeypatch, mutation):
    strategy = payload(cache_view)
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "live")
    cache_view["meta"]["diagnostics"]["data_mode"] = "live_snapshot"
    cache_view["cached_at"] = "2026-10-01T13:30:00"
    if mutation == "version":
        cache_view["rows"] = []
        cache_view["meta"]["cache_version"] -= 1
    elif mutation == "malformed_row":
        cache_view["rows"] = ["not a row"]
    elif mutation == "incomplete_daily_markers":
        cache_view["rows"][0].pop("stock_swing_mode")
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.data_quality["cache_status"] != "fresh"


@pytest.mark.parametrize("empty", [False, True])
@pytest.mark.parametrize("mutation", ["missing_analysis", "future_session", "unknown_mode", "missing_timestamp", "future_timestamp"])
def test_unverified_done_gap_is_not_frontend_ready_or_complete(cache_view, empty, mutation):
    strategy = payload(cache_view, empty=empty)
    if mutation == "missing_analysis":
        cache_view["meta"]["diagnostics"].pop("analysis_as_of")
    elif mutation == "future_session":
        payload(cache_view, session="2026-10-01", empty=empty)
    elif mutation == "unknown_mode":
        cache_view["meta"]["diagnostics"]["data_mode"] = "live_snapshot"
    elif mutation == "missing_timestamp":
        cache_view["cached_at"] = None
    else:
        cache_view["cached_at"] = "2026-10-01T15:00:00"
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.data_quality["cache_status"] == "unknown"
    assert response.data_quality["cache_stale_reason"] == "gap_cache_session_unverified"
    assert response.diagnostics["coverage"] == "unknown"
    # Execute the shipped frontend evidence logic against the real API shape.
    from test_frontend_scanner_lifecycle import evaluate
    data = response.model_dump()
    info = json.dumps(data)
    evidence = evaluate(f"scannerCompactEvidence({{info:scannerSnapshotInfo({info}),hasLoaded:true,count:{response.count}}})")
    assert evidence["tone"] in {"warning", "missing"}
    assert "abgeschlossen" not in evidence["text"].lower()
    if response.cached_at is not None:
        assert evaluate(f"scannerPollOutcome({info},null,false)") == "unverified_final"


def test_running_partial_gap_keeps_provisional_evidence(cache_view):
    strategy = payload(cache_view)
    cache_view["partial"] = True
    api._scan_status[api._strategy_scan_status_key(strategy, "stocks")] = {"running": True}
    response = api.get_scan_results(strategy=strategy, market_type="stocks")
    assert response.partial is True and response.scan_running is True
    assert response.diagnostics["coverage"] == "complete"
    from test_frontend_scanner_lifecycle import evaluate
    info = json.dumps(response.model_dump())
    evidence = evaluate(f"scannerCompactEvidence({{info:scannerSnapshotInfo({info}),hasLoaded:true,running:true,count:1}})")
    assert evidence["tone"] == "running"


@pytest.mark.parametrize("direction", ["Long", "Short"])
def test_live_gap_admin_precheck_keeps_existing_snapshot_classification(cache_view, monkeypatch, direction):
    strategy = payload(cache_view, direction)
    monkeypatch.setenv("STOCK_SWING_DATA_MODE", "live")
    cache_view["rows"] = [{"Ticker": "TEST", "Strategy": strategy, "Preis": 100., "direction": direction.upper()}]
    cache_view["meta"]["diagnostics"]["data_mode"] = "live_snapshot"
    monkeypatch.setattr(api.time, "time", lambda: cache_view["now"].timestamp())
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **k: (deepcopy(cache_view["rows"]), cache_view["cached_at"]))
    monkeypatch.setattr(api, "load_cache_metadata", lambda *a: deepcopy(cache_view["meta"]))
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *a, **k: {
        "ticker": "TEST", "grade": "A", "score": 90, "alertable_now": True,
        "suppression_reasons": [], "decision": "TRADE_NOW"})
    audit = api._build_alert_audit_for_cache("stock_strategy", "fixture-gap.json", read_only=True)
    assert audit["alertable_now_count"] == 1
    assert audit["delivery_evaluated"] is False


@pytest.mark.parametrize("direction", ["Long", "Short"])
@pytest.mark.parametrize("mutation", [None, "old_session", "version", "missing_analysis", "missing_metadata"])
def test_gap_admin_precheck_cannot_promote_old_or_unverified_cache(cache_view, monkeypatch, direction, mutation):
    strategy = payload(cache_view, direction)
    if mutation == "old_session":
        cache_view["now"] = datetime(2026, 10, 1, 20, 40, tzinfo=timezone.utc)
    elif mutation == "version":
        cache_view["meta"]["cache_version"] -= 1
    elif mutation == "missing_analysis":
        cache_view["meta"]["diagnostics"].pop("analysis_as_of")
    elif mutation == "missing_metadata":
        cache_view["meta"] = None
    monkeypatch.setattr(api.time, "time", lambda: cache_view["now"].timestamp())
    monkeypatch.setattr(api, "load_cache_file", lambda *a, **k: (deepcopy(cache_view["rows"]), cache_view["cached_at"]))
    monkeypatch.setattr(api, "load_cache_metadata", lambda *a: deepcopy(cache_view["meta"]))
    # Intentionally optimistic scoring isolates the diagnostic freshness
    # boundary: an old session must still never be reported as mail-ready.
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *a, **k: {
        "ticker": "TEST", "grade": "A", "score": 90, "alertable_now": True,
        "suppression_reasons": [], "decision": "TRADE_NOW"})
    audit = api._build_alert_audit_for_cache("stock_strategy", "fixture-gap.json", read_only=True)
    assert audit["rows_checked"] == 1
    assert audit["alertable_now_count"] == (1 if mutation is None else 0)
    assert audit["delivery_evaluated"] is False
    if mutation is not None:
        assert audit["suppression_counts"]
