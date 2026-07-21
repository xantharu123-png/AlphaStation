"""
Scanners Module — BI Scanner, BioTech Scanner, AutoTrader (V70.0)

Extrahiert aus scanner.py:
- BI Scanner V2: Background scan, caching, scoring
- BioTech Scanner: Clinical trials, news, technical scoring
- AutoTrader: Automated scan + order pipeline
"""
import os
import json
import math
import re
import time
import threading
import tempfile
import datetime as dt
from datetime import datetime, timedelta
from collections import defaultdict
from modules.data_fetchers import (
    rate_limited_get, fetch_grouped_daily, get_ticker_details,
    _get_bpiq_catalysts, _calculate_biotech_catalyst_score,
    get_premium_catalyst_tickers
)
from modules.indicators import (
    calculate_sma, calculate_ema, calculate_rsi_from_bars,
    calculate_atr_14, calculate_obv, calculate_adx
)
from modules.scorers import calculate_setup_score, calculate_alpha_score
from modules.helpers import is_spac
from modules.patterns import analyze_breakout_imminent, analyze_candles
from modules.analysis import _detect_chart_patterns, calculate_short_bonus_signals
from modules.trade_levels import trade_geometry
from modules.vrvp_levels import (
    apply_vrvp_to_trade_setup,
    build_vrvp_structure,
    calculate_wilder_atr,
)
from modules.volume_metrics import historical_volume_baseline

# AutoTrader: IBKR imports (optional — nur wenn ib_insync installiert)
try:
    from modules.brokers import ib_is_connected, ib_calc_shares, _get_ib_state, ib_get_contract, Order
except ImportError:
    def ib_is_connected(): return False
    def ib_calc_shares(*a, **kw): return 0
    def _get_ib_state(): return {}
    def ib_get_contract(*a, **kw): return None
    Order = None

# SPAC SIC Codes (für Biotech Scanner SPAC-Filter)
SPAC_SIC_CODES = {"6770", "6726"}


# ── Biotech Constants (needed by _fetch_biotech_universe etc.) ──
BIOTECH_SIC_CODES = {
    "2833", "2834", "2835", "2836",  # Pharma / Biotech Manufacturing
    "2831",  # Biological Products
    "3841", "3842",  # Medical Instruments & Devices
    "8731", "8734",  # R&D / Testing Labs
}

BIOTECH_NAME_KEYWORDS = [
    "pharma", "therapeutics", "biosciences", "biotech", "biopharma",
    "oncology", "genomics", "immuno", "medical", "diagnostics",
    "gene therapy", "cell therapy", "biologics", "vaccine", "antibody",
    "rna", "mrna", "crispr", "peptide", "neuro", "cardio",
]

FDA_CATALYST_KEYWORDS = {
    # K-1 (Biotech-Audit 10.06.): Texte werden vor dem Matching Roman→Arabisch
    # normalisiert ("phase iii" → "phase 3"). Bare Roman-Keywords ("phase iii",
    # "phase i") sind deshalb entfernt — sie matchten zudem kontextfrei jede
    # Erwaehnung (auch Fehlschlaege) als tier2.
    # M-5 (10.06.): "fda clearance"/"510(k)" sind Device-Routine (kein
    # Binary-Drug-Event) — von tier1 (30) auf tier4-Niveau (8) gestuft.
    "tier1": {
        "keywords": ["fda approval", "fda approved", "pdufa", "nda accepted", "bla accepted",
                     "breakthrough therapy", "fast track", "priority review",
                     "accelerated approval", "orphan drug", "emergency use", "eua granted",
                     "adcom", "advisory committee",
                     "fda decision", "fda action date"],
        "score": 30,
        "label": " FDA Event"
    },
    "tier2": {
        "keywords": ["phase 3 results", "phase 3 data", "pivotal trial",
                     "primary endpoint met", "primary endpoint", "topline results", "topline data",
                     "positive results", "statistically significant", "overall survival",
                     "progression-free survival", "complete remission", "phase 2 results",
                     "phase 2 data", "late-breaking", "interim analysis", "interim data"],
        "score": 22,
        "label": " Trial Results"
    },
    "tier3": {
        "keywords": ["licensing agreement", "partnership", "collaboration", "acquisition target",
                     "buyout", "merger", "ind filed", "ind accepted", "clinical trial initiation",
                     "patient enrollment", "first patient dosed", "dosing initiated",
                     "expanded access", "compassionate use", "label expansion"],
        "score": 15,
        "label": " Deal/Pipeline"
    },
    "tier4": {
        "keywords": ["preclinical", "phase 1", "proof of concept",
                     "patent granted", "patent filed", "ip protection", "data presentation",
                     "conference presentation", "manuscript published", "peer review",
                     "fda clearance", "510(k)"],
        "score": 8,
        "label": " Early Pipeline"
    },
}

BIOTECH_NEGATIVE_CATALYSTS = {
    "clinical hold": -25,
    "fda rejection": -30,
    # V4 AUDIT FIX (Biotech-Audit V3): "complete response" ohne "letter" ist in
    # Trial-Kontext eine POSITIVE Remission (CR = beste Outcome-Kategorie).
    # Nur "complete response letter" bzw. "crl" bezeichnet eine FDA-Ablehnung.
    "complete response letter": -20,
    "crl issued": -20,
    "trial failure": -25,
    "missed endpoint": -25,
    "adverse events": -15,
    "safety concern": -15,
    "stock offering": -10,
    "dilution": -10,
    "shelf registration": -8,
    "going concern": -20,
    "delisting": -25,
    "sec investigation": -15,
    # M-1/M-2 (Biotech-Audit 10.06.): Kapitalerhoehung/Dilution + eingestellte
    # Programme in die News-Negativliste (mit Wortgrenzen; Verb-Formen und
    # Kontext-Fenster deckt zusaetzlich BIOTECH_NEGATIVE_PATTERNS ab).
    "public offering": -12,
    "registered direct offering": -12,
    "underwritten offering": -12,
    "discontinued": -20,
    "discontinuation": -20,
    "terminated": -18,
}

# K-1 (Biotech-Audit 10.06.): Verb-Formen/Kontext-Patterns fuer Fehlschlaege.
# Die starre Phrasen-Liste oben fing "misses primary endpoint", "failed to
# meet", "fell short", "did not achieve" etc. NICHT — das schlimmste
# Biotech-Outcome wurde als positiver Katalysator gescored. Patterns laufen
# auf title UND description (normalisiert, lowercased, Roman→Arabisch).
# [^.!?;]{0,60} = Kontext-Fenster innerhalb desselben Satzteils.
BIOTECH_NEGATIVE_PATTERNS = [
    ("missed endpoint", -25, re.compile(r"\bmiss(?:es|ed|ing)?\b[^.!?;]{0,60}?\bendpoints?\b")),
    ("failed to meet", -25, re.compile(r"\bfail(?:s|ed)?\s+to\s+(?:meet|achieve|demonstrate)\b")),
    ("fell short", -20, re.compile(r"\bfell\s+short\b")),
    ("disappointing results", -20, re.compile(r"\bdisappointing\s+(?:results|data|topline)\b")),
    ("did not meet", -25, re.compile(r"\b(?:did|does|do)\s+not\s+(?:meet|achieve|reach|demonstrate)\b")),
    ("no significant benefit", -20, re.compile(r"\bno\s+(?:statistically\s+significant|significant)\s+(?:improvement|difference|benefit)\b")),
    ("terminated program", -20, re.compile(r"\bterminat(?:e|es|ed|ing|ion)\b[^.!?;]{0,60}?\b(?:trial|study|development|program)\b")),
    ("discontinued development", -20, re.compile(r"\bdiscontinu(?:e|es|ed|ing|ation)\b[^.!?;]{0,60}?\b(?:development|program|trial|study)\b")),
    ("public offering", -12, re.compile(r"\b(?:public|registered\s+direct|underwritten)\s+offering\b")),
    ("dilution", -10, re.compile(r"\bdilut(?:ion|ive)\b")),
    ("shares plunge", -15, re.compile(r"\bshares?\s+(?:plunge[ds]?|tumble[ds]?|sink|sinks|sank|crater(?:s|ed)?)\b")),
    ("delisting", -25, re.compile(r"\bdelist(?:s|ed|ing)?\b")),
]

# K-1c: Verneinungsfenster — Negation bis ~6 Woerter VOR einem Keyword
# ("did not ... meet primary endpoint", "no safety concerns seen").
_BIOTECH_NEGATION_BEFORE_RE = re.compile(
    r"(?:\b(?:did|does|do)\s+not\b|\bnot\b|\bno\b|\bwithout\b|"
    r"\bfail(?:s|ed)?\s+to\b|\bunable\s+to\b|\babsence\s+of\b|\bfree\s+of\b)"
    r"(?:\W+\w+){0,5}\W*$"
)

# K-1a: Roman→Arabisch-Normalisierung fuer Phasen (Reihenfolge: iii vor ii vor i).
_BIOTECH_ROMAN_PHASES = [
    (re.compile(r"\bphase\s+iii\b"), "phase 3"),
    (re.compile(r"\bphase\s+iv\b"), "phase 4"),
    (re.compile(r"\bphase\s+ii\b"), "phase 2"),
    (re.compile(r"\bphase\s+i\b"), "phase 1"),
]

_BIOTECH_READOUT_CATEGORY_ORDER = {
    "IMMINENT": 0,
    "UPCOMING": 1,
    "OVERDUE": 2,
    "OVERDUE_STALE": 3,
}

_BIOTECH_READOUT_TIMING_WEIGHT = {
    "IMMINENT": 3.0,
    "UPCOMING": 1.5,
    "OVERDUE": 0.0,
    "OVERDUE_STALE": 0.0,
}


def _biotech_readout_sort_key(item):
    """Sort readouts without treating a valid T+0 event as missing."""
    days = item.get("days_until_readout")
    distance = abs(days) if isinstance(days, (int, float)) else float("inf")
    return (
        _BIOTECH_READOUT_CATEGORY_ORDER.get(item.get("readout_category"), 9),
        distance,
    )


def _biotech_readout_timing_weight(category):
    """Only future readouts add catalyst edge; overdue dates are unconfirmed metadata."""
    return _BIOTECH_READOUT_TIMING_WEIGHT.get(str(category or "").upper(), 0.0)


def _biotech_normalize_text(text):
    """K-1a (10.06.): lowercased + Phasen Roman→Arabisch ('phase iii'→'phase 3')."""
    t = (text or "").lower()
    for _rx, _repl in _BIOTECH_ROMAN_PHASES:
        t = _rx.sub(_repl, t)
    return t


def _biotech_negative_match(text, kw):
    """Negativ-Keyword mit Verneinungs-Guard: 'no safety concerns seen' ist
    KEIN Negativ-Signal. Liefert True nur fuer nicht-verneinte Treffer."""
    for _m in re.finditer(r"(?<!\w)" + re.escape(kw) + r"s?(?!\w)", text):
        _prefix = text[max(0, _m.start() - 60):_m.start()]
        if _BIOTECH_NEGATION_BEFORE_RE.search(_prefix):
            continue
        return True
    return False


def _biotech_positive_match(text, kw):
    """K-1c (10.06.): Positiv-Keyword nur ohne Negation im Vorfenster
    ('did not meet primary endpoint' darf 'primary endpoint' nicht scoren)."""
    for _m in re.finditer(r"(?<!\w)" + re.escape(kw) + r"(?!\w)", text):
        _prefix = text[max(0, _m.start() - 60):_m.start()]
        if _BIOTECH_NEGATION_BEFORE_RE.search(_prefix):
            continue
        return True
    return False


# H-4 (Biotech-Audit 10.06.): FORWARD-Katalysatoren — angekuendigte, noch NICHT
# eingetretene Ergebnisse ("topline results expected in Q3") duerfen nicht wie
# eingetretene Events scoren. Termin-Wertung laeuft ueber den BPIQ-Pfad.
# "pleased/proud to report" ist Ergebnis-Sprache (kein Forward) → Lookbehinds.
_BIOTECH_FORWARD_RE = re.compile(
    r"\b(?:expected|anticipated|upcoming|on\s+track\s+to|"
    r"will\s+(?:report|announce|present|release)|"
    r"plans?\s+to\s+(?:report|announce|present|release)|"
    r"expects?\s+to\s+(?:report|announce|present|release)|"
    r"(?<!pleased )(?<!proud )(?<!happy )(?<!glad )to\s+report)\b"
)
_BIOTECH_FORWARD_RESULT_RE = re.compile(
    r"\b(?:results?|data|readouts?|topline|interim|endpoints?|analysis|findings|decision)\b"
)

# ── Constants (extracted from scanner.py V70.2) ──
_AUTOTRADER_CONFIG_FILE = "/tmp/alpha_autotrader_config.json"
_AUTOTRADER_STATE_FILE = "/tmp/alpha_autotrader_state.json"
_AUTOTRADER_STOP_FILE = "/tmp/alpha_autotrader_stop"
_AUTOTRADER_LOG_FILE = "/tmp/alpha_autotrader_log.json"

_AUTOTRADER_DEFAULT_CONFIG = {
    "mode": "semi",
    "max_positions": 5,
    "position_size_type": "dollar",
    "position_size": 2000,
    "excluded_grades": ["A"],
    "min_bi_pct": 55,
    "min_smart_money": 2,
    "scan_interval_min": 15,
    "max_daily_loss_pct": 3.0,
    "cooldown_days": 5,
    "trading_hours_only": True,
    "min_rr": 2.0,
    "max_tickers_scan": 300,
    "min_price": 5.0,
    "min_volume": 200000,
}

_BI_CACHE_FILE = "/tmp/bi_cache_{direction}.json"
_BI_PROGRESS_FILE = "/tmp/bi_scan_progress_{direction}.json"
_BI_CONFIG_FILE = "/tmp/alpha_bi_config.json"
_BI_CACHE_MAX_AGE = 7200
_bi_scan_lock = threading.Lock()

_BI_DEFAULT_CONFIG = {
    "direction": "long",
    "threshold": 85,
    "auto_enabled": True,
    "scan1_h": 15,
    "scan1_m": 45,
    "scan2_h": 18,
    "scan2_m": 30,
    "cache_ttl_h": 2,
}

_BIOTECH_CONFIG_FILE = "/tmp/alpha_biotech_config.json"

_BIOTECH_DEFAULT_CONFIG = {
    "auto_scan": True,
    "quick_interval_h": 2,
    "full_interval_h": 6,
    "min_score": 20,
}


# ── Helper Functions (extracted from scanner.py V70.2) ──

def _autotrader_should_stop():
    """Prüft ob Stop-Signal gesetzt ist."""
    return os.path.exists(_AUTOTRADER_STOP_FILE)


def _autotrader_request_stop():
    """Schreibt Stop-Signal für den AutoTrader Background-Thread."""
    try:
        with open(_AUTOTRADER_STOP_FILE, "w") as f:
            f.write("stop")
    except Exception:
        pass


def _autotrader_clear_stop():
    """Löscht Stop-Signal."""
    try:
        os.remove(_AUTOTRADER_STOP_FILE)
    except Exception:
        pass


def _autotrader_check_cooldown(ticker, cooldown_dict, cooldown_days):
    """Prüft ob Ticker noch im Cooldown ist."""
    if ticker not in cooldown_dict:
        return False
    last_trade_date = cooldown_dict[ticker]
    try:
        last_dt = datetime.strptime(last_trade_date, "%Y-%m-%d")
        return (datetime.now() - last_dt).days < cooldown_days
    except Exception:
        return False


def _bi_cache_path(direction="long"):
    """Pfad zur BI Cache-Datei je Richtung."""
    return _BI_CACHE_FILE.format(direction=direction)


def _bi_progress_path(direction="long"):
    """Pfad zur Progress-Datei."""
    return _BI_PROGRESS_FILE.format(direction=direction)


def _bi_stop_file(direction="long"):
    return f"/tmp/alpha_bi_stop_{direction}"


def _bi_request_stop(direction="long"):
    """UI ruft das auf — schreibt Stop-Signal für den Background-Thread."""
    try:
        with open(_bi_stop_file(direction), "w") as f:
            f.write("stop")
    except Exception:
        pass


def _bi_should_stop(direction="long"):
    """Background-Thread prüft das regelmäßig."""
    return os.path.exists(_bi_stop_file(direction))


def _bi_clear_stop(direction="long"):
    """Aufräumen nach Stop oder vor neuem Scan."""
    try:
        os.remove(_bi_stop_file(direction))
    except Exception:
        pass


def _bi_progress_clear(direction="long"):
    """Löscht Progress-Datei."""
    try:
        path = _bi_progress_path(direction)
        if os.path.exists(path):
            os.remove(path)
    except Exception:
        pass


def _biotech_progress_file():
    return "/tmp/alpha_biotech_progress.json"


def _biotech_stop_file():
    return "/tmp/alpha_biotech_stop"


def _biotech_request_stop():
    """UI ruft das auf — schreibt Stop-Signal für den Background-Thread."""
    try:
        with open(_biotech_stop_file(), "w") as f:
            f.write("stop")
    except Exception:
        pass


def _biotech_should_stop():
    """Background-Thread prüft das regelmäßig."""
    return os.path.exists(_biotech_stop_file())


def _biotech_clear_stop():
    """Aufräumen nach Stop oder vor neuem Scan."""
    try:
        os.remove(_biotech_stop_file())
    except Exception:
        pass


def _biotech_cache_file():
    return "/tmp/alpha_biotech_cache.json"


def _biotech_universe_cache_file():
    return "/tmp/alpha_biotech_universe.json"


# ── Scanner Functions ──

def _autotrader_config_load():
    """Lädt Auto-Trader Konfiguration."""
    try:
        with open(_AUTOTRADER_CONFIG_FILE, "r") as f:
            saved = json.load(f)
        config = dict(_AUTOTRADER_DEFAULT_CONFIG)
        config.update(saved)
        return config
    except Exception:
        return dict(_AUTOTRADER_DEFAULT_CONFIG)


def _autotrader_config_save(config):
    """Speichert Auto-Trader Konfiguration persistent."""
    try:
        with open(_AUTOTRADER_CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except Exception:
        pass


def _autotrader_state_read():
    """Liest aktuellen Auto-Trader Status."""
    try:
        with open(_AUTOTRADER_STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"status": "stopped", "positions": [], "daily_pnl": 0, "trades_today": 0,
                "last_scan": None, "cooldown_tickers": {}, "log": []}


def _autotrader_state_write(state):
    """Schreibt Auto-Trader Status."""
    try:
        with open(_AUTOTRADER_STATE_FILE, "w") as f:
            json.dump(state, f, default=str)
    except Exception:
        pass


def _autotrader_log(message, level="INFO"):
    """Schreibt Log-Eintrag."""
    try:
        log_data = []
        try:
            with open(_AUTOTRADER_LOG_FILE, "r") as f:
                log_data = json.load(f)
        except Exception:
            pass
        log_data.append({
            "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "msg": message
        })
        # Behalte nur letzte 200 Einträge
        log_data = log_data[-200:]
        with open(_AUTOTRADER_LOG_FILE, "w") as f:
            json.dump(log_data, f, default=str)
    except Exception:
        pass


def _autotrader_is_market_hours():
    """Prüft ob US-Markt offen ist (9:30-16:00 ET)."""
    try:
        import pytz
        et = pytz.timezone("US/Eastern")
        now_et = datetime.now(et)
        # Mo-Fr
        if now_et.weekday() >= 5:
            return False
        # 9:30 - 16:00
        market_open = now_et.replace(hour=9, minute=30, second=0)
        market_close = now_et.replace(hour=16, minute=0, second=0)
        return market_open <= now_et <= market_close
    except Exception:
        return True  # Fallback: immer erlauben


def _autotrader_long_geometry(entry, stop, tp1, tp2):
    """Return validated, signed geometry for BI AutoTrader long orders."""
    return trade_geometry(entry, stop, tp1, tp2, "LONG")


def autotrader_scan_once(poly_key, config=None):
    """
     Einmaliger Auto-Trader Scan-Durchlauf.

    1. Holt aktuelle Marktdaten (letzte 50 Tage) für Top-Aktien
    2. Läuft analyze_breakout_imminent() auf jede Aktie
    3. Filtert nach Grade, Score, Smart Money, R:R
    4. Berechnet Entry/Stop/TP für qualifizierte Signale
    5. Submittet Bracket Orders an IBKR

    Returns: dict mit signals_found, orders_placed, errors
    """
    if config is None:
        config = _autotrader_config_load()

    state = _autotrader_state_read()
    result = {"signals_found": 0, "orders_placed": 0, "errors": [], "signals": []}

    # Market Hours Check
    if config.get("trading_hours_only", True) and not _autotrader_is_market_hours():
        _autotrader_log(" Außerhalb Handelszeiten — Scan übersprungen", "INFO")
        result["errors"].append("Außerhalb Handelszeiten")
        return result

    # IBKR Connection Check
    if not ib_is_connected():
        _autotrader_log(" IBKR nicht verbunden — Scan übersprungen", "WARN")
        result["errors"].append("IBKR nicht verbunden")
        return result

    # Max Positions Check
    current_positions = len(state.get("positions", []))
    max_pos = config.get("max_positions", 5)
    if current_positions >= max_pos:
        _autotrader_log(f" Max Positionen erreicht ({current_positions}/{max_pos})", "INFO")
        result["errors"].append(f"Max Positionen: {current_positions}/{max_pos}")
        return result

    # Daily Loss Check
    daily_pnl = state.get("daily_pnl", 0)
    max_loss = config.get("max_daily_loss_pct", 3.0)
    if daily_pnl < -max_loss:
        _autotrader_log(f" Tages-Verlustlimit erreicht: {daily_pnl:.1f}% (Max: -{max_loss}%)", "WARN")
        result["errors"].append(f"Tages-Verlustlimit: {daily_pnl:.1f}%")
        return result

    excluded_grades = set(config.get("excluded_grades", ["A"]))
    min_bi_pct = config.get("min_bi_pct", 55)
    min_sm = config.get("min_smart_money", 2)
    cooldown_dict = state.get("cooldown_tickers", {})
    cooldown_days = config.get("cooldown_days", 5)
    position_tickers = set(p.get("ticker") for p in state.get("positions", []))

    # Lade aktuelle Tages-Daten (Grouped Daily — heute)
    today_str = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    _autotrader_log(f" Starte Scan — Max {config['max_tickers_scan']} Tickers", "INFO")

    # Lade 55 Tage History über Grouped Daily
    # 50 completed analysis candles plus one separate trigger candle.
    window_size = 51
    start_dt = datetime.now() - timedelta(days=window_size + 15)  # +Buffer für Wochenenden
    trading_days = []
    current_d = start_dt
    while current_d <= datetime.now():
        if current_d.weekday() < 5:
            trading_days.append(current_d.strftime("%Y-%m-%d"))
        current_d += timedelta(days=1)

    # Baue Ticker History
    ticker_history = {}
    _skip_prefixes = (
        "TQQQ","SQQQ","SOXL","SOXS","LABU","LABD","SPXL","SPXS",
        "UPRO","SPXU","UVXY","SVXY","NUGT","DUST","JNUG","JDST",
        "FNGU","FNGD","TECL","TECS","BULZ","BERZ","GUSH","DRIP",
        "FAS","FAZ","UDOW","SDOW","YANG","YINN","ERX","ERY",
    )

    for date_str in trading_days:
        day_data = fetch_grouped_daily(poly_key, date_str)
        if not day_data:
            continue
        for ticker, r in day_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            if any(ticker.upper().startswith(p) for p in _skip_prefixes):
                continue
            price = r.get("c", 0)
            volume = r.get("v", 0)
            if price < config.get("min_price", 5.0) or volume < config.get("min_volume", 200000):
                continue
            bar = {
                "date": date_str, "open": r.get("o", 0), "high": r.get("h", 0),
                "low": r.get("l", 0), "close": price, "volume": volume, "time": date_str,
            }
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append(bar)

    # Filtere auf Tickers mit genug History
    valid_tickers = {t: bars for t, bars in ticker_history.items() if len(bars) >= window_size}

    # Sortiere nach Volumen (Mid-Cap Prio)
    ticker_vol = {}
    for t, bars_list in valid_tickers.items():
        complete_bars = _bi_strip_partial_bar(bars_list)
        avg_vol = historical_volume_baseline(
            (b.get("volume") for b in complete_bars),
            lookback=20,
            minimum_periods=10,
        )
        if avg_vol is not None:
            ticker_vol[t] = avg_vol

    midcap = {t: v for t, v in ticker_vol.items() if 500_000 <= v <= 10_000_000}
    largecap = {t: v for t, v in ticker_vol.items() if v > 10_000_000}
    sorted_mid = sorted(midcap.keys(), key=lambda t: midcap[t], reverse=True)
    sorted_large = sorted(largecap.keys(), key=lambda t: largecap[t], reverse=True)
    tickers_to_scan = (sorted_mid + sorted_large)[:config.get("max_tickers_scan", 300)]

    _autotrader_log(f" {len(tickers_to_scan)} Tickers geladen, starte BI-Analyse", "INFO")

    qualified_signals = []

    for ticker in tickers_to_scan:
        if _autotrader_should_stop():
            _autotrader_log(" Stop-Signal empfangen", "WARN")
            break

        bars = ticker_history[ticker]
        window = bars[-window_size:]
        # The newest completed bar is the trigger/market bar. BI structure,
        # range and score must only use information that existed beforehand.
        analysis_window = window[:-1]
        trigger_bar = window[-1]
        if len(analysis_window) < 49:
            continue

        # Skip wenn schon Position oder im Cooldown
        if ticker in position_tickers:
            continue
        if _autotrader_check_cooldown(ticker, cooldown_dict, cooldown_days):
            continue

        # BI V2 Analyse
        try:
            bi_result = analyze_breakout_imminent(analysis_window, direction="long")
            if len(bi_result) == 8:
                is_valid, bi_score, bi_max, details, confidence, grade, sm_fires, sm_hits = bi_result
            else:
                is_valid, bi_score, bi_max, details, confidence, grade = bi_result
                sm_fires, sm_hits = 0, 0

            if not is_valid:
                continue

            # Grade Filter (Grade A ist Backtest-bestätigt schlecht!)
            if grade in excluded_grades:
                continue
            if grade == "D":
                continue

            # Raw score; gap penalties are applied before the final gate below.
            score_pct = (bi_score / bi_max * 100) if bi_max > 0 else 0

            # Smart Money Minimum
            if sm_hits < min_sm:
                continue

            # Berechne Entry/Stop/TP (identisch zum Backtest)
            # True Range statt Simple Range (berücksichtigt Gap-Risiko)
            atr_5 = calculate_wilder_atr(analysis_window, period=5)
            if atr_5 <= 0:
                continue
            range_high = max(b["high"] for b in analysis_window[-15:])
            range_low = min(b["low"] for b in analysis_window[-15:])
            range_size = range_high - range_low

            # Qualitäts-Checks
            range_pct = (range_size / range_low * 100) if range_low > 0 else 0
            if range_pct < 2.0:
                continue

            # Fix 1a: Gap/ATR Ratio - frühe Gapbewegungen filtern
            prev_close = analysis_window[-1]["close"]
            gap_pct = ((trigger_bar["open"] - prev_close) / prev_close * 100) if prev_close > 0 else 0
            atr_pct = (atr_5 / prev_close * 100) if prev_close > 0 else 3.0
            gap_to_atr = abs(gap_pct) / (atr_pct if atr_pct > 0 else 3.0)

            if gap_to_atr > 2.5:
                # Gap zu explosiv = wahrscheinlich Fade
                score_pct *= 0.5  # Reduziere Score
                details.append(f"Gap/ATR={gap_to_atr:.1f} — Fade-Risiko")
            elif gap_to_atr < 0.3:
                score_pct *= 0.7  # Gap zu klein = schwaches Signal

            if score_pct < min_bi_pct:
                continue

            # Entry/Stop/TP Berechnung
            breakout_level = range_high
            tp1_mult = 0.7 if grade == "C" else 1.0
            tp2_mult = 1.4 if grade == "C" else 2.0

            entry_price = round(range_high + atr_5 * 0.15, 2)  # Erhöht von 0.05 auf 0.15 für bessere Filtration
            stop_buffer = max(atr_5 * 0.9, range_size * 0.10)
            stop_price = round(range_high - stop_buffer, 2)
            tp1_price = round(range_high + range_size * tp1_mult, 2)
            tp2_price = round(range_high + range_size * tp2_mult, 2)

            # HIGH-1 FIX (Audit V1): Chase-Guard.
            # Wenn Kurs bereits >2% ueber Entry liegt, sind wir zu spaet dran —
            # LMT BUY wuerde entweder sofort als Market-Order fuellen (Chase) oder
            # auf unwahrscheinlichen Pullback warten. Signal droppen.
            current_price_check = trigger_bar["close"]
            chase_cap = entry_price + max(atr_5 * 0.50, (entry_price - stop_price) * 0.50)
            if current_price_check > chase_cap or current_price_check <= stop_price:
                continue

            geometry = _autotrader_long_geometry(
                entry_price,
                stop_price,
                tp1_price,
                tp2_price,
            )
            if not geometry["valid"]:
                continue
            risk = geometry["risk"]
            rr = geometry["rr"] or 0

            if rr < config.get("min_rr", 2.0):
                continue

            # Trend Check (SMA20 > SMA50)
            w_closes = [b["close"] for b in analysis_window]
            sma20 = sum(w_closes[-20:]) / 20
            sma50 = sum(w_closes[-50:]) / 50 if len(w_closes) >= 50 else sma20
            if sma20 <= sma50 * 0.97:
                continue

            result["signals_found"] += 1

            qualified_signals.append({
                "ticker": ticker,
                "grade": grade,
                "bi_score": bi_score,
                "bi_max": bi_max,
                "score_pct": round(score_pct, 1),
                "sm_hits": sm_hits,
                "entry": entry_price,
                "stop": stop_price,
                "tp1": tp1_price,
                "tp2": tp2_price,
                "rr": rr,
                "level_model": "bi_autotrader_structure_first_v2",
                "stop_source": "range_high_retest_invalidation",
                "tp1_source": "range_measured_move",
                "tp2_source": "range_measured_move",
                "range_pct": round(range_pct, 1),
                "current_price": trigger_bar["close"],
                "trigger_bar_date": trigger_bar.get("date"),
                "order_type": "STP_LMT",
                "stop_limit": round(entry_price + max(0.01, entry_price * 0.003), 2),
            })

        except Exception as e:
            continue

    # Sortiere nach Score (beste zuerst)
    qualified_signals.sort(key=lambda x: x["score_pct"], reverse=True)

    # Begrenze auf verfügbare Slots
    slots_available = max_pos - current_positions
    signals_to_trade = qualified_signals[:slots_available]

    _autotrader_log(f" {len(qualified_signals)} qualifizierte Signale, {len(signals_to_trade)} werden getradet", "INFO")

    # Orders an IBKR senden
    for signal in signals_to_trade:
        try:
            # Position Size berechnen
            shares = ib_calc_shares(
                signal["entry"],
                config.get("position_size", 2000),
                "Dollar" if config.get("position_size_type") == "dollar" else "Shares"
            )

            if shares <= 0:
                continue

            # Bracket Order senden
            # Mode: "full" → transmit=True (auto-execute), "semi" → transmit=False (confirm in TWS)
            is_full_auto = config.get("mode") == "full"

            ib_state = _get_ib_state()
            ib = ib_state.get("ib")
            if not ib or not ib.isConnected():
                _autotrader_log(f" IBKR Verbindung verloren bei {signal['ticker']}", "ERROR")
                result["errors"].append(f"IBKR offline bei {signal['ticker']}")
                break

            # Contract erstellen
            contract = ib_get_contract(signal["ticker"], "Aktien", "US")
            if not contract:
                _autotrader_log(f" Contract nicht gefunden: {signal['ticker']}", "WARN")
                continue

            try:
                qualified = ib.qualifyContracts(contract)
                if not qualified:
                    continue
            except Exception:
                continue

            # Bracket Order bauen
            main_action = "BUY"
            exit_action = "SELL"

            # Parent: Stop-limit breakout order. A buy-limit above market can
            # execute immediately and silently turn a trigger into a chase.
            parent = Order(
                action=main_action,
                orderType="STP LMT",
                auxPrice=round(signal["entry"], 2),
                lmtPrice=round(signal["stop_limit"], 2),
                totalQuantity=shares,
                transmit=False
            )
            parent_trade = ib.placeOrder(contract, parent)
            parent_id = parent_trade.order.orderId

            # Stop Loss
            stop_order = Order(
                action=exit_action,
                orderType="STP",
                auxPrice=round(signal["stop"], 2),
                totalQuantity=shares,
                parentId=parent_id,
                transmit=False
            )
            ib.placeOrder(contract, stop_order)

            # TP1: 50% der Shares
            tp1_shares = shares // 2
            tp2_shares = shares - tp1_shares

            tp1_order = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=round(signal["tp1"], 2),
                totalQuantity=tp1_shares,
                parentId=parent_id,
                transmit=False
            )
            ib.placeOrder(contract, tp1_order)

            # TP2: Restliche Shares — transmit hängt vom Mode ab
            tp2_order = Order(
                action=exit_action,
                orderType="LMT",
                lmtPrice=round(signal["tp2"], 2),
                totalQuantity=tp2_shares,
                parentId=parent_id,
                transmit=is_full_auto  # True = sofort aktiv, False = warten auf TWS-Bestätigung
            )
            ib.placeOrder(contract, tp2_order)

            ib.sleep(0.3)

            # Position tracken
            position_entry = {
                "ticker": signal["ticker"],
                "grade": signal["grade"],
                "entry": signal["entry"],
                "stop": signal["stop"],
                "tp1": signal["tp1"],
                "tp2": signal["tp2"],
                "shares": shares,
                "date": datetime.now().strftime("%Y-%m-%d"),
                "time": datetime.now().strftime("%H:%M:%S"),
                "parent_id": parent_id,
                "mode": "AUTO" if is_full_auto else "SEMI",
                "score_pct": signal["score_pct"],
                "rr": signal["rr"],
            }
            state["positions"].append(position_entry)
            state["cooldown_tickers"][signal["ticker"]] = datetime.now().strftime("%Y-%m-%d")
            state["trades_today"] = state.get("trades_today", 0) + 1

            result["orders_placed"] += 1
            result["signals"].append(signal)

            mode_label = " AUTO" if is_full_auto else " SEMI"
            _autotrader_log(
                f"{mode_label} ORDER: {signal['ticker']} Grade {signal['grade']} | "
                f"Entry ${signal['entry']} | SL ${signal['stop']} | TP1 ${signal['tp1']} | "
                f"TP2 ${signal['tp2']} | {shares} Shares | R:R {signal['rr']}",
                "TRADE"
            )

        except Exception as e:
            _autotrader_log(f" Order-Fehler {signal['ticker']}: {str(e)[:80]}", "ERROR")
            result["errors"].append(f"{signal['ticker']}: {str(e)[:50]}")

    # State speichern
    state["last_scan"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    state["status"] = "running"
    _autotrader_state_write(state)

    del ticker_history  # Memory freigeben

    _autotrader_log(
        f" Scan fertig: {result['signals_found']} Signale, {result['orders_placed']} Orders",
        "INFO"
    )

    return result


def autotrader_background_loop(poly_key):
    """
     Background-Loop: Scannt periodisch und platziert Orders.
    Läuft in separatem Thread.
    """
    _autotrader_clear_stop()
    config = _autotrader_config_load()
    interval_sec = config.get("scan_interval_min", 15) * 60

    state = _autotrader_state_read()
    state["status"] = "running"
    state["trades_today"] = 0
    state["daily_pnl"] = 0
    _autotrader_state_write(state)

    _autotrader_log(" Auto-Trader gestartet", "INFO")

    while not _autotrader_should_stop():
        try:
            # Config neu laden (kann sich während Laufzeit ändern)
            config = _autotrader_config_load()

            # Scan durchführen
            scan_result = autotrader_scan_once(poly_key, config)

            # Nächsten Scan planen
            interval_sec = config.get("scan_interval_min", 15) * 60
            _autotrader_log(f" Nächster Scan in {interval_sec // 60} Minuten", "INFO")

            # Warte (mit Stop-Check alle 10 Sekunden)
            for _ in range(interval_sec // 10):
                if _autotrader_should_stop():
                    break
                time.sleep(10)

        except Exception as e:
            _autotrader_log(f" Loop-Fehler: {str(e)[:100]}", "ERROR")
            time.sleep(60)  # 1 Min warten bei Fehler

    # Aufräumen
    state = _autotrader_state_read()
    state["status"] = "stopped"
    _autotrader_state_write(state)
    _autotrader_clear_stop()
    _autotrader_log(" Auto-Trader gestoppt", "INFO")


def _bi_config_load():
    """Lädt persistente BI Scanner Einstellungen."""
    try:
        with open(_BI_CONFIG_FILE, "r") as f:
            saved = json.load(f)
        config = dict(_BI_DEFAULT_CONFIG)
        config.update(saved)
        return config
    except Exception:
        return dict(_BI_DEFAULT_CONFIG)


def _bi_config_save(config):
    """Speichert BI Scanner Einstellungen persistent."""
    try:
        with open(_BI_CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except Exception:
        pass


def _bi_cache_load(direction="long"):
    """Lädt BI-Cache. Returns (results, timestamp, age_minutes) oder (None, None, None)."""
    try:
        path = _bi_cache_path(direction)
        if not os.path.exists(path):
            return None, None, None
        with open(path, "r") as f:
            cache = json.load(f)
        ts = cache.get("timestamp", 0)
        age_sec = time.time() - ts
        age_min = int(age_sec / 60)
        results = cache.get("results", [])
        return results, ts, age_min
    except Exception:
        return None, None, None


def _bi_cache_save(results, direction="long", *, partial=False, checked=0, total=0, detail=""):
    """Speichert atomar; Live-Zwischenstaende ersetzen nie den Final-Cache."""
    tmp_path = None
    try:
        cache = {
            "cached_at": datetime.now().isoformat(),
            "timestamp": time.time(),
            "direction": direction,
            "partial": bool(partial),
            "checked": int(checked or 0),
            "total": int(total or 0),
            "detail": detail or "",
            "count": len(results),
            "results": results
        }
        final_path = _bi_cache_path(direction)
        path = f"{final_path}.partial" if partial else final_path
        tmp_dir = os.path.dirname(path) or "."
        with tempfile.NamedTemporaryFile(mode="w", dir=tmp_dir, delete=False, suffix=".tmp") as f:
            tmp_path = f.name
            json.dump(cache, f, default=str)
        os.replace(tmp_path, path)
        tmp_path = None
        if not partial:
            try:
                os.unlink(f"{final_path}.partial")
            except FileNotFoundError:
                pass
        print(f"[BI {direction}] Cache gespeichert: {len(results)} Ergebnisse → {path}")
    except Exception as e:
        print(f"[BI {direction}] FEHLER beim Cache-Speichern: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _raise_on_systemic_analysis_failures(scan_name, attempts, errors):
    """Reject a scan when a shared code/data defect breaks almost every candidate."""
    attempts = int(attempts or 0)
    errors = int(errors or 0)
    if attempts >= 10 and errors >= 10 and errors * 5 >= attempts * 4:
        raise RuntimeError(
            f"{scan_name}: systemischer Analysefehler bei {errors}/{attempts} Kandidaten"
        )


def _bi_interleave_candidates_by_symbol(candidates):
    """
    Polygon reference pages are alphabetic. Interleave by first ticker character so
    partial BI caches already represent the whole market instead of only A/B.
    """
    buckets = defaultdict(list)
    for candidate in candidates or []:
        ticker = candidate if isinstance(candidate, str) else candidate.get("Ticker", candidate.get("ticker", ""))
        ticker = str(ticker or "").upper()
        if not ticker:
            continue
        key = ticker[0] if ticker[0].isalnum() else "#"
        buckets[key].append(candidate)

    ordered_keys = sorted(buckets)
    interleaved = []
    idx = 0
    while True:
        added = False
        for key in ordered_keys:
            bucket = buckets[key]
            if idx < len(bucket):
                interleaved.append(bucket[idx])
                added = True
        if not added:
            break
        idx += 1
    return interleaved


def _bi_progress_read(direction="long"):
    """Liest Scan-Fortschritt. Returns dict oder None."""
    try:
        path = _bi_progress_path(direction)
        if not os.path.exists(path):
            return None
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None


def _bi_progress_write(direction, status, checked=0, total=0, hits=0, no_data=0, top_score=0, avg_score=0, detail=""):
    """Schreibt Scan-Fortschritt in Datei."""
    try:
        progress = {
            "status": status,  # "running", "done", "error"
            "direction": direction,
            "checked": checked,
            "total": total,
            "hits": hits,
            "no_data": no_data,
            "top_score": top_score,
            "avg_score": avg_score,
            "detail": detail,
            "timestamp": time.time()
        }
        with open(_bi_progress_path(direction), "w") as f:
            json.dump(progress, f)
    except Exception:
        pass


def _bi_cache_age_str(age_min):
    """Formatiert Cache-Alter als lesbaren String."""
    if age_min < 1:
        return "gerade eben"
    elif age_min < 60:
        return f"vor {age_min} Min"
    else:
        h = age_min // 60
        m = age_min % 60
        return f"vor {h}h {m}m"


def _bi_scan_is_running(direction="long"):
    """Prüft ob ein Background-Scan läuft."""
    # Wenn Stop angefordert wurde → sofort als "nicht laufend" melden
    # damit die UI direkt zu FALL 2b/4 wechselt
    if _bi_should_stop(direction):
        return False
    prog = _bi_progress_read(direction)
    if not prog:
        return False
    if prog.get("status") != "running":
        return False
    # Timeout: Scan-Progress nicht aktualisiert seit 2 Min = Thread hängt/crashed
    age = time.time() - prog.get("timestamp", 0)
    if age > 120:  # 2 Min
        _bi_progress_clear(direction)
        return False
    return True


def _bi_strip_partial_bar(all_bars):
    """
    M-1 (BI-Audit 10.06.): Liefert die Bars OHNE den heutigen, noch LAUFENDEN
    Handelstag. Der Partial-Bar floss bisher als vollwertige Kerze in die
    Kontraktions-Signale (ATR-Squeeze meldete morgens in 96% "stark", 8/25
    marginale Setups flippten invalid->valid nur durch die Tagesuhr).
    Nach US-Close (>= 16:00 ET) ist der heutige Bar komplett und bleibt drin —
    gleiche Session-/Datums-Logik wie der RVOL-Pfad (letzter KOMPLETTER Tag).
    """
    if not all_bars:
        return all_bars
    try:
        import pytz
        _et = pytz.timezone("US/Eastern")
        _now_et = datetime.now(_et)
        if all_bars[-1].get("date", "") != _now_et.strftime("%Y-%m-%d"):
            return all_bars  # Letzter Bar ist nicht von heute → komplett
        if _now_et.hour >= 16:
            return all_bars  # Nach US-Close → heutiger Bar ist komplett
        return all_bars[:-1]
    except Exception:
        # Fallback: reine Datums-Logik (wie der bestehende RVOL-Pfad)
        if all_bars[-1].get("date", "") == datetime.now().strftime("%Y-%m-%d"):
            return all_bars[:-1]
        return all_bars


def _bi_background_scan(poly_key, direction="long", candidates=None):
    """
    Background-Thread: Analysiert vorgeladene Kandidaten auf BI-Signale.
    Kandidaten werden VOR dem Thread im Hauptthread geladen (1 API-Call).
    Thread macht nur die langsame Einzelanalyse (2000+ individuelle API-Calls).

    Args:
        poly_key: Polygon API Key
        direction: "long" oder "short"
        candidates: Vorgeladene Kandidaten-Liste (aus fetch_stock_data im Hauptthread)
    """
    try:
        # ── Fallback: Full stock universe from Polygon ──
        if not candidates:
            _bi_progress_write(direction, "scanning", detail="Lade volles Aktien-Universe...")
            try:
                candidates = []
                seen = set()

                # 1. Full Universe: /v3/reference/tickers mit Pagination (8000+ Aktien)
                url = "https://api.polygon.io/v3/reference/tickers"
                params = {
                    "type": "CS",
                    "market": "stocks",
                    "active": "true",
                    "limit": 1000,
                    "apiKey": poly_key,
                }
                next_url = None
                for _page in range(12):  # Max 12 Seiten = 12000 Aktien
                    if next_url:
                        resp = rate_limited_get(next_url, timeout=15)
                    else:
                        resp = rate_limited_get(url, params=params, timeout=15)
                    if resp.status_code != 200:
                        print(f"[BI {direction}] Universe page {_page} failed: {resp.status_code}")
                        break
                    data = resp.json()
                    results = data.get("results", [])
                    for r in results:
                        t = r.get("ticker", "")
                        if not t or t in seen:
                            continue
                        # ── Vorfilter: Schrott-Ticker aussortieren ──
                        # >4 Zeichen oder "." = Warrants, Units, Preferred, Bonds
                        # Suffixe: W=Warrant, U=Unit, R=Rights, H=When-Issued (ADAMH)
                        # Ausnahmen: bekannte 5-Buchstaben-Aktien werden NICHT gefiltert
                        # weil wir type=CS abfragen — aber Sonder-Suffixe trotzdem raus
                        if "." in t:
                            continue
                        if len(t) >= 5 and t[-1] in ("W", "U", "R", "H"):
                            continue  # ADAMH, AACQW, etc.
                        if len(t) > 5:
                            continue
                        seen.add(t)
                        candidates.append(t)
                    next_url = data.get("next_url")
                    if next_url:
                        next_url = f"{next_url}&apiKey={poly_key}"
                    else:
                        break
                    _bi_progress_write(direction, "scanning",
                                       detail=f"Universe: {len(candidates)} Aktien geladen (Seite {_page+1})...")

                # 2. Gainers/Losers als Bonus (aktuelle Mover)
                for endpoint in ["gainers", "losers"]:
                    try:
                        gurl = f"https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/{endpoint}"
                        resp = rate_limited_get(gurl, params={"apiKey": poly_key}, timeout=15)
                        if resp.status_code == 200:
                            for t in resp.json().get("tickers", []):
                                ticker = t.get("ticker", "")
                                if not ticker or ticker in seen:
                                    continue
                                # Gleicher Vorfilter wie Universe
                                if "." in ticker:
                                    continue
                                if len(ticker) >= 5 and ticker[-1] in ("W", "U", "R", "H"):
                                    continue
                                if len(ticker) > 5:
                                    continue
                                seen.add(ticker)
                                candidates.append(ticker)
                    except Exception:
                        pass

                _bi_progress_write(direction, "scanning",
                                   detail=f"{len(candidates)} Kandidaten — starte Analyse")
                print(f"[BI {direction}] Universe loaded: {len(candidates)} stocks")
            except Exception as e:
                _bi_progress_write(direction, "error", detail=f"Universe-Fehler: {str(e)[:50]}")
                raise RuntimeError(f"BI {direction} Universe konnte nicht geladen werden") from e

        if not candidates:
            _bi_progress_write(direction, "error", detail="Keine Kandidaten verfügbar")
            raise RuntimeError(f"BI {direction}: keine Kandidaten verfügbar")

        candidates = _bi_interleave_candidates_by_symbol(candidates)
        total = len(candidates)
        _bi_clear_stop(direction)  # Altes Stop-Signal aufräumen
        _bi_progress_write(direction, "running", total=total, detail=f"{total} Kandidaten — Starte Analyse...")

        # ── Analyse ──
        results = []
        checked = 0
        no_data_count = 0
        low_score_count = 0
        range_fail = 0
        atr_fail = 0
        rr_fail = 0
        ext_fail = 0       # H-2 (10.06.): Entry/Kurs zu weit auseinander (Chase-Schutz)
        cum_pump_fail = 0  # H-2c (10.06.): kumulativer 2-Tages-Pump
        score_sum = 0
        score_count = 0
        top_score = 0
        # V2.2: Score-Verteilungs-Buckets für Debugging
        _score_buckets = {"0-19": 0, "20-39": 0, "40-59": 0, "60-79": 0, "80-99": 0, "100+": 0}
        _short_trend_fail = 0
        _pattern_killed = 0
        analysis_attempts = 0
        analysis_errors = 0

        for candidate in candidates:
            # ── Stop-Signal prüfen ──
            if _bi_should_stop(direction):
                avg_sc = round(score_sum / max(1, score_count)) if score_count else 0
                _bi_progress_write(direction, "stopped", checked=checked, total=total,
                                   hits=len(results), no_data=no_data_count,
                                   top_score=top_score, avg_score=avg_sc,
                                   detail=f" Manuell gestoppt bei {checked}/{total}")
                if results:
                    results = sorted(results, key=lambda x: x.get("BI_Score", 0), reverse=True)
                    _bi_cache_save(
                        results,
                        direction,
                        partial=True,
                        checked=checked,
                        total=total,
                        detail=f"Manuell gestoppt bei {checked}/{total}",
                    )
                _bi_clear_stop(direction)
                return

            # candidate can be a string (ticker name) or dict with "Ticker" key
            ticker = candidate if isinstance(candidate, str) else candidate.get("Ticker", candidate.get("ticker", ""))
            if not ticker:
                continue
            if isinstance(candidate, str):
                candidate = {"Ticker": ticker, "ticker": ticker}
            else:
                candidate = dict(candidate)

            # OHLCV laden — Short braucht 300 Tage für SMA200 Bonus-Signale
            try:
                end_date = datetime.now()
                fetch_days = 320 if direction == "short" else 130
                start_date = end_date - timedelta(days=fetch_days)
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
                params = {"adjusted": "true", "sort": "asc", "apiKey": poly_key}

                resp = rate_limited_get(url, params=params, timeout=15)
                checked += 1

                if checked % 10 == 0:
                    # V2.2: Alle 10 statt 25 Stocks updaten für Live-Fortschritt
                    if not _bi_should_stop(direction):
                        avg_sc = round(score_sum / max(1, score_count))
                        _bi_progress_write(direction, "running", checked=checked, total=total,
                                           hits=len(results), no_data=no_data_count,
                                           top_score=top_score, avg_score=avg_sc,
                                           detail=f"{checked}/{total} analysiert")

                if resp.status_code != 200:
                    no_data_count += 1
                    if resp.status_code == 429:
                        time.sleep(5)  # Rate Limit → 5s Pause
                    continue

                api_data = resp.json()
                raw_bars = api_data.get("results", [])
                if not raw_bars or len(raw_bars) < 10:
                    no_data_count += 1
                    continue

                all_bars = []
                for bar in raw_bars:
                    all_bars.append({
                        "date": datetime.fromtimestamp(bar["t"] / 1000).strftime("%Y-%m-%d"),
                        "open": bar["o"],
                        "high": bar["h"],
                        "low": bar["l"],
                        "close": bar["c"],
                        "volume": bar["v"]
                    })
                # M-1 (BI-Audit 10.06.): Die an analyze_breakout_imminent uebergebenen
                # Bars enden mit dem letzten KOMPLETTEN Handelstag — der laufende
                # Partial-Bar verfaelschte die Kontraktions-Signale. Live-Preis-Checks
                # (Already-Broke-Out, Extension-Gates, Preis-Feld) nutzen weiter all_bars.
                _session_bars = _bi_strip_partial_bar(all_bars)
                bars = _session_bars[-30:]  # Letzte 30 KOMPLETTE Tage für BI-Analyse

                # ── Mindest-History: Brauchen min 15 Bars für zuverlässige Analyse ──
                # IPOs/frische Listings haben zu wenig Daten für Pattern-Erkennung
                if len(_session_bars) < 15:
                    no_data_count += 1
                    continue

                # ── Avg-Volume-Check: min $200K Ø Daily Dollar-Volume ──
                # V2.8: Heutigen partiellen Bar ausschließen für Volume-Berechnung
                _complete_bars = _session_bars
                avg_vol_10d = historical_volume_baseline(
                    (b.get("volume") for b in _complete_bars),
                    lookback=10,
                    minimum_periods=5,
                ) or 0.0
                avg_dollar_vol = avg_vol_10d * all_bars[-1]["close"] if all_bars[-1]["close"] > 0 else 0
                if avg_dollar_vol < 200_000:
                    no_data_count += 1
                    continue

                # ── RVOL Anomalie-Filter: >50x = IPO-Tag oder Pump-Scheme ──
                # Normales RVOL: 0.5-5.0, Breakout: 5-15, >50 = Spam
                _prev_vol = historical_volume_baseline(
                    (b.get("volume") for b in _complete_bars[:-5]),
                    lookback=15,
                    minimum_periods=8,
                )
                _recent_vol = historical_volume_baseline(
                    (b.get("volume") for b in _complete_bars[-5:]),
                    lookback=5,
                    minimum_periods=3,
                )
                _scan_rvol = _recent_vol / _prev_vol if _recent_vol and _prev_vol else 0
                if _scan_rvol > 50:
                    no_data_count += 1
                    continue

                # ── SPAC NAV-Detection: Preis $9.50-$10.50 + ATR < 1% = SPAC bei NAV ──
                _last_price = all_bars[-1]["close"]
                _spac_atr = calculate_wilder_atr(_session_bars, period=10)
                _atr_pct = (_spac_atr / _last_price * 100) if _spac_atr > 0 and _last_price > 0 else 0.0
                if 9.50 <= _last_price <= 10.50 and _atr_pct < 1.0:
                    no_data_count += 1
                    continue

            except Exception:
                no_data_count += 1
                continue

            analysis_attempts += 1
            try:
                # ── Already-Broke-Out Filter: Aktie hat heute schon >15% gemacht → kein "Imminent" mehr ──
                # Breakout IMMINENT = BEVOR der Breakout passiert, nicht NACHDEM er schon +30% gemacht hat
                # Für Long: Wenn die letzte Kerze schon >15% ist, ist der Breakout vorbei
                # Für Short: Wenn die letzte Kerze schon <-15% ist (Crash schon passiert)
                _last_bar = all_bars[-1]
                _prev_close = all_bars[-2]["close"] if len(all_bars) >= 2 else _last_bar["open"]
                if _prev_close > 0:
                    _today_change_pct = ((_last_bar["close"] - _prev_close) / _prev_close) * 100
                    if direction == "long" and _today_change_pct > 15:
                        continue  # Schon explodiert — zu spät für "Imminent"
                    if direction == "short" and _today_change_pct < -15:
                        continue  # Schon gecrasht — zu spät

                # H-2c (BI-Audit 10.06.): KUMULATIVER Pump-Filter (Long).
                # Der 1-Tages-Pump-Filter (patterns.py) laesst 2-Tages-Pumps
                # (+9-12%/Tag) in 25% der Faelle durch. Regel: Summe der letzten
                # 2 Tages-Moves > 12% UND > 4x StdDev der Vorlauf-Moves → kein Setup
                # (schon gelaufen). Auf kompletten Tagen gerechnet (ohne Partial-Bar).
                if direction == "long":
                    _pp_closes = [b["close"] for b in _session_bars[-30:]]
                    if len(_pp_closes) >= 10:
                        _pp_d1 = (_pp_closes[-1] - _pp_closes[-2]) / max(1e-9, _pp_closes[-2]) * 100
                        _pp_d2 = (_pp_closes[-2] - _pp_closes[-3]) / max(1e-9, _pp_closes[-3]) * 100
                        _pp_cum2 = _pp_d1 + _pp_d2
                        _pp_prev = [
                            abs(_pp_closes[i] - _pp_closes[i - 1]) / max(1e-9, _pp_closes[i - 1]) * 100
                            for i in range(1, len(_pp_closes) - 2)
                        ]
                        if _pp_prev and _pp_cum2 > 12:
                            _pp_mean = sum(_pp_prev) / len(_pp_prev)
                            _pp_std = (sum((x - _pp_mean) ** 2 for x in _pp_prev) / len(_pp_prev)) ** 0.5
                            if _pp_cum2 > 4 * max(0.1, _pp_std):
                                cum_pump_fail += 1
                                print(f"[BI {direction}] Suppressed {ticker}: cumulative_pump "
                                      f"(+{_pp_cum2:.1f}% in 2 Tagen, {_pp_cum2 / max(0.1, _pp_std):.1f}x StdDev)")
                                continue

                # Analyse
                result = analyze_breakout_imminent(bars, direction=direction)
                if len(result) == 8:
                    is_valid, bi_score, max_score, details, confidence, grade, sm_fires, sm_hits = result
                else:
                    is_valid, bi_score, max_score, details, confidence, grade = result
                    sm_fires, sm_hits = 0, 0

                score_sum += bi_score
                score_count += 1
                if bi_score > top_score:
                    top_score = bi_score

                # V2.2: Score-Bucket tracking
                if bi_score >= 100: _score_buckets["100+"] += 1
                elif bi_score >= 80: _score_buckets["80-99"] += 1
                elif bi_score >= 60: _score_buckets["60-79"] += 1
                elif bi_score >= 40: _score_buckets["40-59"] += 1
                elif bi_score >= 20: _score_buckets["20-39"] += 1
                else: _score_buckets["0-19"] += 1

                if not is_valid:
                    low_score_count += 1
                    continue

                # V2.7: Short Trend-Info (nur informativ, kein Hard-Reject mehr)
                # V2.6b Hard-Rejects waren zu aggressiv → haben 80%+ der Kandidaten eliminiert
                if direction == "short" and len(all_bars) >= 20:
                    _closes = [b["close"] for b in all_bars]
                    _sma20 = sum(_closes[-20:]) / 20
                    _cur = _closes[-1]
                    _above_sma20_pct = (_cur - _sma20) / _sma20 * 100 if _sma20 > 0 else 0
                    candidate["above_sma20_pct"] = round(_above_sma20_pct, 1)

                # H-2b (BI-Audit 10.06.): Fenster-Kohaerenz Analyse ↔ Level.
                # Die Signale rechnen auf dem ADAPTIVEN Konsolidierungsfenster —
                # Entry/Stop/TP muessen dasselbe Fenster nutzen, sonst ist die
                # Entry-Referenz ein Spike statt der Konsolidierung (Fuzz-Befund:
                # 22% der validen Longs mit Entry >5% ueber Kurs, median 12,4%).
                # patterns liefert range_days in den Details ("... Konsolidierung:
                # N Tage" bzw. "... ignoriert (N Tage)"). Fenster OHNE laufenden
                # Tag (_session_bars enden mit dem letzten kompletten Handelstag).
                _range_days = 0
                for _det in details:
                    _m_rd = re.search(r"Konsolidierung:\s*(\d+)\s*Tage", str(_det))
                    if not _m_rd:
                        _m_rd = re.search(r"ignoriert\s*\((\d+)\s*Tage\)", str(_det))
                    if _m_rd:
                        _range_days = int(_m_rd.group(1))
                        break
                if _range_days >= 5:
                    _range_bars = _session_bars[-_range_days:]
                else:
                    _range_bars = _session_bars[-15:]  # Fallback: 15 komplette Tage
                range_high = max(b["high"] for b in _range_bars)
                range_low = min(b["low"] for b in _range_bars)
                range_size = range_high - range_low
                range_pct = (range_size / range_low * 100) if range_low > 0 else 0

                if range_pct < 1.0:
                    range_fail += 1
                    continue

                _atr_bars = all_bars[-10:]
                avg_daily_range = sum((b["high"] - b["low"]) / b["close"] * 100 for b in _atr_bars if b["close"] > 0) / max(1, len(_atr_bars))
                if avg_daily_range < 0.3:
                    atr_fail += 1
                    continue

                grade_map = {"S": "S — ELITE", "A": "A — STARK", "B": "B — SOLIDE", "C": "C — WATCH", "D": "D — SCHWACH"}
                grade_label = grade_map.get(grade, grade)

                candidate["Alpha"] = bi_score
                candidate["BI_Score"] = bi_score
                candidate["BI_MaxScore"] = max_score
                candidate["BI_Details"] = details
                candidate["BI_Confidence"] = confidence
                candidate["BI_Direction"] = direction.upper()
                candidate["BI_Grade"] = grade
                candidate["BI_GradeLabel"] = grade_label

                atr_5 = calculate_wilder_atr(bars, period=5)
                if atr_5 <= 0:
                    atr_fail += 1
                    continue

                if direction == "long":
                    breakout_buffer = max(atr_5 * 0.1, range_size * 0.02)
                    invalidation_buffer = max(atr_5 * 0.9, range_size * 0.10)
                    candidate["Entry"] = round(range_high + breakout_buffer, 2)
                    candidate["StopLoss"] = round(range_high - invalidation_buffer, 2)
                    _risk_long = max(0.01, candidate["Entry"] - candidate["StopLoss"])
                    candidate["TP1"] = round(range_high + max(range_size * 0.75, _risk_long * 1.35), 2)
                    candidate["TP2"] = round(range_high + max(range_size * 1.618, _risk_long * 2.25), 2)
                    candidate["level_model"] = "bi_structure_first_v2"
                    candidate["stop_source"] = "range_high_retest_invalidation"
                    candidate["tp1_source"] = "range_extension"
                    candidate["tp2_source"] = "range_extension"
                else:
                    # V2.6b AUDIT: SHORT Entry — verbesserte Berechnung
                    _current = bars[-1]["close"]
                    _range_mid = (range_high + range_low) / 2

                    # H-2 (BI-Audit 10.06.): Short-Extension-Gate — vorher toter Code
                    # (Range inkl. letzter Kerze ⇒ Extension ≡ 0). Mit dem adaptiven
                    # Fenster (ohne laufenden Tag) kann der LIVE-Kurs real unter
                    # range_low liegen: zu weit drunter = Breakdown verpasst (Chase).
                    # Gate VOR der Breakdown/Pullback-Zweigwahl und auf dem LIVE-Kurs:
                    # ein Intraday-Crash (letzter KOMPLETTER Close noch in der Range)
                    # darf nicht in den Pullback-Zweig durchrutschen.
                    _live_close_s = all_bars[-1]["close"]
                    _atr_pct_s = atr_5 / _live_close_s if _live_close_s > 0 else 0
                    if _live_close_s < range_low * (1 - max(2 * _atr_pct_s, 0.03)):
                        ext_fail += 1
                        print(f"[BI {direction}] Suppressed {ticker}: entry_too_extended "
                              f"(Kurs {_live_close_s:.2f} zu weit unter Range-Low {range_low:.2f})")
                        continue

                    _near_low = _current < _range_mid  # Preis in unterer Hälfte = Breakdown
                    if _near_low:
                        # BREAKDOWN-SHORT: Preis nahe/unter Range-Low → Entry bei aktuellem Preis
                        candidate["Entry"] = round(_current, 2)
                        reclaim_stop = min(range_high, max(range_low + atr_5 * 0.75, _current + atr_5 * 1.2))
                        candidate["StopLoss"] = round(reclaim_stop, 2)
                        candidate["stop_source"] = "breakdown_reclaim_invalidation"
                    else:
                        # PULLBACK-SHORT: Preis nahe Range-High → Entry bei Resistance
                        candidate["Entry"] = round(range_high * 0.995, 2)
                        candidate["StopLoss"] = round(range_high + atr_5 * 0.5, 2)
                        candidate["stop_source"] = "range_high_reclaim_invalidation"
                    risk_short = max(0.01, candidate["StopLoss"] - candidate["Entry"])
                    # TP basiert auf Support/Range-Extensions, nicht auf reiner R:R-Optimierung.
                    # H-2 (BI-Audit 10.06.): Formel-Absicherung — mit dem adaptiven
                    # Range-Fenster (Pre-Breakdown) kann Entry unter range_low liegen.
                    # min() garantiert Stop > Entry > TP1 > TP2 strukturell
                    # (alter bi_short-TP1-Befund, der durch die Fensteraenderung
                    # sonst zurueckkommen koennte).
                    if candidate["Entry"] > range_low and (candidate["Entry"] - range_low) >= risk_short * 1.15:
                        candidate["TP1"] = round(range_low, 2)  # TP1 = Range-Low (logisches Ziel)
                    else:
                        candidate["TP1"] = round(max(0.01, min(
                            range_low - range_size * 0.272,
                            candidate["Entry"] - 0.5 * risk_short,
                        )), 2)
                    candidate["TP2"] = round(max(0.01, min(
                        range_low - range_size * 0.618,
                        candidate["TP1"] - 0.25 * risk_short,
                    )), 2)
                    candidate["level_model"] = "bi_structure_first_v2"
                    candidate["tp1_source"] = "range_low_support_or_extension"
                    candidate["tp2_source"] = "range_extension"

                _vrvp = build_vrvp_structure(
                    all_bars,
                    candidate.get("Entry"),
                    direction.upper(),
                    timeframe="1D",
                    num_bins=24,
                    min_bars=30,
                    lookback=90,
                )
                _vrvp_atr = calculate_wilder_atr(all_bars, period=14, lookback=90) or atr_5
                _setup = apply_vrvp_to_trade_setup(
                    {
                        "Entry": candidate.get("Entry"),
                        "StopLoss": candidate.get("StopLoss"),
                        "TP1": candidate.get("TP1"),
                        "TP2": candidate.get("TP2"),
                        "direction": direction.upper(),
                        "level_model": candidate.get("level_model"),
                        "stop_source": candidate.get("stop_source"),
                        "tp1_source": candidate.get("tp1_source"),
                        "tp2_source": candidate.get("tp2_source"),
                    },
                    _vrvp,
                    direction=direction.upper(),
                    asset_type="stock_swing",
                    atr=_vrvp_atr,
                )
                candidate["Entry"] = _setup.get("Entry", candidate.get("Entry"))
                candidate["StopLoss"] = _setup.get("StopLoss", candidate.get("StopLoss"))
                candidate["TP1"] = _setup.get("TP1", candidate.get("TP1"))
                candidate["TP2"] = _setup.get("TP2", candidate.get("TP2"))
                candidate["trade_setup"] = _setup
                candidate["level_model"] = _setup.get("level_model", candidate.get("level_model"))
                candidate["stop_source"] = _setup.get("stop_source", candidate.get("stop_source"))
                candidate["tp1_source"] = _setup.get("tp1_source", candidate.get("tp1_source"))
                candidate["tp2_source"] = _setup.get("tp2_source", candidate.get("tp2_source"))
                candidate["vrvp_applied"] = _setup.get("vrvp_applied", False)
                candidate["VRVP_POC"] = _setup.get("vrvp_poc")
                candidate["VRVP_VAH"] = _setup.get("vrvp_vah")
                candidate["VRVP_VAL"] = _setup.get("vrvp_val")

                # H-2a (BI-Audit 10.06.): Long-Extension-Gate — Entry zu weit ueber
                # dem aktuellen Kurs = "imminent"-Trigger, der nie sauber ausloest
                # (Chase-Schutz; Fuzz: 22% der validen Longs mit Entry >5% ueber
                # Kurs, Fantasie-R:R p95=15,2). Check auf dem FINALEN Entry
                # (nach VRVP) gegen den LIVE-Kurs.
                _live_close = all_bars[-1]["close"]
                if direction == "long" and _live_close > 0 and candidate.get("Entry"):
                    _atr_pct_l = atr_5 / _live_close
                    _entry_ext = (candidate["Entry"] - _live_close) / _live_close
                    if _entry_ext > max(2 * _atr_pct_l, 0.03):
                        ext_fail += 1
                        print(f"[BI {direction}] Suppressed {ticker}: entry_too_extended "
                              f"(Entry {_entry_ext * 100:.1f}% ueber Kurs, "
                              f"Limit {max(2 * _atr_pct_l, 0.03) * 100:.1f}%)")
                        continue

                _geometry = trade_geometry(
                    candidate.get("Entry"),
                    candidate.get("StopLoss"),
                    candidate.get("TP1"),
                    candidate.get("TP2"),
                    direction.upper(),
                )
                if not _geometry.get("valid") or (_geometry.get("rr") is not None and _geometry["rr"] < 1.2):
                    rr_fail += 1
                    continue
                candidate["RiskReward"] = round(_geometry["rr"], 1)
                candidate["RangeHigh"] = round(range_high, 2)
                candidate["RangeLow"] = round(range_low, 2)

                # V2.8: Volumen-Daten — letzten KOMPLETTEN Handelstag verwenden
                # all_bars[-1] kann heutiger partieller Bar sein (z.B. 1K um 10 Uhr)
                # Fix: Prüfe ob letzter Bar heute ist → dann all_bars[-2] für Volume/RVOL
                _today_str = datetime.now().strftime("%Y-%m-%d")
                _last_bar_is_today = len(all_bars) >= 2 and all_bars[-1].get("date", "") == _today_str
                _vol_bar_idx = -2 if _last_bar_is_today else -1  # Letzter kompletter Tag

                if len(all_bars) >= abs(_vol_bar_idx):
                    _last_vol = all_bars[_vol_bar_idx]["volume"]
                else:
                    _last_vol = all_bars[-1]["volume"] if all_bars else 0

                # Avg Vol: Immer auf abgeschlossene Tage basieren (heute raus)
                _vol_bars = all_bars[:-1] if _last_bar_is_today else all_bars
                _avg_vol_20 = historical_volume_baseline(
                    [b.get("volume") for b in _vol_bars],
                    lookback=20,
                    minimum_periods=10,
                ) or 0.0

                candidate["Volumen"] = int(_last_vol)
                candidate["AvgVolumen"] = int(_avg_vol_20)
                candidate["RVOL"] = round(_last_vol / _avg_vol_20, 2) if _avg_vol_20 > 0 else 0
                candidate["Preis"] = round(all_bars[-1]["close"], 2) if all_bars else 0
                candidate["Change%"] = round((all_bars[-1]["close"] - all_bars[-2]["close"]) / all_bars[-2]["close"] * 100, 2) if len(all_bars) >= 2 and all_bars[-2]["close"] > 0 else 0

                # V2.9: R:R ist wieder ein echtes Gate. Targets muessen signed zur
                # Richtung passen; bereits verpasste Short-Ziele werden nicht per abs()
                # als Reward schoengerechnet.

                # ── Chart-Pattern-Warnung (auf allen 90 Tage Bars) ──
                # V2.8: Zurück auf Original — nur informativ, KEINE Score-Penalties, KEIN Hard-Reject
                # (V2.5 Hard-Rejects + Score-Penalties haben B/A Grades zerstört)
                pattern_warnings = _detect_chart_patterns(all_bars, direction=direction)
                if pattern_warnings:
                    high_warnings = [w for w in pattern_warnings if w["severity"] == "high"]
                    candidate["PatternWarnings"] = pattern_warnings
                    candidate["PatternCount"] = len(pattern_warnings)
                    candidate["PatternHighCount"] = len(high_warnings)
                    warn_texts = [w["pattern"] for w in pattern_warnings]
                    candidate["PatternLabel"] = " | ".join(warn_texts)
                else:
                    candidate["PatternWarnings"] = []
                    candidate["PatternCount"] = 0
                    candidate["PatternHighCount"] = 0
                    candidate["PatternLabel"] = "Clean"

                # ── Short Bonus Signals (IMMER berechnen, nicht nur bei Pattern-Warnings) ──
                if direction == "short":
                    try:
                        bonus_result = calculate_short_bonus_signals(
                            ticker, all_bars, poly_key=poly_key, mode="swing"
                        )
                        short_bonus = bonus_result.get("bonus_score", 0)
                        bi_score += short_bonus
                        candidate["ShortBonusScore"] = short_bonus
                        # H-1 (10.06.): Bonus separat ausweisen — fliesst in den
                        # SCORE, hebt aber das Grade nicht (kommt aus patterns).
                        candidate["short_bonus"] = short_bonus
                        candidate["ShortBonusDetails"] = bonus_result.get("details", [])
                    except Exception:
                        candidate["ShortBonusScore"] = 0
                        candidate["short_bonus"] = 0
                        candidate["ShortBonusDetails"] = []

                    _short_rvol = candidate.get("RVOL", 0)
                    _has_stage4 = any("Stage 4" in str(d) for d in candidate.get("ShortBonusDetails", []))
                    candidate["has_stage4"] = _has_stage4

                # ── V3.2: Multi-Day Runner Bonus (ELAB-Pattern) ──
                # Erkennt Aktien die 2+ Tage in Folge stark steigen mit hohem Volume
                # Pattern: Day1 +30%+, Day2 Gap-Up + Hold, Day3 Gap-Up = Multi-Day Runner
                # Kriterien: Low Float + Katalysator + aufeinanderfolgende Big Days
                _mdr_bonus = 0
                _mdr_tag = None
                if direction == "long" and len(all_bars) >= 5:
                    _recent = all_bars[-5:]  # Letzte 5 Tage
                    _big_days = 0  # Tage mit >15% Gain
                    _gap_ups = 0   # Gap-Ups (Open > PrevClose)
                    _consec_green = 0  # Aufeinanderfolgende grüne Tage
                    _max_consec = 0
                    _total_move = 0
                    _vol_surge_days = 0  # Tage mit >3x Avg Volume

                    for i in range(1, len(_recent)):
                        _prev_c = _recent[i-1]["close"]
                        _cur_c = _recent[i]["close"]
                        _cur_o = _recent[i]["open"]
                        _cur_v = _recent[i]["volume"]

                        if _prev_c > 0:
                            _day_chg = (_cur_c - _prev_c) / _prev_c * 100
                            _gap_pct = (_cur_o - _prev_c) / _prev_c * 100

                            if _day_chg > 15:
                                _big_days += 1
                            if _gap_pct > 3:
                                _gap_ups += 1
                            if _cur_c > _prev_c:
                                _consec_green += 1
                                _max_consec = max(_max_consec, _consec_green)
                            else:
                                _consec_green = 0
                            if _avg_vol_20 > 0 and _cur_v > _avg_vol_20 * 3:
                                _vol_surge_days += 1

                    # Gesamtbewegung der letzten 5 Tage
                    if _recent[0]["close"] > 0:
                        _total_move = (_recent[-1]["close"] - _recent[0]["close"]) / _recent[0]["close"] * 100

                    # Multi-Day Runner Scoring
                    # V3.3: Volume-Exhaustion-Check — sinkendes Volume = Distribution
                    _last_day_vol = _recent[-1]["volume"] if _recent else 0
                    _prev_day_vol = _recent[-2]["volume"] if len(_recent) >= 2 else 0
                    _vol_declining = _prev_day_vol > 0 and _last_day_vol < _prev_day_vol * 0.5

                    if _vol_declining and _total_move > 30:
                        _mdr_bonus = 0
                        _mdr_tag = f"MDR CRASH-RISIKO: {_total_move:.0f}% Move aber Volume -50%"
                    elif _big_days >= 2 and _max_consec >= 2 and _total_move > 50:
                        _mdr_bonus = 25
                        _mdr_tag = f"MDR ELITE: {_big_days} Big Days, {_total_move:.0f}% Move, {_vol_surge_days} Vol-Surges"
                    elif _big_days >= 2 and _total_move > 30:
                        _mdr_bonus = 18
                        _mdr_tag = f"MDR STARK: {_big_days} Big Days, {_total_move:.0f}% Move"
                    elif _big_days >= 1 and _gap_ups >= 1 and _total_move > 20:
                        _mdr_bonus = 12
                        _mdr_tag = f"MDR AKTIV: {_big_days} Big Day + Gap-Up, {_total_move:.0f}% Move"
                    elif _max_consec >= 3 and _total_move > 15:
                        _mdr_bonus = 8
                        _mdr_tag = f"MDR BASIS: {_max_consec} Grüne Tage, {_total_move:.0f}% Move"

                    if _mdr_bonus > 0:
                        # NUR Info-Tag, KEIN Score-Bonus — BI Scanner misst "Breakout Imminent",
                        # MDR ist bereits ausgebrochen. Score-Verfälschung vermeiden.
                        details.append(f"🔥 {_mdr_tag}")
                        candidate["MDR_Bonus"] = _mdr_bonus  # Informativ für Frontend
                        candidate["MDR_Tag"] = _mdr_tag

                candidate["BI_Score"] = max(0, bi_score)

                # ── H-1 (BI-Audit 10.06.): EINE Grade-Quelle ──
                # Das Grade von analyze_breakout_imminent (V3.3/V4-Leiter, S=85+4f /
                # A=71+3f / B=57+2h / C=55+1h) wird DURCHGEREICHT (bereits oben in
                # BI_Grade/BI_GradeLabel gesetzt). Die Alt-Leiter (113/99/85/75) lag
                # ueber der empirischen Score-Obergrenze (~98): 0,0% der Lehrbuch-
                # Akkumulationen erreichten Scanner-Grade S/A — das Mail-Gate war
                # strukturell ausgehungert. Wichtig: Das patterns-Grade ist VOR dem
                # ShortBonus berechnet — der Bonus fliesst weiter in den SCORE
                # (separat ausgewiesen: short_bonus/ShortBonusScore), hebt aber das
                # Grade nicht mehr ueber die Leiter.
                _cand_rvol = candidate.get("RVOL", 0)

                # ── RVOL Guard: Ohne Volumen kein Top-Grade ──
                # Breakout ohne Volumen ist nicht vertrauenswürdig
                if _cand_rvol < 0.7 and candidate["BI_Grade"] in ("S", "A"):
                    candidate["BI_Grade"], candidate["BI_GradeLabel"] = "B", "B — SOLIDE (RVOL zu niedrig)"
                elif _cand_rvol < 0.5 and candidate["BI_Grade"] == "B":
                    candidate["BI_Grade"], candidate["BI_GradeLabel"] = "C", "C — WATCH (RVOL zu niedrig)"

                results.append(candidate)

                # V2.2: Live-Zwischenergebnisse speichern — alle 5 neuen Treffer
                if len(results) % 5 == 0 or len(results) == 1:
                    _live = sorted(results, key=lambda x: x.get("BI_Score", 0), reverse=True)[:50]
                    _bi_cache_save(
                        _live,
                        direction=direction,
                        partial=True,
                        checked=checked,
                        total=total,
                        detail=f"Zwischenstand: {checked}/{total} analysiert",
                    )
                    print(f"[BI {direction}] Live-Update: {len(_live)} Treffer bei {checked}/{total}")
            except Exception as e:
                analysis_errors += 1
                print(f"[BI {direction}] Error analyzing {ticker}: {e}")
                continue

        # Finale Sortierung + Speichern
        _raise_on_systemic_analysis_failures(
            f"BI {direction}", analysis_attempts, analysis_errors
        )
        results = sorted(results, key=lambda x: x.get("BI_Score", 0), reverse=True)[:50]
        _bi_cache_save(
            results,
            direction=direction,
            partial=False,
            checked=checked,
            total=total,
            detail="Finaler BI Scan abgeschlossen",
        )

        avg_sc = round(score_sum / max(1, score_count))
        _thr = 45 if direction == "long" else 40  # V4: Angepasst an post-Audit Scores
        _buckets_str = " | ".join(f"{k}:{v}" for k, v in _score_buckets.items() if v > 0)
        pipeline = (f"{total} Kandidaten → {no_data_count} kein History → "
                    f"{cum_pump_fail} 2d-Pump → "
                    f"{score_count} analysiert (Ø {avg_sc}, Top {top_score}, Threshold {_thr}) → "
                    f"{low_score_count} unter Threshold → {range_fail} Range → "
                    f"{atr_fail} ATR → {ext_fail} Extension → {rr_fail} R:R → {len(results)} Treffer"
                    f" [Scores: {_buckets_str}]")
        print(f"[BI {direction}] Pipeline: {pipeline}")

        _bi_progress_write(direction, "done", checked=checked, total=total,
                           hits=len(results), no_data=no_data_count,
                           top_score=top_score, avg_score=avg_sc,
                           detail=pipeline)

    except Exception as e:
        _bi_progress_write(direction, "error", detail=f"Fehler: {str(e)[:100]}")
        raise


def _biotech_config_load():
    """Lädt persistente Biotech Scanner Einstellungen."""
    try:
        with open(_BIOTECH_CONFIG_FILE, "r") as f:
            saved = json.load(f)
        # Merge mit Defaults (falls neue Keys hinzukommen)
        config = dict(_BIOTECH_DEFAULT_CONFIG)
        config.update(saved)
        return config
    except Exception:
        return dict(_BIOTECH_DEFAULT_CONFIG)


def _biotech_config_save(config):
    """Speichert Biotech Scanner Einstellungen persistent."""
    try:
        with open(_BIOTECH_CONFIG_FILE, "w") as f:
            json.dump(config, f)
    except Exception:
        pass


def _biotech_progress_write(status, **kwargs):
    try:
        data = {"status": status, "timestamp": time.time()}
        data.update(kwargs)
        with open(_biotech_progress_file(), "w") as f:
            json.dump(data, f)
    except Exception:
        pass


def _biotech_progress_read():
    try:
        with open(_biotech_progress_file(), "r") as f:
            return json.load(f)
    except Exception:
        return None


def _biotech_cache_save(results, *, partial=False, checked=0, total=0, detail=""):
    """Speichert atomar; Live-Zwischenstaende ersetzen nie den Final-Cache."""
    tmp_path = None
    try:
        final_path = _biotech_cache_file()
        path = f"{final_path}.partial" if partial else final_path
        payload = {
            "cached_at": datetime.now().isoformat(),
            "results": results,
            "timestamp": time.time(),
            "partial": bool(partial),
            "checked": int(checked or 0),
            "total": int(total or 0),
            "detail": detail or "",
        }
        tmp_dir = os.path.dirname(path) or "."
        with tempfile.NamedTemporaryFile(mode="w", dir=tmp_dir, delete=False, suffix=".tmp") as f:
            tmp_path = f.name
            json.dump(payload, f, default=str)
        os.replace(tmp_path, path)
        tmp_path = None
        if not partial:
            try:
                os.unlink(f"{final_path}.partial")
            except FileNotFoundError:
                pass
        print(f"[Biotech] Cache gespeichert: {len(results)} Ergebnisse → {path}")
    except Exception as e:
        print(f"[Biotech] FEHLER beim Cache-Speichern: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _biotech_cache_load(max_age_hours=2):
    try:
        with open(_biotech_cache_file(), "r") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > max_age_hours * 3600:
            return None
        results = data.get("results", [])
        # Leere Ergebnisse nicht als gültigen Cache behandeln
        return results if results else None
    except Exception:
        return None


def _fetch_biotech_universe(poly_key, min_price=0.50, min_mcap_m=20, max_mcap_m=50000):
    """
    Scannt Polygon Ticker-Datenbank nach Biotech/Pharma Aktien.
    Nutzt SIC-Codes + Name-Keywords für breite Erkennung.
    Filtert nach min_price und min_mcap_m (Market Cap in Millionen).
    """
    biotech_tickers = []
    min_mcap = min_mcap_m * 1_000_000  # Umrechnung in absoluten Wert
    max_mcap = max_mcap_m * 1_000_000

    # Methode 1: SIC-Code basiert (präziser) — ALLE SIC Codes + Pagination
    existing_tickers = set()
    for sic in BIOTECH_SIC_CODES:
        try:
            url = "https://api.polygon.io/v3/reference/tickers"
            params = {
                "apiKey": poly_key,
                "market": "stocks",
                "active": "true",
                "sic_code": sic,
                "limit": 250,
            }
            # Pagination: Polygon gibt next_url zurück wenn es mehr Ergebnisse gibt
            for _page in range(5):  # Max 5 Seiten = 1250 Tickers pro SIC
                resp = rate_limited_get(url, params=params, timeout=10)
                if resp.status_code != 200:
                    break
                data = resp.json()
                results = data.get("results", [])
                for r in results:
                    ticker = r.get("ticker", "")
                    if ticker and ticker not in existing_tickers:
                        existing_tickers.add(ticker)
                        biotech_tickers.append({
                            "ticker": ticker,
                            "name": r.get("name", ""),
                            "market_cap": r.get("market_cap", 0),
                            "sic_code": sic,
                            "primary_exchange": r.get("primary_exchange", ""),
                            "source": "SIC"
                        })
                # Nächste Seite?
                next_url = data.get("next_url")
                if not next_url:
                    break
                url = next_url
                params = {"apiKey": poly_key}  # next_url enthält bereits die Query-Parameter
        except Exception:
            continue

    # Methode 2: Keyword-basiert für Tickers die keinen SIC haben
    for keyword in ["biotech", "therapeutics", "pharma", "oncology", "genomics"]:
        try:
            url = "https://api.polygon.io/v3/reference/tickers"
            params = {
                "apiKey": poly_key,
                "market": "stocks",
                "active": "true",
                "search": keyword,
                "limit": 100,
            }
            resp = rate_limited_get(url, params=params, timeout=10)
            if resp.status_code == 200:
                results = resp.json().get("results", [])
                for r in results:
                    ticker = r.get("ticker", "")
                    name = (r.get("name", "") or "").lower()
                    if ticker and ticker not in existing_tickers:
                        if any(kw in name for kw in BIOTECH_NAME_KEYWORDS):
                            existing_tickers.add(ticker)
                            biotech_tickers.append({
                                "ticker": ticker,
                                "name": r.get("name", ""),
                                "market_cap": r.get("market_cap", 0),
                                "sic_code": r.get("sic_code", ""),
                                "primary_exchange": r.get("primary_exchange", ""),
                                "source": "Keyword"
                            })
        except Exception:
            continue

    # Market Cap Filter anwenden (Polygon liefert MCap bei Reference-Tickers)
    # Preis-Filter kann erst beim Scan angewendet werden (nicht in Reference-API)
    if min_mcap > 0 or max_mcap > 0:
        biotech_tickers = [t for t in biotech_tickers
                           if t.get("market_cap", 0) == 0  # MCap unbekannt → erstmal behalten, beim Scan filtern
                           or (min_mcap <= t.get("market_cap", 0) <= max_mcap)]

    return biotech_tickers


def _scan_biotech_news(poly_key, ticker, limit=5):
    """
    Scannt News für einen Biotech-Ticker nach FDA/Pipeline Katalysatoren.
    Returns: dict mit catalyst_score, catalysts list, news items
    """
    try:
        url = "https://api.polygon.io/v2/reference/news"
        resp = rate_limited_get(url, params={
            "ticker": ticker, "limit": limit, "order": "desc",
            "sort": "published_utc", "apiKey": poly_key
        }, timeout=5)

        if resp.status_code != 200:
            return {"catalyst_score": 0, "catalysts": [], "news": [], "negative_flags": []}

        articles = resp.json().get("results", [])
        catalyst_score = 0
        catalysts = []
        negative_flags = []
        news_items = []
        best_tier = None
        forward_catalyst = False  # H-4 (10.06.): angekuendigtes, noch nicht eingetretenes Ergebnis

        for article in articles[:limit]:
            title = (article.get("title", "") or "").lower()
            desc = (article.get("description", "") or "").lower()
            pub_date = (article.get("published_utc", "") or "")[:10]

            # K-1a (10.06.): Normalisierung — lowercased + Roman→Arabisch
            # ("phase iii" → "phase 3"), auf title UND description.
            norm_combined = (_biotech_normalize_text(title) + " " + _biotech_normalize_text(desc)).strip()

            # Sentiment
            sentiment = "neutral"
            for insight in article.get("insights", []):
                if insight.get("ticker") == ticker:
                    sentiment = insight.get("sentiment", "neutral")
                    break

            # K-1b (10.06.): NEGATIV-PRUEFUNG ZUERST, auf title UND description.
            # 1) Statische Phrasen (mit Wortgrenzen, Plural-s, Verneinungs-Guard:
            #    "no safety concerns seen" ist kein Negativ-Signal).
            # 2) Verb-Form-/Kontext-Patterns (misses ... endpoint, failed to meet,
            #    fell short, did not achieve, discontinuation of development, ...).
            # Stem-Dedupe: "registered direct offering" + Pattern "public offering"
            # erzeugen nur EINEN Flag pro Artikel-Sachverhalt.
            _is_negative_article = False
            _article_neg_stems = set()

            def _neg_stems(label):
                return {w[:6] for w in label.split() if len(w) >= 3}

            for neg_kw, penalty in BIOTECH_NEGATIVE_CATALYSTS.items():
                if _biotech_negative_match(norm_combined, neg_kw):
                    _stems = _neg_stems(neg_kw)
                    if _stems & _article_neg_stems:
                        _is_negative_article = True
                        continue
                    negative_flags.append({"flag": neg_kw, "penalty": penalty, "date": pub_date})
                    _article_neg_stems |= _stems
                    _is_negative_article = True

            for _neg_label, _neg_penalty, _neg_rx in BIOTECH_NEGATIVE_PATTERNS:
                if _neg_rx.search(norm_combined):
                    _stems = _neg_stems(_neg_label)
                    if _stems & _article_neg_stems:
                        _is_negative_article = True
                        continue
                    negative_flags.append({"flag": _neg_label, "penalty": _neg_penalty, "date": pub_date})
                    _article_neg_stems |= _stems
                    _is_negative_article = True

            # H-4 (10.06.): FORWARD-Check — "expected/anticipated/on track to/
            # will report/upcoming ..." + Ergebnis-Keyword = angekuendigtes Event.
            # KEIN catalyst_score (nichts ist eingetreten), nur Watch-Flag.
            # Die Termin-Wertung (Event-Datum, Naehe) laeuft ueber den BPIQ-Pfad.
            _is_forward_article = False
            if not _is_negative_article:
                if _BIOTECH_FORWARD_RE.search(norm_combined) and _BIOTECH_FORWARD_RESULT_RE.search(norm_combined):
                    _is_forward_article = True
                    forward_catalyst = True

            # Positive Catalyst Detection (Tier-basiert, mit Wortgrenzen)
            # Skip wenn Artikel negativ (z.B. CRL) oder forward (H-4).
            # K-1c (10.06.): Verneinungsfenster — "did not ... <Positiv-Keyword>"
            # innerhalb ~6 Woerter blockiert den Positiv-Score.
            article_catalysts = []
            if not _is_negative_article and not _is_forward_article:
                for tier_name, tier_data in FDA_CATALYST_KEYWORDS.items():
                    for kw in tier_data["keywords"]:
                        if _biotech_positive_match(norm_combined, kw):
                            cat = {
                                "keyword": kw,
                                "tier": tier_name,
                                "score": tier_data["score"],
                                "label": tier_data["label"],
                                "date": pub_date,
                                "headline": article.get("title", "")[:100]
                            }
                            article_catalysts.append(cat)
                            if best_tier is None or tier_data["score"] > best_tier:
                                best_tier = tier_data["score"]
                            break  # Nur höchster Tier pro Artikel
                    if article_catalysts:
                        break

            catalysts.extend(article_catalysts)

            news_items.append({
                "title": article.get("title", "")[:100],
                "description": (article.get("description", "") or "")[:200],
                "published": pub_date,
                "sentiment": sentiment,
                "catalyst": article_catalysts[0]["label"] if article_catalysts else None,
                "url": article.get("article_url", ""),
            })

        # V3: Aggressiverer Time-Decay — FDA-Events sind am Event-Tag relevant,
        # danach schnell eingepreist. ONCY FDA Event vom 17.02. (52 Tage alt) mit
        # 85% Score ist Unsinn — der Markt hat das längst verarbeitet.
        # Neue Kurve: 7d=100%, 14d=75%, 30d=50%, 60d=25%, 90d=10%, >90d=5%
        # H-4 (10.06.): Das News-PUBLIKATIONSdatum ist hier die korrekte
        # Decay-Basis, weil nur EINGETRETENE Events scoren (Forward-Meldungen
        # sind oben aussortiert, forward_catalyst=True). Event-TERMINE werden
        # nicht hier, sondern ueber den BPIQ-Pfad bewertet (Event-Datum,
        # days_until, Kategorie IMMINENT/UPCOMING/...).
        from datetime import datetime as _dt_cls, timedelta as _td_cls, timezone as _dt_tz
        _today = _dt_cls.now(_dt_tz.utc).date()
        for cat in catalysts:
            _cat_date_str = cat.get("date", "")
            if _cat_date_str and len(_cat_date_str) >= 10:
                try:
                    _cat_date = _dt_cls.strptime(_cat_date_str[:10], "%Y-%m-%d").date()
                    _days_old = (_today - _cat_date).days
                    if _days_old > 90:
                        cat["score"] = int(cat["score"] * 0.05)  # > 3 Monate: 5%
                    elif _days_old > 60:
                        cat["score"] = int(cat["score"] * 0.10)  # > 2 Monate: 10%
                    elif _days_old > 30:
                        cat["score"] = int(cat["score"] * 0.25)  # > 1 Monat: 25%
                    elif _days_old > 14:
                        cat["score"] = int(cat["score"] * 0.50)  # > 2 Wochen: 50%
                    elif _days_old > 7:
                        cat["score"] = int(cat["score"] * 0.75)  # > 1 Woche: 75%
                    # <= 7 Tage: voller Score (100%)
                    cat["days_old"] = _days_old
                except (ValueError, TypeError):
                    # Datum nicht parsebar → konservativ: halber Score
                    cat["score"] = int(cat["score"] * 0.50)
            else:
                # Kein Datum vorhanden → konservativ: halber Score
                # (könnte alt oder neu sein, Unsicherheits-Penalty)
                cat["score"] = int(cat["score"] * 0.50)

        # Merke ob VOR Decay Catalyst-Keywords gefunden wurden (für BPIQ-Trigger)
        _had_catalyst_keywords = len(catalysts) > 0

        # Catalyst-Liste bereinigen — Einträge mit Score 0 nach Decay entfernen
        catalysts = [c for c in catalysts if c.get("score", 0) > 0]

        # V68: Score = bester Catalyst + 50% vom zweitbesten (gewichtete Kumulation)
        # Reine max()-Logik unterschlug multi-Catalyst-Situationen (FDA + Phase 3)
        # V69-FIX: Catalysts nach Score sortieren (höchster zuerst)
        if catalysts:
            catalysts.sort(key=lambda c: c.get("score", 0), reverse=True)
            catalyst_score = catalysts[0]["score"]
            if len(catalysts) > 1:
                catalyst_score += int(catalysts[1]["score"] * 0.5)  # 50% Bonus für 2. Catalyst

        # Negative Flags abziehen (mit Time-Decay) — FIX 1: Weniger aggressive Decay
        from datetime import datetime as _nf_dt_cls, timezone as _nf_dt_tz
        _nf_today = _nf_dt_cls.now(_nf_dt_tz.utc).date()
        for nf in negative_flags:
            nf_date_str = nf.get("date", "")
            nf_age_days = 0
            if nf_date_str and len(nf_date_str) >= 10:
                try:
                    nf_date = _nf_dt_cls.strptime(nf_date_str[:10], "%Y-%m-%d").date()
                    nf_age_days = (_nf_today - nf_date).days
                except (ValueError, TypeError):
                    nf_age_days = 0

            # V3: Gleicher aggressiver Decay wie positive Catalysts
            decay = 1.0
            if nf_age_days > 90:
                decay = 0.05
            elif nf_age_days > 60:
                decay = 0.10
            elif nf_age_days > 30:
                decay = 0.25
            elif nf_age_days > 14:
                decay = 0.50
            elif nf_age_days > 7:
                decay = 0.75
            catalyst_score += int(nf["penalty"] * decay)

        # FIX 3: Catalyst Cap Increase from 30 to 45
        catalyst_score = max(0, min(45, catalyst_score))

        return {
            "catalyst_score": catalyst_score,
            "catalysts": catalysts,
            "news": news_items,
            "negative_flags": negative_flags,
            "best_catalyst": catalysts[0] if catalysts else None,  # Jetzt korrekt: höchster Score
            "had_catalyst_keywords": _had_catalyst_keywords,  # Vor Decay Keywords gefunden?
            "forward_catalyst": forward_catalyst,  # H-4: angekuendigtes Event (Watch-Kontext, kein Score)
        }
    except Exception:
        return {"catalyst_score": 0, "catalysts": [], "news": [], "negative_flags": [],
                "forward_catalyst": False}


def _check_clinical_trials(company_name, ticker):
    """
    Prüft ClinicalTrials.gov API nach aktiven Studien + Readout-Kalender.
    Returns: dict mit pipeline_score, trials info, catalyst_readouts

    Catalyst-Readout-Logik:
    - OVERDUE: Primary Completion überschritten → Daten kommen BALD
    - IMMINENT: Primary Completion in ≤30 Tagen
    - UPCOMING: Primary Completion in ≤90 Tagen
    - Phase 3 > Phase 2 > Phase 1 Gewichtung
    """
    try:
        from datetime import timedelta as _td
        _now = datetime.now()

        # Suche nach Company Name (besser als Ticker)
        _strip_words = {"inc", "inc.", "corp", "corp.", "ltd", "ltd.", "plc", "co", "co.",
                        "group", "holdings", "llc", "sa", "se", "nv", "ag", "gmbh", "the",
                        "pharmaceuticals", "pharmaceutical", "therapeutics", "biosciences",
                        "oncology", "sciences", "common", "ordinary", "shares", "class"}
        if company_name:
            _name_parts = [w for w in company_name.split() if w.lower() not in _strip_words]
            search_term = " ".join(_name_parts[:3]) if _name_parts else ticker
        else:
            search_term = ticker

        url = "https://clinicaltrials.gov/api/v2/studies"

        # Zwei Suchen: query.spons (exakt) und query.term (breiter) — nehme besseres Ergebnis
        _all_studies = []
        for _qfield in ["query.spons", "query.term"]:
            params = {
                _qfield: search_term,
                "pageSize": 50,
                "format": "json",
            }
            try:
                resp = rate_limited_get(url, params=params, timeout=10)
                if resp.status_code == 200 and resp.text.strip():
                    _fetched = resp.json().get("studies", [])
                    if len(_fetched) > len(_all_studies):
                        _all_studies = _fetched
            except Exception:
                pass

        # Client-side Filter: nur aktive Studies
        _active_statuses = {"RECRUITING", "ACTIVE_NOT_RECRUITING", "ENROLLING_BY_INVITATION", "NOT_YET_RECRUITING"}
        studies = [s for s in _all_studies
                   if s.get("protocolSection", {}).get("statusModule", {}).get("overallStatus", "") in _active_statuses]

        phase_counts = {"PHASE3": 0, "PHASE2": 0, "PHASE1": 0, "EARLY_PHASE1": 0, "PHASE4": 0, "NA": 0}
        trials = []
        catalyst_readouts = []  # NEU: Readout-Kalender

        for study in studies:
            proto = study.get("protocolSection", {})
            ident = proto.get("identificationModule", {})
            status_mod = proto.get("statusModule", {})
            design = proto.get("designModule", {})
            cond_mod = proto.get("conditionsModule", {})

            nct_id = ident.get("nctId", "")
            title = ident.get("briefTitle", "")[:80]
            phases = design.get("phases", [])
            status = status_mod.get("overallStatus", "")
            conditions = cond_mod.get("conditions", [])

            phase_label = phases[0] if phases else "NA"
            phase_key = phase_label.replace(" ", "").upper()
            if phase_key in phase_counts:
                phase_counts[phase_key] += 1

            # ── NEU: Readout-Kalender ──
            _pc = status_mod.get("primaryCompletionDateStruct", {})
            _pc_str = _pc.get("date", "")
            _days_until = None
            _readout_category = ""
            if _pc_str:
                try:
                    if len(_pc_str) == 7:  # YYYY-MM
                        _pc_date = datetime.strptime(_pc_str, "%Y-%m")
                    else:
                        _pc_date = datetime.strptime(_pc_str[:10], "%Y-%m-%d")
                    _days_until = (_pc_date - _now).days

                    if _days_until < 0:
                        # Decay-Logik: Extrem überfällige Readouts sind Datenleichen
                        # Sponsoren updaten ClinicalTrials.gov oft nicht → 2000d "overdue"
                        _overdue_days = abs(_days_until)
                        if _overdue_days <= 180:
                            _readout_category = "OVERDUE"       # Unbestätigt: Datum liegt in der Vergangenheit
                        elif _overdue_days <= 365:
                            _readout_category = "OVERDUE_STALE" # Fragwürdig — reduzierter Score
                        # >365d: Ignorieren — fast sicher abgeschlossen aber nicht aktualisiert
                    elif _days_until <= 30:
                        _readout_category = "IMMINENT"
                    elif _days_until <= 90:
                        _readout_category = "UPCOMING"
                except Exception:
                    pass

            _trial_info = {
                "nct_id": nct_id,
                "title": title,
                "phase": phase_label,
                "status": status,
                "conditions": conditions[:3],
                "primary_completion": _pc_str,
                "days_until_readout": _days_until,
                "readout_category": _readout_category,
            }
            trials.append(_trial_info)

            # Catalyst-Readout nur wenn zeitlich relevant
            if _readout_category in ("OVERDUE", "OVERDUE_STALE", "IMMINENT", "UPCOMING"):
                catalyst_readouts.append(_trial_info)

        # Handelbare Zukunftstermine zuerst; vergangene Termine nur als Warnkontext.
        catalyst_readouts.sort(key=_biotech_readout_sort_key)

        # Pipeline Score (max 20)
        pipeline_score = 0
        pipeline_score += min(phase_counts["PHASE3"] * 8, 16)
        pipeline_score += min(phase_counts["PHASE2"] * 3, 9)
        pipeline_score += min(phase_counts["PHASE1"] * 1, 3)
        pipeline_score = min(20, pipeline_score)

        # ── NEU: Catalyst-Readout Score (max 15 Bonus) ──
        # Überfällige/bevorstehende Readouts = potentieller Kurssprung
        readout_score = 0
        for _ro in catalyst_readouts:
            _phase = _ro.get("phase", "").replace(" ", "").upper()
            _cat = _ro.get("readout_category", "")

            # Phase-Gewichtung: P3 > P2 > P1
            _phase_mult = 1.0
            if "PHASE3" in _phase:
                _phase_mult = 3.0
            elif "PHASE2" in _phase:
                _phase_mult = 2.0
            elif "PHASE1" in _phase:
                _phase_mult = 1.0
            else:
                _phase_mult = 0.5

            # Timing-Gewichtung: OVERDUE > IMMINENT > UPCOMING
            # OVERDUE_STALE (180-365d): Nur minimaler Score — fragwürdige Daten
            # Past completion dates are stale/unconfirmed metadata, not future catalysts.
            readout_score += _biotech_readout_timing_weight(_cat) * _phase_mult

        readout_score = min(15, int(readout_score))

        # Readout-Label für UI
        _readout_label = ""
        if catalyst_readouts:
            _top = catalyst_readouts[0]
            _d = _top.get("days_until_readout", 0)
            _cat = _top.get("readout_category", "")
            _ph = _top.get("phase", "?")
            if _cat == "OVERDUE":
                _readout_label = f" Readout-Datum abgelaufen ({abs(_d)}d) — unbestätigt — {_ph}"
            elif _cat == "OVERDUE_STALE":
                _readout_label = f" Readout veraltet ({abs(_d)}d) — {_ph}"
            elif _cat == "IMMINENT":
                _readout_label = f" Readout in {_d}d — {_ph}"
            elif _cat == "UPCOMING":
                _readout_label = f" Readout in {_d}d — {_ph}"

        return {
            "pipeline_score": pipeline_score,
            "readout_score": readout_score,
            "readout_label": _readout_label,
            "catalyst_readouts": catalyst_readouts[:5],
            "trials": trials[:10],
            "phase_summary": phase_counts,
            "total_active": len(studies),
        }
    except Exception:
        return {"pipeline_score": 0, "readout_score": 0, "readout_label": "",
                "catalyst_readouts": [], "trials": [], "phase_summary": {}, "total_active": 0}


def _biotech_technical_score(poly_key, ticker):
    """
    Technische Analyse für Biotech: Unusual Volume, Akkumulation, Price Action.
    Returns: dict mit technical_score (max 20), details
    """
    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=90)
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)

        if resp.status_code != 200:
            print(f"[BIOTECH] Technical API failed for {ticker}: HTTP {resp.status_code}")
            return {"technical_score": 0, "details": {}}

        bars = resp.json().get("results", [])
        normalized_bars = []
        for raw_bar in bars if isinstance(bars, list) else []:
            bar = dict(raw_bar)
            try:
                timestamp_ms = float(bar.get("t") or 0)
                if timestamp_ms > 0:
                    import pytz
                    et_zone = pytz.timezone("US/Eastern")
                    bar["date"] = datetime.fromtimestamp(
                        timestamp_ms / 1000.0,
                        tz=dt.timezone.utc,
                    ).astimezone(et_zone).strftime("%Y-%m-%d")
            except (TypeError, ValueError, OverflowError):
                pass
            normalized_bars.append(bar)
        bars = _bi_strip_partial_bar(normalized_bars)
        if not bars or len(bars) < 21:
            return {"technical_score": 0, "details": {}}

        tech_score = 0
        details = {}

        closes = [b["c"] for b in bars]
        volumes = [b["v"] for b in bars]
        highs = [b["h"] for b in bars]
        lows = [b["l"] for b in bars]

        current_price = closes[-1]
        last_vol = volumes[-1]
        avg_vol_20 = historical_volume_baseline(
            volumes[:-1], lookback=20, minimum_periods=10
        )

        # 1. Unusual Volume with Direction Check (max 6 pts) — FIX 2: Volume + Direction
        rvol = last_vol / avg_vol_20 if last_vol > 0 and avg_vol_20 else 0.0
        details["RVOL"] = round(rvol, 2)
        # FIX 1: Track RVOL direction for bonus calculation
        details["rvol_up_day"] = closes[-1] > closes[-2] if len(closes) >= 2 else True
        if rvol >= 3.0:
            # FIX 2: Check direction — high volume on UP day = accumulation, DOWN day = distribution
            if closes[-1] > closes[-2]:  # High volume on UP day = accumulation
                tech_score += 6
                details["vol_signal"] = " Extrem hohes Volumen (Accumulation)"
            else:  # High volume on DOWN day = distribution
                tech_score += 1  # Minimal score, not a buy signal
                details["vol_signal"] = " Extrem hohes Volumen (Distribution) — Vorsicht!"
        elif rvol >= 1.5:
            tech_score += 3  # FIX 2: Moderate volume (was 2 for 1.5-2.0, now unified)
            details["vol_signal"] = " Erhöhtes Volumen"
        elif rvol >= 0.5:
            details["vol_signal"] = " Normal"
        else:
            # V3: RVOL < 0.5x = praktisch kein Volumen → Penalty
            # Wenn niemand handelt, ist der Catalyst eingepreist oder irrelevant
            tech_score -= 3
            details["vol_signal"] = " Sehr niedriges Volumen"

        # 2. Volume Trend — steigendes Volumen = Akkumulation (max 4 pts)
        if len(volumes) >= 20:
            recent_values = [v for v in volumes[-5:] if isinstance(v, (int, float)) and v > 0]
            prior_values = [v for v in volumes[-20:-5] if isinstance(v, (int, float)) and v > 0]
            if len(recent_values) >= 3 and len(prior_values) >= 8:
                vol_recent = sum(recent_values) / len(recent_values)
                vol_prior = sum(prior_values) / len(prior_values)
                vol_trend = vol_recent / vol_prior
                details["vol_trend"] = round(vol_trend, 2)
                if vol_trend >= 2.0:
                    tech_score += 4
                elif vol_trend >= 1.5:
                    tech_score += 3
                elif vol_trend >= 1.2:
                    tech_score += 1
            else:
                details["vol_trend"] = None

        # 3. Price Position — nahe Highs = bullish (max 4 pts)
        high_90d = max(highs)
        low_90d = min(lows)
        range_90d = high_90d - low_90d
        if range_90d > 0:
            pos_90d = (current_price - low_90d) / range_90d * 100
            details["pos_90d"] = round(pos_90d, 1)
            if pos_90d >= 80:
                tech_score += 4
            elif pos_90d >= 60:
                tech_score += 2
            elif pos_90d <= 20:
                tech_score += 0  # Am Boden = könnte Value sein, aber riskant

        # 4. Tight Range (Akkumulation) — niedrige Volatilität = Ruhe vor Sturm (max 3 pts)
        if len(closes) >= 10:
            recent_closes = closes[-10:]
            range_10d = (max(recent_closes) - min(recent_closes)) / max(0.01, min(recent_closes)) * 100
            details["range_10d%"] = round(range_10d, 1)
            if range_10d <= 5:
                tech_score += 3
                details["consolidation"] = " Tight Consolidation"
            elif range_10d <= 10:
                tech_score += 1
                details["consolidation"] = " Moderate Range"
            else:
                details["consolidation"] = " Weit gespreizt"

        # 5. Trend Direction — SMA20 > SMA50 = Aufwärtstrend (max 3 pts)
        # V3: Abwärtstrend bekommt jetzt PENALTY statt 0. Begründung:
        # Long-Grade bei klarem Downtrend (BCRX unter allen EMAs) ist irreführend.
        # Ein Trader der "Grade B LONG" sieht, erwartet einen Aufwärtstrend.
        if len(closes) >= 50:
            sma20 = sum(closes[-20:]) / 20
            sma50 = sum(closes[-50:]) / 50
            if sma20 > sma50:
                tech_score += 3
                details["trend"] = " Aufwärtstrend"
            elif sma20 > sma50 * 0.97:
                tech_score += 1
                details["trend"] = " Seitwärts"
            else:
                tech_score -= 3  # V3: Abwärtstrend bestraft den Score
                details["trend"] = " Abwärtstrend"

        # ── CHART HEALTH (separate Metrik, beeinflusst Score NICHT) ──
        # Gibt dem Trader eine schnelle Einschätzung ob der Chart tradebar ist,
        # ohne gute Catalyst-Trades zu verstecken.
        # Skala: 0-10 (10 = perfekter Chart, 0 = aktiver Crash)
        chart_health = 10  # Start bei "perfekt", dann Abzüge

        # A) Drawdown vom 90d-High
        drawdown_pct = 0
        if range_90d > 0:
            drawdown_pct = (high_90d - current_price) / max(0.01, high_90d) * 100
            details["drawdown%"] = round(drawdown_pct, 1)
            if drawdown_pct >= 30:
                chart_health -= 4
                details["drawdown"] = " −{:.0f}% vom High".format(drawdown_pct)
            elif drawdown_pct >= 20:
                chart_health -= 3
                details["drawdown"] = " −{:.0f}% vom High".format(drawdown_pct)
            elif drawdown_pct >= 15:
                chart_health -= 2
                details["drawdown"] = " −{:.0f}% vom High".format(drawdown_pct)
            elif drawdown_pct <= 5:
                details["drawdown"] = " Nahe Highs"
            else:
                details["drawdown"] = " Normaler Pullback (−{:.0f}%)".format(drawdown_pct)

        # B) Trend — V2.7: Fix startswith("") war immer True (Emoji verloren)
        if "Abwärtstrend" in details.get("trend", ""):
            chart_health -= 2

        # C) Bearish Price Action letzte 5 Tage
        if len(closes) >= 6:
            red_days = sum(1 for i in range(-5, 0) if closes[i] < closes[i-1])
            if red_days >= 4:
                chart_health -= 2
                details["recent_action"] = f" {red_days}/5 rote Tage"
            elif red_days >= 3:
                chart_health -= 1
                details["recent_action"] = f" {red_days}/5 rote Tage"

        # D) Price Position (schon berechnet: pos_90d)
        if details.get("pos_90d", 50) <= 20:
            chart_health -= 1

        chart_health = max(0, min(10, chart_health))
        details["chart_health"] = chart_health

        # Chart Health Label für Tabelle
        if chart_health >= 8:
            details["chart_health_label"] = " Stark"
        elif chart_health >= 6:
            details["chart_health_label"] = " OK"
        elif chart_health >= 4:
            details["chart_health_label"] = " Schwach"
        else:
            details["chart_health_label"] = " Kritisch"

        details["price"] = current_price
        # NACHAUDIT N11: avg_vol_20 kann None sein (Baseline fail-closed) —
        # int(None) wuerde den kompletten Technik-Score still auf 0 werfen.
        details["avg_vol"] = int(avg_vol_20 or 0)
        details["high_90d"] = high_90d
        details["low_90d"] = low_90d

        # V69: Candlestick-Pattern-Erkennung für BioTech
        candle_data = analyze_candles(bars)
        details["candle_analysis"] = candle_data
        details["candle_patterns"] = candle_data.get("patterns", [])
        details["candle_trend"] = candle_data.get("trend", "unknown")
        details["candle_volume_trend"] = candle_data.get("volume_trend", "neutral")
        details["breakout_ready"] = candle_data.get("breakout_ready", False)
        details["support"] = candle_data.get("support", 0)
        details["resistance"] = candle_data.get("resistance", 0)

        # Candlestick-Bonus auf tech_score (max +5 extra)
        _candle_bonus = 0
        _bullish_p = [p for p in candle_data.get("patterns", []) if p.get("type") == "bullish"]
        _bearish_p = [p for p in candle_data.get("patterns", []) if p.get("type") == "bearish"]
        if _bullish_p:
            _candle_bonus += 2  # Bullische Patterns = gut für Catalyst-Play
        if candle_data.get("breakout_ready"):
            _candle_bonus += 2  # Enge Range + steigendes Vol = Breakout imminent
        if candle_data.get("volume_trend") == "accumulation":
            _candle_bonus += 1
        if _bearish_p and not _bullish_p:
            _candle_bonus -= 2  # Nur bearisch = Vorsicht
        tech_score += max(-2, _candle_bonus)

        return {"technical_score": min(20, max(0, tech_score)), "details": details}
    except Exception:
        return {"technical_score": 0, "details": {}}


def _biotech_risk_score(market_cap_m, shares_m, negative_flags, price, catalyst_score=0):
    """
    Opportunity & Risk Score für Biotech (max 15 pts).

    PHILOSOPHIE: Für Catalyst-Trading sind Mid/Small Caps BESSER als Large Caps.
    Ein FDA Approval bewegt ABBV ($400B) vielleicht 2%, aber BCRX ($2B) um 30%+.
    Der Score belohnt den Sweet Spot: groß genug zum sicher Traden,
    klein genug für großes Catalyst-Upside.

    FIX 4: Added catalyst_score parameter for binary event risk assessment.
    """
    risk_score = 0
    risk_details = []

    # Market Cap (max 5 pts) — Sweet Spot: $500M - $10B
    # Zu groß = kaum Bewegung bei Catalyst, zu klein = zu riskant
    if 500 <= market_cap_m <= 10000:
        risk_score += 5
        risk_details.append(" Catalyst Sweet Spot ($0.5-10B)")
    elif 200 <= market_cap_m < 500:
        risk_score += 4
        risk_details.append(" Small Cap — hohes Catalyst-Upside")
    elif 100 <= market_cap_m < 200:
        risk_score += 2
        risk_details.append(" Micro Cap — hohes Risiko, hohes Upside")
    elif 10000 < market_cap_m <= 50000:
        risk_score += 3
        risk_details.append(" Large Cap — solide aber weniger Upside")
    elif market_cap_m > 50000:
        risk_score += 1
        risk_details.append(" Mega Cap — Catalyst bewegt Kurs kaum")
    else:
        risk_score += 0
        risk_details.append(" Nano Cap — sehr hohes Risiko")

    # Price (max 3 pts) — tradeable Range bevorzugen
    if 5 <= price <= 100:
        risk_score += 3
        risk_details.append(" Guter Preis-Range für Trading")
    elif price > 100:
        risk_score += 2
    elif price >= 2:
        risk_score += 1
    else:
        # FIX 2: Penny stock penalty
        risk_score -= 2
        risk_details.append(" Penny Stock (<$2)")

    # Float Size (max 3 pts) — Low Float = explosive Moves bei Catalyst
    if 10 <= shares_m <= 50:
        risk_score += 3
        risk_details.append(" Low Float — explosives Catalyst-Potential")
    elif shares_m < 10 and shares_m > 0:
        risk_score += 2
        risk_details.append(" Micro Float — extrem volatil")
    elif 50 < shares_m <= 200:
        risk_score += 2
        risk_details.append(" Moderate Float")
    elif shares_m > 200:
        risk_score += 1
        risk_details.append(" High Float — weniger explosiv")

    # Sauberkeit: keine negativen Flags = Bonus, viele Flags = Abzug (max 4 pts netto)
    if not negative_flags:
        risk_score += 4
        risk_details.append(" Keine negativen Signale")
    elif len(negative_flags) == 1:
        risk_score += 1
        risk_details.append(f" 1 negatives Signal")
    else:
        # 2+ negative Flags → kein Bonus, zusätzlich Penalty
        penalty = min(4, (len(negative_flags) - 1) * 2)
        risk_score = max(0, risk_score - penalty)
        risk_details.append(f" {len(negative_flags)} negative Signale (−{penalty} Pts)")

    # Borderline Catalyst Risk — NUR wenn nicht schon durch negative Flags bestraft
    if catalyst_score > 0 and catalyst_score < 15 and len(negative_flags) < 2:
        risk_score = max(0, risk_score - 3)
        risk_details.append(" [!] Borderline Catalyst = Risiko")

    return {"risk_score": min(15, risk_score), "risk_details": risk_details}


def _clamp_int(value, low=0, high=100):
    return int(max(low, min(high, round(value))))


def _news_text_blob(news_data):
    parts = []
    for item in news_data.get("news", [])[:10]:
        if not isinstance(item, dict):
            continue
        parts.append(item.get("title", "") or "")
        parts.append(item.get("description", "") or "")
    for flag in news_data.get("negative_flags", [])[:10]:
        if isinstance(flag, dict):
            parts.append(flag.get("flag", "") or "")
    return " ".join(parts).lower()


def _has_any_keyword(text, keywords):
    # N-d (10.06.): Lookaround-Wortgrenzen + optionales Plural-s —
    # "safety concern" matcht jetzt auch "safety concerns" (vorher \b-Miss).
    return any(re.search(r"(?<!\w)" + re.escape(keyword) + r"s?(?!\w)", text) for keyword in keywords)


def _calculate_biotech_catalyst_edge(trial_data, news_data, tech_data, details):
    """
    Trader-facing Bio Catalyst Edge.

    This does not expose the upstream provider. It converts catalyst calendar,
    news risk and chart context into a product-owned risk/edge layer.
    """
    readouts = trial_data.get("catalyst_readouts", []) or []
    tech_details = tech_data.get("details", {}) or {}
    news_blob = _news_text_blob(news_data)
    negative_flags = news_data.get("negative_flags", []) or []
    market_cap_m = details.get("market_cap_millions", 0) or 0
    shares_m = details.get("shares_millions", 0) or 0
    price = tech_details.get("price", 0) or 0

    catalyst_power = 0
    positive_factors = []
    risk_flags = []
    dilution_risk = 0
    regulatory_risk = 0
    sell_news_risk = 0
    halt_risk = 0
    near_binary_event = False  # H-1 (10.06.): Binary-Event in <= 3 Tagen

    if readouts:
        top = readouts[0]
        stage_text = " ".join(str(top.get(k, "") or "") for k in ("stage_label", "event_label", "full_label")).lower()
        days = top.get("days_until")

        if "pdufa" in stage_text or "phase 3" in stage_text or "phase iii" in stage_text:
            catalyst_power += 22
            positive_factors.append("late_stage_or_pdufa")
        elif "phase 2" in stage_text or "phase ii" in stage_text:
            catalyst_power += 16
            positive_factors.append("phase2_readout")
        elif "phase 1" in stage_text or "phase i" in stage_text:
            catalyst_power += 5
            risk_flags.append("early_stage_lower_predictability")

        if days is not None:
            # H-1 (Biotech-Audit 10.06.): T-3 bis T(-1) = unmittelbares
            # Binary-Event (Gap +-40-80%, Stop ueber das Gap wertlos) —
            # eigener Zweig UNABHAENGIG von MCap/halt_risk-Schwelle.
            # days 4-10 bleiben Run-up-Phase (near_term, Modus unveraendert).
            if -1 <= days <= 3:
                near_binary_event = True
                catalyst_power += 4
                halt_risk += 18
                risk_flags.append("near_binary_event")
            elif days < 0:
                catalyst_power -= 8
                risk_flags.append("overdue_catalyst")
            elif days <= 14:
                catalyst_power += 14
                positive_factors.append("near_term_catalyst")
            elif days <= 45:
                catalyst_power += 18
                positive_factors.append("prime_catalyst_window")
            elif days <= 90:
                catalyst_power += 9
                positive_factors.append("watchlist_window")
            else:
                catalyst_power += 2
                risk_flags.append("catalyst_too_far_out")

        # M-3 (Biotech-Audit 10.06.): Der bpiq_score-Anteil (+8/+4 fuer
        # provider_score >= 80/50) ist ENTFERNT — derselbe BPIQ-Readout
        # fliesst bereits via readout_score in den pipeline_score
        # (Doppelzaehlung: pipeline +10 UND Edge +8). Der Readout bleibt
        # NUR im pipeline_score; die Edge bewertet Timing/Risiko des
        # Events und News-Katalysatoren.

        if top.get("is_big_mover") or top.get("is_suspected_mover"):
            catalyst_power += 6
            positive_factors.append("expected_high_move_event")
        if top.get("is_hedge_fund_pick") or top.get("is_high_mgmt_interest"):
            catalyst_power += 5
            positive_factors.append("institutional_interest_marker")
        if top.get("is_hedge_fund_avoid"):
            regulatory_risk += 18
            risk_flags.append("institutional_avoid_marker")
    else:
        if news_data.get("catalyst_score", 0) > 0:
            catalyst_power += 8
            risk_flags.append("news_catalyst_without_calendar_confirmation")
        else:
            risk_flags.append("no_confirmed_catalyst_calendar_event")

    dilution_keywords = {
        "offering", "public offering", "registered direct", "atm", "shelf",
        "s-3", "424b5", "warrant", "convertible", "raise", "priced offering",
        "equity financing", "dilution",
    }
    if _has_any_keyword(news_blob, dilution_keywords):
        dilution_risk += 28
        risk_flags.append("dilution_or_offering_risk")

    regulatory_keywords = {
        "complete response letter", "crl issued", "clinical hold", "partial clinical hold",
        "trial failure", "missed endpoint", "failed to meet", "did not meet",
        "fda rejection", "refuse to file", "safety concern", "adverse events",
        "discontinued", "terminated", "going concern", "delisting",
    }
    if _has_any_keyword(news_blob, regulatory_keywords):
        regulatory_risk += 35
        risk_flags.append("regulatory_or_trial_failure_risk")
    if len(negative_flags) >= 2:
        regulatory_risk += 10
        risk_flags.append("multiple_negative_news_flags")

    # NACHAUDIT: kein `or 50` — pos_90d = 0.0 (Kurs exakt am 90d-Low) ist ein
    # legitimer Messwert und darf nicht zum Neutralwert 50 werden.
    pos_90d = tech_details.get("pos_90d")
    pos_90d = 50 if pos_90d is None else pos_90d
    range_10d = tech_details.get("range_10d%", 0) or 0
    rvol = tech_details.get("RVOL", 0) or 0
    rvol_up_day = tech_details.get("rvol_up_day", True)
    chart_health_raw = tech_details.get("chart_health")
    try:
        chart_health = float(chart_health_raw) if chart_health_raw is not None else 10.0
    except (TypeError, ValueError, OverflowError):
        chart_health = 10.0
    if not math.isfinite(chart_health):
        chart_health = 10.0
    if pos_90d >= 85 and range_10d >= 12:
        sell_news_risk += 18
        risk_flags.append("sell_the_news_risk_extended_chart")
    if rvol >= 3 and not rvol_up_day:
        sell_news_risk += 18
        risk_flags.append("distribution_volume")
    if chart_health <= 4:
        sell_news_risk += 10
        risk_flags.append("weak_chart_before_catalyst")

    if price and price < 2:
        halt_risk += 8
        risk_flags.append("penny_biotech_volatility")
    if market_cap_m and market_cap_m < 100:
        halt_risk += 8
        risk_flags.append("microcap_binary_risk")
    if shares_m and shares_m < 10:
        halt_risk += 6
        risk_flags.append("microfloat_halt_risk")

    risk_penalty = min(60, dilution_risk + regulatory_risk + sell_news_risk + halt_risk)
    edge_score = _clamp_int(50 + catalyst_power - risk_penalty + min(10, tech_data.get("technical_score", 0) or 0))

    if regulatory_risk >= 30 or dilution_risk >= 25:
        trade_mode = "AVOID_NEWS_RISK"
        score_adjustment = -18
    elif near_binary_event:
        # H-1 (10.06.): NEAR_BINARY_EVENT — unabhaengig von MCap und
        # halt_risk-Schwelle. Vorher blieben Mid/Large-Caps bis T-1 in
        # PRIORITY_WATCH (+8); jetzt mindestens -10 zusaetzlich zum
        # halt_risk(+18)-Anteil in der risk_penalty.
        # Vertrag Team C (Mail/api): Mode-String exakt "NEAR_BINARY_EVENT".
        trade_mode = "NEAR_BINARY_EVENT"
        score_adjustment = -10
    elif sell_news_risk >= 25:
        trade_mode = "WAIT_PULLBACK"
        score_adjustment = -10
    elif halt_risk >= 25:
        trade_mode = "SMALL_SIZE_BINARY_RISK"
        score_adjustment = -5
    elif chart_health <= 4:
        # A strong catalyst cannot turn a technically broken chart into an
        # immediate entry. Keep the idea visible, but require fresh structure
        # confirmation before it can become an actionable signal.
        trade_mode = "WAIT_CHART_CONFIRMATION"
        score_adjustment = -6
    elif edge_score >= 75:
        trade_mode = "PRIORITY_WATCH"
        score_adjustment = 8
    elif edge_score >= 65:
        trade_mode = "WATCH_FOR_TRIGGER"
        score_adjustment = 4
    elif edge_score <= 40:
        trade_mode = "LOW_QUALITY"
        score_adjustment = -8
    else:
        trade_mode = "WATCHLIST"
        score_adjustment = 0

    return {
        "bio_edge_score": edge_score,
        "catalyst_power": _clamp_int(catalyst_power, 0, 60),
        "risk_penalty": risk_penalty,
        "score_adjustment": score_adjustment,
        "trade_mode": trade_mode,
        "risk_flags": sorted(set(risk_flags)),
        "positive_factors": sorted(set(positive_factors)),
        "dilution_risk": _clamp_int(dilution_risk, 0, 100),
        "regulatory_risk": _clamp_int(regulatory_risk, 0, 100),
        "sell_the_news_risk": _clamp_int(sell_news_risk, 0, 100),
        "halt_risk": _clamp_int(halt_risk, 0, 100),
        "near_binary_event": near_binary_event,  # H-1 (10.06.): explizit fuer Mail/api (Team C)
    }


def _biotech_news_momentum(news_items):
    """
    Bewertet News-Sentiment und -Momentum (max 15 pts).
    """
    if not news_items:
        return {"momentum_score": 0, "sentiment_summary": "Keine News"}

    pos = sum(1 for n in news_items if n.get("sentiment") == "positive")
    neg = sum(1 for n in news_items if n.get("sentiment") == "negative")
    total = len(news_items)

    score = 0

    # Sentiment Ratio (max 8 pts)
    if total > 0:
        pos_ratio = pos / total
        if pos_ratio >= 0.8:
            score += 8
        elif pos_ratio >= 0.6:
            score += 6
        elif neg / total >= 0.6:
            # V68: Überwiegend negative News = aktiver Penalty statt nur 0
            # NACHAUDIT: Negativ-Check VOR dem 40%-Positiv-Zweig — sonst bekam
            # 2 positive / 3 negative News (+40%/60%) faelschlich +4 statt -4.
            score -= 4  # Warnsignal: 60%+ negativ
        elif pos_ratio >= 0.4:
            score += 4
        else:
            score += 0  # Neutral/gemischt = kein Signal, kein Bonus

    # News Frequency — mehr News = mehr Aufmerksamkeit (max 4 pts)
    if total >= 5:
        score += 4
    elif total >= 3:
        score += 3
    elif total >= 2:
        score += 2
    elif total >= 1:
        score += 1

    # Catalyst in News (max 3 pts)
    cat_count = sum(1 for n in news_items if n.get("catalyst"))
    if cat_count >= 2:
        score += 3
    elif cat_count >= 1:
        score += 2

    sentiment_label = " Positiv" if pos > neg else " Negativ" if neg > pos else " Neutral"

    return {
        "momentum_score": max(0, min(15, score)),  # V68: Floor bei 0 (negative Sentiment konnte Score < 0 erzeugen)
        "sentiment_summary": f"{sentiment_label} ({pos}↑ / {neg}↓ / {total - pos - neg}→)",
        "positive": pos,
        "negative": neg,
        "neutral": total - pos - neg,
    }


def _biotech_background_scan(poly_key):
    """
    Hintergrund-Scan: Findet alle Biotech-Aktien mit FDA-Katalysatoren.
    Läuft als Thread — schreibt Progress in /tmp/.
    """
    try:
        _biotech_clear_stop()  # Altes Stop-Signal löschen
        _biotech_progress_write("running", checked=0, total=0, hits=0, detail="Lade Biotech-Universum...")

        # 1. Biotech Universum laden (oder aus 24h Cache)
        universe = _biotech_universe_cache_load(max_age_hours=24)
        if universe:
            _biotech_progress_write("running", checked=0, total=len(universe), hits=0,
                                    detail=f" {len(universe)} Biotech-Aktien aus Cache, starte Full Scan...")
        else:
            universe = _fetch_biotech_universe(poly_key, min_price=0.50, min_mcap_m=20)
            _biotech_universe_cache_save(universe)

        try:
            catalyst_calendar_tickers = get_premium_catalyst_tickers(window_days=90)
        except Exception as _cat_seed_err:
            print(f"[BIOTECH] Catalyst calendar seed unavailable: {_cat_seed_err}")
            catalyst_calendar_tickers = set()

        if catalyst_calendar_tickers:
            universe_ticker_set = {
                str(u.get("ticker", "")).upper()
                for u in universe
                if isinstance(u, dict) and u.get("ticker")
            }
            for _cat_ticker in sorted(catalyst_calendar_tickers - universe_ticker_set):
                universe.append({
                    "ticker": _cat_ticker,
                    "name": "",
                    "catalyst_calendar_seed": True,
                })

        total = len(universe)
        _biotech_progress_write("running", checked=0, total=total, hits=0,
                                detail=f"{total} Biotech-Aktien gefunden, starte Full Scan...")

        if total == 0:
            raise RuntimeError("Biotech-Universum ist leer")

        results = []
        checked = 0
        analysis_attempts = 0
        analysis_errors = 0

        for stock in universe:
            # Stop-Signal prüfen
            if _biotech_should_stop():
                _biotech_progress_write("stopped", checked=checked, total=total,
                                        hits=len(results), detail=f" Manuell gestoppt bei {checked}/{total}")
                # Bisherige Ergebnisse trotzdem speichern
                if results:
                    results = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:50]
                    _biotech_cache_save(
                        results,
                        partial=True,
                        checked=checked,
                        total=total,
                        detail=f"Manuell gestoppt bei {checked}/{total}",
                    )
                _biotech_clear_stop()
                return

            if not isinstance(stock, dict):
                continue
            ticker = stock.get("ticker", "")
            if not ticker:
                continue
            _in_catalyst_calendar = ticker.upper() in catalyst_calendar_tickers

            # SPAC-Filter: Acquisition Corps etc. aus BioTech-Ergebnissen entfernen
            _stock_name = stock.get("name", "") or ""
            if is_spac(_stock_name):
                continue
            _stock_sic = str(stock.get("sic_code", "") or "")
            if _stock_sic in SPAC_SIC_CODES:
                continue

            checked += 1

            if checked % 5 == 0 and not _biotech_should_stop():
                _biotech_progress_write("running", checked=checked, total=total,
                                        hits=len(results), detail=f"Analysiere {ticker}...")

            analysis_attempts += 1
            try:
                # A) News + Catalyst Scan
                news_data = _scan_biotech_news(poly_key, ticker, limit=5)
                catalyst_score = news_data["catalyst_score"]

                # B) News Momentum
                momentum_data = _biotech_news_momentum(news_data["news"])
                momentum_score = momentum_data["momentum_score"]

                # Quick Filter — QUALITÄT: Mindestens ein echtes Signal nötig
                # Catalyst > 0 = FDA/Pipeline Keyword in News gefunden
                # Momentum >= 6 = starkes positives Sentiment + mehrere News (ohne Catalyst)
                # had_catalyst_keywords = Alte Catalysts gefunden (Time-Decay auf 0) — BPIQ könnte aktuelle haben
                # Alles andere ist Rauschen (normale Biotech-Aktie mit Alltagsnews)
                _had_kw = news_data.get("had_catalyst_keywords", False)
                if catalyst_score == 0 and momentum_score < 6 and not _had_kw and not _in_catalyst_calendar:
                    continue

                # C) Ticker Details (MCap, Shares)
                details = get_ticker_details(poly_key, ticker)

                # C.1) SIC-Code Validierung: Nicht-Biotech rausfiltern
                _detail_sic = str(details.get("sic_code", "") or "")
                if _detail_sic and _detail_sic in SPAC_SIC_CODES:
                    continue  # SPAC/Blank Check — kein Biotech
                if _detail_sic and _detail_sic not in BIOTECH_SIC_CODES and _detail_sic[:2] not in ("28", "38", "87", "80"):
                    # SIC bekannt aber nicht Pharma/Biotech/Medical/R&D — raus
                    # 28xx=Chemicals/Pharma, 38xx=Instruments, 87xx=R&D, 80xx=Health Services
                    _name_lower = (_stock_name or "").lower()
                    if not any(kw in _name_lower for kw in ["pharma", "therapeutics", "bio", "medical", "oncol", "genom"]):
                        continue

                # D) BPIQ Catalyst-Daten (kuratiert, PDUFA-Dates, täglich aktualisiert)
                # ClinicalTrials.gov entfernt — Datenqualität zu schlecht (veraltete Readout-Dates)
                trial_data = {"pipeline_score": 0, "readout_score": 0, "readout_label": "",
                              "catalyst_readouts": [], "trials": [], "phase_summary": {}, "total_active": 0}

                # BPIQ aufrufen wenn: irgendein Catalyst-Keyword in News war (auch wenn
                # Score nach Time-Decay auf 0 fiel), ODER starkes Momentum.
                # BPIQ hat eigene aktuelle Readout-Dates — unabhängig vom News-Alter.
                # H-4 (10.06.): forward_catalyst triggert den BPIQ-Lookup mit —
                # eine angekuendigte Readout-News ist genau der Fall, in dem der
                # Termin-Kalender (Event-Datum) die Wertung uebernehmen muss.
                _had_keywords = news_data.get("had_catalyst_keywords", False) or news_data.get("forward_catalyst", False)
                bpiq_data = {"bpiq_available": False, "readout_score": 0, "readout_label": "", "catalyst_readouts": []}  # Default
                if catalyst_score > 0 or _had_keywords or momentum_score >= 6 or _in_catalyst_calendar:
                    # Nur BPIQ — einzige zuverlässige Catalyst-Quelle
                    bpiq_data = _get_bpiq_catalysts(ticker)
                    if bpiq_data.get("bpiq_available"):
                        trial_data["readout_score"] = bpiq_data["readout_score"]
                        trial_data["readout_label"] = bpiq_data["readout_label"]
                        trial_data["catalyst_readouts"] = bpiq_data["catalyst_readouts"]
                        # Use BPIQ readout as pipeline proxy (0-15 from BPIQ, scale to 0-20)
                        trial_data["pipeline_score"] = min(20, int(bpiq_data["readout_score"] * 20 / 15))

                # E) Technical Score
                tech_data = _biotech_technical_score(poly_key, ticker)

                # F) Risk Score
                risk_data = _biotech_risk_score(
                    market_cap_m=details.get("market_cap_millions", 0),
                    shares_m=details.get("shares_millions", 0),
                    negative_flags=news_data.get("negative_flags", []),
                    price=tech_data.get("details", {}).get("price", 0),
                    catalyst_score=catalyst_score  # FIX 4: Pass catalyst for binary event risk
                )

                # G) Final Score (mit RVOL für Catalyst-Volume Confirmation)
                _rvol_val = tech_data.get("details", {}).get("RVOL", 0)
                # FIX 1: Pass RVOL direction for accurate bonus calculation
                _rvol_up = tech_data.get("details", {}).get("rvol_up_day", True)
                total_score = _calculate_biotech_catalyst_score(
                    catalyst_score=catalyst_score,
                    pipeline_score=trial_data["pipeline_score"],
                    technical_score=tech_data["technical_score"],
                    risk_score=risk_data["risk_score"],
                    news_momentum_score=momentum_score,
                    rvol=_rvol_val,
                    rvol_direction=_rvol_up
                )

                # H) Readout-Bonus: Überfällige/nahende Trial-Readouts boosten den Score
                # V2.7: FIX — readout_score war DOPPELT gezählt (einmal in pipeline_score, einmal hier)
                # Nur noch als Bonus wenn KEIN pipeline_score aus BPIQ vorhanden
                _readout_bonus = trial_data.get("readout_score", 0)
                if trial_data.get("pipeline_score", 0) == 0 and _readout_bonus > 0:
                    total_score = min(100, total_score + _readout_bonus)
                else:
                    total_score = min(100, total_score)

                # ── V4 AUDIT FIX (Biotech-Audit V3): Chart-Health-Penalty ──
                # chart_health wurde bisher NUR als Anzeige-Label genutzt und hatte
                # NULL Einfluss auf total_score. Konsequenz: Biotech-Aktien mit 4/10
                # Chart (aktiver Abverkauf) kamen als Grade B/C durch, nur weil der
                # Catalyst-Score hoch war. Für Breakout-Trading ist ein kaputter Chart
                # aber ein echtes Problem — der Entry wird schlecht, Stop weit weg.
                _tech_details_for_penalty = tech_data.get("details", {})
                _chart_health_val = _tech_details_for_penalty.get("chart_health", 10)
                if _chart_health_val <= 4:
                    total_score = max(0, total_score - 15)  # Kritisch = harter Abzug
                elif _chart_health_val <= 6:
                    total_score = max(0, total_score - 8)   # Schwach = spürbarer Abzug

                # ── V4 AUDIT FIX: Recent-Bearish Hard-Gate für Biotech ──
                # Analog zum BI-Scanner V2: Wenn die letzten 2 Candles bearish sind UND
                # ein ausgeprägter Drawdown läuft (>8% unter 5-Bar-High), ist das kein
                # "pre-catalyst-accumulation"-Setup sondern aktiver Abverkauf — egal wie
                # gut der Catalyst historisch aussah. Nutzt candle_analysis/drawdown
                # die das tech_score bereits berechnet hat.
                _candle_info = _tech_details_for_penalty.get("candle_analysis", {})
                _candle_trend = _tech_details_for_penalty.get("candle_trend", "")
                _bearish_patterns = [
                    p for p in _tech_details_for_penalty.get("candle_patterns", [])
                    if p.get("type") == "bearish"
                ]
                _recent_action = _tech_details_for_penalty.get("recent_action", "")
                # Harte Bearish-Signale: Downtrend + bearish Pattern + 4+/5 rote Tage
                if ("4/5 rote Tage" in _recent_action or "5/5 rote Tage" in _recent_action) and _bearish_patterns:
                    total_score = max(0, total_score - 10)

                # Catalyst Edge Layer: combines catalyst quality with dilution,
                # regulatory, sell-the-news and halt-risk guards.
                _bio_edge = _calculate_biotech_catalyst_edge(
                    trial_data=trial_data,
                    news_data=news_data,
                    tech_data=tech_data,
                    details=details,
                )
                total_score = max(0, min(100, total_score + _bio_edge.get("score_adjustment", 0)))

                # Qualitäts-Gate: Score UND echtes Catalyst-Signal nötig
                # V4 AUDIT: min_required angehoben, weil Grade C vorher = min_required
                # was jede valide Zeile automatisch zu "C" machte. Jetzt Abstand Grade-C
                # zu min_required = 10 Punkte, Grade C braucht zusätzliches Catalyst- oder Tech-Signal.
                # READOUT-OVERRIDE: Wenn ein Readout überfällig/imminent ist, senke den Threshold
                _has_readout = len(trial_data.get("catalyst_readouts", [])) > 0
                if catalyst_score > 0 or _has_readout:
                    min_required = 35  # Mit Catalyst oder Readout: solides Threshold (war: 20)
                else:
                    min_required = 45  # Ohne Catalyst: nur rein wenn Momentum+Technik wirklich stark (war: 35)
                if total_score < min_required:
                    continue

                # Grade — V4 AUDIT: Thresholds enger, Grade C braucht echtes Signal
                # Vorher: C=35 = min_required → jede valide Zeile war automatisch C.
                # Jetzt: C=45+catalyst_signal, B=62+, A=75 (unverändert).
                _tech_score_val = tech_data.get("technical_score", 0)
                _has_tech_signal = _tech_score_val >= 8  # Starkes Tech-Signal (Volume + Trend)
                _has_cat_signal = catalyst_score > 0 or _has_readout
                if total_score >= 75:
                    grade = "A"
                elif total_score >= 62:
                    grade = "B"
                elif total_score >= 45 and (_has_cat_signal or _has_tech_signal):
                    grade = "C"
                else:
                    grade = "D"

                # Best Catalyst Label — mit Datum und Event-Typ
                best_cat = news_data.get("best_catalyst")
                catalyst_label = best_cat["label"] if best_cat else " Pipeline"
                catalyst_headline = best_cat["headline"] if best_cat else ""
                catalyst_date = best_cat["date"] if best_cat else ""
                catalyst_keyword = best_cat["keyword"] if best_cat else ""
                # Ergänze Label mit Datum wenn vorhanden
                if best_cat and best_cat.get("date"):
                    catalyst_label = f"{best_cat['label']} ({best_cat['date']})"

                # Chart Health + Selloff-Reason
                _tech_details = tech_data.get("details", {})
                _chart_health = _tech_details.get("chart_health", 10)
                _chart_label = _tech_details.get("chart_health_label", " Stark")
                _drawdown = _tech_details.get("drawdown%", 0)

                # Selloff-Reason: Warum fällt der Stock?
                # Korreliere Chart-Schwäche mit negativen Catalysts
                _selloff_reason = ""
                _neg_flags = news_data.get("negative_flags", [])
                if _chart_health <= 5:  # Chart ist schwach/kritisch
                    if _neg_flags:
                        _flag_names = [nf["flag"] for nf in _neg_flags[:2]]
                        _selloff_reason = " " + ", ".join(_flag_names)
                    elif _drawdown >= 15:
                        _selloff_reason = " Kein neg. Catalyst — prüfe Chart"
                    # Wenn Chart schwach ABER keine neg. News → könnte Dip-Opportunity sein

                # ── Penny Stock / Micro Cap Warnung ──
                _mcap_m = details.get("market_cap_millions", 0)
                _stock_price = _tech_details.get("price", 0)
                _penny_warning = ""
                if _mcap_m < 50 and _stock_price < 2.0:
                    _penny_warning = " PENNY"
                elif _stock_price < 1.0:
                    _penny_warning = " PENNY"
                elif _mcap_m < 100:
                    _penny_warning = " MICRO"

                # Readout-Label: Wenn vorhanden, ergänze Catalyst-Label
                _readout_lbl = trial_data.get("readout_label", "")
                if _readout_lbl and ("Pipeline" in catalyst_label or not catalyst_label):
                    # V2.7: Fix — startswith("") war immer True → Block war Dead Code
                    # Readout ist das primäre Signal wenn kein stärkerer Catalyst da ist
                    if trial_data.get("readout_score", 0) >= 5:
                        catalyst_label = _readout_lbl
                        _cat_readouts = trial_data.get("catalyst_readouts", [])
                        if _cat_readouts:
                            _rd = _cat_readouts[0]
                            _rd_title = _rd.get("title") or _rd.get("drug_name") or _rd.get("full_label") or "Catalyst"
                            catalyst_headline = f"Trial-Readout erwartet — {_rd_title[:60]}"

                # ── Fallback Readout_Label: Wenn CT.gov leer, nutze besten News-Catalyst ──
                if not _readout_lbl and best_cat:
                    _fb_label = best_cat.get("label", "")
                    _fb_kw = best_cat.get("keyword", "")
                    _fb_date = best_cat.get("date", "")
                    _fb_hl = best_cat.get("headline", "")[:50]
                    if _fb_label:
                        _readout_lbl = f"{_fb_label}" + (f" ({_fb_date})" if _fb_date else "")
                        if _fb_hl:
                            _readout_lbl += f" — {_fb_hl}"

                # ── Event Result Sentiment: Positiv/Negativ/Ausstehend ──
                # V71-FIX: Alte Logik nutzte News-Publikationsdatum als Event-Datum
                # → fast alles "in der Vergangenheit" → fast alles " Unbekannt"
                # Neue Logik: Catalyst-Keyword selbst bestimmt das Ergebnis:
                #   - "fda approved" = Positiv (Ergebnis liegt vor)
                #   - "pdufa" = Ausstehend (Event angekündigt)
                #   - negative_flags = Negativ
                #   - Polygon Sentiment als Tiebreaker
                _event_result = ""
                _all_catalysts = news_data.get("catalysts", [])
                _neg_flags_ev = news_data.get("negative_flags", [])

                # Schritt 1: News-Titel nach expliziten Result-Keywords durchsuchen
                _has_positive_result = False
                _has_negative_result = False
                _positive_result_kws = {
                    "positive results", "primary endpoint met", "statistically significant",
                    "complete remission", "overall survival", "fda approved", "fda approval",
                    "breakthrough therapy", "accelerated approval", "fast track",
                    "pivotal trial success", "topline results positive", "met primary",
                    "exceeded expectations", "superior efficacy", "approval granted",
                    "nda approved", "bla approved", "marketing authorization",
                    "complete response", "durable response", "objective response rate",
                    "favorable safety", "well tolerated", "recommended for approval",
                }
                _negative_result_kws = {
                    "clinical hold", "fda rejection", "complete response letter",
                    "trial failure", "missed endpoint", "failed to meet",
                    "did not meet", "discontinued", "terminated", "negative results",
                    "adverse events", "safety concern", "partial clinical hold",
                    "refuse to file", "not approved", "withdrawal", "halted",
                    "futility", "did not achieve", "failed to demonstrate",
                    "serious adverse", "dose limiting toxicity", "lack of efficacy",
                }
                for _nws in news_data.get("news", [])[:10]:
                    _nws_title = (_nws.get("title", "") or "").lower()
                    _nws_desc = (_nws.get("description", "") or "").lower() if isinstance(_nws, dict) else ""
                    _nws_combined = _nws_title + " " + _nws_desc
                    for _pk in _positive_result_kws:
                        if _pk in _nws_combined:
                            _has_positive_result = True
                            break
                    for _nk in _negative_result_kws:
                        if _nk in _nws_combined:
                            _has_negative_result = True
                            break

                # Auch negative_flags auswerten
                if _neg_flags_ev:
                    _has_negative_result = True

                # Schritt 2: Ergebnis bestimmen — priorisiert
                if _has_negative_result and not _has_positive_result:
                    _event_result = " Negativ"
                elif _has_positive_result and not _has_negative_result:
                    _event_result = " Positiv"
                elif _has_positive_result and _has_negative_result:
                    _event_result = " Gemischt"
                elif best_cat:
                    # Schritt 3: Kein explizites Result-Keyword gefunden
                    # → Nutze den Catalyst-Keyword-Typ um Ergebnis abzuleiten
                    _best_kw = (best_cat.get("keyword", "") or "").lower()

                    # Keywords die ein DEFINITIVES positives Ergebnis anzeigen
                    _definitive_positive_kws = {
                        "fda approved", "fda approval", "fda clearance",
                        "breakthrough therapy", "fast track", "priority review",
                        "accelerated approval", "orphan drug", "emergency use",
                        "eua granted", "positive results", "primary endpoint met",
                        "statistically significant", "overall survival",
                        "progression-free survival", "complete remission",
                        "topline results", "topline data", "late-breaking",
                        "licensing agreement", "partnership", "collaboration",
                        "acquisition target", "buyout", "merger",
                        "label expansion", "expanded access", "compassionate use",
                        "patent granted",
                    }

                    # Keywords die ein BEVORSTEHENDES Event anzeigen (noch kein Ergebnis)
                    _forward_looking_kws = {
                        "pdufa", "nda accepted", "bla accepted", "adcom",
                        "advisory committee", "fda decision", "fda action date",
                        "phase 3 results", "phase 3 data", "phase iii",
                        "pivotal trial", "primary endpoint", "interim analysis",
                        "interim data", "phase 2 results", "phase ii data",
                        "ind filed", "ind accepted", "clinical trial initiation",
                        "patient enrollment", "first patient dosed", "dosing initiated",
                        "preclinical", "phase 1", "phase i", "proof of concept",
                        "patent filed", "ip protection", "data presentation",
                        "conference presentation", "manuscript published", "peer review",
                    }

                    if _best_kw in _definitive_positive_kws:
                        _event_result = " Positiv"
                    elif _best_kw in _forward_looking_kws:
                        # Forward-looking: Polygon-Sentiment als Indikator nutzen
                        _ev_news = news_data.get("news", [])
                        _ev_pos = sum(1 for n in _ev_news if n.get("sentiment") == "positive")
                        _ev_neg = sum(1 for n in _ev_news if n.get("sentiment") == "negative")
                        if _ev_neg > _ev_pos and _ev_neg >= 2:
                            _event_result = " Risiko"
                        else:
                            _event_result = " Ausstehend"
                    else:
                        # Unbekanntes Keyword — Polygon-Sentiment als Fallback
                        _ev_news = news_data.get("news", [])
                        _ev_pos = sum(1 for n in _ev_news if n.get("sentiment") == "positive")
                        _ev_neg = sum(1 for n in _ev_news if n.get("sentiment") == "negative")
                        if _ev_pos > _ev_neg:
                            _event_result = " Positiv"
                        elif _ev_neg > _ev_pos:
                            _event_result = " Risiko"
                        else:
                            _event_result = " Ausstehend"
                elif _all_catalysts:
                    # Hat Catalysts aber kein best_cat (nach Decay alle auf 0)
                    _event_result = " Catalyst"
                else:
                    _event_result = "—"

                # ── V2.6: BPIQ-Daten für Catalyst_Date + Event_Result nutzen ──
                # Wenn BPIQ echte FDA-Dates hat, IMMER diese bevorzugen (statt News-Datum)
                _bpiq_catalyst_date = ""
                _bpiq_event_label = ""
                if bpiq_data.get("bpiq_available") and bpiq_data.get("catalyst_readouts"):
                    _top_bpiq = bpiq_data["catalyst_readouts"][0]
                    _bpiq_cat_date = _top_bpiq.get("catalyst_date_text", "")
                    _bpiq_days = _top_bpiq.get("days_until")
                    _bpiq_category = _top_bpiq.get("category", "")
                    _bpiq_stage = _top_bpiq.get("full_label", "")
                    _bpiq_drug = _top_bpiq.get("drug_name", "")[:25]

                    # Echtes FDA-Datum von BPIQ verwenden
                    if _bpiq_cat_date and _bpiq_cat_date != "TBA":
                        _bpiq_catalyst_date = _bpiq_cat_date
                        # Auch catalyst_date überschreiben (News-Datum ersetzen)
                        catalyst_date = _bpiq_cat_date

                    # Event-Label zusammenbauen
                    if _bpiq_stage:
                        _bpiq_event_label = f"{_bpiq_stage}"
                        if _bpiq_drug:
                            _bpiq_event_label += f" — {_bpiq_drug}"

                    # Event_Result aus BPIQ-Kategorie ableiten (wenn News kein Result hatte)
                    if _event_result in ("—", " Catalyst", ""):
                        if _bpiq_category == "OVERDUE":
                            _event_result = "⏰ Überfällig"
                        elif _bpiq_category == "IMMINENT":
                            _event_result = "⏳ Ausstehend"
                        elif _bpiq_category == "UPCOMING":
                            _event_result = "📅 Geplant"
                        elif _bpiq_category == "LATER":
                            _event_result = "📋 Später"

                    # Catalyst-Label mit BPIQ anreichern wenn besser
                    if _bpiq_event_label and (not catalyst_label or catalyst_label == " Pipeline"):
                        catalyst_label = _bpiq_event_label

                # Datum-Validierung: Ungültige Daten abfangen
                if catalyst_date:
                    try:
                        datetime.strptime(catalyst_date[:10], "%Y-%m-%d")
                    except (ValueError, TypeError):
                        catalyst_date = ""  # Ungültiges Datum entfernen

                result = {
                    "Ticker": ticker,
                    "Name": (details.get("name", "") or stock.get("name", ""))[:30],
                    "Score": total_score,
                    "Grade": grade,
                    "Risk_Flag": _penny_warning,
                    "Catalyst": catalyst_label,
                    "Catalyst_Score": catalyst_score,
                    "Pipeline_Score": trial_data["pipeline_score"],
                    "Readout_Score": trial_data.get("readout_score", 0),
                    "Technical_Score": tech_data["technical_score"],
                    "Risk_Score": risk_data["risk_score"],
                    "Momentum_Score": momentum_score,
                    "Preis": _tech_details.get("price", 0),
                    "MCap_M": details.get("market_cap_millions", 0),
                    "Shares_M": details.get("shares_millions", 0),
                    "RVOL": _tech_details.get("RVOL", 0),
                    "Float_Cat": details.get("float_category", "UNKNOWN"),
                    "Headline": catalyst_headline,
                    "Catalyst_Date": catalyst_date,
                    "Catalyst_Keyword": catalyst_keyword,
                    "Catalysts_All": news_data.get("catalysts", [])[:5],
                    "Readout_Label": _readout_lbl,
                    "Event_Result": _event_result,
                    "Readout_Details": trial_data.get("catalyst_readouts", [])[:3],
                    "BPIQ_Available": bpiq_data.get("bpiq_available", False),
                    "BPIQ_Catalysts": bpiq_data.get("catalyst_readouts", [])[:5],
                    "Phase3": trial_data["phase_summary"].get("PHASE3", 0),
                    "Phase2": trial_data["phase_summary"].get("PHASE2", 0),
                    "Phase1": trial_data["phase_summary"].get("PHASE1", 0),
                    "Active_Trials": trial_data.get("total_active", 0),
                    "Chart": _chart_label,
                    "Chart_Health": _chart_health,
                    "Drawdown": round(_drawdown, 1),
                    "Selloff_Reason": _selloff_reason,
                    "Bio_Edge_Score": _bio_edge.get("bio_edge_score", 0),
                    "Catalyst_Power": _bio_edge.get("catalyst_power", 0),
                    "Bio_Risk_Penalty": _bio_edge.get("risk_penalty", 0),
                    "Bio_Trade_Mode": _bio_edge.get("trade_mode", "WATCHLIST"),
                    "Bio_Risk_Flags": _bio_edge.get("risk_flags", []),
                    "Bio_Positive_Factors": _bio_edge.get("positive_factors", []),
                    "Dilution_Risk": _bio_edge.get("dilution_risk", 0),
                    "Regulatory_Risk": _bio_edge.get("regulatory_risk", 0),
                    "Sell_The_News_Risk": _bio_edge.get("sell_the_news_risk", 0),
                    "Halt_Risk": _bio_edge.get("halt_risk", 0),
                    "Near_Binary_Event": _bio_edge.get("near_binary_event", False),  # H-1 (10.06.)
                    "Forward_Catalyst": news_data.get("forward_catalyst", False),    # H-4 (10.06.)
                    "Trials": trial_data.get("trials", [])[:5],
                    "News": news_data.get("news", [])[:5],
                    "Negative_Flags": _neg_flags,
                    "Risk_Details": risk_data.get("risk_details", []),
                    "Tech_Details": _tech_details,
                    "Sentiment": momentum_data.get("sentiment_summary", ""),
                    # V2.7: Duplicate "Catalysts_All" entfernt (limitiert auf [:5] in Zeile oben)
                }
                results.append(result)

                # V2.2: Live-Zwischenergebnisse
                if len(results) % 3 == 0 or len(results) == 1:
                    _live = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:50]
                    _biotech_cache_save(
                        _live,
                        partial=True,
                        checked=checked,
                        total=total,
                        detail=f"Zwischenstand: {checked}/{total} analysiert",
                    )
                    print(f"[BIOTECH] Live-Update: {len(_live)} Treffer bei {checked}/{total}")

            except Exception as _bio_err:
                analysis_errors += 1
                import traceback
                print(f"[BIOTECH] Fehler bei {ticker}: {_bio_err}\n{traceback.format_exc()}")
                continue

        # Finale Sortierung + Speichern
        _raise_on_systemic_analysis_failures(
            "Biotech Full", analysis_attempts, analysis_errors
        )
        results = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:50]
        _biotech_cache_save(results)

        top_score = results[0]["Score"] if results else 0
        _biotech_progress_write("done", checked=checked, total=total,
                                hits=len(results), top_score=top_score,
                                detail=f"{total} gescannt → {len(results)} mit Katalysator")

    except Exception as e:
        _biotech_progress_write("error", detail=f"Fehler: {str(e)[:150]}")
        raise


def _biotech_universe_cache_save(universe):
    """Speichert Biotech-Universum separat (ändert sich selten)."""
    try:
        with open(_biotech_universe_cache_file(), "w") as f:
            json.dump({"universe": universe, "timestamp": time.time()}, f, default=str)
    except Exception:
        pass


def _biotech_universe_cache_load(max_age_hours=24):
    """Lädt gecachtes Universum (24h gültig — Tickers ändern sich nicht täglich)."""
    try:
        with open(_biotech_universe_cache_file(), "r") as f:
            data = json.load(f)
        if time.time() - data.get("timestamp", 0) > max_age_hours * 3600:
            return None
        return data.get("universe", [])
    except Exception:
        return None


def _biotech_quick_scan(poly_key):
    """
    Quick Scan: Nutzt gecachte Ergebnisse und aktualisiert NUR News/Catalysts.
    Viel schneller als Full Scan weil:
    - Kein Universum-Laden (nutzt bestehende Ticker-Liste aus Cache)
    - Kein extra Pipeline API Call (BPIQ Cache wird im Full Scan geladen)
    - Keine Technical Score Neuberechnung (ändert sich nicht stündlich)
    - NUR: Neue News scannen → Catalyst Score + Momentum aktualisieren
    """
    try:
        # Lade bestehende Ergebnisse
        existing = _biotech_cache_load(max_age_hours=24)  # Alte Daten als Basis
        if not existing:
            # Kein Cache → muss Full Scan machen
            _biotech_progress_write("running", checked=0, total=0, hits=0,
                                    detail="Kein Cache vorhanden — starte Full Scan...")
            _biotech_background_scan(poly_key)
            return

        # Auch Universum laden für neue Tickers die vielleicht noch nicht im Cache sind
        universe_tickers = set()
        universe = _biotech_universe_cache_load(max_age_hours=24)
        if universe:
            universe_tickers = {u["ticker"] for u in universe}

        try:
            catalyst_calendar_tickers = get_premium_catalyst_tickers(window_days=90)
        except Exception as _cat_seed_err:
            print(f"[BIOTECH-QUICK] Catalyst calendar seed unavailable: {_cat_seed_err}")
            catalyst_calendar_tickers = set()

        # Merge: bestehende + ggf. neue Tickers aus Universe
        existing_tickers = {r["Ticker"] for r in existing}
        all_tickers = list(existing_tickers | universe_tickers | catalyst_calendar_tickers)
        total = len(all_tickers)

        _biotech_progress_write("running", checked=0, total=total, hits=0,
                                detail=f" Quick Scan: {total} Tickers, nur News-Update...")

        # Bestehende Ergebnisse als Lookup
        existing_map = {r["Ticker"]: r for r in existing}
        results = []
        checked = 0
        analysis_attempts = 0
        analysis_errors = 0

        _biotech_clear_stop()  # Altes Stop-Signal löschen
        for ticker in all_tickers:
            # Stop-Signal prüfen
            if _biotech_should_stop():
                _biotech_progress_write("stopped", checked=checked, total=total,
                                        hits=len(results), detail=f" Quick Scan gestoppt bei {checked}/{total}")
                if results:
                    results = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:50]
                    _biotech_cache_save(
                        results,
                        partial=True,
                        checked=checked,
                        total=total,
                        detail=f"Quick Scan gestoppt bei {checked}/{total}",
                    )
                _biotech_clear_stop()
                return

            checked += 1
            if checked % 20 == 0:
                _biotech_progress_write("running", checked=checked, total=total,
                                        hits=len(results), detail=f" Quick: {ticker}...")

            analysis_attempts += 1
            try:
                _in_catalyst_calendar = ticker.upper() in catalyst_calendar_tickers
                # NUR News neu scannen
                news_data = _scan_biotech_news(poly_key, ticker, limit=5)
                catalyst_score = news_data["catalyst_score"]
                momentum_data = _biotech_news_momentum(news_data["news"])
                momentum_score = momentum_data["momentum_score"]

                # Quick Filter — gleiche Qualitäts-Logik wie Full Scan
                _had_kw = news_data.get("had_catalyst_keywords", False)
                if catalyst_score == 0 and momentum_score < 6 and not _had_kw and not _in_catalyst_calendar:
                    continue

                bpiq_data = {"bpiq_available": False, "readout_score": 0, "readout_label": "", "catalyst_readouts": []}
                if catalyst_score > 0 or _had_kw or _in_catalyst_calendar:
                    bpiq_data = _get_bpiq_catalysts(ticker)

                # Bestehende Daten wiederverwenden wenn vorhanden
                old = existing_map.get(ticker)

                if old:
                    old = dict(old)  # Copy um Original nicht zu mutieren
                    # Update nur News-bezogene Felder, behalte Rest
                    old["Catalyst_Score"] = catalyst_score
                    old["Momentum_Score"] = momentum_score
                    old["News"] = news_data.get("news", [])[:5]
                    old["Negative_Flags"] = news_data.get("negative_flags", [])
                    old["Sentiment"] = momentum_data.get("sentiment_summary", "")
                    old["Catalysts_All"] = news_data.get("catalysts", [])
                    if bpiq_data.get("bpiq_available"):
                        old["Readout_Score"] = bpiq_data.get("readout_score", 0)
                        old["Readout_Label"] = bpiq_data.get("readout_label", "")
                        old["Readout_Details"] = bpiq_data.get("catalyst_readouts", [])[:3]
                        old["BPIQ_Available"] = True
                        old["BPIQ_Catalysts"] = bpiq_data.get("catalyst_readouts", [])[:5]
                        old["Pipeline_Score"] = min(20, int(bpiq_data.get("readout_score", 0) * 20 / 15))

                    # Best Catalyst aktualisieren
                    best_cat = news_data.get("best_catalyst")
                    old["Catalyst"] = best_cat["label"] if best_cat else old.get("Catalyst", " Pipeline")
                    old["Headline"] = best_cat["headline"] if best_cat else old.get("Headline", "")

                    # Score neu berechnen mit alten Pipeline/Technical/Risk + neuen News
                    old["Score"] = _calculate_biotech_catalyst_score(
                        catalyst_score=catalyst_score,
                        pipeline_score=old.get("Pipeline_Score", 0),
                        technical_score=old.get("Technical_Score", 0),
                        risk_score=old.get("Risk_Score", 0),
                        news_momentum_score=momentum_score,
                        rvol=old.get("RVOL", 0),
                        # NACHAUDIT H7: Ohne diesen Parameter bekam ein
                        # Distribution-Tag (Down-Day) beim 2h-News-Refresh
                        # wieder den vollen RVOL-Bonus (bis +8, Grade-Flip).
                        rvol_direction=(old.get("Tech_Details") or {}).get("rvol_up_day", True),
                    )

                    # Gleiche Qualitätslogik wie Full Scan: Chart-Health und schwache Technik
                    # dürfen einen News-Refresh nicht wieder künstlich hochstufen.
                    _chart_health_val = old.get("Chart_Health", old.get("Tech_Details", {}).get("chart_health", 10))
                    if _chart_health_val <= 4:
                        old["Score"] = max(0, old["Score"] - 15)
                    elif _chart_health_val <= 6:
                        old["Score"] = max(0, old["Score"] - 8)

                    _bio_edge = _calculate_biotech_catalyst_edge(
                        trial_data={
                            "pipeline_score": old.get("Pipeline_Score", 0),
                            "readout_score": old.get("Readout_Score", 0),
                            "catalyst_readouts": old.get("Readout_Details") or old.get("BPIQ_Catalysts") or [],
                        },
                        news_data=news_data,
                        tech_data={
                            "technical_score": old.get("Technical_Score", 0),
                            "details": old.get("Tech_Details", {}),
                        },
                        details={
                            "market_cap_millions": old.get("MCap_M", 0),
                            "shares_millions": old.get("Shares_M", 0),
                        },
                    )
                    old["Score"] = max(0, min(100, old["Score"] + _bio_edge.get("score_adjustment", 0)))
                    old["Bio_Edge_Score"] = _bio_edge.get("bio_edge_score", 0)
                    old["Catalyst_Power"] = _bio_edge.get("catalyst_power", 0)
                    old["Bio_Risk_Penalty"] = _bio_edge.get("risk_penalty", 0)
                    old["Bio_Trade_Mode"] = _bio_edge.get("trade_mode", "WATCHLIST")
                    old["Bio_Risk_Flags"] = _bio_edge.get("risk_flags", [])
                    old["Bio_Positive_Factors"] = _bio_edge.get("positive_factors", [])
                    old["Dilution_Risk"] = _bio_edge.get("dilution_risk", 0)
                    old["Regulatory_Risk"] = _bio_edge.get("regulatory_risk", 0)
                    old["Sell_The_News_Risk"] = _bio_edge.get("sell_the_news_risk", 0)
                    old["Halt_Risk"] = _bio_edge.get("halt_risk", 0)
                    old["Near_Binary_Event"] = _bio_edge.get("near_binary_event", False)  # H-1 (10.06.)
                    old["Forward_Catalyst"] = news_data.get("forward_catalyst", False)    # H-4 (10.06.)

                    # Grade aktualisieren — synchron zu Full Scan / Biotech Audit V3
                    s = old["Score"]
                    _has_readout = bool(old.get("Readout_Details") or old.get("BPIQ_Catalysts"))
                    _has_cat_signal = catalyst_score > 0 or _has_readout
                    _has_tech_signal = old.get("Technical_Score", 0) >= 8
                    if s >= 75:
                        old["Grade"] = "A"
                    elif s >= 62:
                        old["Grade"] = "B"
                    elif s >= 45 and (_has_cat_signal or _has_tech_signal):
                        old["Grade"] = "C"
                    else:
                        old["Grade"] = "D"

                    # Qualitäts-Gate — synchron zu Full Scan
                    _min_req = 35 if _has_cat_signal else 45
                    if old["Score"] >= _min_req:
                        results.append(old)
                else:
                    # Neuer Ticker — minimal-Eintrag (wird beim nächsten Full Scan vervollständigt)
                    if catalyst_score >= 15 and momentum_score >= 6 or bpiq_data.get("bpiq_available"):
                        _readout_score = bpiq_data.get("readout_score", 0)
                        _pipeline_score = min(20, int(_readout_score * 20 / 15))
                        _score = _calculate_biotech_catalyst_score(
                            catalyst_score=catalyst_score,
                            pipeline_score=_pipeline_score,
                            technical_score=0,
                            risk_score=5,
                            news_momentum_score=momentum_score,
                            rvol=0,
                        )
                        _score = max(_score, catalyst_score + momentum_score + _readout_score)
                        _grade = "B" if _score >= 62 else "C" if _score >= 45 else "D"
                        if _score < 35:
                            continue
                        results.append({
                            "Ticker": ticker, "Name": "", "Score": _score,
                            "Grade": _grade, "Risk_Flag": "",
                            "Catalyst": bpiq_data.get("readout_label") or news_data.get("best_catalyst", {}).get("label", " Catalyst"),
                            "Catalyst_Score": catalyst_score, "Pipeline_Score": _pipeline_score,
                            "Readout_Score": _readout_score,
                            "Readout_Label": bpiq_data.get("readout_label", ""),
                            "Readout_Details": bpiq_data.get("catalyst_readouts", [])[:3],
                            "Technical_Score": 0, "Risk_Score": 5, "Momentum_Score": momentum_score,
                            "Preis": 0, "MCap_M": 0, "Shares_M": 0, "RVOL": 0, "Float_Cat": "UNKNOWN",
                            "Headline": news_data.get("best_catalyst", {}).get("headline", ""),
                            "Phase3": 0, "Phase2": 0, "Phase1": 0, "Active_Trials": 0,
                            "Chart": " Neu", "Chart_Health": 5, "Drawdown": 0, "Selloff_Reason": "",
                            "Trials": [], "News": news_data.get("news", [])[:5],
                            "Negative_Flags": news_data.get("negative_flags", []),
                            "Risk_Details": [], "Tech_Details": {},
                            "Sentiment": momentum_data.get("sentiment_summary", ""),
                            "Catalysts_All": news_data.get("catalysts", []),
                            "BPIQ_Available": bpiq_data.get("bpiq_available", False),
                            "BPIQ_Catalysts": bpiq_data.get("catalyst_readouts", [])[:5],
                        })
            except Exception as _bio_q_err:
                analysis_errors += 1
                print(f"[BIOTECH-QUICK] Fehler bei {ticker}: {_bio_q_err}")
                continue

        _raise_on_systemic_analysis_failures(
            "Biotech Quick", analysis_attempts, analysis_errors
        )
        results = sorted(results, key=lambda x: x.get("Score", 0), reverse=True)[:50]
        _biotech_cache_save(results)

        top_score = results[0]["Score"] if results else 0
        _biotech_progress_write("done", checked=checked, total=total,
                                hits=len(results), top_score=top_score,
                                detail=f" Quick Scan: {total} geprüft → {len(results)} Treffer")

    except Exception as e:
        _biotech_progress_write("error", detail=f"Quick Scan Fehler: {str(e)[:150]}")
        raise


def _compute_biotech_technical_from_bars(bars):
    """
    Berechnet den BioTech Technical Score aus einem Bar-Fenster (offline, kein API-Call).

    NACHAUDIT: Diese Backtest-Variante ist NICHT identisch mit dem Live-Score
    _biotech_technical_score(): RVOL-Punkte ohne Up/Down-Richtungscheck, keine
    -3-Penalties (RVOL<0.5, Downtrend), kein Candle-Bonus. Backtest-Ergebnisse
    auf Distribution-/Low-Volume-Tagen fallen dadurch bis ~6 Punkte zu gut aus —
    Kalibrierungen gegen Live-Schwellen entsprechend konservativ interpretieren
    (oder beide Pfade auf eine gemeinsame Bars-Funktion zusammenfuehren).

    Returns: dict mit technical_score (max 20), rvol, details
    """
    if not bars or len(bars) < 20:
        return {"technical_score": 0, "rvol": 0, "details": {}}

    closes = [b["close"] for b in bars]
    volumes = [b["volume"] for b in bars]
    highs = [b["high"] for b in bars]
    lows = [b["low"] for b in bars]

    current_price = closes[-1]
    last_vol = volumes[-1]
    avg_vol_20 = historical_volume_baseline(
        volumes[:-1], lookback=20, minimum_periods=10
    )

    tech_score = 0
    details = {}

    # 1. Unusual Volume (max 6 pts)
    rvol = last_vol / avg_vol_20 if last_vol > 0 and avg_vol_20 else 0.0
    details["RVOL"] = round(rvol, 2)
    if rvol >= 3.0:
        tech_score += 6
    elif rvol >= 2.0:
        tech_score += 4
    elif rvol >= 1.5:
        tech_score += 2

    # 2. Volume Trend — steigendes Volumen = Akkumulation (max 4 pts)
    if len(volumes) >= 20:
        recent_values = [v for v in volumes[-5:] if isinstance(v, (int, float)) and v > 0]
        prior_values = [v for v in volumes[-20:-5] if isinstance(v, (int, float)) and v > 0]
        if len(recent_values) >= 3 and len(prior_values) >= 8:
            vol_recent = sum(recent_values) / len(recent_values)
            vol_prior = sum(prior_values) / len(prior_values)
            vol_trend = vol_recent / vol_prior
            details["vol_trend"] = round(vol_trend, 2)
            if vol_trend >= 2.0:
                tech_score += 4
            elif vol_trend >= 1.5:
                tech_score += 3
            elif vol_trend >= 1.2:
                tech_score += 1
        else:
            details["vol_trend"] = None

    # 3. Price Position — nahe Highs = bullish (max 4 pts)
    high_90d = max(highs[-min(90, len(highs)):])
    low_90d = min(lows[-min(90, len(lows)):])
    range_90d = high_90d - low_90d
    if range_90d > 0:
        pos_90d = (current_price - low_90d) / range_90d * 100
        details["pos_90d"] = round(pos_90d, 1)
        if pos_90d >= 80:
            tech_score += 4
        elif pos_90d >= 60:
            tech_score += 2

    # 4. Tight Range — niedrige Volatilität = Ruhe vor Sturm (max 3 pts)
    if len(closes) >= 10:
        recent_closes = closes[-10:]
        range_10d = (max(recent_closes) - min(recent_closes)) / max(0.01, min(recent_closes)) * 100
        details["range_10d%"] = round(range_10d, 1)
        if range_10d <= 5:
            tech_score += 3
        elif range_10d <= 10:
            tech_score += 1

    # 5. Trend Direction — SMA20 > SMA50 = Aufwärtstrend (max 3 pts)
    if len(closes) >= 50:
        sma20 = sum(closes[-20:]) / 20
        sma50 = sum(closes[-50:]) / 50
        if sma20 > sma50:
            tech_score += 3
        elif sma20 > sma50 * 0.97:
            tech_score += 1

    return {"technical_score": min(20, tech_score), "rvol": round(rvol, 2), "details": details}


