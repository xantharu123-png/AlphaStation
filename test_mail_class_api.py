"""Mail-Audit 10.06. — Tests fuer Q1/Q2/H1/H2/H3/M1.

Abgedeckt:
- Q1: WAIT-Decisions (WAIT_FOR_RETEST / WATCH_ONLY) unterdruecken die
  JETZT-Trade-Mail auch fuer Crypto-Swing und landen stattdessen in der
  separaten Watch-Mail; LONG_TRIGGER + frisch bleibt JETZT-mailbar.
- Q2: 15-Min-Frische-Gate (trigger_stale_for_mail) ueber Trigger-Zeitstempel
  und Scan-Alter-Proxy; Chase-Schutz greift fuer Crypto ohne Soft-Bypass.
- H1: entry_score cappt den Mail-Score ehrlich (auch 0), 80 nur als
  Fallback ohne entry_score.
- H2: Betreff-Praefix pro Mail-Klasse ohne Emoji-Stapelung.
- H3: watch-Mails nur an Abonnenten mit watch_mail_optin; trade unveraendert;
  Test-Mail nur an Admin/Betreiber; Betreiber bekommt alle Klassen.
- M1: synthetische Biotech-Level werden im Mail-HTML gekennzeichnet.

Mock-/Fixture-Muster folgt test_email_alert_audit.py.
"""

import time

import pytest

import api
import modules.auth as auth


@pytest.fixture(autouse=True)
def _isolate_email_state(monkeypatch, tmp_path):
    """Gleiches Isolations-Muster wie test_email_alert_audit.py."""
    api._EMAIL_COOLDOWN.clear()
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(
        api,
        "_has_open_equivalent_trade_safe",
        lambda *args, **kwargs: False,
    )
    monkeypatch.setattr(
        api,
        "_revalidate_early_mover_mail_candidate",
        lambda candidate, now_ts=None: {"ok": True, "candidate": candidate},
    )


def _early_mover_row(**overrides):
    """Sauberes, im Versandmoment handelbares Crypto-Swing-Setup
    (Spiegel der Fixture aus test_email_alert_audit.py inkl. AUDIT-H-1
    entry_score)."""
    row = {
        "Symbol": "EMO",
        "Name": "Early Mover",
        "grade": "A",
        "score": 86,
        "entry_score": 85,
        "Price": 1.25,
        "Change24h": 4.2,
        "VolMCapRatio": 8.5,
        "direction": "LONG",
        "trade_action": "LONG_TRIGGER",
        "entry_status": "CONDITIONAL_LONG",
        "entry_quality": "GOOD",
        "execution_trigger_ok": True,
        "signal_quality": "conditional_long_setup",
        "entry": 1.25,
        "stop_loss": 1.15,
        "tp1": 1.43,
        "tp2": 1.57,
        "live_rr_ratio": 2.4,
        "distance_to_entry_r": 0,
        "late_to_tp1": False,
        "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        "risk_flags": [],
        "trade_setup": {
            "trade_action": "LONG_TRIGGER",
            "entry": 1.25,
            "stop_loss": 1.15,
            "tp1": 1.43,
            "tp2": 1.57,
            "live_rr": 2.4,
            "distance_to_entry_r": 0,
            "btc_context": {"btc_24h": 1.2, "alpha_24h": 3.0, "tailwind": True},
        },
    }
    row.update(overrides)
    return row


def _capture_send(monkeypatch):
    sent = []
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: sent.append((subject, body, kwargs)) or True,
    )
    return sent


def _tradeable_health(**overrides):
    health = {
        "decision": "TRADEABLE",
        "health_score": 95,
        "chase_risk": "LOW",
        "fakeout_risk": "LOW",
        "liquidity_risk": "LOW",
        "entry_quality_score": 90,
        "fakeout_risk_score": 95,
        "liquidity_score": 95,
    }
    health.update(overrides)
    return health


# ── Q1: WAIT-Decisions raus aus der JETZT-Mail, rein in die Watch-Mail ──


def test_q1_wait_for_retest_row_not_in_trade_mail_but_in_watch_mail(monkeypatch):
    sent = _capture_send(monkeypatch)
    payload = {"coins": [
        _early_mover_row(
            Symbol="RETESTZONE",
            trade_action="WAIT_FOR_RETEST",
            entry_status="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)

    assert len(sent) == 1
    subject, body, kwargs = sent[0]
    assert kwargs.get("mail_class") == "watch"
    assert "Retest-Zonen" in subject
    assert "RETESTZONE" in body
    assert "BEOBACHTUNG" in body
    assert "kein Einstiegssignal" in body
    assert "Auf Retest warten" in body


def test_q1_watch_only_health_row_goes_to_watch_mail(monkeypatch):
    sent = _capture_send(monkeypatch)
    monkeypatch.setattr(
        api,
        "calculate_trade_health",
        lambda row, scanner_name="scanner", market_context=None: _tradeable_health(
            decision="WATCH_ONLY", health_score=60
        ),
    )

    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="WATCHME")]})

    assert len(sent) == 1
    subject, body, kwargs = sent[0]
    assert kwargs.get("mail_class") == "watch"
    assert "WATCHME" in body
    assert "Nur beobachten" in body


def test_q1_fresh_long_trigger_row_goes_to_trade_mail(monkeypatch):
    sent = _capture_send(monkeypatch)

    api._send_early_mover_long_alerts({"coins": [_early_mover_row(Symbol="TRADENOW")]})

    trade_mails = [item for item in sent if item[2].get("mail_class") == "trade"]
    watch_mails = [item for item in sent if item[2].get("mail_class") == "watch"]
    assert len(trade_mails) == 1
    assert "TRADENOW" in trade_mails[0][1]
    assert "Crypto Early Mover LONG Digest" in trade_mails[0][0]
    assert watch_mails == []


def test_q1_trade_and_watch_rows_split_into_separate_mails(monkeypatch):
    sent = _capture_send(monkeypatch)
    payload = {"coins": [
        _early_mover_row(Symbol="TRADENOW"),
        _early_mover_row(
            Symbol="RETESTZONE",
            trade_action="WAIT_FOR_RETEST",
            entry_status="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)

    assert len(sent) == 2
    trade = next(item for item in sent if item[2].get("mail_class") == "trade")
    watch = next(item for item in sent if item[2].get("mail_class") == "watch")
    assert "TRADENOW" in trade[1]
    assert "RETESTZONE" not in trade[1]
    assert "RETESTZONE" in watch[1]
    assert "TRADENOW" not in watch[1]


def test_q1_quality_fail_rows_do_not_reach_watch_mail(monkeypatch):
    """Watch-Mail ist kein Auffangbecken: Grade/Score/RVOL/Plan muessen passen."""
    sent = _capture_send(monkeypatch)
    payload = {"coins": [
        # Score/Entry-Qualitaet zu schwach
        _early_mover_row(
            Symbol="LOWSCORE",
            trade_action="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
            entry_score=40,
            score=55,
            grade="B",
        ),
        # Live-R:R unter Schwelle
        _early_mover_row(
            Symbol="BADRR",
            trade_action="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
            live_rr_ratio=0.8,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)

    assert sent == []


def test_q1_watch_mail_has_own_cooldown_key(monkeypatch):
    sent = _capture_send(monkeypatch)
    payload = {"coins": [
        _early_mover_row(
            Symbol="RETESTZONE",
            trade_action="WAIT_FOR_RETEST",
            entry_status="WAIT_FOR_RETEST",
            execution_trigger_ok=False,
        ),
    ]}

    api._send_early_mover_long_alerts(payload)
    api._send_early_mover_long_alerts(payload)

    watch_mails = [item for item in sent if item[2].get("mail_class") == "watch"]
    assert len(watch_mails) == 1
    status = api._email_dedupe_status(now=time.time())
    digest = [item for item in status["recent"] if item["key"] == api._EARLY_MOVER_WATCH_DIGEST_KEY]
    assert digest
    assert 0 < digest[0]["remaining_seconds"] <= api._EARLY_MOVER_WATCH_DIGEST_DEDUPE_SEC


# ── Q2: 15-Min-Frische-Gate ──


def test_q2_trigger_older_than_15min_is_suppressed_for_mail():
    now = 1_000_000.0
    row = _early_mover_row(
        Symbol="STALE",
        intraday_trigger={"ok": True, "reason": "5m_breakout_volume_confirmed", "checked_at": now - 1200},
    )

    state = api._classify_alert_candidate("early_movers", row, now)

    assert state["alertable_now"] is False
    assert "trigger_stale_for_mail" in state["suppression_reasons"]
    assert state["decision"] == "WAIT_TRIGGER"


def test_q2_trigger_younger_than_15min_stays_alertable():
    now = 1_000_000.0
    row = _early_mover_row(
        Symbol="FRESH",
        intraday_trigger={"ok": True, "reason": "5m_breakout_volume_confirmed", "checked_at": now - 600},
    )

    state = api._classify_alert_candidate("early_movers", row, now)

    assert "trigger_stale_for_mail" not in state["suppression_reasons"]
    assert state["alertable_now"] is True


def test_q2_scan_age_proxy_used_when_row_timestamp_missing():
    now = 1_000_000.0
    stale = _early_mover_row(Symbol="OLDSCAN", _mail_scan_age_sec=1200)
    fresh = _early_mover_row(Symbol="NEWSCAN", _mail_scan_age_sec=300)

    stale_state = api._classify_alert_candidate("early_movers", stale, now)
    fresh_state = api._classify_alert_candidate("early_movers", fresh, now)

    assert "trigger_stale_for_mail" in stale_state["suppression_reasons"]
    assert "trigger_stale_for_mail" not in fresh_state["suppression_reasons"]
    # Ohne jeden Zeitstempel: kein Gate (Alter unbekannt)
    unknown_state = api._classify_alert_candidate("early_movers", _early_mover_row(Symbol="NOAGE"), now)
    assert "trigger_stale_for_mail" not in unknown_state["suppression_reasons"]


def test_q2_stale_trigger_row_lands_in_watch_mail_with_hint(monkeypatch):
    sent = _capture_send(monkeypatch)
    row = _early_mover_row(
        Symbol="STALEROW",
        intraday_trigger={"ok": True, "reason": "5m_breakout_volume_confirmed", "checked_at": time.time() - 1200},
    )

    api._send_early_mover_long_alerts({"coins": [row]})

    assert len(sent) == 1
    subject, body, kwargs = sent[0]
    assert kwargs.get("mail_class") == "watch"
    assert "STALEROW" in body
    assert "Trigger abgelaufen - neu bewerten" in body


def test_q2_chase_protection_applies_to_crypto_without_soft_bypass(monkeypatch):
    """Preis weit weg von der Entry-Zone => Health-NO_TRADE, weder JETZT- noch Watch-Mail.

    Health berechnet die Entry-Distanz live aus current/entry/risk: Preis 1.55
    bei Entry 1.25 / Stop 1.15 = 3R Chase => distance-/live_rr-Gates greifen
    nach Q1 auch fuer Crypto-Swing (kein Soft-Bypass mehr).
    """
    sent = _capture_send(monkeypatch)
    row = _early_mover_row(
        Symbol="CHASED",
        Price=1.55,
        current_price=1.55,
        distance_to_entry_r=3.0,
        live_rr_ratio=0.2,
    )

    state = api._classify_alert_candidate("early_movers", row, time.time())
    assert state["alertable_now"] is False
    assert "trade_health_chase_risk" in state["suppression_reasons"]
    assert any(reason.startswith("trade_health_") for reason in state["suppression_reasons"])

    api._send_early_mover_long_alerts({"coins": [row]})
    assert sent == []


# ── H1: Entry-Score-Cap ──


def test_h1_low_entry_score_caps_swing_mail_score():
    state = api._classify_alert_candidate(
        "early_movers", _early_mover_row(Symbol="WEAKENTRY", score=95, entry_score=10), 1_000_000.0
    )

    assert state["score"] == 10
    assert state["alertable_now"] is False
    assert "score_below_alert_threshold" in state["suppression_reasons"]


def test_h1_entry_score_zero_is_valid_and_caps_to_zero():
    state = api._classify_alert_candidate(
        "early_movers", _early_mover_row(Symbol="ZEROENTRY", score=95, entry_score=0), 1_000_000.0
    )

    assert state["score"] == 0
    assert state["alertable_now"] is False


def test_h1_good_entry_score_caps_to_entry_score():
    state = api._classify_alert_candidate(
        "early_movers", _early_mover_row(Symbol="OKENTRY", score=95, entry_score=85), 1_000_000.0
    )

    assert state["score"] == 85
    assert state["alertable_now"] is True


# ── H2: Betreff-Praefixe pro Mail-Klasse ──


def test_h2_subject_prefixes_per_mail_class():
    assert api._apply_mail_class_subject("3 Top-Setups", "trade") == api._MAIL_CLASS_SUBJECT_PREFIXES["trade"] + "3 Top-Setups"
    assert api._apply_mail_class_subject("Aktien Strategie Swing", "swing_trade") == api._MAIL_CLASS_SUBJECT_PREFIXES["swing_trade"] + "Aktien Strategie Swing"
    assert api._apply_mail_class_subject("Crypto Retest-Zonen (2 Kandidaten)", "watch") == api._MAIL_CLASS_SUBJECT_PREFIXES["watch"] + "Crypto Retest-Zonen (2 Kandidaten)"
    assert api._apply_mail_class_subject("Status", "info") == api._MAIL_CLASS_SUBJECT_PREFIXES["info"] + "Status"
    # trade_horizon ist Routing/Subscriber-Logik; der sichtbare Betreff haengt an mail_class.
    assert api._apply_mail_class_subject("Swing ohne Klasse", "trade", trade_horizon="swing") == api._MAIL_CLASS_SUBJECT_PREFIXES["trade"] + "Swing ohne Klasse"
    # Unbekannte Klasse faellt konservativ auf "trade" zurueck
    assert api._apply_mail_class_subject("X", "unknown") == api._MAIL_CLASS_SUBJECT_PREFIXES["trade"] + "X"


def test_h2_no_double_emoji_for_legacy_subjects():
    trade_prefix = api._MAIL_CLASS_SUBJECT_PREFIXES["trade"]
    info_prefix = api._MAIL_CLASS_SUBJECT_PREFIXES["info"]
    # Bestehende Emojis am Anfang werden ersetzt, nicht gestapelt
    assert api._apply_mail_class_subject("\U0001F6A8 3 Top-Setups — BI Scanner LONG", "trade") == trade_prefix + "3 Top-Setups — BI Scanner LONG"
    assert api._apply_mail_class_subject("⚠️ CRASH: 2 Aktien", "trade") == trade_prefix + "CRASH: 2 Aktien"
    assert api._apply_mail_class_subject("\U0001F514 5 ORB Breakouts", "trade") == trade_prefix + "5 ORB Breakouts"
    assert api._apply_mail_class_subject("✅ TradingBot Test", "info") == info_prefix + "TradingBot Test"
    # Idempotent: bereits klassifizierte Betreffs bleiben einfach
    once = api._apply_mail_class_subject("Foo", "trade")
    assert api._apply_mail_class_subject(once, "trade") == once
    assert once.count("\U0001F6A8") == 1
    once_swing = api._apply_mail_class_subject("Swing Foo", "swing_trade")
    assert api._apply_mail_class_subject(once_swing, "swing_trade") == once_swing
    assert once_swing.count("\U0001F6A8") == 1
    assert "SWING:" in once_swing
    # 2026-07-31: Swing-Mails tragen kein "JETZT" mehr - mehrtaegige Plaene;
    # "JETZT" bleibt den Intraday-Trade-Mails mit Frische-Gate vorbehalten.
    assert "JETZT" not in once_swing
    # Alte "JETZT SWING"-Betreffe werden beim Re-Prefix ersetzt, nicht gestapelt:
    legacy = api._apply_mail_class_subject("\U0001F6A8 JETZT SWING: Aktien Foo", "swing_trade")
    assert legacy == api._MAIL_CLASS_SUBJECT_PREFIXES["swing_trade"] + "Aktien Foo"
    assert "JETZT" not in legacy


def test_h2_strategy_swing_timing_label_is_human_readable():
    assert api._format_alert_timing_label("SWING_SETUP", "stocks") == "Swing-Setup aktiv"
    assert api._format_alert_timing_label("", "stocks") == "Swing-Setup aktiv"
    assert api._format_alert_timing_label("WAIT_FOR_RETEST", "stocks") == "Retest abwarten"


def test_h2_stock_strategy_swing_mail_uses_swing_class_and_label(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda: {"allowed": True})
    monkeypatch.setattr(api, "_enrich_stock_alert_5m_state", lambda scanner_key, row, strategy_name: row)
    monkeypatch.setattr(
        api,
        "_classify_alert_candidate",
        lambda scanner_key, row, now: {
            "alertable_now": True,
            "suppression_reasons": [],
            "cooldown_key": "stock_strategy:TEST",
            "ticker": "TEST",
            "grade": "A",
            "score": 88,
            "price": 10.0,
            "rvol": 1.8,
        },
    )
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append((subject, body, kwargs)) or True)

    api._send_strategy_scan_alerts("Momentum Breakout Long", [{"entry_quality": "SWING_SETUP"}], market_type="stocks")

    assert len(sent) == 1
    subject, body, kwargs = sent[0]
    assert "Aktien Strategie Swing" in subject
    assert kwargs["mail_class"] == "swing_trade"
    assert kwargs["trade_horizon"] == "swing"
    assert "Swing-Setup aktiv" in body
    assert ">SWING_SETUP<" not in body


def test_h2_send_email_alert_records_prefixed_subject(monkeypatch):
    events = []
    monkeypatch.setattr(api, "_record_email_event", lambda subject, status, reason=None: events.append((subject, status, reason)))
    monkeypatch.setattr(api, "_SECRETS", {})  # kein Gmail -> skipped, aber Subject ist schon gepraefixt

    ok = api._send_email_alert("Crypto Retest-Zonen (1 Kandidaten)", "<p>x</p>", bypass_startup_cooldown=True, mail_class="watch")

    assert ok is False
    assert events
    assert events[-1][0].startswith(api._MAIL_CLASS_SUBJECT_PREFIXES["watch"])
    assert events[-1][2] == "missing_gmail_config"


# ── H3: Empfaenger-Routing nach Mail-Klasse ──


def _h3_users():
    return {"users": {
        "plain@x.com": {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": "plain@x.com",
            "trade_alert_horizon": "both",
        },
        "watcher@x.com": {
            "plan": "elite",
            "email_alerts_enabled": True,
            "alert_email": "watcher@x.com",
            "trade_alert_horizon": "both",
            "watch_mail_optin": True,
        },
    }}


def test_h3_watch_mails_require_subscriber_optin(monkeypatch):
    monkeypatch.setattr(
        auth,
        "_load_effective_users_atomic",
        lambda: _h3_users()["users"],
    )

    watch = auth.get_email_alert_recipients(trade_horizon="swing", mail_class="watch")
    trade = auth.get_email_alert_recipients(trade_horizon="swing", mail_class="trade")
    default = auth.get_email_alert_recipients(trade_horizon="swing")

    assert watch == ["watcher@x.com"]
    assert trade == ["plain@x.com", "watcher@x.com"]
    assert default == trade  # trade-Verhalten unveraendert (Default)


def test_h3_send_email_alert_passes_mail_class_and_operator_watch_is_opt_in(monkeypatch):
    captured = {}
    deliveries = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, message):
            deliveries.append(list(recipients))

        def quit(self):
            pass

    monkeypatch.setattr(api.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "op@x.com", "GMAIL_APP_PASSWORD": "pw", "ALERT_EMAIL": "op@x.com"})
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "HAS_AUTH", True)

    def _fake_recipients(trade_horizon="", mail_class="trade", mail_channel=""):
        captured["mail_class"] = mail_class
        return []  # kein Abonnent hat Watch-Opt-in

    monkeypatch.setattr(api, "get_email_alert_recipients", _fake_recipients)

    ok = api._send_email_alert("Crypto Retest-Zonen (1 Kandidaten)", "<p>x</p>", bypass_startup_cooldown=True, mail_class="watch")

    assert ok is False
    assert captured["mail_class"] == "watch"
    assert deliveries == []


def test_h3_operator_can_explicitly_opt_in_to_watch_mail(monkeypatch):
    deliveries = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, message):
            deliveries.append(list(recipients))

        def quit(self):
            pass

    monkeypatch.setattr(api.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(
        api,
        "_SECRETS",
        {
            "GMAIL_USER": "op@x.com",
            "GMAIL_APP_PASSWORD": "pw",
            "ALERT_EMAIL": "op@x.com",
            "ALERT_OPERATOR_WATCH_OPTIN": "1",
        },
    )
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)

    ok = api._send_email_alert(
        "Crypto Retest-Zonen (1 Kandidaten)",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_class="watch",
    )

    assert ok is True
    assert deliveries == [["op@x.com"]]


def test_h3_test_email_goes_only_to_admin(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_require_admin", lambda authorization: None)
    monkeypatch.setattr(api, "_email_alert_status", lambda: {"configured": True})
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "op@x.com", "GMAIL_APP_PASSWORD": "pw", "ALERT_EMAIL": "admin@x.com"})
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append((subject, kwargs)) or True)

    result = api.test_email_alert(authorization="Bearer admin-token")

    assert result["status"] == "ok"
    assert len(sent) == 1
    subject, kwargs = sent[0]
    assert kwargs.get("recipient_emails") == ["admin@x.com"]
    assert kwargs.get("mail_class") == "info"


# ── M1: synthetische Biotech-Level kennzeichnen ──


def test_m1_synthetic_biotech_levels_are_marked_in_mail_html():
    row = {
        "Ticker": "BIO",
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.2,
        "TP1": 11.5,
        "TP2": 12.4,
        "Trade_Setup_Source": "biotech_daily_structure",
        "trade_setup": {
            "stop_source": "ATR invalidation fallback",
            "tp1_source": "R1",
            "tp2_source": "127% range extension",
        },
    }

    html_out = api._format_alert_plan_html(row)

    assert "Struktur-Level (ATR/Support) - nicht Scanner-nativ" in html_out


def test_m1_native_levels_are_not_marked():
    row = {
        "Ticker": "NATIVE",
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.2,
        "TP1": 11.5,
        "TP2": 12.4,
    }

    html_out = api._format_alert_plan_html(row)

    assert "nicht Scanner-nativ" not in html_out


# ── ATR-Abstands-Annotationen (AUDIT 2026-07-28) ─────────────────────────────

def test_atr_distance_annotations_rendered_when_atr_present():
    """Stop/TP1/TP2 tragen '%-Abstand ≈ x×ATR' — TSN-Beispiel aus der Swing-Mail."""
    row = {
        "Ticker": "TSN", "direction": "LONG",
        "Entry": 61.63, "StopLoss": 60.67, "TP1": 63.29, "TP2": 64.53,
        "trade_setup": {"atr": 0.96, "stop_source": "s1_invalidation"},
    }
    html_out = api._format_alert_plan_html(row)
    # Stop: 0.96 Risiko => -1.6% und exakt 1.0xATR
    assert "(-1.6% ≈ 1.0×ATR)" in html_out
    # TP1 +1.66 => +2.7% ≈ 1.7x ; TP2 +2.90 => +4.7% ≈ 3.0x
    assert "+2.7% ≈ 1.7×ATR" in html_out
    assert "+4.7% ≈ 3.0×ATR" in html_out


def test_atr_distance_annotations_absent_without_atr():
    """Ohne ATR keine Annotation (graceful, keine '≈ ?×ATR'-Fragmente)."""
    row = {
        "Ticker": "NOATR", "direction": "LONG",
        "Entry": 10.0, "StopLoss": 9.2, "TP1": 11.5, "TP2": 12.4,
    }
    html_out = api._format_alert_plan_html(row)
    assert "×ATR" not in html_out


def test_atr_distance_annotations_fallback_to_row_key():
    """ATR kommt auch als Row-Key (z. B. atr_14 aus den Technicals)."""
    row = {
        "Ticker": "ROWKEY", "direction": "LONG", "atr_14": 0.5,
        "Entry": 20.0, "StopLoss": 19.0, "TP1": 21.0, "TP2": 22.5,
    }
    html_out = api._format_alert_plan_html(row)
    # Stop -1.0 => -5.0% ≈ 2.0xATR
    assert "(-5.0% ≈ 2.0×ATR)" in html_out


# ── AUDIT 2026-07-28: Volatilitätsbudget (HLN) + Stop-Rausch-Warnung (BELFB) ──


def test_low_volatility_budget_blocks_swing_mail():
    """HLN-Repro: 1,7% ATR — TP1 (1,8xATR ≈ 3% brutto) traegt die Kosten eines
    Swing-Trades nicht. Das Gate greift VOR den Momentum-spezifischen Pruefungen."""
    row = {
        "Strategy": "Momentum Breakout Long",
        "Signal_Direction": "LONG",
        "price": 10.29,
        "trade_setup": {"atr": 0.178},
    }
    ok, reason = api._stock_strategy_mail_quality_state(row)
    assert ok is False
    assert reason == "stock_swing_mail_blocked_low_volatility_budget"


def test_volatility_budget_boundary_and_missing_atr_pass():
    """Genau 2,0% ATR passiert das Budget-Gate; Rows ohne ATR-Metadaten
    behalten das bisherige Verhalten (kein hartes Gate auf Alt-Daten)."""
    row_boundary = {"Strategy": "Gap Momentum Long", "price": 100.0, "trade_setup": {"atr": 2.0}}
    ok, reason = api._stock_strategy_mail_quality_state(row_boundary)
    assert ok is True and reason == ""
    row_legacy = {"Strategy": "Gap Momentum Long", "price": 100.0}
    ok, reason = api._stock_strategy_mail_quality_state(row_legacy)
    assert ok is True and reason == ""


def test_stop_noise_warning_rendered_for_sub_atr_stop():
    """BELFB-Repro: Stop nur 0,6xATR entfernt — Warnung vor erhoehtem
    Stop-out-Risiko gehoert in das Plan-HTML."""
    row = {
        "Ticker": "BELFB", "direction": "SHORT",
        "Entry": 249.36, "StopLoss": 260.90, "TP1": 231.92, "TP2": 213.68,
        "trade_setup": {"atr": 19.2},
    }
    html_out = api._format_alert_plan_html(row)
    assert "Tagesrauschen" in html_out
    assert "0.6×ATR" in html_out


def test_stop_noise_warning_absent_when_stop_beyond_noise():
    """Stop bei 1,0xATR liegt ausserhalb des Rauschbereichs — keine Warnung."""
    row = {
        "Ticker": "TSN", "direction": "LONG",
        "Entry": 61.63, "StopLoss": 60.67, "TP1": 63.29, "TP2": 64.53,
        "trade_setup": {"atr": 0.96},
    }
    html_out = api._format_alert_plan_html(row)
    assert "Tagesrauschen" not in html_out


# ── AUDIT 2026-07-28: Mail-Kanal-Opt-out im Versandpfad (api._send_email_alert) ──


def test_mail_channel_filters_operator_and_passes_channel_to_recipients(monkeypatch):
    deliveries = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, message):
            deliveries.append(list(recipients))

        def quit(self):
            pass

    monkeypatch.setattr(api.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "op@x.com", "GMAIL_APP_PASSWORD": "pw", "ALERT_EMAIL": "op@x.com"})
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", True)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    # Betreiber-Adresse hat den crypto-Kanal abgeschaltet.
    monkeypatch.setattr(api, "mail_channel_enabled", lambda addr, channel: not (addr == "op@x.com" and channel == "crypto"))
    captured = {}

    def _fake_recipients(trade_horizon="", mail_class="trade", mail_channel=""):
        captured["mail_channel"] = mail_channel
        return ["sub@example.com"]

    monkeypatch.setattr(api, "get_email_alert_recipients", _fake_recipients)

    ok = api._send_email_alert(
        "Crypto Early Mover LONG Digest: 1 Setup(s)",
        "<p>x</p>",
        bypass_startup_cooldown=True,
        mail_channel="crypto",
    )

    assert ok is True
    assert captured["mail_channel"] == "crypto"
    # op@x.com durch Kanal-Opt-out entfernt; Abonnent mit Kanal AN bleibt.
    assert deliveries == [["sub@example.com"]]


def test_mail_channel_empty_keeps_operator_unfiltered(monkeypatch):
    deliveries = []

    class _FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def ehlo(self):
            pass

        def starttls(self):
            pass

        def login(self, user, password):
            pass

        def sendmail(self, sender, recipients, message):
            deliveries.append(list(recipients))

        def quit(self):
            pass

    monkeypatch.setattr(api.smtplib, "SMTP", _FakeSMTP)
    monkeypatch.setattr(api, "_SECRETS", {"GMAIL_USER": "op@x.com", "GMAIL_APP_PASSWORD": "pw", "ALERT_EMAIL": "op@x.com"})
    monkeypatch.setattr(api, "ALERT_SEND_TO_SUBSCRIBERS", False)
    monkeypatch.setattr(api, "HAS_AUTH", True)
    # Selbst ein "alles aus"-Filter darf ohne mail_channel nicht greifen.
    monkeypatch.setattr(api, "mail_channel_enabled", lambda addr, channel: False)

    ok = api._send_email_alert("Test", "<p>x</p>", bypass_startup_cooldown=True)

    assert ok is True
    assert deliveries == [["op@x.com"]]
