"""smart_money_radar.py — Smart-Money-Radar (31.07.2026, Info-Block).

Beantwortet die Betreiber-Frage: "Kauft/verkauft gerade jemand Grosses
massiv?" — ehrlich begrenzt auf das, was man MESSEN kann:

  1. ETF-Flows (BTC-Spot-ETFs: IBIT/FBTC/..., tagesaktuell, Farside) —
     das IST der institutionelle Kauf, aber erst End-of-Day sichtbar.
  2. Volumen-Wellen (RVOL-Welle) in Makro-Instrumenten (SPY/QQQ/GLD/SLV/USO/
     TLT/...) + BTC/ETH — die Fussspur grosser Kauefe/Verkauefe in Minuten.
  3. Whale-Alerts (grosse On-Chain-Transfers, optional via WHALE_ALERT_KEY).

EHERLICHKEITS-REGEL (Betreiber-Vorgabe 31.07.): Dieser Block ist NUR Kontext.
Er wird von KEINEM Scoring-, Gate- oder Trigger-Pfad importiert (Guard-Test
in test_smart_money_radar.py). Niemals "jemand kauft" als Fakt behaupten —
wir zeigen Fussspuren (Flows, Volumen), keine Akteure.

Datenquellen: Farside (HTML, ohne Key), Polygon Aggs (POLYGON_KEY aus ENV),
Whale-Alert (optionaler Key). Jede Sektion traegt eigenen status
(ok/stale/disabled/error) — Teilausfaelle killen den Block nie.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from typing import Any

import requests

FARSIDE_BTC_URL = "https://farside.co.uk/btc/"
WHALE_ALERT_URL = "https://api.whale-alert.io/v1/transactions"

# Makro-Watchlist: hier zeigt sich die "Welle" grosser Kauefe/Verkauefe.
MACRO_SYMBOLS = [
    ("SPY", "S&P 500"), ("QQQ", "Nasdaq 100"), ("IWM", "Russell 2000"),
    ("GLD", "Gold"), ("SLV", "Silber"), ("USO", "Oel (WTI)"),
    ("UNG", "Erdgas"), ("TLT", "US-Anleihen 20+"), ("UUP", "US-Dollar"),
    ("HYG", "High-Yield"), ("EEM", "Emerging Markets"),
]
CRYPTO_SYMBOLS = [("X:BTCUSD", "Bitcoin"), ("X:ETHUSD", "Ethereum")]

WAVE_RVOL_THRESHOLD = 1.8      # ab 1,8x Normal-Volumen = "Welle"
WAVE_BARS_NEEDED = 21          # 1 aktueller + 20 Referenz-Bars
CACHE_TTL_SEC = 30 * 60        # Gesamt-Cache: max 1 Refresh / 30 Min
HTTP_TIMEOUT = 12

_DEFAULT_CACHE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data_cache", "smart_money_radar.json")
)
RADAR_CACHE_PATH = os.environ.get("SMART_MONEY_CACHE", _DEFAULT_CACHE)

DISCLAIMER = ("Kontext-Block: zeigt Fussspuren grosser Geldbewegungen "
              "(ETF-Flows EOD, Volumen-Wellen, Whale-Transfers). Kein Signal, "
              "kein Trigger, keine Kauf-/Verkaufsempfehlung.")


# ── HTTP-Helfer (einzige Netz-Stelle; Tests patchen hier) ────────────────────

def _http_get_text(url: str, timeout: int = HTTP_TIMEOUT) -> str:
    resp = requests.get(url, timeout=timeout, headers={"User-Agent": "AlphaStation/1.0"})
    resp.raise_for_status()
    return resp.text


def _http_get_json(url: str, params: dict | None = None, timeout: int = HTTP_TIMEOUT) -> dict:
    resp = requests.get(url, params=params or {}, timeout=timeout,
                        headers={"User-Agent": "AlphaStation/1.0"})
    resp.raise_for_status()
    return resp.json()


# ── 1) ETF-Flows (Farside, HTML ohne Key) ────────────────────────────────────

class _TableParser(HTMLParser):
    """Minimaler Zeilen/Zellen-Sammler fuer die Farside-Flow-Tabelle."""

    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append("".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)


def _flow_number(raw: str) -> float | None:
    """'123.4' -> 123.4 | '(45.6)' -> -45.6 | '-'/'—'/'' -> None."""
    txt = (raw or "").strip().replace(",", "")
    if not txt or txt in {"-", "—", "–", "n/a"}:
        return None
    neg = txt.startswith("(") and txt.endswith(")")
    txt = txt.strip("() ")
    try:
        val = float(txt)
    except ValueError:
        return None
    return -val if neg else val


def parse_farside_btc_flows(html_text: str, max_rows: int = 10) -> list[dict[str, Any]]:
    """Parst die Farside-BTC-ETF-Tabelle -> [{date, total_musd, ibit_musd}].

    Spalten werden ueber die Header ('Date', 'IBIT', 'Total') lokalisiert —
    fehlt eine, wird tolerant uebersprungen. Wirft bei unbrauchbarem HTML.
    """
    parser = _TableParser()
    parser.feed(html_text or "")
    header_idx: dict[str, int] = {}
    out: list[dict[str, Any]] = []
    for row in parser.rows:
        lowered = [c.strip().lower() for c in row]
        if not header_idx and "date" in lowered and "total" in lowered:
            header_idx = {
                "date": lowered.index("date"),
                "total": lowered.index("total"),
                "ibit": lowered.index("ibit") if "ibit" in lowered else -1,
            }
            continue
        if not header_idx or len(row) <= max(header_idx["date"], header_idx["total"]):
            continue
        date_txt = row[header_idx["date"]].strip()
        if not date_txt or "date" in date_txt.lower() or "total" in date_txt.lower():
            continue
        total = _flow_number(row[header_idx["total"]])
        if total is None:
            continue
        entry: dict[str, Any] = {"date": date_txt, "total_musd": total}
        if header_idx["ibit"] >= 0 and len(row) > header_idx["ibit"]:
            entry["ibit_musd"] = _flow_number(row[header_idx["ibit"]])
        out.append(entry)
        if len(out) >= max_rows:
            break
    if not out:
        raise ValueError("Farside-Tabelle nicht erkannt (Layout geaendert?)")
    return out


def fetch_etf_flows() -> dict[str, Any]:
    """Sektion ETF-Flows. Bei Fehler: status=error, keine Exception."""
    try:
        rows = parse_farside_btc_flows(_http_get_text(FARSIDE_BTC_URL))
        return {
            "status": "ok",
            "as_of": rows[0]["date"],
            "source": "farside.co.uk/btc",
            "note": "Tagesdaten (EOD) — institutionelle ETF-Zu-/Abfluesse in Mio. USD",
            "rows": rows,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "rows": []}


# ── 2) Volumen-Wellen (Polygon Aggs) ─────────────────────────────────────────

def compute_waves(bars_by_symbol: dict[str, list[dict[str, Any]]],
                  rvol_threshold: float = WAVE_RVOL_THRESHOLD) -> list[dict[str, Any]]:
    """Pure: Bars (aelteste->neueste, Keys v=Volume, c=Close) -> Wellen-Liste.

    RVOL = Volumen(letzter Bar) / Ø Volumen(20 Bars davor). wave=True ab
    threshold. Symbole mit zu wenigen Bars werden uebersprungen.
    """
    waves: list[dict[str, Any]] = []
    for symbol, bars in (bars_by_symbol or {}).items():
        if not isinstance(bars, list) or len(bars) < WAVE_BARS_NEEDED:
            continue
        try:
            ref = [float(b["v"]) for b in bars[-WAVE_BARS_NEEDED:-1]]
            last_v = float(bars[-1]["v"])
            last_c = float(bars[-1]["c"])
        except (KeyError, TypeError, ValueError):
            continue
        base = sum(ref) / len(ref) if ref else 0.0
        if base <= 0 or last_v <= 0:
            continue
        rvol = last_v / base
        waves.append({
            "symbol": symbol,
            "rvol": round(rvol, 2),
            "last_volume": last_v,
            "dollar_volume_musd": round(last_v * last_c / 1e6, 1),
            "wave": bool(rvol >= rvol_threshold),
        })
    waves.sort(key=lambda w: w["rvol"], reverse=True)
    return waves


def _polygon_daily_bars(symbol: str, api_key: str, days: int = 45) -> list[dict[str, Any]]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    data = _http_get_json(
        f"https://api.polygon.io/v2/aggs/ticker/{symbol}/range/1/day/{start}/{end}",
        params={"adjusted": "true", "sort": "asc", "limit": 120, "apiKey": api_key},
    )
    return list(data.get("results") or [])


def fetch_volume_waves(api_key: str | None) -> dict[str, Any]:
    """Sektion Volumen-Wellen ueber Makro-ETFs + BTC/ETH (Tages-Restvol.)."""
    if not api_key:
        return {"status": "disabled", "note": "POLYGON_KEY nicht gesetzt", "waves": []}
    labels = dict(MACRO_SYMBOLS + CRYPTO_SYMBOLS)
    bars_by_symbol: dict[str, list[dict[str, Any]]] = {}
    errors: list[str] = []
    for symbol in labels:
        try:
            bars_by_symbol[symbol] = _polygon_daily_bars(symbol, api_key)
        except Exception as exc:
            errors.append(f"{symbol}: {str(exc)[:80]}")
    waves = compute_waves(bars_by_symbol)
    for w in waves:
        w["label"] = labels.get(w["symbol"], w["symbol"])
    status = "ok" if waves else ("error" if errors else "empty")
    out: dict[str, Any] = {
        "status": status,
        "note": f"RVOL-Welle ab {WAVE_RVOL_THRESHOLD}x 20-Tage-Ø (Tagesdaten)",
        "threshold": WAVE_RVOL_THRESHOLD,
        "waves": waves,
    }
    if errors:
        out["partial_errors"] = errors[:5]
        if waves:
            out["status"] = "stale" if len(errors) >= len(labels) else "ok"
    return out


# ── 2b) Monster-Volumen Aktien (Polygon Snapshot + eigene Baseline) ──────────

POLYGON_SNAPSHOT_URL = "https://api.polygon.io/v2/snapshot/locale/us/market/stocks/tickers"
STOCK_WAVE_MIN_DOLLAR_VOL_MUSD = 50.0   # Rauschfilter: unter 50 M$ Tages-$ kein "Gross"
STOCK_WAVE_TOP_N = 15
VOLUME_HISTORY_MAX_DATES = 30           # Rolling-Store: letzte 30 Handelstage
_BASELINE_FULL = 20                     # danach ist die RVOL-Baseline "voll"
_BASELINE_MIN = 5                       # darunter: $-Volumen-Ranking statt RVOL

_DEFAULT_VOL_HIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data_cache", "smart_money_volumes.json")
)
VOLUME_HISTORY_PATH = os.environ.get("SMART_MONEY_VOLUMES", _DEFAULT_VOL_HIST)


def _polygon_market_snapshot(api_key: str) -> tuple[str, list[dict[str, Any]]]:
    """Ganzer US-Markt in EINEM Call. -> (Daten-Datum ET, Zeilen).

    Daten-Datum = juengster updated-Zeitstempel (nanosec) als ET-Kalendertag —
    so landet Wochenend-/Feiertags-Abfrage unter dem letzten Handelstag und
    verfaelscht die Baseline nicht.
    """
    data = _http_get_json(POLYGON_SNAPSHOT_URL,
                          params={"include_otc": "false", "apiKey": api_key})
    rows: list[dict[str, Any]] = []
    max_updated = 0
    for t in (data.get("tickers") or []):
        day = t.get("day") or {}
        vol = day.get("v")
        close = day.get("c")
        if not isinstance(vol, (int, float)) or vol <= 0:
            continue
        if not isinstance(close, (int, float)) or close <= 0:
            continue
        upd = t.get("updated") or 0
        if isinstance(upd, (int, float)):
            max_updated = max(max_updated, int(upd))
        rows.append({
            "ticker": t.get("ticker"),
            "volume": float(vol),
            "close": float(close),
            "change_pct": t.get("todaysChangePerc"),
        })
    if max_updated:
        try:
            from zoneinfo import ZoneInfo
            data_date = datetime.fromtimestamp(
                max_updated / 1e9, tz=ZoneInfo("America/New_York")).date().isoformat()
        except Exception:
            data_date = datetime.now(timezone.utc).date().isoformat()
    else:
        data_date = datetime.now(timezone.utc).date().isoformat()
    return data_date, rows


def update_volume_history(history: dict[str, Any] | None, date_str: str,
                          today_rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Rolling-Store {dates, volumes{ticker:{date:vol}}} — idempotent je Tag,
    auf die letzten VOLUME_HISTORY_MAX_DATES Tage gekuerzt (pure)."""
    hist = history if isinstance(history, dict) else {}
    dates = [d for d in (hist.get("dates") or []) if isinstance(d, str)]
    volumes = hist.get("volumes") if isinstance(hist.get("volumes"), dict) else {}
    if date_str not in dates:
        dates.append(date_str)
        dates.sort()
    for row in today_rows:
        tk = row.get("ticker")
        if not tk:
            continue
        per = volumes.setdefault(tk, {})
        per[date_str] = row["volume"]
    if len(dates) > VOLUME_HISTORY_MAX_DATES:
        drop = set(dates[:-VOLUME_HISTORY_MAX_DATES])
        dates = dates[-VOLUME_HISTORY_MAX_DATES:]
        for tk in list(volumes):
            per = volumes[tk]
            for d in drop:
                per.pop(d, None)
            if not per:
                volumes.pop(tk, None)
    return {"dates": dates, "volumes": volumes}


def history_to_lists(history: dict[str, Any], before_date: str) -> dict[str, list[float]]:
    """{ticker: [Volumen der Tage VOR before_date, chronologisch]} (pure)."""
    dates = [d for d in (history.get("dates") or []) if d < before_date]
    dates.sort()
    out: dict[str, list[float]] = {}
    for tk, per in (history.get("volumes") or {}).items():
        series = [per[d] for d in dates if isinstance(per.get(d), (int, float))]
        if series:
            out[tk] = series
    return out


def compute_stock_waves(today_rows: list[dict[str, Any]],
                        history_lists: dict[str, list[float]],
                        min_dollar_vol_musd: float = STOCK_WAVE_MIN_DOLLAR_VOL_MUSD,
                        rvol_threshold: float = WAVE_RVOL_THRESHOLD,
                        top_n: int = STOCK_WAVE_TOP_N) -> tuple[list[dict[str, Any]], int]:
    """Pure: heutige Zeilen + Baseline-Listen -> (Wellen, baseline_tage).

    RVOL nur mit >= 3 Baseline-Tagen; sonst rvol=None und Ranking nach
    $-Volumen. direction aus change_pct (>0 Kauf-, <0 Verkaufswelle).
    """
    baseline_days = max((len(v) for v in history_lists.values()), default=0)
    waves: list[dict[str, Any]] = []
    for row in today_rows:
        dollar_musd = row["volume"] * row["close"] / 1e6
        if dollar_musd < min_dollar_vol_musd:
            continue
        series = history_lists.get(row["ticker"]) or []
        rvol = None
        if len(series) >= 3:
            base = sum(series[-_BASELINE_FULL:]) / len(series[-_BASELINE_FULL:])
            if base > 0:
                rvol = round(row["volume"] / base, 2)
        chg = row.get("change_pct")
        waves.append({
            "ticker": row["ticker"],
            "rvol": rvol,
            "dollar_volume_musd": round(dollar_musd, 1),
            "change_pct": round(float(chg), 2) if isinstance(chg, (int, float)) else None,
            "direction": "up" if isinstance(chg, (int, float)) and chg > 0 else "down",
            "wave": bool(rvol is not None and rvol >= rvol_threshold),
        })
    waves.sort(key=lambda w: (w["rvol"] or 0.0, w["dollar_volume_musd"]), reverse=True)
    return waves[:top_n], baseline_days


def fetch_stock_waves(api_key: str | None, history_path: str | None = None) -> dict[str, Any]:
    """Sektion Monster-Volumen Aktien: heutige $-Riesen vs. eigene 20-Tage-Baseline."""
    if not api_key:
        return {"status": "disabled", "note": "POLYGON_KEY nicht gesetzt", "waves": []}
    path = history_path or VOLUME_HISTORY_PATH
    try:
        data_date, rows = _polygon_market_snapshot(api_key)
        history = _read_cache(path) or {}
        history_lists = history_to_lists(history, data_date)
        waves, baseline_days = compute_stock_waves(rows, history_lists)
        _write_cache(path, update_volume_history(history, data_date, rows))
        building = baseline_days < _BASELINE_FULL
        note = (f"Baseline im Aufbau ({baseline_days}/{_BASELINE_FULL} Tage) — "
                "Ranking vorlaeufig nach $-Volumen" if building else
                f"RVOL vs. eigene {min(baseline_days, _BASELINE_FULL)}-Tage-Baseline")
        return {
            "status": "ok" if waves else "empty",
            "baseline_days": baseline_days,
            "building": building,
            "data_date": data_date,
            "note": (note + f" · Filter: >= ${STOCK_WAVE_MIN_DOLLAR_VOL_MUSD:.0f}M Tages-$-Volumen"
                     f" · Welle ab {WAVE_RVOL_THRESHOLD}x"),
            "waves": waves,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "waves": []}


# ── 2c) Insider-Trades (SEC EDGAR Form 4, gratis + namentlich) ───────────────

EDGAR_CURRENT_F4_URL = ("https://www.sec.gov/cgi-bin/browse-edgar"
                        "?action=getcurrent&type=4&owner=include&count=20&output=atom")
SEC_USER_AGENT = os.environ.get("SEC_USER_AGENT", "AlphaStation/1.0 (trading-research)")
INSIDER_MIN_VALUE_USD = 100_000.0   # Rauschfilter: nur Open-Market-Deals >= $100k
INSIDER_MAX_FILINGS = 12            # max. XML-Dateien pro Refresh (Fair-Use)


def parse_atom_feed(atom_text: str) -> list[dict[str, str]]:
    """EDGAR-Atom (neueste Form-4-Filings) -> [{title, link, updated}]."""
    import xml.etree.ElementTree as ET
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(atom_text)
    out: list[dict[str, str]] = []
    for entry in root.findall("a:entry", ns):
        link_el = entry.find("a:link", ns)
        href = (link_el.get("href") or "") if link_el is not None else ""
        if not href:
            continue
        out.append({
            "title": (entry.findtext("a:title", default="", namespaces=ns) or "").strip(),
            "link": href,
            "updated": (entry.findtext("a:updated", default="", namespaces=ns) or "").strip(),
        })
    if not out:
        raise ValueError("EDGAR-Atom ohne Eintraege (Layout geaendert?)")
    return out


def _filing_xml_url(index_link: str) -> str | None:
    """Filing-Index-URL -> primaere XML-Datei (via EDGAR index.json)."""
    # .../data/{cik}/{acc_nodash}/{acc}-index.htm -> .../index.json
    folder = index_link.rsplit("/", 1)[0]
    listing = _http_get_json(folder + "/index.json")
    for item in ((listing.get("directory") or {}).get("item") or []):
        name = str(item.get("name") or "")
        if name.endswith(".xml") and not name.startswith("R"):
            return f"{folder}/{name}"
    return None


def _xml_value(node, *path: str) -> str:
    """ownershipDocument-Helfer: verschachtelten .value-Text holen ('' wenn leer)."""
    cur = node
    for tag in path:
        cur = cur.find(tag) if cur is not None else None
        if cur is None:
            return ""
    val = cur.find("value")
    return (val.text or "").strip() if val is not None and val.text else ""


def parse_form4_xml(xml_text: str, link: str = "") -> list[dict[str, Any]]:
    """ownershipDocument -> Open-Market-Deals (P=Kauf, S=Verkauf) als Zeilen.

    Nur nonDerivative-Transaktionen mit Code P/S und Wert >=
    INSIDER_MIN_VALUE_USD. Gibt [] zurueck, wenn nichts Relevantes dabei ist.
    """
    import xml.etree.ElementTree as ET
    root = ET.fromstring(xml_text)
    issuer = _xml_value(root, "issuer", "issuerName")
    ticker = _xml_value(root, "issuer", "issuerTradingSymbol")
    owner_el = root.find("reportingOwner")
    insider = ""
    title = ""
    if owner_el is not None:
        insider = _xml_value(owner_el, "reportingOwnerId", "rptOwnerName")
        rel = owner_el.find("reportingOwnerRelationship")
        if rel is not None:
            title = _xml_value(rel, "officerTitle")
            if not title:
                flags = []
                if (rel.findtext("isDirector") or "0") in ("1", "true"):
                    flags.append("Director")
                if (rel.findtext("isOfficer") or "0") in ("1", "true"):
                    flags.append("Officer")
                if (rel.findtext("isTenPercentOwner") or "0") in ("1", "true"):
                    flags.append("10%-Owner")
                title = "/".join(flags)
    rows: list[dict[str, Any]] = []
    table = root.find("nonDerivativeTable")
    if table is None:
        return rows
    for tx in table.findall("nonDerivativeTransaction"):
        code = _xml_value(tx, "transactionCoding", "transactionCode")
        if code not in ("P", "S"):
            continue  # nur echte Open-Market-Kaeufe/-Verkauefe
        try:
            shares = float(_xml_value(tx, "transactionAmounts", "transactionShares") or 0)
            price = float(_xml_value(tx, "transactionAmounts", "transactionPricePerShare") or 0)
        except ValueError:
            continue
        value = shares * price
        if value < INSIDER_MIN_VALUE_USD:
            continue
        rows.append({
            "issuer": issuer, "ticker": ticker, "insider": insider,
            "title": title, "kind": "buy" if code == "P" else "sell",
            "shares": shares, "price": price, "value_usd": round(value, 0),
            "date": _xml_value(tx, "transactionDate"), "link": link,
        })
    return rows


def _fetch_latest_form4_trades() -> tuple[list[dict[str, Any]], int, int]:
    """Neueste Form-4-Filings von EDGAR -> (alle P/S-Trades, geparste Filings,
    Fehlerzahl). Geteilt von Anzeige-Sektion und Cluster-Verlauf."""
    headers = {"User-Agent": SEC_USER_AGENT}
    atom_resp = requests.get(EDGAR_CURRENT_F4_URL, timeout=HTTP_TIMEOUT,
                             headers=headers)
    atom_resp.raise_for_status()
    entries = parse_atom_feed(atom_resp.text)
    trades: list[dict[str, Any]] = []
    errors = 0
    parsed = 0
    for entry in entries:
        if parsed >= INSIDER_MAX_FILINGS:
            break
        try:
            xml_url = _filing_xml_url(entry["link"])
            if not xml_url:
                continue
            resp = requests.get(xml_url, timeout=HTTP_TIMEOUT, headers=headers)
            resp.raise_for_status()
            trades.extend(parse_form4_xml(resp.text, link=entry["link"]))
            parsed += 1
        except Exception:
            errors += 1
    trades.sort(key=lambda r: r["value_usd"], reverse=True)
    return trades, parsed, errors


def fetch_insider_trades() -> dict[str, Any]:
    """Sektion Insider-Trades: neueste Form 4 von SEC EDGAR, P/S >= $100k."""
    try:
        trades, parsed, errors = _fetch_latest_form4_trades()
        out: dict[str, Any] = {
            "status": "ok" if trades else "empty",
            "note": (f"Open-Market-Kaeufe/-Verkaeufe von Insidern (Form 4), "
                     f">= ${INSIDER_MIN_VALUE_USD / 1000:.0f}k — SEC EDGAR, "
                     f"1–2 Tage Verspaetung · {parsed} Filings geprueft"),
            "trades": trades[:15],
        }
        if errors:
            out["partial_errors"] = errors
        return out
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "trades": []}


# ── 2d) Insider-Cluster (staerkstes Insider-Signal: 3+ Kauefer, gleiche Firma) ─

CLUSTER_WINDOW_DAYS = 14
CLUSTER_MIN_INSIDERS = 3
INSIDER_HISTORY_MAX_AGE_DAYS = 45

_DEFAULT_INS_HIST = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data_cache", "insider_trades_history.json")
)
INSIDER_HISTORY_PATH = os.environ.get("INSIDER_HISTORY", _DEFAULT_INS_HIST)


def _trade_key(trade: dict[str, Any]) -> str:
    """Dedupe-Schluessel je Insider-Deal (Filing-Link + Richtung + Stueck/Kurs)."""
    return "|".join(str(trade.get(k) or "") for k in
                    ("link", "ticker", "insider", "kind", "date", "shares", "price"))


def update_insider_history(history: dict[str, Any] | None,
                           trades: list[dict[str, Any]],
                           today: str) -> dict[str, Any]:
    """Rolling-Verlauf {trades: {key: trade}, last_dates} — dedupliziert,
    aelteste Eintraege > INSIDER_HISTORY_MAX_AGE_DAYS werden gekuerzt (pure)."""
    hist = history if isinstance(history, dict) else {}
    store = hist.get("trades") if isinstance(hist.get("trades"), dict) else {}
    for trade in trades:
        key = _trade_key(trade)
        if key.strip("|"):
            store[key] = trade
    try:
        cutoff = (datetime.strptime(today, "%Y-%m-%d")
                  - timedelta(days=INSIDER_HISTORY_MAX_AGE_DAYS)).date().isoformat()
        store = {k: t for k, t in store.items()
                 if str(t.get("date") or "9999") >= cutoff}
    except Exception:
        pass
    return {"trades": store, "updated": today}


def detect_insider_clusters(trades: list[dict[str, Any]], today: str,
                            window_days: int = CLUSTER_WINDOW_DAYS,
                            min_insiders: int = CLUSTER_MIN_INSIDERS) -> dict[str, Any]:
    """Pure: Verlauf -> Cluster (>= min_insiders verschiedene Insider, gleiche
    Firma, gleiche Richtung, innerhalb window_days).

    Kauf-Cluster sind das historisch dokumentierte Signal (Lakonishok & Lee);
    Verkauf-Cluster werden separat ausgewiesen (schwaecheres Signal).
    """
    try:
        cutoff = (datetime.strptime(today, "%Y-%m-%d")
                  - timedelta(days=window_days)).date().isoformat()
    except Exception:
        return {"clusters": [], "window_days": window_days, "considered": 0}
    recent = [t for t in trades if str(t.get("date") or "") >= cutoff]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for t in recent:
        key = (str(t.get("ticker") or t.get("issuer") or "?"), str(t.get("kind") or ""))
        groups.setdefault(key, []).append(t)
    clusters: list[dict[str, Any]] = []
    for (symbol, kind), group in groups.items():
        insiders = sorted({str(t.get("insider") or "?") for t in group})
        if len(insiders) < min_insiders:
            continue
        total = sum(float(t.get("value_usd") or 0) for t in group)
        sample = group[0]
        clusters.append({
            "symbol": symbol,
            "issuer": sample.get("issuer") or symbol,
            "side": kind,
            "insiders": len(insiders),
            "names": insiders[:6],
            "total_value_usd": round(total, 0),
            "trades": len(group),
            "latest_date": max(str(t.get("date") or "") for t in group),
        })
    clusters.sort(key=lambda c: (c["side"] != "buy", -c["insiders"],
                                 -c["total_value_usd"]))
    return {"clusters": clusters, "window_days": window_days,
            "considered": len(recent)}


def fetch_insider_clusters(history_path: str | None = None) -> dict[str, Any]:
    """Sektion Insider-Cluster: baut den Verlauf aus den jeweils neuesten
    Filings auf und erkennt Kauf-/Verkauf-Cluster ueber 14 Tage."""
    path = history_path or INSIDER_HISTORY_PATH
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            trades, _parsed, _errors = _fetch_latest_form4_trades()
        except Exception:
            trades = []  # EDGAR down -> Verlauf allein weiterverwenden
        history = _read_cache(path) or {}
        history = update_insider_history(history, trades, today)
        _write_cache(path, history)
        all_trades = list((history.get("trades") or {}).values())
        result = detect_insider_clusters(all_trades, today)
        dates = sorted({str(t.get("date") or "") for t in all_trades if t.get("date")})
        history_days = len(dates)
        building = history_days < CLUSTER_WINDOW_DAYS
        note = (f"Verlauf im Aufbau ({history_days}/{CLUSTER_WINDOW_DAYS} Tage) — "
                "Cluster erscheinen, sobald genug Tage gesammelt sind"
                if building else
                f"Fenster {CLUSTER_WINDOW_DAYS} Tage · >= {CLUSTER_MIN_INSIDERS} Insider "
                f"pro Cluster · {result['considered']} Deals im Fenster")
        return {
            "status": "ok" if result["clusters"] else ("building" if building else "empty"),
            "building": building,
            "history_days": history_days,
            "note": note,
            "clusters": result["clusters"],
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "clusters": []}


# ── 3) Whale-Alerts (optional, WHALE_ALERT_KEY) ──────────────────────────────

def classify_whale_tx(tx: dict[str, Any]) -> str:
    """'exchange_inflow' (Verkaufsdruck) | 'exchange_outflow' (Akkumulation) |
    'wallet_to_wallet' (neutral)."""
    frm = str(((tx.get("from") or {}).get("owner_type")) or "").lower()
    to = str(((tx.get("to") or {}).get("owner_type")) or "").lower()
    if to == "exchange" and frm != "exchange":
        return "exchange_inflow"
    if frm == "exchange" and to != "exchange":
        return "exchange_outflow"
    return "wallet_to_wallet"


def fetch_whale_alerts(api_key: str | None, lookback_sec: int = 6 * 3600,
                       min_value_usd: int = 5_000_000) -> dict[str, Any]:
    """Sektion Whale-Alerts (grosse On-Chain-Transfers, letzte 6h)."""
    if not api_key:
        return {"status": "disabled",
                "note": "WHALE_ALERT_KEY nicht gesetzt — Sektion deaktiviert",
                "transactions": []}
    try:
        data = _http_get_json(WHALE_ALERT_URL, params={
            "api_key": api_key,
            "start": int(time.time()) - lookback_sec,
            "min_value": min_value_usd,
        })
        txs = []
        for tx in (data.get("transactions") or [])[:25]:
            txs.append({
                "symbol": tx.get("symbol"),
                "blockchain": tx.get("blockchain"),
                "amount_usd": tx.get("amount_usd"),
                "kind": classify_whale_tx(tx),
                "timestamp": tx.get("timestamp"),
                "hash": tx.get("hash"),
            })
        inflow = sum(1 for t in txs if t["kind"] == "exchange_inflow")
        outflow = sum(1 for t in txs if t["kind"] == "exchange_outflow")
        return {
            "status": "ok",
            "note": (f"Transfers >= {min_value_usd // 1_000_000} Mio. USD, "
                     f"letzte {lookback_sec // 3600}h"),
            "summary": {"count": len(txs), "exchange_inflow": inflow,
                        "exchange_outflow": outflow},
            "transactions": txs,
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)[:200], "transactions": []}


# ── Gesamt-Block mit Cache ───────────────────────────────────────────────────

def build_radar(polygon_key: str | None = None, whale_key: str | None = None,
                cache_path: str | None = None, ttl_sec: int = CACHE_TTL_SEC,
                force_refresh: bool = False) -> dict[str, Any]:
    """Baut den Radar-Block. Wirft NIE; nutzt frischen Cache oder Stale-Fallback."""
    path = cache_path or RADAR_CACHE_PATH
    now = time.time()
    if not force_refresh:
        cached = _read_cache(path)
        if cached and now - float(cached.get("_cached_at") or 0) < ttl_sec:
            cached["cache"] = "fresh"
            return cached
    polygon_key = polygon_key if polygon_key is not None else os.environ.get("POLYGON_KEY")
    whale_key = whale_key if whale_key is not None else os.environ.get("WHALE_ALERT_KEY")
    try:
        radar = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "disclaimer": DISCLAIMER,
            "sections": {
                "etf_flows": fetch_etf_flows(),
                "volume_waves": fetch_volume_waves(polygon_key),
                "stock_waves": fetch_stock_waves(polygon_key),
                "insider_trades": fetch_insider_trades(),
                "insider_clusters": fetch_insider_clusters(),
                "whale_alerts": fetch_whale_alerts(whale_key),
            },
            "_cached_at": now,
            "cache": "new",
        }
        _write_cache(path, radar)
        return radar
    except Exception as exc:  # Doppelfang — Sektionen fangen bereits selbst
        stale = _read_cache(path)
        if stale:
            stale["cache"] = "stale"
            return stale
        return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "disclaimer": DISCLAIMER, "sections": {}, "cache": "error",
                "error": str(exc)[:200]}


def _read_cache(path: str) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_cache(path: str, radar: dict[str, Any]) -> None:
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(radar, fh, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        pass
