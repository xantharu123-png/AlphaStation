# -*- coding: utf-8 -*-
"""Regressionstests fuer die Cup&Handle-Audit-Fixes (AUDIT_CUP_HANDLE_2026-06-10).

K-1  Anti-Fenster-Shopping (globales Pre-Breakout-Hoch + pivot-treue Bestauswahl)
K-2a Mail-Gate-Suppression fuer INTRADAY_UNCONFIRMED/BEOBACHTEN/WAIT_FOR_*
K-2b Daily-Close-Bestaetigung bleibt Watch-only; keine Afterhours-Entry-Mail
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

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import api
from modules import cup_handle_watch_queue as cup_watch_queue
from modules.regime_filter import update_state as update_locked_json_state


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
    monkeypatch.setattr(api, "_upsert_cup_handle_watch", lambda *a, **k: True)


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
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda *a, **k: ({"CUPX"}, "unit_test"),
    )
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    monkeypatch.setattr(api, "_email_dedupe_mark", lambda *a, **k: None)
    monkeypatch.setattr(api, "_email_dedupe_claim", lambda *a, **k: True)
    monkeypatch.setattr(api, "_email_dedupe_release", lambda *a, **k: True)
    monkeypatch.setattr(api, "_record_email_event", lambda *a, **k: None)
    monkeypatch.setattr(api, "_safe_record_alert_signals", lambda *a, **k: None)
    monkeypatch.setattr(api, "_upsert_cup_handle_watch", lambda *a, **k: True)
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
    assert row["entry_status"] == "DAILY_CLOSE_CONFIRMED_WATCH_ONLY"
    assert row["daily_close_confirmed"] is True
    return row


def test_k2b_afterhours_dailyclose_is_fail_closed_before_revalidation(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=False)  # Session ZU (Afterhours)
    row = _confirmed_fresh_row(monkeypatch, _today_et_str())
    validations = []
    tracked = []
    events = []
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
    monkeypatch.setattr(
        api,
        "_record_email_event",
        lambda subject, status, reason="": events.append((subject, status, reason)),
    )

    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    assert validations == []
    assert sent == []
    assert tracked == []
    assert any(
        status == "skipped"
        and reason == "daily_close_confirmed_watch_only_no_afterhours_entry"
        for _, status, reason in events
    )


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


def _next_session_5m_trigger_bars(level=101.2):
    # Keep the fixture causal without replacing the process-wide ``time.time``
    # function (the Polygon limiter shares that module and would otherwise
    # retain a synthetic future timestamp across tests).
    base_ts = api.time.time() - 900.0
    return [
        {
            "open": level * 0.998,
            "high": level * 1.000,
            "low": level * 0.995,
            "close": level * 0.997,
            "volume": 100_000,
            "timestamp": base_ts,
        },
        {
            "open": level * 0.999,
            "high": level * 1.004,
            "low": level * 0.998,
            "close": level * 1.003,
            "volume": 180_000,
            "timestamp": base_ts + 300,
        },
        {
            "open": level * 1.002,
            "high": level * 1.006,
            "low": level * 1.001,
            "close": level * 1.005,
            "volume": 160_000,
            "timestamp": base_ts + 600,
        },
    ]


def _patch_next_session_dates(monkeypatch):
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: "2026-08-31")
    monkeypatch.setattr(
        api,
        "_previous_us_exchange_trading_date_str",
        lambda _day: "2026-08-28",
    )
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")


def test_k2c_previous_close_promotes_only_after_fresh_next_session_trigger(monkeypatch):
    _mock_session(monkeypatch, allowed=True)
    _patch_next_session_dates(monkeypatch)
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *_args, **_kwargs: _next_session_5m_trigger_bars(),
    )
    bars = _cup_handle_bars(last_bar_date="2026-08-28")

    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=bars),
        {"min_dollar_volume": 2_000_000},
    )

    assert row is not None
    assert row["daily_close_confirmed"] is True
    assert row["daily_close_confirmation_date"] == "2026-08-28"
    assert row["entry_status"] == "NEXT_SESSION_TRIGGER_CONFIRMED"
    assert row["trade_signal"] == "JETZT_TRADEN"
    assert row["trade_action"] == "LONG_NOW"
    assert row["alertable_long"] is True
    assert row["next_session_trigger_type"] == "fresh_5m_cross"
    assert "completed_5m" in row["scan_price_source"]
    assert row["scan_price_observed_at"]
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda *a, **k: ({"CUPX"}, "unit_test"),
    )
    monkeypatch.setattr(
        api,
        "_stock_alert_asset_exclusion_reason",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(api, "_email_dedupe_remaining", lambda *a, **k: 0)
    state = api._classify_alert_candidate("stock_strategy", row)
    assert state["alertable_now"] is True
    assert state["suppression_reasons"] == []


def test_k2c_previous_close_without_fresh_trigger_stays_watch_only(monkeypatch):
    _mock_session(monkeypatch, allowed=True)
    _patch_next_session_dates(monkeypatch)
    waiting_bars = _next_session_5m_trigger_bars()
    for bar in waiting_bars:
        bar["open"] = 100.5
        bar["high"] = 100.8
        bar["low"] = 100.2
        bar["close"] = 100.6
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *_args, **_kwargs: waiting_bars,
    )

    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-28")),
        {"min_dollar_volume": 2_000_000},
    )

    assert row is not None
    assert row["entry_status"] == "NEXT_SESSION_TRIGGER_WAIT"
    assert row["trade_signal"] == "BEOBACHTEN"
    assert row["trade_action"] == "WAIT_FOR_5M_TRIGGER"
    assert row["alertable_long"] is False
    state = api._classify_alert_candidate("stock_strategy", row)
    assert state["alertable_now"] is False
    assert "intraday_unconfirmed_pattern" in state["suppression_reasons"]


def test_k2c_waiting_next_session_trigger_never_revalidates_or_sends(monkeypatch):
    sent = _mock_mail_env(monkeypatch, allowed=True)
    _patch_next_session_dates(monkeypatch)
    waiting_bars = _next_session_5m_trigger_bars()
    for bar in waiting_bars:
        bar.update({"open": 100.5, "high": 100.8, "low": 100.2, "close": 100.6})
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *_args, **_kwargs: waiting_bars,
    )
    snapshot_calls = []
    path_calls = []
    tracked = []
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *_args, **_kwargs: snapshot_calls.append((_args, _kwargs))
        or {"ok": False, "reason": "must_not_run"},
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *_args, **_kwargs: path_calls.append((_args, _kwargs))
        or {"ok": False, "reason": "must_not_run"},
    )
    monkeypatch.setattr(
        api,
        "_safe_record_alert_signals",
        lambda *args, **kwargs: tracked.append((args, kwargs)),
    )

    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-28")),
        {"min_dollar_volume": 2_000_000},
    )
    assert row is not None
    assert row["entry_status"] == "NEXT_SESSION_TRIGGER_WAIT"

    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")

    assert snapshot_calls == []
    assert path_calls == []
    assert sent == []
    assert tracked == []


def test_k2c_old_completed_5m_trigger_is_not_reused_hours_later(monkeypatch):
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")
    bars = _next_session_5m_trigger_bars()

    state = api._cup_handle_next_session_trigger_state(
        "CUPX",
        101.2,
        bars=bars,
        now_ts=bars[-1]["timestamp"] + 300.0 + api._MAIL_TRIGGER_MAX_AGE_SEC + 1.0,
    )

    assert state == {
        "confirmed": False,
        "reason": "cup_next_session_completed_5m_stale",
    }


def test_k2c_prior_session_cross_cannot_be_revived_by_one_fresh_current_bar(
    monkeypatch,
):
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")
    level = 100.0
    friday_open = datetime(2026, 8, 28, 13, 30, tzinfo=timezone.utc).timestamp()
    monday_bar = datetime(2026, 8, 31, 13, 55, tzinfo=timezone.utc).timestamp()
    bars = []
    for idx in range(11):
        close = 99.7 if idx < 8 else 100.6
        bars.append({
            "open": close - 0.1,
            "high": close + 0.3,
            "low": close - 0.3,
            "close": close,
            "volume": 100_000 + idx,
            "timestamp": friday_open + idx * 300,
        })
    bars.append({
        "open": 100.6,
        "high": 101.1,
        "low": 100.5,
        "close": 101.0,
        "volume": 200_000,
        "timestamp": monday_bar,
    })

    state = api._cup_handle_next_session_trigger_state(
        "CUPX",
        level,
        bars=bars,
        now_ts=monday_bar + 310.0,
    )

    assert state == {
        "confirmed": False,
        "reason": "cup_next_session_completed_5m_missing",
    }


def test_k2c_two_current_session_opening_bars_confirm_opening_hold(monkeypatch):
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")
    level = 100.0
    monday_open = datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc).timestamp()
    bars = [
        {
            "open": 100.8,
            "high": 101.3,
            "low": 100.7,
            "close": 101.0,
            "volume": 150_000,
            "timestamp": monday_open,
        },
        {
            "open": 100.9,
            "high": 101.4,
            "low": 100.8,
            "close": 101.2,
            "volume": 170_000,
            "timestamp": monday_open + 300,
        },
    ]

    state = api._cup_handle_next_session_trigger_state(
        "CUPX",
        level,
        bars=bars,
        now_ts=monday_open + 610.0,
    )

    assert state["confirmed"] is True
    assert state["trigger_type"] == "opening_5m_hold"
    assert state["trigger_observed_ts"] == monday_open + 600.0


@pytest.mark.parametrize("trigger_kind", ["cross", "retest", "opening_hold"])
def test_k2c_old_actual_trigger_is_not_revived_by_fresh_hold_bar(
    monkeypatch, trigger_kind
):
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")
    level = 100.0
    market_open = datetime(2026, 8, 31, 13, 30, tzinfo=timezone.utc).timestamp()

    def _bar(idx, *, open_price, high, low, close):
        return {
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": 100_000 + idx,
            "timestamp": market_open + idx * 300,
        }

    if trigger_kind == "cross":
        bars = [
            _bar(0, open_price=99.6, high=99.8, low=99.4, close=99.5),
            _bar(1, open_price=100.0, high=100.5, low=99.9, close=100.3),
        ]
        for idx in range(2, 12):
            bars.append(
                _bar(
                    idx,
                    open_price=100.7,
                    high=100.9,
                    low=100.6,
                    close=100.8,
                )
            )
    elif trigger_kind == "retest":
        bars = [
            _bar(0, open_price=100.3, high=100.5, low=100.0, close=100.2),
        ]
        for idx in range(1, 5):
            bars.append(
                _bar(
                    idx,
                    open_price=100.7,
                    high=100.9,
                    low=100.6,
                    close=100.8,
                )
            )
    else:
        bars = [
            _bar(0, open_price=100.8, high=101.2, low=100.7, close=101.0),
            _bar(1, open_price=100.9, high=101.3, low=100.8, close=101.1),
        ]
        for idx in range(2, 6):
            bars.append(
                _bar(
                    idx,
                    open_price=101.0,
                    high=101.3,
                    low=100.8,
                    close=101.1,
                )
            )

    state = api._cup_handle_next_session_trigger_state(
        "CUPX",
        level,
        bars=bars,
        now_ts=bars[-1]["timestamp"] + 310.0,
    )

    assert state == {
        "confirmed": False,
        "reason": "cup_next_session_trigger_stale",
    }


def test_k2c_next_session_trigger_runs_single_wire_and_tracker_intent(monkeypatch):
    _mock_mail_env(monkeypatch, allowed=True)
    _patch_next_session_dates(monkeypatch)
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *_args, **_kwargs: _next_session_5m_trigger_bars(),
    )
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)

    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-28")),
        {"min_dollar_volume": 2_000_000},
    )
    assert row is not None
    assert row["entry_status"] == "NEXT_SESSION_TRIGGER_CONFIRMED"

    snapshot_calls = []
    path_calls = []
    deliveries = []
    dedupe_marks = []

    levels = api._alert_trade_levels(row)
    planned_entry = float(levels["entry"])
    stop = float(levels["stop"])
    tp1 = float(levels["tp1"])
    live_ask = planned_entry * 1.001
    live_bid = live_ask * 0.999
    scan_ts = api._stock_market_timestamp_seconds(row["scan_price_observed_at"])
    assert scan_ts is not None
    q1_ts = max(api.time.time(), scan_ts) + 0.1
    q2_ts = q1_ts + 0.1
    snapshots = iter(
        [
            {
                "ok": True,
                "bid": live_bid,
                "ask": live_ask,
                "observed_ts": q1_ts,
                "receipt_ts": q1_ts + 0.05,
                "last_trade_ts": q1_ts - 0.1,
            },
            {
                "ok": True,
                "bid": live_bid,
                "ask": live_ask,
                "observed_ts": q2_ts,
                "receipt_ts": q2_ts + 0.05,
                # No trade advanced beyond the already verified Q1 watermark,
                # so the bounded handshake completes with Q2.
                "last_trade_ts": q1_ts,
            },
        ]
    )

    def _snapshot(ticker, **kwargs):
        snapshot_calls.append((ticker, kwargs.get("now_ts")))
        return next(snapshots)

    def _path(ticker, observed_ts, **kwargs):
        path_calls.append(
            (
                ticker,
                observed_ts,
                kwargs.get("now_ts"),
                kwargs.get("last_trade_ts"),
            )
        )
        assert observed_ts == scan_ts
        assert kwargs["now_ts"] == q1_ts
        assert kwargs["last_trade_ts"] == q1_ts - 0.1
        low = max(stop + 0.01, live_bid - 0.05)
        high = min(tp1 - 0.01, live_ask + 0.05)
        assert stop < low <= high < tp1
        return {
            "ok": True,
            "bars": [
                {
                    "timestamp": scan_ts,
                    "high": high,
                    "low": low,
                }
            ],
            "source": "unit_completed_market_path",
            "first_timestamp": scan_ts,
            "last_timestamp": scan_ts,
            "coverage_verified": True,
            "coverage_start_timestamp": scan_ts,
            "coverage_end_timestamp": q1_ts,
        }

    def _send(subject, body, **kwargs):
        deliveries.append({"subject": subject, "body": body, "kwargs": kwargs})
        return True

    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", _snapshot)
    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)
    monkeypatch.setattr(api, "_send_email_alert", _send)
    monkeypatch.setattr(
        api,
        "_email_dedupe_mark",
        lambda key, **kwargs: dedupe_marks.append(key),
    )

    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")

    assert len(snapshot_calls) == 2
    assert [ticker for ticker, _ in snapshot_calls] == ["CUPX", "CUPX"]
    assert len(path_calls) == 1
    assert len(deliveries) == 1
    delivery = deliveries[0]
    assert "Einzelversand CUPX" in delivery["subject"]
    assert delivery["kwargs"]["tracking_scanner"] == "stock_strategy"
    assert delivery["kwargs"]["tracking_scope"] == "entry"
    assert len(delivery["kwargs"]["tracking_rows"]) == 1
    tracked = delivery["kwargs"]["tracking_rows"][0]
    assert tracked["ticker"] == "CUPX"
    assert tracked["entry_status"] == "NEXT_SESSION_TRIGGER_CONFIRMED"
    assert tracked["price_source"] == api._STOCK_FINAL_PRICE_SOURCE
    assert tracked["final_quote_handshake_complete"] is True
    assert tracked["final_market_path_source"] == "unit_completed_market_path"
    assert tracked["final_market_path_bars"] == 1
    assert tracked["final_market_path_round_count"] == 1
    assert len(dedupe_marks) == 1
    assert dedupe_marks == delivery["kwargs"]["delivery_dedupe_keys"]


def test_k2d_persisted_wait_queue_catches_trigger_between_full_sweeps_once(
    monkeypatch, tmp_path
):
    _mock_mail_env(monkeypatch, allowed=False)
    target_date = "2026-08-31"
    confirmation_date = "2026-08-28"
    session_state = {
        "allowed": False,
        "session": "CLOSED",
        "reason": "unit Friday close",
    }
    monkeypatch.setattr(
        api, "_stock_trade_email_status", lambda *a, **k: dict(session_state)
    )
    monkeypatch.setattr(
        api, "_current_us_market_date_str", lambda: confirmation_date
    )
    monkeypatch.setattr(
        api,
        "_next_us_exchange_trading_date_str",
        lambda _day: target_date,
    )
    monkeypatch.setattr(
        api,
        "_previous_us_exchange_trading_date_str",
        lambda _day: confirmation_date,
    )
    monkeypatch.setattr(api, "_stock_quote_session_at", lambda _ts: "US_REGULAR")
    queue_path = tmp_path / "cup-watch.json"
    monkeypatch.setattr(api, "_CUP_HANDLE_WATCH_QUEUE_PATH", queue_path)
    monkeypatch.setattr(api, "_upsert_cup_handle_watch", cup_watch_queue.upsert_watch)
    monkeypatch.setattr(
        api, "_claim_cup_handle_watches", cup_watch_queue.claim_for_session
    )
    monkeypatch.setattr(
        api, "_finish_cup_handle_watch_claim", cup_watch_queue.finish_claim
    )
    monkeypatch.setattr(
        api, "_prune_cup_handle_watches", cup_watch_queue.prune_for_session
    )
    monkeypatch.setattr(api, "_regime_mail_decision", lambda *a, **k: None)
    monkeypatch.setattr(api, "_has_open_equivalent_trade_safe", lambda *a, **k: False)

    # Friday's full sweep persists the completed daily pattern before Monday;
    # Monday therefore needs no full strategy sweep before this monitor.
    from zoneinfo import ZoneInfo

    monitor_now = datetime(
        2026, 8, 31, 9, 40, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    trigger_bars = _next_session_5m_trigger_bars()
    base_ts = monitor_now - 900.0
    for index, bar in enumerate(trigger_bars):
        bar["timestamp"] = base_ts + index * 300.0
    active_5m_bars = {"value": trigger_bars}
    monkeypatch.setattr(
        api,
        "_fetch_recent_stock_5m_bars",
        lambda *_args, **_kwargs: active_5m_bars["value"],
    )
    wait_row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(
            bars=_cup_handle_bars(last_bar_date=confirmation_date)
        ),
        {"min_dollar_volume": 2_000_000},
    )
    assert wait_row is not None
    assert wait_row["entry_status"] == "DAILY_CLOSE_CONFIRMED_WATCH_ONLY"
    persisted = cup_watch_queue.queue_snapshot(path=queue_path)
    assert len(persisted["items"]) == 1
    persisted_item = next(iter(persisted["items"].values()))
    assert persisted_item["ticker"] == "CUPX"
    assert "_daily_bars" not in persisted_item["row"]
    assert set(persisted_item["row"]) <= (
        api._CUP_HANDLE_WATCH_ROW_FIELDS | {"trade_setup"}
    )

    # Monday's fresh trigger arrives without another full strategy sweep.
    session_state.update({
        "allowed": True,
        "session": "US_REGULAR",
        "reason": "unit Monday open",
    })
    trigger_observed_ts = monitor_now
    q1_ts = trigger_observed_ts + 0.1
    q2_ts = q1_ts + 0.1
    levels = api._alert_trade_levels(wait_row)
    live_ask = float(levels["entry"]) * 1.001
    live_bid = live_ask * 0.999
    snapshots = iter([
        {
            "ok": True,
            "bid": live_bid,
            "ask": live_ask,
            "observed_ts": q1_ts,
            "receipt_ts": q1_ts + 0.05,
            "last_trade_ts": q1_ts - 0.1,
        },
        {
            "ok": True,
            "bid": live_bid,
            "ask": live_ask,
            "observed_ts": q2_ts,
            "receipt_ts": q2_ts + 0.05,
            "last_trade_ts": q1_ts,
        },
    ])
    snapshot_calls = []
    path_calls = []

    def _snapshot(ticker, **kwargs):
        snapshot_calls.append(ticker)
        return next(snapshots)

    def _path(ticker, observed_ts, **kwargs):
        path_calls.append(ticker)
        return {
            "ok": True,
            "bars": [{
                "timestamp": observed_ts,
                "high": min(float(levels["tp1"]) - 0.01, live_ask + 0.05),
                "low": max(float(levels["stop"]) + 0.01, live_bid - 0.05),
            }],
            "source": "unit_queue_market_path",
            "first_timestamp": observed_ts,
            "last_timestamp": observed_ts,
            "coverage_verified": True,
            "coverage_start_timestamp": observed_ts,
            "coverage_end_timestamp": kwargs["now_ts"],
        }

    deliveries = []
    marks = []
    events = []
    monkeypatch.setattr(api, "_fetch_stock_revalidation_snapshot", _snapshot)
    monkeypatch.setattr(api, "_fetch_stock_revalidation_market_path", _path)
    monkeypatch.setattr(
        api,
        "_send_email_alert",
        lambda subject, body, **kwargs: deliveries.append(
            {"subject": subject, "body": body, "kwargs": kwargs}
        ) or True,
    )
    monkeypatch.setattr(
        api, "_email_dedupe_mark", lambda key, **kwargs: marks.append(key)
    )
    monkeypatch.setattr(
        api,
        "_record_email_event",
        lambda subject, status, reason="": events.append((subject, status, reason)),
    )
    monkeypatch.setattr(api.time, "time", lambda: monitor_now + 0.3)

    first = api._cup_handle_watch_monitor_wrapper(now_ts=monitor_now)
    second = api._cup_handle_watch_monitor_wrapper(now_ts=monitor_now + 1.0)

    assert first == {"claimed": 1, "triggered": 1, "completed": 1}, (
        deliveries,
        snapshot_calls,
        path_calls,
        marks,
        events,
        cup_watch_queue.queue_snapshot(path=queue_path),
    )
    assert second == {"claimed": 0, "triggered": 0, "completed": 0}
    assert snapshot_calls == ["CUPX", "CUPX"]
    assert path_calls == ["CUPX"]
    assert len(deliveries) == 1
    tracking = deliveries[0]["kwargs"]
    assert tracking["tracking_scanner"] == "stock_strategy"
    assert tracking["tracking_scope"] == "entry"
    assert tracking["tracking_rows"][0]["entry_status"] == (
        "NEXT_SESSION_TRIGGER_CONFIRMED"
    )
    assert tracking["tracking_rows"][0]["final_quote_handshake_complete"] is True
    assert len(marks) == 1
    assert cup_watch_queue.queue_snapshot(path=queue_path)["items"] == {}


def test_k2d_queue_preserves_future_session_and_concurrent_refresh(tmp_path):
    queue_path = tmp_path / "cup-watch.json"
    queue_now = api.time.time()
    base = {
        "id": "CUPX|2026-08-28|2026-08-31",
        "ticker": "CUPX",
        "confirmation_date": "2026-08-28",
        "target_session_date": "2026-08-31",
        "breakout_level": 101.2,
        "created_at": 1.0,
        "updated_at": 1.0,
        "expires_at": queue_now + 7 * 86400.0,
        "row": {"ticker": "CUPX", "score": 90},
    }
    assert cup_watch_queue.upsert_watch(base, path=queue_path)
    assert cup_watch_queue.prune_for_session(
        "2026-08-28", now_ts=queue_now, path=queue_path
    )
    assert len(cup_watch_queue.queue_snapshot(path=queue_path)["items"]) == 1
    assert update_locked_json_state(
        lambda state: state.setdefault("items", {}).update({
            "CORRUPT": {
                "ticker": "BAD",
                "confirmation_date": "2026-08-28",
                "target_session_date": "2026-08-31",
                "breakout_level": 50.0,
                "expires_at": queue_now + 86400.0,
                "lease_until": "not-a-number",
                "generation": "not-a-number",
                "row": {},
            }
        }),
        queue_path,
    )
    claims = cup_watch_queue.claim_for_session(
        "2026-08-31", now_ts=queue_now + 100.0, path=queue_path
    )
    assert len(claims) == 1
    old = claims[0]
    refreshed = dict(base)
    refreshed["updated_at"] = 2.0
    refreshed["row"] = {"ticker": "CUPX", "score": 95}
    assert cup_watch_queue.upsert_watch(refreshed, path=queue_path)
    assert cup_watch_queue.finish_claim(
        old["id"],
        old["lease_owner"],
        remove=True,
        generation=old["generation"],
        path=queue_path,
    )
    saved = cup_watch_queue.queue_snapshot(path=queue_path)["items"][old["id"]]
    assert saved["row"]["score"] == 95
    assert "lease_owner" not in saved
    assert cup_watch_queue.prune_for_session(
        "2026-09-01", now_ts=queue_now + 200.0, path=queue_path
    )
    assert cup_watch_queue.queue_snapshot(path=queue_path)["items"] == {}


def test_k2d_hourly_discovery_owner_queues_only_immediate_prior_close(monkeypatch):
    assert "Cup and Handle Breakout" in api._AUTO_STOCK_ALERT_STRATEGIES
    assert api._scan_status["strategy_scan"]["interval_min"] == 60
    assert api._scan_status["cup_handle_watch"]["interval_min"] == 5
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *a, **k: {"allowed": False, "session": "CLOSED"},
    )
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: "2026-08-28")
    monkeypatch.setattr(
        api, "_next_us_exchange_trading_date_str", lambda _day: "2026-08-31"
    )
    monkeypatch.setattr(
        api,
        "_previous_us_exchange_trading_date_str",
        lambda _day: "2026-08-28",
    )
    monkeypatch.setattr(
        api,
        "_fetch_long_latest_intraday_state",
        lambda _ticker: {
            "latest_bar_change_pct": 0.25,
            "latest_bar_close_pos": 0.85,
        },
    )
    queued = []
    monkeypatch.setattr(
        api,
        "_upsert_cup_handle_watch",
        lambda payload, **kwargs: queued.append(payload) or True,
    )

    fresh = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-28")),
        {"min_dollar_volume": 2_000_000},
    )
    stale = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-27")),
        {"min_dollar_volume": 2_000_000},
    )

    assert fresh is not None and stale is not None
    assert len(queued) == 1
    assert queued[0]["confirmation_date"] == "2026-08-28"
    assert queued[0]["target_session_date"] == "2026-08-31"


def test_k2d_closed_session_prunes_without_provider_or_wire(monkeypatch, tmp_path):
    queue_path = tmp_path / "cup-watch.json"
    monkeypatch.setattr(api, "_CUP_HANDLE_WATCH_QUEUE_PATH", queue_path)
    target_date = _today_et_str()
    expiry = api._cup_handle_watch_expiry_ts(target_date)
    assert expiry is not None
    assert cup_watch_queue.upsert_watch(
        {
            "id": f"CUPX|2026-08-28|{target_date}",
            "ticker": "CUPX",
            "confirmation_date": "2026-08-28",
            "target_session_date": target_date,
            "breakout_level": 101.2,
            "created_at": 1.0,
            "updated_at": 1.0,
            "expires_at": expiry,
            "row": {"ticker": "CUPX"},
        },
        path=queue_path,
    )
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *a, **k: {"allowed": False, "session": "CLOSED"},
    )
    provider_calls = []
    wires = []
    monkeypatch.setattr(
        api,
        "_cup_handle_next_session_trigger_state",
        lambda *a, **k: provider_calls.append(True) or {"confirmed": True},
    )
    monkeypatch.setattr(
        api, "_send_email_alert", lambda *a, **k: wires.append(True) or True
    )
    result = api._cup_handle_watch_monitor_wrapper(now_ts=api.time.time())
    assert result == {"claimed": 0, "triggered": 0, "completed": 0}
    assert provider_calls == []
    assert wires == []


def test_k2d_monday_premarket_restart_rebuilds_queue_without_trigger_or_mail(
    monkeypatch
):
    queued = []
    trigger_calls = []
    snapshot_calls = []
    path_calls = []
    wires = []
    tracked = []
    revalidations = []
    email_events = []
    suppressions = []
    monkeypatch.setattr(
        api,
        "_stock_trade_email_status",
        lambda *a, **k: {
            "allowed": False,
            "session": "PREMARKET",
            "reason": "unit Monday 08:00 ET",
        },
    )
    monkeypatch.setattr(api, "_current_us_market_date_str", lambda: "2026-08-31")
    monkeypatch.setattr(
        api,
        "_previous_us_exchange_trading_date_str",
        lambda _day: "2026-08-28",
    )
    monkeypatch.setattr(
        api,
        "_fetch_long_latest_intraday_state",
        lambda _ticker: {
            "latest_bar_change_pct": 0.25,
            "latest_bar_close_pos": 0.85,
        },
    )
    monkeypatch.setattr(
        api,
        "_upsert_cup_handle_watch",
        lambda payload, **kwargs: queued.append(payload) or True,
    )
    monkeypatch.setattr(
        api,
        "_cup_handle_next_session_trigger_state",
        lambda *a, **k: trigger_calls.append(True) or {"confirmed": True},
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_snapshot",
        lambda *a, **k: snapshot_calls.append(True) or {"ok": False},
    )
    monkeypatch.setattr(
        api,
        "_fetch_stock_revalidation_market_path",
        lambda *a, **k: path_calls.append(True) or {"ok": False},
    )
    monkeypatch.setattr(
        api, "_send_email_alert", lambda *a, **k: wires.append(True) or True
    )
    monkeypatch.setattr(
        api,
        "_safe_record_alert_signals",
        lambda *a, **k: tracked.append(True),
    )
    monkeypatch.setattr(
        api,
        "_revalidate_stock_strategy_mail_candidate",
        lambda *a, **k: revalidations.append(True) or {"valid": True},
    )
    monkeypatch.setattr(
        api,
        "_load_common_stock_universe",
        lambda *a, **k: ({"CUPX"}, "unit"),
    )
    monkeypatch.setattr(api, "_premarket_window_active", lambda *a, **k: True)
    monkeypatch.setattr(
        api, "_record_email_event", lambda *args, **kwargs: email_events.append(args)
    )
    monkeypatch.setattr(
        api,
        "_record_suppression_counts",
        lambda scanner, reasons: suppressions.append((scanner, reasons)),
    )
    monkeypatch.setattr(api, "_prune_cup_handle_watches", lambda *a, **k: True)

    row = api._apply_cup_handle_strategy_filter(
        _mk_candidate(bars=_cup_handle_bars(last_bar_date="2026-08-28")),
        {"min_dollar_volume": 2_000_000},
    )
    assert row is not None
    assert row["entry_status"] == "DAILY_CLOSE_CONFIRMED_WATCH_ONLY"
    assert row["trade_signal"] == "BEOBACHTEN"
    assert len(queued) == 1
    assert queued[0]["confirmation_date"] == "2026-08-28"
    assert queued[0]["target_session_date"] == "2026-08-31"

    # Adversarially make this look like an excellent PM-radar row. The
    # code-owned watch status must still take precedence until regular-session
    # completed-5m promotion.
    row.update(
        {
            "Premarket": True,
            "premarket": True,
            "Grade": "S",
            "grade": "S",
            "Score": 99.0,
            "score": 99.0,
            "RVOL": 4.0,
            "PM_Dollar_Volume": 25_000_000.0,
            "Premarket_Dollar_Volume": 25_000_000.0,
        }
    )

    api._send_strategy_scan_alerts("Cup and Handle Breakout", [row], "stocks")
    from zoneinfo import ZoneInfo

    premarket_ts = datetime(
        2026, 8, 31, 8, 0, tzinfo=ZoneInfo("America/New_York")
    ).timestamp()
    result = api._cup_handle_watch_monitor_wrapper(now_ts=premarket_ts)

    assert result == {"claimed": 0, "triggered": 0, "completed": 0}
    assert trigger_calls == []
    assert snapshot_calls == []
    assert path_calls == []
    assert wires == []
    assert tracked == []
    assert revalidations == []
    assert any(
        len(event) >= 3
        and event[2] == "daily_close_confirmed_watch_only_no_afterhours_entry"
        for event in email_events
    )
    assert suppressions == [
        (
            "stock_strategy",
            {"daily_close_confirmed_watch_only_no_afterhours_entry": 1},
        )
    ]


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
