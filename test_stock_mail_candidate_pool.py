"""Offline finite-pool regressions: raw rank cannot preempt mail eligibility.

Run with the repository's isolated API import harness. Provider/transport
helpers are replaced below; no real mail, cache or trading call is performed.
"""
from copy import deepcopy
from types import SimpleNamespace

import pytest
import api
from test_stock_starter_swing import plan
from test_stock_swing_mail_isolation import _pipeline
from test_stock_strategy_sweep_isolation import STRATEGIES

REAL_STOCK_ENRICH = api._enrich_stock_alert_5m_state
REAL_RELEASE_AFTER_SEND = api._email_dedupe_release_after_send


@pytest.fixture
def pool(monkeypatch, tmp_path):
    sent, counts = _pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_safe_format_telegram_rows", lambda *a, **k: "")
    monkeypatch.setattr(api, "_cluster_warning_html", lambda *a, **k: "")
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    monkeypatch.setattr(api, "_shadow_trackable_reasons", lambda *a, **k: [])
    monkeypatch.setattr(api, "_stock_business_quality_context", lambda *a: False)
    checked, classified, attempts, released = [], [], [], []
    base_classifier = api._classify_alert_candidate

    def classify(scanner, row, now=None):
        classified.append(row["ticker"])
        result = base_classifier(scanner, row, now)
        if row.get("fixture_rejected"):
            result.update(alertable_now=False, suppression_reasons=["estimated_trade_plan"])
        return result

    def final(row, **kwargs):
        checked.append(row["ticker"])
        if row.get("fixture_final_rejected"):
            return {"ok": False, "reason": "final_stop_touched_since_scan"}
        return {"ok": True, "candidate": dict(row)}

    monkeypatch.setattr(api, "_classify_alert_candidate", classify)
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", final)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda key, **k: released.append(key) or True)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", lambda key, **k: released.append(key))
    monkeypatch.setattr(api, "_new_stock_strategy_attempt", lambda **k: {})
    monkeypatch.setattr(api, "_publish_stock_strategy_attempt",
                        lambda attempt, status, **kw: attempts.append((status, deepcopy(kw))))
    monkeypatch.setattr(api, "save_cache_file", lambda *a, **k: None)
    monkeypatch.setattr(api, "_scan_control_point", lambda **k: None)
    monkeypatch.setattr(api.time, "sleep", lambda *a: None)
    monkeypatch.setattr(api, "_AUTO_STOCK_ALERT_STRATEGIES", list(STRATEGIES))
    return SimpleNamespace(sent=sent, counts=counts, checked=checked,
                           classified=classified, attempts=attempts, released=released)


def rows(count, *, valid_index=None):
    return [plan(ticker=f"R{i:03}", Ticker=f"R{i:03}", Strategy=STRATEGIES[0],
                 score=100-i*.01, grade="S", Business_Data_Status="fixture_complete",
                 fixture_rejected=(valid_index is not None and i != valid_index))
            for i in range(count)]


@pytest.mark.parametrize("position", [26, 51, 76, 150])
def test_valid_daily_row_beyond_raw_caps_reaches_sender_in_actual_sweep(pool, monkeypatch, position):
    candidates = rows(position, valid_index=position-1)
    before = deepcopy(candidates)
    monkeypatch.setattr(api, "_strategy_scan_wrapper",
                        lambda name, **k: deepcopy(candidates) if name == STRATEGIES[0] else [])
    api._stock_strategy_alert_sweep_wrapper()
    ticker = f"R{position-1:03}"
    assert pool.classified == [row["ticker"] for row in candidates]
    assert pool.checked == [ticker]
    assert [mail["tracking_rows"][0]["ticker"] for mail in pool.sent] == [ticker]
    assert candidates == before
    diagnostics = pool.attempts[-1][1]["diagnostics"]
    assert diagnostics["mail_audit"]["candidate_rows"] == position
    assert diagnostics["current_result_count"] == position


def test_final_reserve_contains_best_fifty_after_full_static_daily_screen(pool):
    candidates = rows(120)
    for row in candidates[:60]:
        row["fixture_rejected"] = True
    for row in candidates[60:]:
        row["fixture_final_rejected"] = True
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert len(pool.classified) == 120
    assert pool.checked == [f"R{i:03}" for i in range(60, 110)]
    assert pool.sent == []


def test_extra_daily_prescreen_never_fetches_new_enrichment_or_business_data(pool, monkeypatch):
    candidates = rows(70, valid_index=69)
    for row in candidates:
        row.pop("Business_Data_Status")
    business = []
    monkeypatch.setattr(api, "_stock_business_quality_context", lambda *a: True)
    monkeypatch.setattr(api, "_ensure_stock_business_quality",
                        lambda row: business.append(row["ticker"]) or row)
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", REAL_STOCK_ENRICH)
    # No new provider path is exercised by extra rows: rate_limited_get is
    # already a pytest.fail in _pipeline; real daily enrichment is tested in
    # test_stock_starter_swing. Business lookups retain their existing cap.
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert len(business) == 8 and all(int(t[1:]) < 50 for t in business)
    assert pool.checked == ["R069"]


def test_pool_bound_does_not_expand_final_provider_budget(pool):
    candidates = rows(350)
    for row in candidates:
        row["fixture_final_rejected"] = True
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert len(pool.classified) == 300 and len(pool.checked) == 50
    assert pool.sent == []


def test_provenance_rejects_do_not_steal_existing_live_enrichment_slot(pool, monkeypatch):
    from test_stock_strategy_final_revalidation import _row
    invalid = rows(50)
    for row in invalid:
        row["stock_swing_contract_version"] = 0
    legacy = _row(ticker="LIVE", Ticker="LIVE", Strategy=STRATEGIES[1])
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a: {"allowed": True, "session": "US_REGULAR"})
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", invalid + [legacy], "stocks")
    assert pool.classified == pool.checked == ["LIVE"]
    assert len(pool.sent) == 1


def test_additional_legacy_rows_do_not_expand_live_enrichment_budget(pool, monkeypatch):
    from test_stock_strategy_final_revalidation import _row
    candidates = [_row(ticker=f"L{i:03}", Ticker=f"L{i:03}", Strategy=STRATEGIES[1],
                       fixture_rejected=True) for i in range(60)]
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a: {"allowed": True, "session": "US_REGULAR"})
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert len(pool.classified) == 50
    assert pool.checked == [] and pool.sent == []


def test_expanded_reserve_preserves_yellow_two_sender_limit(pool, monkeypatch):
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: {
        "state": "YELLOW", "layer": "market", "score_boost": 5, "max_rows": 2,
        "reason_tag": "market_regime_yellow",
    })
    candidates = rows(80)
    for row in candidates[:60]:
        row["fixture_rejected"] = True
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert pool.checked == ["R060", "R061"]
    assert len(pool.sent) == 2


@pytest.mark.parametrize("send_result", [True, False])
def test_expanded_pool_preserves_ten_sender_attempts_even_on_failed_result(pool, monkeypatch, send_result):
    calls = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kw: calls.append(kw) or send_result)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows(120), "stocks")
    assert len(calls) == len(pool.checked) == 10
    assert all(f"stock_strategy_R{i:03}" in pool.released for i in range(10, 50))
    assert not any(f"stock_strategy_R{i:03}" in api._EMAIL_COOLDOWN for i in range(10, 120))


def test_unknown_send_consumes_quota_but_never_releases_attempted_claim(pool, monkeypatch):
    calls = []
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", REAL_RELEASE_AFTER_SEND)
    monkeypatch.setattr(api, "_last_delivery_outcome", lambda: "unknown")
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kw: calls.append(kw) and False)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows(120), "stocks")
    assert len(calls) == len(pool.checked) == 10
    assert not any(f"stock_strategy_R{i:03}" in pool.released for i in range(10))
    assert all(f"stock_strategy_R{i:03}" in pool.released for i in range(10, 50))


def test_expanded_pool_watch_and_trade_routes_share_same_row_quota(pool, monkeypatch):
    candidates = rows(70)
    for row in candidates[:55]:
        row["fixture_rejected"] = True
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **kw: {
        "state": "RED", "layer": "market", "reason_tag": "market_regime_red",
    } if int(kw["calibration_row"]["ticker"][1:]) < 61 else None)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", candidates, "stocks")
    assert len(pool.sent) == 5
    assert pool.sent[0]["mail_class"] == "watch"
    assert len(pool.sent[0]["delivery_dedupe_keys"]) == 6
    assert pool.checked == [f"R{i:03}" for i in range(61, 65)]


def test_malformed_late_row_invalidates_only_its_leaf_and_preserves_sibling(pool, monkeypatch):
    broken = rows(30)
    broken[29]["score"] = float("nan")
    sibling = rows(1)[0]
    sibling.update(ticker="VALID", Ticker="VALID", Strategy=STRATEGIES[1])
    def scan(name, **kwargs):
        return deepcopy(broken if name == STRATEGIES[0] else [sibling] if name == STRATEGIES[1] else [])
    monkeypatch.setattr(api, "_strategy_scan_wrapper", scan)
    with pytest.raises(api.ScannerDataError, match="scan_data_incomplete"):
        api._stock_strategy_alert_sweep_wrapper()
    assert pool.checked == ["VALID"]
    assert len(pool.sent) == 1
    assert pool.attempts[-1][1]["diagnostics"]["strategies_failed"] == 1


def test_unbounded_leaf_output_fails_without_silently_truncating_it(pool, monkeypatch):
    monkeypatch.setattr(api, "_strategy_scan_wrapper",
                        lambda name, **k: rows(151) if name == STRATEGIES[0] else [])
    with pytest.raises(api.ScannerDataError, match="scan_data_incomplete"):
        api._stock_strategy_alert_sweep_wrapper()
    assert pool.sent == [] and pool.classified == []
