"""Mail timeframes are row-local; one bad sibling cannot poison a daily plan."""
from copy import deepcopy
from datetime import datetime, timezone
import json

import pytest
import api
from modules import stock_swing_contract as swing
from test_stock_starter_swing import plan, utc
from test_stock_strategy_final_revalidation import _patch_strategy_mail_pipeline, _row

REAL_CLASSIFIER = api._classify_alert_candidate


def _pipeline(monkeypatch, tmp_path, stamp="2026-09-15T00:00:00Z"):
    sent, tracked, events, released = _patch_strategy_mail_pipeline(monkeypatch)
    now = utc(stamp)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz) if tz else now.replace(tzinfo=None)

    monkeypatch.setattr(api, "datetime", Clock)
    monkeypatch.setattr(swing, "datetime", Clock)
    monkeypatch.setattr(api.time, "time", lambda: now.timestamp())
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": False, "session": "CLOSED"})
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "dedupe.json"))
    monkeypatch.setattr(api, "_ensure_stock_business_quality", lambda r: r)
    monkeypatch.setattr(api, "rate_limited_get", lambda *a, **k: pytest.fail("no external provider"))
    counts = []
    monkeypatch.setattr(api, "_record_suppression_counts", lambda scanner, reasons: counts.append(dict(reasons)))
    return sent, counts


@pytest.mark.parametrize("bad_kind", ["stale", "tampered", "legacy"])
@pytest.mark.parametrize("bad_first", [True, False])
def test_bad_sibling_cannot_block_current_completed_daily_plan(monkeypatch, tmp_path, bad_kind, bad_first):
    sent, counts = _pipeline(monkeypatch, tmp_path)
    good = plan(ticker="AAA")
    if bad_kind == "legacy":
        bad = _row(ticker="BBB")
    else:
        bad = plan(ticker="BBB")
        bad.update(swing.metadata("2026-09-11", 100) if bad_kind == "stale" else {"stock_swing_contract_version": 0})
    rows = [bad, good] if bad_first else [good, bad]
    before = deepcopy(rows)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert [message["tracking_rows"][0]["ticker"] for message in sent] == ["AAA"]
    assert all(message["mail_channel"] == "stocks_swing" for message in sent)
    assert rows == before
    assert any(reason in batch for batch in counts for reason in (
        "swing_daily_reference_invalid_or_stale", "stock_session_not_executable"))


def test_daily_row_keeps_daily_confirmation_even_with_legacy_regular_row(monkeypatch, tmp_path):
    sent, _ = _pipeline(monkeypatch, tmp_path, "2026-09-15T18:00:00Z")
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": True, "session": "US_REGULAR"})
    modes = []
    monkeypatch.setattr(api, "_stock_strategy_mail_quality_state", lambda row, **kw: modes.append((row["ticker"], kw["daily_close_confirmed_mode"])) or (False, "fixture_end"))
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [plan(ticker="AAA"), _row(ticker="BBB")], "stocks")
    assert modes == [("AAA", True), ("BBB", False)]
    assert sent == []


def test_invalid_daily_row_is_not_downgraded_into_live_or_premarket(monkeypatch, tmp_path):
    sent, counts = _pipeline(monkeypatch, tmp_path, "2026-09-15T12:00:00Z")
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a: True)
    monkeypatch.setattr(api, "_classify_premarket_candidate", lambda *a: pytest.fail("bad daily row cannot become PM"))
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [plan(stock_swing_contract_version=0)], "stocks")
    assert sent == []
    assert any(batch.get("swing_daily_reference_invalid_or_stale") == 1 for batch in counts)


def test_mixed_premarket_and_daily_keep_own_classifiers_final_session_and_channel(monkeypatch, tmp_path):
    sent, _ = _pipeline(monkeypatch, tmp_path, "2026-09-15T12:00:00Z")
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a: True)
    real_classifier = api._classify_alert_candidate
    pm_seen = []
    monkeypatch.setattr(api, "_classify_premarket_candidate", lambda scanner, row, now: pm_seen.append(row["ticker"]) or real_classifier(scanner, row, now))
    final_sessions = []
    def validate(row, **kwargs):
        final_sessions.append((row["ticker"], kwargs["price_session"]))
        return {"ok": True, "candidate": dict(row)}
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [plan(ticker="AAA"), _row(ticker="BBB", Premarket=True)], "stocks")
    assert pm_seen == ["BBB"]
    assert final_sessions == [("AAA", "CLOSED"), ("BBB", "PREMARKET")]
    assert [message["mail_channel"] for message in sent] == ["stocks_swing", "stocks_premarket"]
    assert "Swing" in sent[0]["subject"] and "Pre-Market" in sent[1]["subject"]
    assert "PRE-MARKET-FRUEHWARNUNG" not in sent[0]["body"]
    assert "PRE-MARKET-FRUEHWARNUNG" in sent[1]["body"]


@pytest.mark.parametrize("scanner", ["bi_long", "bi_short", "bear"])
def test_cache_mail_session_gate_isolates_stale_daily_sibling(monkeypatch, tmp_path, scanner):
    sent, counts = _pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *a, **k: pytest.fail("daily admission must not log whole-batch market-closed skip"))
    # This test isolates session admission, not the separately tested 17/20
    # contract. No real transport, provider, or tracker is involved.
    monkeypatch.setattr(api, "_filter_bi_signal_rows", lambda scanner, rows: rows)
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", lambda row, **kw: {"ok": True, "candidate": dict(row)})
    monkeypatch.setattr(api, "_bearish_stock_alert_remaining", lambda *a: 0)
    monkeypatch.setattr(api, "_mark_bearish_stock_alert", lambda *a, **kw: None)
    rows = [plan(ticker="BBB", stock_swing_contract_version=0), plan(ticker="AAA")]
    cache = tmp_path / "daily-cache.json"
    cache.write_text(json.dumps({"results": rows}), encoding="utf-8")
    api._check_and_alert(scanner, str(cache))
    assert [message["tracking_rows"][0]["ticker"] for message in sent] == ["AAA"]
    assert any(batch.get("swing_daily_reference_invalid_or_stale") == 1 for batch in counts)


@pytest.mark.parametrize("scanner", ["orb", "penny_stocks"])
def test_non_swing_cache_cannot_bypass_session_with_daily_fields(monkeypatch, tmp_path, scanner):
    sent, counts = _pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *a, **kw: (False, "closed"))
    cache = tmp_path / "intraday-cache.json"
    cache.write_text(json.dumps({"results": [plan()]}), encoding="utf-8")
    api._check_and_alert(scanner, str(cache))
    assert sent == []
    assert any(batch.get("stock_session_not_executable") == 1 for batch in counts)


def _real_classifier_pipeline(monkeypatch, tmp_path):
    sent, counts = _pipeline(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_classify_alert_candidate", REAL_CLASSIFIER)
    monkeypatch.setattr(api, "_stock_alert_trade_score", lambda *a: 95)
    monkeypatch.setattr(api, "_stock_swing_rule_reasons", lambda *a: [])
    monkeypatch.setattr(api, "_structural_barrier_alert_reason", lambda *a: None)
    monkeypatch.setattr(api, "_alert_trade_health_reasons", lambda *a: [])
    return sent, counts


@pytest.mark.parametrize("storage", ["memory", "durable"])
def test_regular_cooldown_cannot_block_separate_daily_close_identity(monkeypatch, tmp_path, storage):
    sent, counts = _real_classifier_pipeline(monkeypatch, tmp_path)
    row = plan(ticker="AAA")
    normal = api._alert_signal_identity_key("stock_strategy", row)
    if storage == "memory":
        api._EMAIL_COOLDOWN[normal] = api.time.time() - 60
    else:
        api._save_email_dedupe({normal: api.time.time() - 60})
    claimed = []
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, *a, **kw: claimed.append(key) or True)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [row], "stocks")
    assert len(sent) == 1
    assert claimed == [normal + "_dailyclose"]
    assert sent[0]["delivery_dedupe_keys"] == claimed
    assert not any(batch.get("cooldown_active") or batch.get("persistent_dedupe_active") for batch in counts)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [row], "stocks")
    assert len(sent) == 1  # The same daily confirmation must not be sent twice.


@pytest.mark.parametrize("storage", ["memory", "durable"])
def test_existing_daily_close_identity_remains_blocked(monkeypatch, tmp_path, storage):
    sent, counts = _real_classifier_pipeline(monkeypatch, tmp_path)
    row = plan(ticker="AAA")
    key = api._alert_signal_identity_key("stock_strategy", row) + "_dailyclose"
    if storage == "memory":
        api._EMAIL_COOLDOWN[key] = api.time.time() - 60
    else:
        api._save_email_dedupe({key: api.time.time() - 60})
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **kw: pytest.fail("duplicate must not claim"))
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [row], "stocks")
    assert sent == []
    assert any(batch.get("cooldown_active") or batch.get("persistent_dedupe_active") or batch.get("dailyclose_dedupe_active") for batch in counts)
