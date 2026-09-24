"""Offline exact-owner renewal for bounded, slow stock mail reserves."""
import math

import pytest
import api
from modules import email_dedupe as store
from test_mail_pipeline_audit_regressions import _selection_fixture


def test_same_owner_renewal_changes_cleanup_identity_without_sending(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert store.email_delivery_claim(path, "trade", 28800, claim_ttl_seconds=900, now=1000)
    assert store.email_delivery_renew(path, "trade", claimed_at=1000, now=1600)
    assert not store.email_delivery_release(path, "trade", claimed_at=1000)
    assert not store.email_delivery_claim(path, "trade", 28800, claim_ttl_seconds=900, now=2000)
    assert not store.email_dedupe_active(path, "trade", 28800, now=2000)
    assert store.email_delivery_release(path, "trade", claimed_at=1600)


def test_expired_lease_cannot_renew_or_release_replacement_owner(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert store.email_delivery_claim(path, "trade", 28800, claim_ttl_seconds=900, now=1000)
    assert store.email_delivery_claim(path, "trade", 28800, claim_ttl_seconds=900, now=2000)
    assert not store.email_delivery_renew(path, "trade", claimed_at=1000, now=2001)
    assert not store.email_delivery_release(path, "trade", claimed_at=1000)
    assert store.load_email_dedupe(path, now=2002)["__delivery_claim__:trade"] == 2000


def test_expired_but_still_exact_owner_can_renew_atomically(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert store.email_delivery_claim(path, "trade", 28800, now=1000)
    assert store.email_delivery_renew(path, "trade", claimed_at=1000, now=2000)
    assert not store.email_delivery_claim(path, "trade", 28800, now=2001)


def test_sent_or_missing_lease_cannot_be_renewed(tmp_path):
    path = str(tmp_path / "dedupe.json")
    assert not store.email_delivery_renew(path, "trade", claimed_at=1000, now=1001)
    assert store.email_delivery_claim(path, "trade", 28800, now=1000)
    store.email_delivery_mark(path, "trade", now=1001)
    assert not store.email_delivery_renew(path, "trade", claimed_at=1000, now=1002)
    assert store.email_dedupe_active(path, "trade", 28800, now=1003)


@pytest.mark.parametrize("expected,current", [(1000, 999), (math.nan, 1001), (1000, math.inf)])
def test_renewal_rejects_invalid_clock_without_touching_store(tmp_path, expected, current):
    path = str(tmp_path / "dedupe.json")
    assert store.email_delivery_claim(path, "trade", 28800, now=1000)
    assert not store.email_delivery_renew(path, "trade", claimed_at=expected, now=current)
    assert store.load_email_dedupe(path, now=1002)["__delivery_claim__:trade"] == 1000


def _real_leases(monkeypatch, tmp_path, size=1):
    rows, checked, sent, _tracked, _events, _released = _selection_fixture(monkeypatch, size)
    path = str(tmp_path / "mail-dedupe.json")
    clock = [1000.0]
    monkeypatch.setattr(api.time, "time", lambda: clock[0])
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", path)
    monkeypatch.setattr(api, "_mail_outbox", None)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda key, ttl, **kw:
                        store.email_delivery_claim(path, key, ttl, claim_ttl_seconds=900, **kw))
    released = []

    def release(key, **kw):
        released.append((key, kw.get("claimed_at")))
        return store.email_delivery_release(path, key, **kw)

    monkeypatch.setattr(api, "_email_dedupe_release", release)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", release)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda key, **kw: store.email_delivery_mark(path, key, **kw))
    return rows, checked, sent, path, clock, released


def test_slow_rejected_row_releases_renewed_timestamp(monkeypatch, tmp_path):
    rows, checked, sent, path, clock, released = _real_leases(monkeypatch, tmp_path)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *_a, **_k: clock.__setitem__(0, 1600) or False)

    def reject(row, **_kwargs):
        checked.append(row["ticker"])
        return {"ok": False, "reason": "final_stop_touched_since_scan"}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", reject)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == ["R00"] and sent == []
    assert released == [("stock_strategy_R00", 1600)]
    assert store.load_email_dedupe(path, now=1601) == {}


@pytest.mark.parametrize("takeover_stage", ["before_validation", "during_validation"])
def test_lost_lease_never_sends_or_releases_other_owner(monkeypatch, tmp_path, takeover_stage):
    rows, checked, sent, path, clock, _released = _real_leases(monkeypatch, tmp_path)

    def takeover():
        clock[0] = 2000
        assert store.email_delivery_claim(path, "stock_strategy_R00", 28800, claim_ttl_seconds=900, now=2000)

    if takeover_stage == "before_validation":
        monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *_a, **_k: takeover() or False)
    else:
        def validate(row, **_kwargs):
            checked.append(row["ticker"])
            takeover()
            return {"ok": True, "candidate": dict(row)}
        monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert sent == []
    assert checked == ([] if takeover_stage == "before_validation" else ["R00"])
    assert store.load_email_dedupe(path, now=2001)["__delivery_claim__:stock_strategy_R00"] == 2000


def test_slow_valid_row_renews_and_marks_actual_send_time(monkeypatch, tmp_path):
    rows, checked, sent, path, clock, released = _real_leases(monkeypatch, tmp_path)

    def validate(row, **_kwargs):
        checked.append(row["ticker"])
        clock[0] = 1700
        return {"ok": True, "candidate": dict(row)}

    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", validate)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == ["R00"] and len(sent) == 1 and released == []
    assert store.load_email_dedupe(path, now=1701) == {"stock_strategy_R00": 1700}
    assert api._EMAIL_COOLDOWN == {"stock_strategy_R00": 1700}


def _watch_regime(monkeypatch, layer):
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *_a, **_k: {
        "state": "RED", "layer": layer, "reason_tag": "market_regime_red" if layer == "market" else "regime_cooldown",
        "state_key": "review_cell", "watch_cap_seconds": 72000,
    })


@pytest.mark.parametrize("layer", ["market", "breaker"])
def test_watch_render_takeover_prevents_send_and_preserves_replacement(monkeypatch, tmp_path, layer):
    rows, checked, sent, path, clock, _released = _real_leases(monkeypatch, tmp_path)
    _watch_regime(monkeypatch, layer)

    def render_timestamp(*_a, **_k):
        clock[0] = 2000
        assert store.email_delivery_claim(path, "stock_strategy_R00__watch", 28800, claim_ttl_seconds=900, now=2000)
        return "rendered"

    monkeypatch.setattr(api, "_mail_timestamp_dual", render_timestamp)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [] and sent == []
    assert store.load_email_dedupe(path, now=2001)["__delivery_claim__:stock_strategy_R00__watch"] == 2000
    assert not api._EMAIL_COOLDOWN


@pytest.mark.parametrize("layer", ["market", "breaker"])
def test_watch_partial_ownership_rebuilds_body_keys_count_and_actual_timestamp(monkeypatch, tmp_path, layer):
    rows, checked, sent, path, clock, _released = _real_leases(monkeypatch, tmp_path, size=2)
    _watch_regime(monkeypatch, layer)

    def render_timestamp(*_a, **_k):
        clock[0] = 2000
        assert store.email_delivery_claim(path, "stock_strategy_R00__watch", 28800, claim_ttl_seconds=900, now=2000)
        return "rendered"

    monkeypatch.setattr(api, "_mail_timestamp_dual", render_timestamp)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [] and len(sent) == 1
    assert sent[0]["delivery_dedupe_keys"] == ["stock_strategy_R01__watch"]
    assert "1 Setup(s)" in sent[0]["subject"]
    assert "R01" in sent[0]["body"] and "R00" not in sent[0]["body"]
    stored = store.load_email_dedupe(path, now=2001)
    assert stored["__delivery_claim__:stock_strategy_R00__watch"] == 2000
    assert stored["stock_strategy_R01__watch"] == 2000
    assert "stock_strategy_R00__watch" not in stored
    assert api._EMAIL_COOLDOWN == {"stock_strategy_R01__watch": 2000}


def test_breaker_cap_takeover_prevents_watch_and_releases_renewed_row(monkeypatch, tmp_path):
    rows, checked, sent, path, clock, _released = _real_leases(monkeypatch, tmp_path)
    _watch_regime(monkeypatch, "breaker")
    cap_key = []

    def render_timestamp(*_a, **_k):
        clock[0] = 2000
        cap_key.append(next(key.removeprefix("__delivery_claim__:")
                            for key in store.load_email_dedupe(path, now=2000)
                            if key.startswith("__delivery_claim__:regime_cooldown_watch_")))
        assert store.email_delivery_claim(path, cap_key[0], 72000, claim_ttl_seconds=900, now=2000)
        return "rendered"

    monkeypatch.setattr(api, "_mail_timestamp_dual", render_timestamp)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert checked == [] and sent == []
    assert store.load_email_dedupe(path, now=2001) == {"__delivery_claim__:" + cap_key[0]: 2000}


@pytest.mark.parametrize("layer", ["market", "breaker"])
def test_slow_watch_definite_failure_releases_updated_ownership(monkeypatch, tmp_path, layer):
    rows, _checked, sent, path, clock, released = _real_leases(monkeypatch, tmp_path)
    _watch_regime(monkeypatch, layer)
    monkeypatch.setattr(api, "_mail_timestamp_dual", lambda *_a, **_k: clock.__setitem__(0, 1700) or "rendered")

    def send(subject, body, **kwargs):
        sent.append(kwargs)
        return False

    monkeypatch.setattr(api, "_send_email_alert", send)
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")
    assert len(sent) == 1
    assert ("stock_strategy_R00__watch", 1700) in released
    assert store.load_email_dedupe(path, now=1701) == {}
    assert not api._EMAIL_COOLDOWN
