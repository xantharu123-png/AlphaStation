# -*- coding: utf-8 -*-
"""Regressionstests fuer die Cup&Handle-Audit-Fixes (AUDIT_CUP_HANDLE_2026-06-10).

K-1  Anti-Fenster-Shopping (globales Pre-Breakout-Hoch + pivot-treue Bestauswahl)
K-2a Mail-Gate-Suppression fuer INTRADAY_UNCONFIRMED/BEOBACHTEN/WAIT_FOR_*
K-2b Daily-Close-Bestaetigungs-Mail (eng begrenzte Session-Gate-Ausnahme)
H-1  RVOL-Mail-Floor ohne Strategy-Key (Token-Match auf pattern/pattern_type)
M-1  Handle-Abwaertsdrift-Gate (aufwaerts keilender Handle => Reject)
M-2  Volumen-Dry-up als Hard-Gate (Handle-Vol > 1.15x Cup => Reject)
M-3  Stop-Cap 10% (struktureller Stop zu weit => kein Trade)
M-4  Grade-Spreizung (S erst ab final 90, A fuer 80-89)
M-5  Ehrliche Labels (kein "5m-Trigger"-Versprechen)
N-1/N-4 Vortag-Filter geweitet / data_gaps-Flag

Session-Status wird IMMER gemockt (tageszeit-/kalenderunabhaengig).
"""
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api


# ---------------------------------------------------------------------------
# Fixtures (uebernommen aus test_cup_handle_scanner.py + /tmp/cuphandle_audit)
# ---------------------------------------------------------------------------

def _bar(close, volume=1_000_000, high=None, low=None, open_=None):
    high = close * 1.012 if high is None else high
    low = close * 0.988 if low is None else low
    open_ = close * 0.997 if open_ is None else open_
    return {"open": open_, "high": high, "low": low, "close": close, "volume": volume}


_TEXTBOOK_HANDLE = [98.8, 97.2, 95.5, 94.0, 94.8, 95.6, 96.7, 97.5, 98.3]


def _cup_handle_bars(last_close=101.7, last_volume=2_400_000, handle_vol=650_000,
                     handle_closes=None, last_bar_date=""):
    """Lehrbuch-Fixture wie in test_cup_handle_scanner.py (Lip = globales Hoch 101.2)."""
    bars = []
    for i in range(28):
        bars.append(_bar(100 - 24 * (i / 27), volume=1_050_000))
    for i in range(26):
        bars.append(_bar(75 + 2.0 * abs((i - 13) / 13), volume=820_000))
    for i in range(36):
        bars.append(_bar(77 + 22.5 * (i / 35), volume=1_150_000))
    for close in (handle_closes or _TEXTBOOK_HANDLE):
        bars.append(_bar(close, volume=handle_vol))
    bars.append(_bar(last_close, volume=last_volume, high=max(last_close * 1.01, 102.2), low=99.8))
    if last_bar_date:
        bars[-1]["date"] = last_bar_date
    return bars


def _v4_cup_bars(last_close, last_volume=2_400_000):
    """K-1-Beweis-Fixture aus v4_window_shopping_proof.py (echter Rim 101.20)."""
    bars = []
    for i in range(28):
        bars.append(_bar(100 - 24 * (i / 27), volume=1_050_000))
    for i in range(26):
        bars.append(_bar(75 + 2.0 * abs((i - 13) / 13), volume=820_000))
    for i in range(36):
        bars.append(_bar(77 + 22.5 * (i / 35), volume=1_150_000))
    for close in _TEXTBOOK_HANDLE:
        bars.append(_bar(close, volume=650_000))
    bars.append(_bar(last_close, volume=last_volume,
                     high=last_close * 1.006, low=min(99.4, last_close * 0.99)))
    return bars


def _mk_candidate(rvol=1.8, base_score=82, bars=None):
    return {
        "ticker": "CUPX", "Ticker": "CUPX",
        "price": 101.7, "Preis": 101.7,
        "Dollar_Volume": 8_000_000,
        "base_score": base_score, "score": base_score,
        "RVOL": rvol, "rvol": rvol,
        "Change_Pct": 2.4, "change_pct": 2.4,
        "Close_Position": 0.82, "close_pos": 0.82,
        "open_to_current_pct": 1.1,
        "latest_bar_change_pct": 0.22, "latest_bar_close_pos": 0.82,
        "_daily_bars": bars if bars is not None else _cup_handle_bars(),
    }


def _today_et_str():
    from zoneinfo import ZoneInfo
    return datetime.now(timezone.utc).astimezone(ZoneInfo("America/New_York")).date().isoformat()


def _mock_session(monkeypatch, allowed):
    status = (
        {"allowed": True, "session": "US_REGULAR", "reason": "unit-test open"}
        if allowed else
        {"allowed": False, "session": "CLOSED", "reason": "unit-test closed"}
    )
    monkeypatch.setattr(api, "_stock_trade_email_status", lambda *a, **k: dict(status))


def _mock_mail_env(monkeypatch, allowed):
    """Netz-/Mail-/Kalender-Mocks fuer End-to-End-Mail-Pfade (kein I/O)."""
    sent = []
    _mock_session(monkeypatch, allowed)
    monkeypatch.setattr(
        api, "_fetch_long_latest_intraday_state",
        lambda t: {"latest_bar_change_pct": 0.25, "latest_bar_close_pos": 0.85},
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_swing_execution_state",
        lambda *a, **k: {
            "Swing_4H_Execution_Checked": True,
            "Swing_4H_Execution_Status": "CLEAR",
            "Swing_4H_Execution_Reason": "unit_test_clear",
        },
    )
    monkeypatch.setattr(api, "_stock_alert_asset_exclusion_reason", lambda *a, **k: None)
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda *a, **k: None)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **k: True)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda *a, **k: True)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    # Kalender deterministisch: "heute ist US-Handelstag" (kein Wochenend-Flake)
    monkeypatch.setattr(api, "_is_exchange_trading_day", lambda *a, **k: True)
    # Zeitrobust (30.07.): PM-Fenster pinnen — zwischen 07:00-09:25 ET wuerde
    # _send_strategy_scan_alerts sonst in den Pre-Market-Modus wechseln und
    # die K-2b/Daily-Close-Pfade umgehen (Wall-Clock-Flake).
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: False)
    monkeypatch.setattr(
        api, "_send_email_alert",
        lambda subject, body, **k: (sent.append({"subject": subject, "body": body}), True)[1],
    )
    api._EMAIL_COOLDOWN.clear()
    return sent


# ---------------------------------------------------------------------------
# K-1 — Anti-Fenster-Shopping
# ---------------------------------------------------------------------------

def test_k1_price_below_real_rim_returns_none():
    # Kurs 98.0 liegt 3.2% unter dem echten Rim 101.20 — frueher CONFIRMED
    # mit Shopping-Lip 97.44, jetzt: kein Pivot, kein Setup.
    assert api._detect_cup_handle_breakout(_v4_cup_bars(98.0), current_price=98.0) is None


def test_k1_true_breakout_entry_references_real_rim():
    setup = api._detect_cup_handle_breakout(_v4_cup_bars(102.0), current_price=102.0)
    assert setup is not None
    # Entry muss das echte Strukturhoch referenzieren (101.20 +-1%) —
    # frueher meldete der Scanner Entry 98.09.
    assert abs(setup["entry"] / 101.2 - 1.0) <= 0.01


def test_k1_textbook_fixture_still_matches_with_global_lip():
    # Bestands-Fixture: deren Lip IST das globale Hoch -> muss weiter matchen.
    setup = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)
    assert setup is not None
    assert abs(setup["entry"] / 101.2 - 1.0) <= 0.01
    assert setup["stop_loss"] < setup["entry"] < setup["tp1"] < setup["tp2"]


# ---------------------------------------------------------------------------
# K-2a — Mail-Gate-Suppression fuer unbestaetigte Rows
# ---------------------------------------------------------------------------

def test_k2a_unconfirmed_row_not_alertable_and_not_mailed(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=True)  # Session OFFEN
    row = api._apply_cup_handle_strategy_filter(_mk_candidate(), {"min_dollar_volume": 2_000_000})
    assert row is not None
    assert row["entry_status"] == "INTRADAY_UNCONFIRMED"
    assert row["trade_signal"] == "BEOBACHTEN"

    state = api._classify_alert_candidate("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "intraday_unconfirmed_pattern" in state["suppression_reasons"]

    # Laufzeit-Regression aus v3b: dieselbe Row darf NICHT mehr gemailt werden.
    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    assert sent == []


# ---------------------------------------------------------------------------
# K-2b — Daily-Close-Bestaetigungs-Mail (eng begrenzte Ausnahme)
# ---------------------------------------------------------------------------

def _confirmed_fresh_row(monkeypatch, bar_date):
    bars = _cup_handle_bars(last_bar_date=bar_date)
    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=bars), {"min_dollar_volume": 2_000_000}
    )
    assert row is not None
    assert row["entry_status"] == "BREAKOUT_CONFIRMED"
    return row


def test_k2b_afterhours_dailyclose_cannot_bypass_executable_revalidation(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=False)  # Session ZU (Afterhours)
    row = _confirmed_fresh_row(monkeypatch, _today_et_str())
    validations = []
    tracked = []
    monkeypatch.setattr(
        api,
        "_safe_record_alert_signals",
        lambda *args, **kwargs: tracked.append((args, kwargs)),
    )
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda *_args, **kwargs: validations.append(kwargs.get("price_session"))
        or {"ok": False, "reason": "final_price_session_not_executable"},
    )

    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    assert validations == ["CLOSED"]
    assert sent == []
    assert tracked == []


def test_k2b_open_session_sends_no_mail(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=True)  # Session OFFEN
    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date=_today_et_str())),
        {"min_dollar_volume": 2_000_000},
    )
    assert row is not None
    assert row["entry_status"] == "INTRADAY_UNCONFIRMED"  # Downgrade greift
    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    assert sent == []


def test_k2b_stale_daily_bar_sends_no_mail(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=False)
    row = _confirmed_fresh_row(monkeypatch, "2026-06-09")  # Vortags-Kerze => stale
    assert api._strategy_rows_daily_close_confirmed([row]) is False
    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    assert sent == []


# ---------------------------------------------------------------------------
# H-1 — RVOL-Mail-Floor ohne Strategy-Key
# ---------------------------------------------------------------------------

def test_h1_rvol_floor_15_without_strategy_key(monkeypatch):
    _mock_mail_env(monkeypatch, allowed=False)
    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(rvol=1.2), {"min_dollar_volume": 2_000_000}
    )
    assert row is not None
    assert "Strategy" not in row and "strategy" not in row  # Pattern-Row ohne Key
    # Token-Match auf pattern/pattern_type zieht den Breakout-Floor:
    assert api._alert_min_rvol_for_row("stock_strategy", row) == api._ALERT_BREAKOUT_MIN_RVOL == 1.5
    state = api._classify_alert_candidate("stock_strategy", row)
    assert "rvol_below_alert_threshold" in state["suppression_reasons"]
    assert state["alertable_now"] is False
    # Default-Floor fuer Nicht-Breakout-Rows bleibt unangetastet:
    assert api._alert_min_rvol_for_row("stock_strategy", {"pattern": "MA Bounce"}) == api._ALERT_MIN_RVOL


# ---------------------------------------------------------------------------
# M-1 — Handle-Abwaertsdrift-Gate
# ---------------------------------------------------------------------------

def test_m1_upward_wedging_handle_rejected():
    wedge = [95.0, 95.6, 96.2, 96.8, 97.4, 98.0, 98.5, 99.0, 99.3]
    assert api._detect_cup_handle_breakout(
        _cup_handle_bars(handle_closes=wedge), current_price=101.7
    ) is None


# ---------------------------------------------------------------------------
# M-2 — Volumen-Dry-up als Hard-Gate
# ---------------------------------------------------------------------------

def test_m2_handle_distribution_volume_rejected():
    # v1b-Fall A: Handle-Volumen 2.2x Cup-Schnitt (Distribution!), Breakout-
    # Volumen hoch genug fuers RVOL-Gate => das Dry-up-Gate muss verwerfen.
    bars = _cup_handle_bars(handle_vol=2_200_000, last_volume=4_000_000)
    assert api._detect_cup_handle_breakout(bars, current_price=101.7) is None


def test_m2_real_dryup_still_scores_bonus():
    setup = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)
    assert setup is not None
    assert setup["handle_volume_contracts"] is True  # 0.64x < 0.85x = echtes Dry-up


# ---------------------------------------------------------------------------
# M-3 — Stop-Cap 10%
# ---------------------------------------------------------------------------

def _deep_cup_bars(handle_closes):
    """Tiefer Cup (Boden ~65, Tiefe ~36%): genug measured move fuer die RR-Gates."""
    bars = []
    for i in range(28):
        bars.append(_bar(100 - 35 * (i / 27), volume=1_050_000))
    for i in range(26):
        bars.append(_bar(65 + 2.0 * abs((i - 13) / 13), volume=820_000))
    for i in range(36):
        bars.append(_bar(67 + 32.5 * (i / 35), volume=1_150_000))
    for close in handle_closes:
        bars.append(_bar(close, volume=650_000))
    bars.append(_bar(101.7, volume=2_400_000, high=102.717, low=99.8))
    return bars


def test_m3_structural_stop_wider_than_10pct_rejected():
    # Tiefer Handle (Low ~88) => struktureller Stop ~13% unterm Entry.
    deep_handle = [97.0, 94.0, 91.0, 89.0, 89.5, 90.5, 91.5, 92.5, 93.5]
    assert api._detect_cup_handle_breakout(
        _deep_cup_bars(deep_handle), current_price=101.7
    ) is None
    # Kontrolle: identischer Cup mit flachem Lehrbuch-Handle (Stop ~8.7%) matcht —
    # beweist, dass oben der Stop-Cap verwirft und nicht die Cup-Geometrie.
    control = api._detect_cup_handle_breakout(
        _deep_cup_bars(_TEXTBOOK_HANDLE), current_price=101.7
    )
    assert control is not None
    assert (control["entry"] - control["stop_loss"]) / control["entry"] * 100 <= 10.0


# ---------------------------------------------------------------------------
# M-4 — Grade-Spreizung
# ---------------------------------------------------------------------------

def test_m4_detector_score_calibrates_medium_vs_elite():
    elite = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)
    medium = api._detect_cup_handle_breakout(
        _cup_handle_bars(last_close=104.1, last_volume=1_800_000, handle_vol=1_050_000),
        current_price=104.1,
    )

    assert elite is not None and medium is not None
    assert 90 <= elite["score"] <= 100
    assert 80 <= medium["score"] < 90
    assert medium["score_components"]["breakout_volume"] < elite["score_components"]["breakout_volume"]
    assert medium["handle_volume_contracts"] is False


def test_m4_grade_spread_a_for_85_s_for_92(monkeypatch):
    _mock_mail_env(monkeypatch, allowed=False)
    # Kalibrierter Pattern-Score der Fixture ist 95 => final spreizt echte
    # A-/S-Qualitaet statt alles automatisch zu S zu machen.
    row_a = api._apply_cup_handle_strategy_filter(
        _mk_candidate(base_score=25), {"min_dollar_volume": 2_000_000}
    )
    assert row_a is not None and row_a["score"] == 81 and row_a["grade"] == "A"
    row_s = api._apply_cup_handle_strategy_filter(
        _mk_candidate(base_score=82), {"min_dollar_volume": 2_000_000}
    )
    assert row_s is not None and row_s["score"] == 92 and row_s["grade"] == "S"
    assert row_a["grade"] in {"S", "A"} and row_s["grade"] in {"S", "A"}  # Bestands-Kompatibilitaet


# ---------------------------------------------------------------------------
# M-5 — Ehrliche Labels
# ---------------------------------------------------------------------------

def test_m5_labels_no_5m_trigger_promise(monkeypatch):
    setup = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)
    assert setup is not None
    assert setup["confirmation_timeframe"] == "daily_close+5m_fade_check"

    _mock_mail_env(monkeypatch, allowed=False)
    row = api._apply_cup_handle_strategy_filter(_mk_candidate(), {"min_dollar_volume": 2_000_000})
    assert row is not None
    assert row["confirmation_timeframe"] == "daily_close+5m_fade_check"
    assert "5m-Fade-Check bestanden (kein Gegenvolumen)" in row["scanner_note"]
    assert "fresh 5m execution trigger" not in row["scanner_note"]

    strat = api.STRATEGIES["Cup and Handle Breakout"]
    assert strat["confirmation_timeframe"] == "daily_close+5m_fade_check"
    assert "cup_handle_timeframe" not in strat  # AUDIT N-2: ungelesener Parameter entfernt


# ---------------------------------------------------------------------------
# N-1 / N-4 — Vortag-Filter geweitet, data_gaps-Flag
# ---------------------------------------------------------------------------

def test_n1_vortag_filter_widened_for_cup_handle():
    filters = api.STRATEGIES["Cup and Handle Breakout"]["filters"]
    assert tuple(filters["Vortag %"]) == (-3.0, 100.0)


def test_n4_data_gaps_flag_set_when_bars_removed():
    bars = _cup_handle_bars()
    for idx in (35, 40, 45):  # 3 von 100 Bars (3%) unbrauchbar
        bars[idx]["high"] = float("nan")
        bars[idx]["low"] = float("nan")
    setup = api._detect_cup_handle_breakout(bars, current_price=101.7)
    assert setup is not None and setup["data_gaps"] is True
    clean = api._detect_cup_handle_breakout(_cup_handle_bars(), current_price=101.7)
    assert clean is not None and clean["data_gaps"] is False
