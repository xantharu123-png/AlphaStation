#!/usr/bin/env python3
"""
🔄 TradingBot Background Scanner Service V2
=============================================
Läuft als Systemd Service und führt alle Scanner automatisch aus.
Ergebnisse werden als JSON-Dateien gespeichert, Streamlit liest sie.

Usage:
    python bg_service.py start      # Startet den Service
    python bg_service.py stop       # Stoppt den Service
    python bg_service.py status     # Zeigt Status
    python bg_service.py once       # Einmal alle Scanner laufen lassen

Zeitplan:
    - Crash Monitor:    alle 30 Min
    - BTC Divergenz:    alle 30 Min
    - BI Scanner Long:  alle 2h (versetzt)
    - Bear Scanner:     alle 2h (versetzt um 1h zu BI)
    - Biotech Scanner:  alle 1h
    - New Listing Dump: alle 15 Min (Crypto 24/7)
"""

import os
import sys
import json
import time
import signal
import logging
import threading
import traceback
import smtplib
import re
import html
import atexit
import tempfile
import math
import glob
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta, timezone
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    import fcntl
except ImportError:
    class _FcntlFallback:
        LOCK_EX = 0
        LOCK_UN = 0

        @staticmethod
        def flock(*_args, **_kwargs):
            return None

    fcntl = _FcntlFallback()

# ── Pfade ──
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data_cache"
DATA_DIR.mkdir(exist_ok=True)
PID_FILE = DATA_DIR / "bg_service.pid"
LOG_FILE = DATA_DIR / "bg_service.log"
STATUS_FILE = DATA_DIR / "bg_status.json"

# Modules importieren
sys.path.insert(0, str(BASE_DIR))

from modules.trade_levels import normalize_alert_trade_levels, trade_plan_quality
from modules.trade_health import calculate_trade_health  # Q3/B4: zentrales Health-Gate wie api
from modules.email_dedupe import (
    email_dedupe_active as _shared_email_dedupe_active,
    email_dedupe_claim as _shared_email_dedupe_claim,
    email_dedupe_mark as _shared_email_dedupe_mark,
    email_dedupe_release as _shared_email_dedupe_release,
    email_dedupe_remaining as _shared_email_dedupe_remaining,
    load_email_dedupe as _shared_load_email_dedupe,
    save_email_dedupe as _shared_save_email_dedupe,
)
try:
    from modules.auth import get_email_alert_recipients
    HAS_AUTH_ALERT_RECIPIENTS = True
except Exception as _auth_alert_err:
    HAS_AUTH_ALERT_RECIPIENTS = False
    get_email_alert_recipients = None
# Signal-Tracking (Team-A-Kontrakt) — defensiv: bg muss auch ohne Modul laufen.
try:
    from modules.signal_tracker import (
        record_alert_signals,
        evaluate_open_signals,
        load_performance_summary,
    )
except Exception as _tracker_import_err:  # ImportError + Folgefehler beim Parallel-Rollout
    record_alert_signals = None
    evaluate_open_signals = None
    load_performance_summary = None
# Telegram-Benachrichtigung (Team-A-Kontrakt) — optional, gleiche Defensive.
try:
    from modules.notify_telegram import (
        is_telegram_configured,
        send_telegram_alert,
        format_alert_rows_for_telegram,
    )
except Exception as _telegram_import_err:
    is_telegram_configured = None
    send_telegram_alert = None
    format_alert_rows_for_telegram = None

# ── Logging ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger("bg_service")

# ── B-01: Atomic write helper for JSON (prevents corruption) ──
def _atomic_write_json(filepath, data):
    """Atomic JSON write - prevents corruption from concurrent reads."""
    tmp_dir = os.path.dirname(filepath) or "."
    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', dir=tmp_dir, delete=False, suffix='.tmp') as f:
            tmp_path = f.name
            json.dump(data, f)
        os.replace(tmp_path, filepath)
        tmp_path = None
    except Exception as e:
        log.warning(f"Atomic write failed for {filepath}: {e}")
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise

# ── B-06: PID cleanup on exit ──
atexit.register(lambda: PID_FILE.unlink(missing_ok=True))

# ── B-09: Cache cleanup helper ──
def _cleanup_old_cache():
    """Remove progress files older than 24h."""
    for pattern in ["/tmp/*_progress_*.json", "/tmp/*_scan_*.json", "/tmp/*_cache_*.json"]:
        for f in glob.glob(pattern):
            try:
                if time.time() - os.path.getmtime(f) > 86400:
                    os.unlink(f)
                    log.info(f"Cleaned up old cache: {f}")
            except:
                pass

# ── Cache bei Scan-Start löschen (frische Ergebnisse) ──
_SCAN_CACHE_MAP = {
    "bi_long": "/tmp/bi_cache_long.json",
    "bi_short": "/tmp/bi_cache_short.json",
    "bear_scan": "/tmp/bear_scanner_cache.json",
    "biotech": "/tmp/alpha_biotech_cache.json",
    "strategies": "/tmp/strategy_scan_cache.json",
    "orb": "/tmp/orb_scan_results.json",
}

def _clear_scan_cache(scanner_name):
    """Preserve the last successful snapshot while a replacement is built.

    Removing a cache at scan start made every long-running or failed scan look
    like an empty scanner in the UI. Writers replace snapshots atomically (or
    are verified after returning), so readers can safely keep using the last
    successful snapshot until a fresh one is published.
    """
    cache_file = _SCAN_CACHE_MAP.get(scanner_name)
    if cache_file and os.path.exists(cache_file):
        log.debug(f"Bestehender Cache bleibt bis zum erfolgreichen Scan erhalten: {cache_file}")


def _scanner_cache_snapshot(cache_file):
    """Return (revision, payload) for a readable scanner cache."""
    if not cache_file or not os.path.exists(cache_file):
        return None, None
    try:
        stat = os.stat(cache_file)
        with open(cache_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
            return None, None
        return (stat.st_mtime_ns, stat.st_size), payload
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def _scanner_progress_payload(progress_file):
    if not progress_file or not os.path.exists(progress_file):
        return None
    try:
        with open(progress_file, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _require_final_scanner_publish(
    scanner_name,
    cache_file,
    previous_revision,
    progress_file,
    started_at,
    *,
    direction=None,
):
    """Fail unless a scan published a new final cache and a done progress state."""
    revision, payload = _scanner_cache_snapshot(cache_file)
    if revision is None:
        raise RuntimeError(f"{scanner_name}: kein lesbarer Ergebnis-Cache publiziert")
    if revision == previous_revision:
        raise RuntimeError(f"{scanner_name}: Ergebnis-Cache wurde nicht aktualisiert")
    cache_ts = float(payload.get("timestamp", 0) or 0)
    if cache_ts < started_at - 1:
        raise RuntimeError(f"{scanner_name}: publizierter Cache ist nicht frisch")
    if payload.get("partial") is True:
        raise RuntimeError(f"{scanner_name}: nur partieller Cache publiziert")
    if direction and str(payload.get("direction", "")).lower() != str(direction).lower():
        raise RuntimeError(f"{scanner_name}: Cache-Richtung stimmt nicht")

    progress = _scanner_progress_payload(progress_file)
    progress_status = str((progress or {}).get("status", "")).lower()
    progress_ts = float((progress or {}).get("timestamp", 0) or 0)
    if progress_status != "done" or progress_ts < started_at - 1:
        raise RuntimeError(
            f"{scanner_name}: Scan nicht final abgeschlossen (Status {progress_status or 'fehlt'})"
        )
    return payload

# ── API Keys aus secrets.toml laden ──
_EMAIL_CONFIG_KEYS = (
    "GMAIL_USER",
    "GMAIL_APP_PASSWORD",
    "ALERT_EMAIL",
    "ALERT_SEND_TO_SUBSCRIBERS",
    "SMTP_HOST",
    "SMTP_PORT",
    "SMTP_SSL_PORT",
)


def _parse_kv_file(path: Path) -> dict:
    values = {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, val = line.split("=", 1)
                values[key.strip()] = val.strip().strip('"').strip("'")
    except Exception as exc:
        log.warning(f"Config konnte nicht gelesen werden ({path}): {exc}")
    return values


def _load_secrets():
    """Load config from secrets files, .env and process env; partial files do not shadow Gmail config."""
    secrets = {}
    paths = [
        Path.home() / ".streamlit" / "secrets.toml",
        BASE_DIR / ".streamlit" / "secrets.toml",
        BASE_DIR / ".env",
    ]
    for secrets_path in paths:
        if secrets_path.exists():
            secrets.update(_parse_kv_file(secrets_path))
    for key in (
        "POLYGON_KEY",
        "BPIQ_API_KEY",
        "ANTHROPIC_API_KEY",
        "FINNHUB_KEY",
        *_EMAIL_CONFIG_KEYS,
    ):
        if os.environ.get(key):
            secrets[key] = os.environ[key]
    return secrets


# ── E-Mail Alert System ──
_EMAIL_COOLDOWN = {}  # Verhindert Spam: {ticker: last_sent_ts}
_EMAIL_COOLDOWN_SEC = 3600 * 8  # 8 Stunden Cooldown pro Ticker, wie im API-Mailpfad
_EMAIL_DEDUPE_FILE = "/tmp/alphastation_email_dedupe.json"
_CRASH_ALERT_DEDUPE_SEC = 36 * 3600
_ALERT_MIN_SCORE = 80
_NLS_MIN_ALERT_RR = 1.5
_BEARISH_STOCK_ALERT_DEDUPE_SEC = 8 * 3600
_LONG_ENTRY_ALERT_SCANNERS = {"bi_long", "biotech", "strategies", "stock_strategy", "strategy_scan"}
# Q3/B4: RVOL-Floor wie api (0.7) fuer die bg-Mail-Scanner. bi/biotech sind
# Pre-Breakout-Scanner; der 1.5er-Breakout-Floor (AUDIT S-1) gilt nur fuer
# Breakout-/Momentum-Strategien im api-Pfad und betrifft KEINEN bg-Scanner.
_ALERT_MIN_RVOL = 0.7
_BG_ALERT_RVOL_GUARD_SCANNERS = {"bi_long", "bi_short", "biotech"}
_ALERT_MIN_HEALTH_SCORE = 80
# N (Audit 10.06.2026): Frische-Gate fuer bg-Mails — Cache aelter als 2h ist
# kein "JETZT-Trade"-Signal mehr (Schutz gegen tote Scan-Owner, vgl. K-1).
_ALERT_CACHE_MAX_AGE_S = 2 * 3600
# B2: Dedupe-TTLs wie api — Biotech-Katalysator-Setups bleiben tagelang gleich.
_BIOTECH_ALERT_DEDUPE_SEC = 72 * 3600
# B5: Einmalige Invalidierungs-Update-Mail je Symbol (72h-Fenster).
_NLS_INVALIDATION_DEDUPE_SEC = 72 * 3600
# Exit-Update-Mails (Signal-Tracker): einmalig je Transition. 7-Tage-Fenster =
# grosszuegig — deckt das komplette Signal-Leben ab (Aktien max. 5 Daily-Bars,
# Crypto 120h) und entspricht der max. Haltedauer der Dedupe-Datei selbst
# (_load_email_dedupe prunt Eintraege > 7 Tage).
_SIGNAL_UPDATE_DEDUPE_SEC = 7 * 86400
# Startup-Delay wie api (_EMAIL_STARTUP_DELAY): nach Prozess-Restart 5 Min keine
# Mails — alte Cache-Daten erzeugen sonst Phantom-Alerts/Restart-Spam.
_BG_STARTED_AT = time.time()
_BG_STARTUP_MAIL_DELAY = 300
# B6: Mail-Klassen-Praefixe (mit api-Team abgestimmt, identische Konvention).
_MAIL_CLASS_PREFIXES = {"trade": "🚨 JETZT: ", "watch": "👁️ WATCH: ", "info": "ℹ️ "}
_NON_STOCK_PRODUCT_TICKERS = {
    "IREX", "IREZ", "APLZ", "LCIZ", "NBIZ", "MSTX", "MSTU", "MSTZ", "TSLL", "TSLQ",
    "NVDL", "NVDQ", "NVDU", "NVDD", "CONL", "GGLL", "GGLS", "AAPU", "AAPD",
    "AMZU", "AMZD", "METU", "METD", "SOXL", "SOXS", "TQQQ", "SQQQ", "UPRO",
    "SPXU", "SPXL", "SPXS", "LABU", "LABD", "TECL", "TECS", "FNGU", "FNGD",
    "BOIL", "KOLD", "GUSH", "DRIP", "NUGT", "DUST", "JNUG", "JDST", "YINN",
    "YANG", "UVXY", "VIXY", "VXX", "BITO", "BITI",
}
_NON_STOCK_PRODUCT_KEYWORDS = {
    "ETF", "ETN", "ETP", "FUND", "2X", "3X", "LEVERAGED", "INVERSE",
    "ULTRA", "ULTRAPRO", "BULL", "BEAR", "DAILY TARGET", "TRADR", "T-REX",
    "DIREXION", "PROSHARES", "GRANITESHARES", "YIELDMAX", "ROUNDHILL", "DEFIANCE",
    "REX SHARES", "MICROSECTORS", "VOLATILITY SHARES", "WARRANT", "RIGHT", "UNIT",
}
_STOCK_REFERENCE_TYPES = {"CS", "ADRC", "ADRP"}
_COMMON_STOCK_UNIVERSE_CACHE = "/tmp/polygon_common_stock_universe.json"
_EMAIL_BLOCKED_ETF_TICKERS = set(_NON_STOCK_PRODUCT_TICKERS) | {
    "SOXS", "SQQQ", "SPXU", "SPXS", "UVXY", "VIXY", "QID", "SRTY", "TZA", "SDOW", "LABD",
    "SDS", "SH", "PSQ", "DOG", "RWM", "SOXL", "TQQQ", "UPRO", "SPXL", "UDOW", "FNGU",
    "KOLD", "BOIL", "DRIP", "GUSH", "JDST", "JNUG", "NUGT", "DUST", "YANG", "YINN",
    "SVXY", "VXX", "TVIX", "BITI", "BITO", "LABU",
}


def _safe_float(value, default=0.0):
    try:
        val = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(val) or math.isinf(val):
        return default
    return val


def _format_alert_price(value):
    price = _safe_float(value, None)
    if price is None:
        return "-"
    if abs(price) >= 100:
        return f"${price:,.2f}"
    if abs(price) >= 1:
        return f"${price:,.4f}".rstrip("0").rstrip(".")
    return f"${price:.8f}".rstrip("0").rstrip(".")


def _first_trade_level(row, keys):
    sources = [row]
    for nested_key in ("trade_setup", "setup", "signal"):
        nested = row.get(nested_key)
        if isinstance(nested, dict):
            sources.append(nested)
            pump = nested.get("pump_data")
            if isinstance(pump, dict):
                sources.append(pump)
    for source in sources:
        for key in keys:
            value = _safe_float(source.get(key), None)
            if value is not None and value > 0:
                return value
    return None


def _infer_alert_direction(row):
    setup = row.get("trade_setup", {}) if isinstance(row.get("trade_setup"), dict) else {}
    text = " ".join(str(value or "") for value in (
        row.get("Signal_Direction"),
        row.get("BI_Direction"),
        row.get("direction"),
        row.get("_direction"),
        row.get("side"),
        row.get("trade_action"),
        setup.get("direction"),
        setup.get("trade_action"),
    )).upper()
    if "SHORT" in text or text == "SELL":
        return "SHORT"
    if "LONG" in text or "BUY" in text:
        return "LONG"
    return ""


def _extract_alert_price(row):
    for key in ("Preis", "Price", "price", "current", "current_price", "entry"):
        value = row.get(key)
        if value not in (None, ""):
            return value
    return 0


def _alert_trade_levels(row):
    return normalize_alert_trade_levels(
        row,
        price_fallback=_extract_alert_price(row),
        allow_estimated=True,
    )


def _humanize_alert_level_source(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    text = raw.replace("_", " ").replace("-", " ")
    normalized = text.lower()
    for src, dst in {
        "vrvp": "VRVP",
        "poc": "POC",
        "vah": "VAH",
        "val": "VAL",
        "hvn": "HVN",
        "lvn": "LVN",
        "atr": "ATR",
        "vwap": "VWAP",
        "ema": "EMA",
        "ma20": "MA20",
        "ma50": "MA50",
        "tp1": "TP1",
        "tp2": "TP2",
    }.items():
        text = re.sub(rf"\b{re.escape(src)}\b", dst, text, flags=re.IGNORECASE)
    if "measured move" in normalized:
        text = "Measured Move"
    elif "fallback" in normalized and "measured" not in normalized:
        text = text.replace("fallback", "Fallback")
    elif "range" in normalized:
        text = text.replace("range", "Range")
    elif "invalidation" in normalized:
        text = text.replace("invalidation", "Invalidation")
    return text[:90]


def _alert_level_source_line(row):
    setup = row.get("trade_setup") if isinstance(row.get("trade_setup"), dict) else {}
    stop_source = setup.get("stop_source") or row.get("stop_source")
    tp1_source = setup.get("tp1_source") or row.get("tp1_source")
    tp2_source = setup.get("tp2_source") or row.get("tp2_source")
    parts = []
    if stop_source:
        parts.append(f"Stop: {_humanize_alert_level_source(stop_source)}")
    if tp1_source:
        parts.append(f"TP1: {_humanize_alert_level_source(tp1_source)}")
    if tp2_source:
        parts.append(f"TP2: {_humanize_alert_level_source(tp2_source)}")
    if not parts:
        return ""
    safe = html.escape(" | ".join(parts))
    return f'<br><span style="color:#64748b;font-size:11px">Level-Quelle: {safe}</span>'


def _biotech_binary_event_days(row):
    """H-2 (Audit 10.06.2026): Tage bis zum naechsten binaeren Event oder None.

    Quellen: explizite days-Felder der Row, dann die Readout-Listen
    (Readout_Details[*].days_until_readout, BPIQ_Catalysts[*].days_until).
    Negative Werte (overdue) zaehlen nicht — Event vorbei, kein Gap-Risiko.
    """
    if not isinstance(row, dict):
        return None
    for key in ("days_until", "Days_Until", "Catalyst_Days", "catalyst_days", "days_until_readout"):
        val = _safe_float(row.get(key), None)
        if val is not None:
            return val
    best = None
    for list_key in ("Readout_Details", "readout_details", "BPIQ_Catalysts", "bpiq_catalysts"):
        items = row.get(list_key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            for k in ("days_until_readout", "days_until"):
                val = _safe_float(item.get(k), None)
                if val is not None and val >= 0 and (best is None or val < best):
                    best = val
    return best


def _biotech_near_binary_event(row):
    """H-2 (Audit 10.06.2026): True wenn die Row vor einem binaeren Event steht.

    Binaer = PDUFA/Readout <= T-3: Gap-Risiko ±40-80%, ein Stop schuetzt NICHT
    ueber ein Gap. Erkennung wie api: near_binary_event-Feld/-Risk-Flag,
    Bio_Trade_Mode == NEAR_BINARY_EVENT oder days <= 3.
    """
    if not isinstance(row, dict):
        return False
    if row.get("near_binary_event"):
        return True
    mode = str(row.get("Bio_Trade_Mode", row.get("bio_trade_mode", "")) or "").upper()
    if mode == "NEAR_BINARY_EVENT":
        return True
    flags_raw = row.get("Bio_Risk_Flags") or row.get("bio_risk_flags") or []
    flags = {
        str(flag).lower()
        for flag in (flags_raw if isinstance(flags_raw, list) else [flags_raw])
        if flag
    }
    if "near_binary_event" in flags:
        return True
    days = _biotech_binary_event_days(row)
    return days is not None and 0 <= days <= 3


def _biotech_binary_warning_html(row):
    """H-2: Rote Binary-Event-Warnzeile fuer Plan-HTML und Mail-Rows (wie api)."""
    if not _biotech_near_binary_event(row):
        return ""
    days = _biotech_binary_event_days(row)
    days_txt = f"{int(days)}d" if isinstance(days, (int, float)) and days >= 0 else "&le;3d"
    return (
        '<br><span style="color:#dc2626;font-weight:bold;font-size:12px">'
        f'⚠ Binäres Event in {days_txt} — Stop schützt NICHT über ein Gap. '
        'Kein Neueinstieg, Run-up-Trades vor T-3 schließen.</span>'
    )


def _format_alert_plan_html(row):
    levels = _alert_trade_levels(row)
    if not levels.get("valid"):
        errors = ", ".join(levels.get("errors", [])[:3]) or "invalid_trade_plan"
        return (
            '<span style="color:#dc2626;font-weight:bold">Kein gueltiger Trade-Plan</span>'
            f'<br><span style="color:#64748b;font-size:12px">{errors}</span>'
        )
    quality = trade_plan_quality(levels)
    effective_rr = quality.get("effective_rr")
    rr1 = quality.get("rr_tp1")
    rr2 = quality.get("rr_tp2")
    rr_text = ""
    if isinstance(effective_rr, (int, float)):
        rr_text = f'<br><span style="color:#64748b;font-size:12px">R:R eff {effective_rr:.2f}</span>'
        if isinstance(rr1, (int, float)) and isinstance(rr2, (int, float)):
            rr_text += f'<br><span style="color:#64748b;font-size:11px">TP1 {rr1:.1f}R / TP2 {rr2:.1f}R</span>'
    issue_text = ""
    if quality.get("issues"):
        issue_label = html.escape(", ".join(str(item) for item in quality["issues"][:3]))
        issue_text = f'<br><span style="color:#dc2626;font-size:11px;font-weight:bold">Trade-Plan blockiert: {issue_label}</span>'
    level_source_text = _alert_level_source_line(row)
    source_text = (
        '<br><span style="color:#b45309;font-size:11px">Level geschaetzt - native Scanner-Level fehlen/teilweise fehlen</span>'
        if levels.get("estimated") else ""
    )
    # AUDIT M-4 (10.06.2026): synthetische Biotech-Struktur-Level ehrlich
    # kennzeichnen (Flag Trade_Setup_Synthetic / levels['synthetic'];
    # Trade_Setup_Source-Prefix nur als Fallback fuer Alt-Cache-Rows).
    _setup_source = str(row.get("Trade_Setup_Source", row.get("trade_setup_source", "")) or "")
    synthetic_text = (
        '<br><span style="color:#b45309;font-size:11px">Struktur-Level (ATR/Support) - nicht Scanner-nativ</span>'
        if (row.get("Trade_Setup_Synthetic") or levels.get("synthetic")
            or _setup_source.startswith("biotech_daily")) else ""
    )
    # H-2 (Audit 10.06.2026): Binary-Event-Warnung in jeder Abonnenten-Flaeche.
    binary_warning_text = _biotech_binary_warning_html(row)
    return (
        f'Entry <b>{_format_alert_price(levels.get("entry"))}</b><br>'
        f'Stop <b style="color:#dc2626">{_format_alert_price(levels.get("stop"))}</b><br>'
        f'TP1/TP2 <b style="color:#059669">{_format_alert_price(levels.get("tp1"))} / {_format_alert_price(levels.get("tp2"))}</b>'
        f'{rr_text}'
        f'{issue_text}'
        f'{level_source_text}'
        f'{source_text}'
        f'{synthetic_text}'
        f'{binary_warning_text}'
    )


def _alert_trade_plan_ok(row, min_rr=1.0):
    levels = _alert_trade_levels(row)
    if not levels.get("valid"):
        return False
    # Q3/B4: geschaetzte Level (price_fallback / 3%-Range-Schaetzung) sind keine
    # handelbaren nativen Scanner-Level -> nicht mailbar (api: estimated_trade_plan).
    if levels.get("estimated"):
        return False
    quality = trade_plan_quality(levels)
    effective_rr = quality.get("effective_rr")
    if quality.get("tp1_ok") is False or quality.get("issues"):
        return False
    return isinstance(effective_rr, (int, float)) and effective_rr >= min_rr


def _alert_dedupe_ttl_seconds(scanner_name):
    """B2: Dedupe-TTL je Scanner — identisch zu api._alert_dedupe_ttl_seconds."""
    if str(scanner_name or "").lower() == "biotech":
        return _BIOTECH_ALERT_DEDUPE_SEC
    return _EMAIL_COOLDOWN_SEC


def _bg_alert_health_reasons(row, scanner_name):
    """Q3/B4: api-Health-Gate gespiegelt — beantwortet 'jetzt handelbar?'.

    Stop-Breach, Chase-/Entry-Zonen-Schutz und live R:R liegen zentral in
    modules.trade_health; mailbar nur decision==TRADEABLE mit health_score >= 80.
    """
    levels = _alert_trade_levels(row)
    health_row = dict(row)
    if levels.get("valid"):
        health_row.setdefault("entry", levels.get("entry"))
        health_row.setdefault("Entry", levels.get("entry"))
        health_row.setdefault("stop_loss", levels.get("stop"))
        health_row.setdefault("StopLoss", levels.get("stop"))
        health_row.setdefault("tp1", levels.get("tp1"))
        health_row.setdefault("TP1", levels.get("tp1"))
        health_row.setdefault("tp2", levels.get("tp2"))
        health_row.setdefault("TP2", levels.get("tp2"))
        health_row.setdefault("direction", levels.get("direction"))
    if "current_price" not in health_row:
        health_row["current_price"] = _extract_alert_price(health_row)
    try:
        health = calculate_trade_health(health_row, scanner_name=scanner_name)
    except Exception as exc:
        log.warning(f"Health-Gate Fehler ({scanner_name}): {exc}")
        return ["health_check_failed"]
    reasons = []
    decision = str(health.get("decision", "") or "").upper()
    if decision != "TRADEABLE":
        reasons.append(f"health_{(decision or 'unknown').lower()}")
    if _safe_float(health.get("health_score"), 0) < _ALERT_MIN_HEALTH_SCORE:
        reasons.append("health_score_below_threshold")
    if str(health.get("chase_risk", "") or "").upper() in {"HIGH", "CRITICAL"}:
        reasons.append("health_chase_risk")
    if str(health.get("fakeout_risk", "") or "").upper() in {"HIGH", "CRITICAL"}:
        reasons.append("health_fakeout_risk")
    if str(health.get("liquidity_risk", "") or "").upper() == "CRITICAL":
        reasons.append("health_liquidity_risk")
    return list(dict.fromkeys(reasons))


def _extract_long_entry_fields(row):
    return {
        "change_pct": _safe_float(row.get("change_pct", row.get("Change_Pct", row.get("Change%"))), None),
        "gap_pct": _safe_float(row.get("gap_pct", row.get("Gap_Pct", row.get("Gap%"))), None),
        "close_pos": _safe_float(row.get("close_pos", row.get("Close_Position", row.get("Close Position"))), None),
        "open_to_current_pct": _safe_float(row.get("open_to_current_pct", row.get("Open_To_Current_Pct")), None),
        "latest_bar_change_pct": _safe_float(row.get("latest_bar_change_pct"), None),
        "latest_bar_close_pos": _safe_float(row.get("latest_bar_close_pos"), None),
        "extension_atr": _safe_float(row.get("Extension_ATR", row.get("extension_atr")), None),
        "upper_wick_pct": _safe_float(row.get("Upper_Wick_Pct", row.get("upper_wick_pct")), None),
        "rvol": _safe_float(row.get("rvol", row.get("RVOL")), None),
        "mdr_tag": str(row.get("mdr_tag", "") or "").upper(),
    }


def _long_continuation_ok(fields):
    close_pos = fields.get("close_pos")
    latest_change = fields.get("latest_bar_change_pct")
    latest_close_pos = fields.get("latest_bar_close_pos")
    rvol = fields.get("rvol")
    mdr_tag = fields.get("mdr_tag", "")
    latest_available = latest_change is not None and latest_close_pos is not None
    latest_ok = latest_available and (latest_change >= -0.05 or latest_close_pos >= 0.55)
    volume_ok = rvol is None or rvol >= 1.2
    holding_highs = close_pos is not None and close_pos >= 0.78
    mdr_ok = "MDR" in mdr_tag and "CRASH" not in mdr_tag and close_pos is not None and close_pos >= 0.65
    return (holding_highs and latest_ok and volume_ok) or (mdr_ok and latest_ok)


def _long_entry_rule_reasons(row):
    direction = str(row.get("Signal_Direction", row.get("direction", "")) or "").lower()
    if "short" in direction:
        return []
    fields = _extract_long_entry_fields(row)
    reasons = []
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    latest_change = fields["latest_bar_change_pct"]
    latest_close_pos = fields["latest_bar_close_pos"]
    extension_atr = fields["extension_atr"]

    latest_red_fade = (
        latest_change is not None
        and latest_close_pos is not None
        and latest_change < -0.15
        and latest_close_pos < 0.45
    )
    latest_missing = latest_change is None or latest_close_pos is None
    intraday_red_fade = open_to_current is not None and open_to_current < -0.25
    not_holding_highs = change is not None and change > 3 and close_pos is not None and close_pos < 0.55
    extended = (change is not None and change >= 12) or (extension_atr is not None and extension_atr >= 4.0)
    hard_extended = (change is not None and change >= 30) or (extension_atr is not None and extension_atr >= 6.0)
    continuation_ok = _long_continuation_ok(fields)

    if latest_red_fade:
        reasons.append("latest_5m_red_fade")
    if latest_missing:
        reasons.append("fresh_5m_state_missing_wait_trigger")
    if intraday_red_fade:
        reasons.append("current_candle_red_fade")
    if not_holding_highs and (extended or latest_red_fade or intraday_red_fade):
        reasons.append("not_holding_highs_after_up_move")
    if hard_extended and not continuation_ok:
        reasons.append("hard_extended_long_wait_retest")
    elif extended and latest_missing:
        reasons.append("fresh_5m_state_missing_wait_retest")
    elif extended and (latest_red_fade or intraday_red_fade or not_holding_highs):
        reasons.append("extended_long_fading_wait_retest")
    return reasons


def _stock_swing_rule_reasons(row):
    """Daily/swing timing gate; intentionally independent of 1m/5m data."""
    direction = str(row.get("Signal_Direction", row.get("direction", "")) or "").lower()
    if "short" in direction:
        return []
    fields = _extract_long_entry_fields(row)
    reasons = []
    change = fields["change_pct"]
    gap_pct = fields.get("gap_pct")
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    extension_atr = fields["extension_atr"]
    upper_wick_pct = fields.get("upper_wick_pct")
    rvol = fields.get("rvol")
    strategy_name = str(row.get("Strategy") or row.get("strategy") or "").lower()

    extended = (change is not None and change >= 12.0) or (extension_atr is not None and extension_atr >= 4.0)
    hard_extended = (change is not None and change >= 25.0) or (extension_atr is not None and extension_atr >= 6.0)
    soft_extended_without_volume = change is not None and change >= 8.0 and (rvol is None or rvol < 1.5)
    fading_daily = open_to_current is not None and open_to_current < -0.5
    not_holding_highs = change is not None and change > 3 and close_pos is not None and close_pos < 0.55
    is_gap_momentum_long = "gap momentum long" in strategy_name or "gap up" in strategy_name
    is_momentum_breakout_long = "momentum breakout long" in strategy_name
    is_meaningful_gap = is_gap_momentum_long and (
        (gap_pct is not None and gap_pct >= 3.0)
        or (change is not None and change >= 6.0)
    )
    is_momentum_gap_overlap = is_momentum_breakout_long and (
        (gap_pct is not None and gap_pct >= 3.0)
        or (change is not None and change >= 7.0 and rvol is not None and rvol >= 2.0)
    )

    if hard_extended:
        reasons.append("swing_hard_extended_no_chase")
    elif extended:
        reasons.append("swing_extended_wait_retest")
    elif soft_extended_without_volume:
        reasons.append("swing_extended_without_volume_wait_retest")
    if fading_daily:
        reasons.append("swing_current_candle_fading")
    if not_holding_highs and (extended or soft_extended_without_volume or fading_daily):
        reasons.append("swing_not_holding_highs_after_move")
    if is_meaningful_gap:
        if open_to_current is not None and open_to_current < 0.25:
            reasons.append("swing_gap_not_holding_open_wait_retest")
        if close_pos is not None and close_pos < 0.72:
            reasons.append("swing_gap_not_holding_upper_range_wait_retest")
        if upper_wick_pct is not None and upper_wick_pct >= 38:
            reasons.append("swing_gap_wick_rejection_wait_retest")
    if is_momentum_gap_overlap:
        momentum_type = str(row.get("Momentum_Breakout_Type") or row.get("momentum_breakout_type") or "").upper()
        continuation_status = str(row.get("Breakout_Continuation_Status") or row.get("breakout_continuation_status") or "").upper()
        continuation_score = _safe_float(row.get("Breakout_Continuation_Score", row.get("breakout_continuation_score")), None)
        if momentum_type == "TREND_RECLAIM" and change is not None and change >= 6.0:
            reasons.append("swing_momentum_trend_reclaim_gap_wait_retest")
        if open_to_current is not None and open_to_current < 0.25:
            reasons.append("swing_momentum_not_holding_open_wait_retest")
        if close_pos is not None and close_pos < 0.72:
            reasons.append("swing_momentum_not_holding_upper_range_wait_retest")
        if upper_wick_pct is not None and upper_wick_pct >= 34:
            reasons.append("swing_momentum_wick_rejection_wait_retest")
        if continuation_status and continuation_status != "CONTINUATION_OK":
            reasons.append("swing_momentum_breakout_quality_wait_retest")
        elif continuation_score is not None and continuation_score < 78:
            reasons.append("swing_momentum_breakout_quality_wait_retest")
    return list(dict.fromkeys(reasons))


def _long_entry_quality(row):
    reasons = _long_entry_rule_reasons(row)
    if reasons:
        if any(reason.endswith("wait_retest") for reason in reasons):
            return "WAIT_RETEST"
        return "FADE_WATCH"
    fields = _extract_long_entry_fields(row)
    extended = (
        (fields["change_pct"] is not None and fields["change_pct"] >= 12)
        or (fields["extension_atr"] is not None and fields["extension_atr"] >= 4.0)
    )
    if extended and _long_continuation_ok(fields):
        return "CONTINUATION_OK"
    return "TRADEABLE"


def _extract_bear_short_fields(row):
    return {
        "change_pct": _safe_float(row.get("change_pct", row.get("Change%")), None),
        "close_pos": _safe_float(row.get("close_pos", row.get("Close Position")), None),
        "open_to_current_pct": _safe_float(row.get("open_to_current_pct", row.get("intraday_change_pct")), None),
        "latest_bar_change_pct": _safe_float(row.get("latest_bar_change_pct"), None),
        "latest_bar_close_pos": _safe_float(row.get("latest_bar_close_pos"), None),
        "rvol": _safe_float(row.get("rvol", row.get("RVOL")), None),
        "score": _safe_float(row.get("score", row.get("Score")), None),
    }


def _bear_short_rule_reasons(row):
    fields = _extract_bear_short_fields(row)
    reasons = []
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    latest_bar_change = fields["latest_bar_change_pct"]
    latest_bar_close_pos = fields["latest_bar_close_pos"]
    rvol = fields["rvol"]
    latest_missing = latest_bar_change is None or latest_bar_close_pos is None

    if change is None:
        reasons.append("missing_current_drop")
    elif change > -3:
        reasons.append("not_down_enough_for_breakdown")
    elif change <= -12:
        reasons.append("drop_too_extended_no_chase")
    if open_to_current is not None and open_to_current > 0.2:
        reasons.append("current_candle_green_reclaim")
    if close_pos is not None and close_pos > 0.45:
        reasons.append("not_closing_near_low")
    if latest_missing:
        reasons.append("fresh_5m_state_missing_wait_trigger")
    if (
        latest_bar_change is not None
        and latest_bar_close_pos is not None
        and latest_bar_change > 0.15
        and latest_bar_close_pos > 0.55
    ):
        reasons.append("latest_5m_green_reclaim")
    if rvol is not None and rvol < 1.0:
        reasons.append("rvol_below_bear_threshold")
    return reasons


def _stock_swing_short_rule_reasons(row):
    """Daily/swing short gate; prevents chasing a completed daily collapse."""
    fields = _extract_bear_short_fields(row)
    reasons = []
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    rvol = fields["rvol"]

    if change is None:
        reasons.append("missing_current_drop")
    elif change > -2.0:
        reasons.append("swing_short_not_down_enough")
    elif change <= -22.0:
        reasons.append("swing_short_drop_too_extended_no_chase")
    elif change <= -12.0:
        reasons.append("swing_short_extended_wait_retest")
    elif change <= -8.0:
        reasons.append("swing_short_drop_extended_wait_failed_reclaim")
    if open_to_current is not None and open_to_current > 0.5:
        reasons.append("swing_short_current_candle_reclaim")
    if close_pos is not None and close_pos > 0.55:
        reasons.append("swing_short_not_closing_weak")
    if rvol is not None and rvol < 0.7:
        reasons.append("rvol_below_bear_threshold")
    return list(dict.fromkeys(reasons))


def _bear_entry_quality(row):
    reasons = _bear_short_rule_reasons(row)
    if not reasons:
        return "TRADEABLE"
    if "drop_too_extended_no_chase" in reasons:
        return "NO_CHASE"
    if "current_candle_green_reclaim" in reasons:
        return "RECLAIM_WATCH"
    return "WATCH"


def _bear_crash_alert_ok(row):
    if row.get("alertable_short") is False:
        return False
    fields = _extract_bear_short_fields(row)
    change = fields["change_pct"]
    close_pos = fields["close_pos"]
    open_to_current = fields["open_to_current_pct"]
    if change is None or change > -10 or change <= -30:
        return False
    if open_to_current is not None and open_to_current > 0.2:
        return False
    if close_pos is not None and close_pos > 0.35:
        return False
    latest_bar_change = fields["latest_bar_change_pct"]
    latest_bar_close_pos = fields["latest_bar_close_pos"]
    if (
        latest_bar_change is not None
        and latest_bar_close_pos is not None
        and latest_bar_change > 0.15
        and latest_bar_close_pos > 0.55
    ):
        return False
    return True


def _fetch_bear_latest_intraday_state(ticker, poly_key):
    """Fetch the latest 5m candle so Bear mails do not chase into a live bounce."""
    if not ticker or not poly_key:
        return {}
    try:
        try:
            from zoneinfo import ZoneInfo
            today_et = datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d")
        except Exception:
            today_et = datetime.utcnow().strftime("%Y-%m-%d")
        url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{today_et}/{today_et}"
        resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "desc", "limit": 3}, timeout=10)
        if resp.status_code != 200:
            return {}
        bars = resp.json().get("results", [])
        if not bars:
            return {}
        bar = bars[0]
        open_ = bar.get("o", 0) or 0
        high = bar.get("h", 0) or 0
        low = bar.get("l", 0) or 0
        close = bar.get("c", 0) or 0
        if not open_ or not close:
            return {}
        change_pct = ((close - open_) / open_) * 100
        close_pos = ((close - low) / (high - low)) if high > low else 0.5
        return {
            "latest_bar_change_pct": round(change_pct, 2),
            "latest_bar_close_pos": round(close_pos, 3),
            "latest_bar_timestamp": bar.get("t"),
        }
    except Exception:
        return {}


def _fetch_long_latest_intraday_state(ticker, poly_key):
    """Same 5m state, used to block fading long mails without blocking continuation."""
    return _fetch_bear_latest_intraday_state(ticker, poly_key)


def _display_crypto_contract_symbol(symbol):
    display = str(symbol or "").strip().upper()
    for suffix in ("USD-PERP", "USDT-PERP", "_USDT", "-USDT", "USDT", "_PERP", "-PERP", "USD"):
        if display.endswith(suffix) and len(display) > len(suffix):
            display = display[:-len(suffix)]
            break
    return display or str(symbol or "").strip().upper()


def _looks_like_non_stock_product_symbol(ticker):
    tk = str(ticker or "").upper().strip()
    if not tk:
        return "empty ticker"
    if tk in _NON_STOCK_PRODUCT_TICKERS:
        return "known ETF/ETP ticker"
    if len(tk) >= 4 and tk[-1] in ("X", "Q") and tk[-2] in ("X", "Q", "S"):
        return "leveraged ETF ticker pattern"
    return None


def _name_has_non_stock_product_keyword(name):
    normalized_name = re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()).strip()
    if not normalized_name:
        return False
    padded_name = f" {normalized_name} "
    for keyword in _NON_STOCK_PRODUCT_KEYWORDS:
        normalized_keyword = re.sub(r"[^A-Z0-9]+", " ", keyword.upper()).strip()
        if normalized_keyword and f" {normalized_keyword} " in padded_name:
            return True
    return False


def _load_common_stock_universe(poly_key, max_age_seconds=24 * 3600):
    """Load active common-stock/ADR tickers for stock-alert filtering."""
    now_ts = time.time()
    stale_cached_tickers = set()
    stale_cached_source = "not_loaded"
    stale_cached_at = 0
    try:
        if os.path.exists(_COMMON_STOCK_UNIVERSE_CACHE):
            with open(_COMMON_STOCK_UNIVERSE_CACHE, "r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict):
                cached_at = float(cached.get("cached_at", 0) or 0)
                cached_tickers = set(cached.get("tickers", []) or [])
            else:
                cached_at = os.path.getmtime(_COMMON_STOCK_UNIVERSE_CACHE)
                cached_tickers = set(cached or [])
            if cached_tickers and now_ts - cached_at < max_age_seconds:
                return cached_tickers, "file_cache"
            if cached_tickers:
                stale_cached_tickers = cached_tickers
                stale_cached_source = "stale_file_cache"
                stale_cached_at = cached_at
    except Exception as exc:
        log.debug(f"Common stock universe cache read failed: {exc}")

    if not poly_key:
        if stale_cached_tickers:
            return stale_cached_tickers, stale_cached_source
        return None, "missing_polygon_key"

    try:
        from modules.data_fetchers import rate_limited_get
        tickers = set()
        for asset_type in sorted(_STOCK_REFERENCE_TYPES):
            url = "https://api.polygon.io/v3/reference/tickers"
            params = {
                "apiKey": poly_key,
                "market": "stocks",
                "active": "true",
                "type": asset_type,
                "limit": 1000,
                "sort": "ticker",
                "order": "asc",
            }
            pages = 0
            while url and pages < 20:
                resp = rate_limited_get(url, params=params, timeout=20)
                if resp.status_code != 200:
                    break
                payload = resp.json()
                for item in payload.get("results", []) or []:
                    tk = str(item.get("ticker", "") or "").upper().strip()
                    market = str(item.get("market", "") or "").lower()
                    item_type = str(item.get("type", "") or "").upper()
                    name = str(item.get("name", "") or "").upper()
                    if not tk or market != "stocks" or item_type not in _STOCK_REFERENCE_TYPES:
                        continue
                    if _looks_like_non_stock_product_symbol(tk) or _name_has_non_stock_product_keyword(name):
                        continue
                    tickers.add(tk)
                next_url = payload.get("next_url")
                url = next_url if next_url else None
                params = {"apiKey": poly_key} if next_url else {}
                pages += 1
        if tickers:
            os.makedirs(os.path.dirname(_COMMON_STOCK_UNIVERSE_CACHE) or ".", exist_ok=True)
            _atomic_write_json(_COMMON_STOCK_UNIVERSE_CACHE, {"cached_at": now_ts, "tickers": sorted(tickers)})
            return tickers, "polygon_reference"
    except Exception as exc:
        log.warning(f"Common stock universe fetch failed: {exc}")
    if stale_cached_tickers:
        return stale_cached_tickers, stale_cached_source
    return None, "unavailable"


def _stock_alert_asset_exclusion_reason(ticker, common_stock_universe=None, universe_source=""):
    tk = str(ticker or "").upper().strip()
    cheap_reason = _looks_like_non_stock_product_symbol(tk)
    if cheap_reason:
        return cheap_reason
    if "." in tk or "/" in tk:
        return "non-standard ticker class"
    if common_stock_universe is not None and tk not in common_stock_universe:
        return f"not in common-stock universe ({universe_source or 'unknown source'})"
    return None


def _email_has_blocked_etf_content(subject, body_html):
    """Hard guard: email alerts should contain stock/crypto setups, not ETF/ETP watchlists."""
    content = f"{subject or ''} {body_html or ''}".upper()
    if any(marker in content for marker in (
        "INVERSE ETF",
        "INVERSE ETFS",
        "LEVERAGED ETF",
        "LEVERAGED ETFS",
        "3X SHORT",
        "2X SHORT",
    )):
        return True
    tokens = set(re.findall(r"\b[A-Z]{2,6}\b", content))
    if tokens & _EMAIL_BLOCKED_ETF_TICKERS:
        return True
    return any(_looks_like_non_stock_product_symbol(token) for token in tokens)


def _load_email_dedupe(now=None, max_keep_seconds=7 * 86400):
    try:
        return _shared_load_email_dedupe(
            _EMAIL_DEDUPE_FILE,
            now=now,
            max_keep_seconds=max_keep_seconds,
        )
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Datei konnte nicht gelesen werden: {exc}")
        return {}


def _save_email_dedupe(dedupe):
    try:
        _shared_save_email_dedupe(_EMAIL_DEDUPE_FILE, dedupe)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Datei konnte nicht gespeichert werden: {exc}")


def _email_dedupe_active(key, ttl_seconds, now=None):
    try:
        return _shared_email_dedupe_active(_EMAIL_DEDUPE_FILE, key, ttl_seconds, now=now)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Status konnte nicht gelesen werden: {exc}")
        return False


def _email_dedupe_remaining(key, ttl_seconds, now=None):
    try:
        return _shared_email_dedupe_remaining(_EMAIL_DEDUPE_FILE, key, ttl_seconds, now=now)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Restzeit konnte nicht gelesen werden: {exc}")
        return 0


def _bearish_stock_alert_key(ticker):
    return f"bearish_stock_{str(ticker or '').strip().upper()}"


def _bearish_stock_alert_active(ticker, now=None):
    if not ticker:
        return False
    return _email_dedupe_remaining(_bearish_stock_alert_key(ticker), _BEARISH_STOCK_ALERT_DEDUPE_SEC, now) > 0


def _mark_bearish_stock_alert(ticker, now=None):
    if ticker:
        _email_dedupe_mark(_bearish_stock_alert_key(ticker), now=now)


def _email_dedupe_mark(key, now=None):
    try:
        _shared_email_dedupe_mark(_EMAIL_DEDUPE_FILE, key, now=now)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Markierung konnte nicht gespeichert werden: {exc}")


def _email_dedupe_claim(key, ttl_seconds, now=None):
    """Return True only once per key+TTL, even after process restarts."""
    try:
        return _shared_email_dedupe_claim(_EMAIL_DEDUPE_FILE, key, ttl_seconds, now=now)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Claim konnte nicht gespeichert werden: {exc}")
        return False


def _email_dedupe_release(key, claimed_at=None):
    try:
        return _shared_email_dedupe_release(_EMAIL_DEDUPE_FILE, key, claimed_at=claimed_at)
    except Exception as exc:
        log.warning(f"E-Mail-Dedupe-Claim konnte nicht freigegeben werden: {exc}")
        return False


def _cleanup_email_cooldown():
    """Entfernt abgelaufene Cooldown-Einträge (verhindert Memory Leak über Tage/Wochen)"""
    now = time.time()
    expired = [k for k, ts in _EMAIL_COOLDOWN.items() if now - ts > _EMAIL_COOLDOWN_SEC]
    for k in expired:
        del _EMAIL_COOLDOWN[k]
    if expired:
        log.debug(f"  Cooldown cleanup: {len(expired)} abgelaufene Einträge entfernt, {len(_EMAIL_COOLDOWN)} aktiv")


def _apply_mail_class_prefix(subject, mail_class):
    """B6: Betreff-Praefix je Mail-Klasse (trade/watch/info), api-identisch.

    Vorhandene fuehrende Alert-Emojis und alte Klassen-Tokens werden ERSETZT,
    nicht gestapelt.
    """
    text = str(subject or "").strip()
    known_emojis = ("🚨", "📊", "🔴", "👁️", "👁", "ℹ️", "ℹ", "⚠️", "⚠", "🏆", "🔥", "🆕")
    known_tokens = ("JETZT:", "WATCH:")
    changed = True
    while changed and text:
        changed = False
        for emoji in known_emojis:
            if text.startswith(emoji):
                text = text[len(emoji):].lstrip()
                changed = True
        for token in known_tokens:
            if text.upper().startswith(token):
                text = text[len(token):].lstrip()
                changed = True
    prefix = _MAIL_CLASS_PREFIXES.get(str(mail_class or "").strip().lower(), "")
    return f"{prefix}{text}"


# ── Signal-Tracking & Telegram (Team-A-Module, defensiv gekapselt) ──
_CG_MARKETS_CACHE_FILE = "/tmp/coingecko_markets_cache.json"
_SIGNAL_EVAL_INTERVAL_SEC = 3600  # stündlicher Evaluierungs-Job (gehört IMMER bg)
_signal_eval_warned_missing = False
# Quote-/Perp-Suffixe für Crypto-Symbol-Matching (TSTUSDT -> TST)
_CRYPTO_QUOTE_SUFFIXES = ("USDT", "USDC", "PERP", "USD", "EUR", "BTC")


def _record_alert_signals_safe(scanner_name, rows, mail_class="trade", channel="email"):
    """Erfasst gemailte Signale im Tracker (Kontrakt: wirft nie).

    Trotzdem gekapselt: fehlendes Modul / unerwartete Fehler dürfen den
    Mail-Pfad nie brechen. Erwartet ORIGINAL-Rows (inkl. Entry/Stop/TP),
    nicht die aufbereiteten Mail-Dicts.
    """
    if record_alert_signals is None or not rows:
        return 0
    try:
        count = record_alert_signals(scanner_name, rows, mail_class=mail_class, channel=channel)
        log.info(f"[SignalTracker] {scanner_name}: {count} Signal(e) erfasst (mail_class={mail_class})")
        return count
    except Exception as exc:
        log.warning(f"[SignalTracker] record fehlgeschlagen ({scanner_name}): {exc}")
        return 0


def _format_telegram_text(rows):
    """Telegram-Kurztext aus Alert-Rows (Team-A-Formatter, tolerant)."""
    if format_alert_rows_for_telegram is None or not rows:
        return ""
    try:
        return str(format_alert_rows_for_telegram(rows) or "")
    except Exception as exc:
        log.debug(f"[Telegram] Formatter fehlgeschlagen: {exc}")
        return ""


def _send_telegram_companion(final_subject, mail_class, telegram_text=""):
    """Telegram-Spiegel für trade-Mails (nur wenn Modul vorhanden + konfiguriert).

    Läuft NACH erfolgreichem Mail-Versand; Fehler hier ändern nichts am
    Mail-Erfolg (return-Wert von _send_email_alert bleibt unberührt).
    """
    if mail_class != "trade":
        return False
    if is_telegram_configured is None or send_telegram_alert is None:
        return False
    try:
        if not is_telegram_configured():
            return False
        ok = bool(send_telegram_alert(final_subject, telegram_text))
        if ok:
            log.info(f"[Telegram] Alert gespiegelt: {final_subject}")
        return ok
    except Exception as exc:
        log.warning(f"[Telegram] Versand fehlgeschlagen: {exc}")
        return False


def _tracker_stock_fetcher(ticker, since_iso_date):
    """Daily-Bars für den Signal-Tracker via Polygon Aggregates.

    Kontrakt Team A: list[{date, high, low, close}] oder None bei Fehler.
    """
    try:
        import requests as req
        poly_key = _load_secrets().get("POLYGON_KEY", "")
        if not poly_key or not ticker or not since_iso_date:
            return None
        today = datetime.now().strftime("%Y-%m-%d")
        url = (f"https://api.polygon.io/v2/aggs/ticker/{str(ticker).upper()}"
               f"/range/1/day/{since_iso_date}/{today}")
        resp = req.get(url, params={"apiKey": poly_key, "adjusted": "true",
                                    "sort": "asc", "limit": 5000}, timeout=15)
        if resp.status_code != 200:
            log.debug(f"[SignalTracker] Polygon HTTP {resp.status_code} für {ticker}")
            return None
        bars = []
        for b in (resp.json().get("results") or []):
            try:
                bars.append({
                    "date": datetime.fromtimestamp(b["t"] / 1000, tz=timezone.utc).strftime("%Y-%m-%d"),
                    "high": float(b["h"]),
                    "low": float(b["l"]),
                    "close": float(b["c"]),
                })
            except (KeyError, TypeError, ValueError):
                continue
        return bars
    except Exception as exc:
        log.debug(f"[SignalTracker] Stock-Fetcher {ticker} fehlgeschlagen: {exc}")
        return None


def _tracker_crypto_fetcher(ticker, instrument_id=None, venue=None, contract_symbol=None):
    """Aktueller Crypto-Preis best-effort aus dem CoinGecko-Markets-Cache.

    Symbol-Match case-insensitive; Perp-/Quote-Suffixe (z.B. USDT) werden
    gestrippt. Kein Treffer / kein Cache => None (Kontrakt Team A).
    """
    try:
        sym = str(ticker or "").strip().upper()
        if not sym:
            return None
        for suffix in _CRYPTO_QUOTE_SUFFIXES:
            if sym.endswith(suffix) and len(sym) > len(suffix):
                sym = sym[: -len(suffix)].rstrip("-_/ ")
                break
        if not os.path.exists(_CG_MARKETS_CACHE_FILE):
            return None
        with open(_CG_MARKETS_CACHE_FILE, "r") as f:
            payload = json.load(f)
        # NACHAUDIT M18 (v2, re-audit-korrigiert): Cache-Alter pruefen, sonst
        # bewertet der Tracker Stops/TPs gegen stunden- bis tagealte Preise.
        # ACHTUNG: Diese Datei hat ZWEI Writer mit UNTERSCHIEDLICHEM Zeitfeld —
        # bg (_cg_markets_cache_payload) schreibt "ts" (float epoch),
        # api.py (_fetch_coingecko_markets) schreibt "cached_at" (ISO-String).
        # Der erste Fix las nur "ts" -> nach jedem api-Scan war das Feld None
        # -> Tracker lieferte dauerhaft None (stille Abschaltung). Jetzt werden
        # beide Felder gelesen, ISO wird geparst, und als letzter Anker dient
        # die Datei-mtime. TTL 3h > langsamster legitimer Refresh (2h-Divergenz-
        # Scan) und faengt trotzdem eine tote Pipeline ab.
        _cache_epoch = None
        if isinstance(payload, dict):
            _raw_ts = payload.get("ts")
            if isinstance(_raw_ts, (int, float)):
                _cache_epoch = float(_raw_ts)
            else:
                _raw_iso = payload.get("cached_at")
                if _raw_iso:
                    try:
                        _cache_epoch = datetime.fromisoformat(
                            str(_raw_iso).replace("Z", "+00:00")
                        ).timestamp()
                    except (TypeError, ValueError):
                        _cache_epoch = None
        if _cache_epoch is None:
            # Letzter Anker: Datei-Schreibzeit (immer ein echtes Signal).
            try:
                _cache_epoch = os.path.getmtime(_CG_MARKETS_CACHE_FILE)
            except OSError:
                _cache_epoch = None
        _cache_age = (time.time() - _cache_epoch) if _cache_epoch is not None else None
        _MAX_MARKETS_CACHE_AGE = 10800  # 3h
        if _cache_age is None or _cache_age < 0 or _cache_age > _MAX_MARKETS_CACHE_AGE:
            log.debug(f"[SignalTracker] Markets-Cache zu alt/unbekannt (age={_cache_age}) — kein Preis fuer {ticker}")
            return None
        coins = payload.get("coins", []) if isinstance(payload, dict) else payload
        requested_id = str(instrument_id or "").strip().lower()
        candidates = []
        for c in coins or []:
            if not isinstance(c, dict):
                continue
            coin_id = str(c.get("id", "")).strip().lower()
            if requested_id and coin_id == requested_id:
                price = c.get("current_price")
                return float(price) if isinstance(price, (int, float)) and price > 0 else None
            if str(c.get("symbol", "")).strip().upper() == sym:
                candidates.append(c)
        if requested_id:
            return None
        unique_ids = {str(c.get("id", "")).strip().lower() for c in candidates}
        unique_ids.discard("")
        if len(candidates) != 1 or len(unique_ids) > 1:
            if candidates:
                log.warning(
                    "[SignalTracker] Mehrdeutiges Crypto-Symbol %s (%s) ohne coin_id; "
                    "Bewertung wird aus Sicherheitsgruenden uebersprungen",
                    sym,
                    ", ".join(sorted(unique_ids)) or "IDs unbekannt",
                )
            return None
        if candidates:
            price = candidates[0].get("current_price")
            if isinstance(price, (int, float)) and price > 0:
                return float(price)
        return None
    except Exception as exc:
        log.debug(f"[SignalTracker] Crypto-Fetcher {ticker} fehlgeschlagen: {exc}")
        return None


# Mailbare Exit-Ereignisse je Transitions-Status (Kontrakt
# evaluate_open_signals -> result['transitions']). UNTRACKED fehlt bewusst:
# "keine Kursdaten" ist ein Datenproblem, kein Positions-Ereignis fuers Abo.
_SIGNAL_UPDATE_EVENTS = {
    "STOP_HIT": "Stop erreicht",
    "TP1_HIT_OPEN": "TP1 erreicht — Rest Freiroll Richtung TP2",
    "TP2_HIT": "TP2 erreicht",
    "EXPIRED": "Verfallen",
}


def _signal_origin_was_mailed(scanner, ticker, now=None):
    """Zweitsicherung: hat das Ursprungssignal wirklich eine Mail ausgeloest?

    Der Tracker loggt ohnehin NUR tatsaechlich versendete Signale
    (record_alert_signals laeuft erst NACH sent=True, s. B2-Muster) — diese
    Pruefung ist eine bewusste Doppelsicherung gegen Alt-/Fremdbestaende in
    der Tracker-DB (z.B. manuell eingespielte oder Telegram-only Signale).

    Geprueft wird das geteilte persistente Dedupe (api+bg, gleiche Datei) mit
    dem Key-Format f"{scanner}_{ticker}". Sonderfall new_listing: api/bg
    markieren auf dem ROH-Symbol (new_listing_TSTUSDT), der Tracker fuehrt
    das Display-Symbol (TST) — daher dort Suffix-tolerante Suche ueber
    _display_crypto_contract_symbol. 7-Tage-Fenster = grosszuegig (Signal
    lebt max. ~5 Handelstage / 120h).
    """
    now = now or time.time()
    scanner = str(scanner or "").strip()
    ticker = str(ticker or "").strip().upper()
    if not scanner or not ticker:
        return False
    if _email_dedupe_active(f"{scanner}_{ticker}", _SIGNAL_UPDATE_DEDUPE_SEC, now=now):
        return True
    if scanner == "new_listing":
        for key, ts in _load_email_dedupe(now=now).items():
            if not str(key).startswith("new_listing_"):
                continue
            if now - ts >= _SIGNAL_UPDATE_DEDUPE_SEC:
                continue
            raw_symbol = str(key)[len("new_listing_"):]
            if _display_crypto_contract_symbol(raw_symbol) == ticker:
                return True
    return False


def _send_signal_update_mail(transitions, secrets):
    """ℹ️-Sammel-Update fuer Exits bereits gemailter Tracker-Signale.

    Schliesst den Signal-Kreislauf fuers Abo-Produkt: Wer die 🚨-Mail bekam,
    erfaehrt auch Stop/TP1/TP2/Expiry. Regeln:
      - EINE Sammelmail pro Eval-Lauf (alle Transitionen gebuendelt),
        kein Versand ohne mailbare Transitionen.
      - Nur Ereignisse aus _SIGNAL_UPDATE_EVENTS (UNTRACKED wird verworfen).
      - Zweitsicherung _signal_origin_was_mailed (s. dort).
      - Persistentes Dedupe je Transition: signal_update_{id}_{new_status}
        (TTL 7d) — kein Spam bei Re-Evals; Mark erst NACH erfolgreichem
        Versand (B2-Muster: SMTP-Fehler => naechster Lauf darf erneut).
      - Startup-Delay + ETF-Block greifen automatisch in _send_email_alert.

    Returns True nur bei tatsaechlich versendeter Mail.
    """
    if not transitions:
        return False
    now = time.time()
    pending = []
    for tr in transitions:
        if not isinstance(tr, dict):
            continue
        new_status = str(tr.get("new_status") or "")
        event = _SIGNAL_UPDATE_EVENTS.get(new_status)
        if event is None:
            continue
        if not _signal_origin_was_mailed(tr.get("scanner"), tr.get("ticker"), now=now):
            log.debug(f"[SignalTracker] Update unterdrueckt (kein Erst-Mail-Mark): "
                      f"{tr.get('scanner')}/{tr.get('ticker')} -> {new_status}")
            continue
        dedupe_key = f"signal_update_{tr.get('id')}_{new_status}"
        if _email_dedupe_active(dedupe_key, _SIGNAL_UPDATE_DEDUPE_SEC, now=now):
            continue
        pending.append((dedupe_key, event, tr))
    if not pending:
        return False

    n = len(pending)
    stop_count = sum(1 for _, _, tr in pending if tr.get("new_status") == "STOP_HIT")
    tp_count = sum(1 for _, _, tr in pending
                   if tr.get("new_status") in ("TP1_HIT_OPEN", "TP2_HIT"))
    subject = f"Signal-Update: {n} Position(en) — {stop_count} Stop / {tp_count} TP"

    rows = ""
    for _, event, tr in pending:
        ticker = html.escape(str(tr.get("ticker") or "?"))
        scanner = html.escape(str(tr.get("scanner") or "?"))
        direction = "SHORT" if str(tr.get("direction")) == "SHORT" else "LONG"
        r_realized = tr.get("r_realized")
        r_text = (f"{float(r_realized):+.2f}R"
                  if isinstance(r_realized, (int, float)) else "offen")
        plan = " | ".join(
            f"{label} {_format_alert_price(tr.get(key))}"
            for label, key in (("E", "entry"), ("SL", "stop"), ("TP1", "tp1"), ("TP2", "tp2"))
            if tr.get(key) is not None
        )
        rows += f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{ticker}</b> <span style="color:#999;font-size:11px">{direction}</span></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{scanner}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{event}</td>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{r_text}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee;color:#666;font-size:12px">{plan}</td>
        </tr>"""

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
    <h2 style="color:#0f172a">Signal-Update — Positions-Ereignisse</h2>
    <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} Update(s) zu zuvor gemailten Signalen</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Ticker</th>
            <th style="padding:8px;text-align:left">Scanner</th>
            <th style="padding:8px;text-align:left">Ereignis</th>
            <th style="padding:8px;text-align:left">R</th>
            <th style="padding:8px;text-align:left">Plan-Level</th>
        </tr>
        {rows}
    </table>
    <p style="color:#999;font-size:12px;margin-top:20px">
        Automatisches Update vom Signal-Tracker (stuendliche Evaluierung).<br>
        'TP1 erreicht' = Teilgewinn-Zone erreicht, Restposition risikofrei Richtung TP2 (Stop auf Entry).<br>
        Kein neues Signal, keine Handelsaufforderung. Einmalig je Ereignis (7-Tage-Dedupe).
    </p>
    </body></html>"""

    sent = _send_email_alert(subject, body_html, secrets, mail_class="info")
    if sent:
        # B2-Muster: Dedupe-Mark NUR nach erfolgreichem Versand.
        for dedupe_key, _, _ in pending:
            _email_dedupe_mark(dedupe_key, now=now)
        log.info(f"[SignalTracker] 📧 Update-Mail gesendet: {n} Transition(en) "
                 f"({', '.join(str(tr.get('ticker') or '?') for _, _, tr in pending)})")
    else:
        log.warning(f"[SignalTracker] Update-Mail nicht versendet ({n} Transition(en) offen)")
    return bool(sent)


def _run_signal_eval_job(secrets=None):
    """Stündliche Evaluierung offener Tracker-Signale (TP/SL-Auflösung).

    Überspringt sauber (eine Warnung, dann still), wenn das Team-A-Modul fehlt.
    Nach dem Eval gehen ℹ️-Exit-Update-Mails für die Transitionen des Laufs
    raus (_send_signal_update_mail) — Fehler dort dürfen den Eval-Job und
    seine Rückgabe NIE beschädigen (eigenes try/except).
    """
    global _signal_eval_warned_missing
    if evaluate_open_signals is None:
        if not _signal_eval_warned_missing:
            log.warning("[SignalTracker] modules.signal_tracker fehlt — Evaluierungs-Job inaktiv")
            _signal_eval_warned_missing = True
        return None
    try:
        stats = evaluate_open_signals(
            stock_daily_fetcher=_tracker_stock_fetcher,
            crypto_price_fetcher=_tracker_crypto_fetcher,
        ) or {}
        log.info(f"[SignalTracker] Eval-Lauf: evaluated={stats.get('evaluated', 0)} "
                 f"closed={stats.get('closed', 0)} errors={stats.get('errors', 0)} "
                 f"transitions={len(stats.get('transitions') or [])}")
        # Exit-Update-Mails: tolerant gegen alte Tracker-Versionen ohne
        # 'transitions'-Feld (.get) und gegen JEDEN Fehler im Mail-Bau.
        try:
            transitions = stats.get("transitions") or []
            if transitions:
                _send_signal_update_mail(
                    transitions, secrets if secrets is not None else _load_secrets()
                )
        except Exception as exc:
            log.warning(f"[SignalTracker] Exit-Update-Mail fehlgeschlagen (Eval-Ergebnis "
                        f"bleibt gueltig): {exc}")
        return stats
    except Exception as exc:
        log.warning(f"[SignalTracker] Evaluierung fehlgeschlagen: {exc}")
        return None


# ── Wochenreport-Mail (Freitag nach US-Boersenschluss) ──────────────────────
_WEEKLY_REPORT_CHECK_INTERVAL_SEC = 900       # Anklopf-Takt des Schedulers (15 Min)
_WEEKLY_REPORT_DEDUPE_SEC = 8 * 86400         # TTL 8 Tage (Key zusaetzlich wochen-scoped)
_WEEKLY_REPORT_WINDOW_START_MIN = 16 * 60 + 15  # Freitag ab 16:15 ET (nach US-Close)
_WEEKLY_REPORT_WINDOW_END_MIN = 23 * 60         # bis 23:00 ET — danach verfaellt der Slot
_weekly_report_warned_missing = False


def _weekly_report_window_open(now_et):
    """True nur Freitag (ET-Wochentag 4) zwischen 16:15 und 23:00 ET."""
    if now_et.weekday() != 4:
        return False
    now_min = now_et.hour * 60 + now_et.minute
    return _WEEKLY_REPORT_WINDOW_START_MIN <= now_min < _WEEKLY_REPORT_WINDOW_END_MIN


def _weekly_report_dedupe_key(now_et):
    """Persistenter Wochen-Key: weekly_report_{ISO-Jahr}W{ISO-Woche}.

    Wochen-scoped => die 7-Tage-Prune-Grenze von _load_email_dedupe kann nie
    eine Doppel-Mail innerhalb derselben Woche freischalten (naechster
    Freitag = neue ISO-Woche = neuer Key).
    """
    iso = now_et.isocalendar()
    return f"weekly_report_{iso[0]}W{iso[1]:02d}"


def _build_weekly_report_mail(summary, now_et=None):
    """Baut (subject, body_html) der Wochen-Bilanz aus load_performance_summary(days=7).

    Hausstil wie _send_signal_update_mail (Arial, 700px, Tabellen). Subject
    OHNE Klassen-Praefix — das ℹ️ setzt _apply_mail_class_prefix beim Versand.
    Leere Woche => 'Keine Signale diese Woche'-Lebenszeichen statt Tabellen.
    """
    stamp = now_et if now_et is not None else datetime.now()
    total = summary.get("total") or {}

    def _i(bucket, key):
        try:
            return int(bucket.get(key) or 0)
        except (TypeError, ValueError):
            return 0

    def _hit_text(bucket):
        rate = bucket.get("win_rate_pct")
        return f"{float(rate):.0f}%" if isinstance(rate, (int, float)) else "–"

    n = _i(total, "signals")
    decided = _i(total, "tp1_hit") + _i(total, "tp2_hit") + _i(total, "stop_hit")
    sum_r = float(total.get("sum_r") or 0.0)
    avg_r = total.get("avg_r")
    avg_r_text = f"{float(avg_r):+.2f}R" if isinstance(avg_r, (int, float)) else "–"
    subject = (f"Wochenreport Signal-Tracker: {sum_r:+.1f}R | {n} Signale | "
               f"Hit-Rate {_hit_text(total)}")

    if n == 0:
        mid_html = """
    <p style="background:#f5f5f5;padding:12px;border-radius:4px;font-size:14px;color:#0f172a">
        <b>Keine Signale diese Woche.</b> Scanner und Tracker laufen — dies ist
        das woechentliche Lebenszeichen des Forward-Track-Records.
    </p>"""
    else:
        head_html = f"""
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:18px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Signale</th>
            <th style="padding:8px;text-align:left">Entschieden</th>
            <th style="padding:8px;text-align:left">Hit-Rate</th>
            <th style="padding:8px;text-align:left">Σ R</th>
            <th style="padding:8px;text-align:left">Ø R</th>
            <th style="padding:8px;text-align:left">Alerts/Tag</th>
            <th style="padding:8px;text-align:left">Offen</th>
        </tr>
        <tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{n}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{decided}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_hit_text(total)}</td>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{sum_r:+.1f}R</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{avg_r_text}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{float(total.get('alerts_per_day') or 0.0):.1f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(total, 'open')}</td>
        </tr>
    </table>"""

        scanner_rows = ""
        per_scanner = summary.get("per_scanner") or {}
        for scanner, bucket in sorted(
            per_scanner.items(),
            key=lambda item: float((item[1] or {}).get("sum_r") or 0.0),
            reverse=True,
        ):
            bucket = bucket or {}
            row_sum_r = float(bucket.get("sum_r") or 0.0)
            tint = "#e9f7ef" if row_sum_r >= 0 else "#fdecea"
            scanner_rows += f"""<tr style="background:{tint}">
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{html.escape(str(scanner))}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(bucket, 'signals')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(bucket, 'tp1_hit')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(bucket, 'tp2_hit')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(bucket, 'stop_hit')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_i(bucket, 'open')}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{_hit_text(bucket)}</td>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{row_sum_r:+.1f}R</b></td>
        </tr>"""

        recent_rows = ""
        for sig in (summary.get("recent") or [])[:10]:
            if not isinstance(sig, dict):
                continue
            ticker = html.escape(str(sig.get("ticker") or "?"))
            rec_scanner = html.escape(str(sig.get("scanner") or "?"))
            direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
            status = html.escape(str(sig.get("status") or "?"))
            if sig.get("tp1_hit_at") and "TP2" not in status:
                status += " (TP1✓)"
            r_realized = sig.get("r_realized")
            r_text = (f"{float(r_realized):+.2f}R"
                      if isinstance(r_realized, (int, float)) else "–")
            recent_rows += f"""<tr>
            <td style="padding:6px;border-bottom:1px solid #eee"><b>{ticker}</b> <span style="color:#999;font-size:11px">{direction}</span></td>
            <td style="padding:6px;border-bottom:1px solid #eee">{rec_scanner}</td>
            <td style="padding:6px;border-bottom:1px solid #eee">{status}</td>
            <td style="padding:6px;border-bottom:1px solid #eee">{r_text}</td>
        </tr>"""

        mid_html = f"""{head_html}
    <h3 style="color:#0f172a;font-size:14px">Je Scanner</h3>
    <table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:18px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Scanner</th>
            <th style="padding:8px;text-align:left">Signale</th>
            <th style="padding:8px;text-align:left">TP1</th>
            <th style="padding:8px;text-align:left">TP2</th>
            <th style="padding:8px;text-align:left">Stop</th>
            <th style="padding:8px;text-align:left">offen</th>
            <th style="padding:8px;text-align:left">Hit-Rate</th>
            <th style="padding:8px;text-align:left">Σ R</th>
        </tr>
        {scanner_rows}
    </table>
    <h3 style="color:#0f172a;font-size:14px">Letzte Signale</h3>
    <table style="width:100%;border-collapse:collapse;font-size:12px">
        <tr style="background:#f5f5f5">
            <th style="padding:6px;text-align:left">Ticker</th>
            <th style="padding:6px;text-align:left">Scanner</th>
            <th style="padding:6px;text-align:left">Status</th>
            <th style="padding:6px;text-align:left">R</th>
        </tr>
        {recent_rows}
    </table>"""

    sample_hint = ""
    if decided < 30:
        sample_hint = ("<br>Stichprobe noch klein — keine Schwellen-Entscheidungen "
                       "daraus ableiten.")
    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
    <h2 style="color:#0f172a">Wochenreport Signal-Tracker</h2>
    <p style="color:#666">{stamp.strftime('%d.%m.%Y %H:%M')} ET | KW {stamp.isocalendar()[1]} | Fenster: letzte 7 Tage</p>
    {mid_html}
    <p style="color:#999;font-size:12px;margin-top:20px">
        Forward-Track-Record: Signale wurden bei Versand fixiert und mit echten
        Kursen ausgewertet. Mechanik: 1R Risiko, TP1 = halb raus + Stop auf
        Einstand. Kein Backtest.{sample_hint}
    </p>
    </body></html>"""
    return subject, body_html


def _run_weekly_report(secrets=None, now_et=None):
    """Woechentliche ℹ️-Bilanz-Mail des Signal-Trackers (Freitag nach US-Close).

    Self-gated wie der ORB-Job: der Scheduler klopft alle 15 Min an, der Job
    prueft Fenster + Dedupe selbst.
      - Fenster: Freitag (ET) 16:15–23:00. Lief der Service um 16:15 nicht,
        wird innerhalb des Fensters nachgeholt; ausserhalb verfaellt der
        Slot (KEIN Nachschicken am Samstag).
      - Persistentes Dedupe weekly_report_{ISO-Jahr}W{ISO-Woche} (TTL 8 Tage)
        => genau EINE Mail pro Woche, Restart-sicher. Mark erst NACH
        erfolgreichem Versand (B2-Muster: SMTP-Fehler => Retry im Fenster).
      - Fehler im Report-Bau erreichen den Scheduler NIE (try/except + Log).

    Returns True nur bei tatsaechlich versendeter Mail.
    """
    global _weekly_report_warned_missing
    try:
        if now_et is None:
            try:
                from zoneinfo import ZoneInfo
                now_et = datetime.now(ZoneInfo("America/New_York"))
            except Exception:
                now_et = datetime.now()
        if not _weekly_report_window_open(now_et):
            return False
        if load_performance_summary is None:
            if not _weekly_report_warned_missing:
                log.warning("[Wochenreport] modules.signal_tracker fehlt — Report-Job inaktiv")
                _weekly_report_warned_missing = True
            return False
        dedupe_key = _weekly_report_dedupe_key(now_et)
        claim_now = time.time()
        if not _email_dedupe_claim(dedupe_key, _WEEKLY_REPORT_DEDUPE_SEC, now=claim_now):
            return False
        try:
            summary = load_performance_summary(days=7) or {}
            subject, body_html = _build_weekly_report_mail(summary, now_et=now_et)
            sent = _send_email_alert(
                subject, body_html,
                secrets if secrets is not None else _load_secrets(),
                mail_class="info",
            )
        except Exception:
            _email_dedupe_release(dedupe_key, claimed_at=claim_now)
            raise
        if sent:
            _email_dedupe_mark(dedupe_key, now=claim_now)
            log.info(f"[Wochenreport] 📧 Wochen-Bilanz versendet ({dedupe_key})")
        else:
            _email_dedupe_release(dedupe_key, claimed_at=claim_now)
            log.warning(f"[Wochenreport] Mail nicht versendet ({dedupe_key}) — "
                        f"Retry beim naechsten Takt im Fenster")
        return bool(sent)
    except Exception as exc:
        log.warning(f"[Wochenreport] Report fehlgeschlagen (Scheduler laeuft weiter): {exc}")
        return False


def _send_email_alert(subject, body_html, secrets, mail_class="trade", telegram_text=""):
    """Sendet E-Mail Alert via Gmail SMTP. Benötigt GMAIL_USER + GMAIL_APP_PASSWORD in secrets.toml

    mail_class (B6, mit api-Team abgestimmt): "trade" -> '🚨 JETZT: ',
    "watch" -> '👁️ WATCH: ', "info" -> 'ℹ️ ' als Betreff-Praefix.
    telegram_text (optional, gleiches Muster wie api): Kurztext für den
    Telegram-Spiegel; bei trade-Mails wird nach Erfolg zusätzlich Telegram
    benachrichtigt (sofern modules.notify_telegram konfiguriert ist).
    """
    subject = _apply_mail_class_prefix(subject, mail_class)
    if _email_has_blocked_etf_content(subject, body_html):
        log.warning(f"E-Mail Alert blockiert (ETF/ETP-Inhalt): {subject}")
        return False
    # Startup-Delay wie api: nach Restart 5 Min keine Mails (alte Cache-Daten
    # wuerden Phantom-Alerts / Restart-Spam erzeugen).
    if time.time() - _BG_STARTED_AT < _BG_STARTUP_MAIL_DELAY:
        log.info(f"E-Mail Alert unterdrueckt (Startup-Delay {_BG_STARTUP_MAIL_DELAY}s): {subject}")
        return False
    gmail_user = secrets.get("GMAIL_USER", "")
    gmail_pass = secrets.get("GMAIL_APP_PASSWORD", "")
    alert_to = secrets.get("ALERT_EMAIL", gmail_user)  # Default: an sich selbst
    operator_watch_optin = str(
        secrets.get(
            "ALERT_OPERATOR_WATCH_OPTIN",
            os.environ.get("ALERT_OPERATOR_WATCH_OPTIN", "0"),
        )
    ).strip().lower() in {"1", "true", "yes", "on"}
    operator_recipients = [addr.strip().lower() for addr in str(alert_to).split(",") if addr.strip()]
    recipients = operator_recipients if mail_class != "watch" or operator_watch_optin else []
    send_to_subscribers = str(secrets.get("ALERT_SEND_TO_SUBSCRIBERS", os.environ.get("ALERT_SEND_TO_SUBSCRIBERS", "1"))).strip().lower() not in {"0", "false", "no", "off"}
    if send_to_subscribers and HAS_AUTH_ALERT_RECIPIENTS and get_email_alert_recipients:
        try:
            # B7: swing-Horizont wie api; mail_class nur defensiv via
            # TypeError-Fallback mitgeben (api-Team rollt den Parameter parallel aus).
            try:
                recipients.extend(get_email_alert_recipients(trade_horizon="swing", mail_class=mail_class))
            except TypeError:
                recipients.extend(get_email_alert_recipients(trade_horizon="swing"))
        except Exception as exc:
            log.warning(f"Subscriber-Alert-Empfaenger konnten nicht geladen werden: {exc}")
    recipients = sorted(set(addr for addr in recipients if "@" in addr))

    if not gmail_user or not gmail_pass:
        log.warning("⚠️ E-Mail Alert: GMAIL_USER oder GMAIL_APP_PASSWORD fehlt in secrets.toml")
        return False
    if not recipients:
        log.warning("E-Mail Alert: ALERT_EMAIL/GMAIL_USER Empfaenger fehlt")
        return False

    # B-03: Retry logic with exponential backoff
    max_retries = 3
    for attempt in range(max_retries):
        try:
            msg = MIMEMultipart("alternative")
            msg["From"] = f"TradingBot Alert <{gmail_user}>"
            msg["To"] = ", ".join(recipients)
            msg["Subject"] = subject
            body_html = body_html + "<p style='color:#999;font-size:11px;margin-top:18px'>Automatischer Analyse-Alert. Keine Anlageberatung, keine Kauf-/Verkaufsempfehlung. Trading erfolgt eigenverantwortlich.</p>"

            # Plain-Text Fallback
            plain = body_html.replace("<br>", "\n").replace("</tr>", "\n")
            import re
            plain = re.sub(r"<[^>]+>", "", plain)
            msg.attach(MIMEText(plain, "plain", "utf-8"))
            msg.attach(MIMEText(body_html, "html", "utf-8"))

            with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=15) as server:
                server.login(gmail_user, gmail_pass)
                server.sendmail(gmail_user, recipients, msg.as_string())

            log.info(f"📧 E-Mail Alert gesendet: {subject}")
            # Telegram-Spiegel nur für trade-Mails, nur nach Mail-Erfolg;
            # subject ist hier bereits der finale (geprefixte) Betreff.
            _send_telegram_companion(subject, mail_class, telegram_text)
            return True
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                log.warning(f"⚠️ E-Mail Fehler (Versuch {attempt+1}/{max_retries}): {e}, warte {wait_time}s...")
                time.sleep(wait_time)
            else:
                log.error(f"❌ E-Mail Fehler nach {max_retries} Versuchen: {e}")
                return False


def _alert_cache_path(scanner_name):
    """Return the scanner-owner cache consumed by the mail worker.

    The lookup is isolated behind a function so tests and non-Linux runtimes
    can use a private cache without patching file I/O globally.
    """
    if scanner_name.startswith("bi_"):
        direction = "long" if "long" in scanner_name else "short"
        return f"/tmp/bi_cache_{direction}.json"
    return {
        "biotech": "/tmp/alpha_biotech_cache.json",
        "orb": "/tmp/orb_scan_results.json",
    }.get(scanner_name)


def _check_and_alert_scan_results(scanner_name, secrets, trade_horizon="swing"):
    """Prüft Scan-Ergebnisse auf Grade S/A und sendet E-Mail Alert"""
    now = time.time()
    claimed_alerts = []
    mail_sent = False
    default_direction = "long" if scanner_name == "bi_long" else "short" if scanner_name == "bi_short" else ""
    trade_horizon = str(trade_horizon or "swing").strip().lower()
    swing_mode = trade_horizon != "intraday"

    cache_file = _alert_cache_path(scanner_name)
    if not cache_file:
        return

    try:
        if not os.path.exists(cache_file):
            return
        with open(cache_file, "r") as f:
            try:
                data = json.load(f)
            except json.JSONDecodeError:
                log.debug(f"Corrupt cache file, skipping: {cache_file}")
                return

        results = data.get("results", [])
        if not results:
            return

        # N (Audit 10.06.2026): Frische-Gate — bg mailt NIE aus einem stale
        # Cache (> _ALERT_CACHE_MAX_AGE_S). Waere der Scan-Owner erneut tot
        # (vgl. K-1), duerfen alte Treffer keine JETZT-Trade-Mails ausloesen.
        # Zeitquelle: timestamp/cached_at aus dem Cache, Datei-mtime als
        # Fallback (nicht jeder Writer schreibt ein timestamp-Feld).
        cache_ts = _safe_float(data.get("timestamp"), 0)
        if not cache_ts:
            try:
                cache_ts = datetime.fromisoformat(str(data.get("cached_at", ""))).timestamp()
            except Exception:
                cache_ts = 0
        if not cache_ts:
            try:
                cache_ts = os.path.getmtime(cache_file)
            except Exception:
                cache_ts = 0
        cache_age_s = (now - cache_ts) if cache_ts else None
        if cache_age_s is None or cache_age_s > _ALERT_CACHE_MAX_AGE_S:
            age_txt = f"{cache_age_s / 3600.0:.1f}h" if cache_age_s is not None else "unbekannt"
            log.info(f"[Alert] {scanner_name}: Cache stale (Alter {age_txt} > "
                     f"{_ALERT_CACHE_MAX_AGE_S // 3600}h) — keine Mails aus altem Cache")
            return

        # Filter: Nur Grade S oder A
        alerts = []
        # Signal-Tracking: parallele Liste der ORIGINAL-Rows (inkl. Entry/Stop/TP)
        # der tatsächlich gemailten Ticker — die alerts-Dicts sind Mail-Aufbereitung
        # ohne Level-Zahlen (nur trade_plan_html) und für den Tracker unbrauchbar.
        alert_source_rows = []
        suppressed = {}  # Q3/B4: Audit-Dict je Lauf (geblockt wegen health/estimated/rvol/...)

        def _suppress(reason):
            suppressed[reason] = suppressed.get(reason, 0) + 1

        for r in results:
            ticker = r.get("ticker", r.get("Ticker", ""))
            grade = r.get("BI_Grade", r.get("Grade", r.get("rating", "")))
            score = r.get("BI_Score", r.get("Score", r.get("score", 0)))
            score_num = _safe_float(score, 0)

            # Grade-Check: S, A, A+ für alle Scanner
            is_top_grade = grade in ("S", "A", "A+")
            if not is_top_grade:
                continue
            if score_num < _ALERT_MIN_SCORE:
                _suppress("score_below_alert_threshold")
                continue
            # Q3/B4: RVOL-Floor wie api (0.7; None => Block). bi/biotech sind
            # Pre-Breakout-Scanner — der 1.5er-Breakout-Floor (AUDIT S-1) gilt nur
            # fuer Breakout-/Momentum-Strategien im api-Pfad, nicht hier.
            rvol_num = _safe_float(r.get("RVOL", r.get("rvol")), None)
            if scanner_name in _BG_ALERT_RVOL_GUARD_SCANNERS and (rvol_num is None or rvol_num < _ALERT_MIN_RVOL):
                _suppress("rvol_below_alert_threshold")
                log.debug(f"Alert suppressed by RVOL floor: {scanner_name} {ticker} rvol={rvol_num}")
                continue
            if scanner_name == "bi_short" and _bearish_stock_alert_active(ticker, now=now):
                _suppress("bearish_ticker_already_alerted")
                log.debug(f"BI short alert suppressed by bearish ticker dedupe: {ticker}")
                continue
            if scanner_name == "bi_short" and ticker:
                r = dict(r)
                if swing_mode:
                    r["bear_entry_quality"] = "SWING_SETUP"
                    _bear_reasons = _stock_swing_short_rule_reasons(r)
                else:
                    if "latest_bar_change_pct" not in r:
                        r.update(_fetch_bear_latest_intraday_state(ticker, secrets.get("POLYGON_KEY", "")))
                    r["bear_entry_quality"] = _bear_entry_quality(r)
                    _bear_reasons = _bear_short_rule_reasons(r)
                if _bear_reasons:
                    _suppress("short_timing_guard")
                    log.debug(f"BI short alert suppressed by timing guard: {ticker} {_bear_reasons}")
                    continue
            if scanner_name in _LONG_ENTRY_ALERT_SCANNERS and ticker:
                r = dict(r)
                if swing_mode:
                    r["long_entry_quality"] = "SWING_SETUP"
                    long_reasons = _stock_swing_rule_reasons(r)
                else:
                    if "latest_bar_change_pct" not in r:
                        r.update(_fetch_long_latest_intraday_state(ticker, secrets.get("POLYGON_KEY", "")))
                    r["long_entry_quality"] = _long_entry_quality(r)
                    long_reasons = _long_entry_rule_reasons(r)
                r["alertable_long"] = not long_reasons
                if not r["alertable_long"]:
                    _suppress("long_timing_guard")
                    log.debug(f"Long alert suppressed by timing guard: {ticker} {r.get('long_entry_quality')} {r.get('latest_bar_change_pct')}")
                    continue
            # Q3/B4: Trade-Plan-Gates wie api — valid + nicht estimated + rr >= 1.0.
            if not _alert_trade_plan_ok(r):
                _suppress("trade_plan_quality_gate")
                log.debug(f"Alert suppressed by trade-plan quality gate: {scanner_name} {ticker}")
                continue
            # Q3/B4: Health-Gate (zentral in modules.trade_health) — Stop-Breach,
            # Chase/Entry-Zone, live R:R; mailbar nur TRADEABLE + health_score >= 80.
            health_reasons = _bg_alert_health_reasons(r, scanner_name)
            if health_reasons:
                for hreason in health_reasons:
                    _suppress(hreason)
                log.debug(f"Alert suppressed by health gate: {scanner_name} {ticker} {health_reasons}")
                continue
            # H-2 (Audit 10.06.2026): HARTES Gate (Paritaet zu api) — biotech-
            # Rows vor binaerem Event (<= T-3) sind NIE ein JETZT-Trade-Call:
            # das Gap durchschlaegt jeden Stop. Run-up = Beobachtung/Exit.
            if scanner_name == "biotech" and _biotech_near_binary_event(r):
                _suppress("near_binary_event")
                log.debug(f"Alert suppressed by binary event gate: {ticker}")
                continue

            # B2: In-Memory-Cooldown + geteiltes persistentes Dedupe (gleiche Datei
            # und gleiches Key-Format wie api: f"{scanner_name}_{ticker}").
            cooldown_key = f"{scanner_name}_{ticker}"
            dedupe_ttl = _alert_dedupe_ttl_seconds(scanner_name)
            if cooldown_key in _EMAIL_COOLDOWN:
                if now - _EMAIL_COOLDOWN[cooldown_key] < dedupe_ttl:
                    _suppress("cooldown_active")
                    continue
            if _email_dedupe_active(cooldown_key, dedupe_ttl, now=now):
                _suppress("persistent_dedupe_active")
                continue

            alerts.append({
                "ticker": ticker,
                "grade": grade,
                "score": score,
                "price": r.get("Preis", r.get("current", 0)),
                "direction": r.get("direction") or default_direction,
                "name": r.get("Name", r.get("name", "")),
                "rvol": rvol_num if rvol_num is not None else 0,
                "entry_quality": r.get("long_entry_quality", r.get("bear_entry_quality", "")),
                "trade_plan_html": _format_alert_plan_html(r),
                "cooldown_key": cooldown_key,
                "source_row": r,
            })
            alert_source_rows.append(r)

        # Q3/B4: Suppression-Audit — eine Log-Zeile pro Scan-Lauf (wie api).
        if suppressed:
            log.info(f"[Alert] {scanner_name}: suppressed={suppressed}")

        if not alerts:
            return

        claimed_alerts = [
            alert for alert in alerts
            if _email_dedupe_claim(
                alert["cooldown_key"],
                _alert_dedupe_ttl_seconds(scanner_name),
                now=now,
            )
        ]
        alerts = claimed_alerts
        alert_source_rows = [alert["source_row"] for alert in alerts]
        if not alerts:
            return

        # E-Mail bauen
        scanner_labels = {
            "bi_long": "BI Scanner Long",
            "bi_short": "BI Scanner Short",
            "biotech": "Biotech Scanner",
            "orb": "ORB Scanner",
        }
        label = scanner_labels.get(scanner_name, scanner_name)
        n = len(alerts)
        subject = f"🚨 {n} Top-Setup{'s' if n > 1 else ''} — {label}"

        rows = ""
        for a in alerts:
            emoji = "🏆" if a["grade"] == "S" else "🔥"
            # B6: neutraler Fallback bei leerer Richtung statt faelschlich '⬇️ SHORT'.
            direction_text = str(a.get("direction", "")).lower()
            dir_label = "⬆️ LONG" if "long" in direction_text else ("⬇️ SHORT" if "short" in direction_text else "")
            rows += f"""<tr>
                <td style="padding:8px;border-bottom:1px solid #eee"><b>{a['ticker']}</b></td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a.get('name', '')[:25]}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{emoji} {a['grade']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a['score']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{_format_alert_price(a['price'])}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{dir_label}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a['rvol']:.1f}x</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a['trade_plan_html']}</td>
                <td style="padding:8px;border-bottom:1px solid #eee">{a.get('entry_quality', '')}</td>
            </tr>"""

        body_html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
        <h2 style="color:#1a73e8">🚨 TradingBot Alert — {label}</h2>
        <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} starke Setups gefunden</p>
        <table style="width:100%;border-collapse:collapse;font-size:14px">
            <tr style="background:#f5f5f5">
                <th style="padding:8px;text-align:left">Ticker</th>
                <th style="padding:8px;text-align:left">Name</th>
                <th style="padding:8px;text-align:left">Grade</th>
                <th style="padding:8px;text-align:left">Score</th>
                <th style="padding:8px;text-align:left">Preis</th>
                <th style="padding:8px;text-align:left">Richtung</th>
                <th style="padding:8px;text-align:left">RVOL</th>
                <th style="padding:8px;text-align:left">Entry / Stop / TP</th>
                <th style="padding:8px;text-align:left">Timing</th>
            </tr>
            {rows}
        </table>
        <p style="color:#999;font-size:12px;margin-top:20px">
            Automatischer Alert vom TradingBot Background Service.<br>
            Mail ab Score >= {_ALERT_MIN_SCORE}; {int(_alert_dedupe_ttl_seconds(scanner_name) // 3600)}h Cooldown pro Ticker (persistent).<br>
            Grade S = Score 85 + 4 Smart-Money-Fires | A = 71 + 3 Fires (V3.3-Leiter)
        </p>
        </body></html>"""

        # telegram_text-Param TypeError-tolerant mitgeben (B7-Muster: api-Team
        # rollt denselben Parameter parallel aus; alte Mocks/Signaturen ohne
        # telegram_text bleiben funktionsfähig).
        try:
            sent = _send_email_alert(subject, body_html, secrets, mail_class="trade",
                                     telegram_text=_format_telegram_text(alert_source_rows))
        except TypeError:
            sent = _send_email_alert(subject, body_html, secrets, mail_class="trade")
        if sent:
            mail_sent = True
            # B2: Cooldown + persistentes Dedupe NUR bei erfolgreichem Versand setzen.
            for alert in alerts:
                _EMAIL_COOLDOWN[alert["cooldown_key"]] = now
                _email_dedupe_mark(alert["cooldown_key"], now=now)
                if scanner_name == "bi_short":
                    _mark_bearish_stock_alert(alert["ticker"], now=now)
            # Signal-Tracking NUR nach erfolgreichem Versand (wirft nie).
            _record_alert_signals_safe(scanner_name, alert_source_rows,
                                       mail_class="trade", channel="email")
        else:
            for alert in claimed_alerts:
                _email_dedupe_release(alert["cooldown_key"], claimed_at=now)

    except Exception as e:
        if not mail_sent:
            for alert in claimed_alerts:
                _email_dedupe_release(alert.get("cooldown_key", ""), claimed_at=now)
        log.error(f"⚠️ Alert-Check {scanner_name}: {e}")


# ── Cache / Status ──
def cache_write(name, data):
    cache_file = DATA_DIR / f"{name}.json"
    meta = {"updated_at": datetime.now().isoformat(), "updated_ts": time.time(), "scanner": name, "data": data}
    try:
        _atomic_write_json(str(cache_file), meta)
        log.info(f"✅ {name} → Cache geschrieben")
    except Exception as e:
        log.error(f"❌ Cache-Write {name}: {e}")


def cache_age(name):
    cache_file = DATA_DIR / f"{name}.json"
    if not cache_file.exists():
        return None
    try:
        with open(cache_file, "r") as f:
            meta = json.load(f)
        return time.time() - meta.get("updated_ts", 0)
    except Exception:
        return None


def _update_status(scanner_name, status, detail=""):
    # B-02: File locking to prevent race condition
    try:
        # Ensure file exists
        if not STATUS_FILE.exists():
            STATUS_FILE.write_text("{}")

        with open(STATUS_FILE, "r+") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            try:
                existing = json.load(f)
            except (json.JSONDecodeError, ValueError):
                existing = {}

            existing[scanner_name] = {"status": status, "detail": detail, "ts": datetime.now().isoformat()}
            existing["_service"] = {
                "running": True, "pid": os.getpid(),
                "started": existing.get("_service", {}).get("started", datetime.now().isoformat()),
                "last_activity": datetime.now().isoformat(),
            }
            f.seek(0)
            f.truncate()
            json.dump(existing, f, default=str)
            fcntl.flock(f, fcntl.LOCK_UN)
    except Exception as e:
        log.debug(f"Non-critical error in _update_status: {e}")


# ══════════════════════════════════════════════════════════════
# SCANNER FUNKTIONEN
# ══════════════════════════════════════════════════════════════

def _fetch_crash_monitor(poly_key):
    """Crash Monitor: SPY + VIX + Sektoren + Safe Havens + Credit + Breadth"""
    log.info("🔴 Crash Monitor...")
    _update_status("crash_monitor", "fetching")
    from modules.data_fetchers import rate_limited_get

    try:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=400)
        result = {"spy": {}, "vix": {}, "sectors": [], "breadth": {}, "signals": [], "fear_score": 0}
        fear = 0

        # SPY
        url = f"https://api.polygon.io/v2/aggs/ticker/SPY/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
        resp = rate_limited_get(url, params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=15)

        if resp.status_code == 200:
            bars = resp.json().get("results", [])
            if bars and len(bars) >= 50:
                closes = [b["c"] for b in bars]
                volumes = [b["v"] for b in bars]
                highs = [b["h"] for b in bars]
                lows = [b["l"] for b in bars]
                current = closes[-1]
                prev_close = closes[-2]

                sma20 = sum(closes[-20:]) / 20
                sma50 = sum(closes[-50:]) / 50
                sma200 = sum(closes[-200:]) / 200 if len(closes) >= 200 else None

                # RSI
                rsi_data = closes[-100:]
                gains, losses_l = [], []
                for i in range(1, 15):
                    d = rsi_data[i] - rsi_data[i-1]
                    gains.append(max(0, d)); losses_l.append(max(0, -d))
                ag = sum(gains)/14; al = sum(losses_l)/14
                for i in range(15, len(rsi_data)):
                    d = rsi_data[i] - rsi_data[i-1]
                    ag = (ag*13 + max(0, d))/14; al = (al*13 + max(0, -d))/14
                rsi = 100 - (100 / (1 + ag/max(0.001, al)))

                high_252 = max(highs[-252:]) if len(highs) >= 252 else max(highs)
                drawdown = ((current - high_252) / high_252) * 100

                chg_5d = ((closes[-1]-closes[-6])/closes[-6])*100 if len(closes)>=6 else 0
                chg_20d = ((closes[-1]-closes[-21])/closes[-21])*100 if len(closes)>=21 else 0

                vol_avg20 = sum(volumes[-20:]) / 20
                down_vol = sum(volumes[i] for i in range(-20, 0) if closes[i] < closes[i-1])
                total_vol = sum(volumes[-20:])
                sell_pressure = down_vol / max(1, total_vol)

                # Fear Score
                if drawdown <= -20: fear += 25
                elif drawdown <= -10: fear += 18
                elif drawdown <= -5: fear += 12
                elif drawdown <= -3: fear += 7
                if current < sma50: fear += 8
                if sma200 and current < sma200: fear += 12
                if rsi <= 30: fear += 10
                elif rsi <= 40: fear += 6
                if sell_pressure > 0.65: fear += 8
                if chg_5d <= -5: fear += 6
                elif chg_5d <= -2: fear += 3

                result["spy"] = {
                    "price": round(current, 2), "change_pct": round((current-prev_close)/prev_close*100, 2),
                    "sma50": round(sma50, 2), "sma200": round(sma200, 2) if sma200 else None,
                    "rsi": round(rsi, 1), "drawdown": round(drawdown, 1),
                    "chg_5d": round(chg_5d, 2), "chg_20d": round(chg_20d, 2),
                    "sell_pressure": round(sell_pressure*100, 1),
                }

        # VIX
        for vix_etf in ["UVXY", "VIXY"]:
            try:
                vr = rate_limited_get(
                    f"https://api.polygon.io/v2/aggs/ticker/{vix_etf}/range/1/day/{(end_date-timedelta(days=60)).strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}",
                    params={"adjusted": "true", "sort": "asc", "apiKey": poly_key}, timeout=10)
                if vr.status_code == 200:
                    vb = vr.json().get("results", [])
                    if vb and len(vb) >= 10:
                        vc = vb[-1]["c"]; va20 = sum(b["c"] for b in vb[-20:])/20
                        spike = vc/max(0.01, va20)
                        result["vix"] = {"ticker": vix_etf, "price": round(vc, 2), "spike_ratio": round(spike, 2)}
                        if spike > 1.5: fear += 12
                        elif spike > 1.2: fear += 7
                        break
            except Exception as e:
                log.debug(f"Non-critical error: {e}")

        # Breadth
        try:
            snap = rate_limited_get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                                     params={"apiKey": poly_key}, timeout=30)
            if snap.status_code == 200:
                tickers = snap.json().get("tickers", [])
                adv = dec = 0
                for t in tickers:
                    td = t.get("todaysChangePerc", 0) or 0
                    if td > 0: adv += 1
                    elif td < 0: dec += 1
                ad_ratio = adv / max(1, dec)
                result["breadth"] = {"advancing": adv, "declining": dec, "ad_ratio": round(ad_ratio, 2)}
                if ad_ratio < 0.4: fear += 12
                elif ad_ratio < 0.6: fear += 8
                elif ad_ratio < 0.8: fear += 4
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        result["fear_score"] = min(100, fear)
        cache_write("crash_monitor", result)
        _update_status("crash_monitor", "ok", f"Fear: {fear}/100")
        log.info(f"  Fear Score: {fear}/100")
        return result
    except Exception as e:
        log.error(f"❌ Crash Monitor: {e}")
        _update_status("crash_monitor", "error", str(e))
        return None


def _bg_run_bi_scan(direction, secrets, candidates=None):
    """K-1 (Audit 10.06.2026): Testbare Kapselung des ECHTEN BI-Scans.

    Delegiert an modules.scanners._bi_background_scan(poly_key, direction,
    candidates) — denselben Code, den auch der Streamlit-/api-Pfad nutzt
    (Schwelle 45/40, Struktur-Level, Geometrie-Gates). Die Funktion schreibt
    Cache (/tmp/bi_cache_{direction}.json) und Progress-File
    (/tmp/bi_scan_progress_{direction}.json) selbst via _bi_cache_save /
    _bi_progress_write.

    candidates: vorgefilterte Kandidaten-Liste (Dicts mit "Ticker"-Key oder
    Strings — beide Formen akzeptiert _bi_background_scan). None => die
    Funktion laedt das volle Polygon-Universe selbst (Netz!).
    """
    from modules import scanners as _scanners_mod
    poly_key = (secrets or {}).get("POLYGON_KEY", "")
    _scanners_mod._bi_background_scan(poly_key, direction=direction, candidates=candidates)


def _run_bi_scanner(poly_key, direction="long"):
    """BI Scanner via Polygon Snapshot → _bi_background_scan"""
    scanner_name = f"bi_{direction}"
    cache_file = _SCAN_CACHE_MAP[scanner_name]
    previous_revision, _ = _scanner_cache_snapshot(cache_file)
    started_at = time.time()
    _clear_scan_cache(f"bi_{direction}")
    label = "BI Long" if direction == "long" else "Bear Short"
    log.info(f"🔮 {label} Scanner...")
    _update_status(f"bi_{direction}", "fetching")

    import requests as req

    try:
        # 1) Polygon Snapshot
        snap = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                        params={"apiKey": poly_key}, timeout=30)
        if snap.status_code != 200:
            log.error(f"  Polygon HTTP {snap.status_code}")
            _update_status(f"bi_{direction}", "error", f"HTTP {snap.status_code}")
            raise RuntimeError(f"Polygon Snapshot HTTP {snap.status_code}")

        tickers = snap.json().get("tickers", [])
        log.info(f"  {len(tickers)} Aktien geladen")

        # 2) Basis-Daten extrahieren
        raw = []
        for t in tickers:
            try:
                lt = t.get("lastTrade", {}) or {}
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = lt.get("p") or day.get("c") or day.get("vw") or prev.get("c") or 0
                if not price or price <= 0: continue
                change_pct = t.get("todaysChangePerc") or 0
                vol = day.get("v") or 0
                prev_vol = prev.get("v") or 0
                # Wenn Markt zu (vol=0), nutze prevDay Volume
                effective_vol = vol if vol > 0 else prev_vol
                rvol = vol / prev_vol if prev_vol > 0 and vol > 0 else (1.0 if prev_vol > 0 else 0)
                dollar_vol = price * effective_vol
                raw.append({
                    "Ticker": t.get("ticker", ""), "Name": t.get("name", "") or "",
                    "Preis": round(price, 2), "Change%": round(change_pct, 2),
                    "RVOL": round(rvol, 2), "Volume": vol, "DollarVol": dollar_vol,
                })
            except Exception as e:
                log.debug(f"Non-critical error: {e}")
                continue

        # 3) CS-Whitelist
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                with open(cs_file, "r") as f:
                    cs_set = set(json.load(f))
                log.info(f"  CS-Liste aus Cache: {len(cs_set)} Ticker")
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        if not cs_set:
            try:
                from modules.data_fetchers import rate_limited_get
                url = "https://api.polygon.io/v3/reference/tickers"
                params = {"type": "CS", "market": "stocks", "active": "true", "limit": 1000, "apiKey": poly_key}
                next_url = None
                for _ in range(20):
                    if next_url:
                        resp = rate_limited_get(next_url, timeout=30)
                    else:
                        resp = rate_limited_get(url, params=params, timeout=30)
                    data = resp.json()
                    for r in data.get("results", []):
                        cs_set.add(r.get("ticker", "").upper())
                    next_url = data.get("next_url")
                    if next_url:
                        next_url = f"{next_url}&apiKey={poly_key}"
                    else:
                        break
                if cs_set:
                    _atomic_write_json(cs_file, list(cs_set))
                    log.info(f"  CS-Liste von API: {len(cs_set)} Ticker")
            except Exception as e:
                log.warning(f"  CS-Liste Fehler: {e}")

        # 4) Filter
        if direction == "long":
            # Long: Alle liquiden CS-Aktien
            filtered = [s for s in raw if s.get("Preis", 0) >= 5
                        and s.get("DollarVol", 0) >= 200_000
                        and (not cs_set or s.get("Ticker", "").upper() in cs_set)]
        else:
            # Short: Stärkerer Downtrend ODER hohes Volume bei Schwäche
            # Verschärft: Change <= -2% ODER (RVOL >= 1.8 UND Change <= -1%)
            filtered = [s for s in raw if s.get("Preis", 0) >= 5
                        and s.get("DollarVol", 0) >= 500_000
                        and (not cs_set or s.get("Ticker", "").upper() in cs_set)
                        and (s.get("Change%", 0) <= -2.0
                             or (s.get("RVOL", 0) >= 1.8 and s.get("Change%", 0) <= -1.0))]

        log.info(f"  {len(filtered)} Kandidaten nach Filter")

        if not filtered:
            now_ts = time.time()
            _atomic_write_json(cache_file, {
                "cached_at": datetime.now().isoformat(),
                "timestamp": now_ts,
                "direction": direction,
                "partial": False,
                "checked": 0,
                "total": 0,
                "detail": f"0 Kandidaten von {len(raw)}",
                "count": 0,
                "results": [],
            })
            progress_file = f"/tmp/bi_scan_progress_{direction}.json"
            _atomic_write_json(progress_file, {
                "status": "done",
                "direction": direction,
                "checked": 0,
                "total": 0,
                "hits": 0,
                "detail": f"0 Kandidaten von {len(raw)}",
                "timestamp": now_ts,
            })
            _require_final_scanner_publish(
                scanner_name, cache_file, previous_revision, progress_file,
                started_at, direction=direction,
            )
            _update_status(scanner_name, "ok", f"0 Kandidaten von {len(raw)}")
            return []

        # 5) Progress-Datei schreiben damit Streamlit-UI den Fortschritt sieht
        progress_file = f"/tmp/bi_scan_progress_{direction}.json"
        _atomic_write_json(progress_file, {"status": "running", "checked": 0, "total": len(filtered),
                       "hits": 0, "detail": f"{len(filtered)} Kandidaten", "timestamp": time.time()})

        # 6) Analyse starten — ECHTER Scan-Pfad: modules.scanners._bi_background_scan
        # (K-1-Fix 10.06.2026: vorher Import einer nie existierenden *_standalone-
        # Funktion => ImportError => divergenter Direkt-Fallback mit Schwelle 85/75
        # + falschem Level-Modell, der per MACD-TypeError crashte und stuendlich
        # results=[] schrieb. Fallback ersatzlos geloescht.)
        # _bi_background_scan schreibt Cache + Progress-File selbst.
        _bg_run_bi_scan(direction, {"POLYGON_KEY": poly_key}, candidates=filtered)

        payload = _require_final_scanner_publish(
            scanner_name,
            cache_file,
            previous_revision,
            f"/tmp/bi_scan_progress_{direction}.json",
            started_at,
            direction=direction,
        )

        _update_status(f"bi_{direction}", "ok", f"Scan abgeschlossen")
        return payload.get("results", [])

    except Exception as e:
        log.error(f"❌ {label}: {e}\n{traceback.format_exc()}")
        _update_status(f"bi_{direction}", "error", str(e))
        raise


def _run_bear_scanner(poly_key, secrets):
    """
    V2.8: Bear Scanner im Background Service — findet Crash-Kandidaten und sendet Alerts.
    Nutzt Polygon /losers Endpoint + History für Score/Grade.
    """
    _clear_scan_cache("bear_scan")
    import requests as req
    log.info("Bear Scanner (bg_service)...")
    _update_status("bear_scan", "running")
    crash_dedupe_keys = []
    crash_summary_key = ""
    crash_mail_sent = False

    try:
        # 1) Polygon Losers Endpoint
        snap_resp = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/losers",
                           params={"apiKey": poly_key, "limit": 250}, timeout=30)
        if snap_resp.status_code != 200:
            log.error(f"  Losers HTTP {snap_resp.status_code}")
            _update_status("bear_scan", "error", f"HTTP {snap_resp.status_code}")
            return

        tickers = snap_resp.json().get("tickers", [])
        log.info(f"  {len(tickers)} Losers geladen")

        common_stock_universe, common_stock_source = _load_common_stock_universe(poly_key)
        excluded_non_common = 0

        losers = []
        for t in tickers:
            try:
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = day.get("c", 0) or (t.get("lastTrade", {}) or {}).get("p", 0)
                prev_close = prev.get("c", 0)
                if not price or not prev_close or price < 3:
                    continue
                vol = day.get("v", 0)
                dollar_vol = price * vol
                if dollar_vol < 300_000:
                    continue
                chg_pct = ((price - prev_close) / prev_close) * 100
                if chg_pct > -3:
                    continue
                day_open = day.get("o", 0) or prev_close
                day_high = day.get("h", 0) or max(price, day_open)
                day_low = day.get("l", 0) or min(price, day_open)
                open_to_current_pct = ((price - day_open) / day_open * 100) if day_open else None
                close_pos = ((price - day_low) / (day_high - day_low)) if day_high > day_low else 0.5

                ticker_sym = t.get("ticker", "")
                _tk_up = ticker_sym.upper()
                non_stock_reason = _stock_alert_asset_exclusion_reason(
                    _tk_up,
                    common_stock_universe=common_stock_universe,
                    universe_source=common_stock_source,
                )
                if non_stock_reason:
                    excluded_non_common += 1
                    continue

                # History für RVOL + MA
                rvol = 0
                ma20_dist = 0
                has_history = False
                try:
                    url = f"https://api.polygon.io/v2/aggs/ticker/{ticker_sym}/range/1/day/2024-01-01/2099-12-31"
                    resp = rate_limited_get(url, params={"apiKey": poly_key, "limit": 60, "sort": "desc"}, timeout=10)
                    if resp.status_code == 200:
                        bars = resp.json().get("results", [])
                        if len(bars) >= 21:
                            has_history = True
                            ma20 = sum(b.get("c", 0) for b in bars[1:21]) / 20
                            ma20_dist = round((price - ma20) / ma20 * 100, 2) if ma20 > 0 else 0
                            avg_vol = sum(b.get("v", 0) for b in bars[1:21]) / min(20, len(bars) - 1)
                            rvol = round(vol / avg_vol, 2) if avg_vol > 0 else 0
                except Exception:
                    pass

                if not has_history:
                    continue

                # Scoring (0-100)
                score = 0
                abs_chg = abs(chg_pct)
                if abs_chg >= 15: score += 25
                elif abs_chg >= 10: score += 20
                elif abs_chg >= 6: score += 15
                elif abs_chg >= 4: score += 10
                else: score += 5

                if rvol >= 3.0: score += 20
                elif rvol >= 2.0: score += 15
                elif rvol >= 1.5: score += 10
                elif rvol >= 1.0: score += 5

                if ma20_dist < -10: score += 20
                elif ma20_dist < -5: score += 15
                elif ma20_dist < -2: score += 10
                elif ma20_dist < 0: score += 5
                else: score -= 5

                if dollar_vol >= 10_000_000: score += 10
                elif dollar_vol >= 5_000_000: score += 7
                elif dollar_vol >= 1_000_000: score += 4
                else: score += 1

                if 10 <= price <= 200: score += 10
                elif 5 <= price < 10: score += 5
                elif price > 200: score += 7

                if score >= 80: grade = "S"
                elif score >= 65: grade = "A"
                elif score >= 50: grade = "B"
                elif score >= 35: grade = "C"
                else: grade = "D"

                bear_row = {
                    "ticker": ticker_sym, "price": round(price, 2),
                    "change_pct": round(chg_pct, 2), "volume": vol,
                    "dollar_volume": round(dollar_vol, 0), "rvol": rvol,
                    "ma20_dist": ma20_dist, "score": score, "grade": grade,
                    "direction": "SHORT",
                    "open_to_current_pct": round(open_to_current_pct, 2) if open_to_current_pct is not None else None,
                    "close_pos": round(close_pos, 3),
                    "DayHigh": round(day_high, 4) if day_high else None,
                    "DayLow": round(day_low, 4) if day_low else None,
                    "asset_check": "common_stock",
                }
                if score >= 55:
                    bear_row.update(_fetch_bear_latest_intraday_state(ticker_sym, poly_key))
                bear_row["short_block_reasons"] = _bear_short_rule_reasons(bear_row)
                bear_row["entry_quality"] = _bear_entry_quality(bear_row)
                bear_row["alertable_short"] = not bear_row["short_block_reasons"]
                bear_row["crash_alert_ok"] = _bear_crash_alert_ok(bear_row)
                losers.append(bear_row)
            except Exception:
                continue

        losers.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_losers = losers[:30]

        # Cache speichern (gleiche Struktur wie api.py bear scanner)
        cache_data = {
            "inverse_etfs": [],  # ETFs werden nur in api.py geladen
            "short_candidates": [],
            "breakdown_stocks": top_losers,
            "asset_filter": {
                "source": common_stock_source,
                "excluded_non_common": excluded_non_common,
            },
        }
        _atomic_write_json("/tmp/bear_scanner_cache.json",
                          {"results": [cache_data], "timestamp": time.time(),
                           "cached_at": datetime.now().isoformat()})

        log.info(f"  {len(top_losers)} Crash-Kandidaten (Top: {top_losers[0]['ticker'] if top_losers else '–'} {top_losers[0]['score'] if top_losers else 0})")
        _update_status("bear_scan", "done", f"{len(top_losers)} Kandidaten")

        # Crash Alert: only while the current candle is still pressing lows.
        now = time.time()
        crash_stocks = [
            l for l in top_losers
            if l["grade"] in ("S", "A")
            and l["change_pct"] <= -10
            and l["score"] >= _ALERT_MIN_SCORE
            and _bear_crash_alert_ok(l)
            and _alert_trade_plan_ok(l)
        ]
        if crash_stocks:
            crash_date = datetime.now().strftime('%Y%m%d')
            fresh_crash_stocks = []
            crash_dedupe_keys = []
            for cs in crash_stocks:
                ticker = str(cs.get("ticker", "?")).upper()
                dedupe_key = f"crash_stock_{crash_date}_{ticker}"
                if _email_dedupe_claim(dedupe_key, _CRASH_ALERT_DEDUPE_SEC, now=now):
                    fresh_crash_stocks.append(cs)
                    crash_dedupe_keys.append(dedupe_key)
                else:
                    log.info(f"  CRASH Alert skipped by persistent dedupe: {ticker}")
            crash_stocks = fresh_crash_stocks
        else:
            crash_dedupe_keys = []

        if crash_stocks:
            _crash_ck = f"crash_bg_{datetime.now().strftime('%Y%m%d_%H')}"  # Stündlicher Cooldown
            crash_summary_key = _crash_ck
            if (
                _crash_ck not in _EMAIL_COOLDOWN
                and _email_dedupe_claim(_crash_ck, 3600, now=now)
            ):
                _crash_rows = ""
                for cs in crash_stocks[:8]:
                    _gc = {"S": "#7c3aed", "A": "#16a34a"}.get(cs["grade"], "#666")
                    _crash_rows += (
                        f"<tr><td style='padding:6px 8px;font-weight:bold;color:{_gc}'>{cs['grade']}</td>"
                        f"<td style='padding:6px 8px;font-weight:bold'>{cs['ticker']}</td>"
                        f"<td style='padding:6px 8px;text-align:right'>${cs['price']:.2f}</td>"
                        f"<td style='padding:6px 8px;text-align:right;color:#dc2626;font-weight:bold'>{cs['change_pct']:.1f}%</td>"
                        f"<td style='padding:6px 8px;text-align:right'>{cs['rvol']:.1f}x</td>"
                        f"<td style='padding:6px 8px;text-align:left'>{_format_alert_plan_html(cs)}</td>"
                        f"<td style='padding:6px 8px;text-align:right;font-weight:bold'>{cs['score']}</td></tr>"
                    )
                _body = f'''<html><body style="font-family:Arial,sans-serif;max-width:700px;margin:0 auto">
                <h2 style="color:#dc2626">Short Alert — {len(crash_stocks)} Crash-Kandidaten</h2>
                <p style="color:#666;font-size:13px">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET</p>
                <table style="border-collapse:collapse;width:100%;font-size:13px">
                <tr style="background:#fef2f2"><th style="padding:6px 8px;text-align:left">Grd</th>
                <th style="padding:6px 8px;text-align:left">Ticker</th>
                <th style="padding:6px 8px;text-align:right">Preis</th>
                <th style="padding:6px 8px;text-align:right">Drop</th>
                <th style="padding:6px 8px;text-align:right">RVOL</th>
                <th style="padding:6px 8px;text-align:left">Entry / Stop / TP</th>
                <th style="padding:6px 8px;text-align:right">Score</th></tr>
                {_crash_rows}</table>
                <p style="color:#999;font-size:11px;margin-top:12px">Automatischer Short/Crash Alert vom Background Service (stündlich)</p>
                </body></html>'''
                sent = _send_email_alert(f"Short Alert: {len(crash_stocks)} Crash-Kandidaten ({crash_stocks[0]['ticker']} {crash_stocks[0]['change_pct']:.0f}%)", _body, secrets, mail_class="trade")
                if sent:
                    crash_mail_sent = True
                    _EMAIL_COOLDOWN[_crash_ck] = now
                    _email_dedupe_mark(_crash_ck, now=now)
                    for dedupe_key in crash_dedupe_keys:
                        _email_dedupe_mark(dedupe_key, now=now)
                    for cs in crash_stocks:
                        _mark_bearish_stock_alert(cs.get("ticker", ""), now=now)
                    log.info(f"  CRASH ALERT sent: {[c['ticker'] for c in crash_stocks]}")
                    # Signal-Tracking (Pfad dormant): crash_stocks sind Original-Rows.
                    _record_alert_signals_safe("bear_scan", crash_stocks,
                                               mail_class="trade", channel="email")
                else:
                    _email_dedupe_release(_crash_ck, claimed_at=now)
                    for dedupe_key in crash_dedupe_keys:
                        _email_dedupe_release(dedupe_key, claimed_at=now)
            else:
                for dedupe_key in crash_dedupe_keys:
                    _email_dedupe_release(dedupe_key, claimed_at=now)

    except Exception as e:
        if not crash_mail_sent:
            if crash_summary_key:
                _email_dedupe_release(crash_summary_key, claimed_at=locals().get("now"))
            for dedupe_key in crash_dedupe_keys:
                _email_dedupe_release(dedupe_key, claimed_at=locals().get("now"))
        log.error(f"Bear Scanner: {e}\n{traceback.format_exc()}")
        _update_status("bear_scan", "error", str(e))


def _run_strategy_scanner(poly_key, secrets):
    """
    Stündlicher Aktien-Strategien Scanner.
    Prüft alle wichtigen Strategien auf starke Setups und sendet E-Mail Alerts.

    Nutzt Polygon Snapshot API (wie _run_bi_scanner) und wendet Strategie-Filter an.
    """
    log.info("Strategie-Scanner: legacy bg mailer uebersprungen; FastAPI strategy_scan ist authoritative")
    _update_status("strategy_scan", "skipped", "FastAPI strategy_scan owns strategy mails")
    return

    _clear_scan_cache("strategies")
    import requests as req
    log.info("📊 Strategie-Scanner (stündlich)...")
    _update_status("strategy_scan", "running")

    try:
        # 1) Polygon Snapshot laden
        snap = req.get("https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                       params={"apiKey": poly_key}, timeout=30)
        if snap.status_code != 200:
            log.error(f"  Polygon HTTP {snap.status_code}")
            return
        tickers = snap.json().get("tickers", [])
        log.info(f"  {len(tickers)} Aktien geladen")

        # 2) CS-Whitelist laden
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                with open(cs_file, "r") as f:
                    cs_set = set(json.load(f))
        except Exception:
            pass

        # 3) Daten extrahieren
        stocks = []
        for t in tickers:
            try:
                lt = t.get("lastTrade", {}) or {}
                day = t.get("day", {}) or {}
                prev = t.get("prevDay", {}) or {}
                price = lt.get("p") or day.get("c") or day.get("vw") or prev.get("c") or 0
                if not price or price <= 0:
                    continue

                ticker = t.get("ticker", "")
                if len(ticker) > 5 or "." in ticker:
                    continue
                if cs_set and ticker.upper() not in cs_set:
                    continue

                vol = day.get("v") or 0
                prev_vol = prev.get("v") or 0
                prev_close = prev.get("c") or 0
                change_pct = t.get("todaysChangePerc") or 0
                rvol = vol / prev_vol if prev_vol > 0 and vol > 0 else 0
                dollar_vol = price * vol if vol > 0 else price * prev_vol

                # Close Position: (Close - Low) / (High - Low)
                day_high = day.get("h") or 0
                day_low = day.get("l") or 0
                day_close = day.get("c") or price
                close_pos = (day_close - day_low) / (day_high - day_low) if day_high > day_low else 0.5

                # Gap%: (Open - PrevClose) / PrevClose
                day_open = day.get("o") or 0
                gap_pct = ((day_open - prev_close) / prev_close * 100) if prev_close > 0 and day_open > 0 else 0

                # Vortag%: (PrevClose - PrevOpen) / PrevOpen — Kerzen-Performance
                prev_open = prev.get("o") or 0
                vortag_pct = ((prev_close - prev_open) / prev_open * 100) if prev_open > 0 else 0

                stocks.append({
                    "Ticker": ticker, "Name": (t.get("name", "") or "")[:30],
                    "Preis": round(price, 2), "Change%": round(change_pct, 2),
                    "RVOL": round(rvol, 2), "Close Position": round(close_pos, 3),
                    "close_pos": round(close_pos, 3),
                    "open_to_current_pct": round(((price - day_open) / day_open * 100), 2) if day_open > 0 else None,
                    "DayHigh": round(day_high, 4) if day_high else None,
                    "DayLow": round(day_low, 4) if day_low else None,
                    "Volume": vol, "DollarVol": dollar_vol,
                    "Gap%": round(gap_pct, 2), "Vortag%": round(vortag_pct, 2),
                })
            except Exception:
                continue

        log.info(f"  {len(stocks)} Aktien nach Basis-Filter")

        # 4) Strategien definieren — nur die wichtigsten für Alerts
        ALERT_STRATEGIES = {
            "Breakout Long": {
                "filters": {"Change %": (3.0, 50.0), "RVOL": (1.5, 50.0), "Close Position": (0.65, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Breakdown Short": {
                "filters": {"Change %": (-50.0, -3.0), "RVOL": (0.8, 50.0), "Close Position": (0.0, 0.35)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Crash Short": {
                # V2.8: Massive Drops brauchen kein hohes RVOL — der Drop selbst ist das Signal
                "filters": {"Change %": (-60.0, -10.0)},
                "direction": "short", "min_price": 3.0, "min_dv": 300000,
            },
            "Volume Surge": {
                "filters": {"RVOL": (2.0, 50.0), "Change %": (2.0, 100.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Whale Watch": {
                "filters": {"RVOL": (3.0, 100.0), "Change %": (2.0, 100.0), "Close Position": (0.55, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 500000,
            },
            "Whale Watch Short": {
                "filters": {"RVOL": (2.5, 100.0), "Change %": (-100.0, -2.0), "Close Position": (0.0, 0.45)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Early Momentum": {
                "filters": {"Change %": (3.0, 30.0), "RVOL": (1.5, 50.0), "Close Position": (0.6, 1.0), "Preis": (5.0, 500.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Gap Up Momentum": {
                "filters": {"Gap%": (2.0, 30.0), "Close Position": (0.55, 1.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
            "Gap Down Short": {
                "filters": {"Gap%": (-30.0, -2.0), "Close Position": (0.0, 0.45)},
                "direction": "short", "min_price": 5.0, "min_dv": 500000,
            },
            "Reversal Hunter": {
                "filters": {"Vortag%": (-50.0, -3.0), "Change %": (2.0, 30.0), "RVOL": (1.5, 50.0)},
                "direction": "long", "min_price": 5.0, "min_dv": 200000,
            },
        }

        # 5) Jede Strategie durchlaufen und Matches finden
        all_alerts = []
        now = time.time()

        for strat_name, strat in ALERT_STRATEGIES.items():
            matches = []
            for s in stocks:
                # Min Price + Dollar Volume
                if s["Preis"] < strat.get("min_price", 5.0):
                    continue
                if s["DollarVol"] < strat.get("min_dv", 200000):
                    continue

                # Strategie-Filter anwenden
                passed = True
                for filter_key, (fmin, fmax) in strat["filters"].items():
                    # Filter-Key Mapping
                    data_key = {
                        "Change %": "Change%", "RVOL": "RVOL",
                        "Close Position": "Close Position", "Preis": "Preis",
                        "Gap%": "Gap%", "Vortag%": "Vortag%",
                    }.get(filter_key, filter_key)
                    val = s.get(data_key, 0)
                    if not (fmin <= val <= fmax):
                        passed = False
                        break

                if passed:
                    # Score berechnen: gewichtete Kombination der Signalstärke
                    score = 0
                    change = abs(s["Change%"])
                    rvol = s["RVOL"]
                    close_pos = s["Close Position"]

                    # Change-Stärke (max 40)
                    score += min(40, change * 4)
                    # RVOL-Stärke (max 30)
                    score += min(30, rvol * 8)
                    # Close Position Qualität (max 20) — 1.0 = perfekt für Long, 0.0 für Short
                    if strat["direction"] == "long":
                        score += close_pos * 20
                    else:
                        score += (1.0 - close_pos) * 20
                    # DollarVol Bonus (max 10)
                    if s["DollarVol"] >= 5_000_000:
                        score += 10
                    elif s["DollarVol"] >= 1_000_000:
                        score += 5

                    matches.append({**s, "_score": round(score), "_strategy": strat_name,
                                    "_direction": strat["direction"]})

            # Top 5 pro Strategie nach Score
            matches.sort(key=lambda x: x["_score"], reverse=True)
            top = matches[:5]

            for m in top:
                # Cooldown pro Ticker+Strategie (B2: zusaetzlich persistent, B8:
                # gesetzt wird erst NACH erfolgreichem Versand)
                ck = f"strat_{strat_name}_{m['Ticker']}"
                if ck in _EMAIL_COOLDOWN and now - _EMAIL_COOLDOWN[ck] < _EMAIL_COOLDOWN_SEC:
                    continue
                if _email_dedupe_active(ck, _EMAIL_COOLDOWN_SEC, now=now):
                    continue
                # Nur starke Setups: Score >= 80
                if m["_score"] >= _ALERT_MIN_SCORE:
                    if m.get("_direction") == "long":
                        m = dict(m)
                        m.update(_fetch_long_latest_intraday_state(m["Ticker"], poly_key))
                        m["long_entry_quality"] = _long_entry_quality(m)
                        m["alertable_long"] = not _long_entry_rule_reasons(m)
                        if not m["alertable_long"]:
                            continue
                    elif m.get("_direction") == "short":
                        m = dict(m)
                        m.update(_fetch_bear_latest_intraday_state(m["Ticker"], poly_key))
                        m["bear_entry_quality"] = _bear_entry_quality(m)
                        if _bear_short_rule_reasons(m):
                            continue
                    # B8/Q3: dieselben Gates wie _check_and_alert_scan_results
                    # (Pfad ist dormant, aber gegen Reaktivierung abgesichert).
                    if not _alert_trade_plan_ok(m):
                        continue
                    _rvol_num = _safe_float(m.get("RVOL", m.get("rvol")), None)
                    if _rvol_num is None or _rvol_num < _ALERT_MIN_RVOL:
                        continue
                    if _bg_alert_health_reasons(m, "strategy_scan"):
                        continue
                    all_alerts.append(m)

        # 6) Cache speichern
        cache_file = "/tmp/strategy_scan_cache.json"
        try:
            _atomic_write_json(cache_file, {"results": all_alerts, "timestamp": time.time(),
                           "total_stocks": len(stocks)})
        except Exception as e:
            log.debug(f"Non-critical error: {e}")

        log.info(f"  {len(all_alerts)} starke Setups gefunden (Score >= {_ALERT_MIN_SCORE})")
        _update_status("strategy_scan", "done", f"{len(all_alerts)} Alerts")

        # 7) E-Mail senden wenn Alerts vorhanden
        if all_alerts:
            # Gruppiere nach Strategie
            by_strat = {}
            for a in all_alerts:
                sn = a["_strategy"]
                if sn not in by_strat:
                    by_strat[sn] = []
                by_strat[sn].append(a)

            subject = f"📊 {len(all_alerts)} Strategie-Setups gefunden"
            rows = ""
            for sn, items in by_strat.items():
                for a in items:
                    dir_emoji = "⬆️" if a["_direction"] == "long" else "⬇️"
                    rows += f"""<tr>
                        <td style="padding:6px;border-bottom:1px solid #eee"><b>{a['Ticker']}</b></td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a.get('Name', '')}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{sn}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{dir_emoji}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['_score']}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">${a['Preis']}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['Change%']:+.1f}%</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a['RVOL']:.1f}x</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{_format_alert_plan_html(a)}</td>
                        <td style="padding:6px;border-bottom:1px solid #eee">{a.get('long_entry_quality', a.get('bear_entry_quality', ''))}</td>
                    </tr>"""

            body_html = f"""
            <html><body style="font-family:Arial,sans-serif;max-width:750px;margin:0 auto">
            <h2 style="color:#1a73e8">📊 Strategie-Scanner Alert</h2>
            <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {len(all_alerts)} Setups (Score >= {_ALERT_MIN_SCORE})</p>
            <table style="width:100%;border-collapse:collapse;font-size:13px">
                <tr style="background:#f5f5f5">
                    <th style="padding:6px;text-align:left">Ticker</th>
                    <th style="padding:6px;text-align:left">Name</th>
                    <th style="padding:6px;text-align:left">Strategie</th>
                    <th style="padding:6px;text-align:left">Dir</th>
                    <th style="padding:6px;text-align:left">Score</th>
                    <th style="padding:6px;text-align:left">Preis</th>
                    <th style="padding:6px;text-align:left">Change</th>
                    <th style="padding:6px;text-align:left">RVOL</th>
                    <th style="padding:6px;text-align:left">Entry / Stop / TP</th>
                    <th style="padding:6px;text-align:left">Timing</th>
                </tr>
                {rows}
            </table>
            <p style="color:#999;font-size:12px;margin-top:20px">
                Automatischer Strategie-Alert | Score = Change×4 + RVOL×8 + ClosePos×20 + VolBonus<br>
                Nur Setups mit Score >= {_ALERT_MIN_SCORE} werden gemeldet | 8h Cooldown pro Ticker
            </p>
            </body></html>"""

            sent = _send_email_alert(subject, body_html, secrets, mail_class="trade")
            if sent:
                # B8: Cooldown/Dedupe erst NACH erfolgreichem Versand setzen.
                for a in all_alerts:
                    _ck = f"strat_{a['_strategy']}_{a['Ticker']}"
                    _EMAIL_COOLDOWN[_ck] = now
                    _email_dedupe_mark(_ck, now=now)
                # Signal-Tracking (Pfad dormant, gegen Reaktivierung abgesichert):
                # all_alerts SIND hier die Original-Rows inkl. Entry/Stop/TP.
                _record_alert_signals_safe("strategy_scan", all_alerts,
                                           mail_class="trade", channel="email")

    except Exception as e:
        log.error(f"❌ Strategy Scanner: {e}\n{traceback.format_exc()}")
        _update_status("strategy_scan", "error", str(e))


def _run_orb_scanner(poly_key):
    """ORB Scanner — läuft nur Mo-Fr 9:45-11:00 ET, speichert Ergebnisse als Cache"""
    _clear_scan_cache("orb")
    import pytz
    et_tz = pytz.timezone('US/Eastern')
    now_et = datetime.now(et_tz)
    hour, minute = now_et.hour, now_et.minute
    time_val = hour * 60 + minute
    weekday = now_et.weekday()

    # Nur während ORB-Fenster laufen: 9:45-11:00 ET, Mo-Fr
    if weekday >= 5 or time_val < 585 or time_val >= 660:  # 9:45=585, 11:00=660
        log.info("🔔 ORB Scanner — außerhalb Fenster (nur 9:45-11:00 ET Mo-Fr), übersprungen")
        return

    log.info("🔔 ORB Scanner...")
    _update_status("orb", "running")

    try:
        # fetch_orb_scanner ist in scanner.py mit @st.cache_data — wir rufen die Funktion
        # direkt auf, der Decorator wird ignoriert wenn kein Streamlit-Kontext da ist.
        # Stattdessen: Funktion manuell aufrufen und Cache-File schreiben
        import importlib, sys

        # Mock st.cache_data damit der Import nicht crasht
        class _FakeST:
            @staticmethod
            def cache_data(*a, **kw):
                def dec(f): return f
                return dec
            def __getattr__(self, name):
                return lambda *a, **kw: None

        # scanner.py braucht streamlit — wir importieren es mit Mock
        # Besser: Die Kern-Logik direkt nutzen
        from modules.data_fetchers import rate_limited_get, fetch_grouped_daily

        orb_progress = "/tmp/orb_scan_progress.json"
        orb_results = "/tmp/orb_scan_results.json"

        today_str = now_et.strftime("%Y-%m-%d")
        yesterday = (now_et - timedelta(days=1)).strftime("%Y-%m-%d")
        if weekday == 0:
            yesterday = (now_et - timedelta(days=3)).strftime("%Y-%m-%d")

        prev_data = fetch_grouped_daily(poly_key, yesterday)
        if not prev_data:
            day_before = (now_et - timedelta(days=2)).strftime("%Y-%m-%d")
            if weekday == 0:
                day_before = (now_et - timedelta(days=4)).strftime("%Y-%m-%d")
            prev_data = fetch_grouped_daily(poly_key, day_before)

        if not prev_data:
            _update_status("orb", "error", "Keine Vortages-Daten")
            return

        # V2.8: Snapshot API statt fetch_grouped_daily für heutige Daten
        # fetch_grouped_daily liefert während Handelszeit KEINE Daten (nur nach Börsenschluss)
        today_data = {}
        try:
            import requests as _req
            snap_resp = _req.get(
                "https://api.polygon.io/v2/snapshot/locale/us/markets/stocks/tickers",
                params={"apiKey": poly_key}, timeout=30
            )
            if snap_resp.status_code == 200:
                for t in snap_resp.json().get("tickers", []):
                    sym = t.get("ticker", "")
                    day = t.get("day", {}) or {}
                    lt = t.get("lastTrade", {}) or {}
                    if day.get("o"):
                        today_data[sym] = {
                            "o": day.get("o", 0),
                            "h": day.get("h", 0),
                            "l": day.get("l", 0),
                            "c": day.get("c", 0) or lt.get("p", 0),
                            "v": day.get("v", 0),
                        }
                log.info(f"[ORB] Snapshot: {len(today_data)} Ticker mit Intraday-Daten")
            else:
                log.warning(f"[ORB] Snapshot HTTP {snap_resp.status_code} — Fallback grouped daily")
                today_data = fetch_grouped_daily(poly_key, today_str) or {}
        except Exception as e:
            log.warning(f"[ORB] Snapshot Fehler: {e} — Fallback grouped daily")
            today_data = fetch_grouped_daily(poly_key, today_str) or {}

        # CS-Whitelist laden
        cs_set = set()
        cs_file = "/tmp/cs_tickers_cache.json"
        try:
            if os.path.exists(cs_file) and (time.time() - os.path.getmtime(cs_file)) < 86400:
                cs_set = set(json.load(open(cs_file)))
        except Exception:
            pass

        mins_since_open = max(1, time_val - 570)  # 570 = 9:30
        total_market_mins = 390

        candidates = []
        for ticker, prev in prev_data.items():
            if len(ticker) > 5 or "." in ticker:
                continue
            if cs_set and ticker.upper() not in cs_set:
                continue
            prev_close = prev.get("c", 0)
            if prev_close < 5 or prev_close > 2000:
                continue
            prev_vol = prev.get("v", 0)
            if prev_vol < 500000:
                continue

            today = today_data.get(ticker, {}) if today_data else {}
            today_open = today.get("o", 0)
            today_vol = today.get("v", 0)
            today_high = today.get("h", 0)
            today_low = today.get("l", 0)
            today_close = today.get("c", 0)

            if today_open <= 0:
                continue

            gap_pct = ((today_open - prev_close) / prev_close * 100) if prev_close > 0 else 0

            if mins_since_open <= 30:
                expected_vol_fraction = 0.20 * (mins_since_open / 30)
            elif mins_since_open <= 60:
                expected_vol_fraction = 0.20 + 0.10 * ((mins_since_open - 30) / 30)
            else:
                expected_vol_fraction = 0.30 + 0.70 * ((mins_since_open - 60) / (total_market_mins - 60))
            expected_vol_fraction = max(0.01, expected_vol_fraction)
            expected_vol = prev_vol * expected_vol_fraction
            rvol = today_vol / expected_vol if expected_vol > 0 else 0

            if abs(gap_pct) < 2 and rvol < 1.5:
                continue

            candidates.append({
                "ticker": ticker, "prev_close": round(prev_close, 2),
                "open": round(today_open, 2), "current": round(today_close or today_open, 2),
                "high": round(today_high, 2), "low": round(today_low, 2),
                "gap_pct": round(gap_pct, 2), "rvol": round(rvol, 2), "volume": today_vol,
            })

        candidates.sort(key=lambda x: abs(x["gap_pct"]) * 0.6 + min(x["rvol"], 5) * 0.4, reverse=True)
        candidates = candidates[:40]

        # 5-Min Candles für Breakout Detection
        market_open_ms = int(now_et.replace(hour=9, minute=30, second=0, microsecond=0).timestamp() * 1000)
        or_end_ms = int(now_et.replace(hour=9, minute=45, second=0, microsecond=0).timestamp() * 1000)
        breakouts = []

        for cand in candidates:
            ticker = cand["ticker"]
            try:
                url = f"https://api.polygon.io/v2/aggs/ticker/{ticker}/range/5/minute/{today_str}/{today_str}"
                resp = rate_limited_get(url, params={"apiKey": poly_key, "adjusted": "true", "sort": "asc", "limit": 50000}, timeout=10)
                if resp.status_code != 200:
                    continue
                bars = resp.json().get("results", [])
                if not bars or len(bars) < 2:
                    continue
                bars = [b for b in bars if b.get("t", 0) >= market_open_ms]
                if len(bars) < 2:
                    continue

                or_bars = [b for b in bars if b.get("t", 0) < or_end_ms]
                if not or_bars:
                    or_bars = bars[:3]
                or_high = max(b.get("h", 0) for b in or_bars)
                or_low = min(b.get("l", 999999) for b in or_bars)

                # VWAP
                total_vwap_num = sum((b.get("h",0)+b.get("l",0)+b.get("c",0))/3 * b.get("v",0) for b in bars)
                total_vol = sum(b.get("v", 0) for b in bars)
                vwap = total_vwap_num / total_vol if total_vol > 0 else (or_high + or_low) / 2

                current_price = bars[-1].get("c", 0)
                post_or = [b for b in bars if b.get("t", 0) >= or_end_ms]
                bars_above = sum(1 for b in post_or if b.get("c", 0) > or_high)
                bars_below = sum(1 for b in post_or if b.get("c", 0) < or_low)

                breakout_dir = None
                if current_price > or_high and bars_above >= 2:
                    breakout_dir = "LONG"
                elif current_price < or_low and bars_below >= 2:
                    breakout_dir = "SHORT"

                if breakout_dir:
                    breakouts.append({
                        **cand,
                        "or_high": round(or_high, 2), "or_low": round(or_low, 2),
                        "vwap": round(vwap, 2), "direction": breakout_dir,
                        "current_price": round(current_price, 2),
                    })
            except Exception:
                continue

        # Ergebnisse speichern
        result = {
            "breakouts": breakouts, "candidates": candidates[:20],
            "stats": {"scanned": len(prev_data), "candidates": len(candidates), "breakouts": len(breakouts)},
            "or_phase": "active", "market_time": now_et.strftime("%H:%M ET"),
            "timestamp": time.time()
        }
        _atomic_write_json(orb_results, result)

        _update_status("orb", "ok", f"{len(breakouts)} Breakouts")
        log.info(f"  ✅ ORB: {len(breakouts)} Breakouts (von {len(candidates)} Kandidaten)")

    except Exception as e:
        _update_status("orb", "error", str(e))
        log.error(f"  ❌ ORB Fehler: {e}")


def _run_biotech_scanner(poly_key):
    """Biotech Scanner — ruft _biotech_background_scan aus modules/scanners.py auf"""
    cache_file = _SCAN_CACHE_MAP["biotech"]
    previous_revision, _ = _scanner_cache_snapshot(cache_file)
    started_at = time.time()
    _clear_scan_cache("biotech")
    log.info("🧬 Biotech Scanner...")
    _update_status("biotech", "running")

    try:
        from modules.scanners import _biotech_background_scan
        _biotech_background_scan(poly_key)
        payload = _require_final_scanner_publish(
            "biotech",
            cache_file,
            previous_revision,
            "/tmp/alpha_biotech_progress.json",
            started_at,
        )
        _update_status("biotech", "ok", "Scan abgeschlossen")
        log.info("  ✅ Biotech Scan abgeschlossen")
        return payload.get("results", [])
    except Exception as e:
        _update_status("biotech", "error", str(e))
        log.error(f"  ❌ Biotech Scan Fehler: {e}")
        raise


# ── M-7 Audit-Fix: Stablecoins / Wrapped / LSD / Gold-Token — keine direktionalen Mover ──
# SYNC mit api.py EXCLUDED_CRYPTO_SYMBOLS (dort gepflegt, hier gespiegelt) + lokale
# Ergänzung CBBTC (cbBTC). Identische Kopie in scanner.py — Änderungen in beiden nachziehen.
EXCLUDED_CRYPTO_SYMBOLS_LOCAL = {
    "USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USDS", "USDD",
    "USDP", "PYUSD", "FRAX", "LUSD", "GUSD", "DOLA", "SUSD", "EUSD", "USDL",
    "USDY", "USDX", "EURC", "EUROC", "WBTC", "CBTC", "TBTC", "LBTC", "WETH",
    "WBNB", "STETH", "WSTETH", "RETH", "CBETH", "WBETH", "WEETH", "EZETH",
    "METH", "RSETH", "SFRXETH", "FRXETH", "PAXG", "XAUT",
    "CBBTC",  # cbBTC (Coinbase Wrapped BTC) — Ergänzung zur api-Liste
}


def _is_leveraged_token_symbol(symbol):
    """M-7 Audit-Fix: Leveraged-Token erkennen (kein Spot-Mover, gehört nicht in Scans).

    Erkennt: 3L/3S/4L/4S/5L/5S-Suffixe, UP/DOWN-Endungen (Binance Leveraged Tokens)
    und BULL/BEAR-Token. Konservative Mindestlängen, damit echte Ticker wie
    'JUP' (endet auf UP) oder ein Coin namens 'BULL' nicht gefiltert werden.
    SYNC: identische Kopie in scanner.py.
    """
    sym = (symbol or "").upper().strip()
    if len(sym) >= 4 and sym[-2:] in ("3L", "3S", "4L", "4S", "5L", "5S"):
        return True
    if sym.endswith("UP") and len(sym) >= 5:
        return True
    if sym.endswith("DOWN") and len(sym) >= 6:
        return True
    if (sym.endswith("BULL") or sym.endswith("BEAR")) and len(sym) >= 6:
        return True
    return False


def _btc_div_signal_status(exh_score, close_pos, change_1h, change_24h, btc_weak):
    """H-7 Audit-Fix: Einheitliche, pure Timing-/Gate-Logik für BTC-Divergenz-Shorts.

    SYNC: Identische Implementierung in scanner.py UND bg_service.py — Änderungen
    immer in beiden Dateien nachziehen. (Shared-Home wäre modules/scorers, gehört
    aber einem anderen Team; Cross-Import hat Seiteneffekte: Logging/PID/Streamlit.)

    Regeln (konsolidiert mit der api.py-Variante):
    - "JETZT"-Signale erst ab ExhScore >= 65 (vorher bg_service: 55/50/45) UND nur
      bei BTC-Schwäche (btc_weak=True). BTC stark → bestenfalls "BEOBACHTEN".
    - Dieser Pfad liefert KEINEN Entry/Stop/TP → auch "JETZT SHORTEN" ist nur ein
      Beobachtungssignal und trägt den expliziten Hinweis "kein definierter Stop".

    Returns:
        (timing: str, timing_quality: int, btc_gate: bool)
        timing_quality: 5=JETZT SHORTEN, 4=JETZT, 3=BEREIT, 2=WATCH/BEOBACHTEN,
                        0=ZU FRÜH, -1=ZU SPÄT. btc_gate=True nur bei BTC-Schwäche.
    """
    no_stop_note = " · ⚠️ kein definierter Stop — Beobachtungssignal"
    cp = close_pos if close_pos is not None else 0.5
    price_near_high = cp >= 0.70
    price_mid_range = 0.40 <= cp < 0.70
    price_near_low = cp < 0.40
    btc_gate = bool(btc_weak)

    if price_near_low and change_24h < -3:
        return ("⚫ ZU SPÄT — Preis schon {:.0f}% vom High, Move gelaufen".format((1 - cp) * 100), -1, btc_gate)

    if not btc_gate:
        # H-7: BTC auf den Makro-Zeitfenstern stark → KEIN Short-Timing vergeben,
        # egal wie hoch Divergenz/ExhScore sind. Nur beobachten.
        if exh_score >= 50:
            return ("👁️ BEOBACHTEN (BTC stark — kein Short-Timing)", 2, False)
        return ("⚪ ZU FRÜH", 0, False)

    if exh_score >= 65 and price_near_high and change_1h < -1.5:
        return ("🔴 JETZT SHORTEN — Nahe High, 1h kippt ({:+.1f}%){}".format(change_1h, no_stop_note), 5, True)
    if exh_score >= 65 and price_near_high and change_1h < -0.5:
        return ("🟢 JETZT — Nahe High, erste Schwäche (1h {:+.1f}%){}".format(change_1h, no_stop_note), 4, True)
    if exh_score >= 65 and price_near_high:
        return ("🟡 BEREIT — Nahe High, warte auf rote 1h-Kerze", 3, True)
    if exh_score >= 65 and price_mid_range:
        return ("🟡 BEREIT — Warte auf Bounce Richtung High für besseren Entry", 3, True)
    if exh_score >= 50 and price_near_high and change_1h < -2.0:
        # Vorher "JETZT" schon ab Score 50/55 — konsolidiert: unter 65 kein JETZT mehr.
        # WICHTIG: Wort "JETZT" hier vermeiden — UI matcht per Substring!
        return ("🟡 BEREIT — Starker 1h-Dump ({:+.1f}%), ExhScore unter Schwelle 65".format(change_1h), 3, True)
    if exh_score >= 50 and price_mid_range and change_1h < 0:
        return ("🟠 WATCHLIST — Mittlerer Bereich, könnte noch bounzen", 2, True)
    if exh_score >= 65 and price_near_low:
        return ("⚫ ZU SPÄT — Preis schon {:.0f}% vom High gefallen".format((1 - cp) * 100), -1, True)
    if exh_score >= 50:
        return ("🟠 WATCHLIST — Noch nicht reif", 2, True)
    return ("⚪ ZU FRÜH", 0, True)


def _cg_markets_cache_payload(all_coins, pages_ok, pages_wanted=4, per_page=250):
    """H-14 Audit-Fix (pure, testbar): Cache-Payload NUR für vollständige Abrufe.

    429-Teilabrufe dürfen nicht als frischer Voll-Cache geschrieben werden —
    Konsumenten (scanner/api) würden 2 Min lang blind einem Rumpf-Universum
    vertrauen (Vorbild: gehärteter api.py-Writer "partial must not poison cache").

    Returns: Payload-Dict bei Vollständigkeit, sonst None (→ nicht schreiben).
    """
    coins = all_coins or []
    complete = pages_ok >= pages_wanted and len(coins) >= pages_wanted * per_page
    if complete:
        return {"coins": coins, "ts": time.time(), "pages": pages_wanted}
    return None


def _run_btc_divergence(poly_key=None):
    """BTC-Divergenz Scanner — nutzt CoinGecko (kein Polygon nötig)
    V2: Berechnet Timing, ExhScore, SellProb etc. (vorher fehlten diese Felder)"""
    log.info("📉 BTC-Divergenz Scanner...")
    _update_status("btc_divergence", "fetching")

    import requests as req

    # Import Scoring-Funktionen (kein Streamlit nötig)
    # AUDIT-Kleinkram: calculate_close_position aus modules.indicators statt
    # modules.scorers — die scorers-Version ist ein Stub, der min_range_pct
    # ignoriert. Die indicators-Version respektiert min_range_pct=0.3
    # (gewollte Verhaltensänderung: Mini-Ranges liefern None statt Pseudo-Werte).
    try:
        from modules.scorers import (calculate_exhaustion_score,
                                     get_exhaustion_grade)
        from modules.indicators import calculate_close_position
    except ImportError as ie:
        log.error(f"  scorers import fehlgeschlagen: {ie}")
        _update_status("btc_divergence", "error", f"Import: {ie}")
        return

    try:
        # CoinGecko laden (4 Seiten) — H-14: Vollständigkeit mitzählen
        all_coins = []
        _cg_pages_ok = 0
        for page in range(1, 5):
            try:
                resp = req.get("https://api.coingecko.com/api/v3/coins/markets",
                    params={"vs_currency": "usd", "order": "market_cap_desc",
                            "per_page": 250, "page": page, "sparkline": False,
                            "price_change_percentage": "1h,24h,7d,14d,30d"},
                    timeout=30)
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, list):
                        all_coins.extend(data)
                        _cg_pages_ok += 1
                elif resp.status_code == 429:
                    log.warning(f"  CoinGecko Rate Limit bei Seite {page}")
                    break
            except Exception as e:
                log.warning(f"  CoinGecko Seite {page}: {e}")
                if page > 1: break
            if page < 4:
                time.sleep(3)

        if not all_coins:
            _update_status("btc_divergence", "error", "Keine CoinGecko Daten")
            return

        # H-14 Audit-Fix: Teilabrufe (429/Fehler) NICHT als frischen Voll-Cache
        # schreiben — sonst vertrauen Streamlit/api dem Rumpf-Universum 2 Min blind.
        cg_cache = _CG_MARKETS_CACHE_FILE
        _cg_payload = _cg_markets_cache_payload(all_coins, _cg_pages_ok, pages_wanted=4)
        if _cg_payload is not None:
            _atomic_write_json(cg_cache, _cg_payload)
            log.info(f"  {len(all_coins)} Coins geladen, speichere für Streamlit")
        else:
            log.warning(f"  CoinGecko unvollständig (Seiten {_cg_pages_ok}/4, {len(all_coins)} Coins) "
                        f"— Datei-Cache NICHT überschrieben, Scan läuft mit Teilmenge weiter")

        # Progress-Datei für Streamlit
        div_progress = "/tmp/div_scan_progress.json"
        _atomic_write_json(div_progress, {"status": "running", "checked": 0, "total": len(all_coins),
                       "hits": 0, "detail": f"📊 {len(all_coins)} Coins geladen", "timestamp": time.time()})

        # BTC Benchmark
        btc_data = None
        for c in all_coins:
            if c.get("symbol", "").lower() == "btc" or c.get("id") == "bitcoin":
                btc_data = {
                    "price": c.get("current_price", 0),
                    "change_1h": c.get("price_change_percentage_1h_in_currency") or 0,
                    "change_24h": c.get("price_change_percentage_24h") or 0,
                    "change_7d": c.get("price_change_percentage_7d_in_currency") or 0,
                    "change_14d": c.get("price_change_percentage_14d_in_currency") or 0,
                    "change_30d": c.get("price_change_percentage_30d_in_currency") or 0,
                    "market_cap": c.get("market_cap", 0),
                }
                break

        if not btc_data:
            _atomic_write_json(div_progress, {"status": "error", "detail": "BTC nicht gefunden", "timestamp": time.time()})
            return

        btc_7d = btc_data.get("change_7d", 0)
        btc_14d = btc_data.get("change_14d", 0)
        btc_30d = btc_data.get("change_30d", 0)

        # ── H-7 Audit-Fix: BTC-Schwäche-Gate (identische Regel wie scanner.py) ──
        # Short-Timing gibt es nur, wenn BTC auf mind. 2 von 3 Zeitfenstern schwach ist.
        btc_weak_7d = btc_7d <= 0.0          # 7d negativ/flat
        btc_weak_14d = btc_14d <= 3.0        # 14d kaum Bewegung
        btc_weak_30d = btc_30d <= 3.0        # 30d kaum Bewegung
        btc_has_weakness = sum([btc_weak_7d, btc_weak_14d, btc_weak_30d]) >= 2
        btc_bullish = not btc_has_weakness
        log.info(f"  H-7 BTC-Gate: {'SCHWACH → Short-Timing erlaubt' if btc_has_weakness else 'STARK → nur Beobachten'} "
                 f"(7d {btc_7d:+.1f}% / 14d {btc_14d:+.1f}% / 30d {btc_30d:+.1f}%)")

        # ── FIX 2: BTC Dominance als Makro-Filter ──
        btc_dominance = None
        try:
            dom_resp = req.get("https://api.coingecko.com/api/v3/global", timeout=15)
            if dom_resp.status_code == 200:
                gdata = dom_resp.json().get("data", {})
                btc_dominance = gdata.get("market_cap_percentage", {}).get("btc", None)
                # BTC Dom > 55% = Risk-Off → Shorts auf Alts besser
                # BTC Dom < 45% = Altseason → Shorts auf Alts riskanter
                log.info(f"  BTC Dominance: {btc_dominance:.1f}%")
        except Exception as e:
            log.warning(f"  BTC Dominance Fehler: {e}")

        # ── FIX 3: Real RSI via CoinGecko OHLC (Top-Hits) ──
        # Wird NACH dem Scan für Top-Kandidaten nachgeladen
        def _calc_rsi_from_ohlc(coin_id, days=30, period=14):
            """Berechne echten RSI aus CoinGecko OHLC-Daten.
            days=30 gibt Daily Candles (days=14 gibt nur 4h-Kerzen bei CoinGecko)."""
            try:
                ohlc_resp = req.get(f"https://api.coingecko.com/api/v3/coins/{coin_id}/ohlc",
                                    params={"vs_currency": "usd", "days": days},
                                    timeout=15)
                if ohlc_resp.status_code != 200:
                    return None
                ohlc = ohlc_resp.json()  # [[ts, open, high, low, close], ...]
                if not ohlc or len(ohlc) < period + 1:
                    return None
                closes = [c[4] for c in ohlc if len(c) >= 5]
                gains, losses = [], []
                for i in range(1, len(closes)):
                    diff = closes[i] - closes[i - 1]
                    gains.append(max(0, diff))
                    losses.append(max(0, -diff))
                if len(gains) < period:
                    return None
                avg_gain = sum(gains[:period]) / period
                avg_loss = sum(losses[:period]) / period
                for i in range(period, len(gains)):
                    avg_gain = (avg_gain * (period - 1) + gains[i]) / period
                    avg_loss = (avg_loss * (period - 1) + losses[i]) / period
                if avg_loss == 0:
                    return 100.0
                rs = avg_gain / avg_loss
                rsi = 100 - (100 / (1 + rs))
                return round(rsi, 1)
            except Exception:
                return None

        # ── FIX 5: OI Change Delta 24h (Cache-basiert) ──
        oi_cache_file = "/tmp/oi_cache_prev.json"
        oi_prev = {}
        try:
            if os.path.exists(oi_cache_file):
                with open(oi_cache_file, "r") as f:
                    oi_prev = json.load(f)
        except Exception:
            pass

        # ── FIX 4: Liquidation Daten (Coinglass Free) ──
        liquidation_data = {}
        try:
            liq_resp = req.get("https://open-api.coinglass.com/public/v2/liquidation/info",
                               params={"time_type": 1}, timeout=10,  # 24h
                               headers={"accept": "application/json"})
            if liq_resp.status_code == 200:
                liq_list = liq_resp.json().get("data", [])
                for liq in liq_list:
                    sym = (liq.get("symbol") or "").upper()
                    if sym:
                        liquidation_data[sym] = {
                            "long_liq": liq.get("longVolUsd", 0),
                            "short_liq": liq.get("shortVolUsd", 0),
                            "total_liq": liq.get("volUsd", 0),
                        }
                if liquidation_data:
                    log.info(f"  Liquidation Daten: {len(liquidation_data)} Coins")
            else:
                # AUDIT-Kleinkram: nicht still schlucken — 1 Warnung pro Lauf
                log.warning(f"  Coinglass Liquidation HTTP {liq_resp.status_code} — LiqFactor bleibt neutral (1.0)")
        except Exception as _cg_liq_err:
            # Coinglass free tier kann fehlen — aber sichtbar loggen (1x pro Lauf)
            log.warning(f"  Coinglass Liquidation nicht erreichbar: {_cg_liq_err} — LiqFactor bleibt neutral")

        # ── FIX 5: OI Daten holen (für Delta-Berechnung) ──
        oi_current = {}
        try:
            oi_resp = req.get("https://open-api.coinglass.com/public/v2/open_interest",
                              params={"time_type": 0}, timeout=10,
                              headers={"accept": "application/json"})
            if oi_resp.status_code == 200:
                oi_list = oi_resp.json().get("data", [])
                for oi_item in oi_list:
                    sym = (oi_item.get("symbol") or "").upper()
                    oi_val = oi_item.get("openInterest", 0)
                    if sym and oi_val:
                        oi_current[sym] = oi_val
                if oi_current:
                    log.info(f"  OI Daten: {len(oi_current)} Coins")
            else:
                # AUDIT-Kleinkram: nicht still schlucken — 1 Warnung pro Lauf
                log.warning(f"  Coinglass Open Interest HTTP {oi_resp.status_code} — OI-Delta entfällt diesen Lauf")
        except Exception as _cg_oi_err:
            log.warning(f"  Coinglass Open Interest nicht erreichbar: {_cg_oi_err} — OI-Delta entfällt")

        results = []
        checked = 0

        for coin in all_coins:
            checked += 1
            try:
                symbol = coin.get("symbol", "").upper()
                # M-7 Audit-Fix: BTC selbst + vollständige Stable-/Wrapped-/LSD-Liste
                # + Leveraged-Token (3L/3S/…, UP/DOWN, BULL/BEAR) überspringen
                if symbol == "BTC" or symbol in EXCLUDED_CRYPTO_SYMBOLS_LOCAL or _is_leveraged_token_symbol(symbol):
                    continue

                price = coin.get("current_price") or 0
                if price <= 0: continue

                change_1h = coin.get("price_change_percentage_1h_in_currency") or 0
                change_24h = coin.get("price_change_percentage_24h") or 0
                change_7d = (coin.get("price_change_percentage_7d_in_currency")
                             or coin.get("price_change_percentage_7d") or 0)
                change_14d = coin.get("price_change_percentage_14d_in_currency") or 0
                change_30d = coin.get("price_change_percentage_30d_in_currency") or 0
                market_cap = coin.get("market_cap") or 0
                vol_24h = coin.get("total_volume") or 0
                high_24h = coin.get("high_24h") or price
                low_24h = coin.get("low_24h") or price

                if vol_24h < 5_000_000 or market_cap < 10_000_000:
                    continue

                # Multi-Timeframe Divergenz
                div_7d = change_7d - btc_7d
                div_14d = (change_14d - btc_14d) if change_14d else 0
                div_30d = (change_30d - btc_30d) if change_30d else 0

                best_div = max(div_7d, div_14d, div_30d)
                best_tf = "7d"
                if best_div == div_30d and div_30d >= 10:
                    best_tf = "30d"
                elif best_div == div_14d and div_14d >= 10:
                    best_tf = "14d"

                if best_div < 10:
                    continue
                best_change = {"7d": change_7d, "14d": change_14d, "30d": change_30d}[best_tf]
                if best_change < 8:
                    continue

                # OHLC + Wick-Berechnung
                open_price = price / (1 + change_24h / 100) if change_24h != -100 else price
                open_price = max(low_24h, min(high_24h, open_price))
                candle_range = high_24h - low_24h if high_24h > low_24h else 0
                range_pct = (candle_range / low_24h * 100) if low_24h > 0 else 0

                if range_pct >= 0.5 and candle_range > 0:
                    body_top = max(open_price, price)
                    body_bottom = min(open_price, price)
                    upper_wick_pct = ((high_24h - body_top) / candle_range) * 100
                    lower_wick_pct = ((body_bottom - low_24h) / candle_range) * 100
                else:
                    upper_wick_pct = 0
                    lower_wick_pct = 0

                close_pos = calculate_close_position(high_24h, low_24h, price, min_range_pct=0.3)

                # Exhaustion Score
                exh_score, exh_details = calculate_exhaustion_score(
                    change_24h=change_24h, change_7d=change_7d,
                    btc_change_7d=btc_7d, rvol=None, close_pos=close_pos,
                    upper_wick_pct=upper_wick_pct, lower_wick_pct=lower_wick_pct,
                    market_cap=market_cap, high_24h=high_24h, low_24h=low_24h,
                    price=price, vol_24h=vol_24h, change_1h=change_1h,
                    change_14d=change_14d, change_30d=change_30d,
                    btc_change_14d=btc_14d, btc_change_30d=btc_30d,
                    funding_rate=None, oi_volume_ratio=None,
                )
                grade, grade_emoji, grade_label = get_exhaustion_grade(exh_score)

                # ── H-7 Audit-Fix: Short-Timing über gemeinsame Gate-Helper-Logik ──
                # Vorher: bg-eigene Schwellen 55/50/45 für "JETZT" und KEIN BTC-Gate
                # → "JETZT SHORTEN" auch bei BTC +20%/7d. Jetzt: Schwelle 65 (wie api)
                # + BTC-Schwäche-Gate + expliziter "kein Stop"-Hinweis (Beobachtungssignal).
                cp = close_pos if close_pos is not None else 0.5
                timing, _timing_quality, _btc_gate = _btc_div_signal_status(
                    exh_score, close_pos, change_1h, change_24h, btc_has_weakness)

                # RVOL
                if market_cap > 0 and vol_24h > 0:
                    turnover = (vol_24h / market_cap) * 100
                    mc = market_cap
                    if mc > 100_000_000_000:   bl = 3.0
                    elif mc > 10_000_000_000:  bl = 6.0
                    elif mc > 1_000_000_000:   bl = 10.0
                    elif mc > 100_000_000:     bl = 20.0
                    else:                      bl = 30.0
                    rvol = round(turnover / bl, 2)
                else:
                    rvol = 0.8  # Unter Durchschnitt bei fehlenden Daten (konservativ)

                # ── SellProb V4: HYBRID ──
                # bg_service nutzt CoinGecko (kein Funding/OI verfügbar, immer None)
                # → Exhaustion-Komponenten 7+8 (OI, Funding) fehlen immer
                # → Theoretisches Max: 90 Punkte, Empirisches Max: ~75-80
                # Normalisierung /75 statt /100: ExhScore 65→87%, 70→93%, 75→100%
                # Schritt 1: Kern-Score aus 3 Faktoren (gewichtet, 0-100)
                exh_pct = min(100, exh_score * 100 / 75)           # 0-100 (75+ = 100%)
                timing_pct = {5: 100, 4: 85, 3: 60, 2: 35, 0: 5, -1: 0}.get(_timing_quality, 0)
                # pos_pct: Linear 0-100 statt ×130 Cliff
                # cp 0.0→0%, 0.5→50%, 0.8→80%, 1.0→100% (glatter Verlauf)
                pos_pct = min(100, max(0, (cp or 0.3)) * 100)
                kern_score = exh_pct * 0.40 + timing_pct * 0.35 + pos_pct * 0.25

                # Schritt 2: Volume als Skalierung (0.5 - 1.2)
                volume_mult = max(0.5, min(1.2, (rvol or 0.5) / 1.5))

                # BTC Dominance Boost (FIX 2)
                dom_factor = 1.0
                if btc_dominance:
                    if btc_dominance >= 58:
                        dom_factor = 1.15  # Starke BTC Dom → Alts sehr anfällig
                    elif btc_dominance >= 52:
                        dom_factor = 1.05  # Moderat hohe BTC Dom
                    elif btc_dominance >= 45:
                        dom_factor = 0.95  # Neutrale Zone — leichter Malus
                    elif btc_dominance <= 42:
                        dom_factor = 0.75  # Altseason → Shorts riskant

                # Liquidation Boost (FIX 4)
                liq_factor = 1.0
                liq_info = liquidation_data.get(symbol, {})
                long_liq = liq_info.get("long_liq", 0)
                short_liq = liq_info.get("short_liq", 0)
                if long_liq > 0 and short_liq > 0:
                    liq_ratio = long_liq / max(1, short_liq)
                    if liq_ratio >= 3.0:
                        liq_factor = 1.20  # Massive Long-Liquidationen → Short optimal
                    elif liq_ratio >= 1.5:
                        liq_factor = 1.10
                    elif liq_ratio <= 0.3:
                        liq_factor = 0.80  # Short Squeeze Gefahr

                # ── FIX 5: OI Change Delta 24h ──
                oi_delta_pct = None
                oi_now = oi_current.get(symbol, 0)
                oi_before = oi_prev.get(symbol, 0)
                if oi_now > 0 and oi_before > 0:
                    oi_delta_pct = round(((oi_now - oi_before) / oi_before) * 100, 1)

                # OI-Faktor: OI steigt stark + Preis nahe High = Longs überhebelt → Short gut
                oi_factor = 1.0
                if oi_delta_pct is not None:
                    if oi_delta_pct >= 20 and cp >= 0.7:
                        oi_factor = 1.15  # OI explodiert bei Highs → überhebelt
                    elif oi_delta_pct >= 10:
                        oi_factor = 1.05  # OI steigt moderat
                    elif oi_delta_pct <= -20:
                        oi_factor = 0.85  # OI sinkt stark → weniger Squeeze-Potential
                    elif oi_delta_pct <= -10:
                        oi_factor = 0.92  # OI sinkt leicht

                # Schritt 3: Alle Modifier zusammen (dom, liq, oi = Boosts/Malus)
                # Cap bei 1.4 (max +40%) und Floor bei 0.4 (max -60%)
                # Verhindert dass 4 kleine Boosts sich zu 1.9x multiplizieren
                combined_mod = max(0.4, min(1.4, volume_mult * dom_factor * liq_factor * oi_factor))
                sell_prob = max(0, min(100, round(kern_score * combined_mod)))

                results.append({
                    "Ticker": symbol,
                    "Name": coin.get("name", symbol),
                    "Preis": price,
                    "1h%": round(change_1h, 2),
                    "24h%": round(change_24h, 2),
                    "7d%": round(change_7d, 2),
                    "14d%": round(change_14d, 2),
                    "30d%": round(change_30d, 2),
                    "BTC_7d%": round(btc_7d, 2),
                    "BTC_14d%": round(btc_14d, 2),
                    "BTC_30d%": round(btc_30d, 2),
                    "Divergenz%": round(best_div, 1),
                    "BestTF": best_tf,
                    "Div7d%": round(div_7d, 1),
                    "Div14d%": round(div_14d, 1),
                    "Div30d%": round(div_30d, 1),
                    "ExhScore": exh_score,
                    "ExhGrade": grade,
                    "GradeEmoji": grade_emoji,
                    "Timing": timing,
                    "TimingQuality": _timing_quality,
                    "btc_gate": _btc_gate,  # H-7: False = BTC stark, kein Short-Timing
                    "SellProb": sell_prob,
                    "RVOL": rvol,
                    "UpperWick%": round(upper_wick_pct, 1),
                    "ClosePos": close_pos,
                    "MarketCap": market_cap,
                    "Vol24h": vol_24h,
                    "ExhDetails": exh_details,
                    "CoinId": coin.get("id", ""),
                    "FundingRate": None,
                    "OI_Ratio": None,
                    "HasPerp": False,
                    "Exchanges": [],
                    "BestExchange": "",
                    # Neue Felder V3
                    "BTCDominance": btc_dominance,
                    "LiqLong": long_liq,
                    "LiqShort": short_liq,
                    "LiqFactor": round(liq_factor, 2),
                    "DomFactor": round(dom_factor, 2),
                    # Neue Felder V4 (Fix 3 + 5)
                    "RSI14": None,  # Wird für Top-Hits nachgeladen
                    "OI_Delta%": oi_delta_pct,
                    "OI_Factor": round(oi_factor, 2),
                })
            except Exception:
                continue

            if checked % 100 == 0:
                try:
                    _atomic_write_json(div_progress, {"status": "running", "checked": checked, "total": len(all_coins),
                                   "hits": len(results), "detail": f"{checked}/{len(all_coins)}",
                                   "timestamp": time.time()})
                except Exception as e:
                    log.debug(f"Non-critical error: {e}")

        results.sort(key=lambda x: x.get("Divergenz%", 0), reverse=True)

        # ── FIX 3: RSI nachladen für Top-30 Kandidaten (Buffer für Re-Ranking) ──
        # Top-30 statt Top-20: RSI-Boost kann Ranking ändern (Coin #25 → Top-10)
        top_for_rsi = results[:30]
        rsi_loaded = 0
        for r in top_for_rsi:
            coin_id = r.get("CoinId", "")
            if not coin_id:
                continue
            try:
                rsi_val = _calc_rsi_from_ohlc(coin_id, days=30, period=14)
                if rsi_val is not None:
                    r["RSI14"] = rsi_val
                    rsi_loaded += 1
                    # AUDIT FIX: RSI in SellProb einrechnen (nicht nur anzeigen!)
                    old_sp = r["SellProb"]
                    if rsi_val >= 75:
                        r["SellProb"] = min(100, old_sp + 12)  # Stark überkauft
                    elif rsi_val >= 70:
                        r["SellProb"] = min(100, old_sp + 6)   # Überkauft
                    elif rsi_val <= 35:
                        r["SellProb"] = max(0, old_sp - 15)    # Überverkauft → kein Short!
                    elif rsi_val <= 45:
                        r["SellProb"] = max(0, old_sp - 5)     # Neutral-niedrig
                time.sleep(1.5)  # CoinGecko Rate Limit
            except Exception:
                pass
        if rsi_loaded:
            log.info(f"  RSI14 für {rsi_loaded}/{len(top_for_rsi)} Top-Hits geladen")
        # Re-Sort nach RSI-Adjustment (Ranking kann sich geändert haben)
        results.sort(key=lambda x: x.get("SellProb", 0), reverse=True)

        # ── FIX 5: OI Cache speichern für nächsten Delta-Vergleich ──
        if oi_current:
            try:
                _atomic_write_json(oi_cache_file, oi_current)
            except Exception as e:
                log.debug(f"Non-critical error: {e}")

        # Speichere für Streamlit
        div_results = "/tmp/div_scan_results.json"
        _atomic_write_json(div_results, {"results": results, "btc": btc_data,
                       "stats": {"scanned": checked, "candidates": len(results), "btc_7d": btc_7d,
                                 # H-7: Gate-Status für UI (Watch-Only-Box statt Alarm)
                                 "btc_bullish": btc_bullish,
                                 "btc_has_weakness": btc_has_weakness},
                       "ts": time.time()})

        _atomic_write_json(div_progress, {"status": "done", "detail": f"✅ {len(results)} Divergenzen",
                       "timestamp": time.time()})

        _update_status("btc_divergence", "ok", f"{len(results)} Divergenzen")
        log.info(f"  {len(results)} Divergenzen gefunden")

    except Exception as e:
        log.error(f"❌ BTC-Divergenz: {e}\n{traceback.format_exc()}")
        _update_status("btc_divergence", "error", str(e))


# ══════════════════════════════════════════════════════════════
# NEW LISTING DUMP SCANNER
# ══════════════════════════════════════════════════════════════

def _alert_nls_signals_legacy(results, secrets):
    """Legacy NLS mailer kept for rollback reference; hardened mailer is defined below."""
    return
    if not results:
        return
    signals = results.get("signals", [])
    if not signals:
        return

    now = time.time()
    alerts = []
    for entry in signals:
        sig = entry.get("signal", {})
        symbol = entry.get("symbol", "")
        grade = sig.get("grade", "")

        # Nur Grade S oder A
        if grade not in ("S", "A"):
            continue

        # Cooldown
        cooldown_key = f"nls_{symbol}"
        if cooldown_key in _EMAIL_COOLDOWN:
            if now - _EMAIL_COOLDOWN[cooldown_key] < _EMAIL_COOLDOWN_SEC:
                continue

        _EMAIL_COOLDOWN[cooldown_key] = now
        alerts.append({
            "symbol": symbol,
            "exchange": entry.get("exchange", ""),
            "grade": grade,
            "grade_label": sig.get("grade_label", ""),
            "timing": sig.get("timing", ""),
            "entry": sig.get("entry", 0),
            "stop": sig.get("stop", 0),
            "tp1": sig.get("tp1", 0),
            "tp2": sig.get("tp2", 0),
            "rr1": sig.get("rr1", 0),
            "rr2": sig.get("rr2", 0),
            "exh_score": sig.get("exh_score", 0),
            "pump_pct": sig.get("pump_pct", 0),
        })

    if not alerts:
        return

    n = len(alerts)
    subject = f"🔴 {n} Dump-Short Signal{'e' if n > 1 else ''} — New Listing Scanner"

    rows = ""
    for a in alerts:
        emoji = "🏆" if a["grade"] == "S" else "🔥"
        rows += f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{a['symbol']}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['exchange']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{emoji} {a['grade_label']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['timing']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['entry']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['stop']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['tp1']:.4f} / ${a['tp2']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['rr1']:.1f}x / {a['rr2']:.1f}x</td>
        </tr>"""

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
    <h2 style="color:#dc3545">🔴 New Listing Dump — SHORT Signale</h2>
    <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} Dump-Signal{'e' if n > 1 else ''} erkannt</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Symbol</th>
            <th style="padding:8px;text-align:left">Exchange</th>
            <th style="padding:8px;text-align:left">Grade</th>
            <th style="padding:8px;text-align:left">Timing</th>
            <th style="padding:8px;text-align:left">Entry</th>
            <th style="padding:8px;text-align:left">Stop</th>
            <th style="padding:8px;text-align:left">TP1 / TP2</th>
            <th style="padding:8px;text-align:left">R:R</th>
        </tr>
        {rows}
    </table>
    <p style="color:#999;font-size:12px;margin-top:20px">
        Automatischer Alert vom TradingBot — New Listing Dump Scanner.<br>
        Nur Grade S + A Signale. 4h Cooldown pro Symbol.
    </p>
    </body></html>
    """

    _send_email_alert(subject, body_html, secrets)
    log.info(f"📧 NLS Alert: {n} Dump-Signale gesendet ({', '.join(a['symbol'] for a in alerts)})")


def _alert_nls_invalidations(results, secrets):
    """B5: Einmalige Info-Update-Mail, wenn ein bereits GEMAILTES NLS-Signal
    invalidiert wurde (Stop gerissen).

    Nur fuer Symbole mit aktivem new_listing_{RAW}-Dedupe-Mark (= Erst-Mail ging
    wirklich raus); eigener Dedupe-Key new_listing_invalidated_{RAW} (TTL 72h).
    """
    monitoring = results.get("monitoring", []) if isinstance(results, dict) else []
    if not monitoring:
        return
    now = time.time()
    for entry in monitoring:
        if not isinstance(entry, dict):
            continue
        category = str(entry.get("trade_category", "") or "").upper()
        status = str(entry.get("status", "") or "").lower()
        if category != "SIGNAL_INVALIDATED" and status != "invalidated":
            continue
        raw_symbol = str(entry.get("symbol", "") or "").strip().upper()
        if not raw_symbol:
            continue
        # Erst-Mail-Mark? Roh-Symbol-Key wie api (B3). 72h-Fenster: Signal lebt
        # max. 24h, das Fenster deckt Restarts/verzoegerte Scans ab.
        signal_key = f"new_listing_{raw_symbol}"
        if not _email_dedupe_active(signal_key, _NLS_INVALIDATION_DEDUPE_SEC, now=now):
            continue
        invalidation_key = f"new_listing_invalidated_{raw_symbol}"
        if not _email_dedupe_claim(invalidation_key, _NLS_INVALIDATION_DEDUPE_SEC, now=now):
            continue
        display = _display_crypto_contract_symbol(raw_symbol)
        reason = html.escape(str(entry.get("status_reason", "") or "Stop gerissen"))
        price = _safe_float(entry.get("price"), None)
        stop = _safe_float(entry.get("stop_loss"), None)
        price_text = f"${price:.6g}" if price is not None else "-"
        stop_text = f"${stop:.6g}" if stop is not None else "-"
        body_html = f"""
        <html><body style="font-family:Arial,sans-serif;max-width:640px;margin:0 auto">
        <h2 style="color:#0f172a">Signal invalidiert — {display}</h2>
        <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | New Listing Dump Scanner</p>
        <p>Das zuvor gemailte SHORT-Signal fuer <b>{display}</b>
        ({html.escape(str(entry.get('exchange', '') or ''))}) ist invalidiert —
        der Stop wurde gerissen.</p>
        <p>Preis: <b>{price_text}</b> | Stop: <b>{stop_text}</b><br>
        Grund: {reason}</p>
        <p style="color:#999;font-size:12px;margin-top:16px">
            Update-Mail (einmalig pro Symbol). Kein neues Signal, keine Handelsaufforderung.
        </p>
        </body></html>"""
        try:
            sent = _send_email_alert(
                f"Signal invalidiert — Stop gerissen: {display}",
                body_html, secrets, mail_class="info",
            )
        except Exception:
            _email_dedupe_release(invalidation_key, claimed_at=now)
            raise
        if sent:
            _email_dedupe_mark(invalidation_key, now=now)
            log.info(f"NLS Invalidierungs-Update gesendet: {display} ({raw_symbol})")
        else:
            _email_dedupe_release(invalidation_key, claimed_at=now)


def _alert_nls_signals(results, secrets):
    """Override: mail only active, safe Pump-&-Dump SHORT-now signals."""
    if not results:
        return
    # B5: Invalidierungs-Updates zuerst — unabhaengig davon, ob neue Signale da sind.
    try:
        _alert_nls_invalidations(results, secrets)
    except Exception as exc:
        log.warning(f"NLS Invalidierungs-Update fehlgeschlagen: {exc}")
    signals = results.get("signals", [])
    if not signals:
        return

    now = time.time()
    alerts = []
    # Signal-Tracking: Original-Signal-Dicts (entry/stop_loss/tp1/tp2 — Tracker
    # extrahiert tolerant) der tatsächlich gemailten Symbole parallel sammeln.
    alert_source_rows = []
    for entry in signals:
        if not isinstance(entry, dict):
            continue
        sig = entry.get("signal", {})
        if not isinstance(sig, dict):
            continue

        raw_symbol = entry.get("symbol", "")
        symbol = _display_crypto_contract_symbol(raw_symbol)
        grade = str(sig.get("grade", "")).upper()
        timing = str(sig.get("timing", ""))
        timing_quality = _safe_float(sig.get("timing_quality"), 0)
        rr_effective = _safe_float(sig.get("rr_effective", sig.get("rr1", 0)), 0)
        risk_pct = _safe_float(sig.get("risk_pct"), 999)
        safety_ok = bool(sig.get("safety_ok", False))
        confirmation_ok = bool(sig.get("confirmation_ok", False))
        continuation_risk = bool(sig.get("continuation_risk", False))
        signal_quality = str(sig.get("signal_quality", "") or "").lower()
        tp_missed = bool(sig.get("tp1_missed", False) or sig.get("tp2_missed", False))
        pump_data = sig.get("pump_data", {}) if isinstance(sig.get("pump_data", {}), dict) else {}
        micro_required = bool(sig.get("micro_required", True))
        micro_trigger_ok = bool(sig.get("micro_trigger_ok", pump_data.get("micro_trigger_ok", False)))
        score = _safe_float(sig.get("exh_score", entry.get("exh_score", pump_data.get("micro_score", 0))), 0)
        reasons = []

        if grade not in ("S", "A", "A+"):
            reasons.append("grade_below_alert_threshold")
        if score < _ALERT_MIN_SCORE:
            reasons.append("score_below_alert_threshold")
        if timing_quality < 4 or "SHORT" not in timing.upper():
            reasons.append("not_active_short_timing")
        if not safety_ok:
            reasons.append("safety_not_ok")
        if tp_missed:
            reasons.append("target_already_missed")
        if rr_effective < _NLS_MIN_ALERT_RR:
            reasons.append("rr_below_alert_threshold")
        if not _alert_trade_plan_ok({"signal": sig}, _NLS_MIN_ALERT_RR):
            reasons.append("invalid_trade_plan")
        if not confirmation_ok:
            reasons.append("turn_not_confirmed")
        if continuation_risk:
            reasons.append("pump_continuation_risk")
        if micro_required and not micro_trigger_ok:
            reasons.append("micro_trigger_missing")
        if risk_pct > 35:
            reasons.append("risk_too_wide")
        if signal_quality and signal_quality != "tradeable":
            reasons.append("not_tradeable_signal_quality")

        # B3: Dedupe-Key auf ROH-Symbol (api-Format, z.B. new_listing_TSTUSD).
        # Der Display-Symbol-Key (new_listing_TST) lief am persistierten
        # api-Bestand vorbei und erlaubte Doppel-Mails.
        cooldown_key = f"new_listing_{str(raw_symbol or '').strip().upper()}"
        if cooldown_key in _EMAIL_COOLDOWN and now - _EMAIL_COOLDOWN[cooldown_key] < _EMAIL_COOLDOWN_SEC:
            reasons.append("cooldown_active")
        if _email_dedupe_active(cooldown_key, _EMAIL_COOLDOWN_SEC, now=now):
            reasons.append("persistent_dedupe_active")
        if reasons:
            log.debug(f"NLS Alert suppressed {symbol}: {', '.join(reasons)}")
            continue

        alerts.append({
            "symbol": symbol,
            "exchange": entry.get("exchange", ""),
            "grade": grade,
            "grade_label": sig.get("grade_label", grade),
            "score": score,
            "timing": timing,
            "setup": sig.get("setup_type", ""),
            "stop_model": sig.get("stop_model", ""),
            "micro_score": _safe_float((sig.get("pump_data", {}) or {}).get("micro_score"), 0),
            "entry": _safe_float(sig.get("entry"), 0),
            "stop": _safe_float(sig.get("stop_loss", sig.get("stop", 0)), 0),
            "tp1": _safe_float(sig.get("tp1"), 0),
            "tp2": _safe_float(sig.get("tp2"), 0),
            "rr_effective": rr_effective,
            "cooldown_key": cooldown_key,
            "source_row": {**sig, "symbol": raw_symbol, "ticker": symbol,
                           "exchange": entry.get("exchange", ""), "direction": "short"},
        })
        # Roh-Signal (entry/stop_loss/tp*-Felder) + Symbol-Kontext für den Tracker.
        alert_source_rows.append({**sig, "symbol": raw_symbol, "ticker": symbol,
                                  "exchange": entry.get("exchange", ""), "direction": "short"})

    if not alerts:
        return

    claimed_alerts = []
    for alert in alerts:
        if _email_dedupe_claim(alert["cooldown_key"], _EMAIL_COOLDOWN_SEC, now=now):
            claimed_alerts.append(alert)
    alerts = claimed_alerts
    if not alerts:
        return
    alert_source_rows = [alert["source_row"] for alert in alerts]

    n = len(alerts)
    rows = ""
    for a in alerts:
        rows += f"""<tr>
            <td style="padding:8px;border-bottom:1px solid #eee"><b>{a['symbol']}</b></td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['exchange']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['grade_label']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['score']:.0f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['setup'] or a['timing']}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['entry']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['stop']:.4f}<br><span style="color:#999;font-size:11px">{a['stop_model']}</span></td>
            <td style="padding:8px;border-bottom:1px solid #eee">${a['tp1']:.4f} / ${a['tp2']:.4f}</td>
            <td style="padding:8px;border-bottom:1px solid #eee">{a['rr_effective']:.1f}R<br><span style="color:#999;font-size:11px">Micro {a['micro_score']:.0f}</span></td>
        </tr>"""

    body_html = f"""
    <html><body style="font-family:Arial,sans-serif;max-width:800px;margin:0 auto">
    <h2 style="color:#dc3545">New Listing Dump - SHORT Signale</h2>
    <p style="color:#666">{datetime.now().strftime('%d.%m.%Y %H:%M')} CET | {n} aktive SHORT-now Signale</p>
    <table style="width:100%;border-collapse:collapse;font-size:13px">
        <tr style="background:#f5f5f5">
            <th style="padding:8px;text-align:left">Symbol</th>
            <th style="padding:8px;text-align:left">Exchange</th>
            <th style="padding:8px;text-align:left">Grade</th>
            <th style="padding:8px;text-align:left">Score</th>
            <th style="padding:8px;text-align:left">Timing</th>
            <th style="padding:8px;text-align:left">Entry</th>
            <th style="padding:8px;text-align:left">Stop</th>
            <th style="padding:8px;text-align:left">TP1 / TP2</th>
            <th style="padding:8px;text-align:left">R:R</th>
        </tr>
        {rows}
    </table>
    <p style="color:#999;font-size:12px;margin-top:20px">
        Nur aktive SHORT-now Signale: Score >= {_ALERT_MIN_SCORE}, Timing-Quality >=4, Safety OK, erster Crack/Rejection bestaetigt, kein Pump-Continuation-Risk, TP-Zonen nicht verpasst, R:R >= {_NLS_MIN_ALERT_RR}. 8h Cooldown pro Symbol (persistent).
    </p>
    </body></html>
    """

    claimed_keys = [alert["cooldown_key"] for alert in alerts]

    # telegram_text TypeError-tolerant (B7-Muster, s. _check_and_alert_scan_results).
    sent = False
    try:
        try:
            sent = _send_email_alert(f"Pump & Dump: {n} SHORT Top-Signal(e)", body_html, secrets,
                                     mail_class="trade",
                                     telegram_text=_format_telegram_text(alert_source_rows))
        except TypeError:
            sent = _send_email_alert(f"Pump & Dump: {n} SHORT Top-Signal(e)", body_html, secrets, mail_class="trade")
    except Exception:
        for claimed_key in claimed_keys:
            _email_dedupe_release(claimed_key, claimed_at=now)
        raise
    if sent:
        for alert in alerts:
            _EMAIL_COOLDOWN[alert["cooldown_key"]] = now
            _email_dedupe_mark(alert["cooldown_key"], now=now)
        log.info(f"NLS Alert: {n} Dump-Signale gesendet ({', '.join(a['symbol'] for a in alerts)})")
        # Signal-Tracking NUR nach erfolgreichem Versand (wirft nie).
        _record_alert_signals_safe("new_listing", alert_source_rows,
                                   mail_class="trade", channel="email")
    else:
        for claimed_key in claimed_keys:
            _email_dedupe_release(claimed_key, claimed_at=now)
        log.warning(f"NLS Alert konnte nicht gesendet werden ({', '.join(a['symbol'] for a in alerts)})")


def _run_new_listing_scanner():
    """Wrapper für den New Listing Dump Scanner."""
    log.info("🆕 Starte New Listing Scanner...")
    _update_status("new_listing", "running")
    try:
        from modules.new_listing_scanner import run_new_listing_scanner, seed_instrument_cache
        # Beim ersten Start: Cache seeden (keine Falsch-Positiven)
        seed_instrument_cache()
        results = run_new_listing_scanner()
        sig_count = len(results.get("signals", []))
        watch_count = len(results.get("watchlist", []))
        mon_count = len(results.get("monitoring", []))
        _update_status("new_listing", "ok",
                       f"{sig_count} Signale, {watch_count} Watchlist, {mon_count} monitoring")
        return results
    except Exception as e:
        log.error(f"❌ New Listing Scanner: {e}\n{traceback.format_exc()}")
        _update_status("new_listing", "error", str(e))
        return None


# ══════════════════════════════════════════════════════════════
# SERVICE LOOP
# ══════════════════════════════════════════════════════════════

_running = True

def _signal_handler(sig, frame):
    global _running
    log.info("⏹️ Stop-Signal empfangen...")
    _running = False

# ── H-9 Audit-Fix: Doppel-Scheduler — Scan-Ownership bg_service vs. api.py ──
# api.py _scheduler_loop scannt bereits (light): crypto_explosion, early_movers,
# crash_monitor, market_context, btc_divergenz, volume_spikes, money_flow, orb,
# bear, strategy_scan, turtle, new_listing und crypto_trade_signals.
# Audit-Überlappung bg↔api: crash_monitor, btc_divergence(btc_divergenz),
# bear_scan(bear), strategies(strategy_scan), orb, new_listing → Default-Ownership: api.
# bg behält: bi_long, bi_short, biotech (Email-Alerts + feste ET-Zeitfenster)
# New Listing bleibt API-owned, damit Crypto-Listing-Mails nicht still ausfallen,
# wenn tradingbot-bg hängt/stale ist. Per BG_SCAN_SET kann bg new_listing weiter
# explizit übernehmen; geteilte Dedupe schützt dann vor Doppel-Mails.
# Hinweis: bi_long/bi_short/biotech stehen auch im api-Scheduler —
# dortige Skip-Logik ist api-Team-Thema (siehe Audit-Report H-9).
# Override per ENV: BG_SCAN_SET="crash_monitor,btc_divergence,..." (kommasepariert).
BG_ALL_SCANS = {
    "bi_long", "bi_short", "biotech", "crash_monitor", "strategies",
    "bear_scan", "btc_divergence", "new_listing", "orb",
}
BG_API_OWNED_OVERLAP = {"crash_monitor", "btc_divergence", "bear_scan", "strategies", "orb", "new_listing"}
BG_DEFAULT_SCAN_SET = BG_ALL_SCANS - BG_API_OWNED_OVERLAP


def _resolve_bg_scan_set(env_value=None, allow_api_overlap=None):
    """H-9 (pure, testbar): Aktive bg-Scans aus ENV BG_SCAN_SET oder Default-Set.

    Returns: (aktive_scans: set, übersprungene_scans: set)
    """
    raw = env_value if env_value is not None else os.environ.get("BG_SCAN_SET", "")
    raw = (raw or "").strip()
    if raw.lower() == "default":
        raw = ""
    if raw:
        wanted = {s.strip().lower() for s in raw.split(",") if s.strip()}
        unknown = wanted - BG_ALL_SCANS
        if unknown:
            log.warning(f"BG_SCAN_SET: unbekannte Scans ignoriert: {sorted(unknown)} "
                        f"(gültig: {sorted(BG_ALL_SCANS)})")
        valid = wanted & BG_ALL_SCANS
        if not valid:
            log.warning("BG_SCAN_SET ergab leere Scan-Menge — nutze Default-Set")
            active = set(BG_DEFAULT_SCAN_SET)
        else:
            active = set(valid)
            allow_overlap = allow_api_overlap
            if allow_overlap is None:
                allow_overlap = str(os.environ.get("ALLOW_DUPLICATE_SCAN_OWNERSHIP", "0")).strip().lower() in {
                    "1", "true", "yes", "on",
                }
            duplicate_scans = active & BG_API_OWNED_OVERLAP
            if duplicate_scans and not allow_overlap:
                log.warning(
                    "BG_SCAN_SET: API-eigene Scanner werden zum Schutz vor Doppel-Laeufen blockiert: "
                    f"{sorted(duplicate_scans)}"
                )
                active -= duplicate_scans
    else:
        active = set(BG_DEFAULT_SCAN_SET)
    return active, BG_ALL_SCANS - active


def run_service():
    global _running
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    secrets = _load_secrets()
    poly_key = secrets.get("POLYGON_KEY", "")
    if not poly_key:
        log.error("❌ POLYGON_KEY fehlt!")
        return

    PID_FILE.write_text(str(os.getpid()))
    log.info(f"Email alerts: {'AKTIV' if secrets.get('GMAIL_USER') and secrets.get('GMAIL_APP_PASSWORD') else 'INAKTIV (GMAIL_USER/GMAIL_APP_PASSWORD fehlt)'}")
    log.info(f"🚀 Background Service V2 gestartet (PID: {os.getpid()})")
    _update_status("_service", "running", f"PID {os.getpid()}")

    # ── Zeitplan: Feste Uhrzeiten (ET = US Eastern) ──
    # Aktien-Scanner basieren auf Daily Bars → ändern sich kaum untertägig
    # Crypto (BTC Divergenz) ist 24/7, schwankt stärker → häufiger
    # ORB braucht schnelle Checks bei Market Open
    from zoneinfo import ZoneInfo
    ET = ZoneInfo("America/New_York")

    # Format: (stunde_ET, minute_ET)
    # V2: Stündlich während Handelszeiten (9:30-16:00 ET) + E-Mail Alerts
    SCHEDULE_TIMES = {
        "bi_long":        [(10, 0), (11, 0), (12, 0), (13, 0), (14, 0), (15, 0), (16, 0)],  # Stündlich
        "bi_short":       [(10, 30), (11, 30), (12, 30), (13, 30), (14, 30), (15, 30)],      # Stündlich, 30min versetzt
        "crash_monitor":  [(9, 50), (13, 50)],              # 2x/Tag (10min vor BI, kein Konflikt)
        "biotech":        [(10, 15), (11, 15), (12, 15), (13, 15), (14, 15), (15, 15)],      # Stündlich, 15min versetzt
        "strategies":     [(10, 5), (11, 5), (12, 5), (13, 5), (14, 5), (15, 5), (16, 5)],   # Stündlich, 5min versetzt
        "bear_scan":      [(10, 20), (12, 20), (14, 20), (15, 45)],  # V2.8: 4x/Tag — Crash/Short Alerts
    }
    # Interval-basiert (unverändert)
    SCHEDULE_INTERVAL = {
        "btc_divergence":  7200,   # alle 2 Stunden (Crypto 24/7)
        "new_listing":      900,   # optional via BG_SCAN_SET; Default-Owner ist api.py
        "orb":              300,   # 5 Min (nur aktiv 9:45-11:00 ET Mo-Fr)
    }

    # ── H-9 Audit-Fix: Scan-Ownership anwenden (Doppel-Scheduler bg↔api) ──
    _bg_scans, _bg_skipped = _resolve_bg_scan_set()
    SCHEDULE_TIMES = {k: v for k, v in SCHEDULE_TIMES.items() if k in _bg_scans}
    SCHEDULE_INTERVAL = {k: v for k, v in SCHEDULE_INTERVAL.items() if k in _bg_scans}
    log.info(f"🗂️ H-9 Scan-Ownership: bg übernimmt {sorted(_bg_scans)}")
    if _bg_skipped:
        log.info(f"   ⏭️ übersprungen (Ownership beim api.py-Scheduler): {sorted(_bg_skipped)}")
    if os.environ.get("BG_SCAN_SET", "").strip():
        log.info(f"   (BG_SCAN_SET-Override aktiv: '{os.environ['BG_SCAN_SET']}')")

    # ── Signal-Tracker-Evaluierung: gehört IMMER bg (kein api-Pendant) ──
    # Bewusst NACH dem H-9-Ownership-Filter registriert — läuft unabhängig
    # von BG_SCAN_SET stündlich; überspringt sauber, wenn Team-A-Modul fehlt.
    SCHEDULE_INTERVAL["signal_eval"] = _SIGNAL_EVAL_INTERVAL_SEC
    log.info(f"📈 Signal-Tracker-Eval: stündlich aktiv "
             f"({'Modul vorhanden' if evaluate_open_signals is not None else 'WARTET auf modules.signal_tracker'})")

    # ── Wochenreport-Mail: gehört IMMER bg (kein api-Pendant), self-gated ──
    # 15-Min-Takt ist nur der Anklopf-Rhythmus; Fenster (Fr 16:15–23:00 ET)
    # und Wochen-Dedupe prüft _run_weekly_report selbst (Restart-sicher).
    SCHEDULE_INTERVAL["weekly_report"] = _WEEKLY_REPORT_CHECK_INTERVAL_SEC
    log.info(f"🧾 Wochenreport-Mail: Freitag 16:15–23:00 ET aktiv "
             f"({'Modul vorhanden' if load_performance_summary is not None else 'WARTET auf modules.signal_tracker'})")

    last_run = {}
    _today_done = {}  # Track welche festen Zeiten heute schon gelaufen sind
    _running_scanners = set()  # B-05: Prevent concurrent scanner execution

    def _check_fixed_schedule(scanner_name, now_et):
        """Prüft ob ein Scanner mit fester Uhrzeit jetzt laufen soll."""
        times = SCHEDULE_TIMES.get(scanner_name, [])
        today_str = now_et.strftime("%Y-%m-%d")
        for h, m in times:
            slot_key = f"{scanner_name}_{today_str}_{h:02d}:{m:02d}"
            if slot_key in _today_done:
                continue
            # B-05: Check if already running
            if scanner_name in _running_scanners:
                log.debug(f"⚠️ {scanner_name} bereits im Betrieb, überspringe")
                continue
            # Scanner soll laufen wenn aktuelle Zeit >= geplante Zeit
            # und nicht mehr als 30 Min danach (damit er nicht um 23:00 nachholt)
            sched_min = h * 60 + m
            now_min = now_et.hour * 60 + now_et.minute
            if sched_min <= now_min <= sched_min + 30:
                return (True, slot_key)  # Return slot_key for marking after completion
        return (False, None)

    # ── Initialer Load (einmal beim Start) — H-9: nur Scans mit bg-Ownership ──
    log.info("📡 Initialer Load...")
    if "crash_monitor" in _bg_scans:
        try:
            _fetch_crash_monitor(poly_key)
            last_run["crash_monitor"] = time.time()
        except Exception as e:
            log.error(f"Init Crash: {e}")
        time.sleep(5)

    if "btc_divergence" in _bg_scans:
        try:
            _run_btc_divergence(poly_key)
            last_run["btc_divergence"] = time.time()
        except Exception as e:
            log.error(f"Init BTC-Div: {e}")
        time.sleep(5)

    if "new_listing" in _bg_scans:
        try:
            _nls_init = _run_new_listing_scanner()
            _alert_nls_signals(_nls_init, secrets)
            last_run["new_listing"] = time.time()
        except Exception as e:
            log.error(f"Init New Listing: {e}")
        time.sleep(10)

    if "bi_long" in _bg_scans:
        try:
            _run_bi_scanner(poly_key, "long")
            last_run["bi_long"] = time.time()
        except Exception as e:
            log.error(f"Init BI Long: {e}")
        time.sleep(10)

    if "bi_short" in _bg_scans:
        try:
            _run_bi_scanner(poly_key, "short")
            last_run["bi_short"] = time.time()
        except Exception as e:
            log.error(f"Init BI Short: {e}")

    if "biotech" in _bg_scans:
        # Biotech Scanner nach 2 Min starten (nicht sofort — spart API-Calls beim Init)
        time.sleep(120)
        try:
            _run_biotech_scanner(poly_key)
            last_run["biotech"] = time.time()
        except Exception as e:
            log.error(f"Init Biotech: {e}")

    log.info("✅ Initialer Load abgeschlossen. Service läuft.")
    log.info(f"📅 Zeitplan: BI 3x/Tag, Crash+Biotech 2x/Tag, BTC-Div alle 2h, ORB 5min bei Open")

    # B-09: Cache cleanup at startup
    _cleanup_old_cache()
    last_cleanup = time.time()

    # ── Hauptschleife ──
    while _running:
        now = time.time()
        try:
            now_et = datetime.now(ET)
        except Exception:
            now_et = datetime.now()

        # ── Reset _today_done um Mitternacht ET ──
        _today_key = now_et.strftime("%Y-%m-%d")
        _done_keys = list(_today_done.keys())
        for dk in _done_keys:
            if _today_key not in dk:
                del _today_done[dk]

        # ── Cooldown-Cleanup (verhindert Memory Leak) ──
        _cleanup_email_cooldown()

        # B-09: Periodic cache cleanup (every 24h)
        if now - last_cleanup > 86400:
            _cleanup_old_cache()
            last_cleanup = now

        # ── Feste Zeitplan-Scanner (Aktien) — stündlich + E-Mail Alert ──
        for scanner_name in SCHEDULE_TIMES:
            should_run, slot_key = _check_fixed_schedule(scanner_name, now_et)
            if should_run:
                _running_scanners.add(scanner_name)  # B-05: Mark as running
                try:
                    log.info(f"⏰ {scanner_name} — geplante Zeit erreicht ({now_et.strftime('%H:%M')} ET)")
                    if scanner_name == "crash_monitor":
                        _fetch_crash_monitor(poly_key)
                    elif scanner_name == "bi_long":
                        _run_bi_scanner(poly_key, "long")
                        _check_and_alert_scan_results("bi_long", secrets)
                    elif scanner_name == "bi_short":
                        _run_bi_scanner(poly_key, "short")
                        _check_and_alert_scan_results("bi_short", secrets)
                    elif scanner_name == "biotech":
                        _run_biotech_scanner(poly_key)
                        _check_and_alert_scan_results("biotech", secrets)
                    elif scanner_name == "bear_scan":
                        _run_bear_scanner(poly_key, secrets)
                    elif scanner_name == "strategies":
                        _run_strategy_scanner(poly_key, secrets)
                    last_run[scanner_name] = time.time()
                    # B-04: Mark as done AFTER successful completion
                    _today_done[slot_key] = True
                except Exception as e:
                    log.error(f"❌ {scanner_name}: {e}")
                    _update_status(scanner_name, "error", str(e))
                finally:
                    _running_scanners.discard(scanner_name)  # B-05: Mark as not running
                time.sleep(5)

        # ── Interval-basierte Scanner (Crypto + ORB) ──
        for scanner_name, interval in SCHEDULE_INTERVAL.items():
            if now - last_run.get(scanner_name, 0) >= interval:
                # B-05: Check for overlap
                if scanner_name in _running_scanners:
                    log.debug(f"⚠️ {scanner_name} bereits im Betrieb, überspringe")
                    continue
                _running_scanners.add(scanner_name)
                try:
                    if scanner_name == "btc_divergence":
                        _run_btc_divergence(poly_key)
                    elif scanner_name == "new_listing":
                        _nls_results = _run_new_listing_scanner()
                        _alert_nls_signals(_nls_results, secrets)
                    elif scanner_name == "orb":
                        _run_orb_scanner(poly_key)
                        _check_and_alert_scan_results("orb", secrets)
                    elif scanner_name == "signal_eval":
                        # Bewusst ohne secrets-Arg (Wiring-Kontrakt
                        # test_tracker_bg_frontend): der Job laedt secrets
                        # selbst lazy, nur wenn Transitionen anstehen.
                        _run_signal_eval_job()
                    elif scanner_name == "weekly_report":
                        # Freitags-Wochenbilanz; Job ist self-gated und
                        # wirft nie (Fenster/Dedupe/try-except intern).
                        _run_weekly_report(secrets)
                    last_run[scanner_name] = time.time()
                except Exception as e:
                    log.error(f"❌ {scanner_name}: {e}")
                    _update_status(scanner_name, "error", str(e))
                finally:
                    _running_scanners.discard(scanner_name)
                time.sleep(5)

        # 30 Sekunden schlafen
        for _ in range(30):
            if not _running: break
            time.sleep(1)

    if PID_FILE.exists():
        PID_FILE.unlink()
    _update_status("_service", "stopped")
    log.info("👋 Service beendet.")


def run_once():
    secrets = _load_secrets()
    poly_key = secrets.get("POLYGON_KEY", "")
    if not poly_key:
        print("❌ POLYGON_KEY fehlt!")
        return

    print("📡 Crash Monitor...")
    r = _fetch_crash_monitor(poly_key)
    if r: print(f"   Fear: {r.get('fear_score', '?')}/100")

    print("\n📡 BTC-Divergenz...")
    _run_btc_divergence(poly_key)

    print("\n📡 BI Scanner Long...")
    _run_bi_scanner(poly_key, "long")
    _check_and_alert_scan_results("bi_long", secrets)

    print("\n📡 Bear Scanner Short...")
    _run_bear_scanner(poly_key, secrets)
    # Bear Scanner hat eigene Alert-Logik in _run_bear_scanner()

    print("\n📡 Biotech Scanner...")
    try:
        _run_biotech_scanner(poly_key)
        _check_and_alert_scan_results("biotech", secrets)
    except Exception as e:
        print(f"   ❌ Biotech: {e}")

    print("\n📡 Strategien Scanner...")
    try:
        _run_strategy_scanner(poly_key, secrets)
    except Exception as e:
        print(f"   ❌ Strategien: {e}")

# Audit 2026-06-10: bg-Mail-Gates an api angeglichen (Q3/B4, B2, B3, B5, B6-B8).


if __name__ == "__main__":
    # WIEDERHERGESTELLT (Live-Diagnose 11.06.2026): Der Startblock wurde in
    # Commit 2a78cb5 ("AUDIT FIX: 16 Bugs behoben", Fremd-KI) versehentlich
    # entfernt — `python3 bg_service.py` importierte seither nur und endete mit
    # Exit 0 => systemd-Restart-Schleife ("Deactivated successfully",
    # Restart-Counter > 3500) und KEIN bg-Scan/-Alert lief jemals an.
    # Default = "start", damit die systemd-Unit mit UND ohne Argument startet.
    _cmd = sys.argv[1].lower() if len(sys.argv) > 1 else "start"
    if _cmd == "start":
        run_service()
    elif _cmd == "once":
        run_once()
    else:
        print(__doc__)
        print("Befehle: start | once  (Default ohne Argument: start)")

