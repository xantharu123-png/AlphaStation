#!/usr/bin/env python3
# ─────────────────────────────────────────────────────────────────────────────
# backtest_signal_mails.py — Forward-Check der 🚨-Signal-Mails vom 11.06.2026
# First-Touch-Auswertung (konservativ: Stop vor TP) auf 15-Minuten-Bars ab der
# exakten Versandminute. Aufruf auf dem Server:
#   python3 deploy/backtest_signal_mails.py
# ─────────────────────────────────────────────────────────────────────────────
import json
import os
import re
import sys
import urllib.request
import datetime as dt

APP_DIR = "/home/tradingbot/app"


def load_polygon_key() -> str:
    """Key-Quellen wie die App selbst: ENV, .env, .streamlit/secrets.toml —
    tolerant gegenueber Quotes, Leerzeichen und toml-Format."""
    env_key = os.environ.get("POLYGON_KEY", "").strip().strip('"').strip("'")
    if env_key:
        return env_key
    pattern = re.compile(r"""^\s*POLYGON_KEY\s*=\s*["']?([A-Za-z0-9_\-\.]+)["']?\s*$""")
    for path in (os.path.join(APP_DIR, ".env"),
                 os.path.join(APP_DIR, ".streamlit", "secrets.toml"),
                 os.path.expanduser("~/.streamlit/secrets.toml")):
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    m = pattern.match(line)
                    if m:
                        return m.group(1)
        except OSError:
            continue
    return ""


# (ticker, entry, stop, tp1, tp2, signal_utc) — aus den zwei Mails vom 11.06.
SIGNALS = [
    ("UNF", 278.61, 274.15, 289.99, 297.58, "2026-06-11T16:40"),
    ("MPTI", 97.89, 94.94, 105.27, 111.00, "2026-06-11T16:40"),
    ("BMA", 95.72, 90.55, 107.93, 111.52, "2026-06-11T16:40"),
    ("GGAL", 54.42, 52.52, 59.53, 60.91, "2026-06-11T16:40"),
    ("SPKL", 12.36, 12.13, 13.02, 13.29, "2026-06-11T16:40"),
    ("MBX", 33.22, 32.62, 35.99, 40.92, "2026-06-11T16:40"),
    ("ALOT", 16.90, 16.20, 18.28, 19.20, "2026-06-11T19:15"),
    ("CEPU", 15.98, 15.69, 16.95, 17.28, "2026-06-11T19:15"),
    ("TGS", 33.43, 31.96, 35.63, 36.97, "2026-06-11T19:15"),
    ("IRS", 16.57, 16.06, 18.09, 18.74, "2026-06-11T19:15"),
]


def main() -> int:
    key = load_polygon_key()
    if not key:
        print("❌ POLYGON_KEY in ENV/.env/secrets.toml nicht gefunden")
        return 1
    today = dt.date.today().isoformat()
    print(f"{'Ticker':6} {'Status':12} {'Kurs':>9} {'real. R':>8} {'offen R':>8}  Hinweis")
    print("-" * 72)
    total_r = 0.0
    evaluated = 0
    for ticker, entry, stop, tp1, tp2, ts in SIGNALS:
        t0 = int(dt.datetime.fromisoformat(ts).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/15/minute/"
               f"2026-06-11/{today}?adjusted=true&sort=asc&limit=5000&apiKey={key}")
        try:
            with urllib.request.urlopen(url, timeout=20) as resp:
                bars = json.load(resp).get("results") or []
        except Exception as exc:
            print(f"{ticker:6} FETCH-FEHLER {exc}")
            continue
        bars = [b for b in bars if b.get("t", 0) >= t0]
        if not bars:
            print(f"{ticker:6} {'KEINE BARS':12} {'-':>9} {'-':>8} {'-':>8}  (noch keine Daten seit Signal)")
            continue
        risk = entry - stop
        if risk <= 0:
            print(f"{ticker:6} GEOMETRIE-FEHLER")
            continue
        status, tp1_hit = "OPEN", False
        note = ""
        for b in bars:
            lo, hi = b.get("l", 0), b.get("h", 0)
            target = tp2 if tp1_hit else tp1
            if lo <= stop and hi >= target:
                status, note = "STOP", "ambig (Stop+Ziel im selben Bar, konservativ Stop)"
                break
            if lo <= stop:
                status = "STOP"
                break
            if not tp1_hit and hi >= tp1:
                tp1_hit, status = True, "TP1✓"
            if tp1_hit and hi >= tp2:
                status = "TP2✓✓"
                break
        last = bars[-1].get("c", entry)
        open_r = (last - entry) / risk
        # Realisiert nach Plan: TP1 = halbe Position, Stop danach = Einstand
        if status == "STOP" and not tp1_hit:
            realized = -1.0
        elif status == "STOP" and tp1_hit:
            realized = ((tp1 - entry) / risk) * 0.5  # zweite Haelfte Einstand
        elif status == "TP2✓✓":
            realized = ((tp1 - entry) / risk) * 0.5 + ((tp2 - entry) / risk) * 0.5
        elif tp1_hit:
            realized = ((tp1 - entry) / risk) * 0.5 + open_r * 0.5
        else:
            realized = open_r
        total_r += realized
        evaluated += 1
        print(f"{ticker:6} {status:12} {last:9.2f} {realized:+8.2f} {open_r:+8.2f}  {note}{len(bars)} Bars")
    if evaluated:
        print("-" * 72)
        print(f"Bilanz beider Mails ({evaluated} Setups, 1R Risiko je Trade, "
              f"TP1 = halb raus + Stop auf Einstand): {total_r:+.2f}R")
    return 0


if __name__ == "__main__":
    sys.exit(main())
