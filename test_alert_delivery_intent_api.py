"""End-to-end API/SMTP/tracker delivery-intent regressions.

These tests use an isolated SQLite database and the real two-phase tracker
contract.  They prove that SMTP is entered only by the compare-and-set owner,
accepted mail is activated exactly once, and an unknown DATA outcome remains
non-replayable.
"""

from __future__ import annotations

import re
import sqlite3
from email import message_from_string

import pytest

import api
from modules import signal_tracker as tracker


def _row(ticker: str = "INTENT", *, entry: float = 100.0) -> dict:
    return {
        "Ticker": ticker,
        "Signal_Direction": "LONG",
        "Entry": entry,
        "StopLoss": entry - 5.0,
        "TP1": entry + 5.0,
        "TP2": entry + 10.0,
        "trade_horizon": "swing",
        "strategy": "intent-regression",
    }


def _setup(monkeypatch, tmp_path, smtp_cls) -> str:
    db_path = str(tmp_path / "signal_tracker.sqlite")
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", db_path)
    monkeypatch.setattr(api.smtplib, "SMTP", smtp_cls)
    monkeypatch.setattr(
        api,
        "_SECRETS",
        {
            "GMAIL_USER": "operator@example.com",
            "GMAIL_APP_PASSWORD": "test-only-password",
            "ALERT_EMAIL": "operator@example.com",
        },
    )
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "is_telegram_configured", lambda: False)
    return db_path


class _AcceptedSMTP:
    calls = 0
    messages = []
    recipient_batches = []

    def __init__(self, *args, **kwargs):
        pass

    def ehlo(self):
        pass

    def starttls(self, context=None):
        pass

    def login(self, user, password):
        pass

    def sendmail(self, sender, recipients, message):
        type(self).calls += 1
        type(self).messages.append(message)
        type(self).recipient_batches.append(tuple(recipients))
        return {}

    def quit(self):
        pass


def _decoded_wire_message(message: str) -> str:
    wire = message_from_string(message)
    return "\n".join(
        (part.get_payload(decode=True) or b"").decode(
            part.get_content_charset() or "utf-8", errors="replace"
        )
        for part in wire.walk()
        if part.get_content_type() in {"text/plain", "text/html"}
    )


def _decoded_wire_html(message: str) -> str:
    wire = message_from_string(message)
    html_parts = [
        (part.get_payload(decode=True) or b"").decode(
            part.get_content_charset() or "utf-8", errors="replace"
        )
        for part in wire.walk()
        if part.get_content_type() == "text/html"
    ]
    assert len(html_parts) == 1
    return html_parts[0]


def test_accepted_tracking_mail_is_activated_once_and_never_replayed(
    monkeypatch, tmp_path
):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    kwargs = {
        "bypass_startup_cooldown": True,
        "mail_class": "swing_trade",
        "trade_horizon": "swing",
        "mail_channel": "stocks_premarket",
        "tracking_scanner": "stock_strategy",
        "tracking_rows": [_row()],
        "delivery_dedupe_keys": ["intent-regression-key"],
    }

    assert api._send_email_alert("Intent E2E", "<p>x</p>", **kwargs) is True
    assert api._send_email_alert("Intent E2E", "<p>x</p>", **kwargs) is False
    assert _AcceptedSMTP.calls == 1
    decoded = _decoded_wire_message(_AcceptedSMTP.messages[0])
    assert "Signal-ID:" not in decoded
    with sqlite3.connect(db_path) as conn:
        public_ref = conn.execute(
            "SELECT public_signal_ref FROM signals"
        ).fetchone()[0]
    assert re.fullmatch(r"AS1-[0-9A-F]{20}", public_ref)
    assert decoded.count(f"Signal-Ref: {public_ref}") == 2

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT status, delivery_state, delivery_prepared_at, "
            "delivery_attempted_at, delivery_accepted_at, "
            "delivery_recipient_keys_json, mail_channel "
            "FROM signals"
        ).fetchone()
    assert row is not None
    assert row[0:2] == (tracker.STATUS_OPEN, "ACTIVE")
    assert all(row[index] for index in (2, 3, 4, 5))
    assert row[6] == "stocks_premarket"


def test_full_html_tracking_mail_keeps_public_ref_once_in_branded_html(
    monkeypatch, tmp_path
):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)

    assert api._send_email_alert(
        "Full HTML public ref",
        "<!doctype html><html><body><h2>Original body</h2></body></html>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("FULLHTML")],
        delivery_dedupe_keys=["full-html-public-ref"],
    ) is True

    with sqlite3.connect(db_path) as conn:
        public_ref = conn.execute(
            "SELECT public_signal_ref FROM signals"
        ).fetchone()[0]
    branded_html = _decoded_wire_html(_AcceptedSMTP.messages[0])
    assert "Original body" in branded_html
    assert branded_html.count(f"Signal-Ref: {public_ref}") == 1


def test_corrupt_prepared_tuple_between_render_and_claim_never_enters_smtp(
    monkeypatch, tmp_path
):
    """The prepared snapshot must be revalidated at the SMTP ownership CAS."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    original_brand = api._brand_email_html

    def _brand_then_corrupt(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE signals SET public_signal_ref=?, origin_evidence=?, entry=?",
                ("not-a-public-ref", "direct_post_send", 101.0),
            )
            conn.commit()
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_corrupt)

    assert api._send_email_alert(
        "Corrupt prepared tuple", "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade", trade_horizon="swing", mail_channel="stocks_swing",
        tracking_scanner="stock_strategy", tracking_rows=[_row("CLAIM-CORRUPTION")],
        delivery_dedupe_keys=["claim-corruption"],
    ) is False
    assert _AcceptedSMTP.calls == 0

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_attempted_at, delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)


@pytest.mark.parametrize(
    ("column", "mutated_value"),
    [
        ("asset_class", "crypto"),
        ("instrument_id", "tampered-instrument"),
        ("venue", "tampered-venue"),
        ("contract_symbol", "tampered-contract"),
        ("strategy", "tampered-strategy"),
        ("trade_horizon", "intraday"),
        ("setup_key", "tampered-setup"),
    ],
)
def test_every_canonical_identity_field_is_revalidated_before_smtp(
    monkeypatch, tmp_path, column, mutated_value
):
    """Mutating any canonical AS1 input after render must fail the SMTP claim."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    original_brand = api._brand_email_html

    def _brand_then_mutate_identity(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                f"UPDATE signals SET {column}=?",
                (mutated_value,),
            )
            conn.commit()
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_mutate_identity)
    row = _row("FULL-IDENTITY")
    row.update({
        "instrument_id": "original-instrument",
        "venue": "original-venue",
        "contract_symbol": "original-contract",
        "setup_key": "original-setup",
    })

    assert api._send_email_alert(
        "Canonical identity mutation",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[row],
        delivery_dedupe_keys=[f"canonical-mutation-{column}"],
    ) is False
    assert _AcceptedSMTP.calls == 0

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_attempted_at, "
            "delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)


@pytest.mark.parametrize("optout_layer", ["global", "channel", "horizon"])
def test_tracking_recipient_optout_after_render_never_reaches_smtp(
    monkeypatch, tmp_path, optout_layer
):
    """The final authorization lookup must catch every mutable opt-out layer."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    authorization = {"active": True}
    original_brand = api._brand_email_html

    monkeypatch.setattr(
        api,
        "_resolve_email_alert_recipients",
        lambda **_kwargs: (
            ["subscriber@example.com"] if authorization["active"] else []
        ),
    )

    def _brand_then_opt_out(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        authorization["active"] = False
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_opt_out)

    assert api._send_email_alert(
        f"{optout_layer} opt-out",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row(f"OPTOUT-{optout_layer}")],
        delivery_dedupe_keys=[f"optout-{optout_layer}"],
    ) is False
    assert _AcceptedSMTP.calls == 0

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_attempted_at, "
            "delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)


def test_explicit_tracking_recipient_is_still_reauthorized_before_smtp(
    monkeypatch, tmp_path
):
    """An explicit prepared address cannot bypass mutable user consent."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    authorization = {"active": True}
    original_brand = api._brand_email_html

    def _resolve(**kwargs):
        explicit = kwargs.get("recipient_emails")
        if explicit is not None:
            return list(explicit)
        return ["subscriber@example.com"] if authorization["active"] else []

    monkeypatch.setattr(api, "_resolve_email_alert_recipients", _resolve)

    def _brand_then_opt_out(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        authorization["active"] = False
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_opt_out)

    assert api._send_email_alert(
        "Explicit recipient opt-out",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        recipient_emails=["subscriber@example.com"],
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("EXPLICIT-OPTOUT")],
        delivery_dedupe_keys=["explicit-optout"],
    ) is False
    assert _AcceptedSMTP.calls == 0

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_attempted_at, "
            "delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "PREPARED", None, None)


def test_tracking_intent_never_adds_recipient_authorized_after_prepare(
    monkeypatch, tmp_path
):
    """A recipient added during rendering requires a new delivery intent."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    authorization = {"expanded": False}
    original_brand = api._brand_email_html

    monkeypatch.setattr(
        api,
        "_resolve_email_alert_recipients",
        lambda **_kwargs: (
            ["original@example.com", "new@example.com"]
            if authorization["expanded"]
            else ["original@example.com"]
        ),
    )

    def _brand_then_expand(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        authorization["expanded"] = True
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_expand)

    assert api._send_email_alert(
        "Recipient expansion",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("RECIPIENT-EXPANSION")],
        delivery_dedupe_keys=["recipient-expansion"],
    ) is True
    assert _AcceptedSMTP.recipient_batches == [("original@example.com",)]


def test_prepared_recipient_cohort_mutation_blocks_smtp_claim(
    monkeypatch, tmp_path
):
    """The pseudonymized intended cohort is immutable prepared evidence."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    db_path = _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    original_brand = api._brand_email_html

    def _brand_then_corrupt_cohort(*args, **kwargs):
        branded = original_brand(*args, **kwargs)
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE signals SET delivery_recipient_keys_json=?",
                ('["' + "f" * 64 + '"]',),
            )
            conn.commit()
        return branded

    monkeypatch.setattr(api, "_brand_email_html", _brand_then_corrupt_cohort)

    assert api._send_email_alert(
        "Recipient cohort mutation",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("COHORT-MUTATION")],
        delivery_dedupe_keys=["cohort-mutation"],
    ) is False
    assert _AcceptedSMTP.calls == 0


def test_reordered_same_ticker_retry_wires_each_persisted_ref_with_its_plan(
    monkeypatch, tmp_path
):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    row_a = _row("SAME", entry=100.0)
    row_b = _row("SAME", entry=120.0)
    recipient_keys = [api._recipient_delivery_key("operator@example.com")]
    intent_key = tracker.build_alert_delivery_intent_key(
        "stock_strategy", [row_a, row_b], channel="email",
        mail_channel="stocks_premarket", delivery_recipient_keys=recipient_keys,
    )
    first = tracker.prepare_alert_delivery_intent(
        "stock_strategy", [row_a, row_b], intent_key,
        channel="email", mail_channel="stocks_premarket",
        delivery_recipient_keys=recipient_keys,
    )
    assert first["send_allowed"] is True
    refs_by_entry = {signal["entry"]: signal["public_signal_ref"] for signal in first["signals"]}

    assert api._send_email_alert(
        "Reordered intent", "<p>x</p>", bypass_startup_cooldown=True,
        mail_class="swing_trade", trade_horizon="swing", mail_channel="stocks_premarket",
        tracking_scanner="stock_strategy", tracking_rows=[row_b, row_a],
    ) is True

    decoded = _decoded_wire_message(_AcceptedSMTP.messages[0])
    plan_b = (
        f"Signal-Ref: {refs_by_entry[120.0]} | Plan: SAME LONG | "
        "E=120 | SL=115 | TP1=125 | TP2=130"
    )
    plan_a = (
        f"Signal-Ref: {refs_by_entry[100.0]} | Plan: SAME LONG | "
        "E=100 | SL=95 | TP1=105 | TP2=110"
    )
    assert plan_b in decoded
    assert plan_a in decoded
    assert decoded.find(plan_b) < decoded.find(plan_a)


def test_legacy_prepared_intent_without_recipient_cohort_cannot_claim_or_send(
    monkeypatch, tmp_path
):
    """A stored cohortless PREPARED row is not valid SMTP ownership evidence."""
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _AcceptedSMTP.recipient_batches = []
    _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    row = _row("LEGACY-NO-COHORT")
    legacy_key = tracker.build_alert_delivery_intent_key(
        "stock_strategy", [row], channel="email", mail_channel="stocks_swing"
    )
    legacy = tracker.prepare_alert_delivery_intent(
        "stock_strategy", [row], legacy_key,
        channel="email", mail_channel="stocks_swing",
    )
    assert legacy["send_allowed"] is True

    claim = tracker.mark_alert_delivery_attempted(
        legacy_key,
        expected_prepared_rows=legacy["signals"],
    )
    assert claim["send_allowed"] is False
    assert claim["intent_state"] == "INCONSISTENT"
    assert _AcceptedSMTP.calls == 0

    assert api._send_email_alert(
        "Legacy cohortless retry",
        "<p>body</p>",
        bypass_startup_cooldown=True,
        mail_class="trade",
        trade_horizon="swing",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[row],
        delivery_dedupe_keys=["legacy-cohortless"],
    ) is False
    assert _AcceptedSMTP.calls == 0


@pytest.mark.parametrize(
    "persisted_signals",
    [
        [{"public_signal_ref": ""}],
        [
            {"public_signal_ref": "AS1-0123456789ABCDEF0123"},
            {"public_signal_ref": "AS1-0123456789ABCDEF0123"},
        ],
        [{"public_signal_ref": "not-a-public-ref"}],
    ],
)
def test_invalid_persisted_public_refs_fail_closed_before_smtp(
    monkeypatch, tmp_path, persisted_signals
):
    _AcceptedSMTP.calls = 0
    _AcceptedSMTP.messages = []
    _setup(monkeypatch, tmp_path, _AcceptedSMTP)
    rows = [_row(f"BAD-{index}") for index in range(len(persisted_signals))]
    monkeypatch.setattr(
        api,
        "prepare_alert_delivery_intent",
        lambda *_args, **_kwargs: {"send_allowed": True, "signals": persisted_signals},
    )

    assert api._send_email_alert(
        "Bad persisted ref", "<p>x</p>", bypass_startup_cooldown=True,
        mail_class="trade", tracking_scanner="stock_strategy", tracking_rows=rows,
    ) is False
    assert _AcceptedSMTP.calls == 0


class _UnknownSMTP(_AcceptedSMTP):
    calls = 0

    def sendmail(self, sender, recipients, message):
        type(self).calls += 1
        raise ConnectionResetError("unknown after DATA")


def test_unknown_data_tracking_intent_is_not_replayed(monkeypatch, tmp_path):
    _UnknownSMTP.calls = 0
    db_path = _setup(monkeypatch, tmp_path, _UnknownSMTP)
    quarantined = []

    class _Outbox:
        @staticmethod
        def record_tracker_acceptance_pending(*args, **kwargs):
            return "unused-contract-ready"

        @staticmethod
        def quarantine(*args, **kwargs):
            quarantined.append((args, kwargs))
            return 1

    monkeypatch.setattr(api, "_mail_outbox", _Outbox())
    kwargs = {
        "bypass_startup_cooldown": True,
        "mail_class": "trade",
        "mail_channel": "stocks_swing",
        "tracking_scanner": "stock_strategy",
        "tracking_rows": [_row("UNKNOWN")],
        "delivery_dedupe_keys": ["unknown-intent-key"],
    }

    assert api._send_email_alert("Unknown Intent", "<p>x</p>", **kwargs) is False
    assert api._send_email_alert("Unknown Intent", "<p>x</p>", **kwargs) is False
    assert _UnknownSMTP.calls == 1
    assert len(quarantined) == 1

    with sqlite3.connect(db_path) as conn:
        state = conn.execute(
            "SELECT status, delivery_state, delivery_accepted_at FROM signals"
        ).fetchone()
    assert state == (tracker.STATUS_PENDING_DELIVERY, "ATTEMPTED", None)


class _PartialAcceptedSMTP(_AcceptedSMTP):
    calls = []

    def sendmail(self, sender, recipients, message):
        type(self).calls.append(tuple(recipients))
        return {"later@example.com": (450, b"temporary refusal")}


def test_tracking_mail_does_not_merge_later_retry_into_accepted_cohort(
    monkeypatch, tmp_path
):
    _PartialAcceptedSMTP.calls = []
    db_path = _setup(monkeypatch, tmp_path, _PartialAcceptedSMTP)
    authorized = ["first@example.com", "later@example.com"]
    monkeypatch.setattr(
        api,
        "_resolve_email_alert_recipients",
        lambda recipient_emails=None, **_kwargs: list(
            recipient_emails if recipient_emails is not None else authorized
        ),
    )

    assert api._send_email_alert(
        "Partial Intent",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        recipient_emails=authorized,
        mail_class="trade",
        mail_channel="stocks_swing",
        tracking_scanner="stock_strategy",
        tracking_rows=[_row("PARTIAL")],
        delivery_dedupe_keys=["partial-intent-key"],
    ) is True

    assert _PartialAcceptedSMTP.calls == [
        ("first@example.com", "later@example.com")
    ]
    with sqlite3.connect(db_path) as conn:
        recipient_keys = conn.execute(
            "SELECT delivery_recipient_keys_json FROM signals"
        ).fetchone()[0]
    assert api._recipient_delivery_key("first@example.com") in recipient_keys
    assert api._recipient_delivery_key("later@example.com") not in recipient_keys
