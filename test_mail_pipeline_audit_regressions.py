"""Offline audit counterexamples: presentation purity and final mail selection."""
from copy import deepcopy

import pytest
import api
from test_stock_strategy_final_revalidation import _patch_strategy_mail_pipeline, _row


def test_strategy_mail_cap_does_not_starve_first_finally_valid_lower_rank(monkeypatch):
    sent, _tracked, _events, released = _patch_strategy_mail_pipeline(monkeypatch)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_ensure_stock_business_quality", lambda row: row)
    monkeypatch.setattr(api, "_cluster_warning_html", lambda *_a, **_k: "")
    monkeypatch.setattr(api, "_safe_format_telegram_rows", lambda *_a, **_k: "")
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *_a, **_k: None)
    rows = [_row(ticker=f"R{index:02d}", Ticker=f"R{index:02d}",
                 Strategy="Gap Momentum Long", score=99-index)
            for index in range(11)]
    checked = []

    def validate(row, **_kwargs):
        checked.append(row["ticker"])
        if row["ticker"] != "R10":
            return {"ok": False, "reason": "final_stop_touched_since_scan"}
        return {"ok": True, "candidate": dict(row)}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert checked == [f"R{index:02d}" for index in range(11)]
    assert [message["tracking_rows"][0]["ticker"] for message in sent] == ["R10"]
    assert len(sent) <= api._ALERT_EMAIL_MAX_ROWS
    assert "stock_strategy_R10" not in released


def _selection_fixture(monkeypatch, size=15):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_ensure_stock_business_quality", lambda row: row)
    monkeypatch.setattr(api, "_cluster_warning_html", lambda *_a, **_k: "")
    monkeypatch.setattr(api, "_safe_format_telegram_rows", lambda *_a, **_k: "")
    monkeypatch.setattr(api, "_record_suppression_counts", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", lambda key, **_k: released.append(key))
    rows = [_row(ticker=f"R{index:02d}", Ticker=f"R{index:02d}",
                 Strategy="Gap Momentum Long", score=95)
            for index in range(size)]
    checked = []

    def validate(row, **_kwargs):
        checked.append(row["ticker"])
        return {"ok": True, "candidate": dict(row)}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    return rows, checked, sent, tracked, events, released


@pytest.mark.parametrize("send_result", [False, True])
def test_validated_stock_row_is_not_misreported_as_missing_when_delivery_fails(monkeypatch, send_result):
    rows, checked, sent, _tracked, events, _released = _selection_fixture(monkeypatch, size=1)

    def send(_subject, _body, **kwargs):
        sent.append(kwargs)
        return send_result

    monkeypatch.setattr(api, "_send_email_alert", send)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == ["R00"]
    assert len(sent) == 1
    assert not any(event[2] == "no_mail_adjacent_revalidated_rows" for event in events)
    assert ("stock_strategy_R00" in api._EMAIL_COOLDOWN) is send_result


def test_no_validated_stock_rows_still_reports_validation_skip_without_sending(monkeypatch):
    rows, _checked, sent, _tracked, events, released = _selection_fixture(monkeypatch, size=1)
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate",
                        lambda *_a, **_k: {"ok": False, "reason": "final_stop_touched_since_scan"})
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert not sent
    assert "stock_strategy_R00" in released
    assert any(event[2] == "mail_adjacent_stock_revalidation:final_stop_touched_since_scan" for event in events)
    assert any(event[2] == "no_mail_adjacent_revalidated_rows" for event in events)


@pytest.mark.parametrize("send_result", [True, False])
def test_strategy_selection_caps_sender_attempts_and_releases_unused(monkeypatch, send_result):
    rows, checked, sent, _tracked, _events, released = _selection_fixture(monkeypatch)

    def send(subject, body, **kwargs):
        sent.append(kwargs)
        return send_result

    monkeypatch.setattr(api, "_send_email_alert", send)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [f"R{index:02d}" for index in range(10)]
    assert len(sent) == 10
    assert all(f"stock_strategy_R{index:02d}" in released for index in range(10, 15))
    assert not any(f"stock_strategy_R{index:02d}" in api._EMAIL_COOLDOWN for index in range(10, 15))


@pytest.mark.parametrize("blocker", ["open_equivalent", "claim_owned_elsewhere"])
def test_lower_rank_valid_rows_replace_previously_claimed_or_open_leaders(monkeypatch, blocker):
    rows, checked, sent, _tracked, _events, _released = _selection_fixture(monkeypatch)
    if blocker == "open_equivalent":
        monkeypatch.setattr(api, "_has_open_equivalent_trade_safe",
                            lambda _scanner, row: int(row["ticker"][1:]) < 10)
    else:
        monkeypatch.setattr(api, "_email_dedupe_claim",
                            lambda key, *_a, **_k: int(key[-2:]) >= 10)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [f"R{index:02d}" for index in range(10, 15)]
    assert len(sent) == 5


def test_final_fallback_does_not_inspect_more_than_fifty_input_rows(monkeypatch):
    rows, checked, sent, _tracked, _events, _released = _selection_fixture(monkeypatch, 60)

    def validate(row, **_kwargs):
        checked.append(row["ticker"])
        return {"ok": False, "reason": "final_stop_touched_since_scan"}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [f"R{index:02d}" for index in range(50)]
    assert sent == []


def test_yellow_market_two_mail_limit_selects_finally_valid_rows(monkeypatch):
    rows, checked, sent, _tracked, _events, released = _selection_fixture(monkeypatch, 6)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *_a, **_k: {
        "state": "YELLOW", "layer": "market", "score_boost": 5, "max_rows": 2,
        "reason_tag": "market_regime_yellow",
    })

    def validate(row, **_kwargs):
        checked.append(row["ticker"])
        if int(row["ticker"][1:]) < 2:
            return {"ok": False, "reason": "final_stop_touched_since_scan"}
        return {"ok": True, "candidate": dict(row)}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == ["R00", "R01", "R02", "R03"]
    assert [message["tracking_rows"][0]["ticker"] for message in sent] == ["R02", "R03"]
    assert "stock_strategy_R04" in released and "stock_strategy_R05" in released


def test_watch_and_trade_routes_share_ten_row_quota(monkeypatch):
    rows, checked, sent, _tracked, _events, released = _selection_fixture(monkeypatch)

    def regime(*_args, **kwargs):
        if int(kwargs["calibration_row"]["ticker"][1:]) < 6:
            return {"state": "RED", "layer": "market", "reason_tag": "market_regime_red"}
        return None

    monkeypatch.setattr(api, "_regime_mail_decision", regime)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert len(sent) == 5
    assert sent[0]["mail_class"] == "watch"
    assert len(sent[0]["delivery_dedupe_keys"]) == 6
    assert checked == ["R06", "R07", "R08", "R09"]
    assert all(f"stock_strategy_R{index:02d}" in released for index in range(10, 15))


def test_all_market_watch_rows_still_capped_and_deferred_claims_released(monkeypatch):
    rows, checked, sent, _tracked, _events, released = _selection_fixture(monkeypatch)
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *_a, **_k: {
        "state": "RED", "layer": "market", "reason_tag": "market_regime_red",
    })
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == []
    assert len(sent) == 1
    assert len(sent[0]["delivery_dedupe_keys"]) == 10
    assert all(f"stock_strategy_R{index:02d}__watch" in released for index in range(10, 15))


def test_stock_display_decoration_does_not_compound_trade_score(monkeypatch):
    monkeypatch.setattr(api, "_get_market_context_snapshot", lambda: {})
    health = {"health_score": 90, "entry_quality_score": 90,
              "fakeout_risk_score": 90, "liquidity_score": 90,
              "decision": "TRADEABLE"}
    monkeypatch.setattr(api, "calculate_trade_health", lambda *_a, **_k: dict(health))
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda *_a, **_k: [])
    monkeypatch.setattr(api, "_stock_swing_short_rule_reasons", lambda *_a, **_k: [])
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *_a, **_k: ({"AAA"}, "unit"))
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_structural_barrier_alert_reason", lambda *_a, **_k: None)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda *_a, **_k: [])
    monkeypatch.setattr(api, "_email_dedupe_active", lambda *_a, **_k: False)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    item = _row(Strategy="Gap Momentum Long", score=95, grade="S")
    raw = deepcopy(item)
    expected = api._stock_alert_trade_score(raw, "stock_strategy")

    api._apply_scanner_result_trade_state(item, "stock_strategy")
    first = item["trade_score"]
    api._apply_scanner_result_trade_state(item, "stock_strategy")

    assert first == expected
    assert item["raw_score"] == 95
    assert item["trade_score"] == expected
