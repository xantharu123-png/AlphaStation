"""Alpha Station — Signal-Tracker: belegbarer Track-Record fuer Trade-Alerts.

Jede versendete Trade-Alert-Row (mail_class="trade") wird als Signal in einer
SQLite-Datenbank geloggt und ueber die Folgetage automatisch gegen Stop/TP1/TP2
ausgewertet. Ergebnis: nachvollziehbare Hit-Rates und R-Multiples pro Scanner.

API-Kontrakt (von api.py / bg_service.py konsumiert):
  - record_alert_signals(scanner_name, rows, mail_class, channel) -> int
  - evaluate_open_signals(stock_daily_fetcher, crypto_price_fetcher, now) -> dict
  - load_performance_summary(days) -> dict
  - shadow_summary(days) -> dict
  - get_signal_count() -> int

Shadow-Tracking (AUDIT 2026-07-31): mail_class='shadow' markiert Signale, die
die Swing-Timing-Gates NICHT gemailt haben (Chase-Schutz). Sie werden mit
denselben Eval-Regeln weiterverfolgt, loesen aber KEINE Mails aus (bg_service
filtert ihre Transitionen/BE-Aktivierungen) und fliessen NIE in
load_performance_summary ein — auswertbar nur ueber shadow_summary(). Damit
wird in einigen Wochen messbar, was die Gates kosten bzw. sparen
(Selektionsproblem: bisher liess sich nur die gemailte Teilmenge beobachten).

Designgrundsaetze:
  - KEINE Funktion dieses Moduls wirft Exceptions nach aussen: Alert-Versand
    und Background-Loops duerfen niemals am Tracking scheitern. Fehler werden
    geloggt und ueber neutrale Rueckgabewerte signalisiert.
  - Der DB-Pfad wird bei JEDEM Aufruf frisch aus der modulglobalen Variable
    SIGNAL_DB_PATH gelesen — Tests koennen sie per monkeypatch ueberschreiben.
  - Schreibzugriffe sind ueber einen modulglobalen threading.Lock
    serialisiert; jede Operation nutzt eine eigene kurzlebige Connection
    (WAL-Mode, busy_timeout); das Schema wird idempotent migriert
    (CREATE TABLE IF NOT EXISTS).

Status-Modell eines Signals:
  OPEN       — laeuft noch (ggf. TP1 bereits erreicht -> tp1_hit_at gesetzt)
  STOP_HIT   — Stop erreicht, r_realized = -1.0. outcome_detail
               'ambiguous_same_day', wenn Stop und ein TP am selben Tag lagen
               (konservativ: Stop zuerst gewertet, kein TP gutgeschrieben).
  TP2_HIT    — TP2 erreicht, r_realized = Geometrie-R von TP2 (TP1 impliziert).
  EXPIRED    — Laufzeit abgelaufen (Aktien: 5 Daily-Bars nach Alert; Crypto:
               120h nach created_at). r_realized = R des letzten bekannten
               Preises; outcome_detail 'tp1_then_expired', wenn TP1 vorher
               erreicht wurde.
  UNTRACKED  — 5 fehlgeschlagene Bewertungsversuche (keine Kursdaten);
               r_realized bleibt NULL und zaehlt nicht in Win-Rate/avg_r.

Limitation Crypto: Die Bewertung ist ein Best-Effort-Spot-Check auf Basis des
aktuellen Preises zum Zeitpunkt des Evaluierungslaufs (z.B. stuendlich).
Es gibt KEINEN High/Low-Pfad zwischen zwei Checks — kurze Spikes durch Stop
oder TP zwischen zwei Laeufen werden nicht erkannt. Aktien werden dagegen
praezise ueber Daily-OHLC-Bars der Folgetage ausgewertet.
"""

from __future__ import annotations

import inspect
import json
import logging
import math
import os
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from modules.trade_levels import trade_geometry

logger = logging.getLogger(__name__)

__all__ = [
    "SIGNAL_DB_PATH",
    "CRYPTO_SCANNERS",
    "extract_signal_fields",
    "record_alert_signals",
    "evaluate_open_signals",
    "load_performance_summary",
    "load_breaker_recovery_summary",
    "get_signal_count",
    "breakeven_win_rate_pct",
    "scanner_verdict",
]

# ── Pfad-Konfiguration (gleiches Muster wie modules/auth.py) ─────────────────
_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("ALPHA_DATA_DIR", _REPO_ROOT / "data_cache"))

#: Modulglobaler DB-Pfad. Wird von allen Funktionen bei JEDEM Aufruf neu
#: gelesen, damit Tests/Betreiber ihn zur Laufzeit umbiegen koennen
#: (z.B. monkeypatch.setattr(signal_tracker, "SIGNAL_DB_PATH", ...)).
SIGNAL_DB_PATH: str = os.environ.get(
    "SIGNAL_TRACKER_DB_PATH", str(_DATA_DIR / "signal_tracker.sqlite")
)

#: Modulglobaler Lock — serialisiert alle DB-Zugriffe dieses Prozesses.
_DB_LOCK = threading.Lock()

#: Scanner, deren Signale als Krypto-Assets bewertet werden (Spot-Check statt
#: Daily-OHLC). Muss mit api._CRYPTO_SIGNAL_ONLY_SCANNERS uebereinstimmen.
CRYPTO_SCANNERS = {
    "early_movers",
    "crypto_trade_signals",
    "crypto_explosion",
    "new_listing",
    "btc_divergenz",
    "crypto_strategy",
}

STATUS_OPEN = "OPEN"
STATUS_STOP = "STOP_HIT"
STATUS_TP2 = "TP2_HIT"
STATUS_EXPIRED = "EXPIRED"
STATUS_UNTRACKED = "UNTRACKED"
STATUS_NO_FILL = "NO_FILL"
#: Virtueller Transitions-Status (NIE in der DB): TP1 wurde in DIESEM
#: Eval-Lauf erreicht, das Signal bleibt aber OPEN (Teilgewinn, Rest
#: Freiroll Richtung TP2). Nur fuer result['transitions'] der Funktion
#: evaluate_open_signals — Konsument: Exit-Update-Mails in bg_service.
STATUS_TP1_OPEN = "TP1_HIT_OPEN"

STOCK_EXPIRY_BARS = 5       # Handelstage (= Daily-Bars) nach dem Alert
CRYPTO_EXPIRY_HOURS = 120   # Stunden nach created_at
MAX_EVAL_FAILS = 5          # danach status = UNTRACKED

# Tolerante Feld-Aliase — gleiche Logik wie die Alert-Pipeline der App.
_TICKER_KEYS = ("ticker", "Ticker", "symbol", "Symbol")
_ENTRY_KEYS = ("Entry", "entry")
_STOP_KEYS = ("StopLoss", "stop_loss", "stop")
_TP1_KEYS = ("TP1", "tp1")
_TP2_KEYS = ("TP2", "tp2")
_PRICE_KEYS = ("price", "Preis", "current_price")
_GRADE_KEYS = ("grade", "Grade", "BI_Grade")
_SCORE_KEYS = ("score", "Score", "BI_Score")
_RVOL_KEYS = ("rvol", "RVOL")
_DIRECTION_KEYS = ("direction", "Direction")
_INSTRUMENT_ID_KEYS = ("coin_id", "CoinId", "CoinID", "ID", "instrument_id")
_VENUE_KEYS = ("venue", "Venue", "exchange", "Exchange", "BestExchange", "PerpChartExchange")
_CONTRACT_KEYS = (
    "contract_symbol",
    "ContractSymbol",
    "contract",
    "PerpChartSymbol",
    "PerpMatchSymbol",
)


# ── Zeit- und Parsing-Helfer ─────────────────────────────────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    """ISO-String tolerant als UTC-datetime parsen (naive Werte gelten als UTC)."""
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _coerce_now(now: Any) -> datetime:
    """Injizierten now-Parameter (Tests!) auf eine UTC-datetime normalisieren."""
    if isinstance(now, datetime):
        if now.tzinfo is None:
            return now.replace(tzinfo=timezone.utc)
        return now.astimezone(timezone.utc)
    return _utc_now()


def _to_float(value: Any) -> Optional[float]:
    """Tolerante Zahl-Konvertierung ('$1,234.56', '12,34', 12.5, ...) -> float|None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        result = float(value)
        return result if math.isfinite(result) else None
    text = str(value).strip().replace("$", "").replace("%", "").replace(" ", "")
    if not text:
        return None
    if "," in text and "." in text:
        text = text.replace(",", "")     # '1,234.56' -> '1234.56'
    elif "," in text:
        text = text.replace(",", ".")    # '12,34'    -> '12.34'
    try:
        result = float(text)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _first_raw(row: Dict[str, Any], keys: Iterable[str]) -> Any:
    """Ersten nicht-leeren Wert der Alias-Schluessel liefern (oder None)."""
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def extract_signal_fields(row: Any) -> Dict[str, Any]:
    """Extrahiert die Trade-Level-Felder einer Alert-Row mit toleranten Aliasen.

    Aliase: ticker/Ticker/symbol/Symbol; direction/Direction (Default LONG,
    'short' im String -> SHORT); Entry/entry; StopLoss/stop_loss/stop;
    TP1/tp1; TP2/tp2; price/Preis/current_price (-> price_at_alert);
    grade/Grade/BI_Grade; score/Score/BI_Score; rvol/RVOL.

    Wird auch von modules/notify_telegram.py genutzt, damit beide Module
    exakt dieselbe Alias-Logik verwenden. Liefert immer ein Dict (fehlende
    Werte = None, Ticker auf Grossschreibung normalisiert); wirft nie.
    """
    out: Dict[str, Any] = {
        "ticker": None,
        "direction": "LONG",
        "entry": None,
        "stop": None,
        "tp1": None,
        "tp2": None,
        "price_at_alert": None,
        "grade": None,
        "score": None,
        "rvol": None,
        "instrument_id": None,
        "venue": None,
        "contract_symbol": None,
    }
    if not isinstance(row, dict):
        return out
    try:
        raw_ticker = _first_raw(row, _TICKER_KEYS)
        if raw_ticker is not None:
            ticker = str(raw_ticker).strip().upper()
            out["ticker"] = ticker or None
        raw_direction = _first_raw(row, _DIRECTION_KEYS)
        if raw_direction is not None and "short" in str(raw_direction).strip().lower():
            out["direction"] = "SHORT"
        out["entry"] = _to_float(_first_raw(row, _ENTRY_KEYS))
        out["stop"] = _to_float(_first_raw(row, _STOP_KEYS))
        out["tp1"] = _to_float(_first_raw(row, _TP1_KEYS))
        out["tp2"] = _to_float(_first_raw(row, _TP2_KEYS))
        out["price_at_alert"] = _to_float(_first_raw(row, _PRICE_KEYS))
        raw_grade = _first_raw(row, _GRADE_KEYS)
        out["grade"] = str(raw_grade).strip() if raw_grade is not None else None
        out["score"] = _to_float(_first_raw(row, _SCORE_KEYS))
        out["rvol"] = _to_float(_first_raw(row, _RVOL_KEYS))
        for output_key, aliases in (
            ("instrument_id", _INSTRUMENT_ID_KEYS),
            ("venue", _VENUE_KEYS),
            ("contract_symbol", _CONTRACT_KEYS),
        ):
            raw_value = _first_raw(row, aliases)
            if raw_value is not None:
                value = str(raw_value).strip()
                out[output_key] = value or None
    except Exception as exc:  # pragma: no cover — reine Defensive
        logger.warning("extract_signal_fields: Row nicht lesbar: %s", exc)
    return out


# ── DB-Plumbing ──────────────────────────────────────────────────────────────
def _db_path() -> str:
    """Aktuellen DB-Pfad lesen — immer frisch, damit er patchbar bleibt."""
    return str(SIGNAL_DB_PATH)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS signals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    scanner TEXT NOT NULL,
    ticker TEXT NOT NULL,
    asset_class TEXT NOT NULL DEFAULT 'stock',
    direction TEXT NOT NULL DEFAULT 'LONG',
    entry REAL NOT NULL,
    stop REAL NOT NULL,
    tp1 REAL,
    tp2 REAL,
    price_at_alert REAL,
    grade TEXT,
    score REAL,
    rvol REAL,
    mail_class TEXT NOT NULL DEFAULT 'trade',
    channel TEXT NOT NULL DEFAULT 'email',
    status TEXT NOT NULL DEFAULT 'OPEN',
    outcome_detail TEXT NOT NULL DEFAULT '',
    tp1_hit_at TEXT,
    tp2_hit_at TEXT,
    stop_hit_at TEXT,
    closed_at TEXT,
    r_realized REAL,
    max_favorable_r REAL NOT NULL DEFAULT 0,
    max_adverse_r REAL NOT NULL DEFAULT 0,
    last_eval_at TEXT,
    eval_fail_count INTEGER NOT NULL DEFAULT 0
    ,entry_filled_at TEXT
    ,entry_fill_price REAL
    ,instrument_id TEXT
    ,venue TEXT
    ,contract_symbol TEXT
)
"""

_SCHEMA_MIGRATIONS = {
    "entry_filled_at": "TEXT",
    "entry_fill_price": "REAL",
    "instrument_id": "TEXT",
    "venue": "TEXT",
    "contract_symbol": "TEXT",
    "be_activated_at": "TEXT",
    "r_realized_be": "REAL",
    "rates_json": "TEXT",
    # Shadow-Tracking (AUDIT 2026-07-31): bei mail_class='shadow' die Gruende,
    # warum das Signal NICHT gemailt wurde (komma-getrennt, max. 500 Zeichen).
    "block_reasons": "TEXT",
}

# Zins-Block (modules/treasury_rates) als kompakte Annotation pro Signal
# (Mess-First 2026-07-30: Phase-2-Regime-Auswertung liest rates_json).
_RATES_KEEP_KEYS = (
    "as_of", "source", "stale", "dgs2", "dgs10", "dgs30",
    "change_5d_bp", "change_20d_bp", "dgs30_change_20d_bp",
    "curve_10s2s_bp", "curve_30s10s_bp", "regime",
)


def _compact_rates_json(rates_context: Optional[Dict[str, Any]]) -> Optional[str]:
    """Kompaktes JSON des Zins-Blocks fuer die rates_json-Spalte.

    Nur status == 'ok' mit Datum wird gespeichert; Missing/Fehler -> None
    (ehrlich leer statt erfundener Kontext). Wirft nie.
    """
    try:
        if not isinstance(rates_context, dict) or rates_context.get("status") != "ok":
            return None
        payload = {k: rates_context.get(k) for k in _RATES_KEEP_KEYS if k in rates_context}
        if not payload.get("as_of"):
            return None
        return json.dumps(payload, separators=(",", ":"))
    except Exception:  # pragma: no cover - defensiv
        return None

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status)",
    "CREATE INDEX IF NOT EXISTS idx_signals_scanner_ticker ON signals(scanner, ticker)",
    "CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals(created_at)",
)


@contextmanager
def _db_connection():
    """Kurzlebige Connection mit WAL-Mode und idempotenter Schema-Migration.

    Commit bei Erfolg, Rollback bei Exception, Close immer — eine Operation
    ist damit atomar (Aufrufer haelt waehrenddessen _DB_LOCK).
    """
    path = Path(_db_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute(_SCHEMA)
        existing_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        for column, column_type in _SCHEMA_MIGRATIONS.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {column_type}")
        for statement in _INDEXES:
            conn.execute(statement)
        yield conn
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:  # pragma: no cover
            pass
        raise
    finally:
        conn.close()


# ── Recording ────────────────────────────────────────────────────────────────
def record_alert_signals(
    scanner_name: str,
    rows: list,
    mail_class: str = "trade",
    channel: str = "email",
    rates_context: Optional[Dict[str, Any]] = None,
) -> int:
    """Loggt versendete Alert-Rows als offene Signale. Wirft nie.

    Regeln:
      - mail_class "trade" (gemailte Signale) und "shadow" (AUDIT 2026-07-31:
        von den Swing-Timing-Gates NICHT gemailte Signale, still
        weiterverfolgt) werden geloggt; alles andere -> Rueckgabe 0.
        Shadow-Rows tragen ihre Block-Gruende im Row-Feld 'block_reasons'
        (komma-getrennt) und fliessen NIRGENDS in Win-Rate/Verdikt ein
        (load_performance_summary filtert mail_class='trade').
      - Felder werden tolerant extrahiert (siehe extract_signal_fields).
      - Pflichtfelder pro Row: ticker, entry, stop, tp1 und tp2 (numerisch),
        direction (Default LONG). Die komplette Trade-Geometrie muss gueltig
        sein: LONG stop < entry < tp1 < tp2, SHORT spiegelverkehrt.
        Ungueltige Rows werden nicht in die Erfolgsstatistik aufgenommen.
      - asset_class = 'crypto', wenn scanner_name in CRYPTO_SCANNERS,
        sonst 'stock'.
      - Dedupe: Existiert bereits ein OPEN-Signal mit gleichem
        (scanner, ticker, mail_class), wird die Row uebersprungen (gilt auch
        innerhalb eines Batches). Die mail_class im Dedupe-Key stellt sicher,
        dass ein Shadow-Signal weder eine spaetere echte Mail desselben
        Tickers blockiert noch von ihr blockiert wird.
      - rates_context: optionaler Zins-Block (modules/treasury_rates) aus dem
        Market-Context; wird kompakt als rates_json annotiert (kein Gate).

    Returns:
        Anzahl neu geloggter Signale (0 bei Fehler/Filter).
    """
    inserted = 0
    try:
        mail_norm = str(mail_class or "").strip().lower()
        if mail_norm not in ("trade", "shadow"):
            return 0
        if not rows or not isinstance(rows, (list, tuple)):
            return 0
        scanner = str(scanner_name or "").strip()
        if not scanner:
            logger.warning("record_alert_signals: leerer scanner_name — Rows werden nicht geloggt")
            return 0
        asset_class = "crypto" if scanner in CRYPTO_SCANNERS else "stock"
        channel_norm = str(channel or "email").strip().lower() or "email"
        now_iso = _utc_iso()
        rates_json = _compact_rates_json(rates_context)
        with _DB_LOCK:
            with _db_connection() as conn:
                for row in rows:
                    try:
                        fields = extract_signal_fields(row)
                        ticker = fields["ticker"]
                        entry = fields["entry"]
                        stop = fields["stop"]
                        direction = fields["direction"]
                        if not ticker or entry is None or stop is None:
                            continue
                        geometry = trade_geometry(
                            entry,
                            stop,
                            fields["tp1"],
                            fields["tp2"],
                            direction,
                        )
                        if not geometry.get("valid"):
                            logger.debug(
                                "record_alert_signals: %s/%s unplausible Geometrie "
                                "(entry=%s stop=%s %s) — uebersprungen",
                                scanner, ticker, entry, stop, direction,
                            )
                            continue
                        instrument_id = fields.get("instrument_id")
                        venue = fields.get("venue")
                        contract_symbol = fields.get("contract_symbol")
                        if asset_class == "crypto" and instrument_id:
                            exists = conn.execute(
                                "SELECT 1 FROM signals WHERE scanner = ? AND instrument_id = ? "
                                "AND status = ? AND mail_class = ? LIMIT 1",
                                (scanner, instrument_id, STATUS_OPEN, mail_norm),
                            ).fetchone()
                        elif asset_class == "crypto" and venue and contract_symbol:
                            exists = conn.execute(
                                "SELECT 1 FROM signals WHERE scanner = ? AND venue = ? "
                                "AND contract_symbol = ? AND status = ? AND mail_class = ? LIMIT 1",
                                (scanner, venue, contract_symbol, STATUS_OPEN, mail_norm),
                            ).fetchone()
                        else:
                            exists = conn.execute(
                                "SELECT 1 FROM signals WHERE scanner = ? AND ticker = ? "
                                "AND status = ? AND mail_class = ? LIMIT 1",
                                (scanner, ticker, STATUS_OPEN, mail_norm),
                            ).fetchone()
                        if exists:
                            continue
                        fill_at = None
                        fill_price = None
                        alert_price = fields["price_at_alert"]
                        if asset_class == "crypto" and alert_price is not None:
                            if direction == "LONG" and entry <= alert_price < fields["tp1"]:
                                fill_at = now_iso
                                fill_price = float(alert_price)
                            elif direction == "SHORT" and fields["tp1"] < alert_price <= entry:
                                fill_at = now_iso
                                fill_price = float(alert_price)
                        block_reasons_text = ""
                        if mail_norm == "shadow":
                            block_reasons_text = str(row.get("block_reasons") or "")[:500]
                        conn.execute(
                            """
                            INSERT INTO signals (
                                created_at, scanner, ticker, asset_class, direction,
                                entry, stop, tp1, tp2, price_at_alert, grade, score,
                                rvol, mail_class, channel, status, outcome_detail,
                                entry_filled_at, entry_fill_price, instrument_id,
                                venue, contract_symbol, rates_json, block_reasons
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                now_iso, scanner, ticker, asset_class, direction,
                                float(entry), float(stop), fields["tp1"], fields["tp2"],
                                fields["price_at_alert"], fields["grade"], fields["score"],
                                fields["rvol"], mail_norm, channel_norm, STATUS_OPEN, "",
                                fill_at, fill_price, instrument_id, venue, contract_symbol,
                                rates_json, block_reasons_text,
                            ),
                        )
                        inserted += 1
                    except Exception as row_exc:
                        logger.warning(
                            "record_alert_signals: Row uebersprungen (scanner=%s): %s",
                            scanner, row_exc,
                        )
        if inserted:
            logger.info(
                "Signal-Tracker: %d neue(s) Signal(e) fuer Scanner '%s' geloggt (channel=%s)",
                inserted, scanner, channel_norm,
            )
        return inserted
    except Exception as exc:
        # Rollback der Connection verwirft alle Inserts dieses Aufrufs.
        logger.warning("record_alert_signals fehlgeschlagen (scanner=%s): %s", scanner_name, exc)
        return 0


# ── Evaluierung ──────────────────────────────────────────────────────────────
def _signed_r(price: float, entry: float, risk: float, direction: str) -> float:
    """Signiertes R-Multiple: positiv = in Trade-Richtung im Gewinn."""
    if direction == "SHORT":
        return (entry - price) / risk
    return (price - entry) / risk


def _managed_r_50_50(row: Dict[str, Any]) -> Optional[float]:
    """R-Multiple des empfohlenen 50/50-Managements (TP1 = 50% Teilverkauf).

    AUDIT 2026-07-24 (T1): r_realized bucht das Level-R (TP2 = volles
    Geometrie-R). Die Handlungsempfehlung verkauft aber 50% am TP1. Diese
    Funktion leitet das befolgbare Management-R retroaktiv aus den
    gespeicherten Feldern ab:
      - TP1 nicht erreicht: managed = r_realized (beide Modelle identisch)
      - TP2_HIT:            managed = 0.5*r_tp1 + 0.5*r_tp2
      - STOP_HIT nach TP1:  managed = 0.5*r_tp1 + 0.5*r_stop_exit
      - EXPIRED nach TP1:   managed = 0.5*r_tp1 + 0.5*r_close
    Bei unvollstaendigen Levels faellt die Funktion auf r_realized zurueck
    (besser ein Level-R als gar kein Wert); None nur bei fehlendem r_realized.
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None
    tp1 = _to_float(row.get("tp1"))
    if tp1 is None:
        return realized
    tp1_hit = bool(row.get("tp1_hit_at")) or row.get("status") == STATUS_TP2
    if not tp1_hit:
        return realized
    fill = _to_float(row.get("entry_fill_price"))
    if fill is None:
        fill = _to_float(row.get("entry"))
    stop = _to_float(row.get("stop"))
    tp2 = _to_float(row.get("tp2"))
    if fill is None or stop is None:
        return realized
    direction = "SHORT" if str(row.get("direction")) == "SHORT" else "LONG"
    geometry = trade_geometry(fill, stop, tp1, tp2, direction)
    risk = geometry.get("risk")
    if not geometry.get("valid") or not risk:
        return realized
    r_tp1 = _signed_r(tp1, fill, risk, direction)
    return round(0.5 * r_tp1 + 0.5 * realized, 4)


def simulate_breakeven_after_mfe(row: Dict[str, Any], mfe_trigger: float = 1.0) -> Optional[float]:
    """Gegenprobe 'Breakeven-Stop nach +mfe_trigger R' (AUDIT 2026-07-29).

    Die Exit-Effizienz-Messung (MFE-Nutzung -22%) legt nahe, dass offene
    Gewinne systematisch verschenkt werden. Diese reine Funktion simuliert
    die einfachste Gegenregel auf den gespeicherten Feldern:
      - MFE < Trigger        → unveraendert (Regel greift nie)
      - MFE >= Trigger, Gewinner → unveraendert (BE-Stop schadet nicht)
      - MFE >= Trigger, Verlierer/<=0 → 0.0 (BE-Stop haette die Null gerettet)
    Konservative Ausnahme: outcome_detail 'ambiguous_same_day' (Stop und Ziel
    am selben Tag, Intraday-Reihenfolge unbekannt) bleibt beim realisierten
    Wert — dort ist nicht belegbar, dass der MFE VOR dem Stop lag.
    None nur bei fehlendem r_realized.
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None
    mfe = _to_float(row.get("max_favorable_r"))
    if mfe is None or mfe < mfe_trigger:
        return realized
    if realized >= 0:
        return realized
    if str(row.get("outcome_detail") or "") == "ambiguous_same_day":
        return realized
    return 0.0


def simulate_managed_5050_breakeven(row: Dict[str, Any]) -> Optional[float]:
    """Gegenprobe '50/50-Management + Breakeven-Rest nach TP1' (2026-07-29).

    Strengere Variante der bestehenden Empfehlung: TP1 = 50% raus, Rest laeuft
    mit Stop auf Einstand — die zweite Haelfte kann danach nicht mehr negativ
    enden. TP1 nie erreicht: faellt auf die BE-nach-+1R-Regel zurueck.
    None nur bei fehlendem r_realized.
    """
    base = _managed_r_50_50(row)
    if base is None:
        return None
    tp1_hit = bool(row.get("tp1_hit_at")) or row.get("status") == STATUS_TP2
    if not tp1_hit:
        return simulate_breakeven_after_mfe(row, 1.0)
    realized = _to_float(row.get("r_realized"))
    if realized is None or realized >= 0:
        return base
    # base = 0.5*r_tp1 + 0.5*realized; BE ersetzt den negativen Rest durch 0.
    return round(base - 0.5 * realized, 4)


def _register_eval_failure(sig: Dict[str, Any], now_dt: datetime) -> Dict[str, Any]:
    """Fehlversuch zaehlen; ab MAX_EVAL_FAILS Fehlversuchen -> UNTRACKED."""
    fail_count = int(sig.get("eval_fail_count") or 0) + 1
    updates: Dict[str, Any] = {
        "eval_fail_count": fail_count,
        "last_eval_at": now_dt.isoformat(),
    }
    if fail_count >= MAX_EVAL_FAILS:
        updates["status"] = STATUS_UNTRACKED
        updates["closed_at"] = now_dt.isoformat()
        updates["outcome_detail"] = "eval_failed_%dx" % fail_count
        logger.warning(
            "Signal %s/%s nach %d Fehlversuchen ohne Kursdaten -> UNTRACKED",
            sig.get("scanner"), sig.get("ticker"), fail_count,
        )
    return updates


def _parse_bar_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


def _normalize_daily_bars(
    bars_raw: Any, created_date: date
) -> List[Tuple[date, float, float, float, float]]:
    """Bars validieren, auf Folgetage (> Alert-Datum) filtern und sortieren.

    Der Alert-Tag selbst wird bewusst NICHT bewertet: seine Daily-Bar enthaelt
    auch die Kursbewegung VOR dem Alert und waere damit nicht aussagekraeftig.
    """
    bars: List[Tuple[date, float, float, float, float]] = []
    for bar in bars_raw or []:
        if not isinstance(bar, dict):
            continue
        bar_date = _parse_bar_date(bar.get("date"))
        high = _to_float(bar.get("high"))
        low = _to_float(bar.get("low"))
        close = _to_float(bar.get("close"))
        open_price = _to_float(bar.get("open"))
        if bar_date is None or high is None or low is None or close is None:
            continue
        if bar_date <= created_date:
            continue
        if open_price is None:
            # Legacy fetchers did not expose open. Falling back to close keeps
            # old records trackable, while production fetchers provide OHLC.
            open_price = close
        if min(open_price, high, low, close) <= 0 or high < max(open_price, close) or low > min(open_price, close):
            continue
        bars.append((bar_date, open_price, high, low, close))
    bars.sort(key=lambda item: item[0])
    return bars


def _evaluate_stock_signal(
    sig: Dict[str, Any],
    fetcher: Callable[[str, str], Any],
    now_dt: datetime,
) -> Tuple[Dict[str, Any], bool]:
    """Aktien-Signal praezise ueber Daily-OHLC bewerten.

    Liefert (updates, fetch_failed). Chronologisch pro Folgetag-Bar:
      LONG:  low<=stop UND (neuer) TP am selben Tag -> AMBIGUOUS, konservativ
             Stop zuerst (STOP_HIT, outcome_detail 'ambiguous_same_day');
             sonst low<=stop -> STOP_HIT; high>=tp2 -> TP2_HIT (impliziert
             TP1); high>=tp1 -> tp1_hit_at setzen und OPEN weiterlaufen.
      SHORT: spiegelverkehrt.
    max_favorable_r / max_adverse_r werden pro Bar mitgefuehrt
    (r = signiertes R-Multiple; max_adverse_r ist das Minimum, also <= 0).
    Expiry: nach STOCK_EXPIRY_BARS Bars ohne Stop/TP2 -> EXPIRED mit
    r_realized = R des letzten Close (outcome_detail 'tp1_then_expired',
    falls TP1 vorher erreicht war).
    """
    created_dt = _parse_utc_datetime(sig.get("created_at")) or now_dt
    created_date = created_dt.astimezone(ZoneInfo("America/New_York")).date()
    bars_raw = fetcher(sig["ticker"], created_date.isoformat())
    if not bars_raw:
        return _register_eval_failure(sig, now_dt), True

    entry = float(sig["entry"])
    stop = float(sig["stop"])
    tp1 = float(sig["tp1"]) if sig.get("tp1") is not None else None
    tp2 = float(sig["tp2"]) if sig.get("tp2") is not None else None
    direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    planned_risk = geometry.get("risk")
    if not geometry.get("valid") or planned_risk is None:
        return _register_eval_failure(sig, now_dt), True

    now_iso = now_dt.isoformat()
    updates: Dict[str, Any] = {"last_eval_at": now_iso}
    tp1_hit_at = sig.get("tp1_hit_at") or None
    max_fav = float(sig.get("max_favorable_r") or 0.0)
    max_adv = float(sig.get("max_adverse_r") or 0.0)
    fill_at = sig.get("entry_filled_at") or None
    fill_price = _to_float(sig.get("entry_fill_price"))
    fill_date = _parse_bar_date(fill_at) if fill_at else None
    bars_after_alert = 0
    holding_bars = 0

    for bar_date, open_price, high, low, close in _normalize_daily_bars(bars_raw, created_date):
        bars_after_alert += 1
        if fill_price is None:
            if direction == "LONG":
                if open_price >= tp1:
                    updates.update({
                        "status": STATUS_NO_FILL,
                        "closed_at": now_iso,
                        "outcome_detail": "entry_gapped_beyond_tp1",
                    })
                    break
                if open_price >= entry:
                    fill_price = open_price
                elif low <= entry <= high:
                    fill_price = entry
            else:
                if open_price <= tp1:
                    updates.update({
                        "status": STATUS_NO_FILL,
                        "closed_at": now_iso,
                        "outcome_detail": "entry_gapped_beyond_tp1",
                    })
                    break
                if open_price <= entry:
                    fill_price = open_price
                elif low <= entry <= high:
                    fill_price = entry
            if fill_price is None:
                if bars_after_alert >= STOCK_EXPIRY_BARS:
                    updates.update({
                        "status": STATUS_NO_FILL,
                        "closed_at": now_iso,
                        "outcome_detail": "entry_not_reached",
                    })
                    break
                continue
            fill_date = bar_date
            fill_at = bar_date.isoformat()
            updates["entry_filled_at"] = fill_at
            updates["entry_fill_price"] = round(fill_price, 8)

        if fill_date is not None and bar_date < fill_date:
            continue
        actual_geometry = trade_geometry(fill_price, stop, tp1, tp2, direction)
        risk = actual_geometry.get("risk")
        if not actual_geometry.get("valid") or risk is None:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "fill_invalidated_trade_geometry",
            })
            break
        holding_bars += 1
        if direction == "LONG":
            stop_hit = low <= stop
            tp2_hit = tp2 is not None and high >= tp2
            tp1_touch = tp1 is not None and high >= tp1
            favorable = (high - fill_price) / risk
            adverse = (low - fill_price) / risk
        else:
            stop_hit = high >= stop
            tp2_hit = tp2 is not None and low <= tp2
            tp1_touch = tp1 is not None and low <= tp1
            favorable = (fill_price - low) / risk
            adverse = (fill_price - high) / risk
        max_fav = max(max_fav, favorable)
        max_adv = min(max_adv, adverse)
        day_iso = bar_date.isoformat()

        if stop_hit:
            # Stop und ein NEUES TP-Level am selben Tag -> Reihenfolge unklar:
            # konservativ den Stop zuerst werten, TP nicht gutschreiben.
            ambiguous = tp2_hit or (tp1_touch and not tp1_hit_at)
            if direction == "LONG":
                stop_exit = open_price if open_price < stop else stop
            else:
                stop_exit = open_price if open_price > stop else stop
            updates.update({
                "status": STATUS_STOP,
                "stop_hit_at": day_iso,
                "closed_at": now_iso,
                "r_realized": round(_signed_r(stop_exit, fill_price, risk, direction), 4),
                "outcome_detail": "ambiguous_same_day" if ambiguous else "",
            })
            break
        if tp2_hit:
            if tp1 is not None and not tp1_hit_at:
                tp1_hit_at = day_iso  # TP2 impliziert TP1
            updates.update({
                "status": STATUS_TP2,
                "tp2_hit_at": day_iso,
                "closed_at": now_iso,
                "r_realized": round(_signed_r(tp2, fill_price, risk, direction), 4),
                "outcome_detail": "",
            })
            break
        if tp1_touch and not tp1_hit_at:
            tp1_hit_at = day_iso
        if holding_bars >= STOCK_EXPIRY_BARS:
            updates.update({
                "status": STATUS_EXPIRED,
                "closed_at": now_iso,
                "r_realized": round(_signed_r(close, fill_price, risk, direction), 4),
                "outcome_detail": "tp1_then_expired" if tp1_hit_at else "",
            })
            break

    if tp1_hit_at:
        updates["tp1_hit_at"] = tp1_hit_at
    updates["max_favorable_r"] = round(max_fav, 4)
    updates["max_adverse_r"] = round(max_adv, 4)
    return updates, False


def _fetch_crypto_price(fetcher: Callable[..., Any], sig: Dict[str, Any]) -> Any:
    """Call legacy and identity-aware crypto fetchers without masking errors."""
    ticker = sig["ticker"]
    identity = {
        "instrument_id": sig.get("instrument_id"),
        "venue": sig.get("venue"),
        "contract_symbol": sig.get("contract_symbol"),
    }
    try:
        parameters = inspect.signature(fetcher).parameters
    except (TypeError, ValueError):
        parameters = {}
    accepts_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    if accepts_kwargs or any(key in parameters for key in identity):
        supported = identity if accepts_kwargs else {
            key: value for key, value in identity.items() if key in parameters
        }
        return fetcher(ticker, **supported)
    return fetcher(ticker)


def _evaluate_crypto_signal(
    sig: Dict[str, Any],
    fetcher: Callable[..., Any],
    now_dt: datetime,
) -> Tuple[Dict[str, Any], bool]:
    """Crypto-Signal per Best-Effort-Spot-Check bewerten.

    LIMITATION: Es wird nur der aktuelle Preis geprueft (kein High/Low-Pfad
    zwischen zwei Evaluierungslaeufen) — kurze Spikes durch Stop oder TP
    zwischen zwei Checks werden nicht erkannt. Expiry: CRYPTO_EXPIRY_HOURS
    (120h) nach created_at, r_realized = R des letzten bekannten Preises.
    Stop/TP haben Vorrang vor dem Expiry-Check.
    """
    price = _to_float(_fetch_crypto_price(fetcher, sig))
    if price is None or price <= 0:
        return _register_eval_failure(sig, now_dt), True

    entry = float(sig["entry"])
    stop = float(sig["stop"])
    tp1 = float(sig["tp1"]) if sig.get("tp1") is not None else None
    tp2 = float(sig["tp2"]) if sig.get("tp2") is not None else None
    direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    if not geometry.get("valid"):
        return _register_eval_failure(sig, now_dt), True

    now_iso = now_dt.isoformat()
    created_dt = _parse_utc_datetime(sig.get("created_at"))
    expired = created_dt is not None and now_dt >= created_dt + timedelta(hours=CRYPTO_EXPIRY_HOURS)
    fill_at = sig.get("entry_filled_at") or None
    fill_price = _to_float(sig.get("entry_fill_price"))
    alert_price = _to_float(sig.get("price_at_alert"))
    updates: Dict[str, Any] = {"last_eval_at": now_iso}

    if fill_price is None:
        invalidated_before_fill = (
            direction == "LONG"
            and ((alert_price is not None and alert_price <= stop) or price <= stop)
        ) or (
            direction == "SHORT"
            and ((alert_price is not None and alert_price >= stop) or price >= stop)
        )
        if invalidated_before_fill:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_invalidated_before_fill",
            })
            return updates, False
        crossed_from_valid_side = (
            alert_price is not None
            and ((direction == "LONG" and alert_price < entry) or (direction == "SHORT" and alert_price > entry))
        )
        if direction == "LONG" and price >= entry:
            if price >= tp1 and not crossed_from_valid_side:
                updates.update({
                    "status": STATUS_NO_FILL,
                    "closed_at": now_iso,
                    "outcome_detail": "entry_observed_after_tp1",
                })
                return updates, False
            fill_price = entry if crossed_from_valid_side else price
        elif direction == "SHORT" and price <= entry:
            if price <= tp1 and not crossed_from_valid_side:
                updates.update({
                    "status": STATUS_NO_FILL,
                    "closed_at": now_iso,
                    "outcome_detail": "entry_observed_after_tp1",
                })
                return updates, False
            fill_price = entry if crossed_from_valid_side else price
        elif expired:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_not_reached",
            })
            return updates, False
        else:
            return updates, False
        fill_at = now_iso
        updates["entry_filled_at"] = fill_at
        updates["entry_fill_price"] = round(fill_price, 8)

    actual_geometry = trade_geometry(fill_price, stop, tp1, tp2, direction)
    risk = actual_geometry.get("risk")
    if not actual_geometry.get("valid") or risk is None:
        updates.update({
            "status": STATUS_NO_FILL,
            "closed_at": now_iso,
            "outcome_detail": "fill_invalidated_trade_geometry",
        })
        return updates, False

    r_now = _signed_r(price, fill_price, risk, direction)
    max_fav = max(float(sig.get("max_favorable_r") or 0.0), r_now)
    max_adv = min(float(sig.get("max_adverse_r") or 0.0), r_now)
    tp1_hit_at = sig.get("tp1_hit_at") or None
    updates.update({
        "max_favorable_r": round(max_fav, 4),
        "max_adverse_r": round(max_adv, 4),
    })

    if direction == "LONG":
        stop_hit = price <= stop
        tp2_hit = tp2 is not None and price >= tp2
        tp1_touch = tp1 is not None and price >= tp1
    else:
        stop_hit = price >= stop
        tp2_hit = tp2 is not None and price <= tp2
        tp1_touch = tp1 is not None and price <= tp1

    if stop_hit:
        updates.update({
            "status": STATUS_STOP,
            "stop_hit_at": now_iso,
            "closed_at": now_iso,
            "r_realized": round(r_now, 4),
            "outcome_detail": "",
        })
    elif tp2_hit:
        if tp1 is not None and not tp1_hit_at:
            tp1_hit_at = now_iso  # TP2 impliziert TP1
        updates.update({
            "status": STATUS_TP2,
            "tp2_hit_at": now_iso,
            "closed_at": now_iso,
            "r_realized": round(_signed_r(tp2, fill_price, risk, direction), 4),
            "outcome_detail": "",
        })
    else:
        if tp1_touch and not tp1_hit_at:
            tp1_hit_at = now_iso
        if expired:
            updates.update({
                "status": STATUS_EXPIRED,
                "closed_at": now_iso,
                "r_realized": round(r_now, 4),
                "outcome_detail": "tp1_then_expired" if tp1_hit_at else "",
            })

    if tp1_hit_at:
        updates["tp1_hit_at"] = tp1_hit_at
    return updates, False


def _apply_signal_updates(signal_id: int, updates: Dict[str, Any]) -> None:
    """Updates atomar auf ein Signal anwenden (Spalten stammen nur aus Code)."""
    if not updates:
        return
    columns = ", ".join("%s = ?" % key for key in updates)
    values = list(updates.values()) + [signal_id]
    with _DB_LOCK:
        with _db_connection() as conn:
            conn.execute("UPDATE signals SET %s WHERE id = ?" % columns, values)


class _EvalResult(dict):
    """Rueckgabe-Dict von evaluate_open_signals inkl. 'transitions'-Liste
    und 'be_activations'-Liste.

    ABWAERTSKOMPATIBILITAET: Bestands-Aufrufer und -Tests vergleichen das
    Ergebnis strikt mit {'evaluated','closed','errors'}-Dicts (z.B.
    ``result == {"evaluated": 1, "closed": 1, "errors": 0}``). Der
    Gleichheitsvergleich ignoriert deshalb beidseitig die Schluessel
    'transitions' und 'be_activations'. ALLE anderen Zugriffe (Iteration,
    .get, ['transitions'], 'in', json.dumps) sehen die Schluessel normal.
    """

    def __eq__(self, other: Any) -> Any:
        if not isinstance(other, dict):
            return NotImplemented
        extra_keys = ("transitions", "be_activations")
        self_cmp = {k: v for k, v in self.items() if k not in extra_keys}
        other_cmp = {k: v for k, v in other.items() if k not in extra_keys}
        return self_cmp == other_cmp

    def __ne__(self, other: Any) -> Any:
        eq = self.__eq__(other)
        return eq if eq is NotImplemented else not eq


def breakeven_adjusted_r(row: Dict[str, Any]) -> Optional[float]:
    """R unter der BE-Regel (Stop auf Einstand sobald MFE >= +1R) fuer eine Row.

    A/B-Messgroesse des Exit-Effizienz-Audits 2026-07-30: Ist-r_realized vs.
    R bei BE-Management. Regeln (konservativ):
      - kein r_realized (offen/untracked)          -> None
      - BE nie aktiviert (kein be_activated_at)    -> r_realized unveraendert
      - r_realized >= 0                            -> r_realized unveraendert
      - outcome_detail 'ambiguous_same_day'        -> r_realized (Intraday-
        Reihenfolge MFE/Stop unbewiesen — kein BE-Kredit)
      - BE aktiviert UND r_realized < 0            -> 0.0 (Ausstieg am Einstand)
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None
    if not row.get("be_activated_at"):
        return realized
    if realized >= 0:
        return realized
    if str(row.get("outcome_detail") or "") == "ambiguous_same_day":
        return realized
    return 0.0


def _transition_record(
    sig: Dict[str, Any],
    new_status: str,
    updates: Dict[str, Any],
    tp1_hit_this_run: bool,
) -> Dict[str, Any]:
    """Transitions-Dict fuer result['transitions'] bauen (Kontrakt s. Docstring
    von evaluate_open_signals). Plan-Level stammen aus der DB-Row, r_realized
    aus den Updates dieses Laufs (None bei TP1_HIT_OPEN/UNTRACKED)."""
    return {
        "id": int(sig["id"]),
        "ticker": sig.get("ticker"),
        "scanner": sig.get("scanner"),
        "mail_class": str(sig.get("mail_class") or "trade"),
        "direction": "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG",
        "old_status": str(sig.get("status") or STATUS_OPEN),
        "new_status": new_status,
        "entry": _to_float(sig.get("entry")),
        "entry_fill_price": _to_float(updates.get("entry_fill_price", sig.get("entry_fill_price"))),
        "stop": _to_float(sig.get("stop")),
        "tp1": _to_float(sig.get("tp1")),
        "tp2": _to_float(sig.get("tp2")),
        "r_realized": _to_float(updates.get("r_realized")),
        "tp1_hit_this_run": bool(tp1_hit_this_run),
        "asset_class": str(sig.get("asset_class") or "stock"),
    }


def evaluate_open_signals(
    stock_daily_fetcher: Optional[Callable[[str, str], Any]] = None,
    crypto_price_fetcher: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Bewertet alle OPEN-Signale gegen Stop/TP1/TP2. Wirft nie.

    Args:
        stock_daily_fetcher: Callable (ticker, since_iso_date) -> Liste von
            Daily-Bars [{'date', 'high', 'low', 'close'}, ...] oder None.
            Wird vom Aufrufer injiziert (z.B. Polygon-Fetcher); since_iso_date
            ist das Alert-Datum (YYYY-MM-DD). Rueckgabe None/[] zaehlt als
            Fehlversuch: eval_fail_count + 1, nach 5 Fehlversuchen wird das
            Signal auf status='UNTRACKED' gestellt.
        crypto_price_fetcher: Callable (ticker) -> aktueller Preis (float)
            oder None. Gleiche Fehlversuch-Logik.
        now: Optionale UTC-Zeit (datetime) fuer deterministische Tests;
            Default ist die aktuelle UTC-Zeit.

    Returns:
        {'evaluated': n, 'closed': n, 'errors': n, 'transitions': [...]}
          evaluated — Signale, fuer die ein Bewertungsversuch lief (passender
                      Fetcher injiziert; ohne Fetcher wird uebersprungen,
                      ohne dass ein Fehlversuch zaehlt)
          closed    — Signale, die in diesem Lauf einen terminalen Status
                      erreichten (STOP_HIT/TP2_HIT/EXPIRED/UNTRACKED)
          errors    — Fehlversuche (Fetcher lieferte None/[]/Exception)
          transitions — ein Dict je Statusaenderung DIESES Laufs (nur
                      erfolgreich persistierte Updates), Konsument sind die
                      Exit-Update-Mails in bg_service:
                      {'id', 'ticker', 'scanner', 'direction', 'old_status',
                       'new_status', 'entry', 'stop', 'tp1', 'tp2',
                       'r_realized', 'tp1_hit_this_run', 'asset_class'}
                      new_status: STOP_HIT/TP2_HIT/EXPIRED/UNTRACKED oder
                      der virtuelle Status 'TP1_HIT_OPEN' (TP1 in diesem Lauf
                      erreicht, Signal bleibt OPEN). r_realized ist None bei
                      TP1_HIT_OPEN/UNTRACKED. tp1_hit_this_run ist auch bei
                      TP2_HIT/EXPIRED True, wenn TP1 erst in diesem Lauf fiel.
                      ABWAERTSKOMPATIBEL: Gleichheitsvergleiche des
                      Rueckgabe-Dicts ignorieren 'transitions' und
                      'be_activations' (_EvalResult).
          be_activations — ein Dict je Signal, das in DIESEM Lauf erstmals
                      MFE >= +1R erreichte (be_activated_at persistiert):
                      {'id', 'ticker', 'scanner', 'direction', 'entry',
                       'entry_fill_price', 'stop', 'tp1', 'tp2', 'mfe',
                       'asset_class', 'activated_at'}
                      Konsument ist die Stop-Update-Mail in bg_service
                      (Breakeven-Empfehlung, Exit-Effizienz-Audit 2026-07-30).
                      Terminale Exits schreiben zusaetzlich r_realized_be
                      (breakeven_adjusted_r) fuer den Ist-vs-BE-Vergleich.

    Aktien werden praezise ueber Daily-OHLC der Folgetage bewertet (Daily-Bars
    implizieren US-Handelstage), Crypto nur als Best-Effort-Spot-Check des
    aktuellen Preises — siehe Modul-Docstring (Limitation: kein High/Low-Pfad
    zwischen zwei Laeufen). Crypto-Expiry: 120h nach created_at; Aktien-Expiry:
    5 Daily-Bars nach Alert.
    """
    result = _EvalResult({"evaluated": 0, "closed": 0, "errors": 0,
                          "transitions": [], "be_activations": []})
    try:
        now_dt = _coerce_now(now)
        with _DB_LOCK:
            with _db_connection() as conn:
                open_signals = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM signals WHERE status = ? ORDER BY id",
                        (STATUS_OPEN,),
                    ).fetchall()
                ]
        for sig in open_signals:
            is_crypto = str(sig.get("asset_class") or "") == "crypto"
            fetcher = crypto_price_fetcher if is_crypto else stock_daily_fetcher
            if fetcher is None:
                continue
            result["evaluated"] += 1
            try:
                if is_crypto:
                    updates, fetch_failed = _evaluate_crypto_signal(sig, fetcher, now_dt)
                else:
                    updates, fetch_failed = _evaluate_stock_signal(sig, fetcher, now_dt)
            except Exception as exc:
                logger.warning(
                    "Signal %s/%s: Bewertung fehlgeschlagen: %s",
                    sig.get("scanner"), sig.get("ticker"), exc,
                )
                updates, fetch_failed = _register_eval_failure(sig, now_dt), True
            if fetch_failed:
                result["errors"] += 1
            # BE-Trigger (Exit-Effizienz-Audit 2026-07-30): MFE >= +1R einmalig
            # als be_activated_at markieren — bg_service mailt dann die
            # Stop-auf-Einstand-Anweisung. Bei terminalem Exit zusaetzlich
            # r_realized_be (Ist-vs-BE-Vergleich) mitschreiben.
            mfe_now = _to_float(updates.get("max_favorable_r"))
            be_new = (
                not sig.get("be_activated_at")
                and mfe_now is not None
                and mfe_now >= 1.0
            )
            r_now = _to_float(updates.get("r_realized"))
            if be_new and r_now is not None and r_now < 0:
                # Aktivierung UND Verlust-Exit im selben Lauf: Intraday-
                # Reihenfolge unbewiesen -> konservativ KEINE Aktivierung.
                be_new = False
            if be_new:
                updates["be_activated_at"] = now_dt.isoformat()
            if r_now is not None:
                be_row = dict(sig)
                be_row.update(updates)
                updates["r_realized_be"] = breakeven_adjusted_r(be_row)
            new_status = updates.get("status")
            if new_status and new_status != STATUS_OPEN:
                result["closed"] += 1
            try:
                _apply_signal_updates(int(sig["id"]), updates)
            except Exception as exc:
                logger.warning("Signal %s: Update fehlgeschlagen: %s", sig.get("id"), exc)
                result["errors"] += 1
            else:
                # Transition nur fuer PERSISTIERTE Aenderungen melden: echter
                # Statuswechsel ODER TP1 in diesem Lauf erreicht (Signal
                # bleibt OPEN -> virtueller Status TP1_HIT_OPEN).
                tp1_hit_this_run = bool(updates.get("tp1_hit_at")) and not sig.get("tp1_hit_at")
                if new_status and new_status != STATUS_OPEN:
                    transition_status = new_status
                elif tp1_hit_this_run:
                    transition_status = STATUS_TP1_OPEN
                else:
                    transition_status = None
                if transition_status:
                    try:
                        result["transitions"].append(
                            _transition_record(sig, transition_status, updates, tp1_hit_this_run)
                        )
                    except Exception as exc:  # Defensive: darf Eval-Loop nie abbrechen
                        logger.warning("Signal %s: Transition nicht erfasst: %s", sig.get("id"), exc)
                # BE-Aktivierung nur fuer PERSISTIERTE Updates melden.
                if be_new:
                    try:
                        result["be_activations"].append({
                            "id": int(sig["id"]),
                            "ticker": sig.get("ticker"),
                            "scanner": sig.get("scanner"),
                            "mail_class": str(sig.get("mail_class") or "trade"),
                            "direction": "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG",
                            "entry": _to_float(sig.get("entry")),
                            "entry_fill_price": _to_float(
                                updates.get("entry_fill_price", sig.get("entry_fill_price"))
                            ),
                            "stop": _to_float(sig.get("stop")),
                            "tp1": _to_float(sig.get("tp1")),
                            "tp2": _to_float(sig.get("tp2")),
                            "mfe": mfe_now,
                            "asset_class": str(sig.get("asset_class") or "stock"),
                            "activated_at": updates.get("be_activated_at"),
                        })
                    except Exception as exc:  # Defensive: darf Eval-Loop nie abbrechen
                        logger.warning("Signal %s: BE-Aktivierung nicht erfasst: %s",
                                       sig.get("id"), exc)
    except Exception as exc:
        logger.warning("evaluate_open_signals fehlgeschlagen: %s", exc)
        result["errors"] += 1
    return result


# ── Performance-Summary ──────────────────────────────────────────────────────
_METRIC_KEYS = (
    "signals", "open", "tp1_hit", "tp2_hit", "stop_hit", "expired", "no_fill", "untracked"
)


def _empty_bucket() -> Dict[str, Any]:
    bucket: Dict[str, Any] = {key: 0 for key in _METRIC_KEYS}
    bucket.update({"win_rate_pct": None, "avg_r": None, "sum_r": 0.0, "alerts_per_day": 0.0})
    return bucket


def _classify_row(row: Dict[str, Any]) -> str:
    """Signal einem (disjunkten) Summary-Bucket zuordnen."""
    status = row.get("status")
    if status == STATUS_TP2:
        return "tp2_hit"
    if status == STATUS_STOP:
        return "stop_hit"
    if status == STATUS_UNTRACKED:
        return "untracked"
    if status == STATUS_NO_FILL:
        return "no_fill"
    if status == STATUS_EXPIRED:
        realized = _to_float(row.get("r_realized"))
        return "tp1_hit" if row.get("tp1_hit_at") and realized is not None and realized > 0 else "expired"
    return "open"


def _wilson_interval_95(wins: int, decided: int) -> Optional[Dict[str, float]]:
    """Wilson-Score-Konfidenzintervall (95%) fuer eine Trefferquote.

    AUDIT 2026-07-24 (Kalibrier-Loop): exakter Binomial-CI-Ersatz ohne
    Normalapproximation — robust bei kleinen Stichproben und Quoten nahe
    0%/100%, wo das naive p+-2sigma-Intervall aus [0,1] laeuft.
    """
    if decided <= 0:
        return None
    z = 1.96
    p_hat = wins / decided
    denom = 1.0 + z * z / decided
    center = (p_hat + z * z / (2.0 * decided)) / denom
    half = (
        z
        * math.sqrt(p_hat * (1.0 - p_hat) / decided + z * z / (4.0 * decided * decided))
        / denom
    )
    return {
        "lower_pct": round(100.0 * max(0.0, center - half), 1),
        "upper_pct": round(100.0 * min(1.0, center + half), 1),
    }


def _finalize_bucket(
    bucket: Dict[str, Any],
    r_values: List[float],
    window_days: int,
    managed_values: Optional[List[float]] = None,
    be_values: Optional[List[float]] = None,
    be_activations: int = 0,
    be_saved: int = 0,
) -> None:
    wins = sum(1 for value in r_values if value > 0)
    decided = len(r_values)
    bucket["decided_signals"] = decided
    bucket["win_rate_pct"] = round(100.0 * wins / decided, 1) if decided else None
    bucket["win_rate_wilson_95"] = _wilson_interval_95(wins, decided)
    # AUDIT 2026-07-24 (Kalibrier-Loop): 30 entschiedene Signale ist die im
    # Weekly-Report verankerte Mindest-Stichprobe fuer belastbare Quoten.
    bucket["sample_reliable"] = bool(decided >= 30)
    bucket["avg_r"] = round(sum(r_values) / len(r_values), 3) if r_values else None
    bucket["sum_r"] = round(sum(r_values), 3) if r_values else 0.0
    managed = [value for value in (managed_values or []) if value is not None]
    bucket["avg_r_managed_50_50"] = (
        round(sum(managed) / len(managed), 3) if managed else None
    )
    # AUDIT 2026-07-30 (BE-Trigger): live gemessenes R unter der Einstand-Regel
    # (r_realized_be) + Zaehler fuer Markierungen und verhinderte Verlierer.
    be = [value for value in (be_values or []) if value is not None]
    bucket["avg_r_be"] = round(sum(be) / len(be), 3) if be else None
    bucket["be_activations"] = int(be_activations)
    bucket["be_saved"] = int(be_saved)
    bucket["alerts_per_day"] = round(bucket["signals"] / float(window_days), 3)


def breakeven_win_rate_pct(
    win_rate_pct: Optional[float], avg_r: Optional[float]
) -> Optional[float]:
    """Breakeven-Trefferquote p* in % aus dem Bucket-Erwartungswert.

    E = p*(W+L) - L, mit L = 1R normiert => p* = p / (1 + E).
    Heuristik: Wins sind heterogen (TP1-Teilgewinne + TP2-Vollgewinne),
    daher Naeherung ueber avg_r als Erwartungswert.
    """
    if not isinstance(win_rate_pct, (int, float)):
        return None
    if not isinstance(avg_r, (int, float)) or avg_r <= -1.0:
        return None
    return 100.0 * (win_rate_pct / 100.0) / (1.0 + avg_r)


def scanner_verdict(bucket: Dict[str, Any]) -> Tuple[str, str]:
    """Scanner-Verdikt (behalten/beobachten/abschalten, Begruendung).

    AUDIT 2026-07-24 (Kalibrier-Loop):
      behalten   — decided>=30, avg_r>0 UND Wilson-Untergrenze > Breakeven
      abschalten — decided>=30 und (avg_r<=-1R ODER Wilson-Obergrenze <
                   Breakeven)
      beobachten — alles andere / zu kleine Stichprobe
    """
    decided = bucket.get("decided_signals") or 0
    if decided < 30:
        return "beobachten", f"Stichprobe {decided} < 30"
    avg_r = bucket.get("avg_r")
    win = bucket.get("win_rate_pct")
    if not isinstance(avg_r, (int, float)) or not isinstance(win, (int, float)):
        return "beobachten", "keine verwertbaren R-Daten"
    if avg_r <= -1.0:
        return "abschalten", "Ø R <= -1R, strukturell defizitär"
    be = breakeven_win_rate_pct(win, avg_r)
    wilson = bucket.get("win_rate_wilson_95") or {}
    lo, hi = wilson.get("lower_pct"), wilson.get("upper_pct")
    if (avg_r > 0 and isinstance(lo, (int, float))
            and isinstance(be, (int, float)) and lo > be):
        return "behalten", f"KI {lo:.0f}% > Breakeven {be:.0f}%"
    if (avg_r < 0 and isinstance(hi, (int, float))
            and isinstance(be, (int, float)) and hi < be):
        return "abschalten", f"KI {hi:.0f}% < Breakeven {be:.0f}%"
    return "beobachten", "Erwartungswert nicht signifikant"


def load_performance_summary(days: int = 90) -> dict:
    """Track-Record-Zusammenfassung ueber die letzten `days` Tage. Wirft nie.

    Returns:
        {'generated_at', 'window_days', 'total': {...}, 'per_scanner':
         {scanner: {...}}, 'recent': [letzte 20 Signale kompakt]}

    Metrik-Semantik (Buckets sind disjunkt, Summe = signals):
      open      — status OPEN
      tp2_hit   — status TP2_HIT (voller Gewinner)
      tp1_hit   — TP1 erreicht, danach EXPIRED (Teilgewinner,
                  outcome_detail 'tp1_then_expired')
      stop_hit  — status STOP_HIT (Verlierer; auch wenn TP1 vorher kurz
                  erreicht war — konservative Zaehlung)
      expired   — EXPIRED ohne jedes TP
      untracked — keine Kursdaten beschaffbar (zaehlt nirgends als Ergebnis)
      win_rate_pct — wins = tp1_hit + tp2_hit vs. decided = wins + stop_hit;
                     None ohne entschiedene Signale
      avg_r / sum_r — ueber geschlossene Signale mit r_realized
      avg_r_managed_50_50 — R des empfohlenen 50/50-Managements (T1)
      avg_r_be — live gemessenes R unter der Einstand-Regel (Stop auf
                 Einstand ab MFE >= +1R; seit 30.07., kein Backtest);
                 None solange keine BE-Daten existieren
      be_activations / be_saved — Signale mit BE-Markierung / davon vor
                 einem Verlust bewahrt (r_realized < 0, BE-R >= 0)
      win_rate_wilson_95 — Wilson-Konfidenzintervall der Trefferquote
      decided_signals / sample_reliable — Stichprobengroesse / >=30-Flag
      alerts_per_day — signals / window_days
    """
    try:
        window = max(1, int(days))
    except (TypeError, ValueError):
        window = 90
    summary: Dict[str, Any] = {
        "generated_at": _utc_iso(),
        "window_days": window,
        "total": _empty_bucket(),
        "per_scanner": {},
        "recent": [],
    }
    try:
        cutoff_iso = (_utc_now() - timedelta(days=window)).isoformat()
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM signals WHERE created_at >= ? "
                        "AND mail_class = 'trade' "
                        "ORDER BY created_at DESC, id DESC",
                        (cutoff_iso,),
                    ).fetchall()
                ]
        total_r: List[float] = []
        total_managed: List[float] = []
        scanner_r: Dict[str, List[float]] = {}
        scanner_managed: Dict[str, List[float]] = {}
        # BE-Trigger (AUDIT 2026-07-30): live-Wirkung der Einstand-Regel
        total_be: List[float] = []
        scanner_be: Dict[str, List[float]] = {}
        be_act_total = 0
        be_saved_total = 0
        be_act_scanner: Dict[str, int] = {}
        be_saved_scanner: Dict[str, int] = {}
        for row in rows:
            bucket_key = _classify_row(row)
            scanner = str(row.get("scanner") or "unknown")
            scanner_bucket = summary["per_scanner"].setdefault(scanner, _empty_bucket())
            for bucket in (summary["total"], scanner_bucket):
                bucket["signals"] += 1
                bucket[bucket_key] += 1
            r_value = row.get("r_realized")
            if r_value is not None:
                total_r.append(float(r_value))
                scanner_r.setdefault(scanner, []).append(float(r_value))
                managed_value = _managed_r_50_50(row)
                if managed_value is not None:
                    total_managed.append(managed_value)
                    scanner_managed.setdefault(scanner, []).append(managed_value)
                if row.get("be_activated_at"):
                    be_act_total += 1
                    be_act_scanner[scanner] = be_act_scanner.get(scanner, 0) + 1
                be_value = _to_float(row.get("r_realized_be"))
                if be_value is not None:
                    total_be.append(be_value)
                    scanner_be.setdefault(scanner, []).append(be_value)
                    # r < 0, aber BE-R >= 0: die Regel haette den Verlierer
                    # verhindert (impliziert zugleich eine BE-Aktivierung).
                    if float(r_value) < 0.0 and be_value >= 0.0:
                        be_saved_total += 1
                        be_saved_scanner[scanner] = be_saved_scanner.get(scanner, 0) + 1
        _finalize_bucket(summary["total"], total_r, window, total_managed,
                         total_be, be_act_total, be_saved_total)
        for scanner, bucket in summary["per_scanner"].items():
            _finalize_bucket(
                bucket, scanner_r.get(scanner, []), window, scanner_managed.get(scanner, []),
                scanner_be.get(scanner, []), be_act_scanner.get(scanner, 0),
                be_saved_scanner.get(scanner, 0),
            )
        summary["r_semantics"] = (
            "avg_r = Level-R (TP2 volles Geometrie-R, unmanaged); "
            "avg_r_managed_50_50 = R des empfohlenen 50/50-Managements "
            "(50% Teilverkauf am TP1, Rest Stop/TP2/Expiry). "
            "avg_r_be = live gemessenes R unter der Einstand-Regel "
            "(Stop auf Einstand ab MFE >= +1R; seit 30.07., kein Backtest); "
            "be_activations/be_saved = BE-Markierungen / verhinderte Verlierer. "
            "win_rate_wilson_95 = Wilson-Konfidenzintervall der Trefferquote; "
            "sample_reliable ab 30 entschiedenen Signalen. AUDIT 2026-07-24 (T1 + Kalibrier-Loop)."
        )
        summary["recent"] = [
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "scanner": row.get("scanner"),
                "ticker": row.get("ticker"),
                "asset_class": row.get("asset_class"),
                "direction": row.get("direction"),
                "status": row.get("status"),
                "outcome_detail": row.get("outcome_detail") or "",
                "entry": row.get("entry"),
                "entry_filled_at": row.get("entry_filled_at"),
                "entry_fill_price": row.get("entry_fill_price"),
                "stop": row.get("stop"),
                "tp1": row.get("tp1"),
                "tp2": row.get("tp2"),
                "r_realized": row.get("r_realized"),
                "r_realized_be": row.get("r_realized_be"),
                "r_managed_50_50": _managed_r_50_50(row),
                "tp1_hit_at": row.get("tp1_hit_at"),
            }
            for row in rows[:20]
        ]
    except Exception as exc:
        logger.warning("load_performance_summary fehlgeschlagen: %s", exc)
    return summary


def load_breaker_recovery_summary(scanner_key: str, since: Any) -> dict:
    """Return decided post-trip trade/shadow results for one scanner.

    A breaker may only recover from evidence generated after it tripped.
    Open signals are excluded because they contain no realized outcome.
    The function is deliberately fail-closed and never raises.
    """
    scanner = str(scanner_key or "").strip()
    since_dt = _parse_utc_datetime(since)
    summary: Dict[str, Any] = {
        "available": False,
        "scanner": scanner,
        "since": since_dt.isoformat() if since_dt else None,
        "decided": 0,
        "wins": 0,
        "win_pct": None,
        "avg_r": None,
        "sum_r": 0.0,
        "trade_decided": 0,
        "shadow_decided": 0,
        "error": None,
    }
    if not scanner:
        summary["error"] = "scanner_missing"
        return summary
    if since_dt is None:
        summary["error"] = "invalid_since"
        return summary

    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = conn.execute(
                    "SELECT mail_class, r_realized FROM signals "
                    "WHERE scanner = ? AND created_at >= ? "
                    "AND mail_class IN ('trade', 'shadow') "
                    "AND r_realized IS NOT NULL",
                    (scanner, since_dt.isoformat()),
                ).fetchall()

        realized = [float(row["r_realized"]) for row in rows]
        decided = len(realized)
        wins = sum(1 for value in realized if value > 0.0)
        summary.update(
            {
                "available": True,
                "decided": decided,
                "wins": wins,
                "win_pct": round((wins / decided) * 100.0, 2) if decided else None,
                "avg_r": round(sum(realized) / decided, 4) if decided else None,
                "sum_r": round(sum(realized), 4),
                "trade_decided": sum(
                    1 for row in rows if str(row["mail_class"] or "").lower() == "trade"
                ),
                "shadow_decided": sum(
                    1 for row in rows if str(row["mail_class"] or "").lower() == "shadow"
                ),
            }
        )
    except Exception as exc:
        summary["error"] = str(exc)
        logger.warning("load_breaker_recovery_summary fehlgeschlagen: %s", exc)
    return summary


def shadow_summary(days: int = 90) -> dict:
    """Shadow-Messung (AUDIT 2026-07-31): geblockte Signale separat auswerten.

    Loesung des Selektionsproblems der Chase-Gates: Signale, die die
    Swing-Timing-Gates NICHT gemailt haben (mail_class='shadow'), werden mit
    denselben Eval-Regeln weiterverfolgt. Diese Funktion aggregiert NUR sie —
    getrennt von der Trade-Statistik (load_performance_summary filtert
    mail_class='trade'). Erst ab ~30 entschiedenen Shadow-Signalen ist der
    Vergleich geblockt-vs-gemailt belastbar (gleiche Kalibrier-Regel wie im
    Wochenreport).

    Returns:
        {'generated_at', 'window_days',
         'total': {'signals', 'open', 'decided_signals', 'wins',
                   'win_rate_pct', 'avg_r', 'sum_r'},
         'per_reason': {block_reason: anzahl_signale},
         'per_scanner': {scanner: anzahl_signale},
         'recent': [letzte 10 Shadow-Signale kompakt]}
        Wirft nie (bei Fehler leere Struktur).
    """
    try:
        window = max(1, int(days))
    except (TypeError, ValueError):
        window = 90
    summary: Dict[str, Any] = {
        "generated_at": _utc_iso(),
        "window_days": window,
        "total": {
            "signals": 0, "open": 0, "decided_signals": 0, "wins": 0,
            "win_rate_pct": None, "avg_r": None, "sum_r": 0.0,
        },
        "per_reason": {},
        "per_scanner": {},
        "recent": [],
    }
    try:
        cutoff_iso = (_utc_now() - timedelta(days=window)).isoformat()
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM signals WHERE created_at >= ? "
                        "AND mail_class = 'shadow' "
                        "ORDER BY created_at DESC, id DESC",
                        (cutoff_iso,),
                    ).fetchall()
                ]
        r_values: List[float] = []
        wins = 0
        open_count = 0
        per_reason: Dict[str, int] = {}
        per_scanner: Dict[str, int] = {}
        for row in rows:
            scanner = str(row.get("scanner") or "unknown")
            per_scanner[scanner] = per_scanner.get(scanner, 0) + 1
            for reason in str(row.get("block_reasons") or "").split(","):
                reason = reason.strip()
                if reason:
                    per_reason[reason] = per_reason.get(reason, 0) + 1
            if str(row.get("status") or "") == STATUS_OPEN:
                open_count += 1
            r_value = _to_float(row.get("r_realized"))
            if r_value is not None:
                r_values.append(float(r_value))
                if float(r_value) > 0:
                    wins += 1
        decided = len(r_values)
        summary["total"] = {
            "signals": len(rows),
            "open": open_count,
            "decided_signals": decided,
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / decided, 1) if decided else None,
            "avg_r": round(sum(r_values) / decided, 3) if decided else None,
            "sum_r": round(sum(r_values), 3),
        }
        summary["per_reason"] = dict(
            sorted(per_reason.items(), key=lambda item: item[1], reverse=True)
        )
        summary["per_scanner"] = dict(
            sorted(per_scanner.items(), key=lambda item: item[1], reverse=True)
        )
        summary["recent"] = [
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "scanner": row.get("scanner"),
                "ticker": row.get("ticker"),
                "direction": row.get("direction"),
                "status": row.get("status"),
                "r_realized": row.get("r_realized"),
                "block_reasons": row.get("block_reasons") or "",
            }
            for row in rows[:10]
        ]
    except Exception as exc:
        logger.warning("shadow_summary fehlgeschlagen: %s", exc)
    return summary


def get_signal_count() -> int:
    """Gesamtzahl geloggter Signale (fuer Health-/Readiness-Checks).

    Liefert -1 bei DB-Fehler (statt einer Exception), damit Health-Checks
    einen Storage-Defekt von 'noch keine Signale' unterscheiden koennen.
    """
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                row = conn.execute("SELECT COUNT(*) FROM signals").fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("get_signal_count fehlgeschlagen: %s", exc)
        return -1
