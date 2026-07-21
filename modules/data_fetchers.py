"""
Data Fetchers Module — Extrahiert aus scanner.py (V69.9)

Reine API-Funktionen ohne Streamlit-Abhängigkeiten:
- rate_limited_get: Rate-limiter für alle API-Calls
- CoinGecko: Candles, Historical Data
- Polygon.io: Stock Data, OHLCV, News, Details
- Alpaca: Realtime Prices
- Backtest: Daily Data
"""
import re
import json
import time
import threading
import requests
import datetime as dt
from datetime import datetime, timedelta

# Rate limiter state (thread-safe)
_rate_lock = threading.Lock()
_last_api_call = 0
_api_call_count = 0
_api_call_window_start = 0

# Candle analysis cache (in-memory, resets on restart)
_CANDLE_ANALYSIS_CACHE = {}
_CANDLE_CACHE_TTL = 300  # 5 Minuten
_COINGECKO_INTRADAY_MAX_DAYS = 90

# BPIQ catalyst cache — fetches from BPIQ API if key is available
import os as _os
from pathlib import Path as _Path
_BPIQ_CATALYST_CACHE = {}
_BPIQ_CACHE_TIMESTAMP = 0
_BPIQ_CACHE_TTL = 3600  # 1 hour
_BPIQ_CATALYST_STATUS = {
    "status": "unknown",
    "http_status": None,
    "error": None,
    "rows_loaded": 0,
    "ticker_count": 0,
    "timestamp": None,
}

def _first_nonempty(*values):
    for value in values:
        if value is None:
            continue
        if isinstance(value, dict):
            nested = _first_nonempty(value.get("name"), value.get("company_name"), value.get("ticker"))
            if nested:
                return nested
        if isinstance(value, str) and value.strip():
            return value.strip()
        if not isinstance(value, str) and value:
            return value
    return ""


def _get_config_value(key):
    """Read config from env first, then the repo/root secrets files used by API/bg service."""
    value = _os.getenv(key, "")
    if value:
        return value
    paths = [
        _Path.home() / ".streamlit" / "secrets.toml",
        _Path(__file__).resolve().parents[1] / ".streamlit" / "secrets.toml",
        _Path(__file__).resolve().parents[1] / ".env",
    ]
    for path in paths:
        try:
            if not path.exists():
                continue
            for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                cfg_key, cfg_val = line.split("=", 1)
                if cfg_key.strip() == key:
                    return cfg_val.strip().strip('"').strip("'")
        except Exception:
            continue
    return ""

# N-a (Biotech-Audit 10.06.): Fuzzy-Quartals-/Halbjahres-Daten.
# "Q3 2026"/"H2 2026" haben kein exaktes catalyst_date — vorher fiel der
# Readout still aus der Wertung. Jetzt: MITTE des Zeitraums als Schaetzdatum.
_FUZZY_QH_RE = re.compile(r"\b([QH])\s*([1-4])\s*[,/'’-]?\s*((?:20)\d{2}|\d{2})\b", re.IGNORECASE)
_FUZZY_QH_RE_YEARFIRST = re.compile(r"\b((?:20)\d{2})\s*([QH])\s*([1-4])\b", re.IGNORECASE)


def _parse_fuzzy_catalyst_date(text):
    """
    N-a (10.06.): 'Q3 2026' / 'H2 2026' → ISO-Datum der Zeitraums-MITTE.
    Q1→15.02., Q2→15.05., Q3→15.08., Q4→15.11. | H1→01.04., H2→01.10.
    Returns ISO-String 'YYYY-MM-DD' oder None.
    """
    if not text:
        return None
    _s = str(text)
    _kind = _num = _year = None
    _m = _FUZZY_QH_RE.search(_s)
    if _m:
        _kind, _num, _year = _m.group(1).upper(), int(_m.group(2)), _m.group(3)
    else:
        _m2 = _FUZZY_QH_RE_YEARFIRST.search(_s)
        if _m2:
            _year, _kind, _num = _m2.group(1), _m2.group(2).upper(), int(_m2.group(3))
    if not _kind:
        return None
    try:
        _year = int(_year)
    except (TypeError, ValueError):
        return None
    if _year < 100:
        _year += 2000
    if _kind == "Q" and 1 <= _num <= 4:
        _md = {1: "02-15", 2: "05-15", 3: "08-15", 4: "11-15"}[_num]
    elif _kind == "H" and _num in (1, 2):
        _md = {1: "04-01", 2: "10-01"}[_num]
    else:
        return None
    return f"{_year}-{_md}"


def _load_bpiq_catalyst_cache():
    """
    Lädt ALLE Drugs mit Catalyst-Dates von BPIQ in einen In-Memory-Cache.
    Korrekte Implementation: Pagination, Drug-Parsing, Category-Berechnung.
    Cache-TTL: 4 Stunden (BPIQ Daten werden täglich aktualisiert).

    Returns:
        dict: {TICKER: [{drug_name, stage_label, catalyst_date, catalyst_date_text,
                         days_until, category, phase_mult, ...}], ...}
    """
    global _BPIQ_CATALYST_CACHE, _BPIQ_CACHE_TIMESTAMP, _BPIQ_CATALYST_STATUS

    bpiq_key = _get_config_value("BPIQ_API_KEY")
    if not bpiq_key:
        _BPIQ_CATALYST_STATUS = {
            "status": "warning",
            "http_status": None,
            "error": "BPIQ_API_KEY missing",
            "rows_loaded": 0,
            "ticker_count": 0,
            "timestamp": datetime.now().isoformat(),
        }
        return {}

    # Use one central TTL so status text and actual refresh behavior cannot drift.
    now = time.time()
    if _BPIQ_CATALYST_CACHE and (now - _BPIQ_CACHE_TIMESTAMP) < _BPIQ_CACHE_TTL:
        return _BPIQ_CATALYST_CACHE

    try:
        prior_cache = _BPIQ_CATALYST_CACHE
        all_drugs = []
        api_error_status = None
        response_complete = False
        offset = 0
        page_limit = 200
        max_rows = 3000
        while offset < max_rows:
            resp = rate_limited_get(
                f"https://api.bpiq.com/api/v1/drugs/?has_catalyst=true&limit={page_limit}&offset={offset}",
                headers={"Authorization": f"Token {bpiq_key}"},
                timeout=15
            )
            if resp.status_code != 200:
                api_error_status = resp.status_code
                _BPIQ_CATALYST_STATUS = {
                    "status": "warning",
                    "http_status": resp.status_code,
                    "error": f"BPIQ returned HTTP {resp.status_code}",
                    "rows_loaded": len(all_drugs),
                    "ticker_count": len(_BPIQ_CATALYST_CACHE),
                    "timestamp": datetime.now().isoformat(),
                }
                print(f"[CatalystData] API HTTP {resp.status_code}; Catalyst-Daten nicht geladen")
                break
            data = resp.json()
            results = data.get("results", [])
            if not results:
                response_complete = True
                break
            all_drugs.extend(results)
            if len(results) < page_limit:
                response_complete = True
                break
            if "next" in data and not data.get("next"):
                response_complete = True
                break
            offset += page_limit

        # Never replace a complete cache with a rate-limited or truncated page set.
        if not response_complete:
            reason = (
                f"BPIQ returned HTTP {api_error_status}"
                if api_error_status is not None
                else f"BPIQ pagination exceeded safety limit ({max_rows} rows)"
            )
            _BPIQ_CATALYST_STATUS = {
                "status": "warning",
                "http_status": api_error_status,
                "error": reason,
                "rows_loaded": len(all_drugs),
                "ticker_count": len(prior_cache),
                "partial_response_discarded": True,
                "using_stale_cache": bool(prior_cache),
                "timestamp": datetime.now().isoformat(),
            }
            print(f"[CatalystData] Incomplete response discarded: {reason}")
            return prior_cache or {}

        # Gruppiere nach Ticker mit vollständiger Daten-Aufbereitung
        cache = {}
        # N-c (Biotech-Audit 10.06.): UTC-DATUM statt naiver Server-now() —
        # naive Zeit konnte T-0 als OVERDUE (days_until=-1) einstufen.
        _today_utc = datetime.now(dt.timezone.utc).date()
        for drug in all_drugs:
            ticker = drug.get("ticker", "").upper()
            if not ticker:
                continue

            cat_date = drug.get("catalyst_date")
            cat_text = drug.get("catalyst_date_text", "TBA")
            stage = drug.get("stage_event", {})
            stage_label = stage.get("stage_label", "")
            event_label = stage.get("event_label", "")
            full_label = stage.get("label", "")
            bpiq_score = stage.get("score", 0)

            # Tage bis Catalyst berechnen + Datum validieren
            days_until = None
            date_estimated = False
            _had_invalid_date = False
            if cat_date:
                try:
                    _cd = datetime.strptime(cat_date[:10], "%Y-%m-%d")
                    days_until = (_cd.date() - _today_utc).days  # N-c: Datums-Arithmetik in UTC
                except (ValueError, TypeError):
                    # Ungültiges Datum (z.B. "2026-03-35") → Fuzzy-Fallback unten
                    cat_date = None
                    _had_invalid_date = True
            if days_until is None:
                # N-a (10.06.): Fuzzy-Quartale ("Q3 2026"/"H2 2026") → Mitte des
                # Zeitraums als Schaetzdatum statt stillem Verwurf des Readouts.
                _fuzzy_iso = _parse_fuzzy_catalyst_date(cat_text)
                if _fuzzy_iso:
                    cat_date = _fuzzy_iso
                    date_estimated = True
                    days_until = (datetime.strptime(_fuzzy_iso, "%Y-%m-%d").date() - _today_utc).days
                elif _had_invalid_date:
                    cat_text = "TBA"

            # Kategorie bestimmen
            category = ""
            if days_until is not None:
                if days_until < 0:
                    if abs(days_until) <= 90:
                        category = "OVERDUE"
                elif days_until <= 30:
                    category = "IMMINENT"
                elif days_until <= 90:
                    category = "UPCOMING"
                elif days_until <= 365:
                    category = "LATER"

            # Phase-Multiplikator
            phase_mult = 1.0
            if "Phase 3" in stage_label or "PDUFA" in stage_label:
                phase_mult = 3.0
            elif "Phase 2" in stage_label:
                phase_mult = 2.0
            elif "Phase 1" in stage_label:
                phase_mult = 1.0
            else:
                phase_mult = 0.5

            entry = {
                "company_name": _first_nonempty(
                    drug.get("company_name"),
                    drug.get("company"),
                    drug.get("company_full_name"),
                    drug.get("issuer_name"),
                    drug.get("sponsor"),
                ),
                "drug_name": drug.get("drug_name", "")[:60],
                "stage_label": stage_label,
                "event_label": event_label,
                "full_label": full_label,
                "catalyst_date": cat_date,
                "catalyst_date_text": cat_text,
                "days_until": days_until,
                "date_estimated": date_estimated,  # N-a (10.06.): Quartals-/Halbjahres-Schaetzung
                "category": category,
                "phase_mult": phase_mult,
                "bpiq_score": bpiq_score,
                "indications": drug.get("indications_text", ""),
                "note": (drug.get("note", "") or "")[:200],
                "source": drug.get("catalyst_source", ""),
                "is_new": bool(
                    drug.get("is_new")
                    or drug.get("new")
                    or drug.get("is_new_catalyst")
                    or drug.get("new_catalyst")
                ),
                "is_big_mover": bool(drug.get("is_big_mover")),
                "is_hedge_fund_pick": bool(drug.get("is_hedge_fund_pick")),
                "is_hedge_fund_avoid": bool(drug.get("is_hedge_fund_avoid")),
                "is_high_mgmt_interest": bool(drug.get("is_high_mgmt_interest")),
                "is_suspected_mover": bool(drug.get("is_suspected_mover")),
            }

            if ticker not in cache:
                cache[ticker] = []
            cache[ticker].append(entry)

        # Future readouts are actionable context. Past estimated dates are
        # retained as warnings, but must never outrank a confirmed future date.
        cat_order = {"IMMINENT": 0, "UPCOMING": 1, "LATER": 2, "OVERDUE": 8, "": 9}
        for ticker in cache:
            cache[ticker].sort(key=lambda x: (
                cat_order.get(x["category"], 9),
                (
                    abs(x["days_until"])
                    if x["category"] == "OVERDUE" and x["days_until"] is not None
                    else x["days_until"] if x["days_until"] is not None else 9999
                ),
            ))

        if not cache:
            _BPIQ_CATALYST_STATUS = {
                "status": "warning",
                "http_status": 200,
                "error": "BPIQ returned no catalyst rows",
                "rows_loaded": len(all_drugs),
                "ticker_count": len(prior_cache),
                "partial_response_discarded": False,
                "using_stale_cache": bool(prior_cache),
                "timestamp": datetime.now().isoformat(),
            }
            return prior_cache or {}

        _BPIQ_CATALYST_CACHE = cache
        _BPIQ_CACHE_TIMESTAMP = now
        _BPIQ_CATALYST_STATUS = {
            "status": "success",
            "http_status": 200,
            "error": None,
            "rows_loaded": len(all_drugs),
            "ticker_count": len(cache),
            "partial_response_discarded": False,
            "using_stale_cache": False,
            "timestamp": datetime.now().isoformat(),
        }
        print(f"[CatalystData] Cache geladen: {len(all_drugs)} Drugs, {len(cache)} Tickers")
        return cache

    except Exception as e:
        _BPIQ_CATALYST_STATUS = {
            "status": "error",
            "http_status": None,
            "error": str(e),
            "rows_loaded": 0,
            "ticker_count": len(_BPIQ_CATALYST_CACHE),
            "timestamp": datetime.now().isoformat(),
        }
        print(f"[CatalystData] FEHLER beim Laden: {e}")
        return _BPIQ_CATALYST_CACHE or {}


def _is_late_stage_bpiq_event(drug):
    """True for the newsletter-style Phase 2/3/PDUFA readout universe."""
    text = " ".join(
        str(drug.get(k, "") or "")
        for k in ("stage_label", "event_label", "full_label", "note", "indications")
    ).lower()
    phase_markers = (
        "phase 2", "phase ii", "phase iib", "phase 2b", "ph2", "ph 2",
        "phase 3", "phase iii", "phase ii/iii", "phase 2/3", "ph3", "ph 3",
        "pdufa", "nda", "bla",
    )
    return any(marker in text for marker in phase_markers)


def _public_catalyst_warning(status, rows):
    """Map raw provider/API errors to product-safe user-facing messages."""
    raw_error = (status.get("error") or "").lower()
    http_status = status.get("http_status")
    if http_status == 401 or "401" in raw_error or "unauthor" in raw_error:
        return "Catalyst-Datenquelle ist nicht autorisiert. Bitte Datenzugang im Admin-Bereich prüfen."
    if http_status == 429 or "429" in raw_error or "rate" in raw_error:
        return "Catalyst-Datenquelle ist aktuell limitiert. Die Watchlist kann unvollständig sein."
    if "missing" in raw_error or "not configured" in raw_error:
        return "Catalyst-Datenquelle ist nicht verbunden. Bitte Datenzugang im Admin-Bereich prüfen."
    if status.get("status") not in ("success", "unknown") and raw_error:
        return "Catalyst-Datenquelle ist aktuell nicht verfügbar. Bitte später erneut prüfen."
    if not rows:
        return "Keine passenden Phase-2/3- oder PDUFA-Catalysts im aktuellen Zeitfenster gefunden."
    return None


def get_premium_catalyst_tickers(window_days=90, include_overdue_days=30):
    """Return late-stage catalyst-calendar tickers that should seed the Bio scanner."""
    cache = _load_bpiq_catalyst_cache()
    tickers = set()
    for ticker, drugs in cache.items():
        if not ticker:
            continue
        for drug in drugs:
            days_until = drug.get("days_until")
            if days_until is None:
                continue
            if days_until < -abs(int(include_overdue_days or 0)) or days_until > int(window_days or 90):
                continue
            if not _is_late_stage_bpiq_event(drug):
                continue
            tickers.add(ticker.upper())
            break
    return tickers


def get_bpiq_catalyst_watchlist(limit=85, window_days=None):
    """
    Supplemental BPIQ catalyst watchlist for the Biotech scanner.

    The BPIQ newsletter advertises a weekly watchlist of Phase 2/3 readouts.
    We do not scrape the newsletter image/table. Instead, this returns the
    machine-readable BPIQ calendar rows when the API key works, so the UI can
    show catalyst context without turning newsletter marketing into blind
    scan results.
    """
    now = datetime.now()
    if window_days is None:
        h1_end = datetime(now.year, 6, 30)
        if now <= h1_end:
            window_days = max(14, (h1_end - now).days + 1)
        else:
            window_days = 90

    cache = _load_bpiq_catalyst_cache()
    rows = []
    for ticker, drugs in cache.items():
        for drug in drugs:
            days_until = drug.get("days_until")
            if days_until is None:
                continue
            if days_until < -30 or days_until > window_days:
                continue
            if not _is_late_stage_bpiq_event(drug):
                continue
            rows.append({
                "ticker": ticker,
                "company_name": drug.get("company_name", ""),
                "drug_name": drug.get("drug_name", ""),
                "stage_label": drug.get("stage_label", ""),
                "event_label": drug.get("event_label", ""),
                "full_label": drug.get("full_label", ""),
                "catalyst_date": drug.get("catalyst_date"),
                "catalyst_date_text": drug.get("catalyst_date_text", "TBA"),
                "days_until": days_until,
                "category": drug.get("category", ""),
                "catalyst_score": drug.get("bpiq_score", 0),
                "phase_mult": drug.get("phase_mult", 0),
                "indications": drug.get("indications", ""),
                "source": "Premium catalyst calendar",
                "is_new": bool(drug.get("is_new")),
            })

    cat_order = {"IMMINENT": 0, "UPCOMING": 1, "LATER": 2, "OVERDUE": 8, "": 9}
    rows.sort(key=lambda x: (
        cat_order.get(x.get("category", ""), 9),
        (
            abs(x.get("days_until"))
            if x.get("category") == "OVERDUE" and x.get("days_until") is not None
            else x.get("days_until") if x.get("days_until") is not None else 9999
        ),
        -float(x.get("catalyst_score") or 0),
        x.get("ticker", ""),
    ))
    rows = rows[:max(1, int(limit or 85))]
    by_month = {}
    new_count = 0
    for row in rows:
        if row.get("is_new"):
            new_count += 1
        month_key = "TBA"
        cat_date = row.get("catalyst_date")
        if cat_date:
            try:
                month_key = datetime.strptime(cat_date[:10], "%Y-%m-%d").strftime("%B %Y")
            except Exception:
                month_key = row.get("catalyst_date_text") or "TBA"
        by_month[month_key] = by_month.get(month_key, 0) + 1

    status = dict(_BPIQ_CATALYST_STATUS)
    if not cache and status.get("status") in ("unknown", "success"):
        status.update({
            "status": "warning",
            "error": "No BPIQ catalyst rows loaded",
            "timestamp": datetime.now().isoformat(),
        })
    data_source_error = status.get("error") if status.get("status") != "success" else None
    warning = _public_catalyst_warning(status, rows)

    return {
        "status": "success" if rows and not data_source_error else "warning",
        "count": len(rows),
        "data": rows,
        "summary": {
            "new_catalysts": new_count,
            "may_2026_catalysts": by_month.get("May 2026", 0),
            "june_2026_catalysts": by_month.get("June 2026", 0),
            "total_catalysts": len(rows),
            "by_month": by_month,
        },
        "window_days": window_days,
        "source": "Premium catalyst calendar",
        "source_url": None,
        "newsletter_context": {
            "title": "Catalyst Watchlist",
            "newsletter_subject": "Weekly Catalyst Watchlist",
            "newsletter_date": "2026-05-03",
            "newsletter_claim": "85 Ph2 & Ph3 readouts in Q2 2026 / H1 2026",
            "note": "Watchlist rows come from the configured premium catalyst calendar when connected.",
        },
        "provider_status": {
            "status": status.get("status"),
            "http_status": status.get("http_status"),
            "rows_loaded": status.get("rows_loaded", 0),
            "ticker_count": status.get("ticker_count", 0),
            "timestamp": status.get("timestamp"),
        },
        "warning": warning,
        "timestamp": datetime.now().isoformat(),
    }

# Catalyst detection keywords (used by _detect_catalyst)
CATALYST_KEYWORDS = {
    " EARNINGS": {"keywords": ["earnings", "revenue", "profit", "EPS", "guidance", "quarterly", "fiscal", "beat", "miss", "outlook"], "sentiment": "neutral"},
    " FDA/BIO": {"keywords": ["FDA", "approval", "trial", "phase", "drug", "clinical", "PDUFA", "NDA", "breakthrough", "therapy", "patent"], "sentiment": "neutral"},
    "[!!] OFFERING": {"keywords": ["offering", "dilution", "shelf", "secondary", "ATM", "warrant", "convertible", "raise", "registered direct", "public offering"], "sentiment": "bearish"},
    " M&A": {"keywords": ["acquisition", "merger", "takeover", "buyout", "deal", "purchase agreement"], "sentiment": "bullish"},
    " CONTRACT": {"keywords": ["contract", "awarded", "partnership", "agreement", "collaboration", "deal with"], "sentiment": "bullish"},
    " LEGAL": {"keywords": ["lawsuit", "SEC", "investigation", "settlement", "subpoena", "fraud", "class action", "indictment"], "sentiment": "bearish"},
    "UP UPGRADE": {"keywords": ["upgrade", "price target", "buy rating", "overweight", "outperform"], "sentiment": "bullish"},
    "DN DOWNGRADE": {"keywords": ["downgrade", "sell rating", "underweight", "underperform", "cut"], "sentiment": "bearish"},
    "[!!] REVERSE SPLIT": {"keywords": ["reverse split", "reverse stock split", "r/s"], "sentiment": "bearish"},
    " STOCK SPLIT": {"keywords": ["stock split", "forward split"], "sentiment": "bullish"},
    " DIVIDEND": {"keywords": ["dividend", "payout", "distribution"], "sentiment": "bullish"},
    " INSIDER": {"keywords": ["insider", "CEO buy", "director purchase", "10b5"], "sentiment": "bullish"},
    "[>>] PRODUCT": {"keywords": ["launch", "release", "new product", "unveil", "announce"], "sentiment": "bullish"},
    " BANKRUPTCY": {"keywords": ["bankruptcy", "chapter 11", "chapter 7", "delisting", "going concern"], "sentiment": "bearish"},
}


# ── Prozessübergreifender Polygon-Budget-Limiter (Datei-basiert) ─────────────
# Problem: api.py (uvicorn) und bg_service.py sind GETRENNTE Prozesse. Der
# In-Prozess-Limiter in rate_limited_get() zählt pro Prozess => effektiv
# 2x calls_per_minute gegen Polygon => 429er. Lösung: EIN gemeinsames
# Minuten-Budget (epoch//60 + Zähler) in einer /tmp-Datei — beide Prozesse
# sehen dasselbe /tmp (PrivateTmp=false, verifiziert) — serialisiert über
# fcntl.flock auf einer separaten Lock-Datei.
#
# Modul-Konstanten (KEINE Funktions-Defaults), damit Tests sie per
# monkeypatch auf tmp_path umbiegen können; Auflösung erst zur Laufzeit.
POLYGON_BUDGET_FILE = "/tmp/polygon_rate_budget.json"
POLYGON_BUDGET_LOCK_FILE = "/tmp/polygon_rate_budget.lock"
SHARED_BUDGET_MAX_WAIT_S = 65.0  # Schutz: nie länger als ~1 Fenster blockieren

try:
    import fcntl as _fcntl  # Linux/Unix — auf Windows-Dev-Umgebung nicht verfügbar
except ImportError:  # pragma: no cover
    class _NoopFcntl:
        LOCK_EX = 2
        LOCK_UN = 8

        @staticmethod
        def flock(*_args, **_kwargs):
            return None

    _fcntl = _NoopFcntl()

# Nach erstem flock-/IO-Fehler: permanenter Fallback auf In-Prozess-Limiter
# (einmalige Warnung, niemals crashen, niemals Calls verlieren).
_shared_budget_failed = False


def _shared_budget_enabled():
    """Notausstieg: ENV POLYGON_SHARED_BUDGET=0 => altes In-Prozess-Verhalten."""
    flag = _os.getenv("POLYGON_SHARED_BUDGET", "1").strip().lower()
    return flag not in ("0", "false", "no", "off")


def _shared_budget_per_min():
    """Gesamt-Budget pro Minute über ALLE Prozesse (api + bg zusammen).

    ENV POLYGON_BUDGET_PER_MIN, Default 200 — bewusst NICHT der per-Prozess-
    Parameter calls_per_minute, denn der gilt nur innerhalb eines Prozesses.
    """
    try:
        return max(1, int(_os.getenv("POLYGON_BUDGET_PER_MIN", "200")))
    except (TypeError, ValueError):
        return 200


def _shared_budget_try_consume(budget):
    """Ein read→check→increment→write-Zyklus unter fcntl.flock.

    Returns (acquired, wait_seconds). Das Lock-Fenster ist bewusst minimal
    (nur Datei-I/O, kein HTTP, kein Sleep) — typisch <1ms, damit sind auch
    Tausende Calls/min über beide Prozesse problemlos.
    Korrupte/fehlende Budget-Datei => Selbstheilung (Zähler neu ab 0).
    OSError (flock/open/write) propagiert zum Caller => dort Fallback.
    Schreiben als einfaches Truncate-Write: unter Lock reicht das, kein
    tmp+rename nötig (Leser laufen ebenfalls nur unter dem Lock).
    """
    with open(POLYGON_BUDGET_LOCK_FILE, "a") as lock_fh:
        _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_EX)
        try:
            now = time.time()
            window = int(now // 60)
            count = 0
            try:
                with open(POLYGON_BUDGET_FILE, "r") as fh:
                    state = json.load(fh)
                if int(state.get("window", -1)) == window:
                    count = max(0, int(state.get("count", 0)))
            except (OSError, ValueError, TypeError, AttributeError):
                count = 0  # fehlt/korrupt/kein dict => neu initialisieren
            if count < budget:
                with open(POLYGON_BUDGET_FILE, "w") as fh:
                    json.dump({"window": window, "count": count + 1}, fh)
                return True, 0.0
            # Fenster voll: bis zum nächsten Minuten-Fenster warten — gleiche
            # Semantik wie der In-Prozess-Limiter (sleep bis Fensterwechsel).
            wait_s = min(max(60.0 - (now % 60.0), 0.05), SHARED_BUDGET_MAX_WAIT_S)
            return False, wait_s
        finally:
            _fcntl.flock(lock_fh.fileno(), _fcntl.LOCK_UN)


def _acquire_shared_polygon_token():
    """Blockiert, bis ein Token aus dem prozessübergreifenden Budget frei ist.

    True  => Token konsumiert, der HTTP-Call darf raus.
    False => Shared-Limiter inaktiv (ENV-Override, kein fcntl, flock-Fehler);
             der Caller verlässt sich dann allein auf den In-Prozess-Limiter.
    Crasht nie und verliert nie Calls: jeder Fehlerpfad endet im Fallback,
    jeder Wartepfad in einem Retry mit Gesamt-Deckel SHARED_BUDGET_MAX_WAIT_S
    (danach wird der Call durchgelassen statt ewig zu blockieren).
    """
    global _shared_budget_failed
    if not _shared_budget_enabled():
        return False
    if _shared_budget_failed:
        return False
    if _fcntl is None:
        _shared_budget_failed = True
        print("[RATE] WARN: fcntl nicht verfügbar — Shared-Polygon-Budget deaktiviert, Fallback auf In-Prozess-Limiter")
        return False
    deadline = time.time() + SHARED_BUDGET_MAX_WAIT_S
    while True:
        try:
            acquired, wait_s = _shared_budget_try_consume(_shared_budget_per_min())
        except Exception as exc:  # flock/IO-Fehler (z.B. exotisches FS)
            _shared_budget_failed = True
            print(f"[RATE] WARN: Shared-Polygon-Budget deaktiviert ({exc.__class__.__name__}: {exc}) — Fallback auf In-Prozess-Limiter")
            return False
        if acquired:
            return True
        remaining = deadline - time.time()
        if remaining <= 0:
            # Max-Wartezeit-Schutz: lieber durchlassen (429 wird upstream
            # behandelt) als einen Scan-Thread dauerhaft zu blockieren.
            return True
        time.sleep(min(wait_s, remaining))


# ── rate_limited_get (originally line 895) ──
def rate_limited_get(url, params=None, timeout=15, calls_per_minute=200, **kwargs):
    """Rate-limited requests.get() — thread-safe, wartet automatisch wenn zu viele Calls.

    Zweistufig:
      1) Prozessübergreifendes Minuten-Budget (Datei + flock): api.py und
         bg_service.py teilen sich EIN Polygon-Budget (ENV POLYGON_BUDGET_PER_MIN,
         Default 200 GESAMT). Notausstieg: POLYGON_SHARED_BUDGET=0.
      2) In-Prozess-Limiter (unten) bleibt als Sekundär-Schutz: liefert den
         0.05s-Mindestabstand (Burst-Glättung, die das Minuten-Budget nicht
         abdeckt) und ist der alleinige Limiter im Fallback-Fall (kein fcntl /
         flock-Fehler / ENV-Override).

    Default 200 calls/min (Polygon paid plans erlauben deutlich mehr als 75).
    Akzeptiert alle kwargs die requests.get() auch akzeptiert (headers, etc.)
    """
    global _last_api_call, _api_call_count, _api_call_window_start

    # Stufe 1: gemeinsames Budget über beide Prozesse (blockiert ggf. bis
    # Fensterwechsel). Bei False greift ausschließlich Stufe 2 (Fallback).
    _acquire_shared_polygon_token()

    sleep_time = 0
    with _rate_lock:
        now = time.time()

        # Reset Counter jede Minute
        if now - _api_call_window_start > 60:
            _api_call_count = 0
            _api_call_window_start = now

        # Warte wenn Limit erreicht
        if _api_call_count >= calls_per_minute:
            sleep_time = max(0, 60 - (now - _api_call_window_start))

        # Minimum 0.05s zwischen Calls (20/sec max)
        if sleep_time == 0:
            elapsed = now - _last_api_call
            if elapsed < 0.05:
                sleep_time = 0.05 - elapsed

    # Sleep AUSSERHALB des Locks (andere Threads nicht blockieren)
    if sleep_time > 0:
        time.sleep(sleep_time)

    with _rate_lock:
        # Nach dem Sleep: Counter ggf. resetten
        now = time.time()
        if now - _api_call_window_start > 60:
            _api_call_count = 0
            _api_call_window_start = now
        _last_api_call = now
        _api_call_count += 1

    return requests.get(url, params=params, timeout=timeout, **kwargs)


# ── fetch_daily_candles_crypto (originally line 1358) ──
def fetch_daily_candles_crypto(coin_id, days=30):
    """
    Build UTC daily candles from CoinGecko intraday market-chart samples.

    CoinGecko's /ohlc endpoint changes its bar size with the requested range.
    Treating those responses as daily bars corrupts every daily indicator, so
    only validated intraday samples are aggregated into explicit UTC days.
    """
    import time as _t

    try:
        requested_days = max(2, int(days or 30))
    except (TypeError, ValueError):
        return []
    if requested_days > _COINGECKO_INTRADAY_MAX_DAYS:
        return []

    cache_key = f"crypto_{coin_id}_{requested_days}"
    cached = _CANDLE_ANALYSIS_CACHE.get(cache_key)
    if cached and (_t.time() - cached["ts"]) < _CANDLE_CACHE_TTL:
        return cached["data"]

    try:
        from datetime import datetime as _dt

        def _fetch_json(url, params):
            resp = None
            for _attempt in range(2):
                if _attempt > 0:
                    _t.sleep(1.5)
                try:
                    resp = rate_limited_get(url, params=params, timeout=10)
                except Exception:
                    if _attempt < 1:
                        continue
                    return None
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except Exception:
                        return None
                if resp.status_code == 429:
                    _t.sleep(3)
                    continue
                if resp.status_code == 404:
                    return None
            return None

        market_url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        market_data = _fetch_json(market_url, {"vs_currency": "usd", "days": requested_days})
        prices = (market_data or {}).get("prices", [])
        volumes = (market_data or {}).get("total_volumes", [])

        valid_price_samples = []
        for entry in prices or []:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            try:
                ts = int(entry[0])
                price = float(entry[1])
            except (TypeError, ValueError):
                continue
            if ts > 0 and price > 0:
                valid_price_samples.append((ts, price))
        valid_price_samples.sort(key=lambda item: item[0])
        if len(valid_price_samples) < 4:
            return []

        sample_gaps = [
            (valid_price_samples[idx][0] - valid_price_samples[idx - 1][0]) / 3_600_000.0
            for idx in range(1, len(valid_price_samples))
            if valid_price_samples[idx][0] > valid_price_samples[idx - 1][0]
        ]
        median_gap_hours = sorted(sample_gaps)[len(sample_gaps) // 2] if sample_gaps else 999.0
        # Daily close samples cannot produce real daily highs/lows. Fail closed
        # rather than labelling a close proxy as an OHLC candle.
        if median_gap_hours > 6.0:
            return []

        daily_volume = {}
        for entry in volumes or []:
            if isinstance(entry, (list, tuple)) and len(entry) > 1:
                day_key = _dt.fromtimestamp(entry[0] / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")
                try:
                    volume = float(entry[1] or 0)
                except (TypeError, ValueError):
                    continue
                if volume >= 0:
                    daily_volume[day_key] = volume

        volume_available = any(value > 0 for value in daily_volume.values())

        daily = {}
        for ts, price in valid_price_samples:
            day_key = _dt.fromtimestamp(ts / 1000, tz=dt.timezone.utc).strftime("%Y-%m-%d")
            if day_key not in daily:
                daily[day_key] = {
                    "t": ts,
                    "o": price,
                    "h": price,
                    "l": price,
                    "c": price,
                    "v": daily_volume.get(day_key, 0),
                    "volume_is_estimate": True,
                    "volume_available": volume_available,
                    "data_quality": (
                        "aggregated_intraday_ohlcv_estimate"
                        if volume_available
                        else "aggregated_intraday_price_only"
                    ),
                    "source": "coingecko_market_chart_aggregated",
                    "source_timeframe": "1D",
                    "source_sample_interval_hours": round(median_gap_hours, 3),
                }
            else:
                daily[day_key]["h"] = max(daily[day_key]["h"], price)
                daily[day_key]["l"] = min(daily[day_key]["l"], price)
                daily[day_key]["c"] = price
                daily[day_key]["v"] = daily_volume.get(day_key, daily[day_key]["v"])
        bars = [daily[key] for key in sorted(daily)]

        if requested_days and len(bars) > requested_days + 2:
            bars = bars[-(requested_days + 2):]

        if len(bars) < 2:
            return []
        # A price-only response can render a chart, but must be retried instead
        # of being cached as complete OHLCV data.
        if volume_available:
            _CANDLE_ANALYSIS_CACHE[cache_key] = {"data": bars, "ts": _t.time()}
        return bars
    except Exception:
        return []


# ── fetch_daily_candles (originally line 1406) ──
def fetch_daily_candles(poly_key, ticker, days=30):
    """
    Holt Daily Candles von Polygon mit Cache (5 Min TTL).
    Returns: list of dicts mit o, h, l, c, v, t oder leere Liste
    """
    import time as _t
    from datetime import datetime as _dt, timedelta as _td

    cache_key = f"{ticker}_{days}"
    cached = _CANDLE_ANALYSIS_CACHE.get(cache_key)
    if cached and (_t.time() - cached["ts"]) < _CANDLE_CACHE_TTL:
        return cached["data"]

    try:
        end_date = _dt.utcnow().date()
        start_date = end_date - _td(days=days + 10)  # Extra Buffer für Wochenenden/Feiertage
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": days + 10}, timeout=5)
        if resp.status_code != 200:
            return []
        bars = resp.json().get("results", [])
        _CANDLE_ANALYSIS_CACHE[cache_key] = {"data": bars, "ts": _t.time()}
        return bars
    except Exception:
        return []


# ── fetch_multi_day_data (originally line 1461) ──
def fetch_multi_day_data(ticker, api_key, days=5):
    """
    Holt Multi-Day OHLCV Daten von Polygon für echte Pattern-Analyse.
    
    Returns: Liste von Dictionaries mit {date, open, high, low, close, volume}
             Sortiert von ältestem zu neuestem Tag
    """
    try:
        from datetime import datetime, timedelta
        
        end_date = datetime.now()
        # Buffer fuer Wochenenden + Feiertage: ~1.5x fuer kurze, ~1.4x fuer laengere Zeitraeume
        buffer = max(7, int(days * 0.5))
        start_date = end_date - timedelta(days=days + buffer)
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        params = {"adjusted": "true", "sort": "asc", "apiKey": api_key}
        
        resp = rate_limited_get(url, params=params, timeout=15)
        data = resp.json()
        
        if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
            return []
        
        results = []
        for bar in data["results"][-days:]:  # Letzte N Tage
            _c = bar.get("c", 0)
            if not _c or _c <= 0:
                continue  # Skip invalid zero-price bars
            results.append({
                "date": datetime.fromtimestamp(bar.get("t", 0) / 1000).strftime("%Y-%m-%d") if bar.get("t") else "",
                "open": bar.get("o", _c),
                "high": bar.get("h", _c),
                "low": bar.get("l", _c),
                "close": _c,
                "volume": bar.get("v", 0)
            })
        
        return results
    except Exception as e:
        return []


# ── fetch_historical_data_crypto (originally line 6104) ──
def fetch_historical_data_crypto(coin_id, days):
    """
    Holt historische OHLC-Daten von CoinGecko via market_chart (hourly → daily aggregation).

    CoinGecko /ohlc Endpoint gibt für >30 Tage nur 4-Tages-Candles (zu wenig Daten).
    Stattdessen: /market_chart mit days≤90 gibt stündliche Preise (24/Tag),
    die wir zu echten täglichen OHLC-Bars aggregieren.

    Returns: [[timestamp_ms, open, high, low, close], ...] — tägliche Bars
    """
    from datetime import datetime as _dt

    # CoinGecko gibt stündliche Daten nur für days ≤ 90
    try:
        fetch_days = max(2, int(days or 30))
    except (TypeError, ValueError):
        return None
    # Never silently return a shorter history than the caller requested.
    if fetch_days > _COINGECKO_INTRADAY_MAX_DAYS:
        return None

    try:
        url = f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
        params = {"vs_currency": "usd", "days": fetch_days}

        # CoinGecko Free API: ~10 Calls/Min → 7s Pause + Retry bei 429
        for attempt in range(3):
            time.sleep(7 if attempt > 0 else 2)  # 2s vor erstem Call, 7s bei Retry
            resp = rate_limited_get(url, params=params, timeout=15)
            if resp.status_code == 200:
                break
            elif resp.status_code == 429:
                time.sleep(15)  # Extra Cooldown bei Rate Limit
                continue
            else:
                return None

        if resp.status_code != 200:
            return None

        data = resp.json()
        prices = data.get("prices", [])
        if not prices or len(prices) < 48:  # Mindestens 2 Tage stündliche Daten
            return None

        # Prüfe ob wir wirklich stündliche Daten haben (Intervall < 4h)
        if len(prices) > 1:
            interval_h = (prices[1][0] - prices[0][0]) / 3_600_000
            if interval_h > 4:  # Tägliche Daten statt stündliche → kein echtes H/L
                return None

        # Aggregiere stündliche Preise zu täglichen OHLC-Bars
        daily_map = {}
        for ts, price in prices:
            day_key = _dt.utcfromtimestamp(ts / 1000).strftime("%Y-%m-%d")
            if day_key not in daily_map:
                daily_map[day_key] = {"ts": ts, "open": price, "high": price, "low": price, "close": price}
            else:
                daily_map[day_key]["high"] = max(daily_map[day_key]["high"], price)
                daily_map[day_key]["low"] = min(daily_map[day_key]["low"], price)
                daily_map[day_key]["close"] = price

        # Konvertiere zurück zu [[ts, o, h, l, c], ...] Format
        result = []
        for day_key in sorted(daily_map.keys()):
            d = daily_map[day_key]
            result.append([d["ts"], d["open"], d["high"], d["low"], d["close"]])

        return result if len(result) >= 5 else None

    except Exception:
        return None


# ── fetch_historical_data_stocks (originally line 6171) ──
def fetch_historical_data_stocks(ticker, days, poly_key):
    """Holt historische Daten — Polygon für US, Yahoo für internationale Aktien"""
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _is_intl = any(ticker.upper().endswith(s) for s in _intl_suffixes)
    
    if _is_intl:
        return _fetch_historical_yahoo(ticker, days)
    
    # US-Aktien: Polygon
    try:
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
        params = {"apiKey": poly_key, "limit": days}
        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            if results:
                return [[r.get("t", 0), r.get("o", 0), r.get("h", 0), r.get("l", 0), r.get("c", 0), r.get("v", 0)] for r in results if r.get("c", 0) > 0]
    except Exception as e:
        pass
    return None


# ── fetch_ohlcv_for_chart (originally line 6262) ──
def fetch_ohlcv_for_chart(ticker, poly_key, timeframe="1H", bars=300):
    """
    Holt OHLCV Daten für Chart-Darstellung.
    
    V67.5: Yahoo Finance Fallback für internationale Aktien + Forex + Futures + Krypto
    
    Args:
        ticker: Ticker Symbol (z.B. "AAPL", "VNA.DE", "EURUSD=X", "BTC-USD")
        poly_key: Polygon API Key
        timeframe: "5m", "15m", "1H", "4H", "1D", "1W"
        bars: Anzahl Bars (wird pro Timeframe angepasst)
        
    Returns:
        List of dicts with time, open, high, low, close, volume
    """
    # Erkennung: Ist das ein internationaler/Yahoo-Ticker?
    _intl_suffixes = (".DE", ".L", ".SW", ".PA", ".AS", ".BR", ".T", ".HK")
    _yahoo_patterns = ("=X", "=F", "-USD", "-EUR", "-GBP")
    _is_yahoo = any(ticker.upper().endswith(s) for s in _intl_suffixes + _yahoo_patterns)
    
    # Krypto-Tickers (CoinGecko IDs → Yahoo Format)
    if not _is_yahoo and ticker.upper() in ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "DOGE", "AVAX", "DOT", "MATIC"):
        ticker = f"{ticker.upper()}-USD"
        _is_yahoo = True
    
    if _is_yahoo:
        return _fetch_ohlcv_yahoo(ticker, timeframe)
    else:
        return _fetch_ohlcv_polygon(ticker, poly_key, timeframe)


# ── fetch_realtime_price_alpaca (originally line 8045) ──
def fetch_realtime_price_alpaca(ticker, alpaca_key, alpaca_secret):
    """
    Holt REALTIME Preis von Alpaca (kostenlos mit Account!)
    
    Alpaca bietet kostenlose Realtime-Daten für US-Aktien.
    Erstelle Account auf alpaca.markets und hole API Keys.
    
    Returns: dict mit price, change, change_pct, timestamp oder None
    """
    try:
        # Alpaca Latest Quote API
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/quotes/latest"
        headers = {
            "APCA-API-KEY-ID": alpaca_key,
            "APCA-API-SECRET-KEY": alpaca_secret
        }
        
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            quote = data.get("quote", {})
            
            # Bid/Ask Midpoint als Preis
            bid = quote.get("bp", 0)
            ask = quote.get("ap", 0)
            
            if bid > 0 and ask > 0:
                price = (bid + ask) / 2
                timestamp = quote.get("t", "")
                
                return {
                    "price": round(price, 2),
                    "bid": bid,
                    "ask": ask,
                    "spread": round(ask - bid, 4),
                    "timestamp": timestamp,
                    "source": "Alpaca Realtime"
                }
        
        # Fallback: Latest Trade
        url = f"https://data.alpaca.markets/v2/stocks/{ticker}/trades/latest"
        resp = rate_limited_get(url, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            trade = data.get("trade", {})
            price = trade.get("p", 0)
            
            if price > 0:
                return {
                    "price": round(price, 2),
                    "timestamp": trade.get("t", ""),
                    "source": "Alpaca Realtime"
                }
    except Exception as e:
        pass
    return None


# ── fetch_realtime_price_polygon (originally line 8103) ──
def fetch_realtime_price_polygon(ticker, poly_key):
    """
    Holt REALTIME Preis von Polygon (benötigt Stocks Starter oder höher!)
    
    Nutzt den Single-Ticker Snapshot für schnellste Updates.
    
    Returns: dict mit price, change_pct, volume oder None
    """
    try:
        # Single Ticker Snapshot = schnellster Realtime-Endpoint
        url = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers/{ticker}"
        params = {"apiKey": poly_key}
        
        resp = rate_limited_get(url, params=params, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            ticker_data = data.get("ticker", {})
            
            if ticker_data:
                last_trade = ticker_data.get("lastTrade", {})
                day = ticker_data.get("day", {})
                prev = ticker_data.get("prevDay", {})
                
                # Preis aus lastTrade (REALTIME!)
                price = last_trade.get("p", 0) or day.get("c", 0)
                
                if price > 0:
                    prev_close = prev.get("c", 0)
                    change_pct = ((price - prev_close) / prev_close * 100) if prev_close > 0 else 0
                    
                    # Timestamp vom letzten Trade
                    timestamp = last_trade.get("t", 0)
                    if timestamp:
                        # Nanoseconds to datetime
                        from datetime import datetime
                        try:
                            ts_seconds = timestamp / 1e9
                            trade_time = datetime.fromtimestamp(ts_seconds)
                            time_str = trade_time.strftime("%H:%M:%S")
                        except Exception as e:
                            time_str = ""
                    else:
                        time_str = ""
                    
                    return {
                        "price": round(price, 2),
                        "change_pct": round(change_pct, 2),
                        "volume": day.get("v", 0),
                        "high": day.get("h", price),
                        "low": day.get("l", price),
                        "time": time_str,
                        "source": "Polygon Realtime"
                    }
    except Exception as e:
        pass
    return None


# ── get_ticker_news (originally line 10461) ──
def get_ticker_news(poly_key, ticker, limit=3):
    """
    Holt die neuesten News für einen Ticker via Polygon News API.
    NEU: Katalysator-Erkennung (Earnings, FDA, Offering, etc.)
    Returns: List of news items with title, sentiment, published date, catalyst
    """
    
    try:
        url = f"https://api.polygon.io/v2/reference/news"
        _resp = rate_limited_get(url, params={"ticker": ticker, "limit": limit, "apiKey": poly_key}, timeout=5)
        if _resp.status_code != 200:
            return []
        resp = _resp.json()
        results = resp.get("results", [])
        
        news_items = []
        detected_catalysts = []
        
        for item in results[:limit]:
            # Parse published date
            pub_date = item.get("published_utc", "")[:10]  # YYYY-MM-DD
            
            # Sentiment analysieren (wenn vorhanden)
            insights = item.get("insights", [])
            sentiment = "neutral"
            sentiment_score = 0
            for insight in insights:
                if insight.get("ticker") == ticker:
                    sentiment = insight.get("sentiment", "neutral")
                    sentiment_score = insight.get("sentiment_reasoning", "")
                    break
            
            # Katalysator erkennen
            title = item.get("title", "")
            catalyst = _detect_catalyst(title)
            if catalyst and catalyst not in detected_catalysts:
                detected_catalysts.append(catalyst)
            
            news_items.append({
                "title": title[:80],  # Kürzen
                "publisher": item.get("publisher", {}).get("name", ""),
                "published": pub_date,
                "sentiment": sentiment,
                "url": item.get("article_url", ""),
                "catalyst": catalyst,
            })
        
        # Haupt-Katalysator an alle News-Items anhängen
        for n in news_items:
            n["all_catalysts"] = detected_catalysts
        
        return news_items
    except Exception as e:
        return []


# ── get_ticker_details (originally line 10514) ──
def get_ticker_details(poly_key, ticker):
    """
    Holt Ticker Details: Shares Outstanding, Market Cap, etc.
    Returns: dict mit shares_outstanding, market_cap, float_category
    """
    try:
        url = f"https://api.polygon.io/v3/reference/tickers/{ticker}"
        _resp = rate_limited_get(url, params={"apiKey": poly_key}, timeout=5)
        if _resp.status_code != 200:
            return {"shares_outstanding": 0, "market_cap": 0, "float_category": "unknown", "cik": ""}
        resp = _resp.json()
        results = resp.get("results", {})
        
        shares_out = results.get("share_class_shares_outstanding", 0) or results.get("weighted_shares_outstanding", 0)
        market_cap = results.get("market_cap", 0)
        
        # Float Kategorie schätzen (Shares Outstanding als Proxy)
        # Echtes Float = Shares - Insider - Institutional, aber das haben wir nicht
        float_category = "UNKNOWN"
        float_emoji = "?"
        
        if shares_out > 0:
            shares_millions = shares_out / 1_000_000
            if shares_millions < 10:
                float_category = "MICRO"
                float_emoji = "[*][*][*]"  # Sehr explosiv
            elif shares_millions < 20:
                float_category = "LOW"
                float_emoji = "[*][*]"  # Explosiv
            elif shares_millions < 50:
                float_category = "MEDIUM"
                float_emoji = "[*]"
            else:
                float_category = "HIGH"
                float_emoji = ""
        
        return {
            "shares_outstanding": shares_out,
            "shares_millions": round(shares_out / 1_000_000, 1) if shares_out > 0 else 0,
            "market_cap": market_cap,
            "market_cap_millions": round(market_cap / 1_000_000, 1) if market_cap > 0 else 0,
            "float_category": float_category,
            "float_emoji": float_emoji,
            "name": results.get("name", ""),
            "description": results.get("description", "")[:100] if results.get("description") else "",
            "cik": str(results.get("cik") or "").strip(),
        }
    except Exception as e:
        return {
            "shares_outstanding": 0,
            "shares_millions": 0,
            "market_cap": 0,
            "market_cap_millions": 0,
            "float_category": "UNKNOWN",
            "float_emoji": "?",
            "name": "",
            "description": "",
            "cik": "",
        }


# ── fetch_backtest_daily_data (originally line 12113) ──
def fetch_backtest_daily_data(poly_key, ticker, start_date, end_date):
    """
    Holt tägliche OHLCV-Daten von Polygon für Backtesting.
    Includes retry logic für Rate Limits (429).
    """
    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date}/{end_date}"
    params = {"adjusted": "true", "sort": "asc", "limit": 5000, "apiKey": poly_key}
    
    for attempt in range(3):
        try:
            resp = rate_limited_get(url, params=params, timeout=15)
            
            if resp.status_code == 429:
                time.sleep(12 + attempt * 5)
                continue
            
            if resp.status_code != 200:
                # Speichere Fehler für Debug-Anzeige
                _err = f"{ticker}: HTTP {resp.status_code}"
                try:
                    _err += f" | {resp.text[:150]}"
                except:
                    pass
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            data = resp.json()
            
            if data.get("status") not in ("OK", "DELAYED") or not data.get("results"):
                _err = f"{ticker}: status={data.get('status')} results={data.get('resultsCount',0)} | {data.get('error','')}{data.get('message','')}"
                if not hasattr(fetch_backtest_daily_data, '_errors'):
                    fetch_backtest_daily_data._errors = []
                fetch_backtest_daily_data._errors = (fetch_backtest_daily_data._errors + [_err])[-5:]
                return []
            
            bars = []
            for r in data["results"]:
                _c = r.get("c", 0)
                if not _c or _c <= 0:
                    continue  # Skip invalid zero-price bars
                ts = r.get("t", 0)
                dt = datetime.fromtimestamp(ts / 1000).strftime("%Y-%m-%d") if ts else ""
                bars.append({
                    "date": dt,
                    "open": r.get("o", _c),
                    "high": r.get("h", _c),
                    "low": r.get("l", _c),
                    "close": _c,
                    "volume": r.get("v", 0),
                    "vwap": r.get("vw", 0)
                })
            return bars
        except Exception as e:
            if attempt < 2:
                time.sleep(5)
            continue
    
    return []


# ── fetch_grouped_daily (originally line 12172) ──
def fetch_grouped_daily(poly_key, date_str):
    """Holt ALLE US-Aktien für einen Tag (Grouped Daily Bars)."""
    url = f"https://api.polygon.io/v2/aggs/grouped/locale/us/market/stocks/{date_str}"
    params = {"apiKey": poly_key, "adjusted": "true"}
    for attempt in range(4):
        try:
            resp = rate_limited_get(url, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt < 3:
                    retry_after = float(resp.headers.get("Retry-After") or 0)
                    time.sleep(max(retry_after, 2 ** attempt))
                    continue
                return None
            if resp.status_code != 200:
                return None
            data = resp.json()
            if data.get("status") in ("OK", "DELAYED"):
                results = data.get("results") or []
                return {
                    r["T"]: r
                    for r in results
                    if r.get("T") and r.get("c", 0) > 0
                }
            return None
        except (requests.RequestException, ValueError, TypeError):
            if attempt < 3:
                time.sleep(2 ** attempt)
                continue
            return None
    return None



# ── Weitere Data-Fetcher (V70.4) ──

def get_binance_tradingview_symbol(coin_symbol):
    """
    Convert CoinGecko symbol to TradingView BINANCE pair.

    Handles:
    - Stablecoins (USDT, USDC, BUSD, DAI, TUSD, etc.) → Use BNB or ETH instead
    - Symbols with different Binance names (MIOTA → IOTA)
    - Missing USDT pairs → Try BUSD or other alternatives

    Args:
        coin_symbol: CoinGecko symbol (e.g., "BTC", "ETH", "USDT", "MIOTA")

    Returns:
        TradingView symbol ready for BINANCE: prefix (e.g., "BTCUSDT", "ETHUSDT")
    """
    if not coin_symbol:
        return "BTCUSDT"  # Safe fallback

    coin_symbol = coin_symbol.upper().strip()

    # Mapping for symbols that differ between CoinGecko and Binance
    special_mappings = {
        "MIOTA": "IOTA",        # IOTA token
        "IOT": "IOTA",          # Alternative
        "XDG": "DOGE",          # Dogecoin alternative
        "VET": "VET",           # Vechain
        "ONE": "ONE",           # Harmony
        "SCRT": "SCRT",         # Secret
        "RUNE": "RUNE",         # Thorchain
    }

    # Stablecoins - use major trading pairs instead
    stablecoins = {"USDT", "USDC", "BUSD", "DAI", "TUSD", "USDN", "USDP", "GUSD", "PAX"}
    if coin_symbol in stablecoins:
        # Use BNBUSDT or ETHUSDT instead of problematic pairs
        return "BNBUSDT"

    # Apply special mappings
    symbol = special_mappings.get(coin_symbol, coin_symbol)

    # Default: SYMBOL + USDT
    return f"{symbol}USDT"


def _get_bpiq_catalysts(ticker):
    """
    Holt BPIQ-Catalyst-Daten für einen Ticker.
    Returns: dict kompatibel mit dem bestehenden Readout-System.

    Felder:
    - readout_score: 0-15 (Bonus für Score-Berechnung)
    - readout_label: UI-Label ([-] PDUFA, [~] Readout etc.)
    - catalyst_readouts: Liste der Catalyst-Events
    - bpiq_available: True wenn BPIQ-Daten vorhanden
    """
    cache = _load_bpiq_catalyst_cache()
    drugs = cache.get(ticker.upper(), [])

    if not drugs:
        return {
            "readout_score": 0,
            "readout_label": "",
            "catalyst_readouts": [],
            "bpiq_available": False,
            "overdue_count": 0,
            "readout_risk_flags": [],
        }

    # Score berechnen: Gewichtet nach Kategorie und Phase
    readout_score = 0
    catalyst_readouts = []

    for drug in drugs:
        cat = drug["category"]
        pm = drug["phase_mult"]

        if cat == "IMMINENT":
            readout_score += 5 * pm   # BPIQ-IMMINENT ist stärker als CT.gov (kuratiert!)
        elif cat == "UPCOMING":
            readout_score += 2 * pm
        elif cat == "LATER":
            readout_score += 0.5 * pm

        # Past dates stay visible as stale/unconfirmed context, but add no edge.
        if cat in ("OVERDUE", "IMMINENT", "UPCOMING", "LATER"):
            catalyst_readouts.append(drug)

    readout_score = min(15, int(readout_score))
    future_readouts = [
        drug for drug in catalyst_readouts
        if drug.get("category") in ("IMMINENT", "UPCOMING", "LATER")
    ]
    overdue_count = sum(
        1 for drug in catalyst_readouts if drug.get("category") == "OVERDUE"
    )
    readout_risk_flags = (
        ["overdue_catalyst_date_unconfirmed"] if overdue_count else []
    )

    # Label: nearest future event; overdue-only data is explicitly a warning.
    readout_label = ""
    if catalyst_readouts:
        top = future_readouts[0] if future_readouts else catalyst_readouts[0]
        days = top["days_until"]
        stage = top["full_label"]
        drug_name = top["drug_name"][:25]
        cat = top["category"]

        if "PDUFA" in stage:
            # PDUFA = besonders wichtig, eigenes Format
            if cat == "IMMINENT":
                readout_label = f"[-] PDUFA {top['catalyst_date_text']} — {drug_name}"
            elif cat == "UPCOMING":
                readout_label = f"[~] PDUFA in {days}d — {drug_name}"
            elif cat == "OVERDUE":
                readout_label = f"[-] PDUFA ÜBERFÄLLIG ({abs(days)}d) — {drug_name}"
        else:
            if cat == "OVERDUE":
                readout_label = f"[-] {stage} ÜBERFÄLLIG ({abs(days)}d) — {drug_name}"
            elif cat == "IMMINENT":
                readout_label = f"[~] {stage} in {days}d — {drug_name}"
            elif cat == "UPCOMING":
                readout_label = f"[+] {stage} in {days}d — {drug_name}"

        # N-b (10.06.): LATER-only-Faelle bekommen ein eigenes Label
        # (vorher leer, obwohl der Readout scored).
        if not readout_label and cat == "LATER" and days is not None:
            readout_label = f"[.] {stage} in {days}d — {drug_name}"

    return {
        "readout_score": readout_score,
        "readout_label": readout_label,
        "catalyst_readouts": catalyst_readouts[:5],
        "bpiq_available": True,
        "overdue_count": overdue_count,
        "readout_risk_flags": readout_risk_flags,
    }


def _calculate_biotech_catalyst_score(catalyst_score, pipeline_score, technical_score, risk_score, news_momentum_score, rvol=0, rvol_direction=True):
    """
    Berechnet den finalen Biotech Catalyst Score (0-100).

    Bonus: Catalyst + Volume Confirmation = stärkeres Signal.
    Wenn ein Catalyst gefunden wird UND das Volumen ungewöhnlich hoch ist,
    ist das Signal deutlich stärker (Smart Money bestätigt die News).

    rvol_direction: True = UP day (accumulation), False = DOWN day (distribution)
                    Only apply full RVOL bonus on UP days; reduce to 20% on DOWN days.
    """
    # Weighted: Catalyst is primary driver (2x weight)
    # FIX 3: Updated for new catalyst cap of 45
    total = (catalyst_score * 2) + pipeline_score + technical_score + risk_score + news_momentum_score
    # Normalize to 0-100 scale (max: 45*2 + 20 + 20 + 15 + 15 = 160)
    total = min(100, int(total * 100 / 160))

    # Catalyst-Volume Confirmation Bonus (max 10 Extra-Punkte)
    # FIX 1: Apply RVOL bonus based on direction (up=full, down=20%)
    if catalyst_score > 0 and rvol >= 1.5:
        if rvol >= 3.0:
            bonus = 10  # Extrem: Catalyst + 3x Volume = Hot
        elif rvol >= 2.0:
            bonus = 7   # Stark: Catalyst + 2x Volume
        else:
            bonus = 4   # Moderat: Catalyst + 1.5x Volume

        # Apply bonus based on direction
        if rvol_direction:
            total = min(100, total + bonus)  # UP day: full bonus
        else:
            total = min(100, total + int(bonus * 0.2))  # DOWN day: 20% of bonus

    # V68: Finaler Cap NACH allen Boni — kein Score über 100
    # Readout-Bonus wird VOR dem Cap addiert (nicht danach)
    return min(100, max(0, total))


def _detect_catalyst(title):
    """Erkennt Katalysator-Typ aus News-Titel."""
    title_lower = title.lower()
    for catalyst_type, cat_data in CATALYST_KEYWORDS.items():
        for kw in cat_data["keywords"]:
            if kw.lower() in title_lower:
                return catalyst_type
    return None


def _fetch_historical_yahoo(ticker, days):
    """Yahoo Finance historische Daily-Daten für internationale Aktien"""
    try:
        # Days to Yahoo range string
        if days <= 30:
            yf_range = "1mo"
        elif days <= 90:
            yf_range = "3mo"
        elif days <= 180:
            yf_range = "6mo"
        elif days <= 365:
            yf_range = "1y"
        elif days <= 730:
            yf_range = "2y"
        else:
            yf_range = "5y"
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": "1d", "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Format: [[timestamp_ms, open, high, low, close, volume], ...]
        result = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            result.append([
                timestamps[i] * 1000,  # seconds → ms für Kompatibilität
                opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                closes[i],
                volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            ])
        
        return result if result else None
    except Exception:
        return None


def _aggregate_session_bars(
    raw_bars,
    *,
    bars_per_bucket=4,
    timestamp_in_ms=False,
    timezone_name="UTC",
    expected_interval_seconds=3600,
):
    """Aggregate only contiguous intraday bars from the same local session."""
    try:
        from zoneinfo import ZoneInfo

        timezone = ZoneInfo(str(timezone_name or "UTC"))
    except Exception:
        timezone = dt.timezone.utc

    def _seconds(bar):
        value = float(bar.get("t", 0) or 0)
        return value / 1000.0 if timestamp_in_ms else value

    ordered = sorted(
        (bar for bar in (raw_bars or []) if isinstance(bar, dict) and bar.get("t")),
        key=_seconds,
    )
    aggregated = []
    chunk = []
    session_date = None
    previous_ts = None
    max_gap = max(float(expected_interval_seconds) * 1.75, float(expected_interval_seconds) + 60.0)

    def _flush():
        nonlocal chunk
        if not chunk:
            return
        aggregated.append({
            "t": chunk[0]["t"],
            "o": chunk[0]["o"],
            "h": max(float(item["h"]) for item in chunk),
            "l": min(float(item["l"]) for item in chunk),
            "c": chunk[-1]["c"],
            "v": sum(float(item.get("v", 0) or 0) for item in chunk),
            "source_bar_count": len(chunk),
            "partial_source_bar": len(chunk) < int(bars_per_bucket),
        })
        chunk = []

    for bar in ordered:
        timestamp = _seconds(bar)
        current_date = datetime.fromtimestamp(timestamp, tz=timezone).date()
        discontinuous = previous_ts is not None and (timestamp - previous_ts) > max_gap
        if chunk and (current_date != session_date or discontinuous):
            _flush()
        if not chunk:
            session_date = current_date
        chunk.append(bar)
        previous_ts = timestamp
        if len(chunk) >= int(bars_per_bucket):
            _flush()

    _flush()
    return aggregated


def _fetch_ohlcv_yahoo(ticker, timeframe="1H"):
    """Yahoo Finance OHLCV für internationale Aktien, Forex, Futures, Krypto."""
    try:
        # Yahoo Finance interval & range mapping
        tf_map = {
            "5m":  ("5m",  "60d",  500),    # 60 Tage 5-min
            "15m": ("15m", "60d",  500),    # 60 Tage 15-min
            "1H":  ("1h",  "730d", 500),    # 2 Jahre 1H
            "4H":  ("1h",  "730d", 500),    # 2 Jahre 1H → aggregiere zu 4H
            "1D":  ("1d",  "2y",   500),    # 2 Jahre Daily
            "1W":  ("1wk", "5y",   260),    # 5 Jahre Weekly
            "1M":  ("1mo", "max",  120),    # Max Monthly
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        yf_interval, yf_range, max_bars = tf_map[timeframe]
        
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
        params = {"interval": yf_interval, "range": yf_range}
        headers = {"User-Agent": "Mozilla/5.0"}
        
        resp = rate_limited_get(url, params=params, headers=headers, timeout=15)
        if resp.status_code != 200:
            return None
        
        data = resp.json()
        chart = data.get("chart", {}).get("result", [])
        if not chart:
            return None
        
        timestamps = chart[0].get("timestamp", [])
        exchange_timezone = chart[0].get("meta", {}).get("exchangeTimezoneName") or "UTC"
        indicators = chart[0].get("indicators", {}).get("quote", [{}])[0]
        
        opens = indicators.get("open", [])
        highs = indicators.get("high", [])
        lows = indicators.get("low", [])
        closes = indicators.get("close", [])
        volumes = indicators.get("volume", [])
        
        if not timestamps or not closes:
            return None
        
        # Build raw bars (filter None values)
        raw_bars = []
        for i in range(len(timestamps)):
            if i >= len(closes) or closes[i] is None:
                continue
            raw_bars.append({
                "t": timestamps[i],
                "o": opens[i] if i < len(opens) and opens[i] is not None else closes[i],
                "h": highs[i] if i < len(highs) and highs[i] is not None else closes[i],
                "l": lows[i] if i < len(lows) and lows[i] is not None else closes[i],
                "c": closes[i],
                "v": volumes[i] if i < len(volumes) and volumes[i] is not None else 0
            })
        
        if not raw_bars:
            return None
        
        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            raw_bars = _aggregate_session_bars(
                raw_bars,
                bars_per_bucket=4,
                timestamp_in_ms=False,
                timezone_name=exchange_timezone,
            )
        
        # Formatiere für Lightweight Charts
        effective_bars = min(max_bars, len(raw_bars))
        ohlcv = []
        for bar in raw_bars[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"],  # Yahoo timestamps sind schon in Sekunden
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv if ohlcv else None
        
    except Exception as e:
        return None


def _fetch_ohlcv_polygon(ticker, poly_key, timeframe="1H"):
    """Polygon.io OHLCV für US-Aktien."""
    try:
        # Timeframe mapping: (multiplier, span, days_back, max_bars)
        # V3.4: Mehr Bars für saubere EMA-Berechnung (EMA200 braucht ~600+ Bars zum Einschwingen)
        tf_map = {
            "5m": ("5", "minute", 10, 800),
            "15m": ("15", "minute", 30, 800),
            "1H": ("1", "hour", 180, 800),
            "4H": ("1", "hour", 365, 800),
            "1D": ("1", "day", 1095, 800),
            "1W": ("1", "week", 2555, 500),
        }
        
        if timeframe not in tf_map:
            timeframe = "1H"
        
        mult, span, days_back, max_bars = tf_map[timeframe]
        # Keep all chart timeframes on Polygon's adjusted price scale.
        # The scanner/snapshot endpoints expose the current split-adjusted price;
        # unadjusted intraday bars can show stale pre-split levels on names like FFAI.
        adjusted = "true"
        
        end_date = datetime.now().strftime("%Y-%m-%d")
        start_date = (datetime.now() - timedelta(days=days_back)).strftime("%Y-%m-%d")
        
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/{mult}/{span}/{start_date}/{end_date}"
        # Polygon truncates large aggregate queries once the base-bar queryCount
        # hits its cap. With ascending intraday queries that can return old
        # candles and miss the current market entirely. Fetch newest intraday
        # bars first, then reverse back to chronological order for the chart.
        sort_order = "desc" if span in ("minute", "hour") else "asc"
        params = {"apiKey": poly_key, "adjusted": adjusted, "sort": sort_order, "limit": 50000}

        resp = rate_limited_get(url, params=params, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get("results", [])

        if not results:
            return None

        if sort_order == "desc":
            results = list(reversed(results))

        # Für 4H: Aggregiere 1H Bars zu 4H
        if timeframe == "4H":
            results = _aggregate_session_bars(
                results,
                bars_per_bucket=4,
                timestamp_in_ms=True,
                timezone_name="America/New_York",
            )
        
        effective_bars = min(max_bars, len(results))
        ohlcv = []
        for bar in results[-effective_bars:]:
            ohlcv.append({
                "time": bar["t"] // 1000,  # Polygon ms → seconds
                "open": bar["o"],
                "high": bar["h"],
                "low": bar["l"],
                "close": bar["c"],
                "volume": bar.get("v", 0)
            })
        
        return ohlcv
        
    except Exception as e:
        return None
