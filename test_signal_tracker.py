#!/usr/bin/env python3
"""Pytest-Suite fuer modules/signal_tracker.py (API-Kontrakt Signal-Tracking).

Komplett offline: Kursdaten kommen aus injizierten Fake-Fetchern, die DB liegt
in einem pytest-tmp-Verzeichnis (modulglobales SIGNAL_DB_PATH wird pro Test
per monkeypatch ueberschrieben — die Funktionen lesen den Pfad pro Aufruf).
"""
import os
import sqlite3
import sys
from datetime import datetime, timedelta

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


def _bars_after(ticker, specs):
    """Daily-Bars an den Folgetagen des Alerts (Tag +1, +2, ...).

    specs: Liste von (high, low, close)- oder (open, high, low, close)-Tupeln.
    """
    d0 = _created_date(ticker)
    bars = []
    for i, spec in enumerate(specs, start=1):
        if len(spec) == 4:
            open_price, high, low, close = spec
        else:
            high, low, close = spec
            open_price = min(max(100.0, low), high)
        bars.append({
            "date": (d0 + timedelta(days=i)).isoformat(),
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
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
    return st


# ── record_alert_signals ─────────────────────────────────────────────────────
def test_record_basic_fields_and_status(tracker):
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 1
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
    assert sig["eval_fail_count"] == 0
    assert sig["outcome_detail"] == ""
    assert sig["created_at"]
    assert tracker.get_signal_count() == 1


def test_record_only_trade_mail_class(tracker):
    assert tracker.record_alert_signals("breakout", [_base_row()], mail_class="watch") == 0
    assert tracker.record_alert_signals("breakout", [_base_row()], mail_class="info") == 0
    assert tracker.get_signal_count() == 0


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


def test_record_dedupe_open_signal_per_scanner_ticker(tracker):
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 1
    # gleicher (scanner, ticker) noch OPEN -> skip
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 0
    # anderer Scanner darf denselben Ticker loggen
    assert tracker.record_alert_signals("bi_scanner", [_base_row()]) == 1
    # Dedupe greift auch innerhalb EINES Batches
    assert tracker.record_alert_signals("momo", [_base_row(), _base_row()]) == 1
    # geschlossenes Signal blockiert nicht mehr
    conn = sqlite3.connect(st.SIGNAL_DB_PATH)
    conn.execute("UPDATE signals SET status = 'STOP_HIT' WHERE scanner = 'breakout'")
    conn.commit()
    conn.close()
    assert tracker.record_alert_signals("breakout", [_base_row()]) == 1


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
    assert fetcher.calls == [("AAPL", _created_date("AAPL").isoformat())]


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
    assert not sig["tp1_hit_at"]  # TP wird im Zweifel NICHT gutgeschrieben


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
        _base_row(Ticker="PEPE", TP1=102.0),  # Spot ueber TP1, dann Expiry
        _base_row(Ticker="SOL"),              # Spot ueber TP2
    ]
    assert tracker.record_alert_signals("crypto_explosion", rows) == 3
    prices = {"DOGE": 94.0, "PEPE": 103.0, "SOL": 111.0}
    result = tracker.evaluate_open_signals(crypto_price_fetcher=prices.get)
    assert result == {"evaluated": 3, "closed": 2, "errors": 0}
    doge = _signal("DOGE")
    assert doge["status"] == "STOP_HIT"
    assert doge["r_realized"] == pytest.approx(-1.2)  # Spot-Check bereits durch Stop: echte Slippage
    sol = _signal("SOL")
    assert sol["status"] == "TP2_HIT"
    assert sol["r_realized"] == pytest.approx(2.0)
    assert sol["tp1_hit_at"]  # TP2 impliziert TP1
    pepe = _signal("PEPE")
    assert pepe["status"] == "OPEN"
    assert pepe["tp1_hit_at"]
    assert pepe["max_favorable_r"] == pytest.approx(0.6)
    # 121h spaeter (Expiry-Fenster 120h): EXPIRED mit R des letzten Preises
    later = datetime.fromisoformat(pepe["created_at"]) + timedelta(hours=121)
    result2 = tracker.evaluate_open_signals(crypto_price_fetcher=prices.get, now=later)
    assert result2 == {"evaluated": 1, "closed": 1, "errors": 0}
    pepe = _signal("PEPE")
    assert pepe["status"] == "EXPIRED"
    assert pepe["outcome_detail"] == "tp1_then_expired"
    assert pepe["r_realized"] == pytest.approx(0.6)  # (103-100)/5


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


def test_crypto_signal_invalidated_before_entry_is_no_fill(tracker):
    row = _base_row(Ticker="DOGE", Preis=99.0, coin_id="dogecoin")
    assert tracker.record_alert_signals("crypto_explosion", [row]) == 1
    result = tracker.evaluate_open_signals(crypto_price_fetcher=lambda _ticker: 94.0)
    assert result["closed"] == 1
    signal = _signal("DOGE")
    assert signal["status"] == "NO_FILL"
    assert signal["outcome_detail"] == "entry_invalidated_before_fill"
    assert signal["entry_filled_at"] is None


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


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
