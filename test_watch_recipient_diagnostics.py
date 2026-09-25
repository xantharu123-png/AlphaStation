"""Recipient diagnostics in isolation: no app import, credentials or SMTP."""

import ast
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterable, List, Optional

import pytest


@pytest.fixture(scope="module")
def mail_function_code():
    # Compile only the two source functions, never API startup/config loading.
    path = Path(__file__).with_name("api.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    wanted = {"_resolve_email_alert_recipients", "_send_email_alert"}
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert {node.name for node in nodes} == wanted
    return compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec")


@pytest.fixture
def mail_scope(mail_function_code):
    events, suppressions, transport = [], [], []
    ns = dict(Any=Any, Dict=Dict, Iterable=Iterable, List=List, Optional=Optional,
              datetime=datetime, os=SimpleNamespace(environ={}),
              time=SimpleNamespace(time=lambda: 10_000),
              _SECRETS={"GMAIL_USER": "operator@example.invalid",
                        "GMAIL_APP_PASSWORD": "synthetic-only",
                        "ALERT_EMAIL": "operator@example.invalid"},
              _EMAIL_STARTUP_TIME=0, _EMAIL_STARTUP_DELAY=300,
              HAS_AUTH=True, ALERT_SEND_TO_SUBSCRIBERS=True,
              mail_channel_enabled=lambda *_: True,
              get_email_alert_recipients=lambda **_: [],
              scan_mail_audit=SimpleNamespace(transport=lambda *a: transport.append(a)),
              _normalize_mail_rendered_at=lambda v: v,
              _set_last_delivery_recipients=lambda _: None,
              _set_last_delivery_outcome=lambda _: None,
              _apply_mail_class_subject=lambda subject, *_a, **_k: subject,
              _email_has_blocked_etf_content=lambda *_: False,
              _record_email_event=lambda *a: events.append(a),
              _record_suppression_counts=lambda *a: suppressions.append(a))
    exec(mail_function_code, ns)
    return ns, events, suppressions, transport


def test_watch_without_optin_is_not_a_missing_global_recipient(mail_scope, capsys):
    ns, events, suppressions, transport = mail_scope
    assert ns["_send_email_alert"]("WATCH", "<p>watch</p>", mail_class="watch", mail_channel="crypto") is False
    assert events == [("WATCH", "skipped", "watch_no_eligible_recipients")]
    assert suppressions == [("crypto", {"watch_no_eligible_recipients": 1})]
    assert transport == [("watch", "sender_called")]
    assert "kein SMTP-Versuch" in capsys.readouterr().out
    assert "ALERT_OPERATOR_WATCH_OPTIN" not in ns["_SECRETS"]


def test_watch_channel_optout_has_same_honest_eligibility_reason(mail_scope):
    ns, events, _, _ = mail_scope
    ns["_SECRETS"]["ALERT_OPERATOR_WATCH_OPTIN"] = "1"
    ns["mail_channel_enabled"] = lambda *_: False
    assert ns["_send_email_alert"]("WATCH", "x", mail_class="watch", mail_channel="crypto") is False
    assert events[-1][2] == "watch_no_eligible_recipients"


def test_empty_explicit_watch_recipient_does_not_fallback(mail_scope):
    ns, events, _, _ = mail_scope
    ns["_SECRETS"]["ALERT_OPERATOR_WATCH_OPTIN"] = "1"
    assert ns["_send_email_alert"]("WATCH", "x", mail_class="watch", recipient_emails=[]) is False
    assert events[-1][2] == "watch_no_eligible_recipients"


@pytest.mark.parametrize("mail_class", ["trade", "swing_trade", "info", "signal_update"])
def test_other_empty_cohorts_keep_missing_recipient_reason(mail_scope, mail_class):
    ns, events, _, _ = mail_scope
    assert ns["_send_email_alert"]("OTHER", "x", mail_class=mail_class, recipient_emails=[]) is False
    assert events[-1][2] == "missing_recipient"


def test_missing_gmail_is_not_misclassified_as_watch_optin(mail_scope):
    ns, events, _, _ = mail_scope
    ns["_SECRETS"].pop("GMAIL_APP_PASSWORD")
    assert ns["_send_email_alert"]("WATCH", "x", mail_class="watch") is False
    assert events[-1][2] == "missing_gmail_config"


def test_routing_is_unchanged_for_opted_in_watch_and_normal_trade(mail_scope):
    ns, _, _, _ = mail_scope
    resolve = ns["_resolve_email_alert_recipients"]
    assert resolve(mail_class="trade", mail_channel="crypto") == ["operator@example.invalid"]
    assert resolve(mail_class="watch", mail_channel="crypto") == []
    ns["_SECRETS"]["ALERT_OPERATOR_WATCH_OPTIN"] = "1"
    assert resolve(mail_class="watch", mail_channel="crypto") == ["operator@example.invalid"]
    ns["mail_channel_enabled"] = lambda *_: False
    assert resolve(mail_class="watch", mail_channel="crypto") == []
    ns["get_email_alert_recipients"] = lambda **_: ["subscriber@example.invalid"]
    assert resolve(mail_class="watch", mail_channel="crypto") == ["subscriber@example.invalid"]
    assert resolve(mail_class="watch", recipient_emails=[]) == []


def test_new_reason_is_a_reviewed_suppression_identifier():
    from modules.suppression_telemetry import ALLOWED_SUPPRESSION_REASONS
    assert "watch_no_eligible_recipients" in ALLOWED_SUPPRESSION_REASONS
