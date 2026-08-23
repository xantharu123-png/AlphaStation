import hashlib
import os

import pytest


os.environ.setdefault("JWT_SECRET", "test-personal-position-followups-secret")

import bg_service
from modules import auth
from modules import mail_outbox


def _fake_followup_receipt_id(delivery_key):
    digest = hashlib.sha256(str(delivery_key).encode("utf-8")).hexdigest()[:43]
    return f"fr1_{digest}"


def _position(ticker, direction="LONG", scanner="stock_strategy", signal_id=None, **extra):
    position = {
        "ticker": ticker,
        "direction": direction,
        "scanner": scanner,
        "signal_id": signal_id,
    }
    position.update(extra)
    return position


def _event(ticker, direction="LONG", scanner="stock_strategy", signal_id=None, **extra):
    event = {
        "ticker": ticker,
        "direction": direction,
        "scanner": scanner,
        "id": signal_id,
    }
    event.update(extra)
    return event


def _delivery_cohort(*emails):
    return [bg_service._recipient_delivery_key(email) for email in emails]


def test_personal_position_normalization_is_safe_and_stable():
    normalized = auth._normalize_personal_position(
        {
            "ticker": " ab$c ",
            "direction": "long",
            "scanner": "Stock Strategy!",
            "company_name": "Example\x00 Corp",
            "setup_key": "stock:ABC:swing:2026-08-06",
            "strategy": "Momentum Breakout Long",
            "trade_horizon": "SWING",
            "entry": "100.25",
            "stop": 95.0,
            "tp1": 105.0,
            "tp2": 110.0,
            "instrument_id": "us-stock:ABC",
            "venue": "NYSE",
            "contract_symbol": "abc",
        }
    )

    assert normalized["ticker"] == "ABC"
    assert normalized["direction"] == "LONG"
    assert normalized["scanner"] == "stockstrategy"
    assert normalized["company_name"] == "Example Corp"
    assert normalized["setup_key"] == "stock:ABC:swing:2026-08-06"
    assert normalized["strategy"] == "Momentum Breakout Long"
    assert normalized["trade_horizon"] == "swing"
    assert normalized["entry"] == 100.25
    assert normalized["stop"] == 95.0
    assert normalized["tp1"] == 105.0
    assert normalized["tp2"] == 110.0
    assert normalized["instrument_id"] == "us-stock:ABC"
    assert normalized["venue"] == "nyse"
    assert normalized["contract_symbol"] == "ABC"
    assert normalized["id"].startswith("setup-")
    assert auth._normalize_personal_position({"ticker": "ABC", "direction": "SIDEWAYS"}) is None


def test_recipient_profiles_merge_accounts_without_losing_all_scope(monkeypatch):
    users = {
        "one@example.com": {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": "shared@example.com",
            "position_update_scope": "mine",
            "personal_positions": [_position("ABC", signal_id=11)],
        },
        "two@example.com": {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": "shared@example.com",
            "position_update_scope": "mine",
            "personal_positions": [_position("XYZ", direction="SHORT", signal_id=22)],
        },
    }
    monkeypatch.setattr(auth, "_load_effective_users_atomic", lambda: users)
    monkeypatch.setattr(auth, "get_plan_features", lambda _plan: {"has_email_alerts": True})

    profiles = auth.get_followup_alert_recipient_profiles()
    assert len(profiles) == 1
    assert profiles[0]["position_update_scope"] == "mine"
    assert {item["ticker"] for item in profiles[0]["personal_positions"]} == {"ABC", "XYZ"}

    users["three@example.com"] = {
        "plan": "elite",
        "email_alerts_enabled": True,
        "alert_email": "shared@example.com",
        "position_update_scope": "all",
        "personal_positions": [],
    }
    profiles = auth.get_followup_alert_recipient_profiles()
    assert profiles[0]["position_update_scope"] == "all"


def test_followup_event_matching_prefers_signal_id_then_falls_back_to_identity():
    assert bg_service._followup_event_matches_position(
        _event("ABC", signal_id=17), _position("OTHER", signal_id=17)
    )
    assert not bg_service._followup_event_matches_position(
        _event("ABC", signal_id=17), _position("ABC", signal_id=18)
    )
    assert bg_service._followup_event_matches_position(
        _event("ABC", "SHORT", "bear"), _position("ABC", "SHORT", "bear")
    )
    assert not bg_service._followup_event_matches_position(
        _event("ABC", "LONG", "stock_strategy"),
        _position("ABC", "SHORT", "stock_strategy"),
    )


def test_followup_event_matching_uses_setup_identity_before_geometry_fallback():
    position = _position(
        "ABC",
        setup_key="stock:ABC:swing:one",
        strategy="Momentum Breakout Long",
        trade_horizon="swing",
        entry=100.0,
        stop=95.0,
        tp1=105.0,
        tp2=110.0,
    )
    assert bg_service._followup_event_matches_position(
        _event(
            "ABC",
            setup_key="stock:ABC:swing:one",
            strategy="Momentum Breakout Long",
            trade_horizon="swing",
            entry=100.04,
            stop=95.04,
            tp1=105.05,
            tp2=110.05,
        ),
        position,
    )
    assert not bg_service._followup_event_matches_position(
        _event(
            "ABC",
            setup_key="stock:ABC:swing:two",
            strategy="Momentum Breakout Long",
            trade_horizon="swing",
            entry=120.0,
            stop=115.0,
            tp1=125.0,
            tp2=130.0,
        ),
        position,
    )


def test_followup_event_matching_accepts_legacy_geometry_only_when_equivalent():
    position = _position("ABC", entry=100.0, stop=95.0, tp1=105.0, tp2=110.0)
    assert bg_service._followup_event_matches_position(
        _event("ABC", entry=100.04, stop=95.04, tp1=105.05, tp2=110.05),
        position,
    )
    assert not bg_service._followup_event_matches_position(
        _event("ABC", entry=103.0, stop=98.0, tp1=108.0, tp2=113.0),
        position,
    )


def _install_dispatch_fakes(monkeypatch, profiles, send_result):
    active_keys = set()
    deliveries = []

    monkeypatch.setattr(bg_service, "_followup_recipient_profiles", lambda _secrets: profiles)
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {profile["email"] for profile in profiles},
    )
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_active",
        lambda key, _ttl, now=None: key in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_claim",
        lambda key, _ttl, now=None: key not in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_release",
        lambda key, claimed_at=None: True,
    )
    monkeypatch.setattr(
        bg_service,
        "_followup_recipient_delivery_uncertain",
        lambda recipient_key, now=None: (
            bg_service._followup_recipient_uncertain_key(recipient_key)
            in active_keys
        ),
    )

    def fake_send(subject, body_html, _secrets, **kwargs):
        recipients = tuple(kwargs.get("recipient_emails") or [])
        deliveries.append((recipients, subject, body_html))
        return send_result(recipients)

    monkeypatch.setattr(bg_service, "_send_email_alert", fake_send)
    return active_keys, deliveries


def _dispatch(pending):
    return bg_service._dispatch_followup_digest(
        pending,
        {},
        lambda rows: (
            "Update",
            ",".join(item[0]["ticker"] for item in rows),
        ),
        lambda item: item[0],
        lambda item: item[1],
    )


def test_personal_followup_digest_filters_rows_per_recipient(monkeypatch):
    profiles = [
        {
            "email": "all@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
        },
        {
            "email": "mine@example.com",
            "position_update_scope": "mine",
            "personal_positions": [_position("ABC", signal_id=11)],
        },
    ]
    active_keys, deliveries = _install_dispatch_fakes(
        monkeypatch, profiles, lambda _recipients: True
    )
    cohort = _delivery_cohort("all@example.com", "mine@example.com")
    pending = [
        (_event("ABC", signal_id=11, delivery_recipient_keys=cohort), "signal-11"),
        (_event("XYZ", signal_id=22, delivery_recipient_keys=cohort), "signal-22"),
    ]

    sent, complete = _dispatch(pending)

    assert sent is True
    assert complete is True
    assert [(recipient, body) for recipient, _subject, body in deliveries] == [
        (("all@example.com",), "ABC,XYZ"),
        (("mine@example.com",), "ABC"),
    ]
    assert {"signal-11", "signal-22"}.issubset(active_keys)


def test_personal_same_ticker_followup_renders_only_the_matched_public_ref(monkeypatch):
    """A personal digest cannot blur two simultaneous plans for one ticker."""
    profiles = [{
        "email": "mine@example.com",
        "position_update_scope": "mine",
        "personal_positions": [_position("SAME", signal_id=11)],
    }]
    _active_keys, deliveries = _install_dispatch_fakes(
        monkeypatch, profiles, lambda _recipients: True
    )
    cohort = _delivery_cohort("mine@example.com")
    base = {
        "ticker": "SAME",
        "direction": "LONG",
        "scanner": "stock_strategy",
        "old_status": "OPEN",
        "new_status": "TP1_HIT_OPEN",
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "r_realized": None,
        "mfe": 1.2,
        "mail_class": "trade",
        "channel": "email",
        "trade_horizon": "swing",
        "delivery_recipient_keys": cohort,
        "origin_evidence": "smtp_acceptance",
        "delivery_accepted_at": "2026-08-21T10:00:00+00:00",
    }
    pending = [
        (
            "signal_update_11_TP1_HIT_OPEN",
            "TP1 erreicht, Position offen",
            {**base, "id": 11, "public_signal_ref": "AS1-0123456789ABCDEF0123"},
        ),
        (
            "signal_update_12_TP1_HIT_OPEN",
            "TP1 erreicht, Position offen",
            {**base, "id": 12, "public_signal_ref": "AS1-FEDCBA9876543210ABCD"},
        ),
    ]

    sent, complete = bg_service._dispatch_followup_digest(
        pending,
        {},
        bg_service._build_signal_update_digest,
        lambda item: item[2],
        lambda item: item[0],
    )

    assert sent is True
    assert complete is True
    assert len(deliveries) == 1
    body = deliveries[0][2]
    assert "AS1-0123456789ABCDEF0123" in body
    assert "AS1-FEDCBA9876543210ABCD" not in body
    assert "historisch nicht belegt" not in body


def test_followup_retry_only_resends_failed_recipient(monkeypatch):
    profiles = [
        {
            "email": "all@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
        },
        {
            "email": "mine@example.com",
            "position_update_scope": "mine",
            "personal_positions": [_position("ABC", signal_id=11)],
        },
    ]
    mine_fails = {"value": True}
    active_keys, deliveries = _install_dispatch_fakes(
        monkeypatch,
        profiles,
        lambda recipients: not (recipients == ("mine@example.com",) and mine_fails["value"]),
    )
    pending = [
        (
            _event(
                "ABC",
                signal_id=11,
                delivery_recipient_keys=_delivery_cohort(
                    "all@example.com", "mine@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    sent, complete = _dispatch(pending)
    assert sent is True
    assert complete is False
    assert "signal-11" not in active_keys

    mine_fails["value"] = False
    sent, complete = _dispatch(pending)
    assert sent is True
    assert complete is True
    assert "signal-11" in active_keys
    assert [recipient for recipient, _subject, _body in deliveries] == [
        ("all@example.com",),
        ("mine@example.com",),
        ("mine@example.com",),
    ]


def test_followup_digest_intersects_original_cohort_and_current_optin(monkeypatch):
    profiles = [
        {
            "email": email,
            "position_update_scope": "all",
            "personal_positions": [],
        }
        for email in (
            "original-optin@example.com",
            "original-optout@example.com",
            "later-optin@example.com",
        )
    ]
    _active_keys, deliveries = _install_dispatch_fakes(
        monkeypatch, profiles, lambda _recipients: True
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {
            "original-optin@example.com",
            "later-optin@example.com",
        },
    )
    pending = [
        (
            _event(
                "ABC",
                signal_id=11,
                delivery_recipient_keys=_delivery_cohort(
                    "original-optin@example.com",
                    "original-optout@example.com",
                ),
            ),
            "signal-11",
        )
    ]

    sent, complete = _dispatch(pending)

    assert sent is True
    assert complete is True
    assert [recipient for recipient, _subject, _body in deliveries] == [
        ("original-optin@example.com",),
    ]


def test_operator_followups_require_explicit_optin(monkeypatch):
    monkeypatch.delenv("ALERT_OPERATOR_FOLLOWUP_OPTIN", raising=False)
    monkeypatch.setattr(bg_service, "HAS_AUTH_ALERT_RECIPIENTS", True)
    monkeypatch.setattr(
        bg_service, "get_followup_alert_recipient_profiles", lambda: []
    )

    implicit = bg_service._followup_recipient_profiles(
        {"ALERT_EMAIL": "operator@example.com"}
    )
    explicit = bg_service._followup_recipient_profiles(
        {
            "ALERT_EMAIL": "operator@example.com",
            "ALERT_OPERATOR_FOLLOWUP_OPTIN": "1",
        }
    )

    assert implicit == []
    assert explicit == [
        {
            "email": "operator@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
            "operator_followup_optin": True,
        }
    ]


@pytest.mark.parametrize(
    "user_overrides",
    [
        {"email_alerts_enabled": False},
        {"mail_channels": {"stocks_swing": False}},
        {"trade_alert_horizon": "intraday"},
    ],
    ids=("global-optout", "channel-optout", "horizon-optout"),
)
def test_stocks_swing_current_optout_blocks_followup(
    monkeypatch, user_overrides
):
    user = {
        "plan": "elite",
        "email_alerts_enabled": True,
        "alert_email": "subscriber@example.com",
        "position_update_scope": "all",
        "personal_positions": [],
        "trade_alert_horizon": "swing",
        "mail_channels": {"stocks_swing": True},
    }
    user.update(user_overrides)
    monkeypatch.delenv("ALERT_OPERATOR_FOLLOWUP_OPTIN", raising=False)
    monkeypatch.setattr(auth, "_load_effective_users_atomic", lambda: {"subscriber@example.com": user})
    monkeypatch.setattr(auth, "get_plan_features", lambda _plan: {"has_email_alerts": True})
    monkeypatch.setattr(bg_service, "HAS_AUTH_ALERT_RECIPIENTS", True)
    monkeypatch.setattr(
        bg_service,
        "get_followup_alert_recipient_profiles",
        auth.get_followup_alert_recipient_profiles,
    )
    monkeypatch.setattr(
        bg_service, "get_email_alert_recipients", auth.get_email_alert_recipients
    )
    monkeypatch.setattr(bg_service, "scanner_mail_channel", auth.scanner_mail_channel)
    monkeypatch.setattr(
        bg_service, "_email_dedupe_active", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        bg_service, "_email_dedupe_mark", lambda *_args, **_kwargs: None
    )
    deliveries = []
    monkeypatch.setattr(
        bg_service,
        "_send_email_alert",
        lambda *args, **kwargs: deliveries.append(kwargs) or True,
    )
    pending = [
        (
            _event(
                "ABC",
                scanner="stock_strategy",
                signal_id=11,
                trade_horizon="swing",
                delivery_recipient_keys=_delivery_cohort(
                    "subscriber@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    sent, complete = _dispatch(pending)

    assert sent is False
    assert complete is True
    assert deliveries == []


def test_current_optin_resolution_failure_never_acknowledges_event(monkeypatch):
    profiles = [
        {
            "email": "subscriber@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
        }
    ]
    active_keys, deliveries = _install_dispatch_fakes(
        monkeypatch, profiles, lambda _recipients: True
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: None,
    )
    pending = [
        (
            _event(
                "ABC",
                scanner="stock_strategy",
                signal_id=11,
                trade_horizon="swing",
                delivery_recipient_keys=_delivery_cohort(
                    "subscriber@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    sent, complete = _dispatch(pending)

    assert sent is False
    assert complete is False
    assert deliveries == []
    assert "signal-11" not in active_keys


def test_unknown_data_followup_is_quarantined_and_not_retried(monkeypatch):
    profiles = [
        {
            "email": "subscriber@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
        }
    ]
    active_keys, _deliveries = _install_dispatch_fakes(
        monkeypatch, profiles, lambda _recipients: False
    )
    attempts = []

    def unknown_send(_subject, _body, _secrets, **kwargs):
        email = kwargs["recipient_emails"][0]
        attempts.append(email)
        bg_service._set_last_email_delivery(
            intended=[email],
            pending=[email],
            outcome_unknown=True,
        )
        return False

    monkeypatch.setattr(bg_service, "_send_email_alert", unknown_send)
    pending = [
        (
            _event(
                "ABC",
                scanner="stock_strategy",
                signal_id=11,
                trade_horizon="swing",
                delivery_recipient_keys=_delivery_cohort(
                    "subscriber@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    assert _dispatch(pending) == (False, False)
    assert _dispatch(pending) == (False, False)
    recipient_key = bg_service._followup_recipient_dedupe_key(
        "signal-11", "subscriber@example.com"
    )
    uncertain_key = bg_service._followup_recipient_uncertain_key(recipient_key)
    assert attempts == ["subscriber@example.com"]
    assert uncertain_key in active_keys
    assert "signal-11" not in active_keys


def test_durable_unknown_followup_quarantine_does_not_expire_after_7_days(
    monkeypatch, tmp_path
):
    db_path = str(tmp_path / "outbox.sqlite")
    monkeypatch.setenv("MAIL_OUTBOX_ENABLED", "1")
    monkeypatch.setattr(mail_outbox, "MAIL_OUTBOX_DB_PATH", db_path)
    recipient_key = bg_service._followup_recipient_dedupe_key(
        "signal-11", "subscriber@example.com"
    )
    assert mail_outbox.quarantine(
        "Update",
        "body",
        ["subscriber@example.com"],
        mail_class="signal_update",
        delivery_dedupe_keys=[recipient_key],
        now=1_000,
    ) is not None
    monkeypatch.setattr(bg_service.time, "time", lambda: 1_000 + 8 * 86400)
    profiles = [
        {
            "email": "subscriber@example.com",
            "position_update_scope": "all",
            "personal_positions": [],
        }
    ]
    monkeypatch.setattr(
        bg_service, "_followup_recipient_profiles", lambda _secrets: profiles
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda _event, _cache: {"subscriber@example.com"},
    )
    monkeypatch.setattr(
        bg_service, "_email_dedupe_active", lambda *_args, **_kwargs: False
    )
    sent = []
    monkeypatch.setattr(
        bg_service,
        "_send_email_alert",
        lambda *args, **kwargs: sent.append(kwargs) or True,
    )
    pending = [
        (
            _event(
                "ABC",
                scanner="stock_strategy",
                signal_id=11,
                trade_horizon="swing",
                delivery_recipient_keys=_delivery_cohort(
                    "subscriber@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    assert _dispatch(pending) == (False, False)
    assert sent == []


def test_global_subscriber_optout_completes_without_endless_pending(monkeypatch):
    monkeypatch.setattr(
        bg_service, "_followup_recipient_profiles", lambda _secrets: []
    )
    active_keys = set()
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_active",
        lambda key, _ttl, now=None: key in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_claim",
        lambda key, _ttl, now=None: key not in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_release",
        lambda key, claimed_at=None: True,
    )
    monkeypatch.setattr(
        bg_service,
        "_current_followup_recipient_emails",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("global opt-out must not query subscriber auth")
        ),
    )
    pending = [
        (
            _event(
                "ABC",
                scanner="stock_strategy",
                signal_id=11,
                trade_horizon="swing",
                delivery_recipient_keys=_delivery_cohort(
                    "subscriber@example.com"
                ),
            ),
            "signal-11",
        )
    ]

    sent, complete = bg_service._dispatch_followup_digest(
        pending,
        {"ALERT_SEND_TO_SUBSCRIBERS": "0"},
        lambda rows: ("Update", "body"),
        lambda item: item[0],
        lambda item: item[1],
    )

    assert sent is False
    assert complete is True
    assert "signal-11" in active_keys


def _be_activation(signal_id, email, **overrides):
    activation = _event(
        f"BE{signal_id}",
        scanner="stock_strategy",
        signal_id=signal_id,
        entry=100.0,
        entry_fill_price=100.0,
        stop=95.0,
        tp1=105.0,
        tp2=110.0,
        mfe=1.2,
        mail_class="trade",
        channel="email",
        mail_channel="stocks_swing",
        trade_horizon="swing",
        tracker_persisted=True,
        delivery_recipient_keys=_delivery_cohort(email),
    )
    activation.update(overrides)
    return activation


def _install_be_delivery_fakes(monkeypatch, accepted_emails=()):
    active_keys = set()
    accepted_emails = {
        str(email).strip().lower() for email in accepted_emails
    }
    smtp_calls = []
    ack_calls = []

    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_active",
        lambda key, _ttl, now=None: key in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_claim",
        lambda key, _ttl, now=None: key not in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_mark",
        lambda key, now=None: active_keys.add(key),
    )
    monkeypatch.setattr(
        bg_service,
        "_email_delivery_release",
        lambda key, claimed_at=None: True,
    )
    monkeypatch.setattr(
        bg_service,
        "_followup_recipient_delivery_uncertain",
        lambda _recipient_key, now=None: False,
    )

    def fake_send(_subject, _body_html, _secrets, **kwargs):
        intended = tuple(kwargs.get("recipient_emails") or ())
        accepted = tuple(
            email for email in intended if email in accepted_emails
        )
        pending = tuple(
            email for email in intended if email not in accepted_emails
        )
        smtp_calls.append(intended)
        bg_service._set_last_email_delivery(
            intended=intended,
            accepted=accepted,
            pending=pending,
        )
        return bool(accepted)

    def record_receipt(signal_id, *, delivery_key, **_kwargs):
        if delivery_key not in active_keys:
            return None
        return _fake_followup_receipt_id(delivery_key)

    def record_ack(signal_ids, *, delivery_receipt_ids=None):
        ids = list(signal_ids)
        ack_calls.append((ids, dict(delivery_receipt_ids or {})))
        return len(ids)

    monkeypatch.setattr(bg_service, "_send_email_alert", fake_send)
    monkeypatch.setattr(bg_service, "_record_followup_event_receipt", record_receipt)
    monkeypatch.setattr(bg_service, "mark_be_alerts_sent", record_ack)
    return active_keys, smtp_calls, ack_calls


def _install_auth_followup_routing(monkeypatch, users):
    monkeypatch.delenv("ALERT_OPERATOR_FOLLOWUP_OPTIN", raising=False)
    monkeypatch.setattr(auth, "_load_effective_users_atomic", lambda: users)
    monkeypatch.setattr(
        auth, "get_plan_features", lambda _plan: {"has_email_alerts": True}
    )
    monkeypatch.setattr(bg_service, "HAS_AUTH_ALERT_RECIPIENTS", True)
    monkeypatch.setattr(
        bg_service,
        "get_followup_alert_recipient_profiles",
        auth.get_followup_alert_recipient_profiles,
    )
    monkeypatch.setattr(
        bg_service,
        "get_email_alert_recipients",
        auth.get_email_alert_recipients,
    )
    monkeypatch.setattr(
        bg_service, "scanner_mail_channel", auth.scanner_mail_channel
    )


@pytest.mark.parametrize(
    "user_overrides",
    [
        {"email_alerts_enabled": False},
        {"mail_channels": {"stocks_swing": False}},
        {"trade_alert_horizon": "intraday"},
    ],
    ids=("global-optout", "channel-optout", "horizon-optout"),
)
def test_be_optout_completes_workflow_without_delivery_ack(
    monkeypatch, user_overrides
):
    email = "subscriber@example.com"
    user = {
        "plan": "elite",
        "email_alerts_enabled": True,
        "alert_email": email,
        "position_update_scope": "all",
        "personal_positions": [],
        "trade_alert_horizon": "swing",
        "mail_channels": {"stocks_swing": True},
    }
    user.update(user_overrides)
    _install_auth_followup_routing(monkeypatch, {email: user})
    active_keys, smtp_calls, ack_calls = _install_be_delivery_fakes(
        monkeypatch, accepted_emails={email}
    )

    sent = bg_service._send_be_alert_mail(
        [_be_activation(101, email)], {}
    )

    assert sent is False
    assert smtp_calls == []
    assert "signal_be_101" in active_keys
    assert ack_calls == []

    assert bg_service._send_be_alert_mail(
        [_be_activation(101, email)], {}
    ) is False
    assert smtp_calls == []
    assert ack_calls == []


def test_be_mixed_batch_acks_only_smtp_accepted_event(monkeypatch):
    delivered = "delivered@example.com"
    opted_out = "optedout@example.com"
    users = {
        delivered: {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": delivered,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
        opted_out: {
            "plan": "elite",
            "email_alerts_enabled": False,
            "alert_email": opted_out,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
    }
    _install_auth_followup_routing(monkeypatch, users)
    _active_keys, smtp_calls, ack_calls = _install_be_delivery_fakes(
        monkeypatch, accepted_emails={delivered}
    )

    assert bg_service._send_be_alert_mail(
        [
            _be_activation(201, delivered),
            _be_activation(202, opted_out),
        ],
        {},
    ) is True

    assert smtp_calls == [(delivered,)]
    assert ack_calls == [
        ([201], {201: _fake_followup_receipt_id("signal_be_201_recipient_delivered")})
    ]


def test_be_mixed_batch_acks_only_event_with_durable_recipient_delivery(
    monkeypatch,
):
    delivered = "delivered@example.com"
    opted_out = "optedout@example.com"
    users = {
        delivered: {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": delivered,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
        opted_out: {
            "plan": "elite",
            "email_alerts_enabled": False,
            "alert_email": opted_out,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
    }
    _install_auth_followup_routing(monkeypatch, users)
    active_keys, smtp_calls, ack_calls = _install_be_delivery_fakes(monkeypatch)
    active_keys.add(
        bg_service._followup_recipient_dedupe_key(
            "signal_be_301", delivered
        )
    )

    assert bg_service._send_be_alert_mail(
        [
            _be_activation(301, delivered),
            _be_activation(302, opted_out),
        ],
        {},
    ) is False

    assert smtp_calls == []
    assert ack_calls == [
        ([301], {301: _fake_followup_receipt_id("signal_be_301_recipient_delivered")})
    ]


def _terminal_transition(signal_id, email, **overrides):
    transition = _event(
        f"TERM{signal_id}",
        scanner="stock_strategy",
        signal_id=signal_id,
        old_status="OPEN",
        new_status="STOP_HIT",
        entry=100.0,
        entry_fill_price=100.0,
        stop=95.0,
        tp1=105.0,
        tp2=110.0,
        r_realized=-1.0,
        mfe=0.4,
        mail_class="trade",
        channel="email",
        mail_channel="stocks_swing",
        trade_horizon="swing",
        tracker_persisted=True,
        delivery_recipient_keys=_delivery_cohort(email),
    )
    transition.update(overrides)
    return transition


def _install_terminal_delivery_fakes(monkeypatch, accepted_emails=()):
    active_keys, smtp_calls, _be_ack_calls = _install_be_delivery_fakes(
        monkeypatch, accepted_emails=accepted_emails
    )
    ack_calls = []

    def record_ack(signal_ids, *, delivery_receipt_ids=None):
        ids = list(signal_ids)
        ack_calls.append(ids)
        return len(ids)

    monkeypatch.setattr(
        bg_service, "mark_terminal_updates_sent", record_ack
    )
    return active_keys, smtp_calls, ack_calls


@pytest.mark.parametrize(
    "user_overrides",
    [
        {"email_alerts_enabled": False},
        {"mail_channels": {"stocks_swing": False}},
        {"trade_alert_horizon": "intraday"},
    ],
    ids=("global-optout", "channel-optout", "horizon-optout"),
)
def test_terminal_optout_completes_workflow_without_delivery_ack(
    monkeypatch, user_overrides
):
    email = "subscriber@example.com"
    user = {
        "plan": "elite",
        "email_alerts_enabled": True,
        "alert_email": email,
        "position_update_scope": "all",
        "personal_positions": [],
        "trade_alert_horizon": "swing",
        "mail_channels": {"stocks_swing": True},
    }
    user.update(user_overrides)
    _install_auth_followup_routing(monkeypatch, {email: user})
    active_keys, smtp_calls, ack_calls = _install_terminal_delivery_fakes(
        monkeypatch, accepted_emails={email}
    )

    assert bg_service._send_signal_update_mail(
        [_terminal_transition(401, email)], {}
    ) is False
    assert smtp_calls == []
    assert "signal_update_401_STOP_HIT" in active_keys
    assert ack_calls == []

    assert bg_service._send_signal_update_mail(
        [_terminal_transition(401, email)], {}
    ) is False
    assert smtp_calls == []
    assert ack_calls == []


def test_terminal_mixed_batch_acks_only_smtp_accepted_event(monkeypatch):
    delivered = "delivered@example.com"
    opted_out = "optedout@example.com"
    users = {
        delivered: {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": delivered,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
        opted_out: {
            "plan": "elite",
            "email_alerts_enabled": False,
            "alert_email": opted_out,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
    }
    _install_auth_followup_routing(monkeypatch, users)
    _active_keys, smtp_calls, ack_calls = _install_terminal_delivery_fakes(
        monkeypatch, accepted_emails={delivered}
    )

    assert bg_service._send_signal_update_mail(
        [
            _terminal_transition(501, delivered),
            _terminal_transition(502, opted_out),
        ],
        {},
    ) is True

    assert smtp_calls == [(delivered,)]
    assert ack_calls == [[501]]


def test_terminal_mixed_batch_acks_only_event_with_durable_recipient_delivery(
    monkeypatch,
):
    delivered = "delivered@example.com"
    opted_out = "optedout@example.com"
    users = {
        delivered: {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": delivered,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
        opted_out: {
            "plan": "elite",
            "email_alerts_enabled": False,
            "alert_email": opted_out,
            "position_update_scope": "all",
            "personal_positions": [],
            "trade_alert_horizon": "swing",
            "mail_channels": {"stocks_swing": True},
        },
    }
    _install_auth_followup_routing(monkeypatch, users)
    active_keys, smtp_calls, ack_calls = _install_terminal_delivery_fakes(
        monkeypatch
    )
    active_keys.add(
        bg_service._followup_recipient_dedupe_key(
            "signal_update_601_STOP_HIT", delivered
        )
    )

    assert bg_service._send_signal_update_mail(
        [
            _terminal_transition(601, delivered),
            _terminal_transition(602, opted_out),
        ],
        {},
    ) is False

    assert smtp_calls == []
    assert ack_calls == [[601]]
