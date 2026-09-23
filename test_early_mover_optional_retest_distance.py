"""A confirmed breakout replaces retest proof, never the original chase limit."""
from copy import deepcopy
import time

import pytest
import api
from modules.breakout_warnings import BREAKOUT_WITHOUT_RETEST_CODE as CODE


def row(action="WAIT_FOR_RETEST"):
    plan = {"entry": 10., "stop_loss": 9., "tp1": 13., "tp2": 15.,
            "target_quality": "STRUCTURAL", "direction": "LONG"}
    return {**plan, "Symbol": "POLICY", "Price": 10., "trade_action": action,
            "trade_setup": dict(plan), "live_rr_ratio": 4., "distance_to_entry_r": 0.,
            "btc_context": {"btc_24h": 1., "btc_7d": 2., "tailwind": True},
            "risk_flags": [], "score": 95, "grade": "S"}


def trigger(price, matched=None):
    checked = time.time()
    return {"ok": True, "timeframe": "5m", "matched": matched or ["breakout"],
            "reason": "adaptive_5m_breakout", "execution_score": 95,
            "last_close": price, "checked_at": checked,
            "last_candle_closed_at": checked - 60, "execution_data_age_seconds": 60}


@pytest.mark.parametrize("distance,accepted", [(0., True), (.2, True), (.35, True),
                                                (.350001, False), (.5, False), (.74, False)])
def test_wait_origin_actual_breakout_keeps_exact_original_distance_limit(distance, accepted):
    item = row()
    api._apply_early_mover_signal_state(item, trigger(10 + distance))
    assert item["breakout_entry_policy"] == "near_original_entry"
    assert item["breakout_policy_entry"] == 10 and item["breakout_policy_stop"] == 9
    assert item["trade_setup"]["breakout_entry_policy"] == "near_original_entry"
    assert item["alertable_crypto"] is accepted
    assert (item["trade_signal"] == "JETZT_TRADEN") is accepted
    assert (CODE in item.get("warning_codes", [])) is accepted
    reasons = api._early_mover_long_rule_reasons(item)
    assert ("early_mover_retest_not_near_entry" not in reasons) is accepted


def test_confirmed_close_overrides_stale_near_entry_telemetry():
    item = row()
    item.update(Price=10., current_price=10., distance_to_entry_r=0.)
    api._apply_early_mover_signal_state(item, trigger(10.5))
    assert item["trade_signal"] == "WARTEN"
    assert item["breakout_policy_distance_r"] == .5
    assert CODE not in item.get("warning_codes", [])


def test_repeated_promotion_cannot_reset_original_anchors_or_distance_policy():
    item = row()
    api._apply_early_mover_signal_state(item, trigger(10.2))
    assert item["trade_action"] == "LONG_TRIGGER"
    item.update(entry=10.5, distance_to_entry_r=0.)
    item["trade_setup"]["entry"] = 10.5
    api._apply_early_mover_signal_state(item, trigger(10.5))
    assert item["breakout_policy_entry"] == 10.
    assert item["alertable_crypto"] is False
    assert CODE not in item.get("warning_codes", [])
    assert CODE not in item["trade_setup"].get("warning_codes", [])


def test_classification_rechecks_current_price_not_old_policy_distance():
    item = row()
    api._apply_early_mover_signal_state(item, trigger(10.2))
    item["current_price"] = 10.5
    assert item["breakout_policy_distance_r"] == pytest.approx(.2)
    assert "early_mover_retest_not_near_entry" in api._early_mover_long_rule_reasons(item)


@pytest.mark.parametrize("price", [None, float("nan"), float("inf")])
def test_missing_or_nonfinite_current_observation_fails_closed(price):
    item = row()
    item["current_price"] = price
    proof = trigger(10.)
    proof.pop("last_close")
    api._apply_early_mover_signal_state(item, proof)
    assert not item["alertable_crypto"]
    assert "early_mover_retest_not_near_entry" in api._early_mover_long_rule_reasons(item)


@pytest.mark.parametrize("field", ["breakout_policy_entry", "breakout_policy_stop"])
def test_corrupt_persisted_policy_does_not_fall_back_to_new_plan(field):
    item = row()
    api._apply_early_mover_signal_state(item, trigger(10.2))
    item[field] = None
    api._apply_early_mover_signal_state(item, trigger(10.2))
    assert not item["alertable_crypto"]


def test_native_long_trigger_retains_its_existing_distance_policy():
    item = row("LONG_TRIGGER")
    api._apply_early_mover_signal_state(item, trigger(10.5))
    assert item["trade_signal"] == "JETZT_TRADEN"
    assert "breakout_entry_policy" not in item


@pytest.mark.parametrize("already_promoted", [False, True])
def test_vwap_only_is_not_a_replacement_for_wait_origin_break_or_retest(already_promoted):
    item = row()
    if already_promoted:
        api._apply_early_mover_signal_state(item, trigger(10.2))
    api._apply_early_mover_signal_state(item, trigger(10.2, ["vwap_reclaim"]))
    assert item["trade_signal"] == "WARTEN"
    assert not item["alertable_crypto"]
    assert CODE not in item.get("warning_codes", [])


def test_real_retest_is_allowed_near_entry_and_clears_only_missing_retest_warning():
    item = row()
    item["warning_codes"] = ["other"]
    api._apply_early_mover_signal_state(item, trigger(10.2))
    api._apply_early_mover_signal_state(item, trigger(10.1, ["retest_hold"]))
    assert item["alertable_crypto"] and item["retest_confirmed"]
    assert item["warning_codes"] == ["other"]
    assert item["breakout_entry_policy"] == "near_original_entry"


def quote_gate(monkeypatch, price):
    monkeypatch.setattr(api, "_revalidate_crypto_trade_mail_candidate", lambda candidate, **kwargs: {
        "ok": True, "candidate": {**candidate, "price": price,
            "final_quote_spread_bps": 5., "final_quote_depth_10bps_min_usd": 100_000.,
            "final_quote_depth_25bps_min_usd": 200_000., "final_quote_depth_50bps_min_usd": 300_000.}})


def candidate():
    return {"symbol": "POLICY", "price": 10.2, "entry": 10., "stop": 9.,
            "tp1": 13., "tp2": 15., "action": "LONG_TRIGGER",
            "breakout_entry_policy": "near_original_entry",
            "breakout_policy_entry": 10., "breakout_policy_stop": 9.}


@pytest.mark.parametrize("price,accepted", [(10.2, True), (10.35, True), (10.350001, False), (10.5, False)])
def test_final_executable_ask_enforces_original_policy_after_action_promotion(monkeypatch, price, accepted):
    quote_gate(monkeypatch, price)
    result = api._revalidate_early_mover_mail_candidate(candidate())
    assert result["ok"] is accepted
    if not accepted:
        assert result["reason"] == "final_retest_distance_too_far"


def test_revalidated_candidate_cannot_ratchet_original_entry_forward(monkeypatch):
    quote_gate(monkeypatch, 10.2)
    first = api._revalidate_early_mover_mail_candidate(candidate())["candidate"]
    assert first["entry"] == 10.2
    quote_gate(monkeypatch, 10.5)
    result = api._revalidate_early_mover_mail_candidate(first)
    assert not result["ok"] and result["reason"] == "final_retest_distance_too_far"


def test_missing_final_policy_anchor_fails_closed(monkeypatch):
    quote_gate(monkeypatch, 10.2)
    item = candidate()
    item.pop("breakout_policy_stop")
    result = api._revalidate_early_mover_mail_candidate(item)
    assert result == {"ok": False, "reason": "final_retest_policy_levels_missing"}


def test_mail_projection_preserves_policy_for_final_gate_without_sending(monkeypatch):
    item = row()
    api._apply_early_mover_signal_state(item, trigger(10.2))
    monkeypatch.setattr(api, "_scanner_uses_swing_horizon", lambda *args: True)
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda *args: {
        "alertable_now": True, "cooldown_key": "offline_policy", "ticker": "POLICY",
        "grade": "S", "score": 95, "price": 10.2})
    monkeypatch.setattr(api, "_filter_open_equivalent_trade_rows", lambda scanner, rows: (rows, 0))
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *args, **kwargs: True)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *args, **kwargs: None)
    monkeypatch.setattr(api, "_send_early_mover_watch_alerts", lambda *args, **kwargs: False)
    monkeypatch.setattr(api, "_send_email_alert", lambda *args, **kwargs: pytest.fail("no mail"))
    seen = []
    def reject(projected, **kwargs):
        seen.append(deepcopy(projected))
        return {"ok": False, "reason": "offline_stop_after_projection"}
    monkeypatch.setattr(api, "_revalidate_early_mover_mail_candidate", reject)
    api._send_early_mover_long_alerts({"coins": [item]})
    assert len(seen) == 1
    assert seen[0]["breakout_entry_policy"] == "near_original_entry"
    assert seen[0]["breakout_policy_entry"] == 10. and seen[0]["breakout_policy_stop"] == 9.
