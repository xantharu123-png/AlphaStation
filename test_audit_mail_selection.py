"""Offline regression: final executability, not cache position, selects the mail."""
import json
import socket

import pytest

import api


@pytest.mark.parametrize("scanner", ["bi_long", "bi_short", "biotech", "bear"])
@pytest.mark.parametrize("send_ok", [True, False])
def test_finally_rejected_first_row_does_not_starve_second_row(monkeypatch, tmp_path, scanner, send_ok):
    rows = [dict(ticker=t, price=100, RVOL=3, direction="LONG") for t in ("FIRST", "SECOND", "THIRD")]
    cache = tmp_path / "scan.json"
    cache.write_text(json.dumps({"results": rows}), encoding="utf-8")
    outstanding, claimed, released, marked, checked, sent, suppressions = set(), [], [], [], [], [], []

    def no_external_side_effect(*args, **kwargs):
        raise AssertionError("The selection regression must remain fully offline")

    monkeypatch.setattr(socket.socket, "connect", no_external_side_effect)
    monkeypatch.setattr(api, "_EMAIL_COOLDOWN", {})
    # Indicator/quality gates have independent contract tests; this test starts
    # with three previously admissible rows and exercises the final mail loop.
    monkeypatch.setattr(api, "_filter_bi_signal_rows", lambda scanner, data: data)
    monkeypatch.setattr(api, "_stock_trade_email_allowed", lambda *a, **k: (True, "test"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({r["ticker"] for r in rows}, "test"))
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda scanner, row: dict(row))
    monkeypatch.setattr(api, "_attach_stock_company_name", lambda row, *a, **k: dict(row))
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)
    monkeypatch.setattr(api, "_classify_alert_candidate", lambda scanner, row, now: {
        "ticker": row["ticker"], "grade": "A", "score": 95, "rvol": 3,
        "alertable_now": True, "suppression_reasons": [], "cooldown_key": row["ticker"],
    })
    monkeypatch.setattr(api, "_conservative_regular_session_anchor", lambda row, **k: row)
    monkeypatch.setattr(api, "_format_alert_plan_html", lambda row: "reviewed plan")
    monkeypatch.setattr(api, "_format_stock_identity_html", lambda ticker, row: ticker)
    monkeypatch.setattr(api, "_cluster_warning_html", lambda *a, **k: "")
    monkeypatch.setattr(api, "_safe_format_telegram_rows", lambda *a, **k: "test")
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_suppression_counts", lambda scanner, counts: suppressions.append(dict(counts)))
    monkeypatch.setattr(api, "_safe_record_alert_signals", no_external_side_effect)
    monkeypatch.setattr(api, "_mark_bearish_stock_alert", lambda *a, **k: None)

    def claim(key, *args, **kwargs):
        assert key not in outstanding
        outstanding.add(key)
        claimed.append(key)
        return True

    def release(key, **kwargs):
        outstanding.discard(key)
        released.append(key)

    def mark(key, **kwargs):
        outstanding.discard(key)
        marked.append(key)

    def final_revalidation(row, **kwargs):
        checked.append(row["ticker"])
        if row["ticker"] == "FIRST":
            return {"ok": False, "reason": "live_quote_no_longer_executable"}
        assert row["ticker"] == "SECOND", "Third row must remain deferred, not acquire another live quote"
        return {"ok": True, "candidate": dict(row, price=101, final_quote_checked=True)}

    def send(subject, body, **kwargs):
        sent.append((subject, body, kwargs))
        assert outstanding == {"SECOND"}
        return send_ok

    monkeypatch.setattr(api, "_email_dedupe_claim", claim)
    monkeypatch.setattr(api, "_email_dedupe_release", release)
    monkeypatch.setattr(api, "_email_dedupe_release_after_send", release)
    monkeypatch.setattr(api, "_email_dedupe_mark", mark)
    monkeypatch.setattr(api, "_revalidate_stock_strategy_mail_candidate", final_revalidation)
    monkeypatch.setattr(api, "_send_email_alert", send)

    api._check_and_alert(scanner, str(cache))

    assert checked == ["FIRST", "SECOND"]
    assert claimed == ["FIRST", "SECOND", "THIRD"]
    assert len(sent) == 1
    _subject, body, kwargs = sent[0]
    assert "SECOND" in body and "FIRST" not in body and "THIRD" not in body
    assert kwargs["delivery_dedupe_keys"] == ["SECOND"]
    assert [row["ticker"] for row in kwargs["tracking_rows"]] == ["SECOND"]
    assert kwargs["tracking_rows"][0]["price"] == 101
    assert kwargs["tracking_rows"][0]["final_quote_checked"] is True
    assert outstanding == set()
    assert released == (["FIRST", "THIRD"] if send_ok else ["FIRST", "THIRD", "SECOND"])
    assert marked == (["SECOND"] if send_ok else [])
    assert set(api._EMAIL_COOLDOWN) == ({"SECOND"} if send_ok else set())
    assert {"live_quote_no_longer_executable": 1} in suppressions
    assert {"mail_adjacent_single_candidate_deferred": 1} in suppressions
