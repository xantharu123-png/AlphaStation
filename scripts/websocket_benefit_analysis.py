#!/usr/bin/env python3
"""WebSocket-Nutzen-Analyse (AUDIT 2026-07-29, Betreiber-Frage).

Frage: Wie viel bringt ein Echtzeit-Stream (Sekunden statt Minuten bis zum
Alert) WIRKLICH — gemessen an den eigenen Track-Daten statt am Bauchgefuehl?

Phase A (kein API, laeuft ueberall):
  Aus signal_tracker.sqlite, entschiedene Signale (r_realized vorhanden):
  - Zeit von Mail (created_at) bis TP1 bzw. Stop — Verteilung in Minuten.
    Faellt TP1 oft < 15-30 min nach der Mail, zaehlen Minuten echtes Geld.
  - MFE-Ausschoepfung: r_realized vs. max_favorable_r.

Phase B (--with-bars, braucht POLYGON_KEY; nur US-Aktien):
  Stichprobe der juengsten Signale (stock_strategy/strategy_scan/crash_monitor):
  1. Extension zum Mail-Zeitpunkt: (price_at_alert - Vortagesschluss) / ATR14.
     Anteil >= 2 ATR = "Move war bei der Mail schon gelaufen" (Orts-Gate-
     Perspektive: diese Faelle wuerden heute geblockt; frueher Alert haette
     sie im frischen Zustand erwischt).
  2. Preis-Vorteil T-10/T-15: Schlusskurs der 5m-Bar 10/15 min VOR der Mail
     vs. price_at_alert. Positiver Wert = so viel % waere man frueher
     guenstiger drin gewesen.
  3. Ehrlicher Gegencheck: In wie vielen Faellen waere der hypothetische
     Frueh-Entry auf dem Pfad T-delta -> Mailzeit bereits durch den Stop
     gelaufen? Frueher ist nicht automatisch besser — das misst das.

Usage (auf dem Server, im App-Verzeichnis):
    venv/bin/python3 scripts/websocket_benefit_analysis.py
    venv/bin/python3 scripts/websocket_benefit_analysis.py --with-bars --sample 80
    venv/bin/python3 scripts/websocket_benefit_analysis.py --days 60

Exit 0 immer (Analyse-Tool, kein Gate).
"""
from __future__ import annotations  # Annotations lazy: py3.8-kompatibel

import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

BAR_SLEEP_SEC = 0.3  # ~200 calls/min Deckel, konservativ


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _pct(sorted_vals: list[float], q: float) -> float | None:
    if not sorted_vals:
        return None
    idx = min(len(sorted_vals) - 1, max(0, int(round(q * (len(sorted_vals) - 1)))))
    return sorted_vals[idx]


def _load_polygon_key() -> str:
    for name in ("POLYGON_KEY", "POLYGON_API_KEY"):
        if os.environ.get(name):
            return os.environ[name]
    env_path = REPO_ROOT / ".env"
    if env_path.exists():
        try:
            for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                if key.strip() in ("POLYGON_KEY", "POLYGON_API_KEY") and val.strip():
                    return val.strip().strip('"').strip("'")
        except Exception:
            pass
    return ""


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


def _fetch_5m_bars(ticker: str, day: datetime, api_key: str, cache: dict) -> list[dict]:
    day_str = day.strftime("%Y-%m-%d")
    key = (ticker, day_str)
    if key in cache:
        return cache[key]
    import requests

    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{day_str}/{day_str}"
    try:
        resp = requests.get(url, params={"apiKey": api_key, "adjusted": "true", "sort": "asc", "limit": 5000}, timeout=15)
        bars = resp.json().get("results", []) if resp.status_code == 200 else []
    except Exception:
        bars = []
    time.sleep(BAR_SLEEP_SEC)
    cache[key] = bars
    return bars


def _atr14(daily_bars: list[dict]) -> float | None:
    if len(daily_bars) < 15:
        return None
    trs = []
    for i in range(1, len(daily_bars)):
        high = float(daily_bars[i].get("h", 0) or 0)
        low = float(daily_bars[i].get("l", 0) or 0)
        prev_close = float(daily_bars[i - 1].get("c", 0) or 0)
        if high <= 0 or low <= 0 or prev_close <= 0:
            continue
        trs.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    if len(trs) < 14:
        return None
    return sum(trs[-14:]) / 14.0


def _price_at_or_before(bars_5m: list[dict], target: datetime) -> float | None:
    price = None
    target_ms = int(target.timestamp() * 1000)
    for bar in bars_5m:
        if int(bar.get("t", 0) or 0) <= target_ms:
            price = float(bar.get("c", 0) or 0) or price
        else:
            break
    return price if price and price > 0 else None


def _stop_touched_between(bars_5m: list[dict], start: datetime, end: datetime, stop: float, direction: str) -> bool:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    for bar in bars_5m:
        ts = int(bar.get("t", 0) or 0)
        if ts < start_ms or ts > end_ms:
            continue
        if direction == "SHORT":
            if float(bar.get("h", 0) or 0) >= stop:
                return True
        elif float(bar.get("l", 0) or 0) <= stop:
            return True
    return False


def phase_a(rows: list[dict]) -> None:
    decided = [r for r in rows if r.get("r_realized") is not None]
    print(f"\n=== PHASE A (Tracker, ohne API): {len(decided)} entschiedene Signale ===")
    if not decided:
        print("  Keine entschiedenen Signale im Fenster — Phase A nichts zu messen.")
        return

    by_scanner: dict[str, list[dict]] = {}
    for r in decided:
        by_scanner.setdefault(str(r.get("scanner") or "?"), []).append(r)

    header = f"{'Scanner':<18} {'n':>4} {'TP1<15m':>8} {'TP1<30m':>8} {'TP1<60m':>8} {'med TP1-Zeit':>12} {'med Stop-Zeit':>13} {'MFE-Nutzung':>11}"
    print(header)
    print("-" * len(header))
    total_tp1_fast = 0
    total_tp1 = 0
    for scanner in sorted(by_scanner, key=lambda s: -len(by_scanner[s])):
        rows_s = by_scanner[scanner]
        tp1_minutes = []
        stop_minutes = []
        mfe_use = []
        for r in rows_s:
            created = _parse_ts(r.get("created_at"))
            tp1_at = _parse_ts(r.get("tp1_hit_at"))
            stop_at = _parse_ts(r.get("stop_hit_at"))
            if created and tp1_at:
                mins = (tp1_at - created).total_seconds() / 60.0
                if mins >= 0:
                    tp1_minutes.append(mins)
            if created and stop_at:
                mins = (stop_at - created).total_seconds() / 60.0
                if mins >= 0:
                    stop_minutes.append(mins)
            mfe = r.get("max_favorable_r")
            realized = r.get("r_realized")
            try:
                if mfe is not None and realized is not None and float(mfe) > 0.05:
                    mfe_use.append(min(2.0, max(-1.0, float(realized) / float(mfe))))
            except (TypeError, ValueError):
                pass
        tp1_sorted = sorted(tp1_minutes)
        stop_sorted = sorted(stop_minutes)
        n_tp1 = len(tp1_sorted)
        total_tp1 += n_tp1
        fast = len([m for m in tp1_sorted if m < 30])
        total_tp1_fast += fast

        def share(limit: float) -> str:
            return f"{100.0 * len([m for m in tp1_sorted if m < limit]) / n_tp1:.0f}%" if n_tp1 else "—"

        med_tp1 = _pct(tp1_sorted, 0.5)
        med_stop = _pct(stop_sorted, 0.5)
        med_mfe = _pct(sorted(mfe_use), 0.5)
        print(
            f"{scanner:<18} {len(rows_s):>4} {share(15):>8} {share(30):>8} {share(60):>8} "
            f"{(f'{med_tp1:.0f}m' if med_tp1 is not None else '—'):>12} "
            f"{(f'{med_stop:.0f}m' if med_stop is not None else '—'):>13} "
            f"{(f'{med_mfe:.0%}' if med_mfe is not None else '—'):>11}"
        )
    if total_tp1:
        print(f"\n  Anteil aller TP1-Treffer < 30 min nach der Mail: {100.0 * total_tp1_fast / total_tp1:.0f}% "
              f"({total_tp1_fast}/{total_tp1})")
        print("  → Je groesser dieser Anteil, desto mehr zahlt das System auf Minuten ein.")


def phase_b(rows: list[dict], api_key: str, sample: int) -> None:
    print(f"\n=== PHASE B (Polygon 5m/1D, Stichprobe {sample}) ===")
    candidates = []
    for r in rows:
        if r.get("r_realized") is None:
            continue
        scanner = str(r.get("scanner") or "")
        if scanner not in ("stock_strategy", "strategy_scan", "crash_monitor"):
            continue
        ticker = str(r.get("ticker") or "").upper().strip()
        if not ticker or "." in ticker or "/" in ticker:
            continue
        created = _parse_ts(r.get("created_at"))
        entry = r.get("entry") or r.get("price_at_alert")
        stop = r.get("stop")
        if not created or not entry or not stop:
            continue
        candidates.append((created, ticker, r))
    candidates.sort(key=lambda item: item[0], reverse=True)
    candidates = candidates[:sample]
    if not candidates:
        print("  Keine geeigneten Signale fuer Phase B.")
        return

    daily_cache: dict = {}
    bar_cache: dict = {}
    ext_shares = []
    adv10 = []
    adv15 = []
    stop_risked = 0
    measured = 0
    skipped = 0

    for created, ticker, r in candidates:
        entry = float(r.get("entry") or r.get("price_at_alert") or 0)
        stop = float(r.get("stop") or 0)
        direction = str(r.get("direction") or "LONG").upper()
        daily = _fetch_daily_bars(ticker, created, 45, api_key, daily_cache)
        atr = _atr14(daily)
        prev_close = None
        created_date = created.strftime("%Y-%m-%d")
        prior = [b for b in daily if b.get("t") and datetime.fromtimestamp(int(b["t"]) / 1000, timezone.utc).strftime("%Y-%m-%d") < created_date]
        if prior:
            prev_close = float(prior[-1].get("c", 0) or 0) or None
        if atr and prev_close:
            ext = abs(entry - prev_close) / atr
            ext_shares.append(ext)
        bars_5m = _fetch_5m_bars(ticker, created, api_key, bar_cache)
        if not bars_5m:
            skipped += 1
            continue
        price_alert = float(r.get("price_at_alert") or entry)
        p10 = _price_at_or_before(bars_5m, created - timedelta(minutes=10))
        p15 = _price_at_or_before(bars_5m, created - timedelta(minutes=15))
        if p10:
            adv10.append((price_alert - p10) / price_alert * 100.0 if direction != "SHORT" else (p10 - price_alert) / price_alert * 100.0)
        if p15:
            adv15.append((price_alert - p15) / price_alert * 100.0 if direction != "SHORT" else (p15 - price_alert) / price_alert * 100.0)
            if _stop_touched_between(bars_5m, created - timedelta(minutes=15), created, stop, direction):
                stop_risked += 1
        measured += 1

    print(f"  Gemessen: {measured} | ohne 5m-Bars (uebersprungen): {skipped}")
    if ext_shares:
        ext_sorted = sorted(ext_shares)
        over2 = len([e for e in ext_sorted if e >= 2.0])
        over3 = len([e for e in ext_sorted if e >= 3.0])
        print(f"\n  Extension zum Mail-Zeitpunkt (Move in ATR14, n={len(ext_sorted)}):")
        print(f"    Median {_pct(ext_sorted, 0.5):.1f} ATR | p75 {_pct(ext_sorted, 0.75):.1f} ATR")
        print(f"    >= 2 ATR (Move schon gelaufen, Orts-Gate-Fall): {100.0 * over2 / len(ext_sorted):.0f}% ({over2})")
        print(f"    >= 3 ATR (PM-Extensions-Decke):                {100.0 * over3 / len(ext_sorted):.0f}% ({over3})")
    if adv10:
        a10 = sorted(adv10)
        a15 = sorted(adv15)
        better10 = len([a for a in a10 if a > 0.5])
        print(f"\n  Preis-Vorteil bei Entry 10/15 min frueher (n={len(a10)}/{len(a15)}):")
        print(f"    Median T-10: {_pct(a10, 0.5):+.1f}% | p75: {_pct(a10, 0.75):+.1f}% | >0.5% besser: {100.0 * better10 / len(a10):.0f}%")
        print(f"    Median T-15: {_pct(a15, 0.5):+.1f}% | p75: {_pct(a15, 0.75):+.1f}%")
    if adv15:
        print(f"\n  Gegencheck Ehrlichkeit: in {stop_risked} von {len(a15)} Faellen waere der "
              f"Frueh-Entry (T-15) auf dem Pfad zur Mailzeit BEREITS durch den Stop gelaufen.")
        print("  → Frueher ist nicht automatisch besser; dieser Anteil ist der Preis der Eile.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=90, help="Fenster in Tagen (Default 90)")
    parser.add_argument("--db", type=str, default="", help="Pfad zur signal_tracker.sqlite (Default: App-Pfad)")
    parser.add_argument("--with-bars", action="store_true", help="Phase B mit Polygon-Bars (braucht POLYGON_KEY)")
    parser.add_argument("--sample", type=int, default=80, help="Phase-B-Stichprobe (Default 80)")
    args = parser.parse_args()

    from modules import signal_tracker as st

    db_path = args.db or str(st.SIGNAL_DB_PATH)
    print(f"DB: {db_path}")
    print(f"Fenster: {args.days} Tage | Phase B: {'an' if args.with_bars else 'aus (--with-bars fuer Polygon-Messung)'}")
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max(1, args.days))).isoformat()

    if args.db:
        import sqlite3

        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM signals WHERE created_at >= ? ORDER BY created_at ASC, id ASC", (cutoff_iso,)
        ).fetchall()]
        conn.close()
    else:
        with st._db_connection() as conn:  # WAL/Migration wie im Tracker
            rows = [dict(r) for r in conn.execute(
                "SELECT * FROM signals WHERE created_at >= ? ORDER BY created_at ASC, id ASC", (cutoff_iso,)
            ).fetchall()]

    phase_a(rows)

    if args.with_bars:
        api_key = _load_polygon_key()
        if not api_key:
            print("\n  FEHLER: kein POLYGON_KEY (ENV oder .env) — Phase B abgebrochen.")
            return 0
        phase_b(rows, api_key, max(1, args.sample))

    print("\n=== INTERPRETATION (Faustregeln) ===")
    print("  * TP1-Anteil < 30 min > 40%  → Minuten sind Geld; schneller Transport zahlt ein.")
    print("  * Extension >= 2 ATR > 25%   → Alerts kommen oft erst nach dem Move; frueher fangen lohnt.")
    print("  * Median-Preisvorteil T-10 > 1.5% UND Stop-Gegencheck < 15% → Level-Cross-Trigger (Phase 2) ist belegt.")
    print("  * Alles darunter            → B1–B3 (PM-Radar + 10-Min-Opening-Takt) deckt den Nutzen bereits ab.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
