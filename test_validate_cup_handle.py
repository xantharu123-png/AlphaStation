"""Offline-Tests fuer deploy/validate_cup_handle.py (Walk-Forward-Validierung).

KEIN Netz: Polygon wird nicht angefasst. Getestet werden die Walk-Forward-
Mechanik (kein Look-ahead, Dedupe-Sperre), die First-Touch-Forward-Auswertung
(R-Werte per Handrechnung, identische Logik wie deploy/backtest_signal_mails.py)
und das CSV-Format. Die Chart-Fixture wird aus test_cup_handle_scanner
wiederverwendet, der Detektor ist der ECHTE aus api.py.
"""
import csv
import datetime as dt
import importlib.util
import os

import api
from test_cup_handle_scanner import _bar, _cup_handle_bars

_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_module():
    # Session-unabhaengig: Pfad via __file__, nicht via cwd oder hardcoded Mounts.
    spec = importlib.util.spec_from_file_location(
        "validate_cup_handle", os.path.join(_HERE, "deploy", "validate_cup_handle.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


vch = _load_module()


def _with_dates(bars, start="2024-06-03"):
    d0 = dt.date.fromisoformat(start)
    out = []
    for i, bar in enumerate(bars):
        item = dict(bar)
        item["date"] = (d0 + dt.timedelta(days=i)).isoformat()
        out.append(item)
    return out


# ---------------------------------------------------------------------------
# Walk-Forward-Mechanik
# ---------------------------------------------------------------------------

def test_walk_forward_no_lookahead_with_real_detector():
    # Synthetische Serie: 150 ruhige Vorlauf-Bars + die Lehrbuch-Fixture, die
    # exakt an ihrem LETZTEN Bar (Tag X) bestaetigt. Der Spy-Wrapper haelt
    # jeden Detektor-Input fest, damit Look-ahead beweisbar ausgeschlossen ist.
    series = _with_dates([_bar(100.0, volume=1_000_000) for _ in range(150)]
                         + _cup_handle_bars())
    x = len(series) - 1

    seen = []

    def spy(bars, current_price=None):
        seen.append(bars)
        return api._detect_cup_handle_breakout(bars, current_price=current_price)

    events = vch.walk_forward("CUPX", series, spy, start_index=235)

    assert len(events) == 1
    ev = events[0]
    assert ev["index"] == x
    assert ev["date"] == series[x]["date"]
    # Der Input des Event-Aufrufs endete exakt an Tag X und war bars[:x+1][-200:].
    confirm_input = seen[-1]
    assert confirm_input[-1] is series[x]
    assert confirm_input == series[:x + 1][-200:]
    assert len(confirm_input) <= 200
    # KEIN Aufruf hat je Bars hinter seinem Stichtag gesehen.
    assert len(seen) == x - 235 + 1
    for t, passed in zip(range(235, x + 1), seen):
        assert passed[-1] is series[t]
        assert len(passed) <= 200
    # Levels kommen 1:1 aus dem Produktions-Detektor.
    assert ev["stop"] < ev["entry"] < ev["tp1"] < ev["tp2"]
    assert ev["score"] >= 80
    assert ev["cup_len"] > 0 and ev["handle_len"] > 0


def test_walk_forward_dedupe_blocks_20_trading_days():
    series = _with_dates([_bar(50.0) for _ in range(260)])
    # 3 Folgetage CONFIRMED (205-207) => genau 1 Event; Tag 228 liegt hinter
    # der 20-Tage-Sperre (205+20=225) und darf wieder feuern.
    for idx in (205, 206, 207, 228):
        series[idx]["close"] = 100.0

    seen = []

    def fake_detect(bars, current_price=None):
        seen.append(bars)
        if bars[-1]["close"] == 100.0:
            return {"entry": 100.0, "stop_loss": 95.0, "tp1": 110.0, "tp2": 120.0,
                    "score": 85, "cup_length": 60, "handle_length": 8,
                    "breakout_rvol": 2.0}
        return None

    events = vch.walk_forward("DEDU", series, fake_detect)

    assert seen[0][-1] is series[200]  # Walk startet ab Bar 200
    assert [ev["index"] for ev in events] == [205, 228]
    # Waehrend der Sperre (206..225) wird der Detektor gar nicht erst gerufen.
    inspected = {id(bars[-1]) for bars in seen}
    assert id(series[206]) not in inspected
    assert id(series[225]) not in inspected
    assert id(series[226]) in inspected


# ---------------------------------------------------------------------------
# Forward-Auswertung (Handrechnung: entry 100, stop 95 => 1R = 5 Punkte)
# ---------------------------------------------------------------------------
E, S, T1, T2 = 100.0, 95.0, 110.0, 120.0


def _fbar(low, high, close):
    return {"date": "x", "open": close, "high": high, "low": low,
            "close": close, "volume": 1.0}


def test_forward_stop_first_is_minus_one_r():
    fwd = [_fbar(94.0, 99.0, 96.0)] + [_fbar(96.0, 99.0, 97.0)] * 5
    res = vch.evaluate_forward(fwd, E, S, T1, T2)
    assert res["outcome"] == "STOP"
    assert res["tp1_hit"] is False
    assert res["r_realized"] == -1.0
    assert res["days_to_outcome"] == 1


def test_forward_tp1_then_tp2_r_exact():
    fwd = [_fbar(99.0, 105.0, 104.0), _fbar(101.0, 111.0, 109.0),
           _fbar(103.0, 112.0, 108.0), _fbar(105.0, 121.0, 119.0)]
    res = vch.evaluate_forward(fwd, E, S, T1, T2)
    # Handrechnung: 0.5*(110-100)/5 + 0.5*(120-100)/5 = 1.0 + 2.0 = +3.0R
    assert res["outcome"] == "TP2"
    assert res["tp1_hit"] is True
    assert abs(res["r_realized"] - 3.0) < 1e-9
    assert res["days_to_outcome"] == 4


def test_forward_tp1_then_breakeven_stop():
    fwd = [_fbar(101.0, 111.0, 110.0), _fbar(94.0, 105.0, 96.0)]
    res = vch.evaluate_forward(fwd, E, S, T1, T2)
    # Handrechnung: halbe Position TP1 = 0.5*(110-100)/5 = +1.0R, Rest Einstand 0R.
    assert res["outcome"] == "TP1_EINSTAND"
    assert res["tp1_hit"] is True
    assert abs(res["r_realized"] - 1.0) < 1e-9
    assert res["days_to_outcome"] == 2


def test_forward_expired_uses_final_close():
    # 20 Tage weder Stop noch TP1: Schluss-R = (102-100)/5 = +0.4R.
    fwd = [_fbar(97.0, 106.0, 102.0)] * 20
    res = vch.evaluate_forward(fwd, E, S, T1, T2)
    assert res["outcome"] == "EXPIRED"
    assert res["tp1_hit"] is False
    assert abs(res["r_realized"] - 0.4) < 1e-9
    assert res["days_to_outcome"] == 20
    # TP1 am Tag 1, danach 19 ruhige Tage: 0.5*2.0R + 0.5*(104-100)/5 = +1.4R.
    fwd2 = [_fbar(101.0, 111.0, 109.0)] + [_fbar(101.0, 106.0, 104.0)] * 19
    res2 = vch.evaluate_forward(fwd2, E, S, T1, T2)
    assert res2["outcome"] == "TP1_EXPIRED"
    assert abs(res2["r_realized"] - 1.4) < 1e-9
    assert res2["days_to_outcome"] == 20


def test_forward_ambiguous_day_is_conservative_stop():
    # Stop UND TP1 am selben Tag beruehrt => konservativ Stop, TP1 zaehlt NICHT —
    # auch wenn die Folgetage durch die Decke gehen.
    fwd = [_fbar(94.0, 111.0, 108.0)] + [_fbar(112.0, 125.0, 124.0)] * 3
    res = vch.evaluate_forward(fwd, E, S, T1, T2)
    assert res["outcome"] == "STOP"
    assert res["tp1_hit"] is False
    assert res["r_realized"] == -1.0
    assert res["days_to_outcome"] == 1
    assert "ambig" in res["note"]


# ---------------------------------------------------------------------------
# CSV + Universum
# ---------------------------------------------------------------------------

def test_csv_format_and_columns(tmp_path):
    rows = [{
        "ticker": "CUPX", "date": "2025-03-07", "index": 240,
        "entry": 101.2, "stop": 97.4, "tp1": 114.3, "tp2": 127.4,
        "score": 91, "cup_len": 70, "handle_len": 9, "breakout_rvol": 2.59,
        "outcome": "TP2", "tp1_hit": True, "r_realized": 3.0,
        "days_to_outcome": 7, "note": "",
    }]
    out = tmp_path / "events.csv"

    vch.write_events_csv(str(out), rows)

    with open(out, newline="", encoding="utf-8") as fh:
        parsed = list(csv.DictReader(fh))
    assert len(parsed) == 1
    row = parsed[0]
    assert list(row.keys()) == vch.CSV_FIELDS
    for col in ("ticker", "date", "entry", "stop", "tp1", "tp2", "score",
                "cup_len", "outcome", "r_realized", "days_to_outcome"):
        assert col in vch.CSV_FIELDS
    assert row["ticker"] == "CUPX"
    assert row["date"] == "2025-03-07"
    assert row["outcome"] == "TP2"
    assert float(row["r_realized"]) == 3.0
    assert row["days_to_outcome"] == "7"
    assert "index" not in row  # interner Schluessel landet nicht im CSV


def test_universe_is_curated_and_unique():
    assert len(vch.UNIVERSE) == 150
    assert len(set(vch.UNIVERSE)) == 150
    assert all(t.isalpha() and t.isupper() and 1 <= len(t) <= 5 for t in vch.UNIVERSE)
