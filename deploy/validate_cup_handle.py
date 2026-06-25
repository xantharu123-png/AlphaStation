#!/usr/bin/env python3
# -----------------------------------------------------------------------------
# validate_cup_handle.py - Walk-Forward-Validierung des Cup&Handle-Detektors
#
# Zweck: beweisen/widerlegen, dass der ECHTE Produktions-Detektor
# api._detect_cup_handle_breakout auf echten Charts handelbare Cup&Handle-
# Breakouts findet. Dazu laeuft der Detektor Tag fuer Tag ueber historische
# Daily-Bars - an Tag t sieht er ausschliesslich bars[:t+1][-200:], also exakt
# die Information, die er an jenem Tag gehabt haette (KEIN Look-ahead).
# Jeder CONFIRMED-Fund wird als Event gespeichert (Dedupe: 20 Handelstage
# Sperre je Ticker) und auf den naechsten max. 20 Handelstagen ausgewertet.
#
# First-Touch-Auswertung konservativ, identische R-Logik wie
# deploy/backtest_signal_mails.py (Vorbild fuer Loop + Realisierung):
#   STOP          Stop vor TP1 beruehrt                        -> -1.0R
#   TP1_EINSTAND  TP1 erreicht (halbe Position raus), danach
#                 Stop-Level beruehrt -> Rest zum Einstand     -> +0.5*(TP1-R)
#   TP2           TP1 + TP2 erreicht                           -> 0.5*TP1R + 0.5*TP2R
#   TP1_EXPIRED   TP1 erreicht, Fenster (20 Tage) laeuft aus   -> 0.5*TP1R + 0.5*Schluss-R
#   TP1_OPEN      wie TP1_EXPIRED, aber Datenende vor Tag 20
#   EXPIRED       weder Stop noch TP1 in 20 Tagen              -> Schluss-R
#   OPEN          Datenende vor Tag 20, nichts beruehrt        -> Schluss-R
# Ambiguitaet (Stop UND aktuelles Ziel am selben Tag) zaehlt konservativ als
# Stop - TP1 wird in dem Fall NICHT gutgeschrieben.
#
# Aufruf auf dem Server (dort liegt .env mit POLYGON_KEY):
#   cd /home/tradingbot/app
#   python3 deploy/validate_cup_handle.py --years 2 --out cup_handle_events.csv
#   python3 deploy/validate_cup_handle.py --tickers AAPL,MSFT,NVDA --years 3
#
# Das CSV (--out) ist fuer die Augen-Pruefung gedacht: Ticker + Datum in
# TradingView eintippen und den Fund am Chart verifizieren.
# -----------------------------------------------------------------------------
import argparse
import csv
import datetime as dt
import json
import math
import os
import re
import statistics
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

APP_DIR = "/home/tradingbot/app"
APP_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

START_INDEX = 200          # Walk-Forward beginnt ab Bar 200 (Anlauf-Historie)
LOOKBACK_BARS = 200        # Detektor-Input je Tag: bars[:t+1][-200:]
FORWARD_DAYS = 20          # Forward-Fenster je Event (Handelstage nach t)
COOLDOWN_DAYS = 20         # Dedupe: Ticker nach Event 20 Handelstage gesperrt
SLEEP_BETWEEN_TICKERS = 0.35  # Rate-Schonung Polygon (~170 Calls/min Budget)

FOOTNOTE = ("Walk-Forward auf adjustierten Daily-Bars; konservatives "
            "First-Touch; keine Kommissionen/Slippage.")

CSV_FIELDS = [
    "ticker", "date", "entry", "stop", "tp1", "tp2", "score",
    "cup_len", "handle_len", "breakout_rvol",
    "outcome", "tp1_hit", "r_realized", "days_to_outcome", "note",
]

# -----------------------------------------------------------------------------
# Universum: ~150 kuratierte, hochliquide US-Namen. Auswahlprinzip:
#  - S&P-500-Querschnitt ueber alle 11 Sektoren (Mega-/Large-Caps), damit das
#    Ergebnis nicht von einem einzelnen Sektor-Regime dominiert wird;
#  - bekannte Momentum-/High-Beta-Namen (dort entstehen Cup&Handles am
#    haeufigsten - O'Neil-Universum);
#  - eine Handvoll liquider Mid-Caps als Realitaets-Check;
#  - KEINE Mini-Caps: alle Namen liegen weit ueber der Produktions-
#    Liquiditaetsschranke (Dollar-Volume >= 2 Mio.), so dass jeder Fund auch
#    im Live-Scanner zulaessig gewesen waere.
#  - Keine bekannten Delisting-/Merger-Opfer des Zeitraums (PXD, HES, SPLK,
#    JNPR, ANSS, CTLT, X, ... bewusst NICHT enthalten).
# Ueberschreibbar via --tickers AAPL,MSFT,...
# -----------------------------------------------------------------------------
UNIVERSE: List[str] = [
    # Mega-Cap Tech (10)
    "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA", "AVGO", "ORCL", "NFLX",
    # Halbleiter / Hardware (15)
    "AMD", "QCOM", "TXN", "INTC", "MU", "KLAC", "LRCX", "AMAT", "ASML", "TSM",
    "MRVL", "ON", "ADI", "NXPI", "SMCI",
    # Software / Cloud / Security (20)
    "CRM", "ADBE", "NOW", "INTU", "IBM", "CSCO", "ACN", "PANW", "CRWD", "ZS",
    "FTNT", "SNOW", "DDOG", "NET", "MDB", "TEAM", "SHOP", "PLTR", "ANET", "DELL",
    # Internet / Kommunikation / Medien (10)
    "TMUS", "VZ", "T", "CMCSA", "DIS", "SPOT", "UBER", "ABNB", "DASH", "RBLX",
    # Finanzen / Fintech (15)
    "JPM", "BAC", "WFC", "GS", "MS", "C", "SCHW", "BLK", "AXP", "V",
    "MA", "PYPL", "COIN", "HOOD", "SOFI",
    # Consumer Discretionary / Retail / Travel (15)
    "WMT", "COST", "TGT", "HD", "LOW", "NKE", "SBUX", "MCD", "CMG", "LULU",
    "TJX", "ROST", "BKNG", "MAR", "RCL",
    # Consumer Staples (10)
    "KO", "PEP", "PG", "CL", "MDLZ", "PM", "MO", "STZ", "KMB", "GIS",
    # Healthcare / Pharma / Biotech-Large (15)
    "UNH", "JNJ", "LLY", "ABBV", "MRK", "PFE", "BMY", "AMGN", "GILD", "VRTX",
    "REGN", "ISRG", "BSX", "MDT", "TMO",
    # Industrie / Defense / Transport (15)
    "CAT", "DE", "BA", "GE", "HON", "UNP", "UPS", "FDX", "LMT", "RTX",
    "NOC", "ETN", "EMR", "PH", "URI",
    # Energie / Rohstoffe / Chemie (10)
    "XOM", "CVX", "COP", "SLB", "EOG", "OXY", "PSX", "VLO", "FCX", "LIN",
    # Utilities / Power (5)
    "NEE", "DUK", "SO", "VST", "CEG",
    # Momentum- / Mid-Cap-Namen (10)
    "APP", "MSTR", "RKLB", "AFRM", "DKNG", "CVNA", "CELH", "DUOL", "AXON", "DECK",
]


def load_polygon_key() -> str:
    """Key-Quellen wie die App selbst: ENV, .env, .streamlit/secrets.toml -
    tolerant gegenueber Quotes, Leerzeichen und toml-Format.
    (1:1 uebernommen aus deploy/backtest_signal_mails.py)"""
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


def _load_production_detector() -> Callable[..., Optional[Dict[str, Any]]]:
    """Laedt den ECHTEN Produktions-Detektor. `import api` zieht den
    FastAPI-Stack hoch, startet aber weder Server noch Scheduler - exakt so
    arbeiten auch die Unit-Tests des Repos."""
    if APP_ROOT not in sys.path:
        sys.path.insert(0, APP_ROOT)
    import api  # noqa: PLC0415 - bewusst lazy, damit --help/--tickers '' ohne Stack laufen
    return api._detect_cup_handle_breakout


def fetch_daily_bars(ticker: str, years: float, key: str) -> List[Dict[str, Any]]:
    """EIN Polygon-Aggregates-Call je Ticker (1/day, adjusted, asc, limit 50000).
    429 -> 60s warten + Retry (max 2). Rueckgabe in der Bar-Konvention des
    Detektors/der Test-Fixture: open/high/low/close/volume (+date)."""
    today = dt.date.today()
    frm = (today - dt.timedelta(days=int(round(years * 365.25)))).isoformat()
    url = (f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/"
           f"{frm}/{today.isoformat()}?adjusted=true&sort=asc&limit=50000&apiKey={key}")
    retries = 0
    while True:
        try:
            with urllib.request.urlopen(url, timeout=30) as resp:
                payload = json.load(resp)
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 429 and retries < 2:
                retries += 1
                print(f"  {ticker}: HTTP 429 - warte 60s (Retry {retries}/2)")
                time.sleep(60)
                continue
            raise
    bars: List[Dict[str, Any]] = []
    for raw in payload.get("results") or []:
        ts = raw.get("t")
        date = (dt.datetime.fromtimestamp(ts / 1000, tz=dt.timezone.utc).date().isoformat()
                if ts else "")
        bars.append({
            "date": date,
            "open": float(raw.get("o") or 0.0),
            "high": float(raw.get("h") or 0.0),
            "low": float(raw.get("l") or 0.0),
            "close": float(raw.get("c") or 0.0),
            "volume": float(raw.get("v") or 0.0),
        })
    return bars


def walk_forward(
    ticker: str,
    bars: List[Dict[str, Any]],
    detect_fn: Callable[..., Optional[Dict[str, Any]]],
    *,
    start_index: int = START_INDEX,
    lookback: int = LOOKBACK_BARS,
    cooldown: int = COOLDOWN_DAYS,
) -> List[Dict[str, Any]]:
    """Laesst den Detektor Tag fuer Tag laufen. An Tag t bekommt er NUR
    bars[:t+1][-lookback:] - exakt die Bars, die er an jenem Tag gehabt
    haette. Nach einem Event ist der Ticker `cooldown` Handelstage gesperrt
    (sonst meldet jeder Folgetag dasselbe Pattern erneut)."""
    events: List[Dict[str, Any]] = []
    blocked_until = -1
    for t in range(start_index, len(bars)):
        if t <= blocked_until:
            continue
        window = bars[:t + 1][-lookback:]
        current_price = float(window[-1].get("close") or 0.0)
        setup = detect_fn(window, current_price=current_price)
        if not setup:
            continue
        entry = float(setup.get("entry") or 0.0)
        stop = float(setup.get("stop_loss") or 0.0)
        if entry <= 0 or stop <= 0 or stop >= entry:
            continue  # defensiv - der Detektor garantiert das bereits
        events.append({
            "ticker": ticker,
            "index": t,
            "date": str(bars[t].get("date") or t),
            "entry": entry,
            "stop": stop,
            "tp1": float(setup.get("tp1") or 0.0),
            "tp2": float(setup.get("tp2") or 0.0),
            "score": int(setup.get("score") or 0),
            "cup_len": int(setup.get("cup_length") or 0),
            "handle_len": int(setup.get("handle_length") or 0),
            "breakout_rvol": float(setup.get("breakout_rvol") or 0.0),
        })
        blocked_until = t + cooldown
    return events


def evaluate_forward(
    fwd_bars: List[Dict[str, Any]],
    entry: float,
    stop: float,
    tp1: float,
    tp2: float,
    horizon: int = FORWARD_DAYS,
) -> Dict[str, Any]:
    """First-Touch-Auswertung auf den Bars NACH dem Event-Tag - identische
    R-Logik wie deploy/backtest_signal_mails.py:
    konservativ (Stop+Ziel am selben Tag => Stop), TP1 = halbe Position raus,
    Rest: TP2 / Einstand-Stop / Schluss-R am Fensterende."""
    risk = entry - stop
    if risk <= 0:
        return {"outcome": "GEOMETRIE_FEHLER", "tp1_hit": False,
                "r_realized": 0.0, "days_to_outcome": 0, "note": "entry<=stop"}
    if not fwd_bars:
        return {"outcome": "OPEN", "tp1_hit": False, "r_realized": 0.0,
                "days_to_outcome": 0, "note": "keine Forward-Bars (Event am Datenende)"}

    status, tp1_hit, days, note = "", False, 0, ""
    for i, bar in enumerate(fwd_bars, start=1):
        lo = float(bar.get("low") or 0.0)
        hi = float(bar.get("high") or 0.0)
        if lo <= 0 or hi <= 0:
            continue  # kaputte Bars im Forward-Fenster ueberspringen
        target = tp2 if tp1_hit else tp1
        if lo <= stop and hi >= target:
            status, days = "STOP", i
            note = "ambig: Stop+Ziel am selben Tag, konservativ Stop"
            break
        if lo <= stop:
            status, days = "STOP", i
            break
        if not tp1_hit and hi >= tp1:
            tp1_hit = True
        if tp1_hit and hi >= tp2:
            status, days = "TP2", i
            break

    last_close = next(
        (float(b.get("close") or 0.0) for b in reversed(fwd_bars)
         if float(b.get("close") or 0.0) > 0),
        entry,
    )
    open_r = (last_close - entry) / risk
    if status == "STOP" and not tp1_hit:
        outcome, r = "STOP", -1.0
    elif status == "STOP":
        # TP1 vorher erreicht: halbe Position +TP1-R, Rest zum Einstand (0R).
        outcome, r = "TP1_EINSTAND", ((tp1 - entry) / risk) * 0.5
    elif status == "TP2":
        outcome, r = "TP2", ((tp1 - entry) / risk) * 0.5 + ((tp2 - entry) / risk) * 0.5
    elif tp1_hit:
        outcome = "TP1_EXPIRED" if len(fwd_bars) >= horizon else "TP1_OPEN"
        r = ((tp1 - entry) / risk) * 0.5 + open_r * 0.5
        days = len(fwd_bars)
    else:
        outcome = "EXPIRED" if len(fwd_bars) >= horizon else "OPEN"
        r = open_r
        days = len(fwd_bars)
    return {"outcome": outcome, "tp1_hit": tp1_hit, "r_realized": round(r, 4),
            "days_to_outcome": days, "note": note}


def write_events_csv(path: str, rows: List[Dict[str, Any]]) -> None:
    """CSV fuer die Augen-Pruefung: Ticker + Datum in TradingView eintippen."""
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore", restval="")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


# -----------------------------------------------------------------------------
# Report
# -----------------------------------------------------------------------------

def _print_block(label: str, rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print(f"{label:>16}: keine Events")
        return
    rs = [float(r["r_realized"]) for r in rows]
    n = len(rows)
    tp1 = sum(1 for r in rows if r["tp1_hit"])
    stops = sum(1 for r in rows if r["outcome"] == "STOP")
    gains = sum(x for x in rs if x > 0)
    losses = abs(sum(x for x in rs if x < 0))
    if losses > 0:
        pf_txt = f"{gains / losses:.2f}"
    else:
        pf_txt = "inf" if gains > 0 else "n/a"
    print(f"{label:>16}: Events {n:>3} | TP1 vor Stop {tp1 / n * 100:5.1f}% ({tp1}/{n}) | "
          f"Stop -1R {stops / n * 100:5.1f}% ({stops}/{n}) | "
          f"Mittel {statistics.mean(rs):+.2f}R | Median {statistics.median(rs):+.2f}R | "
          f"Summe {sum(rs):+.1f}R | PF {pf_txt}")


def _print_extremes(title: str, rows: List[Dict[str, Any]]) -> None:
    print(f"-- {title} --")
    for r in rows:
        print(f"  {r['ticker']:<6} {r['date']}  Score {r['score']:>3}  "
              f"{r['outcome']:<13} {float(r['r_realized']):+6.2f}R  "
              f"({r['days_to_outcome']} Tage)  Entry {r['entry']} Stop {r['stop']}")


def print_report(
    rows: List[Dict[str, Any]],
    universe: List[str],
    skipped_no_data: List[str],
    skipped_short: List[str],
    errors: List[str],
    out_path: str,
    interrupted: bool,
) -> None:
    print()
    print("=" * 96)
    title = "WALK-FORWARD-VALIDIERUNG CUP & HANDLE"
    if interrupted:
        title += "  [ZWISCHENSTAND - per Ctrl-C abgebrochen]"
    print(title)
    print("=" * 96)
    print(f"Universum: {len(universe)} Ticker | ohne Daten: {len(skipped_no_data)} | "
          f"zu wenig Historie (<{START_INDEX + 1} Bars): {len(skipped_short)} | Fehler: {len(errors)}")
    for label, names in (("ohne Daten", skipped_no_data),
                         ("zu wenig Historie", skipped_short),
                         ("Fehler", errors)):
        if names:
            shown = ", ".join(names[:15]) + (" ..." if len(names) > 15 else "")
            print(f"  {label}: {shown}")

    if not rows:
        print()
        print("KEINE Events gefunden. Entweder ist der Detektor auf echten Charts zu strikt,")
        print("oder Universum/Zeitraum enthalten schlicht keine bestaetigten Cup&Handles.")
    else:
        print()
        print("-- (a) Gesamt --")
        _print_block("GESAMT", rows)
        print()
        print("-- (b) nach Jahr --")
        for year in sorted({str(r["date"])[:4] for r in rows}):
            _print_block(year, [r for r in rows if str(r["date"]).startswith(year)])
        print()
        print("-- (c) nach Score-Bucket (kalibriert: 80-84 / 85-89 / 90+) --")
        _print_block("80-84", [r for r in rows if 80 <= int(r["score"]) <= 84])
        _print_block("85-89", [r for r in rows if 85 <= int(r["score"]) <= 89])
        _print_block("90+   (S)", [r for r in rows if int(r["score"]) >= 90])
        print()
        ranked = sorted(rows, key=lambda r: float(r["r_realized"]), reverse=True)
        _print_extremes("(d) Top 10", ranked[:10])
        _print_extremes("(d) Flop 10", list(reversed(ranked[-10:])))

    print()
    print(f"CSV geschrieben: {out_path} ({len(rows)} Events) - Ticker+Datum in TradingView pruefen.")
    print(FOOTNOTE)


# -----------------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------------

def _parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=("Walk-Forward-Validierung des Cup&Handle-Detektors: der echte "
                     "Produktions-Detektor laeuft Tag fuer Tag ueber historische "
                     "Polygon-Daily-Bars (kein Look-ahead), jeder CONFIRMED-Fund wird "
                     "20 Handelstage forward ausgewertet (First-Touch konservativ)."),
        epilog=("Universum: ~150 kuratierte liquide US-Namen (S&P-500-Querschnitt + "
                "Momentum-Namen + liquide Mid-Caps, keine Mini-Caps). "
                f"Hinweis: die ersten {START_INDEX} Bars je Ticker sind Anlauf-Historie. "
                + FOOTNOTE),
    )
    parser.add_argument("--tickers", default=None,
                        help="Kommagetrennte Ticker-Liste, ueberschreibt das kuratierte Universum "
                             "(z.B. AAPL,MSFT,NVDA)")
    parser.add_argument("--years", type=float, default=2.0,
                        help="Historie in Jahren (Default 2; mehr Jahre = mehr auswertbare Tage, "
                             "die ersten 200 Bars sind immer Anlauf-Historie)")
    parser.add_argument("--max-tickers", type=int, default=150,
                        help="Obergrenze Ticker (Default 150)")
    parser.add_argument("--out", default="events.csv",
                        help="Pfad fuer das Event-CSV (Default events.csv)")
    return parser.parse_args(argv)


def _resolve_universe(args: argparse.Namespace) -> List[str]:
    if args.tickers is not None:
        tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]
    else:
        tickers = list(UNIVERSE)
    seen, ordered = set(), []
    for ticker in tickers:
        if ticker not in seen:
            seen.add(ticker)
            ordered.append(ticker)
    return ordered[: max(0, int(args.max_tickers))]


def main(argv: Optional[List[str]] = None) -> int:
    args = _parse_args(argv)
    universe = _resolve_universe(args)
    if not universe:
        print("Keine Ticker angegeben (--tickers leer / --max-tickers 0) - nichts zu tun.")
        return 0

    key = load_polygon_key()
    if not key:
        print("FEHLER: POLYGON_KEY nicht gefunden (ENV, .env, .streamlit/secrets.toml).")
        return 1

    detect = _load_production_detector()
    print(f"Starte Walk-Forward: {len(universe)} Ticker, {args.years:g} Jahre Historie, "
          f"Detektor-Fenster {LOOKBACK_BARS} Bars, Forward {FORWARD_DAYS} Handelstage, "
          f"Dedupe-Sperre {COOLDOWN_DAYS} Handelstage.")

    rows: List[Dict[str, Any]] = []
    skipped_no_data: List[str] = []
    skipped_short: List[str] = []
    errors: List[str] = []
    interrupted = False
    t0 = time.time()

    try:
        for i, ticker in enumerate(universe, 1):
            try:
                bars = fetch_daily_bars(ticker, args.years, key)
                if not bars:
                    skipped_no_data.append(ticker)
                elif len(bars) <= START_INDEX:
                    skipped_short.append(ticker)
                else:
                    events = walk_forward(ticker, bars, detect)
                    for ev in events:
                        fwd = bars[ev["index"] + 1: ev["index"] + 1 + FORWARD_DAYS]
                        result = evaluate_forward(fwd, ev["entry"], ev["stop"],
                                                  ev["tp1"], ev["tp2"])
                        rows.append({**ev, **result})
            except Exception as exc:  # Detektor ist NaN-fest, trotzdem kein Crash je Ticker
                errors.append(ticker)
                print(f"  {ticker}: FEHLER {type(exc).__name__}: {exc}")
            if i % 10 == 0 or i == len(universe):
                print(f"  [{i}/{len(universe)}] zuletzt {ticker} - Events: {len(rows)}, "
                      f"ohne Daten: {len(skipped_no_data)}, zu kurz: {len(skipped_short)}, "
                      f"Fehler: {len(errors)} ({time.time() - t0:.0f}s)")
            if i < len(universe):
                time.sleep(SLEEP_BETWEEN_TICKERS)
    except KeyboardInterrupt:
        interrupted = True
        print("\nABBRUCH (Ctrl-C) - Zwischenstand wird ausgewertet und geschrieben.")

    write_events_csv(args.out, rows)
    print_report(rows, universe, skipped_no_data, skipped_short, errors, args.out, interrupted)
    return 0


if __name__ == "__main__":
    sys.exit(main())
