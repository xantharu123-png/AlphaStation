"""Real Cup plans across isolated persistence and delivery boundaries."""
from datetime import datetime, timezone

import pytest

import api


NAME = "Cup and Handle Breakout"
NOW = datetime(2026, 8, 31, 15, 0, tzinfo=timezone.utc)


def _row(monkeypatch, **changes):
    # Local import keeps the real producer fixture out of collection-time
    # parametrization. Neither a version stamp nor a synthetic positive plan
    # can supply authority to these downstream delivery/lifecycle tests.
    from test_cup_final_plan_coherence import (
        SESSION, _causal_cup_inputs, _pin_cup_clock,
    )
    _pin_cup_clock(monkeypatch, NOW)
    candidate, snapshot = _causal_cup_inputs(monkeypatch, far_targets=True)
    candidate.update(api.stock_swing.metadata(SESSION, 101.7))
    candidate.update(Strategy=NAME, strategy=NAME)
    row = api._apply_cup_handle_strategy_filter(
        candidate, {"min_dollar_volume": 2_000_000}, structure_snapshot=snapshot)
    assert row is not None
    # Global original rim is 100*1.012; old observed 118/135 supply pivots
    # independently justify structural targets rather than measured depth.
    assert row["cup_rim_level"] == row["Breakout_Level"] == 101.2
    assert (row["Entry"], row["StopLoss"], row["TP1"], row["TP2"]) == (
        101.2, 92.38, 117.75, 134.75)
    assert api._cup_signal_contract_reason(row, strategy_name=NAME) is None
    assert api._cup_final_plan_contract_reason(row) is None
    assert api._alert_trade_plan_rejection_reason(row) is None
    row.update(changes)
    return row


def _forbidden(*args, **kwargs):
    pytest.fail("Rejected Cup row escaped into provider, decoration, tracking, or mail")


def _cache_fixture(monkeypatch, tmp_path, rows, *, version, partial=False):
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(tmp_path / "absent.json"))
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: (
        rows, "2026-08-31T14:59:30",
        {"strategy": NAME, "cache_version": version,
         "diagnostics": {"strategy": NAME, "coverage": "incomplete" if partial else "complete"}},
        partial,
    ))
    monkeypatch.setattr(api, "_stock_strategy_result_attempt", lambda name, state, cached, **kw: (state, {}))
    decorated = []
    def decorate(items, *args):
        decorated.extend(items)
        return items
    monkeypatch.setattr(api, "_decorate_scan_results", decorate)
    monkeypatch.setattr(api, "_apply_scanner_visibility_policy", lambda scanner, items: items)
    monkeypatch.setattr(api, "_scan_quality_payload", lambda *a: {
        "warnings": [], "data_source": "offline fixture", "exclusion_policy": [],
    })
    monkeypatch.setattr(api, "rate_limited_get", _forbidden)
    return decorated


@pytest.mark.parametrize("partial", [False, True])
@pytest.mark.parametrize("identity", ["named", "nameless", "other_strategy"])
def test_public_cache_rejects_legacy_cup_before_decoration(monkeypatch, tmp_path, partial, identity):
    row = {"ticker": "OLD", "score": 99, "grade": "S"}
    if identity == "named":
        row.update(strategy=NAME, pattern_type="cup_handle_breakout")
    elif identity == "other_strategy":
        row.update(strategy="Gap Momentum Long")
    decorated = _cache_fixture(monkeypatch, tmp_path, [row],
                               version=api.STOCK_STRATEGY_CACHE_VERSION, partial=partial)
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.count == 0 and result.data == []
    assert decorated == []
    assert result.partial is partial
    assert result.diagnostics["cup_contract_rejected"] == 1


@pytest.mark.parametrize("version", [None, 8, 9, 10, "10", 11, "11"])
def test_public_cache_rejects_previous_cache_generation_even_with_current_row(monkeypatch, tmp_path, version):
    assert api.STOCK_STRATEGY_CACHE_VERSION >= 12
    decorated = _cache_fixture(monkeypatch, tmp_path, [_row(monkeypatch)], version=version)
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.count == 0 and decorated == []
    assert result.diagnostics["warning"] == "strategy_cache_version_old_scan_again"
    assert result.diagnostics["required_cache_version"] == api.STOCK_STRATEGY_CACHE_VERSION


@pytest.mark.parametrize("partial", [False, True])
def test_public_cache_preserves_current_proof_and_drops_only_invalid_rows(monkeypatch, tmp_path, partial):
    valid = _row(monkeypatch)
    below = _row(monkeypatch, ticker="WICK", cup_confirmation_close=100.5)
    decorated = _cache_fixture(monkeypatch, tmp_path, [valid, below],
                               version=api.STOCK_STRATEGY_CACHE_VERSION, partial=partial)
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.count == 1
    assert decorated == [valid] and result.data == [valid]
    assert result.diagnostics["cup_contract_rejected"] == 1


def _monitor_fixture(monkeypatch, row, **claim_changes):
    claim = {
        "id": "CUPX|2026-08-28|2026-08-31", "lease_owner": "unit-test",
        "generation": 2, "ticker": "CUPX", "breakout_level": 101.2,
        "confirmation_date": "2026-08-28", "target_session_date": "2026-08-31",
        "row": row, **claim_changes,
    }
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_previous_us_exchange_trading_date_str", lambda *a: "2026-08-28")
    monkeypatch.setattr(api, "_claim_cup_handle_watches", lambda *a, **k: [claim])
    finished, suppressed = [], []
    monkeypatch.setattr(api, "_finish_cup_handle_watch_claim", lambda *a, **k: finished.append((a, k)))
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    for name in ("_prune_cup_handle_watches", "_send_strategy_scan_alerts", "_send_email_alert", "rate_limited_get"):
        monkeypatch.setattr(api, name, _forbidden)
    return finished, suppressed


@pytest.mark.parametrize("row_changes", [
    None, {"cup_pattern_version": None},
    {"cup_confirmation_close": 100.1}, {"cup_rim_level": None},
])
def test_monitor_removes_old_or_invalid_claims_before_any_provider(monkeypatch, row_changes):
    row = ({"ticker": "CUPX"} if row_changes is None else
           _row(monkeypatch, **row_changes))
    finished, suppressed = _monitor_fixture(monkeypatch, row)
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", _forbidden)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert finished[0][1]["remove"] is True
    assert finished[0][1]["generation"] == 2
    assert suppressed == [("cup_handle_watch", {"cup_next_session_claim_invalid": 1})]


@pytest.mark.parametrize("changes", [
    {"ticker": "WRONG"}, {"breakout_level": 1.}, {"breakout_level": 101.},
    {"breakout_level": float("nan")}, {"breakout_level": True},
])
def test_monitor_cannot_rebind_valid_proof_to_other_ticker_or_pivot(monkeypatch, changes):
    finished, suppressed = _monitor_fixture(monkeypatch, _row(monkeypatch), **changes)
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", _forbidden)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert finished[0][1]["remove"] is True
    assert suppressed == [("cup_handle_watch", {"cup_next_session_claim_invalid": 1})]


def test_monitor_keeps_valid_untriggered_claim_and_checks_original_rim(monkeypatch):
    row = _row(monkeypatch)
    assert row["price"] == 101.7 and row["cup_rim_level"] == 101.2
    finished, suppressed = _monitor_fixture(monkeypatch, row)
    checked = []
    def trigger(ticker, level, **kwargs):
        checked.append((ticker, level))
        return {"confirmed": False, "reason": "unit-no-trigger"}
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", trigger)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert checked == [("CUPX", 101.2)]  # original detected rim, never a rebound pivot
    assert finished[0][1]["remove"] is False
    assert suppressed == [("cup_handle_watch", {"unit-no-trigger": 1})]


class _ReachedSenderAfterProof(Exception):
    pass


def test_aggregate_sender_does_not_treat_owner_label_as_a_different_strategy(monkeypatch):
    row = _row(monkeypatch)
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    def after_gate(*args, **kwargs):
        raise _ReachedSenderAfterProof
    monkeypatch.setattr(api, "_load_common_stock_universe", after_gate)
    with pytest.raises(_ReachedSenderAfterProof):
        api._send_strategy_scan_alerts("Aktien Auto-Sweep", [row], "stocks")
    assert suppressed == []


@pytest.mark.parametrize("owner", [NAME, "Aktien Auto-Sweep"])
def test_sender_still_rejects_legacy_rows_before_swing_premarket_tracking(monkeypatch, owner):
    row = _row(monkeypatch, cup_pattern_version=None, Premarket=True)
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    for name in ("_load_common_stock_universe", "_stock_trade_email_status",
                 "_classify_premarket_candidate", "_safe_record_alert_signals", "_send_email_alert"):
        monkeypatch.setattr(api, name, _forbidden)
    monkeypatch.setattr(api.stock_swing, "validate", _forbidden)
    api._send_strategy_scan_alerts(owner, [row], "stocks")
    assert suppressed == [("stock_strategy", {"cup_contract_legacy_or_missing_version": 1})]


@pytest.mark.parametrize("scanner", ["stock_strategy", "strategy_scan"])
def test_classifier_rejects_legacy_cup_without_trade_health_or_swing_enrichment(monkeypatch, scanner):
    row = _row(monkeypatch, cup_pattern_version=None)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 99)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"CUPX"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: "")
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    for name in ("_stock_swing_rule_reasons", "_stock_swing_short_rule_reasons", "_alert_trade_health_reasons"):
        monkeypatch.setattr(api, name, _forbidden)
    state = api._classify_alert_candidate(scanner, row, NOW.timestamp())
    assert state["alertable_now"] is False
    assert "cup_contract_legacy_or_missing_version" in state["suppression_reasons"]
