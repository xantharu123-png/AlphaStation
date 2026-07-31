"""Tests fuer scripts/chase_gate_backtest.py (AUDIT 2026-07-31).

Deckt ab: DB-Filter (asset_class/mail_class/Fenster), Gate-Input-
Rekonstruktion aus Tages-Bars (Change_5D/Vortag/ATR-Semantik wie
history_metrics) und dass die rekonstruierte BHC-Kurve die ECHTEN
Produktiv-Gates ausloest.
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.chase_gate_backtest import (  # noqa: E402
    apply_production_gates,
    load_decided_signals,
    reconstruct_gate_row,
)


def _bar(date_str: str, o: float, h: float, l: float, c: float) -> dict:
    dt = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return {"t": int(dt.timestamp() * 1000), "o": o, "h": h, "l": l, "c": c}


def _flat_bars(end_offset_days: int = 2, n: int = 25, base: float = 4.6) -> list:
    """n ruhige Tages-Bars um `base`, endend `end_offset_days` vor dem Alert."""
    bars = []
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(n, 0, -1):
        day = today - timedelta(days=i + end_offset_days - 1)
        close = base * (1 + 0.002 * ((i % 3) - 1))
        bars.append(_bar(day.strftime("%Y-%m-%d"), close * 0.998, close * 1.01, close * 0.99, close))
    return bars


def _signal(**overrides) -> dict:
    sig = {
        "ticker": "TEST",
        "scanner": "stock_strategy",
        "direction": "LONG",
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "price_at_alert": 6.33,
        "entry": 6.33,
        "stop": 6.0,
        "tp1": 7.14,
        "tp2": 7.42,
        "r_realized": -1.0,
        "outcome_detail": "",
    }
    sig.update(overrides)
    return sig


def test_load_decided_signals_filters(tmp_path):
    db = tmp_path / "sig.sqlite"
    conn = sqlite3.connect(str(db))
    conn.execute(
        """CREATE TABLE signals (
            id INTEGER PRIMARY KEY, created_at TEXT, scanner TEXT, ticker TEXT,
            asset_class TEXT, direction TEXT, entry REAL, stop REAL, tp1 REAL,
            tp2 REAL, price_at_alert REAL, grade TEXT, score REAL, rvol REAL,
            mail_class TEXT, channel TEXT, status TEXT, outcome_detail TEXT,
            tp1_hit_at TEXT, tp2_hit_at TEXT, stop_hit_at TEXT, closed_at TEXT,
            r_realized REAL, max_favorable_r REAL, max_adverse_r REAL,
            last_eval_at TEXT, eval_fail_count INTEGER)"""
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%dT%H:%M:%S")
    base = "(NULL, ?, 'stock_strategy', ?, ?, 'LONG', 1, 1, 1, 1, 1, 'A', 90, 2, ?, 'email', 'OPEN', '', NULL, NULL, NULL, NULL, -1.0, 0, 0, NULL, 0)"
    conn.execute(f"INSERT INTO signals VALUES {base}", (today, "KEEP1", "stock", "trade"))
    conn.execute(f"INSERT INTO signals VALUES {base}", (today, "DROP_CRYPTO", "crypto", "trade"))
    conn.execute(f"INSERT INTO signals VALUES {base}", (today, "DROP_WATCH", "stock", "watch"))
    conn.execute(f"INSERT INTO signals VALUES {base}", (old, "DROP_OLD", "stock", "trade"))
    conn.commit()
    conn.close()

    signals = load_decided_signals(db, 90)

    tickers = {s["ticker"] for s in signals}
    assert tickers == {"KEEP1"}


def test_reconstruct_gate_row_bhc_curve():
    """BHC-artige Kurve: Wochen flach ~4.6, Vortag +30% auf 5.98, heute
    +5.9% auf 6.33 — die Rekonstruktion muss Change_5D/Vortag liefern, und
    die ECHTEN Gates muessen feuern."""
    bars = _flat_bars(base=4.6)
    # Vortag: +30% auf ~5.98
    alert = datetime.now(timezone.utc)
    yesterday = (alert - timedelta(days=1)).strftime("%Y-%m-%d")
    today = alert.strftime("%Y-%m-%d")
    bars.append(_bar(yesterday, 4.62, 6.1, 4.6, 5.98))
    bars.append(_bar(today, 6.0, 6.35, 5.9, 6.3))

    row, why = reconstruct_gate_row(_signal(), bars)

    assert row is not None, why
    assert row["change_pct"] == round((6.33 - 5.98) / 5.98 * 100, 2)
    # 5-Tage-Anker: Schluss vor 5 kompletten Bars ~4.6 -> ~+37%
    assert row["Change_5D"] is not None and row["Change_5D"] > 30
    # Vortag: ~+30%
    assert row["Vortag_Pct"] is not None and row["Vortag_Pct"] > 25
    assert row["trade_setup"]["atr"] > 0

    reasons = apply_production_gates(row)
    assert "swing_multi_day_exhausted_no_chase" in reasons
    assert "swing_prevday_run_top_entry_wait_retest" in reasons


def test_reconstruct_gate_row_calm_curve_stays_free():
    """Ruhige Woche + moderater heutiger Breakout: die neuen Gruende duerfen
    nicht feuern (Gegenprobe gegen Ueberschiessen)."""
    bars = _flat_bars(base=10.0)
    alert = datetime.now(timezone.utc)
    yesterday = (alert - timedelta(days=1)).strftime("%Y-%m-%d")
    today = alert.strftime("%Y-%m-%d")
    bars.append(_bar(yesterday, 10.0, 10.1, 9.9, 10.0))
    bars.append(_bar(today, 10.02, 10.4, 10.0, 10.3))

    row, why = reconstruct_gate_row(_signal(price_at_alert=10.3, entry=10.3, stop=9.8, tp1=10.9, tp2=11.1), bars)

    assert row is not None, why
    reasons = apply_production_gates(row)
    assert "swing_multi_day_exhausted_no_chase" not in reasons
    assert "swing_multi_day_extended_wait_retest" not in reasons
    assert "swing_prevday_run_top_entry_wait_retest" not in reasons


def test_reconstruct_gate_row_rejects_thin_history():
    bars = _flat_bars(n=5, base=4.6)
    row, why = reconstruct_gate_row(_signal(), bars)
    assert row is None
    assert "history" in why
