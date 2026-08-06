import os


os.environ.setdefault("JWT_SECRET", "test-personal-position-followups-secret")

import bg_service
from modules import auth


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
        "_email_dedupe_active",
        lambda key, _ttl, now=None: key in active_keys,
    )
    monkeypatch.setattr(
        bg_service,
        "_email_dedupe_mark",
        lambda key, now=None: active_keys.add(key),
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
    pending = [(_event("ABC", signal_id=11), "signal-11"), (_event("XYZ", signal_id=22), "signal-22")]

    sent, complete = _dispatch(pending)

    assert sent is True
    assert complete is True
    assert [(recipient, body) for recipient, _subject, body in deliveries] == [
        (("all@example.com",), "ABC,XYZ"),
        (("mine@example.com",), "ABC"),
    ]
    assert {"signal-11", "signal-22"}.issubset(active_keys)


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
    pending = [(_event("ABC", signal_id=11), "signal-11")]

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
