#!/usr/bin/env python3
"""Pytest-Suite fuer modules/signal_tracker.py (API-Kontrakt Signal-Tracking).

Komplett offline: Kursdaten kommen aus injizierten Fake-Fetchern, die DB liegt
in einem pytest-tmp-Verzeichnis (modulglobales SIGNAL_DB_PATH wird pro Test
per monkeypatch ueberschrieben — die Funktionen lesen den Pfad pro Aufruf).
"""
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

_DIR = os.path.dirname(os.path.abspath(__file__))
if _DIR not in sys.path:
    sys.path.insert(0, _DIR)

import modules.signal_tracker as st


# ── Helpers ──────────────────────────────────────────────────────────────────
def _base_row(**overrides):
    """Plausible LONG-Alert-Row: Entry 100, Stop 95 (Risk 5), TP1 105, TP2 110."""
    row = {
        "Ticker": "AAPL", "Entry": 100.0, "StopLoss": 95.0,
        "TP1": 105.0, "TP2": 110.0, "Preis": 100.0,
        "price_observed_at": datetime.now(timezone.utc).isoformat(),
        "price_source": "live_quote_test",
        "fill_evidence_verified": True,
        "price_mode": "ask",
        "price_session": "US_REGULAR",
    }
    row.update(overrides)
    return row


def _db_rows(query="SELECT * FROM signals ORDER BY id", params=()):
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(query, params).fetchall()]
    finally:
        conn.close()


def _signal(ticker):
    rows = _db_rows("SELECT * FROM signals WHERE ticker = ? ORDER BY id", (ticker,))
    assert rows, "Signal %s nicht in der DB" % ticker
    return rows[-1]


def _created_date(ticker):
    return datetime.fromisoformat(_signal(ticker)["created_at"]).date()


def _created_market_date(ticker):
    created_at = datetime.fromisoformat(_signal(ticker)["created_at"])
    return created_at.astimezone(st.ZoneInfo("America/New_York")).date()


def _bars_after(ticker, specs):
    """Complete Daily bars in consecutive US-equity sessions after the alert.

    specs: Liste von (high, low, close)- oder (open, high, low, close)-Tupeln.
    """
    cursor = _created_market_date(ticker)
    bars = []
    for spec in specs:
        cursor += timedelta(days=1)
        while not st._is_us_equity_session(cursor):
            cursor += timedelta(days=1)
        if len(spec) == 4:
            open_price, high, low, close = spec
        else:
            high, low, close = spec
            open_price = min(max(100.0, low), high)
        bars.append({
            "date": cursor.isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "interval_complete": True,
        })
    return bars


def _stock_fetcher(bars_by_ticker):
    """Fake-Polygon-Fetcher: liefert vordefinierte Bars (None fuer Unbekannte)."""
    calls = []

    def fetcher(ticker, since_iso_date):
        calls.append((ticker, since_iso_date))
        return bars_by_ticker.get(ticker)

    fetcher.calls = calls
    return fetcher


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    """Frische tmp-DB pro Test (SIGNAL_DB_PATH wird pro Aufruf gelesen)."""
    monkeypatch.setattr(st, "SIGNAL_DB_PATH", str(tmp_path / "signal_tracker_test.sqlite"))
    monkeypatch.setattr(
        st,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(tmp_path / "signal_delivery_acceptance_test.sqlite"),
    )
    return st


# ── record_alert_signals ─────────────────────────────────────────────────────
def test_record_basic_fields_and_status(tracker):
    assert tracker.record_alert_signals(
        "breakout", [_base_row()], mail_channel="stocks_swing"
    ) == 1
    sig = _signal("AAPL")
    assert sig["scanner"] == "breakout"
    assert sig["asset_class"] == "stock"
    assert sig["direction"] == "LONG"
    assert sig["status"] == "OPEN"
    assert sig["entry"] == pytest.approx(100.0)
    assert sig["stop"] == pytest.approx(95.0)
    assert sig["tp1"] == pytest.approx(105.0)
    assert sig["tp2"] == pytest.approx(110.0)
    assert sig["mail_class"] == "trade"
    assert sig["channel"] == "email"
    assert sig["mail_channel"] == "stocks_swing"
    assert sig["eval_fail_count"] == 0
    assert sig["outcome_detail"] == ""
    assert sig["evaluation_horizon_bars"] == 5
    assert sig["created_at"]
    assert tracker.get_signal_count() == 1


def test_record_persists_only_hashed_delivery_recipient_keys(tracker):
    key = "a" * 64
    assert tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="DELIVERY")],
        delivery_recipient_keys=[key, "recipient@example.com", key.upper()],
    ) == 1

    signal = _signal("DELIVERY")
    assert signal["delivery_recipient_keys_json"] == '["' + key + '"]'
    assert "@" not in signal["delivery_recipient_keys_json"]


@pytest.mark.parametrize(
    ("scanner", "strategy", "trade_horizon", "explicit", "expected"),
    [
        ("orb", "Opening Range Breakout", "intraday", None, 1),
        ("stock_strategy", "Momentum Breakout Long", "swing", None, 8),
        ("bi_long", "BI Long", "swing", None, 10),
        ("turtle", "Turtle Breakout", "swing", None, 20),
        ("stock_strategy", "Momentum Breakout Long", "swing", 12, 12),
    ],
)
def test_stock_horizon_is_strategy_specific(
    scanner, strategy, trade_horizon, explicit, expected
):
    assert st._infer_stock_horizon_bars(
        scanner, strategy, trade_horizon, explicit
    ) == expected


def test_record_only_trade_mail_class(tracker):
    assert tracker.record_alert_signals("breakout", [_base_row()], mail_class="watch") == 0
    assert tracker.record_alert_signals("breakout", [_base_row()], mail_class="info") == 0
    assert tracker.record_alert_signals(
        "breakout", [_base_row(Ticker="SHADOW")], mail_class="shadow"
    ) == 1
    assert tracker.get_signal_count() == 1
    assert _signal("SHADOW")["mail_class"] == "shadow"


def test_record_skips_rows_without_required_fields(tracker):
    rows = [
        {"Entry": 100.0, "StopLoss": 95.0},                     # kein Ticker
        {"Ticker": "NOSTOP", "Entry": 100.0},                   # kein Stop
        {"Ticker": "NONUM", "Entry": "n/a", "StopLoss": 95.0},  # Entry nicht numerisch
        _base_row(Ticker="BADGEO", StopLoss=105.0),             # LONG mit Stop > Entry
        _base_row(Ticker="NOTP1", TP1=None),                    # kein erstes Ziel
        _base_row(Ticker="NOTP2", TP2=None),                    # kein zweites Ziel
        _base_row(Ticker="SAMETP", TP2=105.0),                  # TP1 und TP2 identisch
        _base_row(Ticker="WRONGTP", TP1=99.0, TP2=110.0),       # LONG-Ziel unter Entry
        _base_row(Ticker="OK1"),
    ]
    assert tracker.record_alert_signals("breakout", rows) == 1
    assert [r["ticker"] for r in _db_rows()] == ["OK1"]


def test_record_rejects_wrong_short_target_geometry(tracker):
    rows = [
        _base_row(Ticker="SHORTOK", direction="SHORT", StopLoss=105.0, TP1=95.0, TP2=90.0),
        _base_row(Ticker="SHORTUP", direction="SHORT", StopLoss=105.0, TP1=101.0, TP2=90.0),
        _base_row(Ticker="SHORTREV", direction="SHORT", StopLoss=105.0, TP1=90.0, TP2=95.0),
    ]
    assert tracker.record_alert_signals("short_squeeze", rows) == 1
    assert [r["ticker"] for r in _db_rows()] == ["SHORTOK"]


def test_record_tolerant_field_aliases(tracker):
    row = {
        "symbol": "tsla",                # alias + Kleinschreibung
        "direction": "short squeeze",    # 'short' im String -> SHORT
        "entry": "240.50",               # numerischer String
        "stop_loss": 250.0,
        "tp1": 230.0,
        "tp2": 220.0,
        "current_price": 239.8,
        "BI_Grade": "A+",
        "BI_Score": 87,
        "RVOL": 3.4,
    }
    assert tracker.record_alert_signals("short_squeeze", [row]) == 1
    sig = _signal("TSLA")  # Ticker normalisiert auf Grossschreibung
    assert sig["direction"] == "SHORT"
    assert sig["entry"] == pytest.approx(240.5)
    assert sig["stop"] == pytest.approx(250.0)
    assert sig["tp1"] == pytest.approx(230.0)
    assert sig["tp2"] == pytest.approx(220.0)
    assert sig["price_at_alert"] == pytest.approx(239.8)
    assert sig["grade"] == "A+"
    assert sig["score"] == pytest.approx(87.0)
    assert sig["rvol"] == pytest.approx(3.4)


@pytest.mark.parametrize(
    "direction_fields",
    [
        {"Signal_Direction": "SHORT"},
        {"BI_Direction": "SHORT"},
        {"_direction": "short"},
        {"side": "SELL"},
        {"trade_action": "SELL"},
        {"trade_setup": {"direction": "SHORT"}},
    ],
)
def test_record_uses_shared_direction_inference_for_short_aliases(
    tracker, direction_fields
):
    row = _base_row(StopLoss=105.0, TP1=95.0, TP2=90.0)
    row.update(direction_fields)

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    assert _signal("AAPL")["direction"] == "SHORT"


def test_unverified_scalar_price_is_not_an_immediate_fill(tracker):
    row = _base_row(
        Preis=101.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal("AAPL")
    assert signal["price_at_alert"] == pytest.approx(101.0)
    assert signal["entry_filled_at"] is None
    assert signal["entry_fill_price"] is None
    assert signal["fill_evidence_mode"] == "pending_interval"


def test_verified_quote_before_long_entry_waits_for_interval_fill(tracker):
    row = _base_row(
        Ticker="WAITENTRY",
        Preis=99.0,
        price_source="polygon_snapshot_revalidated",
        price_mode="ask",
        price_session="US_REGULAR",
    )

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal("WAITENTRY")
    assert signal["entry_filled_at"] is None
    assert signal["fill_evidence_verified"] == 1
    assert signal["fill_evidence_mode"] == "pending_interval"


@pytest.mark.parametrize(
    ("ticker", "direction", "price", "expected_fill"),
    [
        ("LONGEV", "LONG", 100.0, 100.0),
        ("SHORTEV", "SHORT", 100.0, 100.0),
    ],
)
def test_recent_verified_execution_evidence_can_fill_immediately(
    tracker, ticker, direction, price, expected_fill
):
    row = _base_row(Ticker=ticker, Preis=price)
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    row["price_mode"] = "bid" if direction == "SHORT" else "ask"
    row["price_session"] = "US_REGULAR"
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal(ticker)
    assert signal["entry_filled_at"]
    assert signal["entry_fill_price"] == pytest.approx(expected_fill)
    assert signal["fill_evidence_verified"] == 1
    assert signal["price_mode"] == row["price_mode"]
    assert signal["price_session"] == "US_REGULAR"
    assert signal["fill_evidence_mode"] == "verified_snapshot"


@pytest.mark.parametrize(
    ("source", "expected_fill"),
    [
        ("confirmed_daily_close", None),
        ("premarket_live_ask", 100.0),
    ],
)
def test_immediate_fill_evidence_is_safe_for_daily_close_and_premarket(
    tracker, source, expected_fill
):
    session = "CLOSED" if "daily_close" in source else "PREMARKET"
    row = _base_row(
        Ticker="SESSION",
        Preis=100.0,
        price_source=source,
        price_mode="ask",
        price_session=session,
    )

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal("SESSION")
    if expected_fill is None:
        assert signal["entry_filled_at"] is None
        assert signal["fill_evidence_verified"] == 0
        assert signal["fill_evidence_mode"] == "pending_interval"
    else:
        assert signal["entry_fill_price"] == pytest.approx(expected_fill)
        assert signal["fill_evidence_mode"] == "verified_snapshot"


def test_wrong_quote_side_cannot_be_immediate_fill_evidence(tracker):
    row = _base_row(
        Ticker="WRONGSIDE",
        Preis=100.0,
        price_source="polygon_snapshot_revalidated",
        price_mode="bid",  # LONG execution must use the ask.
        price_session="US_REGULAR",
    )

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal("WRONGSIDE")
    assert signal["entry_filled_at"] is None
    assert signal["fill_evidence_verified"] == 0
    assert signal["fill_evidence_mode"] == "pending_interval"


@pytest.mark.parametrize(
    ("ticker", "overrides"),
    [
        ("NOMODE", {"price_mode": None}),
        ("NOSESSION", {"price_session": None}),
        ("UNKNOWNSESSION", {"price_session": "UNKNOWN"}),
    ],
)
def test_incomplete_execution_contract_cannot_verify_immediate_fill(
    tracker, ticker, overrides
):
    row = _base_row(Ticker=ticker, Preis=100.0, **overrides)

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal(ticker)
    assert signal["entry_filled_at"] is None
    assert signal["fill_evidence_verified"] == 0
    assert signal["fill_evidence_mode"] == "pending_interval"


def test_stale_verified_quote_cannot_fill_immediately(tracker):
    row = _base_row(
        Ticker="STALE",
        Preis=101.0,
        price_observed_at=(datetime.now(timezone.utc) - timedelta(minutes=6)).isoformat(),
    )

    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    signal = _signal("STALE")
    assert signal["entry_filled_at"] is None
    assert signal["fill_evidence_verified"] == 0
    assert signal["fill_evidence_mode"] == "pending_interval"


def test_record_dedupe_open_signal_per_scanner_ticker(tracker):
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 1
    # Derselbe wirtschaftliche Trade bleibt auch scanneruebergreifend einmalig.
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 0
    assert tracker.record_alert_signals("bi_scanner", [_base_row()]) == 0
    assert tracker.has_open_equivalent_signal("BI_SCANNER", _base_row()) is True

    # Ein materiell anderer Plan im selben Ticker darf separat getrackt werden.
    distinct_plan = _base_row(Entry=120.0, Preis=120.0, StopLoss=115.0, TP1=125.0, TP2=130.0)
    assert tracker.record_alert_signals("bi_scanner", [distinct_plan]) == 1
    # Dedupe greift auch innerhalb EINES Batches
    msft = _base_row(Ticker="MSFT")
    assert tracker.record_alert_signals("momo", [msft, msft]) == 1
    # geschlossenes Signal blockiert nicht mehr
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    conn.execute("UPDATE signals SET status = 'STOP_HIT' WHERE ticker = 'AAPL'")
    conn.commit()
    conn.close()
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 1


def test_explicit_setup_key_dedupes_rounded_versions_and_is_persisted(tracker):
    first = _base_row(
        setup_key="stock:AAPL:swing:2026-08-06",
        strategy="Momentum Breakout Long",
        trade_horizon="swing",
    )
    assert tracker.record_alert_signals("stock_strategy", [first]) == 1

    stored = _signal("AAPL")
    assert stored["setup_key"] == "stock:AAPL:swing:2026-08-06"
    assert stored["strategy"] == "Momentum Breakout Long"
    assert stored["trade_horizon"] == "swing"

    rounded = _base_row(
        Entry=100.04,
        Preis=100.04,
        StopLoss=95.04,
        TP1=105.08,
        TP2=110.08,
        setup_key="stock:AAPL:swing:2026-08-06",
        strategy="Momentum Breakout Long",
        trade_horizon="swing",
    )
    assert tracker.record_alert_signals("bi_scanner", [rounded]) == 0
    assert tracker.get_signal_count() == 1


def test_crypto_dedupe_uses_instrument_identity_not_ambiguous_symbol(tracker):
    rows = [
        _base_row(
            Ticker="UP",
            coin_id="superform",
            venue="binance",
            contract_symbol="UPUSDT",
        ),
        _base_row(
            Ticker="UP",
            coin_id="up-token",
            venue="mexc",
            contract_symbol="UP_USDT",
        ),
    ]
    assert tracker.record_alert_signals("crypto_explosion", rows) == 2
    stored = _db_rows()
    assert {row["instrument_id"] for row in stored} == {"superform", "up-token"}
    assert tracker.record_alert_signals("crypto_explosion", [rows[0]]) == 0


def test_record_asset_class_crypto_vs_stock(tracker):
    for scanner in sorted(st.CRYPTO_SCANNERS):
        assert tracker.record_alert_signals(scanner, [_base_row(Ticker="C_" + scanner.upper())]) == 1
        assert _signal("C_" + scanner.upper())["asset_class"] == "crypto"
    tracker.record_alert_signals("breakout", [_base_row(Ticker="NVDA")])
    assert _signal("NVDA")["asset_class"] == "stock"


def test_record_never_raises_and_returns_int(tracker, tmp_path, monkeypatch):
    assert tracker.record_alert_signals("breakout", None) == 0
    assert tracker.record_alert_signals("breakout", [None, 42, "x", {}, []]) == 0
    assert tracker.record_alert_signals("", [_base_row()]) == 0
    # kaputter DB-Pfad (Parent ist eine Datei) -> Warnung statt Exception
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", str(blocker / "sub" / "x.sqlite"))
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 0


# ── evaluate_open_signals: Aktien (Daily-OHLC) ───────────────────────────────
def test_evaluate_stock_long_stop_hit(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    expected_since = _created_market_date("AAPL").isoformat()
    bars = _bars_after("AAPL", [(103.0, 99.0, 102.0), (101.0, 94.5, 96.0)])
    fetcher = _stock_fetcher({"AAPL": bars})
    result = tracker.evaluate_open_signals(stock_daily_fetcher=fetcher)
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal("AAPL")
    assert sig["status"] == "STOP_HIT"
    assert sig["r_realized"] == pytest.approx(-1.0)
    assert sig["outcome_detail"] == ""
    assert sig["stop_hit_at"] == bars[1]["date"]
    assert sig["closed_at"]
    assert sig["last_eval_at"]
    # Fetcher bekam (ticker, Alert-Datum als ISO-String)
    assert fetcher.calls == [("AAPL", expected_since)]


def test_evaluate_stock_long_tp1_then_tp2(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    day1 = (106.0, 99.0, 105.5)  # TP1 beruehrt, kein Stop, kein TP2
    bars1 = _bars_after("AAPL", [day1])
    r1 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    assert r1 == {"evaluated": 1, "closed": 0, "errors": 0}
    sig = _signal("AAPL")
    assert sig["status"] == "OPEN"
    assert sig["tp1_hit_at"] == bars1[0]["date"]
    # Folgelauf: Tag 2 erreicht TP2
    bars2 = _bars_after("AAPL", [day1, (111.0, 104.0, 110.5)])
    r2 = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    assert r2 == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal("AAPL")
    assert sig["status"] == "TP2_HIT"
    assert sig["tp2_hit_at"] == bars2[1]["date"]
    assert sig["tp1_hit_at"] == bars1[0]["date"]      # bleibt vom ersten Lauf
    assert sig["r_realized"] == pytest.approx(2.0)    # (110-100)/(100-95)


def test_evaluate_stock_long_tp1_then_expired(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    specs = [
        (105.5, 99.0, 105.0),   # Tag 1: TP1
        (106.0, 101.0, 103.0),
        (104.0, 100.0, 102.0),
        (103.0, 99.5, 101.0),
        (104.5, 100.5, 104.0),  # Tag 5: Expiry, Close 104
    ]
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": _bars_after("AAPL", specs)})
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal("AAPL")
    assert sig["status"] == "EXPIRED"
    assert sig["outcome_detail"] == "tp1_then_expired"
    assert sig["tp1_hit_at"]
    assert sig["r_realized"] == pytest.approx(0.8)  # (104-100)/5


def test_evaluate_stock_ambiguous_same_day_conservative_stop(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    # TP1 (106 >= 105) UND Stop (94 <= 95) am selben Tag -> konservativ Stop
    bars = _bars_after("AAPL", [(106.0, 94.0, 100.0)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars}))
    sig = _signal("AAPL")
    assert sig["status"] == "STOP_HIT"
    assert sig["outcome_detail"] == "ambiguous_same_day"
    assert sig["r_realized"] == pytest.approx(-1.0)
    assert sig["r_realized_upper"] == pytest.approx(1.0)
    assert not sig["tp1_hit_at"]  # TP wird im Zweifel NICHT gutgeschrieben


@pytest.mark.parametrize(
    ("row", "bar"),
    [
        (
            _base_row(Ticker="LONGAMB", Preis=97.0),
            (97.0, 102.0, 94.0, 99.0),
        ),
        (
            _base_row(
                Ticker="SHORTAMB",
                Direction="SHORT",
                Preis=103.0,
                StopLoss=105.0,
                TP1=95.0,
                TP2=90.0,
            ),
            (103.0, 106.0, 98.0, 101.0),
        ),
    ],
)
def test_stock_entry_and_stop_in_same_bar_is_marked_ambiguous(tracker, row, bar):
    ticker = row["Ticker"]
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    bars = _bars_after(ticker, [bar])
    tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: bars})
    )
    sig = _signal(ticker)
    assert sig["status"] == "STOP_HIT"
    assert sig["outcome_detail"] == "ambiguous_same_day_entry_and_stop"
    assert sig["r_realized"] == pytest.approx(-1.0)
    assert sig["r_realized_upper"] == pytest.approx(-0.2)


@pytest.mark.parametrize(
    ("row", "first_bar", "gap_bar", "expected_r"),
    [
        (
            _base_row(Ticker="LONGGAPTARGET"),
            (99.0, 101.0, 98.0, 100.0),
            (92.0, 111.0, 90.0, 109.0),
            -1.6,
        ),
        (
            _base_row(
                Ticker="SHORTGAPTARGET",
                Direction="SHORT",
                StopLoss=105.0,
                TP1=95.0,
                TP2=90.0,
            ),
            (101.0, 102.0, 99.0, 100.0),
            (108.0, 110.0, 89.0, 91.0),
            -1.6,
        ),
    ],
)
def test_stock_gap_through_stop_is_not_ambiguous_even_if_target_touches_later(
    tracker, row, first_bar, gap_bar, expected_r
):
    ticker = row["Ticker"]
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher(
            {ticker: _bars_after(ticker, [first_bar, gap_bar])}
        )
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal(ticker)
    assert sig["status"] == "STOP_HIT"
    assert sig["outcome_detail"] == "stop_gap_slippage"
    assert sig["r_realized"] == pytest.approx(expected_r)
    assert sig["r_realized_upper"] == pytest.approx(expected_r)


@pytest.mark.parametrize(
    ("row", "first_bar", "gap_bar"),
    [
        (
            _base_row(Ticker="LONGGAP"),
            (99.0, 101.0, 98.0, 100.0),
            (92.0, 94.0, 90.0, 91.0),
        ),
        (
            _base_row(
                Ticker="SHORTGAP",
                Direction="SHORT",
                StopLoss=105.0,
                TP1=95.0,
                TP2=90.0,
            ),
            (101.0, 102.0, 99.0, 100.0),
            (108.0, 110.0, 106.0, 109.0),
        ),
    ],
)
def test_stock_gap_through_stop_uses_open_and_marks_slippage(
    tracker, row, first_bar, gap_bar
):
    ticker = row["Ticker"]
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher(
            {ticker: _bars_after(ticker, [first_bar, gap_bar])}
        )
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal(ticker)
    assert sig["status"] == "STOP_HIT"
    assert sig["r_realized"] == pytest.approx(-1.6)
    assert sig["r_realized_upper"] == pytest.approx(-1.6)
    assert sig["outcome_detail"] == "stop_gap_slippage"


def test_daily_bar_without_open_retries_instead_of_using_close_as_gap_fill(tracker):
    assert tracker.record_alert_signals("stock_strategy", [_base_row(Ticker="NOOPEN")]) == 1
    before = _signal("NOOPEN")
    assert before["entry_fill_price"] == pytest.approx(100.0)
    incomplete = _bars_after("NOOPEN", [(92.0, 94.0, 90.0, 91.0)])
    # Regression: the legacy tracker substituted close=91 as the open and
    # manufactured a -1.8R stop-gap exit.
    incomplete[0].pop("open")

    retry = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"NOOPEN": incomplete})
    )

    signal = _signal("NOOPEN")
    assert retry == {"evaluated": 1, "closed": 0, "errors": 1}
    assert signal["status"] == "OPEN"
    assert signal["eval_fail_count"] == 1
    assert signal["r_realized"] is None
    assert signal["exit_fill_price"] is None

    complete = [dict(incomplete[0], open=92.0)]
    decided = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"NOOPEN": complete})
    )
    signal = _signal("NOOPEN")
    assert decided == {"evaluated": 1, "closed": 1, "errors": 0}
    assert signal["status"] == "STOP_HIT"
    assert signal["exit_fill_price"] == pytest.approx(92.0)
    assert signal["r_realized"] == pytest.approx(-1.6)


@pytest.mark.parametrize(
    ("row", "bar"),
    [
        (
            _base_row(Ticker="LONGNOFILL", Preis=99.0),
            (106.0, 108.0, 104.0, 107.0),
        ),
        (
            _base_row(
                Ticker="SHORTNOFILL",
                Direction="SHORT",
                StopLoss=105.0,
                TP1=95.0,
                TP2=90.0,
                Preis=101.0,
            ),
            (94.0, 96.0, 92.0, 93.0),
        ),
    ],
)
def test_stock_gap_beyond_tp1_is_no_fill(tracker, row, bar):
    ticker = row["Ticker"]
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: _bars_after(ticker, [bar])})
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    sig = _signal(ticker)
    assert sig["status"] == "NO_FILL"
    assert sig["entry_filled_at"] is None
    assert sig["entry_fill_price"] is None
    assert sig["r_realized"] is None
    assert sig["outcome_detail"] == "entry_gapped_beyond_tp1"
    assert sig["be_activated_at"] is None


def test_breakeven_activation_requires_confirmed_fill(tracker):
    row = _base_row(Ticker="NOFILLBE", Preis=99.0)
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    with sqlite3.connect(tracker.SIGNAL_DB_PATH) as conn:
        conn.execute(
            "UPDATE signals SET max_favorable_r=1.5 WHERE ticker='NOFILLBE'"
        )

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher(
            {"NOFILLBE": _bars_after("NOFILLBE", [(99.0, 99.5, 98.0, 99.0)])}
        )
    )

    assert result["evaluated"] == 1
    assert result["be_activations"] == []
    sig = _signal("NOFILLBE")
    assert sig["status"] == "OPEN"
    assert sig["entry_filled_at"] is None
    assert sig["be_activated_at"] is None


def test_stock_strategy_does_not_expire_after_legacy_five_bars(tracker):
    row = _base_row(
        Strategy="Momentum Breakout Long",
        TradeHorizon="swing",
    )
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    five_bars = _bars_after("AAPL", [(102.0, 98.0, 101.0)] * 5)
    first = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": five_bars})
    )
    assert first == {"evaluated": 1, "closed": 0, "errors": 0}
    assert _signal("AAPL")["status"] == "OPEN"

    eight_bars = _bars_after("AAPL", [(102.0, 98.0, 101.0)] * 8)
    second = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": eight_bars})
    )
    assert second == {"evaluated": 1, "closed": 1, "errors": 0}
    assert _signal("AAPL")["status"] == "EXPIRED"


def test_orb_expires_after_one_completed_daily_bar(tracker):
    assert tracker.record_alert_signals("orb", [_base_row()]) == 1
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher(
            {"AAPL": _bars_after("AAPL", [(102.0, 98.0, 101.0)])}
        )
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    assert _signal("AAPL")["status"] == "EXPIRED"


def test_same_day_stock_uses_intraday_interval_instead_of_daily_bars(tracker):
    assert tracker.record_alert_signals("stock_strategy", [_base_row()]) == 1
    created_at = datetime.fromisoformat(_signal("AAPL")["created_at"])
    daily_calls = []
    intraday_calls = []

    def daily_fetcher(ticker, since_iso_date):
        daily_calls.append((ticker, since_iso_date))
        return []

    def intraday_fetcher(ticker, **kwargs):
        intraday_calls.append((ticker, kwargs))
        return {
            "current": 96.0,
            "interval_open": 100.0,
            "interval_high": 102.0,
            "interval_low": 94.0,
            "interval_complete": True,
            "source": "polygon_5m",
        }

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=daily_fetcher,
        stock_intraday_fetcher=intraday_fetcher,
        now=created_at + timedelta(minutes=10),
    )

    assert result["evaluated"] == 1
    assert result["closed"] == 1
    assert result["errors"] == 0
    assert daily_calls == []
    assert intraday_calls and intraday_calls[0][0] == "AAPL"
    assert _signal("AAPL")["status"] == "STOP_HIT"


def test_missing_same_day_stock_interval_does_not_advance_failure_state(tracker):
    row = _base_row(Ticker="MSFT", Preis=99.0)
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    before = _signal("MSFT")
    created_at = datetime.fromisoformat(before["created_at"])

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *_args: pytest.fail("daily fetcher must not run same-day"),
        stock_intraday_fetcher=lambda _ticker, **_kwargs: None,
        now=created_at + timedelta(minutes=10),
    )

    after = _signal("MSFT")
    assert result["evaluated"] == 1
    assert result["closed"] == 0
    assert result["errors"] == 0
    assert after["status"] == "OPEN"
    assert after["last_eval_at"] == before["last_eval_at"]
    assert after["eval_fail_count"] == before["eval_fail_count"] == 0


def test_incomplete_alert_day_backfill_blocks_next_day_daily_outcome(tracker):
    row = _base_row(
        Ticker="BACKFILL",
        Preis=99.0,
        fill_evidence_verified=False,
    )
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    before = _signal("BACKFILL")
    created_at = datetime.fromisoformat(before["created_at"])
    next_day = created_at + timedelta(days=1)
    while next_day.astimezone(tracker.ZoneInfo("America/New_York")).date() == created_at.astimezone(tracker.ZoneInfo("America/New_York")).date():
        next_day += timedelta(hours=1)
    daily_calls = []

    def incomplete_backfill(_ticker, **_kwargs):
        return {
            "current": 96.0,
            "interval_open": 99.0,
            "interval_high": 101.0,
            "interval_low": 94.0,
            "interval_complete": False,
            "source": "polygon_5m_incomplete",
        }

    def daily_fetcher(*args):
        daily_calls.append(args)
        return _bars_after("BACKFILL", [(100.0, 111.0, 99.0, 110.0)])

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=daily_fetcher,
        stock_intraday_fetcher=incomplete_backfill,
        now=next_day,
    )

    after = _signal("BACKFILL")
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert daily_calls == []
    assert after["status"] == "OPEN"
    assert after["last_eval_at"] is None
    assert after["eval_fail_count"] == 0
    assert after["entry_filled_at"] is None
    assert after["r_realized"] is None


def test_alert_day_backfill_rejects_completed_prefix_with_missing_tail(tracker):
    row = _base_row(
        Ticker="BACKGAP", Preis=99.0, fill_evidence_verified=False
    )
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    created_at = datetime.fromisoformat(_signal("BACKGAP")["created_at"])
    next_day = created_at + timedelta(days=1)
    daily_calls = []
    first_start = created_at
    first_end = first_start + timedelta(minutes=5)

    def truncated_backfill(_ticker, **_kwargs):
        return {
            "current": 99.0,
            "interval_open": 99.0,
            "interval_high": 99.5,
            "interval_low": 98.5,
            "interval_complete": True,
            "source": "polygon_5m_truncated",
            "intervals": [{
                "current": 99.0,
                "interval_open": 99.0,
                "interval_high": 99.5,
                "interval_low": 98.5,
                "interval_complete": True,
                "source": "polygon_5m_truncated",
                "started_at": first_start.isoformat(),
                "observed_at": first_end.isoformat(),
            }],
        }

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *args: daily_calls.append(args) or [],
        stock_intraday_fetcher=truncated_backfill,
        now=next_day,
    )

    after = _signal("BACKGAP")
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert daily_calls == []
    assert after["status"] == "OPEN"
    assert after["last_eval_at"] is None
    assert after["eval_fail_count"] == 0


def test_alert_day_initial_five_minute_blind_gap_is_untracked(
    tracker, monkeypatch
):
    created_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    row = _base_row(
        Ticker="INITIALGAP",
        price_observed_at=created_at.isoformat(),
    )
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    first_start = datetime(2026, 8, 11, 14, 5, tzinfo=timezone.utc)
    close_at = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    intervals = []
    cursor = first_start
    while cursor < close_at:
        intervals.append({
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": cursor.isoformat(),
            "observed_at": (cursor + timedelta(minutes=5)).isoformat(),
        })
        cursor += timedelta(minutes=5)

    daily_calls = []
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *args: daily_calls.append(args) or [],
        stock_intraday_fetcher=lambda *_args, **_kwargs: {
            "current": 100.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        },
        now=datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc),
    )

    signal = _signal("INITIALGAP")
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    assert daily_calls == []
    assert signal["status"] == "UNTRACKED"
    assert signal["last_eval_at"] is None
    assert signal["r_realized"] is None
    assert signal["outcome_detail"] == (
        "alert_day_initial_interval_unobservable_after_confirmed_fill"
    )


def test_alert_day_tail_backfills_from_existing_causal_cursor(
    tracker, monkeypatch
):
    created_at = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    row = _base_row(
        Ticker="TAILSTOP",
        price_observed_at=created_at.isoformat(),
    )
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    tail_since = datetime(2026, 8, 11, 18, 0, tzinfo=timezone.utc)
    conn = sqlite3.connect(tracker.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET last_eval_at = ? WHERE ticker = 'TAILSTOP'",
            (tail_since.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    close_at = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    intervals = []
    cursor = tail_since
    while cursor < close_at:
        stop_bar = cursor == tail_since
        intervals.append({
            "current": 96.0 if stop_bar else 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 94.0 if stop_bar else 99.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": cursor.isoformat(),
            "observed_at": (cursor + timedelta(minutes=5)).isoformat(),
        })
        cursor += timedelta(minutes=5)
    fetch_calls = []
    daily_calls = []

    def tail_fetcher(_ticker, **kwargs):
        fetch_calls.append(kwargs)
        return {
            "current": 100.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        }

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *args: daily_calls.append(args) or [],
        stock_intraday_fetcher=tail_fetcher,
        now=datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc),
    )

    signal = _signal("TAILSTOP")
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    assert fetch_calls[0]["since"] == tail_since.isoformat()
    assert daily_calls == []
    assert signal["status"] == "STOP_HIT"
    assert signal["r_realized"] == pytest.approx(-1.0)


def test_alert_day_backfill_uses_exchange_calendar_early_close(
    tracker, monkeypatch
):
    alert_at = datetime(2026, 11, 27, 16, 0, tzinfo=timezone.utc)
    close_at = datetime(2026, 11, 27, 18, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: alert_at)
    assert tracker._us_equity_session_close(
        alert_at.astimezone(tracker.ZoneInfo("America/New_York")).date()
    ).hour == 13
    assert tracker._us_equity_session_close(datetime(2026, 7, 2).date()).hour == 13
    assert tracker._us_equity_session_close(datetime(2027, 7, 2).date()).hour == 16
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(
            Ticker="EARLYCLOSE",
            fill_evidence_verified=False,
            price_observed_at=None,
            price_source=None,
        )],
    ) == 1

    intervals = []
    cursor = alert_at
    while cursor < close_at:
        intervals.append({
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": cursor.isoformat(),
            "observed_at": (cursor + timedelta(minutes=5)).isoformat(),
        })
        cursor += timedelta(minutes=5)
    calls = []

    def fetcher(_ticker, **kwargs):
        calls.append(kwargs)
        return {
            "current": 100.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        }

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *_args: [],
        stock_intraday_fetcher=fetcher,
        now=datetime(2026, 11, 30, 14, 0, tzinfo=timezone.utc),
    )

    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    assert calls[0]["since"] == alert_at.isoformat()
    assert calls[0]["until"] == close_at.isoformat()
    assert _signal("EARLYCLOSE")["last_eval_at"] == close_at.isoformat()


def test_nyse_special_closure_drives_session_close_backfill_and_maturity(tracker):
    mourning_day = date(2025, 1, 9)
    assert mourning_day in tracker._us_equity_holidays(2025)
    assert tracker._is_us_equity_session(mourning_day) is False
    assert tracker._us_equity_session_close(mourning_day) is None
    assert tracker._latest_completed_stock_session(
        datetime(2025, 1, 9, 22, 0, tzinfo=timezone.utc)
    ) == date(2025, 1, 8)

    # Friday's valid bar follows Wednesday's alert without a missing-session
    # error: Thursday was the one-off exchange closure, not a trading day.
    bars, error = tracker._normalize_daily_bars(
        [{
            "date": "2025-01-10",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "interval_complete": True,
        }],
        date(2025, 1, 8),
        datetime(2025, 1, 10, 22, 0, tzinfo=timezone.utc),
    )
    assert error is None
    assert [bar[0] for bar in bars] == [date(2025, 1, 10)]

    # One observation bar plus the two conservative buffer sessions matures on
    # Jan 14, not Jan 13, because Jan 9 must not be counted.
    assert tracker._stock_maturity_at(
        datetime(2025, 1, 8, 15, 0, tzinfo=timezone.utc), 1
    ) == datetime(2025, 1, 14, 23, 59, 59, tzinfo=timezone.utc)


def test_nyse_observed_fixed_holidays_follow_exchange_rules(tracker):
    # NYSE explicitly does not substitute Friday when New Year's Day is a
    # Saturday; the other fixed-date holidays retain Friday/Monday observation.
    assert tracker._is_us_equity_session(date(2021, 12, 31)) is True
    assert tracker._is_us_equity_session(date(2022, 6, 20)) is False
    assert tracker._is_us_equity_session(date(2026, 7, 3)) is False
    assert tracker._is_us_equity_session(date(2027, 12, 24)) is False


@pytest.mark.parametrize(
    ("ticker", "direction", "bar"),
    [
        (
            "MALFORMEDLONG",
            "LONG",
            {"high": 101.0, "low": 94.0, "close": 96.0},
        ),
        (
            "MALFORMEDSHORT",
            "SHORT",
            {"high": 106.0, "low": 99.0, "close": 104.0},
        ),
    ],
)
def test_malformed_daily_bar_cannot_be_skipped_before_later_winner(
    tracker, monkeypatch, ticker, direction, bar
):
    created_at = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    row = _base_row(Ticker=ticker, price_observed_at=created_at.isoformat())
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    sessions = _bars_after(
        ticker,
        [
            (100.0, bar["high"], bar["low"], bar["close"]),
            (100.0, 111.0, 99.0, 110.0)
            if direction == "LONG"
            else (100.0, 101.0, 89.0, 90.0),
        ],
    )
    sessions[0].pop("open")

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: sessions}),
        now=datetime(2026, 8, 13, 21, 0, tzinfo=timezone.utc),
    )

    signal = _signal(ticker)
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert signal["status"] == "OPEN"
    assert signal["r_realized"] is None
    assert signal["eval_fail_count"] == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_missing_daily_session_blocks_later_target(
    tracker, monkeypatch, direction
):
    created_at = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    ticker = "MISSLONG" if direction == "LONG" else "MISSSHORT"
    row = _base_row(Ticker=ticker, price_observed_at=created_at.isoformat())
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1
    bars = _bars_after(
        ticker,
        [
            (100.0, 101.0, 99.0, 100.0),
            (100.0, 111.0, 99.0, 110.0)
            if direction == "LONG"
            else (100.0, 101.0, 89.0, 90.0),
        ],
    )
    bars = bars[1:]  # first regular session is entirely absent

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: bars}),
        now=datetime(2026, 8, 13, 21, 0, tzinfo=timezone.utc),
    )

    signal = _signal(ticker)
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert signal["status"] == "OPEN"
    assert signal["r_realized"] is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_running_daily_bar_never_counts_toward_one_bar_expiry(
    tracker, monkeypatch, direction
):
    created_at = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    ticker = "RUNLONG" if direction == "LONG" else "RUNSHORT"
    row = _base_row(
        Ticker=ticker,
        fill_evidence_verified=False,
        price_observed_at=None,
        price_source=None,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("orb", [row]) == 1
    bar = _bars_after(
        ticker,
        [(98.0, 99.0, 97.0, 98.0)]
        if direction == "LONG"
        else [(102.0, 103.0, 101.0, 102.0)],
    )[0]
    bar["interval_complete"] = False

    partial = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: [bar]}),
        now=datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc),
    )
    assert partial == {"evaluated": 1, "closed": 0, "errors": 0}
    assert _signal(ticker)["status"] == "OPEN"

    bar["interval_complete"] = True
    completed = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({ticker: [bar]}),
        now=datetime(2026, 8, 11, 21, 0, tzinfo=timezone.utc),
    )
    assert completed == {"evaluated": 1, "closed": 1, "errors": 0}
    assert _signal(ticker)["status"] == "NO_FILL"


def test_empty_daily_history_after_completed_session_is_a_data_failure(
    tracker, monkeypatch
):
    created_at = datetime(2026, 8, 10, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="EMPTYDAILY", price_observed_at=created_at.isoformat())],
    ) == 1

    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *_args: [],
        now=datetime(2026, 8, 12, 14, 0, tzinfo=timezone.utc),
    )

    signal = _signal("EMPTYDAILY")
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert signal["status"] == "OPEN"
    assert signal["eval_fail_count"] == 1


def test_git_revision_fallback_marks_dirty_tracked_worktree(monkeypatch):
    monkeypatch.delenv("APP_REVISION", raising=False)
    monkeypatch.delenv("GIT_COMMIT", raising=False)

    def fake_run(args, **_kwargs):
        if args[1] == "rev-parse":
            return SimpleNamespace(returncode=0, stdout="0123456789ab\n")
        assert args[1:3] == ["status", "--porcelain"]
        return SimpleNamespace(returncode=0, stdout=" M modules/signal_tracker.py\n")

    monkeypatch.setattr(st.subprocess, "run", fake_run)
    assert st._read_process_code_revision() == "0123456789ab-dirty"


def test_eval_failure_never_advances_causal_cursor(tracker):
    assert tracker.record_alert_signals(
        "crypto_explosion", [_base_row(Ticker="CURSOR", fill_evidence_verified=False)]
    ) == 1
    before = _signal("CURSOR")

    first = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda *_args, **_kwargs: None
    )
    after_failure = _signal("CURSOR")

    assert first == {"evaluated": 1, "closed": 0, "errors": 1}
    assert after_failure["last_eval_at"] == before["last_eval_at"] is None
    assert after_failure["eval_fail_count"] == 1


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_crypto_initial_five_minute_boundary_gap_is_untracked(
    tracker, monkeypatch, direction
):
    created_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: created_at)
    ticker = "CRYPTGAPLONG" if direction == "LONG" else "CRYPTGAPSHORT"
    row = _base_row(
        Ticker=ticker,
        price_observed_at=created_at.isoformat(),
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    started_at = datetime(2026, 8, 11, 14, 5, tzinfo=timezone.utc)
    interval = {
        "current": 100.0,
        "interval_open": 100.0,
        "interval_high": 101.0,
        "interval_low": 99.0,
        "interval_complete": True,
        "source": "binance_5m",
        "started_at": started_at.isoformat(),
        "observed_at": (started_at + timedelta(minutes=5)).isoformat(),
    }

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda *_args, **_kwargs: {
            "current": 100.0,
            "interval_complete": True,
            "source": "binance_5m",
            "intervals": [interval],
        },
        now=started_at + timedelta(minutes=5),
    )

    signal = _signal(ticker)
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    assert signal["status"] == "UNTRACKED"
    assert signal["last_eval_at"] is None
    assert signal["r_realized"] is None
    assert signal["outcome_detail"] == (
        "initial_interval_coverage_incomplete_after_confirmed_fill"
    )


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_crypto_internal_candle_gap_retries_then_untracks_without_tp2_or_cursor_loss(
    tracker, monkeypatch, direction
):
    causal_at = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: causal_at)
    ticker = "INTERNALGAPLONG" if direction == "LONG" else "INTERNALGAPSHORT"
    row = _base_row(Ticker=ticker, price_observed_at=causal_at.isoformat())
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    first_end = causal_at + timedelta(minutes=5)
    target_start = causal_at + timedelta(minutes=10)  # missing 14:05 candle
    target_end = target_start + timedelta(minutes=5)
    intervals = [
        {
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "binance_5m",
            "started_at": causal_at.isoformat(),
            "observed_at": first_end.isoformat(),
        },
        {
            "current": 110.0 if direction == "LONG" else 90.0,
            "interval_open": 100.0,
            "interval_high": 111.0 if direction == "LONG" else 101.0,
            "interval_low": 99.0 if direction == "LONG" else 89.0,
            "interval_complete": True,
            "source": "binance_5m",
            "started_at": target_start.isoformat(),
            "observed_at": target_end.isoformat(),
        },
    ]

    def gapped_fetcher(*_args, **_kwargs):
        return {
            "current": intervals[-1]["current"],
            "interval_complete": True,
            "source": "binance_5m",
            "intervals": intervals,
        }

    for attempt in range(1, tracker.MAX_EVAL_FAILS + 1):
        result = tracker.evaluate_open_signals(
            crypto_price_fetcher=gapped_fetcher,
            now=target_end,
        )
        signal = _signal(ticker)
        assert result["errors"] == 1
        assert signal["last_eval_at"] is None
        assert signal["tp2_hit_at"] is None
        assert signal["r_realized"] is None
        assert signal["eval_fail_count"] == attempt
        if attempt < tracker.MAX_EVAL_FAILS:
            assert result["closed"] == 0
            assert signal["status"] == "OPEN"
        else:
            assert result["closed"] == 1
            assert signal["status"] == "UNTRACKED"
            assert signal["outcome_detail"] == "eval_failed_5x_after_confirmed_fill"


@pytest.mark.parametrize(
    ("second_start_offset", "second_end_offset"),
    [
        (4, 9),    # overlap
        (-5, 0),   # out of order
    ],
)
def test_interval_coverage_rejects_overlap_and_out_of_order(
    tracker, second_start_offset, second_end_offset
):
    since = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)

    def interval(start_offset, end_offset):
        return {
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "binance_5m",
            "started_at": (since + timedelta(minutes=start_offset)).isoformat(),
            "observed_at": (since + timedelta(minutes=end_offset)).isoformat(),
        }

    observation = tracker._normalize_crypto_observation({
        "current": 100.0,
        "interval_complete": True,
        "intervals": [interval(0, 5), interval(second_start_offset, second_end_offset)],
    })
    assert tracker._has_complete_interval_coverage(observation, since) is False


def test_same_day_stock_internal_candle_gap_retries_without_tp2_or_cursor_loss(
    tracker, monkeypatch
):
    causal_at = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: causal_at)
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(Ticker="STOCKINTERNALGAP", price_observed_at=causal_at.isoformat())],
    ) == 1
    target_end = causal_at + timedelta(minutes=15)
    intervals = [
        {
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": causal_at.isoformat(),
            "observed_at": (causal_at + timedelta(minutes=5)).isoformat(),
        },
        {
            "current": 110.0,
            "interval_open": 100.0,
            "interval_high": 111.0,
            "interval_low": 99.0,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": (causal_at + timedelta(minutes=10)).isoformat(),
            "observed_at": target_end.isoformat(),
        },
    ]

    result = tracker.evaluate_open_signals(
        stock_intraday_fetcher=lambda *_args, **_kwargs: {
            "current": intervals[-1]["current"],
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        },
        now=target_end,
    )

    signal = _signal("STOCKINTERNALGAP")
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert signal["status"] == "OPEN"
    assert signal["last_eval_at"] is None
    assert signal["eval_fail_count"] == 0
    assert signal["tp2_hit_at"] is None
    assert signal["r_realized"] is None


@pytest.mark.parametrize("direction", ["LONG", "SHORT"])
def test_unaligned_crypto_boundary_no_touch_then_contiguous_target_is_causal(
    tracker, monkeypatch, direction
):
    accepted_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: accepted_at)
    ticker = "BOUNDARYLONG" if direction == "LONG" else "BOUNDARYSHORT"
    row = _base_row(
        Ticker=ticker,
        Preis=99.0 if direction == "LONG" else 101.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    boundary_start = accepted_at.replace(second=0, microsecond=0)
    boundary_end = boundary_start + timedelta(minutes=5)
    later_end = boundary_end + timedelta(minutes=5)
    intervals = [
        {
            "current": 98.0 if direction == "LONG" else 102.0,
            "interval_open": 98.0 if direction == "LONG" else 102.0,
            "interval_high": 99.0 if direction == "LONG" else 104.0,
            "interval_low": 96.0 if direction == "LONG" else 101.0,
            "interval_complete": True,
            "boundary_overlap": True,
            "source": "binance_5m",
            "started_at": boundary_start.isoformat(),
            "observed_at": boundary_end.isoformat(),
        },
        {
            "current": 110.0 if direction == "LONG" else 90.0,
            "interval_open": 99.0 if direction == "LONG" else 101.0,
            "interval_high": 111.0 if direction == "LONG" else 102.0,
            "interval_low": 98.0 if direction == "LONG" else 89.0,
            "interval_complete": True,
            "boundary_overlap": False,
            "source": "binance_5m",
            "started_at": boundary_end.isoformat(),
            "observed_at": later_end.isoformat(),
        },
    ]
    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda *_args, **_kwargs: {
            "current": intervals[-1]["current"],
            "interval_complete": True,
            "source": "binance_5m",
            "intervals": intervals,
        },
        now=later_end,
    )

    signal = _signal(ticker)
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    assert signal["status"] == "TP2_HIT"
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["r_realized"] == pytest.approx(2.0)
    assert signal["last_eval_at"] == later_end.isoformat()


@pytest.mark.parametrize(
    ("scanner", "fetcher_name"),
    [
        ("crypto_explosion", "crypto_price_fetcher"),
        ("stock_strategy", "stock_intraday_fetcher"),
    ],
)
def test_unaligned_boundary_touch_is_untracked_without_invented_fill_or_r(
    tracker, monkeypatch, scanner, fetcher_name
):
    accepted_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: accepted_at)
    ticker = "CRYPTOBOUNDARYTOUCH" if scanner.startswith("crypto") else "STOCKBOUNDARYTOUCH"
    assert tracker.record_alert_signals(
        scanner,
        [_base_row(
            Ticker=ticker,
            Preis=99.0,
            price_observed_at=None,
            price_source=None,
            fill_evidence_verified=False,
        )],
    ) == 1
    boundary_start = accepted_at.replace(second=0, microsecond=0)
    boundary_end = boundary_start + timedelta(minutes=5)
    interval = {
        "current": 100.0,
        "interval_open": 99.0,
        "interval_high": 101.0,
        "interval_low": 98.0,
        "interval_complete": True,
        "boundary_overlap": True,
        "source": "exchange_5m",
        "started_at": boundary_start.isoformat(),
        "observed_at": boundary_end.isoformat(),
    }
    fetcher = lambda *_args, **_kwargs: {
        "current": 100.0,
        "interval_complete": True,
        "source": "exchange_5m",
        "intervals": [interval],
    }

    result = tracker.evaluate_open_signals(
        now=boundary_end,
        **{fetcher_name: fetcher},
    )

    signal = _signal(ticker)
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    assert signal["status"] == "UNTRACKED"
    assert signal["outcome_detail"] == (
        "causal_boundary_interval_level_touch_unresolved"
    )
    assert signal["last_eval_at"] is None
    assert signal["entry_filled_at"] is None
    assert signal["entry_fill_price"] is None
    assert signal["tp1_hit_at"] is None
    assert signal["tp2_hit_at"] is None
    assert signal["stop_hit_at"] is None
    assert signal["r_realized"] is None


def test_alert_day_backfill_accepts_no_touch_boundary_then_contiguous_path(
    tracker, monkeypatch
):
    accepted_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: accepted_at)
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(
            Ticker="BACKFILLBOUNDARY",
            Preis=99.0,
            price_observed_at=None,
            price_source=None,
            fill_evidence_verified=False,
        )],
    ) == 1

    boundary_start = accepted_at.replace(second=0, microsecond=0)
    close_at = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    intervals = []
    cursor = boundary_start
    while cursor < close_at:
        first = cursor == boundary_start
        target = cursor == boundary_start + timedelta(minutes=5)
        intervals.append({
            "current": 110.0 if target else 98.0,
            "interval_open": 99.0 if target else 98.0,
            "interval_high": 111.0 if target else 99.0,
            "interval_low": 98.0 if target else 96.0,
            "interval_complete": True,
            "boundary_overlap": first,
            "source": "polygon_5m",
            "started_at": cursor.isoformat(),
            "observed_at": (cursor + timedelta(minutes=5)).isoformat(),
        })
        cursor += timedelta(minutes=5)

    daily_calls = []
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *_args: daily_calls.append(True) or [],
        stock_intraday_fetcher=lambda *_args, **_kwargs: {
            "current": intervals[-1]["current"],
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        },
        now=datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc),
    )

    signal = _signal("BACKFILLBOUNDARY")
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    assert daily_calls == []
    assert signal["status"] == "TP2_HIT"
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["r_realized"] == pytest.approx(2.0)


def test_boundary_be_trigger_touch_after_verified_fill_is_unresolved_not_r(
    tracker, monkeypatch
):
    accepted_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: accepted_at)
    assert tracker.record_alert_signals(
        "crypto_explosion",
        [_base_row(
            Ticker="BOUNDARYBETRIGGER",
            TP1=110.0,
            TP2=115.0,
            price_observed_at=accepted_at.isoformat(),
        )],
    ) == 1
    boundary_start = accepted_at.replace(second=0, microsecond=0)
    boundary_end = boundary_start + timedelta(minutes=5)
    interval = {
        "current": 104.0,
        "interval_open": 100.0,
        "interval_high": 105.5,  # +1R trigger, but before/after acceptance unknown
        "interval_low": 99.0,
        "interval_complete": True,
        "boundary_overlap": True,
        "source": "binance_5m",
        "started_at": boundary_start.isoformat(),
        "observed_at": boundary_end.isoformat(),
    }

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda *_args, **_kwargs: {
            "current": interval["current"],
            "interval_complete": True,
            "source": "binance_5m",
            "intervals": [interval],
        },
        now=boundary_end,
    )

    signal = _signal("BOUNDARYBETRIGGER")
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    assert signal["status"] == "UNTRACKED"
    assert signal["outcome_detail"] == (
        "causal_boundary_interval_level_touch_unresolved_after_confirmed_fill"
    )
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["be_activated_at"] is None
    assert signal["r_realized"] is None
    assert signal["r_realized_be"] is None


def test_alert_day_backfill_boundary_touch_stays_terminally_unresolved(
    tracker, monkeypatch
):
    accepted_at = datetime(2026, 8, 11, 14, 0, 1, tzinfo=timezone.utc)
    monkeypatch.setattr(tracker, "_utc_now", lambda: accepted_at)
    assert tracker.record_alert_signals(
        "stock_strategy",
        [_base_row(
            Ticker="BACKFILLBOUNDARYTOUCH",
            Preis=99.0,
            price_observed_at=None,
            price_source=None,
            fill_evidence_verified=False,
        )],
    ) == 1

    boundary_start = accepted_at.replace(second=0, microsecond=0)
    close_at = datetime(2026, 8, 11, 20, 0, tzinfo=timezone.utc)
    intervals = []
    cursor = boundary_start
    while cursor < close_at:
        first = cursor == boundary_start
        intervals.append({
            "current": 100.0 if first else 98.0,
            "interval_open": 99.0 if first else 98.0,
            "interval_high": 101.0 if first else 99.0,
            "interval_low": 98.0 if first else 96.0,
            "interval_complete": True,
            "boundary_overlap": first,
            "source": "polygon_5m",
            "started_at": cursor.isoformat(),
            "observed_at": (cursor + timedelta(minutes=5)).isoformat(),
        })
        cursor += timedelta(minutes=5)

    daily_calls = []
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=lambda *_args: daily_calls.append(True) or [],
        stock_intraday_fetcher=lambda *_args, **_kwargs: {
            "current": intervals[-1]["current"],
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": intervals,
        },
        now=datetime(2026, 8, 12, 17, 0, tzinfo=timezone.utc),
    )

    signal = _signal("BACKFILLBOUNDARYTOUCH")
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    assert daily_calls == []
    assert signal["status"] == "UNTRACKED"
    assert signal["outcome_detail"] == (
        "causal_boundary_interval_level_touch_unresolved"
    )
    assert signal["entry_filled_at"] is None
    assert signal["r_realized"] is None


def test_stock_gap_fill_is_rejected_when_slippage_breaks_plan(tracker):
    row = _base_row(Ticker="FSLR", Preis=99.0, TP1=110.0, TP2=115.0)
    assert tracker.record_alert_signals("stock_strategy", [row]) == 1

    # Planned risk is 5. A gap-open at 108 would add 1.6R adverse slippage and
    # leave insufficient reward, so the tracker must not pretend this was a fill.
    bars = _bars_after("FSLR", [(108.0, 109.0, 107.0, 108.5)])
    result = tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"FSLR": bars})
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal("FSLR")
    assert signal["status"] == "NO_FILL"
    assert signal["entry_filled_at"] is None
    assert signal["outcome_detail"].startswith("adverse_fill_slippage|")
    assert "adverse_slippage_r=1.6000" in signal["outcome_detail"]


def test_evaluate_stock_expired_without_any_tp(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    specs = [(103.0, 98.0, 101.0)] * 4 + [
        (102.0, 99.0, 99.0),    # Tag 5: Expiry mit Close 99
        (111.0, 104.0, 110.0),  # Tag 6 existiert, zaehlt aber nicht mehr
    ]
    tracker.evaluate_open_signals(
        stock_daily_fetcher=_stock_fetcher({"AAPL": _bars_after("AAPL", specs)})
    )
    sig = _signal("AAPL")
    assert sig["status"] == "EXPIRED"
    assert sig["outcome_detail"] == ""
    assert sig["r_realized"] == pytest.approx(-0.2)  # (99-100)/5
    assert not sig["tp1_hit_at"]


def test_evaluate_stock_short_mirrored(tracker):
    loser = {"Ticker": "SHRT", "direction": "SHORT", "Entry": 100.0,
             "StopLoss": 105.0, "TP1": 95.0, "TP2": 90.0}
    winner = dict(loser, Ticker="SHRT2")
    assert tracker.record_alert_signals("short_squeeze", [loser, winner]) == 2
    bars = {
        "SHRT": _bars_after("SHRT", [(104.0, 98.0, 99.0), (106.0, 101.0, 105.5)]),
        "SHRT2": _bars_after("SHRT2", [(102.0, 94.0, 95.0), (96.0, 89.0, 90.5)]),
    }
    result = tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher(bars))
    assert result == {"evaluated": 2, "closed": 2, "errors": 0}
    sig_loser = _signal("SHRT")
    assert sig_loser["status"] == "STOP_HIT"               # Tag 2: High >= Stop
    assert sig_loser["r_realized"] == pytest.approx(-1.0)
    assert sig_loser["stop_hit_at"] == bars["SHRT"][1]["date"]
    sig_winner = _signal("SHRT2")
    assert sig_winner["status"] == "TP2_HIT"               # Tag 1 TP1, Tag 2 TP2
    assert sig_winner["tp1_hit_at"] == bars["SHRT2"][0]["date"]
    assert sig_winner["tp2_hit_at"] == bars["SHRT2"][1]["date"]
    assert sig_winner["r_realized"] == pytest.approx(2.0)  # (100-90)/(105-100)


def test_evaluate_tracks_max_favorable_and_adverse_r(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    bars1 = _bars_after("AAPL", [(104.0, 97.0, 103.0)])  # +0.8R / -0.6R
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars1}))
    sig = _signal("AAPL")
    assert sig["status"] == "OPEN"
    assert sig["max_favorable_r"] == pytest.approx(0.8)
    assert sig["max_adverse_r"] == pytest.approx(-0.6)
    # zweiter Lauf erweitert die Extreme nur nach aussen
    bars2 = _bars_after("AAPL", [(104.0, 97.0, 103.0), (107.0, 99.0, 106.0)])
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher({"AAPL": bars2}))
    sig = _signal("AAPL")
    assert sig["max_favorable_r"] == pytest.approx(1.4)  # (107-100)/5
    assert sig["max_adverse_r"] == pytest.approx(-0.6)   # bleibt das Minimum


# ── evaluate_open_signals: Fehlversuche / UNTRACKED ──────────────────────────
def test_evaluate_untracked_after_five_failures(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])
    none_fetcher = _stock_fetcher({})  # liefert fuer AAPL immer None
    for attempt in range(1, 5):
        result = tracker.evaluate_open_signals(stock_daily_fetcher=none_fetcher)
        assert result == {"evaluated": 1, "closed": 0, "errors": 1}
        sig = _signal("AAPL")
        assert sig["status"] == "OPEN"
        assert sig["eval_fail_count"] == attempt
    # 5. Fehlversuch -> UNTRACKED (terminal)
    result = tracker.evaluate_open_signals(stock_daily_fetcher=none_fetcher)
    assert result == {"evaluated": 1, "closed": 1, "errors": 1}
    sig = _signal("AAPL")
    assert sig["status"] == "UNTRACKED"
    assert sig["eval_fail_count"] == 5
    assert sig["closed_at"]
    assert sig["r_realized"] is None
    # danach gibt es nichts mehr zu bewerten
    assert tracker.evaluate_open_signals(stock_daily_fetcher=none_fetcher) == {
        "evaluated": 0, "closed": 0, "errors": 0,
    }


def test_evaluate_fetcher_exception_counts_as_failure(tracker):
    tracker.record_alert_signals("breakout", [_base_row()])

    def boom(ticker, since_iso_date):
        raise RuntimeError("api down")

    result = tracker.evaluate_open_signals(stock_daily_fetcher=boom)
    assert result == {"evaluated": 1, "closed": 0, "errors": 1}
    assert _signal("AAPL")["eval_fail_count"] == 1
    # ohne injizierten Fetcher wird uebersprungen — KEIN weiterer Fehlversuch
    assert tracker.evaluate_open_signals() == {"evaluated": 0, "closed": 0, "errors": 0}
    assert _signal("AAPL")["eval_fail_count"] == 1


# ── evaluate_open_signals: Crypto (Spot-Check) ───────────────────────────────
def test_evaluate_crypto_spot_stop_tp_and_expiry(tracker):
    rows = [
        _base_row(Ticker="DOGE"),             # Spot unter Stop
        _base_row(Ticker="PEPE"),              # Spot ueber TP1, dann Expiry
        _base_row(Ticker="SOL"),              # Spot ueber TP2
    ]
    assert tracker.record_alert_signals("crypto_explosion", rows) == 3
    prices = {"DOGE": 94.0, "PEPE": 106.0, "SOL": 111.0}
    result = tracker.evaluate_open_signals(crypto_price_fetcher=prices.get)
    assert result == {"evaluated": 3, "closed": 0, "errors": 0}
    doge = _signal("DOGE")
    assert doge["status"] == "OPEN"
    assert doge["r_realized"] is None
    sol = _signal("SOL")
    assert sol["status"] == "OPEN"
    pepe = _signal("PEPE")
    assert pepe["status"] == "OPEN"
    # Punktpreise besitzen keinen Kursweg. Nach Ablauf werden sie ehrlich als
    # UNTRACKED geschlossen statt Stop/TP/Expiry zu erfinden.
    later = datetime.fromisoformat(pepe["created_at"]) + timedelta(hours=121)
    result2 = tracker.evaluate_open_signals(crypto_price_fetcher=prices.get, now=later)
    assert result2 == {"evaluated": 3, "closed": 3, "errors": 0}
    for ticker in ("DOGE", "PEPE", "SOL"):
        signal = _signal(ticker)
        assert signal["status"] == "UNTRACKED"
        assert signal["outcome_detail"] == (
            "observation_window_ended_after_fill_without_complete_interval_path"
        )


def test_crypto_completed_interval_captures_tp_touch_between_checks(tracker):
    assert tracker.record_alert_signals(
        "crypto_explosion", [_base_row(Ticker="BTC", TP1=105.0, TP2=110.0)]
    ) == 1

    observation = {
        "current": 102.0,
        "interval_open": 101.0,
        "interval_high": 106.0,
        "interval_low": 99.0,
        "interval_complete": True,
        "source": "binance_5m",
    }
    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )
    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    signal = _signal("BTC")
    assert signal["status"] == "OPEN"
    assert signal["tp1_hit_at"]
    assert signal["max_favorable_r"] == pytest.approx(1.2)
    assert signal["max_adverse_r"] == pytest.approx(-0.2)


@pytest.mark.parametrize(
    ("ticker", "direction", "observation"),
    [
        (
            "POSTLONG",
            "LONG",
            {
                "current": 101.0,
                "interval_open": 99.0,
                "interval_high": 102.0,
                "interval_low": 98.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
        (
            "POSTSHORT",
            "SHORT",
            {
                "current": 99.0,
                "interval_open": 101.0,
                "interval_high": 102.0,
                "interval_low": 98.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
    ],
)
def test_completed_post_alert_interval_fills_long_and_short(
    tracker, ticker, direction, observation
):
    row = _base_row(
        Ticker=ticker,
        Preis=100.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    assert _signal(ticker)["entry_filled_at"] is None

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "OPEN"
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["fill_evidence_mode"] == "post_alert_interval"


@pytest.mark.parametrize(
    ("ticker", "direction", "observation"),
    [
        (
            "AMBILONG",
            "LONG",
            {
                "current": 96.0,
                "interval_open": 99.0,
                "interval_high": 101.0,
                "interval_low": 94.0,
                "interval_complete": True,
                "source": "polygon_5m",
            },
        ),
        (
            "AMBISHORT",
            "SHORT",
            {
                "current": 104.0,
                "interval_open": 101.0,
                "interval_high": 106.0,
                "interval_low": 99.0,
                "interval_complete": True,
                "source": "polygon_5m",
            },
        ),
    ],
)
def test_unverified_scalar_cannot_resolve_same_interval_entry_stop_order(
    tracker, ticker, direction, observation
):
    # A deliberately invalid-looking displayed scalar (CBLL regression shape)
    # must not invent a pre-alert fill or settle the post-alert path order.
    alert_price = 90.0 if direction == "LONG" else 110.0
    row = _base_row(
        Ticker=ticker,
        Preis=alert_price,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "UNTRACKED"
    assert signal["entry_filled_at"] is None
    assert signal["outcome_detail"] == "ambiguous_entry_and_stop_same_interval"


@pytest.mark.parametrize(
    ("ticker", "direction", "observation"),
    [
        (
            "PARTIALLONG",
            "LONG",
            {
                "current": 92.0,
                "interval_open": 93.0,
                "interval_high": 94.0,
                "interval_low": 91.0,
                "interval_complete": False,
                "source": "binance_5m",
            },
        ),
        (
            "PARTIALSHORT",
            "SHORT",
            {
                "current": 108.0,
                "interval_open": 107.0,
                "interval_high": 109.0,
                "interval_low": 106.0,
                "interval_complete": False,
                "source": "binance_5m",
            },
        ),
    ],
)
def test_incomplete_interval_cannot_claim_invalidation_before_unfilled_entry(
    tracker, ticker, direction, observation
):
    row = _base_row(
        Ticker=ticker,
        Preis=100.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "OPEN"
    assert signal["entry_filled_at"] is None
    assert signal["r_realized"] is None


@pytest.mark.parametrize(
    ("ticker", "direction", "observation"),
    [
        (
            "OPENCAUSALLONG",
            "LONG",
            {
                "current": 100.0,
                "interval_open": 100.0,
                "interval_high": 111.0,
                "interval_low": 94.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
        (
            "OPENCAUSALSHORT",
            "SHORT",
            {
                "current": 100.0,
                "interval_open": 100.0,
                "interval_high": 106.0,
                "interval_low": 89.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
    ],
)
def test_open_proves_fill_before_same_interval_stop_and_target(
    tracker, ticker, direction, observation
):
    row = _base_row(
        Ticker=ticker,
        Preis=100.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "STOP_HIT"
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["r_realized"] == pytest.approx(-1.0)
    assert signal["r_realized_upper"] == pytest.approx(2.0)
    assert signal["outcome_detail"] == "ambiguous_same_interval_stop_first"


@pytest.mark.parametrize(
    ("ticker", "direction", "observation"),
    [
        (
            "CROSSCAUSALLONG",
            "LONG",
            {
                "current": 110.0,
                "interval_open": 99.0,
                "interval_high": 111.0,
                "interval_low": 98.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
        (
            "CROSSCAUSALSHORT",
            "SHORT",
            {
                "current": 90.0,
                "interval_open": 101.0,
                "interval_high": 102.0,
                "interval_low": 89.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
        ),
    ],
)
def test_cross_from_safe_open_proves_entry_before_same_interval_target(
    tracker, ticker, direction, observation
):
    row = _base_row(
        Ticker=ticker,
        Preis=100.0,
        price_observed_at=None,
        price_source=None,
        fill_evidence_verified=False,
    )
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "TP2_HIT"
    assert signal["entry_fill_price"] == pytest.approx(100.0)
    assert signal["r_realized"] == pytest.approx(2.0)


def test_crypto_same_interval_stop_and_target_is_conservatively_stopped(tracker):
    assert tracker.record_alert_signals("crypto_explosion", [_base_row(Ticker="ETH")]) == 1
    observation = {
        "current": 100.0,
        "interval_open": 100.0,
        "interval_high": 111.0,
        "interval_low": 94.0,
        "interval_complete": True,
        "source": "binance_5m",
    }
    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal("ETH")
    assert signal["status"] == "STOP_HIT"
    assert signal["r_realized"] == pytest.approx(-1.0)
    assert signal["r_realized_upper"] == pytest.approx(2.0)
    assert signal["outcome_detail"] == "ambiguous_same_interval_stop_first"


def test_crypto_gap_fill_is_rejected_when_slippage_breaks_plan(tracker):
    row = _base_row(Ticker="AVAX", Preis=99.0, coin_id="avalanche-2")
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    observation = {
        "current": 104.5,
        "interval_open": 103.0,
        "interval_high": 104.5,
        "interval_low": 102.0,
        "interval_complete": True,
        "source": "binance_5m",
    }
    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal("AVAX")
    assert signal["status"] == "NO_FILL"
    assert signal["entry_filled_at"] is None
    assert signal["outcome_detail"].startswith("adverse_fill_slippage|")
    assert "adverse_slippage_r=0.6000" in signal["outcome_detail"]


def test_crypto_coingecko_point_fallback_cannot_create_new_fill(tracker):
    row = _base_row(Ticker="LINK", Preis=99.0, coin_id="chainlink")
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    observation = {
        "current": 102.0,
        "interval_high": 102.0,
        "interval_low": 102.0,
        "interval_complete": False,
        "source": "coingecko_point_fallback",
    }

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    signal = _signal("LINK")
    assert signal["status"] == "OPEN"
    assert signal["entry_filled_at"] is None
    assert signal["entry_fill_price"] is None


def test_crypto_gap_through_stop_records_observed_slippage(tracker):
    assert tracker.record_alert_signals("crypto_explosion", [_base_row(Ticker="XRP")]) == 1
    observation = {
        "current": 92.0,
        "interval_open": 93.0,
        "interval_high": 94.0,
        "interval_low": 91.0,
        "interval_complete": True,
        "source": "binance_5m",
    }
    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )
    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal("XRP")
    assert signal["status"] == "STOP_HIT"
    assert signal["r_realized"] == pytest.approx(-1.4)
    assert signal["outcome_detail"] == "stop_gap_slippage"


@pytest.mark.parametrize(
    ("ticker", "direction", "observation", "expected_exit"),
    [
        (
            "GAPTARGETLONG",
            "LONG",
            {
                "current": 100.0,
                "interval_open": 93.0,
                "interval_high": 111.0,
                "interval_low": 92.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
            93.0,
        ),
        (
            "GAPTARGETSHORT",
            "SHORT",
            {
                "current": 100.0,
                "interval_open": 107.0,
                "interval_high": 108.0,
                "interval_low": 89.0,
                "interval_complete": True,
                "source": "binance_5m",
            },
            107.0,
        ),
    ],
)
def test_gap_through_stop_cannot_gain_same_interval_target_upper_bound(
    tracker, ticker, direction, observation, expected_exit
):
    row = _base_row(Ticker=ticker)
    if direction == "SHORT":
        row.update({
            "Signal_Direction": "SHORT",
            "StopLoss": 105.0,
            "TP1": 95.0,
            "TP2": 90.0,
            "price_mode": "bid",
        })
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1

    result = tracker.evaluate_open_signals(
        crypto_price_fetcher=lambda _ticker, **_kwargs: observation
    )

    assert result == {"evaluated": 1, "closed": 1, "errors": 0}
    signal = _signal(ticker)
    assert signal["status"] == "STOP_HIT"
    assert signal["outcome_detail"] == "stop_gap_slippage"
    assert signal["exit_fill_price"] == pytest.approx(expected_exit)
    assert signal["r_realized"] == pytest.approx(-1.4)
    assert signal["r_realized_upper"] == pytest.approx(-1.4)
    assert signal["stop_gap_slippage_r"] == pytest.approx(0.4)


def test_crypto_evaluation_passes_exact_instrument_identity(tracker):
    row = _base_row(
        Ticker="UP",
        Preis=99.0,
        coin_id="superform",
        venue="binance",
        contract_symbol="UPUSDT",
    )
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    seen = {}

    def fetcher(ticker, instrument_id=None, venue=None, contract_symbol=None):
        seen.update({
            "ticker": ticker,
            "instrument_id": instrument_id,
            "venue": venue,
            "contract_symbol": contract_symbol,
        })
        return 101.0

    result = tracker.evaluate_open_signals(crypto_price_fetcher=fetcher)
    assert result["evaluated"] == 1
    assert result["errors"] == 0
    assert seen == {
        "ticker": "UP",
        "instrument_id": "superform",
        "venue": "binance",
        "contract_symbol": "UPUSDT",
    }


def test_crypto_point_beyond_stop_cannot_prove_invalidation_order(tracker):
    row = _base_row(Ticker="DOGE", Preis=99.0, coin_id="dogecoin")
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    result = tracker.evaluate_open_signals(crypto_price_fetcher=lambda _ticker: 94.0)
    assert result["closed"] == 0
    signal = _signal("DOGE")
    assert signal["status"] == "OPEN"
    assert signal["outcome_detail"] == ""
    assert signal["entry_filled_at"] is None


def test_entry_delivery_intent_has_stable_id_and_activates_only_after_acceptance(tracker):
    row = _base_row(Ticker="INTENT")
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout",
        [row],
        "mail-batch-123",
        mail_channel="stocks_swing",
    )
    assert prepared["prepared"] is True
    assert len(prepared["signal_ids"]) == 1
    signal_id = prepared["signal_ids"][0]
    created_at = prepared["signals"][0]["created_at"]
    assert prepared["signals"][0]["status"] == tracker.STATUS_PENDING_DELIVERY
    assert prepared["signals"][0]["mail_channel"] == "stocks_swing"
    assert _signal("INTENT")["entry_filled_at"] is None
    assert tracker.get_signal_count() == 0

    retry = tracker.prepare_alert_delivery_intent(
        "breakout",
        [row],
        "mail-batch-123",
        mail_channel="stocks_swing",
    )
    assert retry["signal_ids"] == [signal_id]
    assert retry["signals"][0]["created_at"] == created_at
    assert tracker.evaluate_open_signals(stock_daily_fetcher=lambda *_: [])[
        "evaluated"
    ] == 0

    recipient_key = "d" * 64
    finalized = tracker.finalize_alert_delivery(
        "mail-batch-123", [recipient_key], accepted_at=datetime.now(timezone.utc)
    )
    assert finalized["accepted"] is True
    assert finalized["activated"] is True
    sig = _signal("INTENT")
    assert sig["id"] == signal_id
    assert sig["created_at"] == created_at
    assert sig["status"] == "OPEN"
    assert sig["delivery_state"] == "ACTIVE"
    assert sig["mail_channel"] == "stocks_swing"
    assert sig["delivery_recipient_keys_json"] == f'["{recipient_key}"]'
    assert tracker.get_signal_count() == 1
    assert tracker.load_pending_accepted_deliveries() == []
    assert tracker.finalize_alert_delivery("mail-batch-123", [recipient_key])[
        "activated"
    ] is True
    replay = tracker.prepare_alert_delivery_intent(
        "breakout",
        [row],
        "mail-batch-123",
        mail_channel="stocks_swing",
    )
    assert replay["intent_state"] == "ACTIVE"
    assert replay["prepared"] is False
    assert replay["send_allowed"] is False
    assert replay["already_accepted"] is True
    assert replay["active"] is True
    # Ein alter Post-SMTP-Recorder darf denselben aktivierten Plan nicht ein
    # zweites Mal anlegen.
    assert tracker.record_alert_signals("breakout", [row]) == 0
    assert tracker.get_signal_count() == 1


def test_entry_intent_market_path_starts_at_smtp_acceptance(
    tracker, monkeypatch
):
    prepared_at = datetime(2026, 8, 11, 14, 0, tzinfo=timezone.utc)
    accepted_at = prepared_at + timedelta(minutes=10)
    observed_at = accepted_at + timedelta(minutes=5)
    monkeypatch.setattr(tracker, "_utc_now", lambda: prepared_at)
    prepared = tracker.prepare_alert_delivery_intent(
        "stock_strategy",
        [_base_row(
            Ticker="CAUSALSTART",
            price_observed_at=prepared_at.isoformat(),
        )],
        "mail-batch-causal-start",
        mail_channel="stocks_swing",
    )
    assert prepared["prepared"] is True
    finalized = tracker.finalize_alert_delivery(
        "mail-batch-causal-start",
        ["a" * 64],
        accepted_at=accepted_at.timestamp(),
    )
    assert finalized["activated"] is True
    signal = _signal("CAUSALSTART")
    assert signal["created_at"] == prepared_at.isoformat()
    assert signal["delivery_accepted_at"] == accepted_at.isoformat()
    assert signal["entry_filled_at"] is None
    calls = []

    def interval_fetcher(_ticker, **kwargs):
        calls.append(kwargs)
        since = datetime.fromisoformat(kwargs["since"])
        # A buggy preparation-time cursor would expose this pre-acceptance
        # stop. The accepted signal is actionable only from accepted_at.
        if since < accepted_at:
            started = prepared_at
            low = 94.0
        else:
            started = accepted_at
            low = 99.0
        interval = {
            "current": 100.0,
            "interval_open": 100.0,
            "interval_high": 101.0,
            "interval_low": low,
            "interval_complete": True,
            "source": "polygon_5m",
            "started_at": started.isoformat(),
            "observed_at": (started + timedelta(minutes=5)).isoformat(),
        }
        return {
            "current": interval["current"],
            "interval_complete": True,
            "source": "polygon_5m",
            "intervals": [interval],
        }

    result = tracker.evaluate_open_signals(
        stock_intraday_fetcher=interval_fetcher,
        now=observed_at,
    )

    signal = _signal("CAUSALSTART")
    assert result == {"evaluated": 1, "closed": 0, "errors": 0}
    assert calls[0]["since"] == accepted_at.isoformat()
    assert signal["status"] == "OPEN"
    assert signal["entry_filled_at"] == observed_at.isoformat()


def test_delivery_intent_key_is_deterministic_and_row_order_stable(tracker):
    row_a = _base_row(Ticker="KEY-A", extra={"b": 2, "a": 1})
    row_b = _base_row(Ticker="KEY-B", Preis=101.25)
    first = tracker.build_alert_delivery_intent_key(
        " Breakout ",
        [row_a, row_b],
        channel="EMAIL",
        mail_channel="stocks_swing",
    )
    reordered_a = {key: row_a[key] for key in reversed(list(row_a))}
    second = tracker.build_alert_delivery_intent_key(
        "breakout",
        [row_b, reordered_a],
        channel="email",
        mail_channel="STOCKS_SWING",
    )

    assert first == second
    assert first.startswith("signal-entry-")
    assert len(first.removeprefix("signal-entry-")) == 64
    assert tracker.build_alert_delivery_intent_key(
        "breakout",
        [row_a, row_b],
        channel="telegram",
        mail_channel="stocks_swing",
    ) != first
    assert tracker.build_alert_delivery_intent_key(
        "breakout",
        [row_a, row_b],
        channel="email",
        mail_channel="stocks_premarket",
    ) != first


def test_pending_accepted_entry_delivery_is_reconcilable(tracker):
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout",
        [_base_row(Ticker="RECON")],
        "mail-batch-reconcile",
        mail_channel="stocks_premarket",
    )
    signal_id = prepared["signal_ids"][0]
    recipient_key = "e" * 64
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET delivery_state='ACCEPTED_PENDING', "
            "delivery_accepted_at=?, delivery_recipient_keys_json=? WHERE id=?",
            (
                datetime.now(timezone.utc).isoformat(),
                f'["{recipient_key}"]',
                signal_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()

    pending = tracker.load_pending_accepted_deliveries()
    assert len(pending) == 1
    assert pending[0]["intent_key"] == "mail-batch-reconcile"
    assert pending[0]["signal_ids"] == [signal_id]
    assert pending[0]["mail_channel"] == "stocks_premarket"
    replay = tracker.prepare_alert_delivery_intent(
        "breakout",
        [_base_row(Ticker="RECON")],
        "mail-batch-reconcile",
        mail_channel="stocks_premarket",
    )
    assert replay["intent_state"] == "ACCEPTED_PENDING"
    assert replay["prepared"] is False
    assert replay["send_allowed"] is False
    assert replay["already_accepted"] is True
    reconciled = tracker.finalize_alert_delivery(
        "mail-batch-reconcile", [recipient_key]
    )
    assert reconciled["activated"] is True
    assert _signal("RECON")["status"] == "OPEN"


def test_independent_acceptance_journal_recovers_tracker_db_outage(
    tracker, monkeypatch, tmp_path
):
    intent_key = "intent-journal-outage"
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="JOURNAL")], intent_key
    )
    assert prepared["prepared"] is True
    assert tracker.mark_alert_delivery_attempted(intent_key)[
        "claimed_this_call"
    ] is True
    tracker_db = tracker.SIGNAL_DB_PATH
    blocker = tmp_path / "tracker-db-blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        tracker, "SIGNAL_DB_PATH", str(blocker / "unavailable.sqlite")
    )

    recipient_key = "c" * 64
    accepted_at = datetime.now(timezone.utc)
    result = tracker.finalize_alert_delivery(
        intent_key, [recipient_key], accepted_at=accepted_at
    )

    assert result["accepted"] is True
    assert result["journaled"] is True
    assert result["durable_acceptance"] is True
    assert result["tracker_acceptance_persisted"] is False
    assert result["activated"] is False
    assert result["tracker_pending"] is True
    health = tracker.load_delivery_acceptance_health()
    assert health["status"] == "degraded"
    assert health["tracker_pending"] is True
    assert health["pending_count"] == 1
    pending = tracker.load_pending_accepted_deliveries()
    assert len(pending) == 1
    assert pending[0]["intent_key"] == intent_key
    assert pending[0]["journal_persisted"] is True
    assert pending[0]["tracker_persisted"] is False

    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", tracker_db)
    reconciled = tracker.reconcile_pending_accepted_deliveries()
    assert len(reconciled) == 1
    assert reconciled[0]["activated"] is True
    assert _signal("JOURNAL")["status"] == "OPEN"
    assert _signal("JOURNAL")["delivery_accepted_at"] == accepted_at.isoformat()
    assert tracker.load_pending_accepted_deliveries() == []
    health = tracker.load_delivery_acceptance_health()
    assert health["status"] == "ok"
    assert health["pending_count"] == 0
    assert health["reconciled_count"] == 1


def test_delivery_health_counts_legacy_open_email_rows_without_cohort(tracker):
    assert tracker.record_alert_signals(
        "breakout", [_base_row(Ticker="LEGACY")], channel="email"
    ) == 1
    intent_key = "intent-cohort-known"
    assert tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="KNOWN")], intent_key
    )["prepared"] is True
    assert tracker.mark_alert_delivery_attempted(intent_key)[
        "claimed_this_call"
    ] is True
    assert tracker.finalize_alert_delivery(
        intent_key, ["f" * 64], accepted_at=datetime.now(timezone.utc)
    )["activated"] is True

    health = tracker.load_delivery_acceptance_health()

    assert health["status"] == "degraded"
    assert health["legacy_cohort_check_available"] is True
    assert health["legacy_open_cohort_unknown_count"] == 1


def test_partial_smtp_attempt_journal_preserves_earliest_data_acceptance(
    tracker,
):
    intent_key = "intent-partial-attempts"
    assert tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="PARTIAL")], intent_key
    )["prepared"] is True
    assert tracker.mark_alert_delivery_attempted(intent_key)[
        "claimed_this_call"
    ] is True
    first_key = "1" * 64
    retry_key = "2" * 64
    first_accepted_at = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)
    retry_accepted_at = first_accepted_at + timedelta(seconds=20)

    first = tracker.journal_alert_delivery_acceptance(
        intent_key, [first_key], accepted_at=first_accepted_at
    )
    retry = tracker.journal_alert_delivery_acceptance(
        intent_key, [retry_key], accepted_at=retry_accepted_at.timestamp()
    )
    finalized = tracker.finalize_alert_delivery(
        intent_key,
        [first_key, retry_key],
        accepted_at=retry_accepted_at,
    )

    expected_keys_json = f'["{first_key}","{retry_key}"]'
    assert first["accepted_at"] == first_accepted_at.isoformat()
    assert retry["accepted_at"] == first_accepted_at.isoformat()
    assert retry["delivery_recipient_keys_json"] == expected_keys_json
    assert finalized["accepted_at"] == first_accepted_at.isoformat()
    assert finalized["delivery_recipient_keys_json"] == expected_keys_json
    assert finalized["activated"] is True
    signal = _signal("PARTIAL")
    assert signal["delivery_accepted_at"] == first_accepted_at.isoformat()
    assert signal["delivery_recipient_keys_json"] == expected_keys_json

    # Even an out-of-order durable replay must move neither journal nor active
    # signal forward; the earliest known successful DATA attempt is causal.
    still_earlier = first_accepted_at - timedelta(seconds=5)
    replay = tracker.finalize_alert_delivery(
        intent_key, [first_key], accepted_at=still_earlier
    )
    assert replay["activated"] is True
    assert replay["accepted_at"] == still_earlier.isoformat()
    assert _signal("PARTIAL")["delivery_accepted_at"] == still_earlier.isoformat()


def test_acceptance_journal_rejects_malformed_explicit_timestamp(tracker):
    result = tracker.journal_alert_delivery_acceptance(
        "intent-invalid-time", ["3" * 64], accepted_at="not-a-time"
    )

    assert result["journaled"] is False
    assert result["durable_acceptance"] is False
    assert result["error"] == "invalid_acceptance_evidence"


def test_acceptance_requires_external_outbox_if_both_sqlite_stores_fail(
    tracker, monkeypatch, tmp_path
):
    intent_key = "intent-double-storage-failure"
    assert tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="NOJOURNAL")], intent_key
    )["prepared"] is True
    assert tracker.mark_alert_delivery_attempted(intent_key)[
        "claimed_this_call"
    ] is True
    blocker = tmp_path / "all-storage-blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setattr(
        tracker, "SIGNAL_DB_PATH", str(blocker / "signals.sqlite")
    )
    monkeypatch.setattr(
        tracker,
        "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
        str(blocker / "acceptance.sqlite"),
    )

    result = tracker.finalize_alert_delivery(intent_key, ["d" * 64])

    assert result["accepted"] is False
    assert result["journaled"] is False
    assert result["durable_acceptance"] is False
    assert result["tracker_pending"] is False
    assert result["error"]


def test_prepared_intent_cancel_and_stale_cleanup_never_delete_accepted(tracker):
    now = datetime.now(timezone.utc)
    stale = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="STALE")], "intent-stale"
    )
    fresh = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="FRESH-I")], "intent-fresh"
    )
    accepted = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="ACCEPT-I")], "intent-accepted"
    )
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        old = (now - timedelta(hours=2)).isoformat()
        conn.execute(
            "UPDATE signals SET delivery_prepared_at=? WHERE id IN (?, ?)",
            (old, stale["signal_ids"][0], accepted["signal_ids"][0]),
        )
        conn.execute(
            "UPDATE signals SET delivery_state='ACCEPTED_PENDING', "
            "delivery_accepted_at=?, delivery_recipient_keys_json=? WHERE id=?",
            (old, f'["{"f" * 64}"]', accepted["signal_ids"][0]),
        )
        conn.commit()
    finally:
        conn.close()

    assert tracker.cleanup_stale_prepared_delivery_intents(30, now=now) == 1
    assert _signal("FRESH-I")["id"] == fresh["signal_ids"][0]
    assert _signal("ACCEPT-I")["id"] == accepted["signal_ids"][0]
    assert tracker.cancel_alert_delivery_intent("intent-accepted") == 0
    assert tracker.cancel_alert_delivery_intent("intent-fresh") == 1
    assert tracker.cancel_alert_delivery_intent("intent-fresh") == 0


def test_attempted_unknown_intent_is_not_replayed_or_auto_expired(tracker):
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="ATTEMPTED")], "intent-attempted"
    )
    signal_id = prepared["signal_ids"][0]
    attempt = tracker.mark_alert_delivery_attempted(
        "intent-attempted",
        attempted_at=datetime.now(timezone.utc) - timedelta(hours=2),
    )
    assert attempt["attempted"] is True
    assert attempt["claimed"] is True
    assert attempt["claimed_this_call"] is True
    assert attempt["send_allowed"] is True
    assert attempt["intent_state"] == "ATTEMPTED_UNKNOWN"
    duplicate_attempt = tracker.mark_alert_delivery_attempted("intent-attempted")
    assert duplicate_attempt["attempted"] is False
    assert duplicate_attempt["claimed"] is False
    assert duplicate_attempt["claimed_this_call"] is False
    assert duplicate_attempt["send_allowed"] is False
    assert duplicate_attempt["manual_reconciliation_required"] is True
    replay = tracker.prepare_alert_delivery_intent(
        "breakout", [_base_row(Ticker="ATTEMPTED")], "intent-attempted"
    )
    assert replay["intent_state"] == "ATTEMPTED_UNKNOWN"
    assert replay["send_allowed"] is False
    assert replay["already_accepted"] is False

    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET delivery_prepared_at=? WHERE id=?",
            ((datetime.now(timezone.utc) - timedelta(hours=2)).isoformat(), signal_id),
        )
        conn.commit()
    finally:
        conn.close()
    assert tracker.cleanup_stale_prepared_delivery_intents(30) == 0
    assert _signal("ATTEMPTED")["delivery_state"] == "ATTEMPTED"
    assert tracker.cancel_alert_delivery_intent("intent-attempted") == 0
    assert tracker.cancel_alert_delivery_intent(
        "intent-attempted", delivery_definitively_not_accepted=True
    ) == 1


def test_delivery_attempt_claim_has_exactly_one_worker_owner(tracker):
    prepared = tracker.prepare_alert_delivery_intent(
        "breakout",
        [
            _base_row(Ticker="CAS-A"),
            _base_row(Ticker="CAS-B"),
        ],
        "intent-two-workers",
    )
    assert len(prepared["signal_ids"]) == 2

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(
            lambda _index: tracker.mark_alert_delivery_attempted(
                "intent-two-workers"
            ),
            range(2),
        ))

    owners = [result for result in results if result["claimed_this_call"]]
    denied = [result for result in results if not result["claimed_this_call"]]
    assert len(owners) == 1
    assert owners[0]["attempted"] is True
    assert owners[0]["send_allowed"] is True
    assert owners[0]["signal_ids"] == prepared["signal_ids"]
    assert len(denied) == 1
    assert denied[0]["attempted"] is False
    assert denied[0]["send_allowed"] is False
    assert denied[0]["intent_state"] == "ATTEMPTED_UNKNOWN"
    assert denied[0]["manual_reconciliation_required"] is True
    rows = _db_rows(
        "SELECT delivery_state FROM signals WHERE delivery_intent_key LIKE ?",
        ("intent-two-workers:%",),
    )
    assert [row["delivery_state"] for row in rows] == ["ATTEMPTED", "ATTEMPTED"]


# ── load_performance_summary / get_signal_count ──────────────────────────────
def test_performance_summary_math(tracker):
    rows = [_base_row(Ticker=t) for t in ("WIN", "LOSS", "RUN", "PART")]
    assert tracker.record_alert_signals("breakout", rows) == 4
    assert tracker.record_alert_signals("bi_scanner", [_base_row(Ticker="OTHER")]) == 1
    bars = {
        "WIN": _bars_after("WIN", [(111.0, 99.0, 110.0)]),    # Entry handelbar, TP2 -> +2.0R
        "LOSS": _bars_after("LOSS", [(101.0, 94.0, 95.0)]),   # Stop -> -1.0R
        "RUN": _bars_after("RUN", [(103.0, 99.0, 102.0)]),    # bleibt OPEN
        "PART": _bars_after(                                   # TP1, dann EXPIRED (+0.4R)
            "PART", [(106.0, 99.0, 105.0)] + [(103.0, 99.0, 102.0)] * 4
        ),
        # "OTHER" fehlt absichtlich -> Fehlversuch, bleibt OPEN
    }
    tracker.evaluate_open_signals(stock_daily_fetcher=_stock_fetcher(bars))
    summary = tracker.load_performance_summary(days=30)
    assert summary["window_days"] == 30
    assert summary["generated_at"]
    bucket = summary["per_scanner"]["breakout"]
    assert bucket["signals"] == 4
    assert bucket["open"] == 1
    assert bucket["tp2_hit"] == 1
    assert bucket["stop_hit"] == 1
    assert bucket["tp1_hit"] == 1   # TP1 erreicht, dann ausgelaufen (Teilgewinner)
    assert bucket["expired"] == 0
    assert bucket["untracked"] == 0
    # wins = tp1_hit + tp2_hit = 2; decided = wins + stop_hit = 3
    assert bucket["win_rate_pct"] == pytest.approx(66.7, abs=0.05)
    # r-Werte: +2.0 (TP2), -1.0 (Stop), +0.4 (PART: Close 102 -> (102-100)/5)
    assert bucket["avg_r"] == pytest.approx((2.0 - 1.0 + 0.4) / 3.0, abs=1e-3)
    assert bucket["sum_r"] == pytest.approx(1.4, abs=1e-3)
    # WIN/PART liefen durch den +1R-Trigger, aber dieser isolierte Tracker-Test
    # belegt keine Stop-Update-Zustellung. Beide Vorzeichen werden deshalb
    # outcome-neutral als unresolved gesperrt; nur LOSS (<1R MFE) bleibt drin.
    assert bucket["managed_be_decided_signals"] == 1
    assert bucket["managed_be_wins"] == 0
    assert bucket["managed_be_losses"] == 1
    assert bucket["managed_be_breakevens"] == 0
    assert bucket["managed_be_unresolved"] == 2
    assert bucket["managed_be_sample_reliable"] is False
    assert bucket["avg_r_managed_50_50_be"] == pytest.approx(-1.0)
    assert bucket["sum_r_managed_50_50_be"] == pytest.approx(-1.0)
    assert bucket["profit_factor_managed_be"] == pytest.approx(0.0)
    assert bucket["alerts_per_day"] == pytest.approx(4.0 / 30.0, abs=1e-3)
    assert summary["per_scanner"]["bi_scanner"]["signals"] == 1
    assert summary["per_scanner"]["bi_scanner"]["win_rate_pct"] is None
    total = summary["total"]
    assert total["signals"] == 5
    assert total["open"] == 2
    assert total["tp2_hit"] == 1 and total["stop_hit"] == 1 and total["tp1_hit"] == 1
    assert len(summary["recent"]) == 5
    assert {r["ticker"] for r in summary["recent"]} == {"WIN", "LOSS", "RUN", "PART", "OTHER"}
    for entry in summary["recent"]:
        for key in ("created_at", "scanner", "status", "entry", "stop", "r_realized"):
            assert key in entry


def test_recommended_payoff_statistics_treats_breakevens_consistently(tracker):
    stats = tracker._recommended_payoff_statistics([1.5, -1.0, 0.0])
    assert stats["decided"] == 3
    assert stats["wins"] == 1
    assert stats["losses"] == 1
    assert stats["breakevens"] == 1
    assert stats["win_rate_pct"] == pytest.approx(33.3, abs=0.05)
    assert stats["win_rate_ex_breakeven_pct"] == pytest.approx(50.0)
    assert stats["breakeven_outcome_rate_pct"] == pytest.approx(33.3, abs=0.05)
    # Bedingte Schwelle ohne 0R = 1 / (1.5 + 1) = 40%.
    assert stats["breakeven_win_rate_ex_breakeven_pct"] == pytest.approx(40.0)
    # Gesamt-Schwelle mit beobachteter 0R-Quote = 40% * 2/3.
    assert stats["breakeven_win_rate_pct"] == pytest.approx(26.7, abs=0.05)
    assert stats["avg_r"] == pytest.approx(1.0 / 6.0, abs=1e-3)
    assert stats["profit_factor"] == pytest.approx(1.5)


def test_mature_summary_excludes_right_censored_recent_signals(tracker):
    as_of = datetime.now(timezone.utc)
    assert tracker.record_alert_signals(
        "breakout",
        [_base_row(Ticker="MATURE"), _base_row(Ticker="FRESH")],
    ) == 2
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, created_at=? "
            "WHERE ticker='MATURE'",
            ((as_of - timedelta(days=30)).isoformat(),),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, created_at=? "
            "WHERE ticker='FRESH'",
            ((as_of - timedelta(days=2)).isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    summary = tracker.load_performance_summary(
        days=30,
        mature_only=True,
        as_of=as_of,
    )
    assert summary["cohort_mode"] == "fully_observed"
    assert summary["excluded_not_mature"] == 1
    assert summary["total"]["signals"] == 1
    assert summary["total"]["managed_be_decided_signals"] == 1
    assert [row["ticker"] for row in summary["recent"]] == ["MATURE"]


def test_breaker_recovery_summary_rejects_right_censored_post_trip_rows(tracker):
    since = datetime.now(timezone.utc) - timedelta(hours=1)
    before_trip = since - timedelta(days=1)
    now_iso = datetime.now(timezone.utc).isoformat()
    rows = [
        _base_row(Ticker="POSTWIN"),
        _base_row(Ticker="POSTLOSS"),
        _base_row(Ticker="OLDWIN"),
    ]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == 3
    assert tracker.record_alert_signals(
        "breakout", [_base_row(Ticker="SHADOWWIN")], mail_class="shadow"
    ) == 1
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, created_at=? "
            "WHERE ticker='POSTWIN'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, created_at=? "
            "WHERE ticker='POSTLOSS'",
            (now_iso,),
        )
        conn.execute(
            "UPDATE signals SET status='EXPIRED', r_realized=0.5, "
            "tp1_hit_at=?, created_at=? "
            "WHERE ticker='SHADOWWIN'",
            (now_iso, now_iso),
        )
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=5.0, created_at=? "
            "WHERE ticker='OLDWIN'",
            (before_trip.isoformat(),),
        )
        conn.commit()
    finally:
        conn.close()

    summary = tracker.load_breaker_recovery_summary("breakout", since)
    assert summary["available"] is False
    assert summary["joint_cell_verified"] is True
    assert summary["decided"] == 0
    assert summary["fully_observed_post_trip"] == 0
    assert summary["error"] == "insufficient_joint_cell_sample"
    assert summary["r_model"] == "managed_50_50_plus_be_actual_or_shadow_counterfactual"


def test_breaker_recovery_requires_thirty_mature_cases_in_one_joint_cell(tracker):
    as_of = datetime.now(timezone.utc)
    since = as_of - timedelta(days=45)
    created = (as_of - timedelta(days=30)).isoformat()
    rows = [
        _base_row(
            Ticker=f"CELL{i:02d}",
            trade_horizon="swing",
            evaluation_horizon_bars=5,
            market_regime="GREEN",
        )
        for i in range(30)
    ]
    assert tracker.record_alert_signals("breakout", rows, mail_class="trade") == 30
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "entry_filled_at=?, entry_fill_price=entry, created_at=? "
            "WHERE ticker LIKE 'CELL%'",
            (created, created),
        )
        conn.commit()
    finally:
        conn.close()

    summary = tracker.load_breaker_recovery_summary(
        "breakout", since, as_of=as_of
    )
    assert summary["joint_cell_verified"] is True
    assert summary["cell_id"] == "breakout|LONG|swing:5bars|GREEN"
    assert summary["fully_observed_post_trip"] == 30
    assert summary["decided"] == 30
    assert summary["managed_be_unresolved"] == 0
    assert summary["available"] is True

    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute("DELETE FROM signals WHERE ticker='CELL29'")
        conn.commit()
    finally:
        conn.close()
    insufficient = tracker.load_breaker_recovery_summary(
        "breakout", since, as_of=as_of
    )
    assert insufficient["decided"] == 29
    assert insufficient["available"] is False
    assert insufficient["error"] == "insufficient_joint_cell_sample"

    assert tracker.record_alert_signals(
        "breakout",
        [
            _base_row(
                Ticker="OTHER-CELL",
                Signal_Direction="SHORT",
                StopLoss=105.0,
                TP1=95.0,
                TP2=90.0,
                trade_horizon="swing",
                evaluation_horizon_bars=5,
                market_regime="GREEN",
            )
        ],
    ) == 1
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='STOP_HIT', r_realized=-1.0, "
            "entry_filled_at=?, entry_fill_price=entry, created_at=? "
            "WHERE ticker='OTHER-CELL'",
            (created, created),
        )
        conn.commit()
    finally:
        conn.close()
    ambiguous = tracker.load_breaker_recovery_summary(
        "breakout", since, as_of=as_of
    )
    assert ambiguous["available"] is False
    assert ambiguous["joint_cell_verified"] is False
    assert ambiguous["error"] == "joint_cell_ambiguous"
    assert len(ambiguous["joint_cell_candidates"]) == 2

    selected = tracker.load_breaker_recovery_summary(
        "breakout",
        since,
        direction="LONG",
        horizon="swing:5bars",
        market_regime="GREEN",
        as_of=as_of,
    )
    assert selected["joint_cell_verified"] is True
    assert selected["decided"] == 29
    assert selected["available"] is False


def test_breaker_recovery_accepts_thirty_causal_shadow_tp2_counterfactuals(
    tracker,
):
    as_of = datetime.now(timezone.utc)
    since = as_of - timedelta(days=45)
    created_dt = as_of - timedelta(days=30)
    created = created_dt.isoformat()
    tp1_at = (created_dt + timedelta(days=2)).isoformat()
    tp2_at = (created_dt + timedelta(days=3)).isoformat()
    rows = [
        _base_row(
            Ticker=f"SHADOW-REC-{i:02d}",
            trade_horizon="swing",
            evaluation_horizon_bars=5,
            market_regime="RISK_ON",
        )
        for i in range(30)
    ]
    assert tracker.record_alert_signals(
        "breakout", rows, mail_class="shadow"
    ) == 30
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "entry_filled_at=?, entry_fill_price=entry, created_at=?, "
            "tp1_hit_at=?, tp2_hit_at=?, max_favorable_r=2.0, "
            "be_trigger_at=?, be_activated_at=?, be_mail_sent_at=NULL "
            "WHERE ticker LIKE 'SHADOW-REC-%'",
            (created, created, tp1_at, tp2_at, tp1_at, tp1_at),
        )
        conn.commit()
    finally:
        conn.close()

    summary = tracker.load_breaker_recovery_summary(
        "breakout",
        since,
        direction="LONG",
        horizon="swing:5bars",
        market_regime="RISK_ON",
        as_of=as_of,
    )
    assert summary["available"] is True
    assert summary["decided"] == 30
    assert summary["shadow_decided"] == 30
    assert summary["shadow_counterfactual_decided"] == 30
    assert summary["actual_delivery_decided"] == 0
    assert summary["managed_be_unresolved"] == 0
    assert summary["avg_r"] == pytest.approx(1.5)
    assert all(row["be_mail_sent_at"] is None for row in _db_rows())


def test_breaker_recovery_malformed_managed_geometry_blocks_release(tracker):
    as_of = datetime.now(timezone.utc)
    since = as_of - timedelta(days=45)
    created_dt = as_of - timedelta(days=30)
    created = created_dt.isoformat()
    tp1_at = (created_dt + timedelta(days=2)).isoformat()
    tp2_at = (created_dt + timedelta(days=3)).isoformat()
    rows = [
        _base_row(
            Ticker=f"CONTROL-{i:02d}",
            trade_horizon="swing",
            evaluation_horizon_bars=5,
            market_regime="RISK_ON",
        )
        for i in range(31)
    ]
    assert tracker.record_alert_signals(
        "breakout", rows, mail_class="shadow"
    ) == 31
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    try:
        conn.execute(
            "UPDATE signals SET status='TP2_HIT', r_realized=2.0, "
            "entry_filled_at=?, entry_fill_price=entry, created_at=?, "
            "tp1_hit_at=?, tp2_hit_at=?, max_favorable_r=2.0 "
            "WHERE ticker LIKE 'CONTROL-%'",
            (created, created, tp1_at, tp2_at),
        )
        # Legacy/malformed example: plausible-looking Level-R and TP marker,
        # but no complete Entry/Stop/TP geometry.
        conn.execute(
            "UPDATE signals SET r_realized=1.5, entry_fill_price=NULL, "
            "tp2=tp1 WHERE ticker='CONTROL-30'"
        )
        conn.commit()
    finally:
        conn.close()

    summary = tracker.load_breaker_recovery_summary(
        "breakout",
        since,
        direction="LONG",
        horizon="swing:5bars",
        market_regime="RISK_ON",
        as_of=as_of,
    )
    assert summary["decided"] == 30
    assert summary["managed_be_unresolved"] == 1
    assert summary["available"] is False
    assert summary["error"] == "managed_be_unresolved"


def test_summary_and_count_safe_on_empty_or_broken_db(tracker, tmp_path, monkeypatch):
    summary = tracker.load_performance_summary()
    assert summary["window_days"] == 90
    assert summary["total"]["signals"] == 0
    assert summary["total"]["win_rate_pct"] is None
    assert summary["total"]["avg_r"] is None
    assert summary["per_scanner"] == {}
    assert summary["recent"] == []
    assert tracker.get_signal_count() == 0
    # kaputter DB-Pfad: alles bleibt exception-frei
    blocker = tmp_path / "blockfile"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setattr(tracker, "SIGNAL_DB_PATH", str(blocker / "nope" / "db.sqlite"))
    summary2 = tracker.load_performance_summary()
    assert summary2["total"]["signals"] == 0
    assert tracker.get_signal_count() == -1  # Fehlerindikator fuer Health-Checks
    assert tracker.evaluate_open_signals(stock_daily_fetcher=lambda t, s: []) == {
        "evaluated": 0, "closed": 0, "errors": 1,
    }
    recovery = tracker.load_breaker_recovery_summary("breakout", datetime.now(timezone.utc))
    assert recovery["available"] is False
    assert recovery["error"]


def test_breaker_recovery_summary_rejects_invalid_input(tracker):
    assert tracker.load_breaker_recovery_summary("", datetime.now(timezone.utc))[
        "error"
    ] == "scanner_missing"
    assert tracker.load_breaker_recovery_summary("breakout", "not-a-date")[
        "error"
    ] == "invalid_since"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
