#!/usr/bin/env python3
"""Chase-Gate-Backtest (AUDIT 2026-07-31, Betreiber-Auftrag nach BHC).

Frage: Wie viele der tatsaechlich gemailten Signale der letzten N Tage
haetten die neuen Mehrtages-/Vortag-Chase-Gates (31.07.) blockiert — und
haette das Geld gespart oder gekostet?

Warum Rekonstruktion noetig ist: signal_tracker.sqlite speichert die
Gate-Eingangsdaten (Change_5D, Vortag_Pct, Day_High, ATR) NICHT — nur
Entry/Stop/TP/Preis. Dieses Skript rekonstruiert die Gate-Inputs aus
Polygon-Tages-Bars um das Alert-Datum und ruft dann die ECHTEN
Produktiv-Gates (api._stock_swing_rule_reasons / _short_) auf. Keine
nachgebaute Gate-Logik im Skript — was hier feuert, feuert auch live.

Gezaehlt wird getrennt:
  A) NEUE Gruende (31.07.): multi_day_* / prevday_* — "waere heute blockiert".
  B) ALT-Gruende, die im Backtest zusaetzlich feuern — meist Artefakt der
     Ganztages-Verzerrung (wir kennen nur das High des GANZEN Tages, nicht
     das High zur Mail-Minute). Ehrlich ausgewiesen, nicht mitgezaehlt.

Bekannte Messverzerrungen (konservativ):
  - Day_High/Day_Low = Extrem des ganzen Alert-Tages => das Orts-Gate
    (<= 1% am Extrem) feuert im Backtest SELTENER als live moeglich.
  - price_at_alert = Preis zur Mail-Zeit (korrekt), aber close_pos/gap
    beziehen sich auf die Ganztages-Bar.
  => Die Blockquote ist eher eine UNTERE Schranke.

Usage (auf dem Server, im App-Verzeichnis):
    venv/bin/python3 scripts/chase_gate_backtest.py
    venv/bin/python3 scripts/chase_gate_backtest.py --days 90 --sample 60

Exit 0 immer (Analyse-Tool, kein Gate).
"""
from __future__ import annotations  # Annotations lazy: py3.8-kompatibel

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BAR_SLEEP_SEC = 0.3  # ~200 calls/min Deckel, konservativ

NEW_REASONS_LONG = {
    "swing_multi_day_exhausted_no_chase",
    "swing_multi_day_extended_wait_retest",
    "swing_prevday_run_top_entry_wait_retest",
}
NEW_REASONS_SHORT = {
    "swing_short_multi_day_exhausted_no_chase",
    "swing_short_multi_day_extended_wait_retest",
    "swing_short_prevday_run_bottom_entry_wait_retest",
}
HARD_NO_CHASE = {
    "swing_multi_day_exhausted_no_chase",
    "swing_short_multi_day_exhausted_no_chase",
}


def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _load_polygon_key() -> str:
    """Key wie die App laden: ENV, dann secrets.toml/.env. Nie drucken."""
    for name in ("POLYGON_KEY", "POLYGON_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    candidates = [
        Path.home() / ".streamlit" / "secrets.toml",
        REPO_ROOT / ".streamlit" / "secrets.toml",
        REPO_ROOT / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                if line.startswith("export "):
                    line = line[len("export "):].strip()
                key, val = line.split("=", 1)
                if key.strip() in ("POLYGON_KEY", "POLYGON_API_KEY") and val.strip():
                    return val.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


def _bar_date(bar: dict) -> str | None:
    ts = bar.get("t", bar.get("timestamp"))
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(float(ts) / 1000.0, timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return None


def _fetch_daily_bars(ticker: str, end: datetime, days: int, api_key: str, cache: dict) -> list[dict]:
    key = (ticker, end.strftime("%Y-%m-%d"))
    if key in cache:
        return cache[key]
    import requests

    start = (end - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (
        f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
        f"{start}/{end.strftime('%Y-%m-%d')}"
    )
    try:
        resp = requests.get(url, params={"apiKey": api_key, "adjusted": "true", "sort": "asc", "limit": 120}, timeout=15)
        bars = resp.json().get("results", []) if resp.status_code == 200 else []
    except Exception:
        bars = []
    time.sleep(BAR_SLEEP_SEC)
    cache[key] = bars
    return bars


def load_decided_signals(db_path: Path, days: int) -> list[dict]:
    """Aktien-Trade-Signale der letzten N Tage aus dem Tracker."""
    if not db_path.exists():
        return []
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    conn = sqlite3.connect(str(db_path), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT ticker, scanner, direction, created_at, price_at_alert,
                   entry, stop, tp1, tp2, r_realized, outcome_detail
            FROM signals
            WHERE created_at >= ?
              AND COALESCE(asset_class, 'stock') = 'stock'
              AND mail_class = 'trade'
            ORDER BY created_at DESC
            """,
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def reconstruct_gate_row(signal: dict, bars: list[dict]) -> tuple[dict | None, str]:
    """Baut die Scanner-aequivalente Row zum Alert-Zeitpunkt aus Tages-Bars.

    Rueckgabe (row, ""); bei nicht rekonstruierbar (None, grund).
    """
    alert_dt = _parse_ts(signal.get("created_at"))
    price = signal.get("price_at_alert") or signal.get("entry")
    if alert_dt is None or not price or price <= 0:
        return None, "kein_alertdatum_oder_preis"
    alert_date = alert_dt.strftime("%Y-%m-%d")
    dated = [(b, _bar_date(b)) for b in bars if isinstance(b, dict)]
    prev_bars = [b for b, d in dated if d and d < alert_date]
    day_bars = [b for b, d in dated if d == alert_date]
    if len(prev_bars) < 16:
        return None, f"zu_wenig_history ({len(prev_bars)} Bars)"
    if not day_bars:
        return None, "alert_tag_bar_fehlt"
    day_bar = day_bars[-1]
    prev_close = float(prev_bars[-1].get("c", prev_bars[-1].get("close", 0)) or 0)
    if prev_close <= 0:
        return None, "prev_close_fehlt"

    def _c(bar):
        return float(bar.get("c", bar.get("close", 0)) or 0)

    def _h(bar):
        return float(bar.get("h", bar.get("high", 0)) or 0)

    def _l(bar):
        return float(bar.get("l", bar.get("low", 0)) or 0)

    def _o(bar):
        return float(bar.get("o", bar.get("open", 0)) or 0)

    from modules.vrvp_levels import calculate_wilder_atr

    norm_bars = [
        {"high": _h(b), "low": _l(b), "close": _c(b), "t": b.get("t")}
        for b in prev_bars
    ]
    atr14 = calculate_wilder_atr(norm_bars, 14)
    base_5d = _c(prev_bars[-5]) if len(prev_bars) >= 5 else 0.0
    change_5d = ((price - base_5d) / base_5d * 100.0) if base_5d > 0 else None
    prev2_close = _c(prev_bars[-2]) if len(prev_bars) >= 2 else 0.0
    vortag_pct = ((prev_close - prev2_close) / prev2_close * 100.0) if prev2_close > 0 else None
    day_open = _o(day_bar)
    day_high = _h(day_bar)
    day_low = _l(day_bar)
    change_pct = (price - prev_close) / prev_close * 100.0
    gap_pct = ((day_open - prev_close) / prev_close * 100.0) if day_open > 0 else None
    open_to_current = ((price - day_open) / day_open * 100.0) if day_open > 0 else None
    close_pos = ((price - day_low) / (day_high - day_low)) if day_high > day_low else None

    row = {
        "price": price,
        "current_price": price,
        "change_pct": round(change_pct, 2),
        "gap_pct": round(gap_pct, 2) if gap_pct is not None else None,
        "close_pos": round(close_pos, 2) if close_pos is not None else None,
        "open_to_current_pct": round(open_to_current, 2) if open_to_current is not None else None,
        "Day_High": day_high,
        "Day_Low": day_low,
        "Change_5D": round(change_5d, 2) if change_5d is not None else None,
        "Vortag_Pct": round(vortag_pct, 2) if vortag_pct is not None else None,
        "trade_setup": {"atr": atr14} if atr14 and atr14 > 0 else {},
        "Signal_Direction": str(signal.get("direction") or "LONG").upper(),
    }
    return row, ""


def apply_production_gates(row: dict) -> list[str]:
    """Die ECHTEN Produktiv-Gates — kein Nachbau."""
    import api

    direction = str(row.get("Signal_Direction") or "LONG").upper()
    if direction == "SHORT":
        return api._stock_swing_short_rule_reasons(row)
    return api._stock_swing_rule_reasons(row)


def main() -> int:
    parser = argparse.ArgumentParser(description="Chase-Gate-Backtest (31.07.)")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--sample", type=int, default=0, help="nur juengste N Signale")
    parser.add_argument("--db", default=str(REPO_ROOT / "data_cache" / "signal_tracker.sqlite"))
    args = parser.parse_args()

    db_path = Path(args.db)
    print(f"DB: {db_path}")
    print(f"Fenster: {args.days} Tage")

    signals = load_decided_signals(db_path, args.days)
    if args.sample > 0:
        signals = signals[: args.sample]
    print(f"Signale (Aktien, trade): {len(signals)}")
    if not signals:
        print("FEHLER: keine Signale im Fenster — DB-Pfad/Fenster pruefen.")
        return 0

    api_key = _load_polygon_key()
    if not api_key:
        print("FEHLER: kein POLYGON_KEY (ENV oder .env) — Rekonstruktion braucht Tages-Bars.")
        return 0

    cache: dict = {}
    measured: list[dict] = []
    skipped = 0
    for sig in signals:
        alert_dt = _parse_ts(sig.get("created_at"))
        if alert_dt is None:
            skipped += 1
            continue
        bars = _fetch_daily_bars(str(sig.get("ticker") or ""), alert_dt, 75, api_key, cache)
        row, why = reconstruct_gate_row(sig, bars)
        if row is None:
            skipped += 1
            continue
        reasons = apply_production_gates(row)
        direction = str(sig.get("direction") or "LONG").upper()
        new_set = NEW_REASONS_SHORT if direction == "SHORT" else NEW_REASONS_LONG
        new_hits = [r for r in reasons if r in new_set]
        old_hits = [r for r in reasons if r not in new_set]
        measured.append({
            "ticker": sig.get("ticker"),
            "scanner": sig.get("scanner"),
            "direction": direction,
            "created_at": sig.get("created_at"),
            "r": sig.get("r_realized"),
            "new_hits": new_hits,
            "old_hits": old_hits,
            "row": row,
        })

    print(f"\nGemessen: {len(measured)} | uebersprungen (History/Daten): {skipped}")

    blocked = [m for m in measured if m["new_hits"]]
    free = [m for m in measured if not m["new_hits"]]
    hard = [m for m in blocked if any(h in HARD_NO_CHASE for h in m["new_hits"])]

    def _rs(items):
        vals = [m["r"] for m in items if isinstance(m["r"], (int, float))]
        return vals

    def _avg(vals):
        return sum(vals) / len(vals) if vals else None

    print("\n=== A) NEUE Gates (31.07.) — 'waere heute blockiert' ===")
    print(f"  blockiert: {len(blocked)} von {len(measured)} ({(len(blocked) / len(measured) * 100) if measured else 0:.0f}%)")
    print(f"  davon hart (NO_CHASE, >= 7 ATR/5d): {len(hard)}")
    reason_counts: dict = {}
    for m in blocked:
        for r in m["new_hits"]:
            reason_counts[r] = reason_counts.get(r, 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        print(f"    {count:>3}x {reason}")

    print("\n=== B) Outcome-Vergleich (nur entschiedene Signale mit r_realized) ===")
    rb = _rs(blocked)
    rf = _rs(free)
    print(f"  blockiert: n={len(rb)}  ØR {_avg(rb) if _avg(rb) is not None else '—'}  Summe R {round(sum(rb), 1) if rb else '—'}")
    print(f"  frei:      n={len(rf)}  ØR {_avg(rf) if _avg(rf) is not None else '—'}  Summe R {round(sum(rf), 1) if rf else '—'}")
    if rb:
        wins_b = len([v for v in rb if v > 0])
        print(f"  Trefferquote blockiert: {wins_b}/{len(rb)} ({wins_b / len(rb) * 100:.0f}%)")
    if rf:
        wins_f = len([v for v in rf if v > 0])
        print(f"  Trefferquote frei:      {wins_f}/{len(rf)} ({wins_f / len(rf) * 100:.0f}%)")
    if rb and _avg(rb) is not None:
        verdict = "GESPART" if _avg(rb) < 0 else "GEKOSTET"
        print(f"\n  → Das Gate haette auf dieser Stichprobe {verdict}: "
              f"die blockierten Signale liefen im Schnitt bei {_avg(rb):+.2f}R.")

    old_only = [m for m in measured if m["old_hits"]]
    print("\n=== C) Alt-Gates, die im Backtest ZUSAETZLICH feuern ===")
    print(f"  {len(old_only)} Faelle — meist Ganztages-Verzerrung (High/Low des ganzen")
    print("  Tages statt Mail-Minute). Nicht in (A) gezaehlt; Stichprobe:")
    for m in old_only[:8]:
        print(f"    {m['ticker']} {str(m['created_at'])[:10]}: {', '.join(m['old_hits'][:3])}")

    print("\n=== D) Blockierte Signale (Detail, juengste zuerst) ===")
    for m in blocked[:15]:
        row = m["row"]
        print(
            f"  {str(m['created_at'])[:16]} {m['ticker']:<7} {m['direction']:<5} "
            f"chg {row.get('change_pct')}%  5d {row.get('Change_5D')}%  "
            f"vortag {row.get('Vortag_Pct')}%  R={m['r']}  -> {', '.join(m['new_hits'])}"
        )

    print("\nLimitationen: Day_High/Low = Ganztages-Extrem (Orts-Gate feuert im")
    print("Backtest seltener als live); kein Intraday-RVOL; ATR aus Daily-Bars")
    print("(Wilder, wie Produktion). Blockquote = eher UNTERE Schranke.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
