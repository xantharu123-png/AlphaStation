"""Execute the shipped Admin-only mail evidence view with disposable fixtures."""
import json
from pathlib import Path

import pytest

from test_frontend_scanner_lifecycle import SOURCE, node_run


def shipped_view():
    start = SOURCE.find("function stockMailAuditCounts(")
    assert start >= 0, "Admin must render the persisted per-attempt mail evidence"
    return SOURCE[start:SOURCE.index("function AdminTab(", start)]


def evaluate(payload):
    return json.loads(node_run(shipped_view().split("function CompletedStockMailAudit(")[0]
        + "\nconsole.log(JSON.stringify(stockMailAuditCounts(" + json.dumps(payload) + ")));"))


def test_missing_mail_evidence_is_unknown_not_a_measured_zero():
    assert evaluate({}) == dict.fromkeys(("candidates", "sender", "accepted", "partial", "partialUnknown", "failed", "unknown", "queued"))


def test_sparse_valid_audit_preserves_observed_zero_and_distinct_message_events():
    assert evaluate({"mail_audit": {"schema_version": 1, "candidate_rows": 0,
        "transport_events": {"trade_sender_called": 2, "trade_accepted": 1,
            "trade_partial": 1, "trade_failed": 0}}}) == {
        "candidates": 0, "sender": 2, "accepted": 1, "partial": 1, "partialUnknown": None,
        "failed": 0, "unknown": None, "queued": None,
    }


def test_uncertain_partial_acceptance_and_queue_events_are_not_hidden_or_summed():
    counts = evaluate({"mail_audit": {"schema_version": 1, "transport_events": {
        "trade_partial_unknown": 2, "trade_unknown": 1, "trade_queued": 3,
    }}})
    assert counts["partialUnknown"] == 2
    assert counts["unknown"] == 1
    assert counts["queued"] == 3
    assert counts["accepted"] is None and counts["partial"] is None


@pytest.mark.parametrize("bad", [True, -1, 1.5, "PRIVATE", 10**9 + 1, None])
def test_invalid_counts_never_become_zero_or_leak_raw_values(bad):
    assert evaluate({"mail_audit": {"schema_version": 1, "candidate_rows": bad,
        "transport_events": {"trade_sender_called": bad, "trade_accepted": bad,
            "trade_failed": bad}}}) == dict.fromkeys(("candidates", "sender", "accepted", "partial", "partialUnknown", "failed", "unknown", "queued"))


@pytest.mark.parametrize("audit", [None, {}, {"schema_version": True}, {"schema_version": 2}])
def test_invalid_audit_does_not_claim_mail_activity(audit):
    assert evaluate({"mail_audit": audit}) == dict.fromkeys(("candidates", "sender", "accepted", "partial", "partialUnknown", "failed", "unknown", "queued"))


def render(attempts, sweep=None):
    # The real JSX is compiled with the bundled production compiler. A tiny
    # createElement recorder permits assertions on the actual rendered tree,
    # without copying the view or needing a browser/network for unit tests.
    vendor = Path(__file__).resolve().parent / "frontend/vendor/babel.min.js"
    harness = """
const vm=require('node:vm'), fs=require('node:fs');
const ctx={console}; ctx.self=ctx; ctx.window=ctx; vm.createContext(ctx);
vm.runInContext(fs.readFileSync(BABEL_PATH,'utf8'),ctx);
const React={createElement:(tag,props,...children)=>({tag,props:props||{},children})};
eval(ctx.Babel.transform(VIEW,{presets:['react'],sourceType:'script'}).code);
console.log(JSON.stringify(CompletedStockMailAudit({attempts:ATTEMPTS,sweep:SWEEP})));
"""
    return json.loads(node_run("const BABEL_PATH=" + json.dumps(str(vendor)) + ";const VIEW="
        + json.dumps(shipped_view()) + ";const ATTEMPTS=" + json.dumps(attempts)
        + ";const SWEEP=" + json.dumps(sweep) + ";" + harness))


def test_admin_view_is_collapsed_and_empty_evidence_is_not_a_zero_history():
    tree = render([])
    assert tree["tag"] == "details"
    assert tree["props"].get("open") is not True
    assert "Keine gespeicherte Mailprüfung verfügbar" in json.dumps(tree, ensure_ascii=False)
    assert "0 Mails" not in json.dumps(tree)


def test_view_keeps_attempts_separate_and_never_echoes_private_fields():
    tree = render([
        {"strategy": "Momentum Breakout Long", "status": "complete", "attempt_run_id": "a"*32,
         "updated_at": "2026-10-08T07:00:00Z", "private": "PRIVATE_RECIPIENT",
         "mail_audit": {"schema_version": 1, "candidate_rows": 3, "recipient": "PRIVATE_RECIPIENT",
            "transport_events": {"trade_sender_called": 1, "trade_accepted": 0}}},
        {"strategy": "Gap Momentum Short", "status": "error", "attempt_run_id": "b"*32,
         "updated_at": "2026-10-07T06:00:00Z", "mail_audit": None},
    ])
    text = json.dumps(tree, ensure_ascii=False)
    assert "Momentum Breakout Long" in text and "Gap Momentum Short" in text
    assert "Abgeschlossen" in text and "Fehler" in text
    assert "PRIVATE" not in text
    assert "—" in text
    assert "SMTP-Annahme" in text
    # Timestamps and IDs stay attached to their own row, never an aggregate.
    def nodes(value):
        if isinstance(value, dict):
            yield value
            for child in value.get("children", []):
                yield from nodes(child)
        elif isinstance(value, list):
            for child in value:
                yield from nodes(child)
    rows = [item for item in nodes(tree) if item.get("tag") == "tr" and "data-run-id" in item.get("props", {})]
    assert [row["props"]["data-run-id"] for row in rows] == ["a"*32, "b"*32]
    assert "2026-10-08" in json.dumps(rows[0]) and "2026-10-07" in json.dumps(rows[1])


def test_automatic_sweep_mail_evidence_is_visible_even_when_leaf_did_not_send():
    tree = render([
        {"strategy":"Momentum Breakout Long","status":"complete","attempt_run_id":"a"*32,
         "updated_at":"2026-10-08T07:00:00Z"},
    ], {"available":True,"status":"complete","attempt_run_id":"b"*32,
        "updated_at":"2026-10-08T07:05:00Z", "mail_audit":{
            "schema_version":1,"candidate_rows":6,"transport_events":{
                "trade_sender_called":2,"trade_accepted":1,"trade_failed":1,
            }}})
    text = json.dumps(tree,ensure_ascii=False)
    assert "Aktien Auto-Sweep" in text
    assert "Momentum Breakout Long" in text
    assert "Automatischer Sammellauf" in text
    assert '"children": [2]' in text and '"children": [6]' in text
    assert '"data-run-id": "' + "b"*32 + '"' in text
    assert '"data-run-id": "' + "a"*32 + '"' in text


def test_missing_automatic_sweep_does_not_invent_a_zero_mail_run():
    tree = render([], {"available":False,"reason":"missing"})
    assert "Keine gespeicherte Mailprüfung verfügbar" in json.dumps(tree,ensure_ascii=False)
    assert "Aktien Auto-Sweep" not in json.dumps(tree)
