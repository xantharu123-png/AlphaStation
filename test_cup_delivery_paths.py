"""Cup persistence and delivery boundaries; all providers and writes mocked."""
from datetime import datetime, timezone

import pytest

import api
from modules.cup_signal_contract import CUP_PATTERN_CONTRACT_VERSION


NAME = "Cup and Handle Breakout"
NOW = datetime(2026, 9, 21, 15, 0, tzinfo=timezone.utc)


def _row(**changes):
    return {
        "ticker": "CUPTEST", "Strategy": NAME, "strategy": NAME,
        "pattern_type": "cup_handle_breakout", "pattern_timeframe": "1D",
        "cup_pattern_version": CUP_PATTERN_CONTRACT_VERSION,
        "cup_rim_level": 100., "cup_confirmation_level": 101.,
        "cup_confirmation_close": 101.202, "Breakout_Level": 100.,
        "direction": "LONG", "Signal_Direction": "LONG", "price": 101.5,
        "entry": 101., "stop_loss": 97., "tp1": 111., "tp2": 119.,
        "score": 99, "grade": "S", "RVOL": 3.,
        "trade_action": "LONG_NOW", "trade_signal": "JETZT_TRADEN",
        "trade_decision": "TRADEABLE", **changes,
    }


def _forbidden(*args, **kwargs):
    pytest.fail("Rejected Cup row escaped into provider, decoration, tracking, or mail")


def _cache_fixture(monkeypatch, tmp_path, rows, *, version, partial=False):
    monkeypatch.setattr(api, "_strategy_cache_path", lambda *a: str(tmp_path / "absent.json"))
    monkeypatch.setattr(api, "_scan_status", {})
    monkeypatch.setattr(api, "load_live_cache_file", lambda *a, **k: (
        rows, "2026-09-21T12:00:00",
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
    monkeypatch.setattr(api, "_apply_signal_only_policy", lambda scanner, items: items)
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


@pytest.mark.parametrize("version", [None, 8, 9, "10"])
def test_public_cache_rejects_previous_cache_generation_even_with_current_row(monkeypatch, tmp_path, version):
    assert api.STOCK_STRATEGY_CACHE_VERSION == 11
    decorated = _cache_fixture(monkeypatch, tmp_path, [_row()], version=version)
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.count == 0 and decorated == []
    assert result.diagnostics["warning"] == "strategy_cache_version_old_scan_again"
    assert result.diagnostics["required_cache_version"] == 11


@pytest.mark.parametrize("partial", [False, True])
def test_public_cache_preserves_current_proof_and_drops_only_invalid_rows(monkeypatch, tmp_path, partial):
    valid = _row()
    below = _row(ticker="WICK", cup_confirmation_close=100.5)
    decorated = _cache_fixture(monkeypatch, tmp_path, [valid, below],
                               version=api.STOCK_STRATEGY_CACHE_VERSION, partial=partial)
    result = api.get_scan_results(NAME, None, "stocks")
    assert result.count == 1
    assert decorated == [valid] and result.data == [valid]
    assert result.diagnostics["cup_contract_rejected"] == 1


def _monitor_fixture(monkeypatch, row, **claim_changes):
    claim = {
        "id": "CUPTEST|2026-09-18|2026-09-21", "lease_owner": "unit-test",
        "generation": 2, "ticker": "CUPTEST", "breakout_level": 100.,
        "confirmation_date": "2026-09-18", "target_session_date": "2026-09-21",
        "row": row, **claim_changes,
    }
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a: {"allowed": True, "session": "US_REGULAR"})
    monkeypatch.setattr(api, "_previous_us_exchange_trading_date_str", lambda *a: "2026-09-18")
    monkeypatch.setattr(api, "_claim_cup_handle_watches", lambda *a, **k: [claim])
    finished, suppressed = [], []
    monkeypatch.setattr(api, "_finish_cup_handle_watch_claim", lambda *a, **k: finished.append((a, k)))
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    for name in ("_prune_cup_handle_watches", "_send_strategy_scan_alerts", "_send_email_alert", "rate_limited_get"):
        monkeypatch.setattr(api, name, _forbidden)
    return finished, suppressed


@pytest.mark.parametrize("row", [
    {"ticker": "CUPTEST"}, _row(cup_pattern_version=None),
    _row(cup_confirmation_close=100.1), _row(cup_rim_level=None),
])
def test_monitor_removes_old_or_invalid_claims_before_any_provider(monkeypatch, row):
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
    finished, suppressed = _monitor_fixture(monkeypatch, _row(), **changes)
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", _forbidden)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert finished[0][1]["remove"] is True
    assert suppressed == [("cup_handle_watch", {"cup_next_session_claim_invalid": 1})]


def test_monitor_keeps_valid_untriggered_claim_and_checks_original_rim(monkeypatch):
    finished, suppressed = _monitor_fixture(monkeypatch, _row())
    checked = []
    def trigger(ticker, level, **kwargs):
        checked.append((ticker, level))
        return {"confirmed": False, "reason": "unit-no-trigger"}
    monkeypatch.setattr(api, "_cup_handle_next_session_trigger_state", trigger)
    result = api._cup_handle_watch_monitor_wrapper(now_ts=NOW.timestamp())
    assert result == {"claimed": 1, "triggered": 0, "completed": 0}
    assert checked == [("CUPTEST", 100.)]
    assert finished[0][1]["remove"] is False
    assert suppressed == [("cup_handle_watch", {"unit-no-trigger": 1})]


class _ReachedSenderAfterProof(Exception):
    pass


def test_aggregate_sender_does_not_treat_owner_label_as_a_different_strategy(monkeypatch):
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    monkeypatch.setattr(api.stock_swing, "validate", lambda *a: False)
    def after_gate(*args, **kwargs):
        raise _ReachedSenderAfterProof
    monkeypatch.setattr(api, "_load_common_stock_universe", after_gate)
    with pytest.raises(_ReachedSenderAfterProof):
        api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_row()], "stocks")
    assert suppressed == []


@pytest.mark.parametrize("owner", [NAME, "Aktien Auto-Sweep"])
def test_sender_still_rejects_legacy_rows_before_swing_premarket_tracking(monkeypatch, owner):
    suppressed = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *a: suppressed.append(a))
    for name in ("_load_common_stock_universe", "_stock_trade_email_status",
                 "_classify_premarket_candidate", "_safe_record_alert_signals", "_send_email_alert"):
        monkeypatch.setattr(api, name, _forbidden)
    monkeypatch.setattr(api.stock_swing, "validate", _forbidden)
    api._send_strategy_scan_alerts(owner, [_row(cup_pattern_version=None, Premarket=True)], "stocks")
    assert suppressed == [("stock_strategy", {"cup_contract_legacy_or_missing_version": 1})]


@pytest.mark.parametrize("scanner", ["stock_strategy", "strategy_scan"])
def test_classifier_rejects_legacy_cup_without_trade_health_or_swing_enrichment(monkeypatch, scanner):
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 99)
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"CUPTEST"}, "fixture"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: "")
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    for name in ("_stock_swing_rule_reasons", "_stock_swing_short_rule_reasons", "_alert_trade_health_reasons"):
        monkeypatch.setattr(api, name, _forbidden)
    state = api._classify_alert_candidate(scanner, _row(cup_pattern_version=None), NOW.timestamp())
    assert state["alertable_now"] is False
    assert "cup_contract_legacy_or_missing_version" in state["suppression_reasons"]
