"""Tests fuer das Pre-Market-Radar (AUDIT 2026-07-29, Punkt C / RITM+NVST).

Deckt B1 (PM-Scan-Gates + RVOL-Proxy), B2 (PM-Mail-Pfad + Kanal) und
B3 (dynamischer Opening-Takt) ab. Die Fenster-Tests sind kalenderfest:
feste ET-Zeitpunkte an einem bekannten Wochentag (2026-07-27 = Montag).
"""
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import api


ET = ZoneInfo("America/New_York")


def _et(day: int, hour: int, minute: int) -> datetime:
    """Fester ET-Zeitpunkt im Juli 2026, als aware-UTC fuer die API."""
    return datetime(2026, 7, day, hour, minute, tzinfo=ET).astimezone(timezone.utc)


MONDAY = 27      # 2026-07-27 = Montag
SATURDAY = 25    # 2026-07-25 = Samstag


@pytest.fixture(autouse=True)
def _offline_guards(monkeypatch, tmp_path):
    """Asset-Guard + Dedupe offline halten; Cooldowns je Test sauber."""
    monkeypatch.setattr(api, "_EMAIL_DEDUPE_FILE", str(tmp_path / "email_dedupe.json"))
    monkeypatch.setattr(api, "_load_common_stock_universe", lambda *a, **k: ({"AAA", "BBB", "NVST"}, "unit"))
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: {"ok": True, "candidate": dict(row)},
    )
    api._EMAIL_COOLDOWN.clear()
    yield
    api._EMAIL_COOLDOWN.clear()


# ── Fenster-Logik ──────────────────────────────────────────────

def test_premarket_window_boundaries():
    assert api._premarket_window_active(_et(MONDAY, 6, 59)) is False
    assert api._premarket_window_active(_et(MONDAY, 7, 0)) is True
    assert api._premarket_window_active(_et(MONDAY, 8, 30)) is True
    assert api._premarket_window_active(_et(MONDAY, 9, 24)) is True
    assert api._premarket_window_active(_et(MONDAY, 9, 25)) is False
    assert api._premarket_window_active(_et(MONDAY, 12, 0)) is False


def test_premarket_window_closed_on_weekend():
    assert api._premarket_window_active(_et(SATURDAY, 8, 0)) is False


def test_opening_window_boundaries():
    assert api._opening_window_active(_et(MONDAY, 9, 24)) is False
    assert api._opening_window_active(_et(MONDAY, 9, 25)) is True
    assert api._opening_window_active(_et(MONDAY, 10, 30)) is True
    assert api._opening_window_active(_et(MONDAY, 11, 29)) is True
    assert api._opening_window_active(_et(MONDAY, 11, 30)) is False
    assert api._opening_window_active(_et(SATURDAY, 10, 0)) is False


# ── B1: RVOL-Proxy + fruehe PM-Gates ───────────────────────────

def test_premarket_rvol_proxy_thresholds():
    assert api._premarket_rvol_proxy(0) == 0.0
    assert api._premarket_rvol_proxy(499_999) == 0.0
    assert api._premarket_rvol_proxy(500_000) == 1.5
    assert api._premarket_rvol_proxy(1_000_000) == 2.0
    assert api._premarket_rvol_proxy(2_000_000) == 2.5
    assert api._premarket_rvol_proxy(5_000_000) == 3.0
    assert api._premarket_rvol_proxy(50_000_000) == 3.0
    assert api._premarket_rvol_proxy(None) == 0.0
    assert api._premarket_rvol_proxy("bad") == 0.0


def test_premarket_scan_gate_reason():
    # Zu wenig absolute PM-Liquiditaet
    assert api._premarket_scan_gate_reason(dollar_vol=300_000, spread_pct=2.0) == "premarket_dollar_volume_filter"
    # Keine Quote = keine Spread-Info = kein PM-Trade
    assert api._premarket_scan_gate_reason(dollar_vol=800_000, spread_pct=None) == "premarket_missing_quote"
    # Zu weite PM-Spreize
    assert api._premarket_scan_gate_reason(dollar_vol=800_000, spread_pct=7.5) == "premarket_spread_guard"
    # Passiert: genuegend Liquiditaet + enge Spreize (Grenzwert 7.0 ist ok)
    assert api._premarket_scan_gate_reason(dollar_vol=500_000, spread_pct=7.0) == ""
    assert api._premarket_scan_gate_reason(dollar_vol=2_000_000, spread_pct=1.2) == ""
    # Strategie-Override der PM-Mindestliquiditaet
    assert api._premarket_scan_gate_reason(dollar_vol=600_000, spread_pct=1.0, min_dollar_vol=1_000_000) == "premarket_dollar_volume_filter"


# ── B3: dynamischer Scan-Takt ──────────────────────────────────

def test_effective_scan_interval_opening_window():
    assert api._scan_status["strategy_scan"]["interval_min"] == 60
    assert api._effective_scan_interval_min("strategy_scan", _et(MONDAY, 10, 0)) == 10.0
    assert api._effective_scan_interval_min("strategy_scan", _et(MONDAY, 12, 0)) == 60.0
    assert api._effective_scan_interval_min("strategy_scan", _et(MONDAY, 8, 0)) == 60.0
    # Andere Scanner bleiben unberuehrt
    assert api._effective_scan_interval_min("orb", _et(MONDAY, 10, 0)) == api._scan_status["orb"]["interval_min"]
    # Unbekannter Scanner: defensiver Fallback
    assert api._effective_scan_interval_min("does_not_exist", _et(MONDAY, 10, 0)) == 1.0


# ── B2: PM-Kandidaten-Klassifizierung ──────────────────────────

def _pm_row(**overrides):
    row = {
        "Ticker": "AAA",
        "ticker": "AAA",
        "Strategy": "Gap Momentum Long",
        "grade": "S",
        "score": 90,
        "price": 10.0,
        "Preis": 10.0,
        "Premarket": True,
        "premarket": True,
        "PM_DollarVol": 2_000_000,
        "Extension_ATR": 1.2,
        "RVOL": 2.5,
        "direction": "LONG",
        "Entry": 10.0,
        "StopLoss": 9.5,
        "TP1": 10.8,
        "TP2": 11.3,
    }
    row.update(overrides)
    return row


def test_classify_premarket_candidate_alertable():
    state = api._classify_premarket_candidate("stock_strategy", _pm_row())
    assert state["alertable_now"] is True
    assert state["suppression_reasons"] == []
    assert state["cooldown_key"].startswith("stock_strategy_AAA_")
    assert state["cooldown_key"].endswith("__premarket")
    assert state["pm_dollar_vol"] == 2_000_000


def test_classify_premarket_candidate_rejects_non_pm_row():
    state = api._classify_premarket_candidate("stock_strategy", _pm_row(Premarket=False, premarket=False))
    assert state["alertable_now"] is False
    assert "not_a_premarket_row" in state["suppression_reasons"]


def test_classify_premarket_candidate_score_floor_is_stricter_than_regular():
    state = api._classify_premarket_candidate("stock_strategy", _pm_row(score=84, grade="A"))
    assert state["alertable_now"] is False
    assert "premarket_score_below_threshold" in state["suppression_reasons"]


def test_classify_premarket_candidate_liquidity_floor():
    state = api._classify_premarket_candidate("stock_strategy", _pm_row(PM_DollarVol=300_000))
    assert state["alertable_now"] is False
    assert "premarket_liquidity_below_threshold" in state["suppression_reasons"]


def test_classify_premarket_candidate_extension_cap():
    state = api._classify_premarket_candidate("stock_strategy", _pm_row(Extension_ATR=3.5))
    assert state["alertable_now"] is False
    assert "premarket_extension_too_stretched" in state["suppression_reasons"]


def test_classify_premarket_candidate_missing_levels():
    row = _pm_row()
    for key in ("Entry", "StopLoss", "TP1", "TP2"):
        row.pop(key, None)
    state = api._classify_premarket_candidate("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "premarket_missing_trade_levels" in state["suppression_reasons"]


def test_classify_premarket_candidate_cooldown_namespace():
    row = _pm_row()
    identity_key = api._alert_signal_identity_key("stock_strategy", row, "AAA")
    api._EMAIL_COOLDOWN[f"{identity_key}__premarket"] = time.time()
    state = api._classify_premarket_candidate("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "cooldown_active" in state["suppression_reasons"]
    # ... waehrend der Regular-Cooldown desselben Tickers frei ist
    assert identity_key not in api._EMAIL_COOLDOWN


# ── B2: PM-Mail-Pfad Ende-zu-Ende ──────────────────────────────

def _capture_mail(monkeypatch):
    sent = []
    monkeypatch.setattr(api, "_send_email_alert", lambda subject, body, **kwargs: sent.append({"subject": subject, "body": body, **kwargs}) or True)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    return sent


def test_premarket_mail_sends_with_own_channel_and_warning(monkeypatch):
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": False, "session": "CLOSED", "reason": "unit closed"})
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: True)
    sent = _capture_mail(monkeypatch)

    rows = [
        _pm_row(),
        # Regulaere (nicht-PM) Row darf in der PM-Mail NICHT auftauchen
        _pm_row(ticker="BBB", Ticker="BBB", Premarket=False, premarket=False),
    ]
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", rows, "stocks")

    assert len(sent) == 1
    mail = sent[0]
    assert mail["subject"].startswith("Aktien Pre-Market Radar")
    assert mail["mail_channel"] == "stocks_premarket"
    assert "PRE-MARKET-FRUEHWARNUNG" in mail["body"]
    assert "PM $2.0M" in mail["body"]
    assert "AAA" in mail["body"]
    assert "BBB" not in mail["body"]
    assert "Score >= 85" in mail["body"] or "ab Score 85" in mail["body"]
    # Eigener Cooldown-Namespace: Regular-Mail nach Open bleibt moeglich
    assert any(
        key.startswith("stock_strategy_AAA_") and key.endswith("__premarket")
        for key in api._EMAIL_COOLDOWN
    )
    assert not any(
        key.startswith("stock_strategy_AAA_") and not key.endswith("__premarket")
        for key in api._EMAIL_COOLDOWN
    )


def test_premarket_mixed_batch_suppresses_cup_watch_per_row(monkeypatch):
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *a, **k: {
            "allowed": False,
            "session": "PREMARKET",
            "reason": "unit Monday premarket",
        },
    )
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: True)
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: "2026-08-31")
    monkeypatch.setattr(
        api,
        "_previous_us_exchange_trading_date_str",
        lambda _day: "2026-08-28",
    )
    sent = []
    revalidated = []
    suppressions = []
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: sent.append((subject, body, kwargs)) or True,
    )
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: suppressions.append((scanner, dict(reasons))),
    )
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda row, **kwargs: revalidated.append(row.get("ticker"))
        or {"ok": True, "candidate": dict(row)},
    )

    cup_watch = _pm_row(
        ticker="CUPX",
        Ticker="CUPX",
        Strategy="Cup and Handle Breakout",
        daily_close_confirmed=True,
        daily_close_confirmation_date="2026-08-28",
        last_daily_bar_date="2026-08-28",
        entry_status="DAILY_CLOSE_CONFIRMED_WATCH_ONLY",
        trade_signal="BEOBACHTEN",
    )
    genuine_pm = _pm_row(ticker="AAA", Ticker="AAA")

    api._send_strategy_scan_alerts(
        "Aktien Auto-Sweep", [cup_watch, genuine_pm], "stocks"
    )

    assert len(sent) == 1
    assert "AAA" in sent[0][1]
    assert "CUPX" not in sent[0][1]
    assert revalidated == ["AAA"]
    assert [
        row.get("ticker") for row in sent[0][2].get("tracking_rows", [])
    ] == ["AAA"]
    assert any(
        reasons.get("daily_close_confirmed_watch_only_no_afterhours_entry") == 1
        for _scanner, reasons in suppressions
    )


def test_premarket_mail_skips_when_no_pm_rows(monkeypatch):
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": False, "session": "CLOSED", "reason": "unit closed"})
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: True)
    sent = _capture_mail(monkeypatch)
    events = []
    # NACH _capture_mail setzen — sonst ueberschreibt der Helper diesen Mock.
    monkeypatch.setattr(api, "_record_email_event", lambda subject, status, reason=None: events.append((subject, status, reason)))

    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [_pm_row(ticker="BBB", Ticker="BBB", Premarket=False, premarket=False)], "stocks")

    assert sent == []
    assert any(status == "skipped" for _, status, _ in events)


def test_regular_path_untouched_when_market_open_in_pm_window(monkeypatch):
    """9:25–9:30 ET Ueberlappung: Markt offen => normaler Pfad, kein PM-Modus."""
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: {"allowed": True, "session": "US_REGULAR", "reason": "unit open"})
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: True)  # darf nichts aendern
    monkeypatch.setattr(api, "_fetch_stock_swing_execution_state", lambda *a, **k: {
        "Swing_4H_Execution_Checked": True,
        "Swing_4H_Execution_Status": "CLEAR",
        "Swing_4H_Execution_Reason": "unit",
    })
    monkeypatch.setattr(api, "_fetch_recent_stock_5m_bars", lambda *a, **k: [])
    sent = _capture_mail(monkeypatch)

    # Gap Momentum (kein Freshness-Fetch nötig), Regular-Row ohne PM-Flag
    row = _pm_row(Premarket=False, premarket=False, RVOL=3.0, score=92, History_OK=True,
                  MedianDollarVol20=5_000_000, ATR14=0.4, Momentum_Breakout_Type="20D_HIGH_BREAKOUT",
                  Breakout_Freshness_Checked=True, Breakout_Freshness_Status="FRESH_BREAKOUT")
    api._send_strategy_scan_alerts("Aktien Auto-Sweep", [row], "stocks")

    assert len(sent) == 1
    assert sent[0]["subject"].startswith("Aktien Strategie Swing")
    assert sent[0]["mail_channel"] == "stocks_swing"


def test_premarket_mail_channel_registered():
    from modules.auth import MAIL_CHANNELS
    assert MAIL_CHANNELS.get("stocks_premarket") == "Aktien Pre-Market"
