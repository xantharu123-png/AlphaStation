"""Alpha Station — Signal-Tracker: belegbarer Track-Record fuer Trade-Alerts.

Jede versendete Trade-Alert-Row (mail_class="trade") wird als Signal in einer
SQLite-Datenbank geloggt und ueber die Folgetage automatisch gegen Stop/TP1/TP2
ausgewertet. Ergebnis: nachvollziehbare Hit-Rates und R-Multiples pro Scanner.

API-Kontrakt (von api.py / bg_service.py konsumiert):
  - record_alert_signals(scanner_name, rows, mail_class, channel) -> int
  - prepare_alert_delivery_intent(...) / finalize_alert_delivery(...)
    (stabile IDs vor SMTP; OPEN erst nach akzeptierter Zustellung)
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
  PENDING_DELIVERY — vor SMTP persistiert, aber noch nicht als versendet
               aktiviert; wird weder bewertet noch in Statistiken gezaehlt.
  OPEN       — laeuft noch (ggf. TP1 bereits erreicht -> tp1_hit_at gesetzt)
  STOP_HIT   — Stop erreicht, r_realized = -1.0. outcome_detail
               'ambiguous_same_day', wenn Stop und ein TP am selben Tag lagen
               (konservativ: Stop zuerst gewertet, kein TP gutgeschrieben).
  TP2_HIT    — TP2 erreicht, r_realized = Geometrie-R von TP2 (TP1 impliziert).
  EXPIRED    — Laufzeit abgelaufen (Aktien: beim Versand eingefrorener,
               strategieabhaengiger Bar-Horizont; Crypto: 120h nach
               created_at). r_realized = R des letzten bekannten
               Preises; outcome_detail 'tp1_then_expired', wenn TP1 vorher
               erreicht wurde.
  UNTRACKED  — 5 fehlgeschlagene Bewertungsversuche (keine Kursdaten);
               r_realized bleibt NULL und zaehlt nicht in Win-Rate/avg_r.

Bewertungsgrenze: Vollstaendige OHLC-Intervalle koennen zeigen, welche Levels
beruehrt wurden, aber nicht deren Reihenfolge innerhalb derselben Kerze. Solche
Faelle werden konservativ als Stop gewertet und zusaetzlich mit einer rein
moeglichen Obergrenze gekennzeichnet. Fehlende Intervalldaten duerfen den
Signalzustand nicht fortschreiben.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import math
import os
import re
import sqlite3
import subprocess
import threading
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from modules.trade_levels import infer_trade_direction, trade_geometry

logger = logging.getLogger(__name__)

__all__ = [
    "SIGNAL_DB_PATH",
    "SIGNAL_DELIVERY_JOURNAL_DB_PATH",
    "CRYPTO_SCANNERS",
    "extract_signal_fields",
    "record_alert_signals",
    "prepare_alert_delivery_intent",
    "build_alert_delivery_intent_key",
    "mark_alert_delivery_attempted",
    "journal_alert_delivery_acceptance",
    "finalize_alert_delivery",
    "load_pending_accepted_deliveries",
    "reconcile_pending_accepted_deliveries",
    "load_delivery_acceptance_health",
    "cancel_alert_delivery_intent",
    "cleanup_stale_prepared_delivery_intents",
    "has_open_equivalent_signal",
    "evaluate_open_signals",
    "load_pending_be_activations",
    "mark_be_alerts_sent",
    "load_pending_terminal_updates",
    "mark_terminal_updates_sent",
    "load_performance_summary",
    "load_breaker_recovery_summary",
    "build_calibration_cell_identity",
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

# Independent SQLite acceptance journal. An empty configured value derives a
# sibling file from SIGNAL_DB_PATH at call time, so test/runtime path overrides
# remain isolated. Keeping this separate from the signals database lets SMTP
# acceptance survive a tracker-DB lock/corruption window.
SIGNAL_DELIVERY_JOURNAL_DB_PATH: str = os.environ.get(
    "SIGNAL_DELIVERY_JOURNAL_DB_PATH", ""
)

#: Modulglobaler Lock — serialisiert alle DB-Zugriffe dieses Prozesses.
_DB_LOCK = threading.Lock()
_DELIVERY_JOURNAL_LOCK = threading.Lock()

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
STATUS_PENDING_DELIVERY = "PENDING_DELIVERY"
#: Virtueller Transitions-Status (NIE in der DB): TP1 wurde in DIESEM
#: Eval-Lauf erreicht, das Signal bleibt aber OPEN (Teilgewinn, Rest
#: Freiroll Richtung TP2). Nur fuer result['transitions'] der Funktion
#: evaluate_open_signals — Konsument: Exit-Update-Mails in bg_service.
STATUS_TP1_OPEN = "TP1_HIT_OPEN"
_MAILABLE_TERMINAL_STATUSES = frozenset({STATUS_STOP, STATUS_TP2, STATUS_EXPIRED})

STOCK_EXPIRY_BARS = 5       # Nur Legacy-Fallback fuer unbekannte Alt-Signale
CRYPTO_EXPIRY_HOURS = 120   # Stunden nach created_at
MAX_EVAL_FAILS = 5          # danach status = UNTRACKED
_UNTRACKED_AFTER_FILL_OUTCOME_DETAIL = (
    "observation_window_ended_after_fill_without_complete_interval_path"
)
_UNTRACKED_AFTER_FILL_FAILURE_DETAIL = "eval_failed_5x_after_confirmed_fill"
_CAUSAL_BOUNDARY_TOUCH_UNRESOLVED = (
    "causal_boundary_interval_level_touch_unresolved"
)
_CAUSAL_BOUNDARY_TOUCH_UNRESOLVED_AFTER_FILL = (
    "causal_boundary_interval_level_touch_unresolved_after_confirmed_fill"
)

# Der Bewertungszeitraum ist Teil des Tradeplans und wird bei Versand
# eingefroren. STOCK_EXPIRY_BARS bleibt nur der Legacy-Fallback fuer alte
# Datensaetze und unbekannte Scanner.
_STOCK_HORIZON_BY_SCANNER = {
    "orb": 1,
    "orb_scanner": 1,
    "crash": 3,
    "crash_monitor": 3,
    "bear": 3,
    "bear_scan": 3,
    "volume_spikes": 3,
    "penny": 3,
    "penny_stock": 3,
    "penny_stocks": 3,
    "stock_strategy": 8,
    "strategy_scan": 8,
    "strategies": 8,
    "bi": 10,
    "bi_scanner": 10,
    "bi_long": 10,
    "bi_short": 10,
    "biotech": 10,
    "biotech_scanner": 10,
    "turtle": 20,
    "turtle_scanner": 20,
}

# Maximal tolerierte Verschlechterung gegenueber dem geplanten Entry.
# Intraday-/Event-Signale brauchen eine engere Fill-Disziplin als Swings.
_MAX_ADVERSE_FILL_R = {
    "orb": 0.25,
    "crash": 0.25,
    "bear": 0.25,
    "early_movers": 0.35,
    "crypto_explosion": 0.35,
    "crypto_trade_signals": 0.35,
    "new_listing": 0.25,
    "stock_strategy": 0.50,
    "strategy_scan": 0.50,
}
_DEFAULT_MAX_ADVERSE_FILL_R = 0.50

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
_INSTRUMENT_ID_KEYS = ("coin_id", "CoinId", "CoinID", "ID", "instrument_id")
_VENUE_KEYS = ("venue", "Venue", "exchange", "Exchange", "BestExchange", "PerpChartExchange")
_CONTRACT_KEYS = (
    "contract_symbol",
    "ContractSymbol",
    "contract",
    "PerpChartSymbol",
    "PerpMatchSymbol",
)
_STRATEGY_KEYS = (
    "strategy", "Strategy", "setup_type", "SetupType", "pattern", "Pattern",
    "scanner_strategy",
)
_HORIZON_KEYS = (
    "trade_horizon", "TradeHorizon", "horizon", "Horizon",
    "holding_period", "HoldingPeriod", "_alert_horizon", "alert_horizon",
)
_EVALUATION_HORIZON_KEYS = (
    "evaluation_horizon_bars", "EvaluationHorizonBars", "horizon_bars",
    "HorizonBars", "max_hold_bars", "MaxHoldBars",
)
_SETUP_KEY_KEYS = ("setup_key", "SetupKey", "signal_key", "SignalKey")
_MARKET_REGIME_KEYS = (
    "market_regime", "MarketRegime", "regime_state", "RegimeState",
    "btc_regime", "BTCRegime",
)
_PRICE_OBSERVED_AT_KEYS = (
    "price_observed_at", "PriceObservedAt", "quote_observed_at", "QuoteObservedAt",
)
_PRICE_SOURCE_KEYS = (
    "price_source", "PriceSource", "quote_source", "QuoteSource",
    "entry_price_source",
)
_FILL_EVIDENCE_VERIFIED_KEYS = (
    "fill_evidence_verified", "FillEvidenceVerified",
)
_PRICE_MODE_KEYS = ("price_mode", "PriceMode", "quote_side", "QuoteSide")
_PRICE_SESSION_KEYS = (
    "price_session", "PriceSession", "market_session", "MarketSession",
)

# A displayed scalar price is not proof of an executable fill.  Immediate
# fills require an explicitly verified, recent and execution-capable quote.
_IMMEDIATE_FILL_EVIDENCE_MAX_AGE_SECONDS = 300
_IMMEDIATE_FILL_EVIDENCE_FUTURE_SKEW_SECONDS = 30
_NON_EXECUTABLE_PRICE_SOURCE_TOKENS = (
    "daily_close", "eod", "historical", "cache", "fallback", "proxy",
    "indicative", "stale", "untradeable", "completed_bar",
)
_EXECUTABLE_PRICE_SOURCE_TOKENS = (
    "live", "quote", "ask", "bid", "snapshot", "last_trade", "websocket",
    "exchange", "polygon",
)
_NON_EXECUTABLE_PRICE_SESSION_TOKENS = (
    "closed", "daily_close", "postmarket", "post_market", "after_hours",
    "overnight",
)


def _no_fill_cleanup_updates() -> Dict[str, Any]:
    """Canonical terminal state for a setup that never had a valid fill."""
    return {
        "entry_filled_at": None,
        "entry_fill_price": None,
        "tp1_hit_at": None,
        "tp2_hit_at": None,
        "stop_hit_at": None,
        "r_realized": None,
        "r_realized_upper": None,
        "r_realized_be": None,
        "exit_fill_price": None,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
        "be_activated_at": None,
        "be_mail_sent_at": None,
        "be_trigger_at": None,
        "be_exit_fill_price": None,
        "be_exit_at": None,
        "be_exit_evidence_mode": None,
        "max_favorable_r": 0.0,
        "max_adverse_r": 0.0,
    }


def _untracked_state_updates(
    sig: Mapping[str, Any],
    now_dt: datetime,
    *,
    unfilled_detail: str,
    after_fill_detail: Optional[str] = None,
) -> Dict[str, Any]:
    """Build a non-outcome terminal state without contradictory hit/exit data."""
    has_fill = bool(sig.get("entry_filled_at")) and _to_float(
        sig.get("entry_fill_price")
    ) is not None
    detail = (
        str(after_fill_detail or _UNTRACKED_AFTER_FILL_OUTCOME_DETAIL)
        if has_fill
        else str(unfilled_detail)
    )
    updates: Dict[str, Any] = {
        "status": STATUS_UNTRACKED,
        "closed_at": now_dt.isoformat(),
        "outcome_detail": detail,
        "tp1_hit_at": None,
        "tp2_hit_at": None,
        "stop_hit_at": None,
        "r_realized": None,
        "r_realized_upper": None,
        "r_realized_be": None,
        "exit_fill_price": None,
        "stop_gap_slippage_r": None,
        "stop_gap_slippage_pct": None,
        "be_exit_fill_price": None,
        "be_exit_at": None,
        "be_exit_evidence_mode": None,
    }
    if not has_fill:
        updates.update({"entry_filled_at": None, "entry_fill_price": None})
    return updates
_EXECUTABLE_PRICE_SESSION_TOKENS = (
    "regular", "premarket", "market_open", "continuous", "24_7", "24x7",
)


# ── Zeit- und Parsing-Helfer ─────────────────────────────────────────────────
def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso() -> str:
    return _utc_now().isoformat()


def _parse_utc_datetime(value: Any) -> Optional[datetime]:
    """ISO-String tolerant als UTC-datetime parsen (naive Werte gelten als UTC)."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            if math.isfinite(float(value)):
                return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
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


def _signal_causal_start(row: Mapping[str, Any]) -> Optional[datetime]:
    """First instant at which a delivered signal could be acted upon.

    Prepared entry intents exist before SMTP acceptance solely to reserve a
    stable ID.  Their preparation time is not market evidence.  New rows use
    ``delivery_accepted_at``; legacy/directly-recorded rows fall back to
    ``created_at``.
    """
    return (
        _parse_utc_datetime(row.get("delivery_accepted_at"))
        or _parse_utc_datetime(row.get("created_at"))
    )


def _verified_immediate_fill_evidence(
    fields: Dict[str, Any],
    now_dt: datetime,
) -> Optional[Dict[str, Any]]:
    """Return audited quote evidence eligible for an immediate fill.

    ``price_at_alert`` on its own is presentation data.  It becomes causal
    fill evidence only when the producer explicitly verified it, supplied an
    execution-capable source, and timestamped the observation close enough to
    this recording operation.  Daily closes and other historical/indicative
    sources are deliberately rejected even when mislabeled as verified.
    """
    if fields.get("fill_evidence_verified") is not True:
        return None
    price = _to_float(fields.get("price_at_alert"))
    observed_at = _parse_utc_datetime(fields.get("price_observed_at"))
    source = " ".join(str(fields.get("price_source") or "").strip().lower().split())
    price_mode = str(fields.get("price_mode") or "").strip().lower()
    price_session = str(fields.get("price_session") or "").strip().lower()
    if price is None or observed_at is None or not source:
        return None
    if any(token in source for token in _NON_EXECUTABLE_PRICE_SOURCE_TOKENS):
        return None
    if not any(token in source for token in _EXECUTABLE_PRICE_SOURCE_TOKENS):
        return None
    if not price_session:
        return None
    if any(token in price_session for token in _NON_EXECUTABLE_PRICE_SESSION_TOKENS):
        return None
    if not any(token in price_session for token in _EXECUTABLE_PRICE_SESSION_TOKENS):
        return None
    direction = "SHORT" if str(fields.get("direction")).upper() == "SHORT" else "LONG"
    expected_mode = "bid" if direction == "SHORT" else "ask"
    if price_mode != expected_mode:
        return None
    age_seconds = (now_dt - observed_at).total_seconds()
    if age_seconds < -_IMMEDIATE_FILL_EVIDENCE_FUTURE_SKEW_SECONDS:
        return None
    if age_seconds > _IMMEDIATE_FILL_EVIDENCE_MAX_AGE_SECONDS:
        return None
    return {
        "price": float(price),
        "observed_at": observed_at.isoformat(),
        "source": source[:160],
        "price_mode": price_mode[:40],
        "price_session": price_session[:80],
    }


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

    Aliase: ticker/Ticker/symbol/Symbol; gemeinsame Direction-Inferenz aus
    Signal_Direction/BI_Direction/direction/_direction/side/trade_action und
    trade_setup (Default LONG); Entry/entry; StopLoss/stop_loss/stop;
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
        "price_observed_at": None,
        "price_source": None,
        "fill_evidence_verified": False,
        "price_mode": None,
        "price_session": None,
        "grade": None,
        "score": None,
        "rvol": None,
        "instrument_id": None,
        "venue": None,
        "contract_symbol": None,
        "strategy": None,
        "trade_horizon": None,
        "evaluation_horizon_bars": None,
        "setup_key": None,
        "market_regime": None,
    }
    if not isinstance(row, dict):
        return out
    try:
        raw_ticker = _first_raw(row, _TICKER_KEYS)
        if raw_ticker is not None:
            ticker = str(raw_ticker).strip().upper()
            out["ticker"] = ticker or None
        direction_row = row
        # ``Direction`` is the long-standing mail alias, while the shared
        # helper deliberately owns the actual LONG/SHORT/BUY/SELL parsing.
        if not str(row.get("direction") or "").strip() and row.get("Direction") is not None:
            direction_row = dict(row)
            direction_row["direction"] = row.get("Direction")
        out["direction"] = infer_trade_direction(direction_row) or "LONG"
        out["entry"] = _to_float(_first_raw(row, _ENTRY_KEYS))
        out["stop"] = _to_float(_first_raw(row, _STOP_KEYS))
        out["tp1"] = _to_float(_first_raw(row, _TP1_KEYS))
        out["tp2"] = _to_float(_first_raw(row, _TP2_KEYS))
        out["price_at_alert"] = _to_float(_first_raw(row, _PRICE_KEYS))
        raw_observed_at = _first_raw(row, _PRICE_OBSERVED_AT_KEYS)
        if raw_observed_at is not None:
            observed_text = str(raw_observed_at).strip()
            out["price_observed_at"] = observed_text[:80] or None
        raw_price_source = _first_raw(row, _PRICE_SOURCE_KEYS)
        if raw_price_source is not None:
            source_text = str(raw_price_source).strip()
            out["price_source"] = source_text[:160] or None
        out["fill_evidence_verified"] = (
            _first_raw(row, _FILL_EVIDENCE_VERIFIED_KEYS) is True
        )
        for output_key, aliases, limit in (
            ("price_mode", _PRICE_MODE_KEYS, 40),
            ("price_session", _PRICE_SESSION_KEYS, 80),
        ):
            raw_value = _first_raw(row, aliases)
            if raw_value is not None:
                value = str(raw_value).strip()
                out[output_key] = value[:limit] or None
        raw_grade = _first_raw(row, _GRADE_KEYS)
        out["grade"] = str(raw_grade).strip() if raw_grade is not None else None
        out["score"] = _to_float(_first_raw(row, _SCORE_KEYS))
        out["rvol"] = _to_float(_first_raw(row, _RVOL_KEYS))
        raw_evaluation_horizon = _to_float(
            _first_raw(row, _EVALUATION_HORIZON_KEYS)
        )
        if raw_evaluation_horizon is not None:
            out["evaluation_horizon_bars"] = int(round(raw_evaluation_horizon))
        for output_key, aliases in (
            ("instrument_id", _INSTRUMENT_ID_KEYS),
            ("venue", _VENUE_KEYS),
            ("contract_symbol", _CONTRACT_KEYS),
            ("strategy", _STRATEGY_KEYS),
            ("trade_horizon", _HORIZON_KEYS),
            ("setup_key", _SETUP_KEY_KEYS),
            ("market_regime", _MARKET_REGIME_KEYS),
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


def _delivery_journal_path() -> str:
    configured = str(SIGNAL_DELIVERY_JOURNAL_DB_PATH or "").strip()
    if configured:
        return configured
    signal_path = Path(_db_path())
    return str(
        signal_path.with_name(
            f"{signal_path.stem}_delivery_acceptance{signal_path.suffix or '.sqlite'}"
        )
    )


_DELIVERY_JOURNAL_SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_acceptance_journal (
    intent_key TEXT PRIMARY KEY,
    accepted_at TEXT NOT NULL,
    recipient_keys_json TEXT NOT NULL,
    journaled_at TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'PENDING',
    reconciled_at TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    reconcile_error TEXT
)
"""


@contextmanager
def _delivery_journal_connection():
    """Independent WAL transaction for post-SMTP acceptance evidence."""
    path = Path(_delivery_journal_path())
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_DELIVERY_JOURNAL_SCHEMA)
        conn.commit()
        # Keep the caller's SELECT/merge/UPSERT in one cross-process write
        # transaction. The Python lock only serializes threads in this process;
        # without this second BEGIN two workers could both read the same prior
        # recipient cohort and the last UPSERT would lose the other's hashes.
        conn.execute("BEGIN IMMEDIATE")
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
    price_observed_at TEXT,
    price_source TEXT,
    fill_evidence_verified INTEGER NOT NULL DEFAULT 0,
    price_mode TEXT,
    price_session TEXT,
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
    ,strategy TEXT
    ,trade_horizon TEXT
    ,evaluation_horizon_bars INTEGER
    ,setup_key TEXT
    ,r_realized_upper REAL
    ,exit_fill_price REAL
    ,stop_gap_slippage_r REAL
    ,stop_gap_slippage_pct REAL
    ,code_revision TEXT
    ,fill_evidence_mode TEXT
    ,be_exit_fill_price REAL
    ,be_exit_at TEXT
    ,be_exit_evidence_mode TEXT
    ,be_trigger_at TEXT
    ,market_regime TEXT
    ,delivery_recipient_keys_json TEXT
    ,pending_update_status TEXT
    ,pending_update_at TEXT
    ,delivery_intent_key TEXT
    ,delivery_state TEXT
    ,delivery_prepared_at TEXT
    ,delivery_accepted_at TEXT
    ,mail_channel TEXT
    ,delivery_attempted_at TEXT
)
"""

_SCHEMA_MIGRATIONS = {
    "entry_filled_at": "TEXT",
    "entry_fill_price": "REAL",
    "instrument_id": "TEXT",
    "venue": "TEXT",
    "contract_symbol": "TEXT",
    "strategy": "TEXT",
    "trade_horizon": "TEXT",
    "evaluation_horizon_bars": "INTEGER",
    "setup_key": "TEXT",
    "r_realized_upper": "REAL",
    "exit_fill_price": "REAL",
    "stop_gap_slippage_r": "REAL",
    "stop_gap_slippage_pct": "REAL",
    "code_revision": "TEXT",
    "fill_evidence_mode": "TEXT",
    "be_exit_fill_price": "REAL",
    "be_exit_at": "TEXT",
    "be_exit_evidence_mode": "TEXT",
    "be_trigger_at": "TEXT",
    "market_regime": "TEXT",
    "delivery_recipient_keys_json": "TEXT",
    "pending_update_status": "TEXT",
    "pending_update_at": "TEXT",
    "delivery_intent_key": "TEXT",
    "delivery_state": "TEXT",
    "delivery_prepared_at": "TEXT",
    "delivery_accepted_at": "TEXT",
    "mail_channel": "TEXT",
    "delivery_attempted_at": "TEXT",
    "price_observed_at": "TEXT",
    "price_source": "TEXT",
    "fill_evidence_verified": "INTEGER NOT NULL DEFAULT 0",
    "price_mode": "TEXT",
    "price_session": "TEXT",
    "be_activated_at": "TEXT",
    "be_mail_sent_at": "TEXT",
    "r_realized_be": "REAL",
    "rates_json": "TEXT",
    # Shadow-Tracking (AUDIT 2026-07-31): bei mail_class='shadow' die Gruende,
    # warum das Signal NICHT gemailt wurde (komma-getrennt, max. 500 Zeichen).
    "block_reasons": "TEXT",
}


def _read_process_code_revision() -> str:
    """Best-effort immutable code identity for newly recorded signals.

    The background service does not currently receive the API module's
    ``BUILD_REVISION`` constant.  Prefer an explicit deployment environment
    value and otherwise read the checked-out Git revision.  A missing identity
    is stored explicitly as ``unknown`` instead of silently mixing revisions.
    """
    for env_key in ("APP_REVISION", "GIT_COMMIT"):
        configured = str(os.environ.get(env_key) or "").strip().lower()
        if re.fullmatch(r"[0-9a-f]{12,40}", configured):
            return configured[:12]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short=12", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        revision = result.stdout.strip().lower()
        if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{12}", revision):
            # A bare commit hash is admissible only for a clean tracked
            # worktree.  Otherwise signals produced by uncommitted code would
            # be silently mixed into the clean commit's performance cohort.
            status = subprocess.run(
                ["git", "status", "--porcelain", "--untracked-files=no"],
                cwd=str(_REPO_ROOT),
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            if status.returncode != 0:
                return f"{revision}-tree-unknown"
            return f"{revision}-dirty" if status.stdout.strip() else revision
    except (OSError, subprocess.SubprocessError):
        pass
    return "unknown"


# Freeze at module import. During safe_deploy Git may already point at the new
# commit while the old worker process is still draining; a per-alert Git read
# would then contaminate revision cohorts with code that never produced them.
_PROCESS_CODE_REVISION = _read_process_code_revision()


def _detect_code_revision() -> str:
    return _PROCESS_CODE_REVISION

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
    "CREATE INDEX IF NOT EXISTS idx_signals_setup_open ON signals(setup_key, status, mail_class)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_signals_delivery_intent "
    "ON signals(delivery_intent_key) WHERE delivery_intent_key IS NOT NULL",
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
        conn.execute("PRAGMA busy_timeout=15000")
        conn.execute("PRAGMA journal_mode=WAL")
        # Serialize cross-process DDL (API + background service can start in
        # parallel). The Python lock only protects threads in one process.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(_SCHEMA)
        existing_columns = {
            str(row[1]) for row in conn.execute("PRAGMA table_info(signals)").fetchall()
        }
        for column, column_type in _SCHEMA_MIGRATIONS.items():
            if column not in existing_columns:
                conn.execute(f"ALTER TABLE signals ADD COLUMN {column} {column_type}")
        for statement in _INDEXES:
            conn.execute(statement)
        conn.commit()
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


def _fill_quality(
    scanner: str,
    planned_entry: float,
    fill_price: float,
    stop: float,
    tp1: float,
    tp2: float,
    direction: str,
) -> Dict[str, Any]:
    """Bewertet, ob der reale Fill den versendeten Plan noch repraesentiert."""
    direction_norm = "SHORT" if str(direction).upper() == "SHORT" else "LONG"
    planned_risk = abs(float(planned_entry) - float(stop))
    if planned_risk <= 0:
        return {"valid": False, "reason": "invalid_planned_risk"}

    actual_geometry = trade_geometry(fill_price, stop, tp1, tp2, direction_norm)
    actual_risk = _to_float(actual_geometry.get("risk"))
    if not actual_geometry.get("valid") or actual_risk is None or actual_risk <= 0:
        return {"valid": False, "reason": "fill_invalidated_trade_geometry"}

    if direction_norm == "LONG":
        adverse_slippage_r = max(0.0, (float(fill_price) - float(planned_entry)) / planned_risk)
        reward_tp1 = float(tp1) - float(fill_price)
        reward_tp2 = float(tp2) - float(fill_price)
    else:
        adverse_slippage_r = max(0.0, (float(planned_entry) - float(fill_price)) / planned_risk)
        reward_tp1 = float(fill_price) - float(tp1)
        reward_tp2 = float(fill_price) - float(tp2)

    rr_tp1 = reward_tp1 / actual_risk
    rr_tp2 = reward_tp2 / actual_risk
    effective_rr = 0.5 * (rr_tp1 + rr_tp2)
    max_slippage_r = _MAX_ADVERSE_FILL_R.get(
        str(scanner or "").strip().lower(),
        _DEFAULT_MAX_ADVERSE_FILL_R,
    )
    if adverse_slippage_r > max_slippage_r:
        return {
            "valid": False,
            "reason": "adverse_fill_slippage",
            "adverse_slippage_r": round(adverse_slippage_r, 4),
            "max_adverse_slippage_r": max_slippage_r,
            "rr_tp1": round(rr_tp1, 4),
            "effective_rr": round(effective_rr, 4),
        }
    if rr_tp1 < 1.0 or effective_rr < 1.5:
        return {
            "valid": False,
            "reason": "fill_rr_below_minimum",
            "adverse_slippage_r": round(adverse_slippage_r, 4),
            "rr_tp1": round(rr_tp1, 4),
            "effective_rr": round(effective_rr, 4),
        }
    return {
        "valid": True,
        "adverse_slippage_r": round(adverse_slippage_r, 4),
        "rr_tp1": round(rr_tp1, 4),
        "rr_tp2": round(rr_tp2, 4),
        "effective_rr": round(effective_rr, 4),
    }


def validate_fill_quality(
    scanner: str,
    planned_entry: float,
    fill_price: float,
    stop: float,
    tp1: float,
    tp2: float,
    direction: str,
) -> Dict[str, Any]:
    """Public single-source contract for executable fill-plan quality."""
    return _fill_quality(
        scanner, planned_entry, fill_price, stop, tp1, tp2, direction
    )


def _fill_rejection_detail(fill_check: Dict[str, Any]) -> str:
    """Kompakte, auswertbare Begruendung fuer einen verworfenen Fill."""
    reason = str(fill_check.get("reason") or "invalid_fill")
    details = []
    for key in ("adverse_slippage_r", "max_adverse_slippage_r", "rr_tp1", "effective_rr"):
        value = _to_float(fill_check.get(key))
        if value is not None:
            details.append(f"{key}={value:.4f}")
    return reason if not details else reason + "|" + "|".join(details)


def _identity_text(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())[:160]


def _identity_level(value: Any) -> str:
    number = _to_float(value)
    return "" if number is None else format(number, ".10g")


def _asset_identity(fields: Dict[str, Any], asset_class: str) -> str:
    if asset_class == "crypto":
        instrument_id = _identity_text(fields.get("instrument_id"))
        if instrument_id:
            return "coin:" + instrument_id
        venue = _identity_text(fields.get("venue"))
        contract = _identity_text(fields.get("contract_symbol"))
        if venue and contract:
            return f"contract:{venue}:{contract}"
    return "ticker:" + _identity_text(fields.get("ticker"))


def _generated_setup_key(
    scanner: str,
    fields: Dict[str, Any],
    asset_class: str,
) -> str:
    payload = {
        "asset": _asset_identity(fields, asset_class),
        "direction": _identity_text(fields.get("direction") or "LONG"),
        "scanner": _identity_text(scanner),
        "strategy": _identity_text(fields.get("strategy") or scanner),
        "horizon": _identity_text(fields.get("trade_horizon") or "unspecified"),
        "entry": _identity_level(fields.get("entry")),
        "stop": _identity_level(fields.get("stop")),
        "tp1": _identity_level(fields.get("tp1")),
        "tp2": _identity_level(fields.get("tp2")),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sig_" + hashlib.sha256(encoded).hexdigest()[:24]


def _bounded_horizon_bars(value: Any) -> Optional[int]:
    parsed = _to_float(value)
    if parsed is None:
        return None
    rounded = int(round(parsed))
    return max(1, min(60, rounded))


def _infer_stock_horizon_bars(
    scanner: str,
    strategy: Any,
    trade_horizon: Any,
    explicit: Any = None,
) -> int:
    """Freeze a stock signal's intended holding period in daily bars."""
    explicit_bars = _bounded_horizon_bars(explicit)
    if explicit_bars is not None:
        return explicit_bars

    horizon_text = str(trade_horizon or "").strip().lower()
    if any(token in horizon_text for token in ("intraday", "daytrade", "day_trade", "scalp")):
        return 1
    if any(token in horizon_text for token in ("position", "positionstrade", "long_term")):
        return 20

    strategy_text = str(strategy or "").strip().lower()
    if "opening range" in strategy_text or strategy_text == "orb":
        return 1
    if any(token in strategy_text for token in ("crash", "gap momentum short")):
        return 3
    if "turtle" in strategy_text:
        return 20
    if any(
        token in strategy_text
        for token in (
            "cup", "handle", "wyckoff", "moving average", "ma bounce",
            "bull flag", "bear flag", "compression", "biotech",
        )
    ):
        return 10

    scanner_key = str(scanner or "").strip().lower()
    scanner_horizon = _STOCK_HORIZON_BY_SCANNER.get(scanner_key)
    if scanner_horizon is not None:
        return scanner_horizon
    if any(token in horizon_text for token in ("swing", "mehrtaeg", "multi_day")):
        return 8
    return STOCK_EXPIRY_BARS


def _stock_horizon_bars(row: Dict[str, Any]) -> int:
    return _infer_stock_horizon_bars(
        str(row.get("scanner") or ""),
        row.get("strategy"),
        row.get("trade_horizon"),
        row.get("evaluation_horizon_bars"),
    )


def _prepare_identity_fields(
    fields: Dict[str, Any],
    scanner: str,
    asset_class: str,
) -> Dict[str, Any]:
    prepared = dict(fields)
    strategy = str(prepared.get("strategy") or scanner).strip()[:160]
    horizon = str(prepared.get("trade_horizon") or "unspecified").strip()[:80]
    supplied_key = str(prepared.get("setup_key") or "").strip()[:160]
    prepared["strategy"] = strategy
    prepared["trade_horizon"] = horizon
    prepared["evaluation_horizon_bars"] = (
        None
        if asset_class == "crypto"
        else _infer_stock_horizon_bars(
            scanner,
            strategy,
            horizon,
            prepared.get("evaluation_horizon_bars"),
        )
    )
    prepared["setup_key"] = supplied_key or _generated_setup_key(
        scanner, prepared, asset_class
    )
    return prepared


def _geometry_equivalent(existing: sqlite3.Row, fields: Dict[str, Any]) -> bool:
    levels = {
        "entry": (_to_float(existing["entry"]), _to_float(fields.get("entry"))),
        "stop": (_to_float(existing["stop"]), _to_float(fields.get("stop"))),
        "tp1": (_to_float(existing["tp1"]), _to_float(fields.get("tp1"))),
        "tp2": (_to_float(existing["tp2"]), _to_float(fields.get("tp2"))),
    }
    if any(old is None or new is None for old, new in levels.values()):
        return False
    old_entry = float(levels["entry"][0])
    new_entry = float(levels["entry"][1])
    old_risk = abs(old_entry - float(levels["stop"][0]))
    new_risk = abs(new_entry - float(levels["stop"][1]))
    # Very tight stops must not make harmless quote rounding look like a new setup.
    scale = max(old_risk, new_risk, abs(old_entry) * 0.002, abs(new_entry) * 0.002, 1e-9)
    return (
        abs(float(levels["entry"][0]) - float(levels["entry"][1])) <= 0.15 * scale
        and abs(float(levels["stop"][0]) - float(levels["stop"][1])) <= 0.15 * scale
        and abs(float(levels["tp1"][0]) - float(levels["tp1"][1])) <= 0.25 * scale
        and abs(float(levels["tp2"][0]) - float(levels["tp2"][1])) <= 0.25 * scale
    )


def _equivalent_open_rows(
    conn: sqlite3.Connection,
    scanner: str,
    fields: Dict[str, Any],
    asset_class: str,
    mail_class: str,
) -> List[sqlite3.Row]:
    """Liest offene Signale desselben wirtschaftlichen Instruments."""
    direction = fields.get("direction") or "LONG"
    params: List[Any] = [
        asset_class, direction, STATUS_OPEN, STATUS_PENDING_DELIVERY, mail_class
    ]
    where = (
        "asset_class = ? AND direction = ? AND status IN (?, ?) "
        "AND mail_class = ?"
    )
    instrument_id = fields.get("instrument_id")
    venue = fields.get("venue")
    contract_symbol = fields.get("contract_symbol")
    ticker = fields.get("ticker")
    if asset_class == "crypto" and instrument_id:
        where += " AND instrument_id = ?"
        params.append(instrument_id)
    elif asset_class == "crypto" and venue and contract_symbol:
        where += " AND venue = ? AND contract_symbol = ?"
        params.extend([venue, contract_symbol])
    else:
        where += " AND ticker = ?"
        params.append(ticker)
    return list(
        conn.execute(
            "SELECT scanner, strategy, trade_horizon, setup_key, entry, stop, tp1, tp2 "
            "FROM signals WHERE " + where,
            tuple(params),
        ).fetchall()
    )


def _has_equivalent_open_signal(
    conn: sqlite3.Connection,
    scanner: str,
    fields: Dict[str, Any],
    asset_class: str,
    mail_class: str,
) -> bool:
    """Dedupe nur fuer denselben wirtschaftlichen Tradeplan."""
    setup_key = str(fields.get("setup_key") or "")
    strategy = _identity_text(fields.get("strategy") or scanner)
    horizon = _identity_text(fields.get("trade_horizon") or "unspecified")
    for existing in _equivalent_open_rows(conn, scanner, fields, asset_class, mail_class):
        existing_key = str(existing["setup_key"] or "")
        if setup_key and existing_key and setup_key == existing_key:
            return True
        same_scanner = str(existing["scanner"] or "") == scanner
        if mail_class != "trade" and not same_scanner:
            continue
        if not _geometry_equivalent(existing, fields):
            continue
        same_context = (
            _identity_text(existing["strategy"] or existing["scanner"]) == strategy
            and _identity_text(existing["trade_horizon"] or "unspecified") == horizon
        )
        if (same_scanner and same_context) or mail_class == "trade":
            return True
    return False


def has_open_equivalent_signal(
    scanner_name: str,
    row: Dict[str, Any],
    mail_class: str = "trade",
) -> bool:
    """Oeffentliche Vorabpruefung, damit doppelte Mails gar nicht erst rausgehen."""
    try:
        scanner = str(scanner_name or "").strip().lower()
        mail_norm = str(mail_class or "trade").strip().lower()
        fields = extract_signal_fields(row)
        asset_class = "crypto" if scanner in CRYPTO_SCANNERS else "stock"
        if not scanner or not fields.get("ticker") or mail_norm not in ("trade", "shadow"):
            return False
        fields = _prepare_identity_fields(fields, scanner, asset_class)
        with _DB_LOCK:
            with _db_connection() as conn:
                return _has_equivalent_open_signal(
                    conn,
                    scanner,
                    fields,
                    asset_class,
                    mail_norm,
                )
    except Exception as exc:
        logger.warning("has_open_equivalent_signal fehlgeschlagen: %s", exc)
        return False


# ── Recording ────────────────────────────────────────────────────────────────
def record_alert_signals(
    scanner_name: str,
    rows: list,
    mail_class: str = "trade",
    channel: str = "email",
    rates_context: Optional[Dict[str, Any]] = None,
    delivery_recipient_keys: Optional[Iterable[str]] = None,
    mail_channel: Optional[str] = None,
    _delivery_intent_key: Optional[str] = None,
    _defer_activation: bool = False,
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
      - Dedupe: Existiert bereits derselbe OPEN-Tradeplan (Setup-ID oder eng
        uebereinstimmende Entry/Stop/TP-Geometrie), wird die Row uebersprungen.
        Ein veraenderter Plan desselben Scanners darf dagegen neu geloggt
        werden. Shadow-Signale bleiben scannerbezogen und blockieren echte
        Mails nicht.
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
        scanner = str(scanner_name or "").strip().lower()
        if not scanner:
            logger.warning("record_alert_signals: leerer scanner_name — Rows werden nicht geloggt")
            return 0
        asset_class = "crypto" if scanner in CRYPTO_SCANNERS else "stock"
        channel_norm = str(channel or "email").strip().lower() or "email"
        mail_channel_norm = str(mail_channel or "").strip().lower()[:120] or None
        now_dt = _coerce_now(_utc_now())
        now_iso = now_dt.isoformat()
        code_revision = _detect_code_revision()
        rates_json = _compact_rates_json(rates_context)
        normalized_recipient_keys = sorted({
            str(value).strip().lower()
            for value in (delivery_recipient_keys or [])
            if re.fullmatch(r"[0-9a-fA-F]{64}", str(value).strip())
        })
        delivery_recipient_keys_json = (
            json.dumps(normalized_recipient_keys, separators=(",", ":"))
            if normalized_recipient_keys
            else None
        )
        intent_base = str(_delivery_intent_key or "").strip()[:180]
        defer_activation = bool(_defer_activation and intent_base and mail_norm == "trade")
        with _DB_LOCK:
            with _db_connection() as conn:
                for row_index, row in enumerate(rows):
                    try:
                        row_intent_key = (
                            f"{intent_base}:{row_index}" if defer_activation else None
                        )
                        if row_intent_key and conn.execute(
                            "SELECT 1 FROM signals WHERE delivery_intent_key = ? LIMIT 1",
                            (row_intent_key,),
                        ).fetchone() is not None:
                            continue
                        fields = _prepare_identity_fields(
                            extract_signal_fields(row), scanner, asset_class
                        )
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
                        if _has_equivalent_open_signal(
                            conn,
                            scanner,
                            fields,
                            asset_class,
                            mail_norm,
                        ):
                            continue
                        fill_at = None
                        fill_price = None
                        signal_status = STATUS_OPEN
                        outcome_detail = ""
                        # A PREPARED intent is not yet actionable. Even a
                        # trusted pre-SMTP quote cannot prove a post-acceptance
                        # fill or invalidation; evaluation begins at the
                        # durable delivery_accepted_at boundary instead.
                        fill_evidence = (
                            _verified_immediate_fill_evidence(fields, now_dt)
                            if mail_norm == "trade" and not defer_activation
                            else None
                        )
                        if fill_evidence is not None:
                            observed_price = float(fill_evidence["price"])
                            if direction == "LONG":
                                if observed_price <= float(stop):
                                    signal_status = STATUS_NO_FILL
                                    outcome_detail = "entry_invalidated_before_fill"
                                elif observed_price >= float(fields["tp1"]):
                                    signal_status = STATUS_NO_FILL
                                    outcome_detail = "entry_observed_after_tp1"
                                elif observed_price >= float(entry):
                                    fill_at = str(fill_evidence["observed_at"])
                                    fill_price = observed_price
                            else:
                                if observed_price >= float(stop):
                                    signal_status = STATUS_NO_FILL
                                    outcome_detail = "entry_invalidated_before_fill"
                                elif observed_price <= float(fields["tp1"]):
                                    signal_status = STATUS_NO_FILL
                                    outcome_detail = "entry_observed_after_tp1"
                                elif observed_price <= float(entry):
                                    fill_at = str(fill_evidence["observed_at"])
                                    fill_price = observed_price
                        if fill_price is not None:
                            fill_check = _fill_quality(
                                scanner,
                                float(entry),
                                fill_price,
                                float(stop),
                                float(fields["tp1"]),
                                float(fields["tp2"]),
                                direction,
                            )
                            if not fill_check.get("valid"):
                                signal_status = STATUS_NO_FILL
                                outcome_detail = _fill_rejection_detail(fill_check)
                                fill_at = None
                                fill_price = None
                        fill_evidence_mode = (
                            "verified_snapshot"
                            if fill_evidence is not None
                            and (fill_price is not None or signal_status == STATUS_NO_FILL)
                            else "pending_interval"
                        )
                        delivery_state = None
                        delivery_prepared_at = None
                        delivery_accepted_at = None
                        if defer_activation and signal_status == STATUS_OPEN:
                            signal_status = STATUS_PENDING_DELIVERY
                            delivery_state = "PREPARED"
                            delivery_prepared_at = now_iso
                        elif defer_activation:
                            # A pre-send invalid row is not part of the SMTP
                            # intent and must not block/reconcile that intent.
                            row_intent_key = None
                        block_reasons_text = ""
                        if mail_norm == "shadow":
                            block_reasons_text = str(row.get("block_reasons") or "")[:500]
                        conn.execute(
                            """
                            INSERT INTO signals (
                                created_at, scanner, ticker, asset_class, direction,
                                entry, stop, tp1, tp2, price_at_alert,
                                price_observed_at, price_source, fill_evidence_verified,
                                price_mode, price_session,
                                grade, score,
                                rvol, mail_class, channel, status, outcome_detail,
                                closed_at, entry_filled_at, entry_fill_price, instrument_id,
                                venue, contract_symbol, strategy, trade_horizon, setup_key,
                                evaluation_horizon_bars, market_regime, rates_json, block_reasons,
                                delivery_recipient_keys_json,
                                code_revision, fill_evidence_mode,
                                delivery_intent_key, delivery_state,
                                delivery_prepared_at, delivery_accepted_at,
                                mail_channel
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                now_iso, scanner, ticker, asset_class, direction,
                                float(entry), float(stop), fields["tp1"], fields["tp2"],
                                fields["price_at_alert"], fields.get("price_observed_at"),
                                fields.get("price_source"),
                                1 if fill_evidence is not None else 0,
                                fields.get("price_mode"), fields.get("price_session"),
                                fields["grade"], fields["score"],
                                fields["rvol"], mail_norm, channel_norm, signal_status, outcome_detail,
                                now_iso if signal_status == STATUS_NO_FILL else None,
                                fill_at, fill_price, instrument_id, venue, contract_symbol,
                                fields.get("strategy"), fields.get("trade_horizon"),
                                fields.get("setup_key"), fields.get("evaluation_horizon_bars"),
                                fields.get("market_regime") or "UNKNOWN",
                                rates_json, block_reasons_text,
                                delivery_recipient_keys_json,
                                code_revision, fill_evidence_mode,
                                row_intent_key, delivery_state,
                                delivery_prepared_at, delivery_accepted_at,
                                mail_channel_norm,
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


def _canonical_intent_value(value: Any) -> Any:
    """Convert common scanner values into deterministic JSON-safe data."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {
            str(key): _canonical_intent_value(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_canonical_intent_value(item) for item in value]
    if isinstance(value, (set, frozenset)):
        items = [_canonical_intent_value(item) for item in value]
        return sorted(items, key=lambda item: json.dumps(item, sort_keys=True, default=str))
    # numpy/pandas scalar compatibility without importing either dependency.
    item_method = getattr(value, "item", None)
    if callable(item_method):
        try:
            return _canonical_intent_value(item_method())
        except (TypeError, ValueError):
            pass
    isoformat = getattr(value, "isoformat", None)
    if callable(isoformat):
        try:
            return str(isoformat())
        except (TypeError, ValueError):
            pass
    return str(value)


def build_alert_delivery_intent_key(
    scanner_name: str,
    rows: Iterable[Any],
    *,
    channel: str = "email",
    mail_channel: Optional[str] = None,
) -> str:
    """Build an order-stable SHA-256 key from scanner, channel and rows."""
    scanner = str(scanner_name or "").strip().lower()
    channel_norm = str(channel or "email").strip().lower() or "email"
    mail_channel_norm = str(mail_channel or "").strip().lower() or None
    canonical_rows: List[Any] = []
    for row in (() if rows is None else rows):
        if isinstance(row, Mapping):
            normalized = _canonical_intent_value(row)
        else:
            try:
                normalized = _canonical_intent_value(dict(row))
            except (TypeError, ValueError):
                normalized = _canonical_intent_value(row)
        canonical_rows.append(normalized)
    canonical_rows.sort(
        key=lambda item: json.dumps(
            item, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
    )
    payload = json.dumps(
        {
            "scanner": scanner,
            "channel": channel_norm,
            "mail_channel": mail_channel_norm,
            "rows": canonical_rows,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"signal-entry-{hashlib.sha256(payload).hexdigest()}"


def prepare_alert_delivery_intent(
    scanner_name: str,
    rows: list,
    intent_key: str,
    *,
    channel: str = "email",
    mail_channel: Optional[str] = None,
    rates_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Persist stable signal IDs before SMTP without exposing them to eval.

    Retrying the same ``intent_key`` is idempotent. Prepared rows use
    ``PENDING_DELIVERY`` and become ``OPEN`` only after accepted-recipient
    hashes have been durably recorded by :func:`finalize_alert_delivery`.
    """
    base = str(intent_key or "").strip()[:180]
    result: Dict[str, Any] = {
        "intent_key": base or None,
        "prepared": False,
        "send_allowed": False,
        "intent_state": "MISSING",
        "already_accepted": False,
        "active": False,
        "signal_ids": [],
        "signals": [],
    }
    if not base or not rows:
        return result
    record_alert_signals(
        scanner_name,
        rows,
        mail_class="trade",
        channel=channel,
        mail_channel=mail_channel,
        rates_context=rates_context,
        _delivery_intent_key=base,
        _defer_activation=True,
    )
    try:
        prefix = f"{base}:"
        with _DB_LOCK:
            with _db_connection() as conn:
                matches = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, created_at, scanner, ticker, direction, status, "
                        "delivery_intent_key, delivery_state, delivery_prepared_at, "
                        "delivery_attempted_at, delivery_accepted_at, "
                        "delivery_recipient_keys_json, mail_channel "
                        "FROM signals WHERE delivery_intent_key IS NOT NULL "
                        "ORDER BY id"
                    ).fetchall()
                    if str(row["delivery_intent_key"] or "").startswith(prefix)
                ]
        result["signals"] = matches
        result["signal_ids"] = [int(row["id"]) for row in matches]
        expected_count = len(rows)
        state_pairs = {
            (str(row.get("status") or ""), str(row.get("delivery_state") or ""))
            for row in matches
        }
        complete = bool(matches) and len(matches) == expected_count
        if complete and state_pairs == {(STATUS_PENDING_DELIVERY, "PREPARED")}:
            intent_state = "PREPARED"
        elif complete and state_pairs == {(STATUS_PENDING_DELIVERY, "ATTEMPTED")}:
            intent_state = "ATTEMPTED_UNKNOWN"
        elif complete and state_pairs == {(STATUS_PENDING_DELIVERY, "ACCEPTED_PENDING")}:
            intent_state = "ACCEPTED_PENDING"
        elif complete and state_pairs == {(STATUS_OPEN, "ACTIVE")}:
            intent_state = "ACTIVE"
        elif matches:
            intent_state = "INCOMPLETE_OR_INCONSISTENT"
        else:
            intent_state = "MISSING"
        result.update({
            "intent_state": intent_state,
            "prepared": intent_state == "PREPARED",
            "send_allowed": intent_state == "PREPARED",
            "already_accepted": intent_state in {"ACCEPTED_PENDING", "ACTIVE"},
            "active": intent_state == "ACTIVE",
        })
    except Exception as exc:
        logger.warning("Alert-Zustellungsintent konnte nicht geladen werden: %s", exc)
    return result


def mark_alert_delivery_attempted(
    intent_key: str,
    *,
    attempted_at: Any = None,
) -> Dict[str, Any]:
    """Durably close replay before entering an SMTP delivery attempt.

    Only the caller receiving ``send_allowed=True`` owns the atomic attempt
    claim. ``ATTEMPTED`` is intentionally not auto-resendable: a process crash
    after DATA may leave the external acceptance outcome unknowable.
    Definitive pre-acceptance failures can be removed explicitly with
    :func:`cancel_alert_delivery_intent`; accepted attempts use finalize.
    """
    base = str(intent_key or "").strip()[:180]
    result: Dict[str, Any] = {
        "intent_key": base or None,
        "attempted": False,
        "claimed": False,
        "claimed_this_call": False,
        "send_allowed": False,
        "manual_reconciliation_required": False,
        "intent_state": "MISSING",
        "signal_ids": [],
    }
    if not base:
        return result
    attempted_dt = _parse_utc_datetime(attempted_at) or _coerce_now(_utc_now())
    prefix = f"{base}:"
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, status, delivery_state, delivery_intent_key "
                        "FROM signals WHERE delivery_intent_key IS NOT NULL"
                    ).fetchall()
                    if str(row["delivery_intent_key"] or "").startswith(prefix)
                ]
                ids = [int(row["id"]) for row in rows]
                result["signal_ids"] = ids
                if not ids:
                    return result
                states = {
                    (str(row.get("status") or ""), str(row.get("delivery_state") or ""))
                    for row in rows
                }
                if states == {(STATUS_PENDING_DELIVERY, "PREPARED")}:
                    placeholders = ",".join("?" for _ in ids)
                    cursor = conn.execute(
                        "UPDATE signals SET delivery_state='ATTEMPTED', "
                        "delivery_attempted_at=COALESCE(delivery_attempted_at, ?) "
                        f"WHERE id IN ({placeholders}) AND status=? "
                        "AND delivery_state='PREPARED' AND delivery_accepted_at IS NULL",
                        [attempted_dt.isoformat(), *ids, STATUS_PENDING_DELIVERY],
                    )
                    claimed = int(cursor.rowcount or 0) == len(ids)
                    if not claimed:
                        # One statement must claim the complete intent. Any
                        # partial/unexpected rowcount rolls the transaction
                        # back rather than leaving split ownership.
                        raise sqlite3.IntegrityError(
                            "delivery attempt claim did not cover the complete intent"
                        )
                    result.update({
                        "attempted": True,
                        "claimed": True,
                        "claimed_this_call": True,
                        "send_allowed": True,
                        "intent_state": "ATTEMPTED_UNKNOWN",
                    })
                elif states == {(STATUS_PENDING_DELIVERY, "ATTEMPTED")}:
                    result.update({
                        "attempted": False,
                        "claimed": False,
                        "claimed_this_call": False,
                        "send_allowed": False,
                        "manual_reconciliation_required": True,
                        "intent_state": "ATTEMPTED_UNKNOWN",
                    })
                elif states == {(STATUS_PENDING_DELIVERY, "ACCEPTED_PENDING")}:
                    result.update({
                        "intent_state": "ACCEPTED_PENDING",
                        "manual_reconciliation_required": True,
                    })
                elif states == {(STATUS_OPEN, "ACTIVE")}:
                    result["intent_state"] = "ACTIVE"
                else:
                    result["intent_state"] = "INCOMPLETE_OR_INCONSISTENT"
    except Exception as exc:
        logger.warning("Alert-Zustellungsversuch konnte nicht markiert werden: %s", exc)
    return result


def journal_alert_delivery_acceptance(
    intent_key: str,
    accepted_recipient_keys: Iterable[str],
    *,
    accepted_at: Any = None,
) -> Dict[str, Any]:
    """Persist one SMTP attempt's accepted DATA independently from activation.

    Only SHA-256 recipient keys are stored. Replays union recipient cohorts and
    preserve the earliest acceptance instant, making partial-recipient retries
    idempotent without retaining addresses. Callers should invoke this directly
    after every successful ``sendmail`` return and pass that attempt's timestamp;
    a later finalization may safely submit the complete accepted cohort again.
    """
    base = str(intent_key or "").strip()[:180]
    recipient_keys = sorted({
        str(value).strip().lower()
        for value in (accepted_recipient_keys or [])
        if re.fullmatch(r"[0-9a-fA-F]{64}", str(value).strip())
    })
    parsed_accepted_at = _parse_utc_datetime(accepted_at)
    accepted_dt = parsed_accepted_at or _coerce_now(_utc_now())
    result: Dict[str, Any] = {
        "intent_key": base or None,
        "journaled": False,
        "durable_acceptance": False,
        "accepted_at": accepted_dt.isoformat(),
        "delivery_recipient_keys_json": None,
        "tracker_pending": False,
        "error": None,
    }
    if (
        not base
        or not recipient_keys
        or (accepted_at is not None and parsed_accepted_at is None)
    ):
        result["error"] = "invalid_acceptance_evidence"
        return result
    try:
        with _DELIVERY_JOURNAL_LOCK:
            with _delivery_journal_connection() as conn:
                existing = conn.execute(
                    "SELECT accepted_at, recipient_keys_json, state "
                    "FROM delivery_acceptance_journal WHERE intent_key=?",
                    (base,),
                ).fetchone()
                merged = set(recipient_keys)
                earliest = accepted_dt
                state = "PENDING"
                if existing is not None:
                    try:
                        merged.update(json.loads(existing["recipient_keys_json"] or "[]"))
                    except (TypeError, json.JSONDecodeError):
                        pass
                    previous_at = _parse_utc_datetime(existing["accepted_at"])
                    if previous_at is not None:
                        earliest = min(earliest, previous_at)
                    state = str(existing["state"] or "PENDING")
                recipients_json = json.dumps(sorted({
                    value for value in merged
                    if re.fullmatch(r"[0-9a-f]{64}", str(value))
                }), separators=(",", ":"))
                conn.execute(
                    "INSERT INTO delivery_acceptance_journal ("
                    "intent_key, accepted_at, recipient_keys_json, journaled_at, state"
                    ") VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(intent_key) DO UPDATE SET "
                    "accepted_at=excluded.accepted_at, "
                    "recipient_keys_json=excluded.recipient_keys_json",
                    (
                        base,
                        earliest.isoformat(),
                        recipients_json,
                        _utc_iso(),
                        state,
                    ),
                )
        result.update({
            "journaled": True,
            "durable_acceptance": True,
            "accepted_at": earliest.isoformat(),
            "delivery_recipient_keys_json": recipients_json,
            "tracker_pending": state != "RECONCILED",
        })
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}:{str(exc)[:180]}"
        logger.error("SMTP-Akzeptanzjournal fehlgeschlagen: %s", exc)
    return result


def _update_delivery_acceptance_journal(
    intent_key: str,
    *,
    reconciled: bool,
    error: Optional[str] = None,
) -> None:
    base = str(intent_key or "").strip()[:180]
    if not base:
        return
    try:
        with _DELIVERY_JOURNAL_LOCK:
            with _delivery_journal_connection() as conn:
                if reconciled:
                    conn.execute(
                        "UPDATE delivery_acceptance_journal SET state='RECONCILED', "
                        "reconciled_at=?, reconcile_error=NULL WHERE intent_key=?",
                        (_utc_iso(), base),
                    )
                else:
                    conn.execute(
                        "UPDATE delivery_acceptance_journal SET state='PENDING', "
                        "retry_count=retry_count+1, reconcile_error=? "
                        "WHERE intent_key=?",
                        (str(error or "activation_pending")[:300], base),
                    )
    except Exception as exc:
        logger.error("SMTP-Akzeptanzjournal-Status fehlgeschlagen: %s", exc)


def finalize_alert_delivery(
    intent_key: str,
    accepted_recipient_keys: Iterable[str],
    *,
    accepted_at: Any = None,
) -> Dict[str, Any]:
    """Journal accepted SMTP DATA, then idempotently activate its signals.

    The acceptance journal is a separate SQLite file. If the signals database
    is unavailable after DATA, ``accepted`` remains true because the durable
    journal drives later reconciliation. ``durable_acceptance=false`` means
    both stores failed and the caller must use its independent outbox fallback.
    """
    base = str(intent_key or "").strip()[:180]
    recipient_keys = sorted({
        str(value).strip().lower()
        for value in (accepted_recipient_keys or [])
        if re.fullmatch(r"[0-9a-fA-F]{64}", str(value).strip())
    })
    result: Dict[str, Any] = {
        "intent_key": base or None,
        "accepted": False,
        "activated": False,
        "journaled": False,
        "durable_acceptance": False,
        "tracker_acceptance_persisted": False,
        "tracker_pending": False,
        "accepted_at": None,
        "delivery_recipient_keys_json": None,
        "error": None,
        "signal_ids": [],
    }
    if not base or not recipient_keys:
        return result
    parsed_accepted_at = _parse_utc_datetime(accepted_at)
    if accepted_at is not None and parsed_accepted_at is None:
        result["error"] = "invalid_acceptance_evidence"
        return result
    accepted_dt = parsed_accepted_at or _coerce_now(_utc_now())
    journal = journal_alert_delivery_acceptance(
        base,
        recipient_keys,
        accepted_at=accepted_dt,
    )
    result["journaled"] = bool(journal.get("journaled"))
    result["durable_acceptance"] = bool(journal.get("durable_acceptance"))
    accepted_iso = str(journal.get("accepted_at") or accepted_dt.isoformat())
    journal_accepted_dt = _parse_utc_datetime(accepted_iso) or accepted_dt
    recipients_json = str(
        journal.get("delivery_recipient_keys_json")
        or json.dumps(recipient_keys, separators=(",", ":"))
    )
    result["accepted_at"] = accepted_iso
    result["delivery_recipient_keys_json"] = recipients_json
    prefix = f"{base}:"
    tracker_error: Optional[str] = None
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, delivery_intent_key, status, delivery_state, "
                        "delivery_accepted_at FROM signals "
                        "WHERE delivery_intent_key IS NOT NULL"
                    ).fetchall()
                    if str(row["delivery_intent_key"] or "").startswith(prefix)
                ]
                ids = [int(row["id"]) for row in rows]
                result["signal_ids"] = ids
                if not ids:
                    raise LookupError("delivery_intent_signals_missing")
                placeholders = ",".join("?" for _ in ids)
                allowed_states = {
                    (STATUS_PENDING_DELIVERY, "PREPARED"),
                    (STATUS_PENDING_DELIVERY, "ATTEMPTED"),
                    (STATUS_PENDING_DELIVERY, "ACCEPTED_PENDING"),
                    (STATUS_OPEN, "ACTIVE"),
                }
                if any(
                    (row.get("status"), row.get("delivery_state"))
                    not in allowed_states
                    for row in rows
                ):
                    raise sqlite3.IntegrityError(
                        "delivery intent contains inconsistent signal state"
                    )
                for row in rows:
                    previous_at = _parse_utc_datetime(
                        row.get("delivery_accepted_at")
                    )
                    row_accepted_at = (
                        min(previous_at, journal_accepted_dt)
                        if previous_at is not None
                        else journal_accepted_dt
                    ).isoformat()
                    next_state = (
                        "ACCEPTED_PENDING"
                        if row.get("status") == STATUS_PENDING_DELIVERY
                        else "ACTIVE"
                    )
                    conn.execute(
                        "UPDATE signals SET delivery_state=?, "
                        "delivery_accepted_at=?, delivery_recipient_keys_json=? "
                        "WHERE id=?",
                        (
                            next_state,
                            row_accepted_at,
                            recipients_json,
                            int(row["id"]),
                        ),
                    )
                accepted_states = conn.execute(
                    "SELECT status, delivery_state FROM signals "
                    f"WHERE id IN ({placeholders})",
                    ids,
                ).fetchall()
                acceptance_persisted = bool(accepted_states) and all(
                    (
                        row["status"] == STATUS_PENDING_DELIVERY
                        and row["delivery_state"] == "ACCEPTED_PENDING"
                    )
                    or (
                        row["status"] == STATUS_OPEN
                        and row["delivery_state"] == "ACTIVE"
                    )
                    for row in accepted_states
                )
                if not acceptance_persisted:
                    raise sqlite3.IntegrityError(
                        "delivery acceptance did not cover complete intent"
                    )
        result["tracker_acceptance_persisted"] = True
        result["accepted"] = True
        result["durable_acceptance"] = True
    except Exception as exc:
        tracker_error = f"{type(exc).__name__}:{str(exc)[:180]}"
        result["error"] = tracker_error
        logger.warning("Alert-Zustellung konnte nicht bestaetigt werden: %s", exc)

    if result["signal_ids"] and result["tracker_acceptance_persisted"]:
        try:
            with _DB_LOCK:
                with _db_connection() as conn:
                    ids = result["signal_ids"]
                    placeholders = ",".join("?" for _ in ids)
                    conn.execute(
                        "UPDATE signals SET status = ?, delivery_state = 'ACTIVE' "
                        f"WHERE id IN ({placeholders}) "
                        "AND status = ? AND delivery_state = 'ACCEPTED_PENDING'",
                        [STATUS_OPEN, *ids, STATUS_PENDING_DELIVERY],
                    )
                    states = conn.execute(
                        "SELECT status, delivery_state FROM signals "
                        f"WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
            result["activated"] = bool(states) and all(
                row["status"] == STATUS_OPEN and row["delivery_state"] == "ACTIVE"
                for row in states
            )
        except Exception as exc:
            tracker_error = f"{type(exc).__name__}:{str(exc)[:180]}"
            result["error"] = tracker_error
            logger.warning(
                "Akzeptierte Alert-Zustellung wartet auf Aktivierung: %s", exc
            )
    if result["activated"]:
        result.update({
            "accepted": True,
            "durable_acceptance": True,
            "tracker_pending": False,
            "error": None,
        })
        if result["journaled"]:
            _update_delivery_acceptance_journal(base, reconciled=True)
    else:
        result["tracker_pending"] = bool(
            result["journaled"] or result["tracker_acceptance_persisted"]
        )
        if result["journaled"]:
            result["accepted"] = True
            result["durable_acceptance"] = True
            _update_delivery_acceptance_journal(
                base,
                reconciled=False,
                error=tracker_error or "activation_pending",
            )
        elif not result["tracker_acceptance_persisted"]:
            result["accepted"] = False
            result["durable_acceptance"] = False
    return result


def load_pending_accepted_deliveries() -> List[Dict[str, Any]]:
    """Return durable accepted SMTP intents whose activation needs retry.

    Main-DB ACCEPTED_PENDING rows and independent-journal PENDING rows are
    merged by intent. Journal rows remain visible even while the signal DB is
    unavailable, so health/reconciliation never mistakes that state for empty.
    """
    grouped: Dict[str, Dict[str, Any]] = {}
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT id, created_at, scanner, ticker, delivery_intent_key, "
                        "delivery_accepted_at, delivery_recipient_keys_json "
                        ", mail_channel "
                        "FROM signals WHERE status = ? "
                        "AND delivery_state = 'ACCEPTED_PENDING' "
                        "ORDER BY delivery_accepted_at, id",
                        (STATUS_PENDING_DELIVERY,),
                    ).fetchall()
                ]
        for row in rows:
            row_key = str(row.get("delivery_intent_key") or "")
            base, separator, suffix = row_key.rpartition(":")
            if not separator or not suffix.isdigit():
                continue
            group = grouped.setdefault(base, {
                "intent_key": base,
                "accepted_at": row.get("delivery_accepted_at"),
                "delivery_recipient_keys_json": row.get(
                    "delivery_recipient_keys_json"
                ),
                "mail_channel": row.get("mail_channel"),
                "signal_ids": [],
                "signals": [],
                "tracker_persisted": True,
                "journal_persisted": False,
                "activation_pending": True,
            })
            group["signal_ids"].append(int(row["id"]))
            group["signals"].append(row)
    except Exception as exc:
        logger.warning("Akzeptierte Alert-Zustellungen konnten nicht geladen werden: %s", exc)
    try:
        with _DELIVERY_JOURNAL_LOCK:
            with _delivery_journal_connection() as conn:
                journal_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM delivery_acceptance_journal "
                        "WHERE state='PENDING' ORDER BY accepted_at, intent_key"
                    ).fetchall()
                ]
        for row in journal_rows:
            base = str(row.get("intent_key") or "")
            if not base:
                continue
            group = grouped.setdefault(base, {
                "intent_key": base,
                "accepted_at": row.get("accepted_at"),
                "delivery_recipient_keys_json": row.get("recipient_keys_json"),
                "mail_channel": None,
                "signal_ids": [],
                "signals": [],
                "tracker_persisted": False,
                "journal_persisted": True,
                "activation_pending": True,
            })
            group.update({
                "accepted_at": row.get("accepted_at") or group.get("accepted_at"),
                "delivery_recipient_keys_json": (
                    row.get("recipient_keys_json")
                    or group.get("delivery_recipient_keys_json")
                ),
                "journal_persisted": True,
                "journal_retry_count": int(row.get("retry_count") or 0),
                "journal_reconcile_error": row.get("reconcile_error"),
            })
    except Exception as exc:
        logger.error("SMTP-Akzeptanzjournal konnte nicht geladen werden: %s", exc)
    return list(grouped.values())


def reconcile_pending_accepted_deliveries(limit: int = 100) -> List[Dict[str, Any]]:
    """Idempotently retry journal/main-DB acceptance activation."""
    try:
        bounded_limit = max(1, min(int(limit), 1000))
    except (TypeError, ValueError):
        bounded_limit = 100
    results: List[Dict[str, Any]] = []
    for pending in load_pending_accepted_deliveries()[:bounded_limit]:
        try:
            recipient_keys = json.loads(
                pending.get("delivery_recipient_keys_json") or "[]"
            )
        except (TypeError, json.JSONDecodeError):
            recipient_keys = []
        result = finalize_alert_delivery(
            str(pending.get("intent_key") or ""),
            recipient_keys,
            accepted_at=pending.get("accepted_at"),
        )
        results.append(result)
    return results


def load_delivery_acceptance_health() -> Dict[str, Any]:
    """Expose pending journal backlog for readiness/health reporting."""
    health: Dict[str, Any] = {
        "status": "ok",
        "tracker_pending": False,
        "pending_count": 0,
        "reconciled_count": 0,
        "legacy_open_cohort_unknown_count": 0,
        "legacy_cohort_check_available": True,
        "oldest_pending_at": None,
        "last_error": None,
        "journal_path": _delivery_journal_path(),
    }
    try:
        with _DELIVERY_JOURNAL_LOCK:
            with _delivery_journal_connection() as conn:
                counts = {
                    str(row["state"]): int(row["count"])
                    for row in conn.execute(
                        "SELECT state, COUNT(*) AS count "
                        "FROM delivery_acceptance_journal GROUP BY state"
                    ).fetchall()
                }
                oldest = conn.execute(
                    "SELECT accepted_at, reconcile_error "
                    "FROM delivery_acceptance_journal WHERE state='PENDING' "
                    "ORDER BY accepted_at LIMIT 1"
                ).fetchone()
        pending_count = int(counts.get("PENDING", 0))
        legacy_open_cohort_unknown_count = 0
        legacy_cohort_check_available = True
        try:
            with _DB_LOCK:
                with _db_connection() as conn:
                    legacy_open_cohort_unknown_count = int(
                        conn.execute(
                            "SELECT COUNT(*) FROM signals "
                            "WHERE status=? AND mail_class='trade' AND channel='email' "
                            "AND (delivery_recipient_keys_json IS NULL "
                            "OR TRIM(delivery_recipient_keys_json) IN ('', '[]'))",
                            (STATUS_OPEN,),
                        ).fetchone()[0]
                        or 0
                    )
        except Exception as legacy_exc:
            legacy_cohort_check_available = False
            logger.warning(
                "Legacy-Empfaenger-Kohorte konnte nicht geprueft werden: %s",
                type(legacy_exc).__name__,
            )
        health.update({
            "status": (
                "degraded"
                if (
                    pending_count
                    or legacy_open_cohort_unknown_count
                    or not legacy_cohort_check_available
                )
                else "ok"
            ),
            "tracker_pending": bool(pending_count),
            "pending_count": pending_count,
            "reconciled_count": int(counts.get("RECONCILED", 0)),
            "legacy_open_cohort_unknown_count": (
                legacy_open_cohort_unknown_count
            ),
            "legacy_cohort_check_available": legacy_cohort_check_available,
            "oldest_pending_at": oldest["accepted_at"] if oldest else None,
            "last_error": oldest["reconcile_error"] if oldest else None,
        })
    except Exception as exc:
        health.update({
            "status": "error",
            "tracker_pending": True,
            "last_error": f"{type(exc).__name__}:{str(exc)[:180]}",
        })
    return health


def cancel_alert_delivery_intent(
    intent_key: str,
    *,
    delivery_definitively_not_accepted: bool = False,
) -> int:
    """Delete only rows known not to have been accepted by SMTP.

    Untouched PREPARED rows are safe to cancel. ATTEMPTED rows require the
    caller's explicit attestation that DATA was definitively not accepted;
    unknown delivery outcomes remain durable and are never auto-deleted.
    """
    base = str(intent_key or "").strip()[:180]
    if not base:
        return 0
    prefix = f"{base}:"
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                ids = [
                    int(row["id"])
                    for row in conn.execute(
                        "SELECT id, delivery_intent_key, delivery_state FROM signals "
                        "WHERE status = ? "
                        "AND delivery_state IN ('PREPARED', 'ATTEMPTED') "
                        "AND delivery_accepted_at IS NULL",
                        (STATUS_PENDING_DELIVERY,),
                    ).fetchall()
                    if str(row["delivery_intent_key"] or "").startswith(prefix)
                    and (
                        str(row["delivery_state"] or "") == "PREPARED"
                        or bool(delivery_definitively_not_accepted)
                    )
                ]
                if not ids:
                    return 0
                placeholders = ",".join("?" for _ in ids)
                cursor = conn.execute(
                    f"DELETE FROM signals WHERE id IN ({placeholders}) "
                    "AND status = ? "
                    "AND delivery_state IN ('PREPARED', 'ATTEMPTED') "
                    "AND delivery_accepted_at IS NULL",
                    [*ids, STATUS_PENDING_DELIVERY],
                )
                return int(cursor.rowcount or 0)
    except Exception as exc:
        logger.warning("Vorbereiteter Alert-Intent konnte nicht verworfen werden: %s", exc)
        return 0


def cleanup_stale_prepared_delivery_intents(
    max_age_minutes: int = 30,
    *,
    now: Any = None,
) -> int:
    """Remove stale, never-accepted entry intents after a short trade window."""
    try:
        age_minutes = max(5, min(int(max_age_minutes), 24 * 60))
    except (TypeError, ValueError):
        age_minutes = 30
    now_dt = _parse_utc_datetime(now) if now is not None else _utc_now()
    now_dt = now_dt or _utc_now()
    cutoff = (now_dt - timedelta(minutes=age_minutes)).isoformat()
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                cursor = conn.execute(
                    "DELETE FROM signals WHERE status = ? "
                    "AND delivery_state = 'PREPARED' "
                    "AND delivery_accepted_at IS NULL "
                    "AND delivery_prepared_at IS NOT NULL "
                    "AND delivery_prepared_at < ?",
                    (STATUS_PENDING_DELIVERY, cutoff),
                )
                return int(cursor.rowcount or 0)
    except Exception as exc:
        logger.warning("Veraltete Alert-Intents konnten nicht bereinigt werden: %s", exc)
        return 0


# ── Evaluierung ──────────────────────────────────────────────────────────────
def _signed_r(price: float, entry: float, risk: float, direction: str) -> float:
    """Signiertes R-Multiple: positiv = in Trade-Richtung im Gewinn."""
    if direction == "SHORT":
        return (entry - price) / risk
    return (price - entry) / risk


def _stop_exit_metrics(
    exit_fill_price: float,
    planned_stop: float,
    entry_fill_price: float,
    direction: str,
) -> Dict[str, float]:
    """Return separately auditable stop execution and adverse gap slippage.

    Entry slippage answers whether the trade was entered away from its planned
    entry.  Stop-gap slippage answers a different question: how far the first
    executable exit lay beyond the planned stop, measured against actual
    initial risk and against the stop price.  Both values are positive costs.
    """
    exit_fill = float(exit_fill_price)
    stop = float(planned_stop)
    fill = float(entry_fill_price)
    risk = abs(fill - stop)
    if direction == "SHORT":
        adverse_price = max(0.0, exit_fill - stop)
    else:
        adverse_price = max(0.0, stop - exit_fill)
    return {
        "exit_fill_price": round(exit_fill, 8),
        "stop_gap_slippage_r": round(adverse_price / risk, 4) if risk > 0 else 0.0,
        "stop_gap_slippage_pct": (
            round(100.0 * adverse_price / abs(stop), 4) if stop != 0 else 0.0
        ),
    }


def _is_ambiguous_outcome(value: Any) -> bool:
    """Return whether one OHLC interval permits mutually exclusive paths."""
    detail = value.get("outcome_detail") if isinstance(value, dict) else value
    return str(detail or "").startswith("ambiguous_same_")


def _realized_upper(row: Dict[str, Any]) -> Optional[float]:
    """Upper feasible R bound for path-ambiguous bars, never below lower R."""
    lower = _to_float(row.get("r_realized"))
    if lower is None:
        return None
    upper = _to_float(row.get("r_realized_upper"))
    return max(lower, upper) if upper is not None else lower


def _managed_upper_r(row: Dict[str, Any]) -> Optional[float]:
    """Feasible upper bound under the recommended 50/50 plus BE policy."""
    managed_value, unresolved = _managed_5050_be_resolution(row)
    if unresolved:
        return None
    if not _is_ambiguous_outcome(row):
        return managed_value
    upper = _realized_upper(row)
    lower = _to_float(row.get("r_realized"))
    fill = _to_float(row.get("entry_fill_price"))
    if fill is None:
        fill = _to_float(row.get("entry"))
    stop = _to_float(row.get("stop"))
    tp1 = _to_float(row.get("tp1"))
    tp2 = _to_float(row.get("tp2"))
    if None in (upper, lower, fill, stop, tp1, tp2) or upper <= lower:
        return managed_value
    direction = "SHORT" if str(row.get("direction")) == "SHORT" else "LONG"
    geometry = trade_geometry(fill, stop, tp1, tp2, direction)
    risk = geometry.get("risk")
    if not geometry.get("valid") or not risk:
        return upper
    r_tp1 = _signed_r(tp1, fill, risk, direction)
    r_tp2 = _signed_r(tp2, fill, risk, direction)
    if upper >= r_tp2 - 1e-6:
        return round(0.5 * r_tp1 + 0.5 * r_tp2, 4)
    if upper >= r_tp1 - 1e-6:
        return round(0.5 * r_tp1, 4)
    return upper


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


def _managed_control_geometry(row: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Validate the exact geometry allowed to influence managed control.

    Legacy Level-R rows may be useful for descriptive history, but a breaker
    must not treat an arbitrary ``r_realized`` as the 50/50+BE policy result.
    Control evidence therefore requires an actual filled entry plus complete,
    directionally valid Entry/Stop/TP1/TP2 geometry.
    """
    fill = _to_float(row.get("entry_fill_price"))
    stop = _to_float(row.get("stop"))
    tp1 = _to_float(row.get("tp1"))
    tp2 = _to_float(row.get("tp2"))
    direction = str(row.get("direction") or "").strip().upper()
    if (
        not row.get("entry_filled_at")
        or _parse_utc_datetime(row.get("entry_filled_at")) is None
        or None in (fill, stop, tp1, tp2)
        or direction not in {"LONG", "SHORT"}
    ):
        return None
    geometry = trade_geometry(fill, stop, tp1, tp2, direction)
    if not geometry.get("valid") or not geometry.get("risk"):
        return None
    return {
        "fill": float(fill),
        "stop": float(stop),
        "tp1": float(tp1),
        "tp2": float(tp2),
        "risk": float(geometry["risk"]),
        "direction": direction,
    }


def simulate_breakeven_after_mfe(row: Dict[str, Any], mfe_trigger: float = 1.0) -> Optional[float]:
    """Gegenprobe 'Breakeven-Stop nach +mfe_trigger R' (AUDIT 2026-07-29).

    Die Exit-Effizienz-Messung (MFE-Nutzung -22%) legt nahe, dass offene
    Gewinne systematisch verschenkt werden. Diese reine Funktion simuliert
    die einfachste Gegenregel auf den gespeicherten Feldern:
      - MFE < Trigger        → unveraendert (Regel greift nie)
      - MFE >= Trigger        → nur nach belegter Stop-Update-Zustellung
      - negativer Ausgang    → nur ein kausal beobachteter BE-Exit darf
        angerechnet werden; sonst None statt erfundener 0R
    None bedeutet fehlendes r_realized oder unvollstaendige BE-Evidenz.
    """
    value, _unresolved = _breakeven_after_mfe_resolution(row, mfe_trigger)
    return value


def _managed_be_was_triggered(row: Dict[str, Any], mfe_trigger: float = 1.0) -> bool:
    """Return whether the stored evidence proves that the BE rule was due."""
    explicit_evidence = any(
        row.get(field)
        for field in (
            "be_trigger_at", "be_activated_at", "be_mail_sent_at", "be_exit_at"
        )
    )
    if _is_ambiguous_outcome(row) and not explicit_evidence:
        # A bar that contains mutually exclusive paths does not prove that the
        # +1R trigger preceded the terminal event.
        return False
    mfe = _to_float(row.get("max_favorable_r"))
    if mfe is not None and mfe >= mfe_trigger:
        return True
    return explicit_evidence


def _be_delivery_is_proven(row: Dict[str, Any]) -> bool:
    """Require a causal, parseable activation and delivery acknowledgement."""
    activated_at = _parse_utc_datetime(row.get("be_activated_at"))
    delivered_at = _parse_utc_datetime(row.get("be_mail_sent_at"))
    trigger_at = _parse_utc_datetime(row.get("be_trigger_at"))
    if activated_at is None or delivered_at is None or delivered_at < activated_at:
        return False
    if trigger_at is not None and delivered_at < trigger_at:
        return False
    return True


def _breakeven_after_mfe_resolution(
    row: Dict[str, Any],
    mfe_trigger: float = 1.0,
) -> Tuple[Optional[float], bool]:
    """Return ``(R, unresolved)`` without selecting outcomes by their sign.

    Once the rule was due, both winners and losers require proof that the
    stop-update was delivered. This prevents an optimistic subset in which
    undelivered winners remain while comparable undelivered losses disappear.
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None, False
    if not _managed_be_was_triggered(row, mfe_trigger):
        return realized, False
    if not _be_delivery_is_proven(row):
        return None, True
    if row.get("be_exit_at"):
        delivered_at = _parse_utc_datetime(row.get("be_mail_sent_at"))
        be_exit_at = _parse_utc_datetime(row.get("be_exit_at"))
        if be_exit_at is None or delivered_at is None or be_exit_at < delivered_at:
            return None, True
        observed = breakeven_adjusted_r(row)
        return (observed, False) if observed is not None else (None, True)
    if realized >= 0.0 and not _is_ambiguous_outcome(row):
        return realized, False
    return None, True


def _managed_5050_be_resolution(row: Dict[str, Any]) -> Tuple[Optional[float], bool]:
    """Resolve recommended 50/50+BE R and flag incomplete delivery evidence."""
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None, False
    if _managed_control_geometry(row) is None:
        # A terminal Level-R without auditable geometry is incomplete control
        # evidence, not a neutral omission that could silently shrink n.
        return None, True
    base = _managed_r_50_50(row)
    if base is None:
        return None, True
    tp1_hit = bool(row.get("tp1_hit_at")) or row.get("status") == STATUS_TP2
    if not tp1_hit:
        return _breakeven_after_mfe_resolution(row, 1.0)
    if not _managed_be_was_triggered(row, 1.0):
        return base, False
    if not _be_delivery_is_proven(row):
        return None, True
    if row.get("be_exit_at"):
        delivered_at = _parse_utc_datetime(row.get("be_mail_sent_at"))
        be_exit_at = _parse_utc_datetime(row.get("be_exit_at"))
        if be_exit_at is None or delivered_at is None or be_exit_at < delivered_at:
            return None, True
        be_exit_r = breakeven_adjusted_r(row)
        if be_exit_r is None:
            return None, True
        # Der beobachtete BE-Exit ersetzt den Exit der Resthaelfte und darf bei
        # einem Gap unter/ueber Einstand negativ sein.
        return round(base - 0.5 * realized + 0.5 * be_exit_r, 4), False
    if realized >= 0.0 and not _is_ambiguous_outcome(row):
        return base, False
    return None, True


def _shadow_counterfactual_5050_be_resolution(
    row: Dict[str, Any],
) -> Tuple[Optional[float], bool]:
    """Resolve a causal, explicitly counterfactual policy result for shadows.

    Shadow rows never received a stop-update mail, so ``be_mail_sent_at`` must
    stay empty and the live-delivery resolver must not be reused.  Recovery can
    nevertheless observe the *policy counterfactual* when complete geometry
    and chronology prove it:

    * before +1R, the BE rule never became active;
    * a later virtual BE touch recorded by the evaluator supplies its real
      open/entry-level fill; or
    * a non-ambiguous TP2 outcome with TP1 <= TP2 chronology and MFE reaching
      both +2R and the geometric TP2 threshold is a completed positive path.

    Every other triggered shadow stays unresolved, keeping release fail-closed.
    This function never writes or infers a real delivery acknowledgement.
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None, False
    geometry = _managed_control_geometry(row)
    if geometry is None:
        return None, True
    base = _managed_r_50_50(row)
    if base is None:
        return None, True
    if not _managed_be_was_triggered(row, 1.0):
        return base, False

    trigger_at = (
        _parse_utc_datetime(row.get("be_trigger_at"))
        or _parse_utc_datetime(row.get("be_activated_at"))
    )
    be_exit_at = _parse_utc_datetime(row.get("be_exit_at"))
    be_mode = str(row.get("be_exit_evidence_mode") or "").strip().lower()
    if (
        trigger_at is not None
        and be_exit_at is not None
        and be_exit_at >= trigger_at
        and be_mode.startswith("shadow_counterfactual_")
    ):
        be_exit = _to_float(row.get("be_exit_fill_price"))
        if be_exit is None:
            return None, True
        be_exit_r = _signed_r(
            be_exit, geometry["fill"], geometry["risk"], geometry["direction"]
        )
        tp1_hit = bool(row.get("tp1_hit_at")) or row.get("status") == STATUS_TP2
        if not tp1_hit:
            return round(be_exit_r, 4), False
        return round(base - 0.5 * realized + 0.5 * be_exit_r, 4), False

    tp1_at = _parse_utc_datetime(row.get("tp1_hit_at"))
    tp2_at = _parse_utc_datetime(row.get("tp2_hit_at"))
    mfe = _to_float(row.get("max_favorable_r"))
    geometric_tp2_r = _signed_r(
        geometry["tp2"],
        geometry["fill"],
        geometry["risk"],
        geometry["direction"],
    )
    completed_tp2_path = (
        row.get("status") == STATUS_TP2
        and not _is_ambiguous_outcome(row)
        and tp1_at is not None
        and tp2_at is not None
        and tp2_at >= tp1_at
        and mfe is not None
        and mfe >= max(2.0, geometric_tp2_r) - 1e-6
    )
    if completed_tp2_path:
        return base, False
    return None, True


def simulate_managed_5050_breakeven(row: Dict[str, Any]) -> Optional[float]:
    """Gegenprobe '50/50-Management + Breakeven-Rest nach TP1' (2026-07-29).

    Strengere Variante der bestehenden Empfehlung: TP1 = 50% raus, Rest laeuft
    mit Stop auf Einstand. Ein Gap kann die zweite Haelfte trotzdem negativ
    ausfuehren; deshalb wird nur ein kausal gespeicherter BE-Exit angerechnet.
    TP1 nie erreicht: faellt auf die BE-nach-+1R-Regel zurueck.
    None bedeutet fehlendes r_realized oder unvollstaendige BE-Evidenz.
    """
    value, _unresolved = _managed_5050_be_resolution(row)
    return value


def _recommended_payoff_statistics(values: List[float]) -> Dict[str, Any]:
    """Exakte Payoff-Metriken fuer das empfohlene 50/50-plus-BE-Modell."""
    clean: List[float] = []
    for value in values:
        parsed = _to_float(value)
        if parsed is not None and math.isfinite(parsed):
            clean.append(parsed)
    wins = [value for value in clean if value > 0.0]
    losses = [value for value in clean if value < 0.0]
    breakevens = [value for value in clean if value == 0.0]
    decided = len(clean)
    decisive = len(wins) + len(losses)
    avg_win = sum(wins) / len(wins) if wins else None
    avg_loss = abs(sum(losses) / len(losses)) if losses else None
    if avg_win is not None and avg_loss is not None and (avg_win + avg_loss) > 0.0:
        conditional_breakeven = 100.0 * avg_loss / (avg_win + avg_loss)
        # Die berichtete Trefferquote zaehlt 0R-Ausgaenge im Nenner mit.
        # Deshalb muss auch die erforderliche Gesamt-Trefferquote um die
        # beobachtete Einstandsquote bereinigt werden.
        decisive_share = decisive / decided if decided else 0.0
        breakeven = conditional_breakeven * decisive_share
    elif avg_win is not None and avg_loss is None:
        conditional_breakeven = 0.0
        breakeven = 0.0
    elif avg_win is None and avg_loss is not None:
        conditional_breakeven = 100.0
        breakeven = 100.0
    else:
        conditional_breakeven = None
        breakeven = None
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0.0 else None
    return {
        "decided": decided,
        "wins": len(wins),
        "losses": len(losses),
        "breakevens": len(breakevens),
        "win_rate_pct": round(100.0 * len(wins) / decided, 1) if decided else None,
        "win_rate_ex_breakeven_pct": (
            round(100.0 * len(wins) / decisive, 1) if decisive else None
        ),
        "breakeven_outcome_rate_pct": (
            round(100.0 * len(breakevens) / decided, 1) if decided else None
        ),
        "win_rate_wilson_95": _wilson_interval_95(len(wins), decided),
        "avg_r": round(sum(clean) / decided, 3) if decided else None,
        "sum_r": round(sum(clean), 3) if decided else 0.0,
        "avg_win_r": round(avg_win, 3) if avg_win is not None else None,
        "avg_loss_r": round(avg_loss, 3) if avg_loss is not None else None,
        "profit_factor": round(profit_factor, 3) if profit_factor is not None else None,
        "breakeven_win_rate_pct": round(breakeven, 1) if breakeven is not None else None,
        "breakeven_win_rate_ex_breakeven_pct": (
            round(conditional_breakeven, 1)
            if conditional_breakeven is not None
            else None
        ),
    }


def _easter_sunday(year: int) -> date:
    """Gregorian Easter date (Meeus/Jones/Butcher), dependency-free."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, occurrence: int) -> date:
    first = date(year, month, 1)
    offset = (weekday - first.weekday()) % 7
    return first + timedelta(days=offset + 7 * (occurrence - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    first_next = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    cursor = first_next - timedelta(days=1)
    return cursor - timedelta(days=(cursor.weekday() - weekday) % 7)


def _observed_fixed_holiday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def _observed_new_year_holiday(day: date) -> Optional[date]:
    """NYSE New Year observation rule (Saturday has no Friday substitute)."""
    if day.weekday() == 5:
        return None
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


# Non-recurring, officially announced full-day NYSE closures.  Keep these
# separate from rule-derived holidays so additions remain explicit and
# auditable instead of being guessed from a federal-holiday calendar.
_US_EQUITY_SPECIAL_FULL_DAY_CLOSURES = {
    2025: ((1, 9),),  # National Day of Mourning for President Jimmy Carter
}


@lru_cache(maxsize=32)
def _us_equity_holidays(year: int) -> frozenset[date]:
    """Regular and explicitly announced full-day NYSE closures.

    Two extra trading sessions are added below, so unexpected one-off exchange
    closures and data-finalisation delays do not leak right-censored signals
    into the mature cohort.
    """
    holidays = {
        _nth_weekday(year, 1, 0, 3),   # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),   # Presidents' Day
        _easter_sunday(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),     # Memorial Day
        _observed_fixed_holiday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),   # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving
        _observed_fixed_holiday(date(year, 12, 25)),
    }
    observed_new_year = _observed_new_year_holiday(date(year, 1, 1))
    if observed_new_year is not None:
        holidays.add(observed_new_year)
    if year >= 2022:
        holidays.add(_observed_fixed_holiday(date(year, 6, 19)))
    # A Saturday New Year's Day has no preceding-Friday NYSE substitute.  This
    # branch therefore normally handles only future rule variants defensively.
    observed_next_new_year = _observed_new_year_holiday(date(year + 1, 1, 1))
    if observed_next_new_year is not None and observed_next_new_year.year == year:
        holidays.add(observed_next_new_year)
    holidays.update(
        date(year, month, day)
        for month, day in _US_EQUITY_SPECIAL_FULL_DAY_CLOSURES.get(int(year), ())
    )
    return frozenset(holidays)


def _is_us_equity_session(day: date) -> bool:
    return day.weekday() < 5 and day not in _us_equity_holidays(day.year)


@lru_cache(maxsize=32)
def _us_equity_early_closes(year: int) -> frozenset[date]:
    """Official known NYSE 13:00 ET closes used by the tracker calendar.

    Early-close dates do not follow a safe year-agnostic rule (2026 closes on
    July 2, while 2027 has no July half-day).  Keep the same explicit official
    ICE/NYSE 2025-2027 schedule as the app exchange calendar. Unknown years
    deliberately fall back to 16:00, which delays evidence instead of treating
    a still-running session as complete.
    """
    known = {
        2025: ((7, 3), (11, 28), (12, 24)),
        2026: ((7, 2), (11, 27), (12, 24)),
        2027: ((11, 26),),
    }
    return frozenset(
        date(year, month, day)
        for month, day in known.get(int(year), ())
        if _is_us_equity_session(date(year, month, day))
    )


def _us_equity_session_close(day: date) -> Optional[datetime]:
    """Official recurring NYSE session close as an America/New_York instant."""
    if not _is_us_equity_session(day):
        return None
    close_hour = 13 if day in _us_equity_early_closes(day.year) else 16
    return datetime(
        day.year,
        day.month,
        day.day,
        close_hour,
        0,
        tzinfo=ZoneInfo("America/New_York"),
    )


def _stock_maturity_at(created_at: datetime, observation_bars: int) -> datetime:
    created_ny = created_at.astimezone(ZoneInfo("America/New_York"))
    remaining = max(1, int(observation_bars)) + 2
    cursor = created_ny.date()
    while remaining > 0:
        cursor += timedelta(days=1)
        if _is_us_equity_session(cursor):
            remaining -= 1
    # End-of-UTC-day is deliberately later than the regular close and avoids
    # claiming maturity while the final session's provider bar is unfinished.
    return datetime(cursor.year, cursor.month, cursor.day, 23, 59, 59, tzinfo=timezone.utc)


def _signal_maturity_at(row: Dict[str, Any]) -> Optional[datetime]:
    """Return the conservative UTC instant at which a signal is fully observed.

    Krypto wird 120 Stunden beobachtet. Bei Aktien kann der Fill erst am Ende
    des Entry-Fensters erfolgen und danach noch den gesamten Haltedauer-
    Horizont benoetigen. Deshalb umfasst die Reifepruefung im Worst Case
    ``2 * bars - 1`` Daily-Bars. Die Reife wird ueber regulaere US-Aktien-
    Sessions plus zwei konservative Zusatz-Sessions berechnet. Ein frueher Stop wird bewusst nicht vorzeitig
    als reifes Ergebnis gewertet, solange potenzielle Gewinner aus derselben
    Versandkohorte noch offen sein koennen.
    """
    causal_start = _signal_causal_start(row)
    if causal_start is None:
        return None
    asset_class = str(row.get("asset_class") or "stock").strip().lower()
    if asset_class == "crypto":
        horizon = timedelta(hours=CRYPTO_EXPIRY_HOURS)
    else:
        bars = _stock_horizon_bars(row)
        max_observation_bars = 1 if bars <= 1 else (2 * bars) - 1
        return _stock_maturity_at(causal_start, max_observation_bars)
    return causal_start + horizon


def _signal_has_full_observation_window(row: Dict[str, Any], as_of: datetime) -> bool:
    """Rechtszensierte Signale aus belastbaren Performance-Kohorten fernhalten."""
    maturity_at = _signal_maturity_at(row)
    return bool(maturity_at is not None and maturity_at <= as_of)


# 119 regular sessions for the supported 60-bar worst case, plus the two
# conservative sessions above, fit within 190 calendar days even across the
# densest regular US holiday windows. Querying farther is cheap and fail-safe.
_MAX_PERFORMANCE_MATURITY_LOOKBACK_DAYS = 190


def _register_eval_failure(sig: Dict[str, Any], now_dt: datetime) -> Dict[str, Any]:
    """Fehlversuch zaehlen; ab MAX_EVAL_FAILS Fehlversuchen -> UNTRACKED."""
    fail_count = int(sig.get("eval_fail_count") or 0) + 1
    updates: Dict[str, Any] = {
        "eval_fail_count": fail_count,
    }
    if fail_count >= MAX_EVAL_FAILS:
        updates.update(
            _untracked_state_updates(
                sig,
                now_dt,
                unfilled_detail="eval_failed_%dx" % fail_count,
                after_fill_detail=_UNTRACKED_AFTER_FILL_FAILURE_DETAIL,
            )
        )
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


def _daily_bar_is_complete(bar: Dict[str, Any], bar_date: date, now_dt: datetime) -> bool:
    """Return whether a Daily OHLC bar is closed and therefore causal.

    Injected/test fetchers may provide an explicit boolean.  Production Daily
    rows currently do not, so the current New-York session is held back until
    five minutes after its exchange-calendar close (including 13:00 ET early
    closes).
    """
    explicit = bar.get("interval_complete")
    if explicit is True:
        return True
    if explicit is False:
        return False
    now_ny = now_dt.astimezone(ZoneInfo("America/New_York"))
    if bar_date < now_ny.date():
        return True
    close_at = _us_equity_session_close(bar_date)
    if bar_date > now_ny.date() or close_at is None:
        return False
    return now_ny >= close_at + timedelta(minutes=5)


def _latest_completed_stock_session(now_dt: datetime) -> date:
    """Latest regular US-equity session conservatively complete at ``now``."""
    now_ny = now_dt.astimezone(ZoneInfo("America/New_York"))
    cursor = now_ny.date()
    close_at = _us_equity_session_close(cursor)
    if close_at is None or now_ny < close_at + timedelta(minutes=5):
        cursor -= timedelta(days=1)
    while not _is_us_equity_session(cursor):
        cursor -= timedelta(days=1)
    return cursor


def _normalize_daily_bars(
    bars_raw: Any,
    created_date: date,
    now_dt: datetime,
) -> Tuple[List[Tuple[date, float, float, float, float]], Optional[str]]:
    """Validate complete post-alert Daily bars and prove session continuity.

    Der Alert-Tag selbst wird bewusst NICHT bewertet: seine Daily-Bar enthaelt
    auch die Kursbewegung VOR dem Alert und waere damit nicht aussagekraeftig.
    Malformed bars and missing regular sessions are fail-closed instead of
    being skipped in front of a later winner.  A running current-day bar is
    valid input but is not allowed to count toward fill/holding expiry.
    """
    if bars_raw is None:
        return [], "fetch_missing"
    if not isinstance(bars_raw, (list, tuple)):
        return [], "payload_not_a_bar_list"
    bars: List[Tuple[date, float, float, float, float]] = []
    seen_dates: set[date] = set()
    for bar in bars_raw:
        if not isinstance(bar, dict):
            return [], "malformed_daily_bar"
        bar_date = _parse_bar_date(bar.get("date"))
        if bar_date is None:
            return [], "malformed_daily_bar_date"
        if bar_date <= created_date:
            continue
        if not _is_us_equity_session(bar_date):
            return [], "daily_bar_on_non_session"
        if not _daily_bar_is_complete(bar, bar_date, now_dt):
            # Running/future bars carry no terminal evidence.  Ignore them
            # without counting a provider failure; a later run can settle them.
            continue
        high = _to_float(bar.get("high"))
        low = _to_float(bar.get("low"))
        close = _to_float(bar.get("close"))
        open_price = _to_float(bar.get("open"))
        if (
            bar_date is None
            or open_price is None
            or high is None
            or low is None
            or close is None
        ):
            return [], "malformed_daily_ohlc"
        if min(open_price, high, low, close) <= 0 or high < max(open_price, close) or low > min(open_price, close):
            return [], "invalid_daily_ohlc_geometry"
        if bar_date in seen_dates:
            return [], "duplicate_daily_session"
        seen_dates.add(bar_date)
        bars.append((bar_date, open_price, high, low, close))
    bars.sort(key=lambda item: item[0])
    if bars:
        expected: set[date] = set()
        cursor = created_date
        last_date = bars[-1][0]
        while cursor < last_date:
            cursor += timedelta(days=1)
            if _is_us_equity_session(cursor):
                expected.add(cursor)
        missing = expected.difference(seen_dates)
        if missing:
            return [], "missing_daily_sessions:" + ",".join(
                day.isoformat() for day in sorted(missing)
            )
    elif _latest_completed_stock_session(now_dt) > created_date:
        return [], "missing_all_completed_daily_sessions"
    return bars, None


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
    Expiry: nach dem beim Versand eingefrorenen Strategie-Horizont ohne
    Stop/TP2 -> EXPIRED mit
    r_realized = R des letzten Close (outcome_detail 'tp1_then_expired',
    falls TP1 vorher erreicht war).
    """
    causal_start = _signal_causal_start(sig) or now_dt
    created_date = causal_start.astimezone(ZoneInfo("America/New_York")).date()
    bars_raw = fetcher(sig["ticker"], created_date.isoformat())

    entry = float(sig["entry"])
    stop = float(sig["stop"])
    tp1 = float(sig["tp1"]) if sig.get("tp1") is not None else None
    tp2 = float(sig["tp2"]) if sig.get("tp2") is not None else None
    direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
    horizon_bars = _stock_horizon_bars(sig)
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    planned_risk = geometry.get("risk")
    if not geometry.get("valid") or planned_risk is None:
        return _register_eval_failure(sig, now_dt), True

    bars, bars_issue = _normalize_daily_bars(bars_raw, created_date, now_dt)
    if bars_issue:
        # An incomplete Daily payload is not OHLC evidence.  In particular,
        # never substitute close for a missing open: that fabricated the gap
        # execution used by the old tracker.  Use the normal retry path and
        # keep the signal out of decided performance until complete data exist.
        logger.warning(
            "Signal %s/%s: Daily-Historie unvollstaendig (%s)",
            sig.get("scanner"), sig.get("ticker"), bars_issue,
        )
        return _register_eval_failure(sig, now_dt), True
    if not bars:
        # A valid empty payload (for example before today's close) is not an
        # evaluation failure and must never age a signal into UNTRACKED.
        return {}, False

    now_iso = now_dt.isoformat()
    updates: Dict[str, Any] = {"last_eval_at": now_iso}
    tp1_hit_at = sig.get("tp1_hit_at") or None
    max_fav = float(sig.get("max_favorable_r") or 0.0)
    max_adv = float(sig.get("max_adverse_r") or 0.0)
    fill_at = sig.get("entry_filled_at") or None
    fill_price = _to_float(sig.get("entry_fill_price"))
    fill_date = _parse_bar_date(fill_at) if fill_at else None
    be_exit_fill_price = _to_float(sig.get("be_exit_fill_price"))
    shadow_counterfactual_be = (
        str(sig.get("mail_class") or "").strip().lower() == "shadow"
    )
    # A trading rule becomes user-actionable only after the dedicated update
    # was actually delivered. Trigger/activation time alone is not execution.
    be_effective_candidates = [
        value
        for value in (
            _parse_bar_date(sig.get("be_trigger_at")),
            _parse_bar_date(sig.get("be_mail_sent_at")),
        )
        if value is not None
    ]
    be_effective_date = max(be_effective_candidates) if be_effective_candidates else None
    bars_after_alert = 0
    holding_bars = 0

    if fill_price is not None:
        fill_check = _fill_quality(
            str(sig.get("scanner") or ""),
            entry,
            fill_price,
            stop,
            float(tp1),
            float(tp2),
            direction,
        )
        if not fill_check.get("valid"):
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": _fill_rejection_detail(fill_check),
            })
            updates.update(_no_fill_cleanup_updates())
            return updates, False

    for bar_date, open_price, high, low, close in bars:
        bars_after_alert += 1
        filled_before_bar = fill_price is not None
        filled_intrabar_this_bar = False
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
                    filled_intrabar_this_bar = True
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
                    filled_intrabar_this_bar = True
            if fill_price is None:
                if bars_after_alert >= horizon_bars:
                    updates.update({
                        "status": STATUS_NO_FILL,
                        "closed_at": now_iso,
                        "outcome_detail": "entry_not_reached",
                    })
                    break
                continue
            fill_check = _fill_quality(
                str(sig.get("scanner") or ""),
                entry,
                fill_price,
                stop,
                float(tp1),
                float(tp2),
                direction,
            )
            if not fill_check.get("valid"):
                updates.update({
                    "status": STATUS_NO_FILL,
                    "closed_at": now_iso,
                    "outcome_detail": _fill_rejection_detail(fill_check),
                })
                break
            fill_date = bar_date
            fill_at = bar_date.isoformat()
            updates["entry_filled_at"] = fill_at
            updates["entry_fill_price"] = round(fill_price, 8)
            updates["fill_evidence_mode"] = "post_alert_interval"

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

        if (
            (sig.get("be_mail_sent_at") or shadow_counterfactual_be)
            and be_exit_fill_price is None
            and be_effective_date is not None
            and bar_date > be_effective_date
        ):
            be_touched = low <= fill_price if direction == "LONG" else high >= fill_price
            if be_touched:
                if direction == "LONG" and open_price < fill_price:
                    be_exit_fill_price = open_price
                elif direction == "SHORT" and open_price > fill_price:
                    be_exit_fill_price = open_price
                else:
                    be_exit_fill_price = fill_price
                updates.update({
                    "be_exit_fill_price": round(be_exit_fill_price, 8),
                    "be_exit_at": day_iso,
                    "be_exit_evidence_mode": (
                        "shadow_counterfactual_daily_open_or_entry_level"
                        if shadow_counterfactual_be
                        else "daily_open_or_entry_level"
                    ),
                })

        if (
            not sig.get("be_activated_at")
            and "be_trigger_at" not in updates
            and max_fav >= 1.0
        ):
            updates["be_trigger_at"] = day_iso

        if stop_hit:
            # Stop und ein NEUES TP-Level am selben Tag -> Reihenfolge unklar:
            # konservativ den Stop zuerst werten, TP nicht gutschreiben.
            ambiguous_entry_stop = filled_intrabar_this_bar
            if direction == "LONG":
                gap_through_stop = filled_before_bar and open_price < stop
            else:
                gap_through_stop = filled_before_bar and open_price > stop
            stop_exit = open_price if gap_through_stop else stop
            stop_gap_slippage = bool(gap_through_stop)
            # Bei einem echten Gap liegt der Stop bereits zur Eroeffnung hinter
            # dem Markt. Ein spaeteres Tagesziel kann daher nicht vor dem Stop
            # erreicht worden sein und ist keine Pfad-Obergrenze.
            ambiguous_target = (not stop_gap_slippage) and (
                tp2_hit or (tp1_touch and not tp1_hit_at)
            )
            lower_r = round(_signed_r(stop_exit, fill_price, risk, direction), 4)
            upper_r = lower_r
            if ambiguous_target:
                upper_target = tp2 if tp2_hit else tp1
                if upper_target is not None:
                    upper_r = round(_signed_r(upper_target, fill_price, risk, direction), 4)
            elif ambiguous_entry_stop:
                # Ohne Intraday-Reihenfolge ist auch moeglich, dass der Stop-
                # Extrempunkt vor dem Entry lag. Dann blieb die Position bis
                # zum Close offen. Das ist nur eine Obergrenze, kein Gewinn.
                upper_r = max(
                    lower_r,
                    round(_signed_r(close, fill_price, risk, direction), 4),
                )
            if ambiguous_entry_stop and ambiguous_target:
                outcome_detail = "ambiguous_same_day_entry_stop_and_target"
            elif ambiguous_entry_stop:
                outcome_detail = "ambiguous_same_day_entry_and_stop"
            elif ambiguous_target:
                outcome_detail = "ambiguous_same_day"
            elif stop_gap_slippage:
                outcome_detail = "stop_gap_slippage"
            else:
                outcome_detail = ""
            updates.update({
                "status": STATUS_STOP,
                "stop_hit_at": day_iso,
                "closed_at": now_iso,
                "r_realized": lower_r,
                "r_realized_upper": max(lower_r, upper_r),
                "outcome_detail": outcome_detail,
            })
            updates.update(_stop_exit_metrics(stop_exit, stop, fill_price, direction))
            break
        if tp2_hit:
            if tp1 is not None and not tp1_hit_at:
                tp1_hit_at = day_iso  # TP2 impliziert TP1
            updates.update({
                "status": STATUS_TP2,
                "tp2_hit_at": day_iso,
                "closed_at": now_iso,
                "r_realized": round(_signed_r(tp2, fill_price, risk, direction), 4),
                "r_realized_upper": round(_signed_r(tp2, fill_price, risk, direction), 4),
                "outcome_detail": "",
            })
            break
        if tp1_touch and not tp1_hit_at:
            tp1_hit_at = day_iso
        if holding_bars >= horizon_bars:
            expiry_r = round(_signed_r(close, fill_price, risk, direction), 4)
            updates.update({
                "status": STATUS_EXPIRED,
                "closed_at": now_iso,
                "r_realized": expiry_r,
                "r_realized_upper": expiry_r,
                "outcome_detail": "tp1_then_expired" if tp1_hit_at else "",
            })
            break

    if tp1_hit_at:
        updates["tp1_hit_at"] = tp1_hit_at
    updates["max_favorable_r"] = round(max_fav, 4)
    updates["max_adverse_r"] = round(max_adv, 4)
    return updates, False


def _fetch_crypto_price(
    fetcher: Callable[..., Any],
    sig: Dict[str, Any],
    now_dt: Optional[datetime] = None,
) -> Any:
    """Call legacy and interval-aware crypto fetchers without masking errors."""
    ticker = sig["ticker"]
    identity = {
        "instrument_id": sig.get("instrument_id"),
        "venue": sig.get("venue"),
        "contract_symbol": sig.get("contract_symbol"),
        "since": sig.get("last_eval_at") or (
            _signal_causal_start(sig).isoformat()
            if _signal_causal_start(sig) is not None
            else None
        ),
        "until": now_dt.isoformat() if now_dt is not None else None,
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


def _normalize_crypto_observation(raw: Any) -> Optional[Dict[str, Any]]:
    """Normalize a point price or an exchange interval observation."""
    if isinstance(raw, dict):
        raw_intervals = raw.get("intervals")
        intervals: List[Dict[str, Any]] = []
        if isinstance(raw_intervals, list):
            for interval in raw_intervals:
                normalized_interval = _normalize_crypto_observation(interval)
                if normalized_interval is None:
                    return None
                normalized_interval.pop("intervals", None)
                intervals.append(normalized_interval)
        current = None
        for key in ("current", "price", "close", "last"):
            current = _to_float(raw.get(key))
            if current is not None:
                break
        interval_high = None
        for key in ("interval_high", "high"):
            interval_high = _to_float(raw.get(key))
            if interval_high is not None:
                break
        interval_low = None
        for key in ("interval_low", "low"):
            interval_low = _to_float(raw.get(key))
            if interval_low is not None:
                break
        interval_open = None
        for key in ("interval_open", "open"):
            interval_open = _to_float(raw.get(key))
            if interval_open is not None:
                break
        complete_raw = raw.get("interval_complete", raw.get("complete", False))
        if isinstance(complete_raw, str):
            interval_complete = complete_raw.strip().lower() in {"1", "true", "yes", "ok"}
        else:
            interval_complete = bool(complete_raw)
        source = str(raw.get("source") or "interval")
        observed_at = raw.get("observed_at") or raw.get("interval_end_at")
        started_at = raw.get("started_at") or raw.get("interval_start_at")
        boundary_overlap = raw.get("boundary_overlap") is True
    else:
        current = _to_float(raw)
        interval_open = current
        interval_high = current
        interval_low = current
        interval_complete = False
        source = "point"
        observed_at = None
        started_at = None
        boundary_overlap = False

    if current is None or current <= 0:
        return None
    if interval_high is None:
        interval_high = current
    if interval_low is None:
        interval_low = current
    if interval_high <= 0 or interval_low <= 0:
        return None
    if interval_low > interval_high:
        interval_low, interval_high = interval_high, interval_low
    if interval_open is not None and interval_open <= 0:
        interval_open = None
    normalized = {
        "current": current,
        "interval_open": interval_open,
        "interval_high": max(interval_high, current),
        "interval_low": min(interval_low, current),
        "interval_complete": interval_complete,
        "source": source,
        "observed_at": observed_at,
        "started_at": started_at,
        "boundary_overlap": boundary_overlap,
    }
    if isinstance(raw, dict) and intervals:
        normalized["intervals"] = intervals
    return normalized


def _initial_interval_boundary_mode(
    interval: Any,
    since_dt: datetime,
) -> Optional[str]:
    """Classify exact or explicitly overlapping causal-boundary coverage."""
    if not isinstance(interval, dict):
        return None
    started_at = _parse_utc_datetime(interval.get("started_at"))
    ended_at = _parse_utc_datetime(interval.get("observed_at"))
    if started_at is None or ended_at is None or ended_at <= started_at:
        return None
    tolerance_seconds = 1e-6
    if abs((started_at - since_dt).total_seconds()) <= tolerance_seconds:
        # An overlap marker on an exactly aligned interval is structurally
        # contradictory and must not weaken the ordinary exact-boundary rule.
        return None if interval.get("boundary_overlap") is True else "exact"
    if (
        interval.get("boundary_overlap") is True
        and started_at < since_dt
        and since_dt < ended_at
    ):
        return "overlap"
    return None


def _has_complete_interval_coverage(
    observation: Optional[Dict[str, Any]],
    since_dt: datetime,
    until_dt: Optional[datetime] = None,
) -> bool:
    """Prove ordered, gap-free and non-overlapping completed intervals.

    With ``until_dt=None`` this validates the complete returned prefix starting
    at the causal cursor.  A historical backfill additionally supplies its
    required tail boundary, which must be covered by the final interval.
    """
    if not isinstance(observation, dict):
        return False
    if until_dt is not None and until_dt < since_dt:
        return False
    intervals = observation.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        return False
    previous_end: Optional[datetime] = None
    boundary_tolerance_seconds = 1e-6
    for interval in intervals:
        if not isinstance(interval, dict) or not interval.get("interval_complete"):
            return False
        started_at = _parse_utc_datetime(interval.get("started_at"))
        ended_at = _parse_utc_datetime(interval.get("observed_at"))
        if started_at is None or ended_at is None or ended_at <= started_at:
            return False
        if previous_end is None:
            if _initial_interval_boundary_mode(interval, since_dt) is None:
                return False
        else:
            if interval.get("boundary_overlap") is True:
                return False
            # A positive delta is a missing candle; a negative delta is an
            # overlap or out-of-order payload.  Neither may be evaluated.
            boundary_delta = (started_at - previous_end).total_seconds()
            if abs(boundary_delta) > boundary_tolerance_seconds:
                return False
        previous_end = ended_at
    return bool(
        previous_end is not None
        and (until_dt is None or previous_end >= until_dt)
    )


def _interval_initial_boundary_mismatch(
    observation: Optional[Dict[str, Any]],
    since_dt: datetime,
) -> bool:
    """Detect a structurally unobservable gap/overlap at a causal boundary."""
    if not isinstance(observation, dict):
        return False
    intervals = observation.get("intervals")
    if not isinstance(intervals, list) or not intervals:
        return False
    first = intervals[0]
    if not isinstance(first, dict):
        return False
    return _initial_interval_boundary_mode(first, since_dt) is None


def _causal_boundary_interval_touches_relevant_level(
    sig: Mapping[str, Any],
    interval: Mapping[str, Any],
) -> bool:
    """Whether full-candle OHLC makes the post-boundary path unknowable.

    The marked candle contains both pre- and post-acceptance trading.  It may
    prove *non-touch* for its entire span, but any relevant touch could have
    happened on either side of the causal boundary and therefore cannot create
    a fill, target, stop, BE trigger or BE exit.
    """
    high = _to_float(interval.get("interval_high"))
    low = _to_float(interval.get("interval_low"))
    if high is None or low is None:
        return True
    direction = "SHORT" if str(sig.get("direction") or "").upper() == "SHORT" else "LONG"
    entry = _to_float(sig.get("entry"))
    stop = _to_float(sig.get("stop"))
    tp1 = _to_float(sig.get("tp1"))
    tp2 = _to_float(sig.get("tp2"))
    fill = _to_float(sig.get("entry_fill_price"))
    has_fill = bool(sig.get("entry_filled_at")) and fill is not None

    if direction == "LONG":
        if not has_fill and any(
            (
                entry is not None and high >= entry,
                stop is not None and low <= stop,
                tp1 is not None and high >= tp1,
                tp2 is not None and high >= tp2,
            )
        ):
            return True
        if has_fill and any(
            (
                stop is not None and low <= stop,
                not sig.get("tp1_hit_at") and tp1 is not None and high >= tp1,
                tp2 is not None and high >= tp2,
            )
        ):
            return True
    else:
        if not has_fill and any(
            (
                entry is not None and low <= entry,
                stop is not None and high >= stop,
                tp1 is not None and low <= tp1,
                tp2 is not None and low <= tp2,
            )
        ):
            return True
        if has_fill and any(
            (
                stop is not None and high >= stop,
                not sig.get("tp1_hit_at") and tp1 is not None and low <= tp1,
                tp2 is not None and low <= tp2,
            )
        ):
            return True

    if has_fill and stop is not None:
        risk = abs(float(fill) - float(stop))
        if risk > 0 and not sig.get("be_activated_at"):
            be_trigger = fill - risk if direction == "SHORT" else fill + risk
            if (direction == "SHORT" and low <= be_trigger) or (
                direction == "LONG" and high >= be_trigger
            ):
                return True
        if sig.get("be_activated_at") and sig.get("be_mail_sent_at"):
            if (direction == "SHORT" and high >= fill) or (
                direction == "LONG" and low <= fill
            ):
                return True
    return False


def _evaluate_crypto_signal(
    sig: Dict[str, Any],
    fetcher: Callable[..., Any],
    now_dt: datetime,
    *,
    expiry_hours: Optional[int] = CRYPTO_EXPIRY_HOURS,
    register_failures: bool = True,
) -> Tuple[Dict[str, Any], bool]:
    """Signal conservatively evaluated with completed interval ranges.

    Exchange-native interval high/low values capture touches between evaluator
    runs. If one completed interval touches mutually exclusive levels and their
    order cannot be proven, the evaluator chooses the conservative outcome.
    """
    observation = _normalize_crypto_observation(_fetch_crypto_price(fetcher, sig, now_dt))
    if observation is None:
        if register_failures:
            return _register_eval_failure(sig, now_dt), True
        # Do not advance the cursor until a completed post-alert interval exists.
        return {}, False

    intervals = observation.get("intervals") or []
    if intervals:
        causal_since = (
            _parse_utc_datetime(sig.get("last_eval_at"))
            or _signal_causal_start(sig)
        )
        if (
            causal_since is not None
            and _interval_initial_boundary_mismatch(observation, causal_since)
        ):
            return _untracked_state_updates(
                sig,
                now_dt,
                unfilled_detail="initial_interval_coverage_incomplete",
                after_fill_detail=(
                    "initial_interval_coverage_incomplete_after_confirmed_fill"
                ),
            ), True
        if causal_since is None or not _has_complete_interval_coverage(
            observation, causal_since
        ):
            # Validate the entire returned list before consuming its first
            # candle.  Otherwise a later TP2 after a missing/overlapping candle
            # could settle the signal and permanently discard the causal
            # cursor needed to retry the same range.
            if register_failures:
                return _register_eval_failure(sig, now_dt), True
            return {}, True
        working = dict(sig)
        combined: Dict[str, Any] = {}
        any_failure = False
        for interval_index, interval in enumerate(intervals):
            if interval_index == 0 and interval.get("boundary_overlap") is True:
                if _causal_boundary_interval_touches_relevant_level(working, interval):
                    # Full 5m OHLC cannot locate the touch before or after the
                    # arbitrary SMTP-acceptance instant.  This is terminally
                    # unresolved, never a fabricated fill or R outcome.
                    unresolved = _untracked_state_updates(
                        working,
                        now_dt,
                        unfilled_detail=_CAUSAL_BOUNDARY_TOUCH_UNRESOLVED,
                        after_fill_detail=(
                            _CAUSAL_BOUNDARY_TOUCH_UNRESOLVED_AFTER_FILL
                        ),
                    )
                    combined.update(unresolved)
                    return combined, True
                boundary_end = _parse_utc_datetime(interval.get("observed_at"))
                if boundary_end is None:  # coverage validation should preclude this
                    if register_failures:
                        return _register_eval_failure(sig, now_dt), True
                    return {}, True
                # A no-touch over the *whole* candle proves no relevant event
                # after acceptance too.  Skip its pre-causal extrema entirely,
                # but advance to the exact candle end before evaluating later
                # contiguous intervals.
                boundary_updates = {"last_eval_at": boundary_end.isoformat()}
                combined.update(boundary_updates)
                working.update(boundary_updates)
                continue

            def _single_fetcher(*_args: Any, _interval=interval, **_kwargs: Any) -> Dict[str, Any]:
                return dict(_interval)

            interval_now = _parse_utc_datetime(interval.get("observed_at")) or now_dt
            interval_updates, interval_failed = _evaluate_crypto_signal(
                working,
                _single_fetcher,
                interval_now,
                expiry_hours=expiry_hours,
                register_failures=register_failures,
            )
            any_failure = any_failure or interval_failed
            combined.update(interval_updates)
            working.update(interval_updates)
            if working.get("status") and working.get("status") != STATUS_OPEN:
                break
        return combined, any_failure

    price = float(observation["current"])
    interval_high = float(observation["interval_high"])
    interval_low = float(observation["interval_low"])
    interval_open = _to_float(observation.get("interval_open"))
    interval_complete = bool(observation["interval_complete"])
    observation_source = str(observation.get("source") or "")

    entry = float(sig["entry"])
    stop = float(sig["stop"])
    tp1 = float(sig["tp1"]) if sig.get("tp1") is not None else None
    tp2 = float(sig["tp2"]) if sig.get("tp2") is not None else None
    direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
    geometry = trade_geometry(entry, stop, tp1, tp2, direction)
    if not geometry.get("valid"):
        if register_failures:
            return _register_eval_failure(sig, now_dt), True
        return {}, False

    now_iso = now_dt.isoformat()
    created_dt = _signal_causal_start(sig)
    expired = (
        expiry_hours is not None
        and created_dt is not None
        and now_dt >= created_dt + timedelta(hours=expiry_hours)
    )
    fill_at = sig.get("entry_filled_at") or None
    fill_price = _to_float(sig.get("entry_fill_price"))
    # Only a completed interval may advance the causal cursor. A running bar
    # would otherwise make its unfinished range disappear from the next fetch.
    updates: Dict[str, Any] = {"last_eval_at": now_iso} if interval_complete else {}

    if direction == "LONG":
        entry_touched = interval_high >= entry
        stop_touched = interval_low <= stop
        tp1_touched = tp1 is not None and interval_high >= tp1
        tp2_touched = tp2 is not None and interval_high >= tp2
    else:
        entry_touched = interval_low <= entry
        stop_touched = interval_high >= stop
        tp1_touched = tp1 is not None and interval_low <= tp1
        tp2_touched = tp2 is not None and interval_low <= tp2

    if fill_price is None:
        if observation_source in {"coingecko_point_fallback", "point"} and not interval_complete:
            # A point has no path. It proves neither entry-first nor
            # invalidation-first; treating only the stop-side point as
            # NO_FILL would asymmetrically erase possible losses.
            if expired:
                updates.update(
                    _untracked_state_updates(
                        sig,
                        now_dt,
                        unfilled_detail=(
                            "observation_window_ended_without_interval_path"
                        ),
                    )
                )
            return updates, False

        # A partial interval cannot establish whether entry or invalidation
        # happened first.  In particular, aggregated high/low from a response
        # with a missing first candle must never turn a possible filled loss
        # into NO_FILL.  At expiry the unresolved path is honestly UNTRACKED.
        if not interval_complete:
            if expired:
                updates.update(
                    _untracked_state_updates(
                        sig,
                        now_dt,
                        unfilled_detail=(
                            "observation_window_ended_without_interval_path"
                        ),
                    )
                )
            return updates, False

        open_known = interval_open is not None
        if direction == "LONG":
            open_invalidated = open_known and interval_open <= stop
            open_beyond_target = open_known and tp1 is not None and interval_open >= tp1
            filled_at_open = open_known and interval_open >= entry
        else:
            open_invalidated = open_known and interval_open >= stop
            open_beyond_target = open_known and tp1 is not None and interval_open <= tp1
            filled_at_open = open_known and interval_open <= entry

        # The interval open is the only OHLC point with a proven order.  If it
        # already lies beyond stop/TP1, invalidation happened before a possible
        # new entry.  Conversely, an executable open on the entry side proves a
        # fill before all later extrema in this completed interval.
        if open_invalidated:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_invalidated_before_fill",
            })
            return updates, False
        if open_beyond_target:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_observed_after_tp1",
            })
            return updates, False

        # If the open was still between stop and entry, a bar touching both
        # levels permits two incompatible histories: invalidation before entry
        # (no trade) or entry before stop (filled loss).  Neither NO_FILL nor a
        # filled R-result is proved, so keep it out of both cohorts as UNTRACKED.
        if entry_touched and stop_touched and not filled_at_open:
            updates.update(
                _untracked_state_updates(
                    sig,
                    now_dt,
                    unfilled_detail="ambiguous_entry_and_stop_same_interval",
                )
            )
            return updates, False

        # Without an interval open, a simultaneous first entry/target
        # observation cannot distinguish a gap beyond TP1 from a causal cross
        # through entry.  Do not invent an executable fill price.
        if entry_touched and tp1_touched and not open_known:
            updates.update(
                _untracked_state_updates(
                    sig,
                    now_dt,
                    unfilled_detail="ambiguous_entry_and_target_same_interval",
                )
            )
            return updates, False

        # ``price_at_alert`` is presentation data unless it already produced
        # a verified persisted fill during record_alert_signals().  Only this
        # completed post-alert interval may now establish path or invalidation.
        invalidated_before_fill = interval_complete and stop_touched and not entry_touched
        if invalidated_before_fill:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_invalidated_before_fill",
            })
            return updates, False
        if entry_touched:
            fill_price = interval_open if filled_at_open else entry
        elif expired:
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": "entry_not_reached",
            })
            return updates, False
        else:
            return updates, False

        fill_check = _fill_quality(
            str(sig.get("scanner") or ""),
            entry,
            fill_price,
            stop,
            float(tp1),
            float(tp2),
            direction,
        )
        if not fill_check.get("valid"):
            updates.update({
                "status": STATUS_NO_FILL,
                "closed_at": now_iso,
                "outcome_detail": _fill_rejection_detail(fill_check),
            })
            updates.update(_no_fill_cleanup_updates())
            return updates, False
        fill_at = now_iso
        updates["entry_filled_at"] = fill_at
        updates["entry_fill_price"] = round(fill_price, 8)
        updates["fill_evidence_mode"] = "post_alert_interval"

    if not interval_complete:
        # A live point/running interval may invalidate an unfilled setup above,
        # but it never settles an already filled trade or advances its cursor.
        if expired:
            updates.update(
                _untracked_state_updates(
                    sig,
                    now_dt,
                    unfilled_detail=(
                        "observation_window_ended_without_interval_path"
                    ),
                )
            )
        return updates, False

    actual_geometry = trade_geometry(fill_price, stop, tp1, tp2, direction)
    risk = actual_geometry.get("risk")
    if not actual_geometry.get("valid") or risk is None:
        updates.update({
            "status": STATUS_NO_FILL,
            "closed_at": now_iso,
            "outcome_detail": "fill_invalidated_trade_geometry",
        })
        updates.update(_no_fill_cleanup_updates())
        return updates, False

    r_now = _signed_r(price, fill_price, risk, direction)
    favorable_price = interval_high if direction == "LONG" else interval_low
    adverse_price = interval_low if direction == "LONG" else interval_high
    interval_favorable_r = _signed_r(favorable_price, fill_price, risk, direction)
    interval_adverse_r = _signed_r(adverse_price, fill_price, risk, direction)
    max_fav = max(float(sig.get("max_favorable_r") or 0.0), interval_favorable_r)
    max_adv = min(float(sig.get("max_adverse_r") or 0.0), interval_adverse_r)
    tp1_hit_at = sig.get("tp1_hit_at") or None
    updates.update({
        "max_favorable_r": round(max_fav, 4),
        "max_adverse_r": round(max_adv, 4),
    })

    interval_started_at = _parse_utc_datetime(observation.get("started_at"))
    be_mail_sent_at = _parse_utc_datetime(sig.get("be_mail_sent_at"))
    be_trigger_at = _parse_utc_datetime(sig.get("be_trigger_at"))
    shadow_counterfactual_be = (
        str(sig.get("mail_class") or "").strip().lower() == "shadow"
    )
    be_effective_at = max(
        value for value in (be_mail_sent_at, be_trigger_at) if value is not None
    ) if (be_mail_sent_at is not None or be_trigger_at is not None) else None
    if (
        (sig.get("be_mail_sent_at") or shadow_counterfactual_be)
        and be_effective_at is not None
        and interval_started_at is not None
        and interval_started_at >= be_effective_at
        and not sig.get("be_exit_at")
    ):
        be_touched = interval_low <= fill_price if direction == "LONG" else interval_high >= fill_price
        if be_touched:
            if direction == "LONG" and interval_open is not None and interval_open < fill_price:
                be_exit_fill = interval_open
            elif direction == "SHORT" and interval_open is not None and interval_open > fill_price:
                be_exit_fill = interval_open
            else:
                be_exit_fill = fill_price
            updates.update({
                "be_exit_fill_price": round(be_exit_fill, 8),
                "be_exit_at": now_iso,
                "be_exit_evidence_mode": (
                    "shadow_counterfactual_completed_interval_open_or_entry_level"
                    if shadow_counterfactual_be
                    else "completed_interval_open_or_entry_level"
                ),
            })

    if (
        not sig.get("be_activated_at")
        and max_fav >= 1.0
    ):
        updates["be_trigger_at"] = now_iso

    if stop_touched:
        stop_fill_price = stop
        if observation_source == "point":
            stop_fill_price = price
        elif interval_open is not None:
            if direction == "LONG" and interval_open < stop:
                stop_fill_price = interval_open
            elif direction == "SHORT" and interval_open > stop:
                stop_fill_price = interval_open
        stop_slipped = abs(stop_fill_price - stop) > max(abs(stop) * 1e-9, 1e-12)
        # An adverse open through the stop proves that the position was already
        # executable at the gap price before any later target touch.  Only a
        # normal in-range stop/target collision has an unknown intrabar order.
        ambiguous_stop_and_target = (
            interval_complete
            and not stop_slipped
            and (tp1_touched or tp2_touched)
        )
        if ambiguous_stop_and_target:
            outcome_detail = "ambiguous_same_interval_stop_first"
        elif stop_slipped:
            outcome_detail = "stop_gap_slippage"
        else:
            outcome_detail = ""
        lower_r = round(_signed_r(stop_fill_price, fill_price, risk, direction), 4)
        upper_r = lower_r
        if ambiguous_stop_and_target:
            upper_target = tp2 if tp2_touched else tp1
            if upper_target is not None:
                upper_r = round(_signed_r(upper_target, fill_price, risk, direction), 4)
        updates.update({
            "status": STATUS_STOP,
            "stop_hit_at": now_iso,
            "closed_at": now_iso,
            "r_realized": lower_r,
            "r_realized_upper": max(lower_r, upper_r),
            "outcome_detail": outcome_detail,
        })
        updates.update(
            _stop_exit_metrics(stop_fill_price, stop, fill_price, direction)
        )
    elif tp2_touched:
        if tp1 is not None and not tp1_hit_at:
            tp1_hit_at = now_iso  # TP2 impliziert TP1
        tp2_r = round(_signed_r(tp2, fill_price, risk, direction), 4)
        updates.update({
            "status": STATUS_TP2,
            "tp2_hit_at": now_iso,
            "closed_at": now_iso,
            "r_realized": tp2_r,
            "r_realized_upper": tp2_r,
            "outcome_detail": "",
        })
    else:
        if tp1_touched and not tp1_hit_at:
            tp1_hit_at = now_iso
        if expired:
            expiry_r = round(r_now, 4)
            updates.update({
                "status": STATUS_EXPIRED,
                "closed_at": now_iso,
                "r_realized": expiry_r,
                "r_realized_upper": expiry_r,
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
      - kausal beobachteter BE-Exit                -> dessen echtes Gap-/Level-R
      - r_realized >= 0 ohne BE-Exit               -> r_realized unveraendert
      - outcome_detail 'ambiguous_same_day'        -> r_realized (Intraday-
        Reihenfolge MFE/Stop unbewiesen — kein BE-Kredit)
      - BE aktiviert, Verlust, aber Exit unbewiesen -> None (kein 0R erfinden)
    """
    realized = _to_float(row.get("r_realized"))
    if realized is None:
        return None
    if not row.get("be_activated_at"):
        return realized
    be_exit = _to_float(row.get("be_exit_fill_price"))
    if be_exit is not None and row.get("be_exit_at"):
        fill = _to_float(row.get("entry_fill_price"))
        if fill is None:
            fill = _to_float(row.get("entry"))
        stop = _to_float(row.get("stop"))
        if fill is not None and stop is not None:
            risk = abs(fill - stop)
            if risk > 0:
                direction = "SHORT" if str(row.get("direction")) == "SHORT" else "LONG"
                return round(_signed_r(be_exit, fill, risk, direction), 4)
    if _is_ambiguous_outcome(row):
        return realized
    if realized >= 0:
        return realized
    return None


def _transition_record(
    sig: Dict[str, Any],
    new_status: str,
    updates: Dict[str, Any],
    tp1_hit_this_run: bool,
) -> Dict[str, Any]:
    """Transitions-Dict fuer result['transitions'] bauen (Kontrakt s. Docstring
    von evaluate_open_signals). Plan-Level stammen aus der DB-Row, r_realized
    aus den Updates dieses Laufs (None bei TP1_HIT_OPEN/UNTRACKED)."""
    planned_entry = _to_float(sig.get("entry"))
    fill_price = _to_float(updates.get("entry_fill_price", sig.get("entry_fill_price")))
    stop = _to_float(sig.get("stop"))
    tp1 = _to_float(sig.get("tp1"))
    tp2 = _to_float(sig.get("tp2"))
    direction = "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
    fill_check: Dict[str, Any] = {}
    if None not in (planned_entry, fill_price, stop, tp1, tp2):
        fill_check = _fill_quality(
            str(sig.get("scanner") or ""),
            float(planned_entry),
            float(fill_price),
            float(stop),
            float(tp1),
            float(tp2),
            direction,
        )
    slippage_pct = None
    if planned_entry not in (None, 0) and fill_price is not None:
        if direction == "LONG":
            slippage_pct = ((fill_price - planned_entry) / planned_entry) * 100.0
        else:
            slippage_pct = ((planned_entry - fill_price) / planned_entry) * 100.0

    return {
        "id": int(sig["id"]),
        "ticker": sig.get("ticker"),
        "scanner": sig.get("scanner"),
        "strategy": sig.get("strategy"),
        "trade_horizon": sig.get("trade_horizon"),
        "evaluation_horizon_bars": sig.get("evaluation_horizon_bars"),
        "setup_key": sig.get("setup_key"),
        "mail_class": str(sig.get("mail_class") or "trade"),
        "channel": str(sig.get("channel") or "email"),
        "mail_channel": sig.get("mail_channel"),
        "delivery_recipient_keys_json": sig.get("delivery_recipient_keys_json"),
        "direction": direction,
        "old_status": str(sig.get("status") or STATUS_OPEN),
        "new_status": new_status,
        "entry": planned_entry,
        "entry_fill_price": fill_price,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "fill_quality": "OK" if fill_check.get("valid") else (
            "REJECTED" if fill_check else "UNAVAILABLE"
        ),
        "fill_rejection_reason": fill_check.get("reason"),
        "adverse_slippage_r": _to_float(fill_check.get("adverse_slippage_r")),
        "adverse_slippage_pct": round(slippage_pct, 4) if slippage_pct is not None else None,
        "live_rr_tp1": _to_float(fill_check.get("rr_tp1")),
        "live_effective_rr": _to_float(fill_check.get("effective_rr")),
        "exit_fill_price": _to_float(
            updates.get("exit_fill_price", sig.get("exit_fill_price"))
        ),
        "stop_gap_slippage_r": _to_float(
            updates.get("stop_gap_slippage_r", sig.get("stop_gap_slippage_r"))
        ),
        "stop_gap_slippage_pct": _to_float(
            updates.get("stop_gap_slippage_pct", sig.get("stop_gap_slippage_pct"))
        ),
        "r_realized": _to_float(updates.get("r_realized")),
        "r_realized_upper": _to_float(updates.get("r_realized_upper")),
        "outcome_detail": str(updates.get("outcome_detail") or ""),
        "tp1_hit_this_run": bool(tp1_hit_this_run),
        "asset_class": str(sig.get("asset_class") or "stock"),
        "code_revision": str(sig.get("code_revision") or "legacy_unknown"),
        "fill_evidence_mode": str(
            updates.get("fill_evidence_mode", sig.get("fill_evidence_mode"))
            or "legacy_unclassified"
        ),
    }


def evaluate_open_signals(
    stock_daily_fetcher: Optional[Callable[[str, str], Any]] = None,
    crypto_price_fetcher: Optional[Callable[..., Any]] = None,
    now: Optional[datetime] = None,
    *,
    stock_intraday_fetcher: Optional[Callable[..., Any]] = None,
) -> dict:
    """Bewertet alle OPEN-Signale gegen Stop/TP1/TP2. Wirft nie.

    Args:
        stock_daily_fetcher: Callable (ticker, since_iso_date) -> Liste von
            Daily-Bars [{'date', 'open', 'high', 'low', 'close'}, ...] oder None.
            Wird vom Aufrufer injiziert (z.B. Polygon-Fetcher); since_iso_date
            ist das Alert-Datum (YYYY-MM-DD). Rueckgabe None/[] zaehlt als
            Fehlversuch: eval_fail_count + 1, nach 5 Fehlversuchen wird das
            Signal auf status='UNTRACKED' gestellt.
        crypto_price_fetcher: Callable (ticker, identity/since/until) ->
            Exchange-Intervall mit current/high/low oder Legacy-Punktpreis.
            Gleiche Fehlversuch-Logik.
        stock_intraday_fetcher: Optionaler Intervall-Fetcher fuer Aktien am
            Versandtag. Vollstaendige 5m-High/Low-Spannen verhindern, dass
            Same-Day-Entry, Stop oder Ziele erst am Folgetag sichtbar werden.
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
                       'new_status', 'entry', 'entry_fill_price', 'stop',
                       'tp1', 'tp2', 'fill_quality', 'adverse_slippage_r',
                       'adverse_slippage_pct', 'live_rr_tp1',
                       'live_effective_rr', 'r_realized',
                       'tp1_hit_this_run', 'asset_class'}
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

    Aktien werden am Versandtag ueber vollstaendige Intraday-Intervalle und
    danach ueber Daily-OHLC der Folgetage bewertet. Crypto nutzt bei einem interval-faehigen
    Fetcher Exchange-5m-High/Low seit dem letzten Lauf; ein Legacy-Punktpreis
    bleibt ausdruecklich unvollstaendig. Crypto-Expiry: 120h nach created_at;
    Aktien-Expiry: bei Versand eingefrorener, strategieabhaengiger Horizont.
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
            created_dt = _signal_causal_start(sig)
            same_market_day = bool(
                created_dt
                and created_dt.astimezone(ZoneInfo("America/New_York")).date()
                == now_dt.astimezone(ZoneInfo("America/New_York")).date()
            )
            use_stock_intraday = bool(
                not is_crypto and same_market_day and stock_intraday_fetcher is not None
            )
            market_tz = ZoneInfo("America/New_York")
            needs_alert_day_backfill = False
            alert_day_close_utc: Optional[datetime] = None
            alert_day_backfill_since_utc: Optional[datetime] = None
            if (
                not is_crypto
                and not same_market_day
                and created_dt is not None
                and stock_intraday_fetcher is not None
            ):
                created_et = created_dt.astimezone(market_tz)
                close_et = _us_equity_session_close(created_et.date())
                last_eval_dt = _parse_utc_datetime(sig.get("last_eval_at"))
                if close_et is not None and created_et < close_et and (
                    last_eval_dt is None
                    or last_eval_dt < close_et.astimezone(timezone.utc)
                ):
                    needs_alert_day_backfill = True
                    alert_day_close_utc = close_et.astimezone(timezone.utc)
                    alert_day_backfill_since_utc = max(
                        created_dt,
                        last_eval_dt or created_dt,
                    )
            if is_crypto:
                fetcher = crypto_price_fetcher
            elif use_stock_intraday or needs_alert_day_backfill:
                fetcher = stock_intraday_fetcher
            else:
                fetcher = stock_daily_fetcher
            if fetcher is None:
                continue
            result["evaluated"] += 1
            try:
                if is_crypto:
                    updates, fetch_failed = _evaluate_crypto_signal(sig, fetcher, now_dt)
                elif needs_alert_day_backfill:
                    backfill_until = alert_day_close_utc or now_dt
                    backfill_since = alert_day_backfill_since_utc or created_dt
                    backfill_sig = dict(sig)
                    if backfill_since is not None:
                        backfill_sig["last_eval_at"] = backfill_since.isoformat()
                    raw_backfill = _normalize_crypto_observation(
                        _fetch_crypto_price(fetcher, backfill_sig, backfill_until)
                    )
                    initial_boundary_unobservable = bool(
                        backfill_since is not None
                        and _interval_initial_boundary_mismatch(
                            raw_backfill, backfill_since
                        )
                    )
                    coverage_complete = bool(
                        backfill_since is not None
                        and _has_complete_interval_coverage(
                            raw_backfill, backfill_since, backfill_until
                        )
                    )
                    if coverage_complete:
                        updates, fetch_failed = _evaluate_crypto_signal(
                            backfill_sig,
                            lambda *_args, **_kwargs: raw_backfill,
                            backfill_until,
                            expiry_hours=None,
                            register_failures=False,
                        )
                    else:
                        updates, fetch_failed = {}, True
                    # A partial/missing first interval is not a completed
                    # alert-day path. It must neither advance the cursor nor
                    # allow next-day Daily OHLC to fabricate a clean history.
                    backfill_complete = bool(
                        coverage_complete and (
                            updates.get("last_eval_at")
                            or updates.get("status") == STATUS_UNTRACKED
                        )
                    )
                    if not backfill_complete:
                        if initial_boundary_unobservable:
                            updates = _untracked_state_updates(
                                sig,
                                now_dt,
                                unfilled_detail=(
                                    "alert_day_initial_interval_unobservable"
                                ),
                                after_fill_detail=(
                                    "alert_day_initial_interval_unobservable_after_confirmed_fill"
                                ),
                            )
                        else:
                            # Missing/partial middle or tail coverage may be a
                            # transient provider failure.  Keep the last causal
                            # cursor unchanged and retry the identical range.
                            updates = {}
                        fetch_failed = True
                    backfilled = dict(sig)
                    backfilled.update(updates)
                    if (
                        not fetch_failed
                        and backfilled.get("status", STATUS_OPEN) == STATUS_OPEN
                        and stock_daily_fetcher is not None
                    ):
                        daily_updates, daily_failed = _evaluate_stock_signal(
                            backfilled, stock_daily_fetcher, now_dt
                        )
                        updates.update(daily_updates)
                        fetch_failed = daily_failed
                elif use_stock_intraday:
                    updates, fetch_failed = _evaluate_crypto_signal(
                        sig,
                        fetcher,
                        now_dt,
                        expiry_hours=None,
                        register_failures=False,
                    )
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
            be_trigger_at = updates.get("be_trigger_at")
            effective_fill_at = updates.get("entry_filled_at") or sig.get("entry_filled_at")
            effective_fill_price = _to_float(
                updates.get("entry_fill_price", sig.get("entry_fill_price"))
            )
            be_new = (
                not sig.get("be_activated_at")
                and bool(effective_fill_at)
                and effective_fill_price is not None
                and mfe_now is not None
                and mfe_now >= 1.0
            )
            r_now = _to_float(updates.get("r_realized"))
            if be_new and r_now is not None:
                # Aktivierung und terminaler Exit im selben Bewertungsfenster:
                # Reihenfolge bzw. Reaktionszeit unbewiesen, daher weder eine
                # BE-Aktivierungsmail noch einen simulierten Exit gutschreiben.
                be_new = False
            if be_new:
                updates["be_activated_at"] = str(be_trigger_at or now_dt.isoformat())
            elif be_trigger_at and not sig.get("be_activated_at"):
                updates.pop("be_trigger_at", None)
            if r_now is not None:
                be_row = dict(sig)
                be_row.update(updates)
                updates["r_realized_be"] = breakeven_adjusted_r(be_row)
            new_status = updates.get("status")
            if new_status and new_status != STATUS_OPEN:
                result["closed"] += 1
            if (
                new_status in _MAILABLE_TERMINAL_STATUSES
                and str(sig.get("mail_class") or "trade").lower() == "trade"
            ):
                # One transaction persists both trading outcome and durable
                # delivery intent. A crash after this commit cannot lose the
                # terminal follow-up event; the pending loader reconstructs it.
                updates["pending_update_status"] = str(new_status)
                updates["pending_update_at"] = now_dt.isoformat()
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
                            "strategy": sig.get("strategy"),
                            "trade_horizon": sig.get("trade_horizon"),
                            "setup_key": sig.get("setup_key"),
                            "mail_class": str(sig.get("mail_class") or "trade"),
                            "channel": str(sig.get("channel") or "email"),
                            "mail_channel": sig.get("mail_channel"),
                            "delivery_recipient_keys_json": sig.get(
                                "delivery_recipient_keys_json"
                            ),
                            "direction": "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG",
                            "entry": _to_float(sig.get("entry")),
                            "entry_fill_price": effective_fill_price,
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
def load_pending_be_activations(
    max_age_hours: int = 168,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Load undelivered +1R stop updates from the tracker database.

    ``be_activated_at`` records the trading event. ``be_mail_sent_at`` is
    written only after the dedicated stop-update email was delivered. This
    keeps delivery retryable across SMTP failures and process restarts.
    Closed or stale signals are intentionally not sent as late instructions.
    """
    try:
        now_dt = _coerce_now(now)
        cutoff = now_dt - timedelta(hours=max(1, int(max_age_hours)))
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        """
                        SELECT id, ticker, scanner, strategy, trade_horizon,
                               setup_key, mail_class, channel, mail_channel, direction,
                               entry, entry_fill_price, stop, tp1, tp2,
                               max_favorable_r, asset_class, be_activated_at,
                               delivery_recipient_keys_json
                        FROM signals
                        WHERE status = ?
                          AND mail_class = 'trade'
                          AND be_activated_at IS NOT NULL
                          AND be_mail_sent_at IS NULL
                        ORDER BY be_activated_at, id
                        """,
                        (STATUS_OPEN,),
                    ).fetchall()
                ]

        pending: List[Dict[str, Any]] = []
        for sig in rows:
            activated_at = _parse_utc_datetime(sig.get("be_activated_at"))
            if activated_at is None or activated_at < cutoff:
                continue
            pending.append({
                "id": int(sig["id"]),
                "ticker": sig.get("ticker"),
                "scanner": sig.get("scanner"),
                "strategy": sig.get("strategy"),
                "trade_horizon": sig.get("trade_horizon"),
                "setup_key": sig.get("setup_key"),
                "mail_class": str(sig.get("mail_class") or "trade"),
                "channel": str(sig.get("channel") or "email"),
                "mail_channel": sig.get("mail_channel"),
                "delivery_recipient_keys_json": sig.get(
                    "delivery_recipient_keys_json"
                ),
                "direction": (
                    "SHORT" if str(sig.get("direction")) == "SHORT" else "LONG"
                ),
                "entry": _to_float(sig.get("entry")),
                "entry_fill_price": _to_float(sig.get("entry_fill_price")),
                "stop": _to_float(sig.get("stop")),
                "tp1": _to_float(sig.get("tp1")),
                "tp2": _to_float(sig.get("tp2")),
                "mfe": _to_float(sig.get("max_favorable_r")),
                "asset_class": str(sig.get("asset_class") or "stock"),
                "activated_at": sig.get("be_activated_at"),
                "tracker_persisted": True,
            })
        return pending
    except Exception as exc:
        logger.warning("Ausstehende Stop-Updates konnten nicht geladen werden: %s", exc)
        return []


def mark_be_alerts_sent(
    signal_ids: Iterable[Any],
    sent_at: Optional[datetime] = None,
) -> int:
    """Idempotently acknowledge delivered stop updates for signal IDs."""
    ids: List[int] = []
    for raw_id in signal_ids or []:
        try:
            signal_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if signal_id > 0 and signal_id not in ids:
            ids.append(signal_id)
    if not ids:
        return 0

    stamp = _coerce_now(sent_at).isoformat() if sent_at is not None else _utc_iso()
    placeholders = ",".join("?" for _ in ids)
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                cursor = conn.execute(
                    f"UPDATE signals SET be_mail_sent_at = ? "
                    f"WHERE id IN ({placeholders}) AND be_mail_sent_at IS NULL",
                    [stamp, *ids],
                )
                return int(cursor.rowcount or 0)
    except Exception as exc:
        logger.warning("Stop-Update-Zustellung konnte nicht gespeichert werden: %s", exc)
        return 0


def load_pending_terminal_updates() -> List[Dict[str, Any]]:
    """Rebuild committed terminal follow-up events not yet acknowledged.

    Outcome and ``pending_update_*`` are persisted by the same SQLite UPDATE.
    This outbox-style loader therefore closes the crash window between the
    trading-state commit and the caller handing the event to the mail layer.
    Rows with a contradictory pending/current status fail closed.
    """
    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM signals "
                        "WHERE mail_class = 'trade' "
                        "AND pending_update_status IS NOT NULL "
                        "ORDER BY pending_update_at, id"
                    ).fetchall()
                ]
        pending: List[Dict[str, Any]] = []
        for sig in rows:
            pending_status = str(sig.get("pending_update_status") or "")
            if (
                pending_status not in _MAILABLE_TERMINAL_STATUSES
                or str(sig.get("status") or "") != pending_status
            ):
                logger.warning(
                    "Signal %s: widerspruechlicher ausstehender Terminal-Status %r/%r",
                    sig.get("id"), pending_status, sig.get("status"),
                )
                continue
            prior = dict(sig)
            prior["status"] = STATUS_OPEN
            event = _transition_record(prior, pending_status, sig, False)
            event["pending_update_at"] = sig.get("pending_update_at")
            event["tracker_persisted"] = True
            pending.append(event)
        return pending
    except Exception as exc:
        logger.warning("Ausstehende Terminal-Updates konnten nicht geladen werden: %s", exc)
        return []


def mark_terminal_updates_sent(signal_ids: Iterable[Any]) -> int:
    """Idempotently acknowledge durable terminal updates by signal ID."""
    ids: List[int] = []
    for raw_id in signal_ids or []:
        try:
            signal_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if signal_id > 0 and signal_id not in ids:
            ids.append(signal_id)
    if not ids:
        return 0
    try:
        placeholders = ",".join("?" for _ in ids)
        with _DB_LOCK:
            with _db_connection() as conn:
                cursor = conn.execute(
                    "UPDATE signals "
                    "SET pending_update_status = NULL, pending_update_at = NULL "
                    f"WHERE id IN ({placeholders}) "
                    "AND pending_update_status IS NOT NULL",
                    ids,
                )
                return int(cursor.rowcount or 0)
    except Exception as exc:
        logger.warning("Terminal-Update-Zustellung konnte nicht gespeichert werden: %s", exc)
        return 0


_METRIC_KEYS = (
    "signals", "open", "tp1_hit", "tp2_hit", "stop_hit", "expired", "no_fill", "untracked"
)


def _empty_bucket() -> Dict[str, Any]:
    bucket: Dict[str, Any] = {key: 0 for key in _METRIC_KEYS}
    bucket.update(
        {
            "win_rate_pct": None,
            "win_rate_pct_upper": None,
            "avg_r": None,
            "sum_r": 0.0,
            "avg_r_upper": None,
            "sum_r_upper": 0.0,
            "ambiguous_outcomes": 0,
            "ambiguity_rate_pct": None,
            "alerts_per_day": 0.0,
            "managed_be_decided_signals": 0,
            "managed_be_wins": 0,
            "managed_be_losses": 0,
            "managed_be_breakevens": 0,
            "managed_be_unresolved": 0,
            "managed_be_win_rate_pct": None,
            "managed_be_win_rate_pct_upper": None,
            "managed_be_win_rate_ex_breakeven_pct": None,
            "managed_be_breakeven_outcome_rate_pct": None,
            "avg_r_managed_50_50_be": None,
            "sum_r_managed_50_50_be": 0.0,
            "avg_r_managed_50_50_be_upper": None,
            "sum_r_managed_50_50_be_upper": 0.0,
            "breakeven_win_rate_managed_be_pct": None,
            "breakeven_win_rate_ex_breakeven_managed_be_pct": None,
            "stop_gap_exits": 0,
            "sum_stop_gap_slippage_r": 0.0,
            "avg_stop_gap_slippage_r": None,
        }
    )
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
    managed_be_values: Optional[List[float]] = None,
    r_upper_values: Optional[List[float]] = None,
    managed_be_upper_values: Optional[List[float]] = None,
    ambiguous_outcomes: int = 0,
    managed_be_unresolved: int = 0,
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
    upper = [value for value in (r_upper_values or []) if value is not None]
    upper_wins = sum(1 for value in upper if value > 0)
    bucket["win_rate_pct_upper"] = (
        round(100.0 * upper_wins / len(upper), 1)
        if upper
        else bucket["win_rate_pct"]
    )
    bucket["avg_r_upper"] = round(sum(upper) / len(upper), 3) if upper else bucket["avg_r"]
    bucket["sum_r_upper"] = round(sum(upper), 3) if upper else bucket["sum_r"]
    bucket["ambiguous_outcomes"] = int(ambiguous_outcomes)
    bucket["ambiguity_rate_pct"] = (
        round(100.0 * ambiguous_outcomes / decided, 1) if decided else None
    )
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
    recommended = _recommended_payoff_statistics(managed_be_values or [])
    bucket["managed_be_decided_signals"] = recommended["decided"]
    bucket["managed_be_wins"] = recommended["wins"]
    bucket["managed_be_losses"] = recommended["losses"]
    bucket["managed_be_breakevens"] = recommended["breakevens"]
    bucket["managed_be_win_rate_pct"] = recommended["win_rate_pct"]
    bucket["managed_be_win_rate_ex_breakeven_pct"] = recommended[
        "win_rate_ex_breakeven_pct"
    ]
    bucket["managed_be_breakeven_outcome_rate_pct"] = recommended[
        "breakeven_outcome_rate_pct"
    ]
    bucket["managed_be_win_rate_wilson_95"] = recommended["win_rate_wilson_95"]
    bucket["managed_be_unresolved"] = int(managed_be_unresolved)
    bucket["managed_be_sample_reliable"] = bool(
        recommended["decided"] >= 30 and managed_be_unresolved == 0
    )
    bucket["avg_r_managed_50_50_be"] = recommended["avg_r"]
    bucket["sum_r_managed_50_50_be"] = recommended["sum_r"]
    recommended_upper = _recommended_payoff_statistics(managed_be_upper_values or [])
    bucket["managed_be_win_rate_pct_upper"] = (
        recommended_upper["win_rate_pct"]
        if recommended_upper["decided"]
        else recommended["win_rate_pct"]
    )
    bucket["avg_r_managed_50_50_be_upper"] = (
        recommended_upper["avg_r"]
        if recommended_upper["decided"]
        else recommended["avg_r"]
    )
    bucket["sum_r_managed_50_50_be_upper"] = (
        recommended_upper["sum_r"]
        if recommended_upper["decided"]
        else recommended["sum_r"]
    )
    bucket["avg_win_r_managed_be"] = recommended["avg_win_r"]
    bucket["avg_loss_r_managed_be"] = recommended["avg_loss_r"]
    bucket["profit_factor_managed_be"] = recommended["profit_factor"]
    bucket["breakeven_win_rate_managed_be_pct"] = recommended["breakeven_win_rate_pct"]
    bucket["breakeven_win_rate_ex_breakeven_managed_be_pct"] = recommended[
        "breakeven_win_rate_ex_breakeven_pct"
    ]
    bucket["alerts_per_day"] = round(bucket["signals"] / float(window_days), 3)
    bucket["cohort_events_per_day"] = bucket["alerts_per_day"]


def _add_stop_gap_metrics(bucket: Dict[str, Any], rows: Iterable[Dict[str, Any]]) -> None:
    """Add measured adverse stop-gap execution costs to a metric bucket."""
    values: List[float] = []
    for row in rows:
        if row.get("status") != STATUS_STOP:
            continue
        value = _to_float(row.get("stop_gap_slippage_r"))
        if value is not None and value > 0.0:
            values.append(value)
    bucket["stop_gap_exits"] = len(values)
    bucket["sum_stop_gap_slippage_r"] = round(sum(values), 4) if values else 0.0
    bucket["avg_stop_gap_slippage_r"] = (
        round(sum(values) / len(values), 4) if values else None
    )


def _performance_bucket_for_rows(
    rows: Iterable[Dict[str, Any]],
    window_days: int,
) -> Dict[str, Any]:
    """Build one complete performance bucket for an additive dimension."""
    materialized = list(rows)
    bucket = _empty_bucket()
    r_values: List[float] = []
    r_upper_values: List[float] = []
    managed_values: List[float] = []
    managed_be_values: List[float] = []
    managed_be_upper_values: List[float] = []
    be_values: List[float] = []
    be_activations = 0
    be_saved = 0
    ambiguous = 0
    managed_be_unresolved = 0
    for row in materialized:
        bucket["signals"] += 1
        bucket[_classify_row(row)] += 1
        r_value = _to_float(row.get("r_realized"))
        is_decided_fill = (
            row.get("status") in {STATUS_STOP, STATUS_TP2, STATUS_EXPIRED}
            and bool(row.get("entry_filled_at"))
            and _to_float(row.get("entry_fill_price")) is not None
        )
        if r_value is None or not is_decided_fill:
            continue
        r_values.append(r_value)
        upper = _realized_upper(row)
        if upper is not None:
            r_upper_values.append(upper)
        if _is_ambiguous_outcome(row):
            ambiguous += 1
        managed = _managed_r_50_50(row)
        if managed is not None:
            managed_values.append(managed)
        managed_be, is_managed_be_unresolved = _managed_5050_be_resolution(row)
        if managed_be is not None:
            managed_be_values.append(managed_be)
        if is_managed_be_unresolved:
            managed_be_unresolved += 1
        managed_be_upper = _managed_upper_r(row)
        if managed_be_upper is not None:
            managed_be_upper_values.append(managed_be_upper)
        if row.get("be_activated_at"):
            be_activations += 1
        be_value = _to_float(row.get("r_realized_be"))
        if be_value is not None:
            be_values.append(be_value)
            if r_value < 0.0 and be_value >= 0.0:
                be_saved += 1
    _finalize_bucket(
        bucket,
        r_values,
        window_days,
        managed_values,
        be_values,
        be_activations,
        be_saved,
        managed_be_values,
        r_upper_values,
        managed_be_upper_values,
        ambiguous,
        managed_be_unresolved,
    )
    _add_stop_gap_metrics(bucket, materialized)
    return bucket


def _grouped_performance(
    rows: Iterable[Dict[str, Any]],
    window_days: int,
    key_fn: Callable[[Dict[str, Any]], str],
) -> Dict[str, Dict[str, Any]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = str(key_fn(row) or "unknown")
        grouped.setdefault(key, []).append(row)
    return {
        key: _performance_bucket_for_rows(group_rows, window_days)
        for key, group_rows in sorted(grouped.items())
    }


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
    unresolved = bucket.get("managed_be_unresolved") or 0
    if isinstance(unresolved, int) and unresolved > 0:
        return "beobachten", f"Managed-BE-Evidenz unvollstaendig: {unresolved}"
    decided = bucket.get("managed_be_decided_signals")
    if not isinstance(decided, int):
        decided = bucket.get("decided_signals") or 0
    if decided < 30:
        return "beobachten", f"Stichprobe {decided} < 30"
    avg_r = bucket.get("avg_r_managed_50_50_be")
    win = bucket.get("managed_be_win_rate_pct")
    if not isinstance(avg_r, (int, float)):
        avg_r = bucket.get("avg_r")
    if not isinstance(win, (int, float)):
        win = bucket.get("win_rate_pct")
    if not isinstance(avg_r, (int, float)) or not isinstance(win, (int, float)):
        return "beobachten", "keine verwertbaren R-Daten"
    if avg_r <= -1.0:
        return "abschalten", "Ø R <= -1R, strukturell defizitär"
    be = bucket.get("breakeven_win_rate_managed_be_pct")
    if not isinstance(be, (int, float)):
        be = breakeven_win_rate_pct(win, avg_r)
    wilson = (
        bucket.get("managed_be_win_rate_wilson_95")
        or bucket.get("win_rate_wilson_95")
        or {}
    )
    lo, hi = wilson.get("lower_pct"), wilson.get("upper_pct")
    if (avg_r > 0 and isinstance(lo, (int, float))
            and isinstance(be, (int, float)) and lo > be):
        return "behalten", f"KI {lo:.0f}% > Breakeven {be:.0f}%"
    if (avg_r < 0 and isinstance(hi, (int, float))
            and isinstance(be, (int, float)) and hi < be):
        return "abschalten", f"KI {hi:.0f}% < Breakeven {be:.0f}%"
    return "beobachten", "Erwartungswert nicht signifikant"


def _performance_direction_key(row: Dict[str, Any]) -> str:
    return "SHORT" if str(row.get("direction") or "").upper() == "SHORT" else "LONG"


def _performance_horizon_key(row: Dict[str, Any]) -> str:
    horizon = str(row.get("trade_horizon") or "unspecified").strip().lower()
    bars = _to_float(row.get("evaluation_horizon_bars"))
    return f"{horizon}:{int(round(bars))}bars" if bars is not None else horizon


def _performance_regime_key(row: Dict[str, Any]) -> str:
    return str(row.get("market_regime") or "legacy_unknown").strip().upper() or "LEGACY_UNKNOWN"


def _calibration_cell_key(row: Dict[str, Any]) -> Tuple[str, str, str, str]:
    return (
        str(row.get("scanner") or "unknown"),
        _performance_direction_key(row),
        _performance_horizon_key(row),
        _performance_regime_key(row),
    )


def build_calibration_cell_identity(
    scanner_name: str,
    row: Mapping[str, Any],
    market_regime: Any = None,
) -> Optional[Dict[str, str]]:
    """Build the exact joint-cell identity used by performance and breaker.

    The input may be a raw scanner row (including the aliases accepted by
    :func:`extract_signal_fields`). Stock horizon inference and the ``UNKNOWN``
    regime fallback deliberately mirror persistence, after which the same
    direction/horizon/regime key functions as ``calibration_cells`` are used.
    ``None`` is returned for malformed input so callers can fail closed.
    """
    try:
        scanner = str(scanner_name or "").strip().lower()
        if not scanner or not isinstance(row, Mapping):
            return None
        asset_class = "crypto" if scanner in CRYPTO_SCANNERS else "stock"
        fields = _prepare_identity_fields(
            extract_signal_fields(row), scanner, asset_class
        )
        fields["scanner"] = scanner
        fields["market_regime"] = (
            market_regime
            if market_regime is not None and str(market_regime).strip()
            else fields.get("market_regime") or "UNKNOWN"
        )
        key = _calibration_cell_key(fields)
        return {
            "cell_id": "|".join(key),
            "scanner": key[0],
            "direction": key[1],
            "horizon": key[2],
            "market_regime": key[3],
        }
    except Exception as exc:
        logger.warning("Kalibrierzellen-Identitaet konnte nicht gebaut werden: %s", exc)
        return None


def load_performance_summary(
    days: int = 90,
    mature_only: bool = False,
    as_of: Any = None,
) -> dict:
    """Track-Record-Zusammenfassung ueber die letzten `days` Tage. Wirft nie.

    Mit `mature_only=True` werden nur Signale ausgewertet, deren komplettes
    Beobachtungsfenster am `as_of`-Zeitpunkt abgelaufen ist. Das verhindert,
    dass schnelle Stops gegen noch offene potenzielle Gewinner als fertige
    Trefferquote erscheinen.

    Die `managed_be_*`-Felder bilden das empfohlene Modell ab: 50 Prozent am
    TP1, Rest bis TP2/Stop/Expiry und Stop auf Einstand ab +1R. Die gemeldete
    Trefferquote behaelt 0R-Einstandsausgaenge im Nenner; die zusaetzliche
    `managed_be_win_rate_ex_breakeven_pct` betrachtet nur Gewinne/Verluste.
    Die Break-even-Schwellen verwenden jeweils denselben Nenner wie die
    zugehoerige Trefferquote.

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
      win_rate_pct — Anteil aller geschlossenen R-Ergebnisse > 0;
                     0R bleibt im Nenner, None ohne R-Ergebnisse
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
    if isinstance(as_of, datetime):
        as_of_dt = _coerce_now(as_of)
    elif as_of is not None:
        as_of_dt = _parse_utc_datetime(as_of) or _utc_now()
    else:
        as_of_dt = _utc_now()
    summary: Dict[str, Any] = {
        "generated_at": as_of_dt.isoformat(),
        "window_days": window,
        "cohort_mode": "fully_observed" if mature_only else "created_in_window",
        "cohort_selection_basis": "matured_in_window" if mature_only else "created_in_window",
        "as_of": as_of_dt.isoformat(),
        "excluded_not_mature": 0,
        "total": _empty_bucket(),
        "per_scanner": {},
        "per_strategy": {},
        "per_direction": {},
        "per_horizon": {},
        "per_market_regime": {},
        "per_code_revision": {},
        "per_fill_evidence_mode": {},
        "segments": [],
        "calibration_cells": [],
        "calibration_cell_dimensions": [
            "scanner", "direction", "horizon", "market_regime",
        ],
        "segment_dimensions": [
            "scanner", "strategy", "direction", "horizon", "market_regime",
            "code_revision", "fill_evidence_mode",
        ],
        "cohort": {
            "mode": "fully_observed" if mature_only else "created_in_window",
            "selection_basis": "matured_in_window" if mature_only else "created_in_window",
            "mature_only": bool(mature_only),
            "created_in_window": 0,
            "matured_in_window": 0,
            "included_signals": 0,
            "excluded_not_mature": 0,
        },
        "recent": [],
    }
    try:
        cutoff_dt = as_of_dt - timedelta(days=window)
        cutoff_iso = cutoff_dt.isoformat()
        query_cutoff = (
            cutoff_dt - timedelta(days=_MAX_PERFORMANCE_MATURITY_LOOKBACK_DAYS)
            if mature_only
            else cutoff_dt
        )
        with _DB_LOCK:
            with _db_connection() as conn:
                candidate_rows = [
                    dict(row)
                    for row in conn.execute(
                        "SELECT * FROM signals "
                        "WHERE COALESCE(delivery_accepted_at, created_at) >= ? "
                        "AND COALESCE(delivery_accepted_at, created_at) <= ? "
                        "AND mail_class = 'trade' AND status != ? "
                        "ORDER BY COALESCE(delivery_accepted_at, created_at) DESC, id DESC",
                        (
                            query_cutoff.isoformat(), as_of_dt.isoformat(),
                            STATUS_PENDING_DELIVERY,
                        ),
                    ).fetchall()
                ]
        created_rows = [
            row for row in candidate_rows
            if (_signal_causal_start(row) or datetime.min.replace(tzinfo=timezone.utc))
            >= cutoff_dt
        ]
        created_in_window = len(created_rows)
        if mature_only:
            rows = []
            for row in candidate_rows:
                maturity_at = _signal_maturity_at(row)
                if maturity_at is not None and cutoff_dt <= maturity_at <= as_of_dt:
                    row = dict(row)
                    row["maturity_at"] = maturity_at.isoformat()
                    rows.append(row)
            summary["excluded_not_mature"] = sum(
                1
                for row in created_rows
                if (_signal_maturity_at(row) is None or _signal_maturity_at(row) > as_of_dt)
            )
        else:
            rows = created_rows
        summary["cohort"].update({
            "created_in_window": created_in_window,
            "matured_in_window": len(rows) if mature_only else sum(
                1 for row in created_rows if _signal_has_full_observation_window(row, as_of_dt)
            ),
            "included_signals": len(rows),
            "excluded_not_mature": summary["excluded_not_mature"],
        })
        total_r: List[float] = []
        total_r_upper: List[float] = []
        total_managed: List[float] = []
        total_managed_be: List[float] = []
        total_managed_be_upper: List[float] = []
        scanner_r: Dict[str, List[float]] = {}
        scanner_r_upper: Dict[str, List[float]] = {}
        scanner_managed: Dict[str, List[float]] = {}
        scanner_managed_be: Dict[str, List[float]] = {}
        scanner_managed_be_upper: Dict[str, List[float]] = {}
        managed_be_unresolved_total = 0
        managed_be_unresolved_scanner: Dict[str, int] = {}
        ambiguous_total = 0
        ambiguous_scanner: Dict[str, int] = {}
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
            r_value = _to_float(row.get("r_realized"))
            is_decided_fill = (
                row.get("status") in {STATUS_STOP, STATUS_TP2, STATUS_EXPIRED}
                and bool(row.get("entry_filled_at"))
                and _to_float(row.get("entry_fill_price")) is not None
            )
            if r_value is not None and is_decided_fill:
                total_r.append(r_value)
                scanner_r.setdefault(scanner, []).append(r_value)
                upper_value = _realized_upper(row)
                if upper_value is not None:
                    total_r_upper.append(upper_value)
                    scanner_r_upper.setdefault(scanner, []).append(upper_value)
                if _is_ambiguous_outcome(row):
                    ambiguous_total += 1
                    ambiguous_scanner[scanner] = ambiguous_scanner.get(scanner, 0) + 1
                managed_value = _managed_r_50_50(row)
                if managed_value is not None:
                    total_managed.append(managed_value)
                    scanner_managed.setdefault(scanner, []).append(managed_value)
                managed_be_value, is_managed_be_unresolved = _managed_5050_be_resolution(row)
                if managed_be_value is not None:
                    total_managed_be.append(managed_be_value)
                    scanner_managed_be.setdefault(scanner, []).append(managed_be_value)
                if is_managed_be_unresolved:
                    managed_be_unresolved_total += 1
                    managed_be_unresolved_scanner[scanner] = (
                        managed_be_unresolved_scanner.get(scanner, 0) + 1
                    )
                managed_be_upper = _managed_upper_r(row)
                if managed_be_upper is not None:
                    total_managed_be_upper.append(managed_be_upper)
                    scanner_managed_be_upper.setdefault(scanner, []).append(managed_be_upper)
                if row.get("be_activated_at"):
                    be_act_total += 1
                    be_act_scanner[scanner] = be_act_scanner.get(scanner, 0) + 1
                be_value = _to_float(row.get("r_realized_be"))
                if be_value is not None:
                    total_be.append(be_value)
                    scanner_be.setdefault(scanner, []).append(be_value)
                    # r < 0, aber BE-R >= 0: die Regel haette den Verlierer
                    # verhindert (impliziert zugleich eine BE-Aktivierung).
                    if r_value < 0.0 and be_value >= 0.0:
                        be_saved_total += 1
                        be_saved_scanner[scanner] = be_saved_scanner.get(scanner, 0) + 1
        _finalize_bucket(
            summary["total"],
            total_r,
            window,
            total_managed,
            total_be,
            be_act_total,
            be_saved_total,
            total_managed_be,
            total_r_upper,
            total_managed_be_upper,
            ambiguous_total,
            managed_be_unresolved_total,
        )
        summary["total"]["alerts_per_day"] = round(created_in_window / float(window), 3)
        created_scanner_counts: Dict[str, int] = {}
        for row in created_rows:
            key = str(row.get("scanner") or "unknown")
            created_scanner_counts[key] = created_scanner_counts.get(key, 0) + 1
        for scanner, bucket in summary["per_scanner"].items():
            _finalize_bucket(
                bucket, scanner_r.get(scanner, []), window, scanner_managed.get(scanner, []),
                scanner_be.get(scanner, []), be_act_scanner.get(scanner, 0),
                be_saved_scanner.get(scanner, 0),
                scanner_managed_be.get(scanner, []),
                scanner_r_upper.get(scanner, []),
                scanner_managed_be_upper.get(scanner, []),
                ambiguous_scanner.get(scanner, 0),
                managed_be_unresolved_scanner.get(scanner, 0),
            )
            bucket["alerts_per_day"] = round(
                created_scanner_counts.get(scanner, 0) / float(window), 3
            )
        _add_stop_gap_metrics(summary["total"], rows)
        for scanner, bucket in summary["per_scanner"].items():
            _add_stop_gap_metrics(
                bucket,
                (row for row in rows if str(row.get("scanner") or "unknown") == scanner),
            )

        strategy_key = lambda row: str(
            row.get("strategy") or row.get("scanner") or "unknown"
        )
        scanner_key = lambda row: str(row.get("scanner") or "unknown")
        direction_key = _performance_direction_key
        horizon_key = _performance_horizon_key
        regime_key = _performance_regime_key
        revision_key = lambda row: str(row.get("code_revision") or "legacy_unknown")
        evidence_key = lambda row: str(
            row.get("fill_evidence_mode") or "legacy_unclassified"
        )
        summary["per_strategy"] = _grouped_performance(rows, window, strategy_key)
        summary["per_direction"] = _grouped_performance(rows, window, direction_key)
        summary["per_horizon"] = _grouped_performance(rows, window, horizon_key)
        summary["per_market_regime"] = _grouped_performance(rows, window, regime_key)
        summary["per_code_revision"] = _grouped_performance(rows, window, revision_key)
        summary["per_fill_evidence_mode"] = _grouped_performance(
            rows, window, evidence_key
        )
        for grouped, key_fn in (
            (summary["per_strategy"], strategy_key),
            (summary["per_direction"], direction_key),
            (summary["per_horizon"], horizon_key),
            (summary["per_market_regime"], regime_key),
            (summary["per_code_revision"], revision_key),
            (summary["per_fill_evidence_mode"], evidence_key),
        ):
            activity: Dict[str, int] = {}
            for row in created_rows:
                key = key_fn(row)
                activity[key] = activity.get(key, 0) + 1
            for key, bucket in grouped.items():
                bucket["alerts_per_day"] = round(activity.get(key, 0) / float(window), 3)
        segment_rows: Dict[Tuple[str, str, str, str, str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            segment_key = (
                scanner_key(row), strategy_key(row), direction_key(row), horizon_key(row),
                regime_key(row), revision_key(row), evidence_key(row),
            )
            segment_rows.setdefault(segment_key, []).append(row)
        created_segment_counts: Dict[Tuple[str, str, str, str, str, str, str], int] = {}
        for row in created_rows:
            key = (
                scanner_key(row), strategy_key(row), direction_key(row), horizon_key(row),
                regime_key(row), revision_key(row), evidence_key(row),
            )
            created_segment_counts[key] = created_segment_counts.get(key, 0) + 1
        summary["segments"] = []
        for key, group_rows in sorted(segment_rows.items()):
            segment = {
                "scanner": key[0],
                "strategy": key[1],
                "direction": key[2],
                "horizon": key[3],
                "market_regime": key[4],
                "code_revision": key[5],
                "fill_evidence_mode": key[6],
                **_performance_bucket_for_rows(group_rows, window),
            }
            segment["alerts_per_day"] = round(
                created_segment_counts.get(key, 0) / float(window), 3
            )
            summary["segments"].append(segment)
        calibration_rows: Dict[
            Tuple[str, str, str, str], List[Dict[str, Any]]
        ] = {}
        for row in rows:
            cell_key = _calibration_cell_key(row)
            calibration_rows.setdefault(cell_key, []).append(row)
        created_calibration_counts: Dict[Tuple[str, str, str, str], int] = {}
        for row in created_rows:
            cell_key = _calibration_cell_key(row)
            created_calibration_counts[cell_key] = (
                created_calibration_counts.get(cell_key, 0) + 1
            )
        summary["calibration_cells"] = []
        for key, group_rows in sorted(calibration_rows.items()):
            bucket = _performance_bucket_for_rows(group_rows, window)
            verdict, verdict_reason = scanner_verdict(bucket)
            cell = {
                "cell_id": "|".join(key),
                "scanner": key[0],
                "direction": key[1],
                "horizon": key[2],
                "market_regime": key[3],
                "cohort_mode": summary["cohort_mode"],
                "verdict": verdict,
                "verdict_reason": verdict_reason,
                **bucket,
            }
            cell["alerts_per_day"] = round(
                created_calibration_counts.get(key, 0) / float(window), 3
            )
            summary["calibration_cells"].append(cell)
        summary["r_semantics"] = (
            "avg_r = Level-R (TP2 volles Geometrie-R, unmanaged); "
            "avg_r_managed_50_50 = R des empfohlenen 50/50-Managements "
            "(50% Teilverkauf am TP1, Rest Stop/TP2/Expiry). "
            "avg_r_be = live gemessenes R unter der Einstand-Regel "
            "(Stop auf Einstand ab MFE >= +1R; seit 30.07., kein Backtest); "
            "avg_r_managed_50_50_be = einheitliches Empfehlungsmodell: "
            "50% am TP1, Rest bis TP2/Stop/Expiry und Stop auf Einstand ab +1R. "
            "Die exakte Breakeven-Trefferquote stammt aus realisiertem "
            "Durchschnittsgewinn, Durchschnittsverlust und der beobachteten "
            "0R-Einstandsquote dieses Modells. "
            "mature_only=true schliesst rechtszensierte, noch nicht voll "
            "beobachtbare Versandkohorten aus. "
            "be_activations/be_saved = BE-Markierungen / verhinderte Verlierer. "
            "managed_be_unresolved zaehlt entschiedene Signale, bei denen die "
            "BE-Regel faellig war, aber Zustellung bzw. kausaler Exit nicht "
            "belegt ist; solche Zeilen sperren Managed-BE-Reliability und Verdikt. "
            "win_rate_wilson_95 = Wilson-Konfidenzintervall der Trefferquote; "
            "sample_reliable ab 30 entschiedenen Signalen. Aktien werden bis zum "
            "beim Versand eingefrorenen Strategie-Horizont beobachtet; ORB, Swing, "
            "BI/Biotech und Turtle verwenden deshalb nicht dasselbe Bar-Limit. "
            "Entry-Slippage und Stop-Gap-Slippage sind getrennte Kosten; "
            "stop_gap_* misst nur die zusaetzliche adverse Ausfuehrung hinter "
            "dem Plan-Stop. "
            "AUDIT 2026-07-24 (T1 + Kalibrier-Loop)."
        )
        summary["segmentation_semantics"] = (
            "Alle per_*-Buckets und segments verwenden exakt dieselbe gefilterte "
            "Versandkohorte wie total. segments ist die gemeinsame Zelle aus "
            "Scanner, Strategie, Richtung, Horizont, Marktregime, Code-Revision "
            "und Fill-Evidenzmodus. "
            "calibration_cells ist die gemeinsame Freigabezelle ausschliesslich "
            "aus Scanner, Richtung, Horizont und Marktregime; Revision und "
            "Fill-Evidenzmodus teilen diese Kalibrierstichprobe nicht weiter. "
            "legacy_unknown/legacy_unclassified kennzeichnet historische Zeilen "
            "ohne revisions- bzw. evidenzgebundene Annotation; sie werden nicht "
            "still mit neuem Evidenzcode vermischt."
        )
        summary["uncertainty_semantics"] = (
            "Konservativ = Stop zuerst, wenn dieselbe OHLC-Bar Stop und Ziel "
            "beruehrt. Upper = bester noch moeglicher Pfad nur fuer solche "
            "reihenfolge-unklaren Bars. Upper ist kein Erwartungswert und keine "
            "behauptete Performance, sondern die Obergrenze des Datenbands."
        )
        summary["recent"] = [
            {
                "id": row.get("id"),
                "created_at": row.get("created_at"),
                "scanner": row.get("scanner"),
                "strategy": row.get("strategy") or row.get("scanner"),
                "ticker": row.get("ticker"),
                "asset_class": row.get("asset_class"),
                "direction": row.get("direction"),
                "trade_horizon": row.get("trade_horizon"),
                "evaluation_horizon_bars": row.get("evaluation_horizon_bars"),
                "market_regime": row.get("market_regime") or "legacy_unknown",
                "status": row.get("status"),
                "outcome_detail": row.get("outcome_detail") or "",
                "entry": row.get("entry"),
                "entry_filled_at": row.get("entry_filled_at"),
                "entry_fill_price": row.get("entry_fill_price"),
                "fill_evidence_mode": row.get("fill_evidence_mode") or "legacy_unclassified",
                "code_revision": row.get("code_revision") or "legacy_unknown",
                "stop": row.get("stop"),
                "exit_fill_price": row.get("exit_fill_price"),
                "stop_gap_slippage_r": row.get("stop_gap_slippage_r"),
                "stop_gap_slippage_pct": row.get("stop_gap_slippage_pct"),
                "tp1": row.get("tp1"),
                "tp2": row.get("tp2"),
                "r_realized": row.get("r_realized"),
                "r_realized_upper": _realized_upper(row),
                "r_realized_be": row.get("r_realized_be"),
                "r_managed_50_50": _managed_r_50_50(row),
                "r_managed_50_50_be": simulate_managed_5050_breakeven(row),
                "r_managed_50_50_be_upper": _managed_upper_r(row),
                "managed_be_unresolved": _managed_5050_be_resolution(row)[1],
                "tp1_hit_at": row.get("tp1_hit_at"),
            }
            for row in rows[:20]
        ]
    except Exception as exc:
        logger.warning("load_performance_summary fehlgeschlagen: %s", exc)
    return summary


def load_breaker_recovery_summary(
    scanner_key: str,
    since: Any,
    direction: Optional[str] = None,
    horizon: Optional[str] = None,
    market_regime: Optional[str] = None,
    *,
    as_of: Any = None,
) -> dict:
    """Return one fully-observed post-trip joint calibration cell.

    Breaker release is fail-closed: never aggregate across direction, horizon
    or regime, require 30 mature outcomes in the same cell, and reject any
    unresolved managed-BE evidence.
    """
    scanner = str(scanner_key or "").strip()
    since_dt = _parse_utc_datetime(since)
    as_of_dt = _parse_utc_datetime(as_of) if as_of is not None else _utc_now()
    as_of_dt = as_of_dt or _utc_now()
    requested = (direction, horizon, market_regime)
    summary: Dict[str, Any] = {
        "available": False,
        "joint_cell_verified": False,
        "scanner": scanner,
        "direction": None,
        "horizon": None,
        "market_regime": None,
        "cell_id": None,
        "since": since_dt.isoformat() if since_dt else None,
        "as_of": as_of_dt.isoformat(),
        "minimum_decided": 30,
        "fully_observed_post_trip": 0,
        "r_model": "managed_50_50_plus_be_actual_or_shadow_counterfactual",
        "shadow_counterfactual_contract": (
            "No delivery is asserted. Shadow results use complete fill/stop/TP "
            "geometry plus a causal virtual BE exit or a non-ambiguous "
            "TP2/MFE>=max(2R, geometric TP2 R) completion."
        ),
        "decided": 0,
        "wins": 0,
        "win_pct": None,
        "avg_r": None,
        "sum_r": 0.0,
        "trade_decided": 0,
        "shadow_decided": 0,
        "actual_delivery_decided": 0,
        "shadow_counterfactual_decided": 0,
        "managed_be_unresolved": 0,
        "error": None,
    }
    if not scanner:
        summary["error"] = "scanner_missing"
        return summary
    if since_dt is None:
        summary["error"] = "invalid_since"
        return summary
    if any(value is not None for value in requested) and not all(
        value is not None and str(value).strip() for value in requested
    ):
        summary["error"] = "joint_cell_identifiers_incomplete"
        return summary

    try:
        with _DB_LOCK:
            with _db_connection() as conn:
                rows = [
                    dict(row)
                    for row in conn.execute(
                    "SELECT * FROM signals "
                    "WHERE scanner = ? "
                    "AND COALESCE(delivery_accepted_at, created_at) >= ? "
                    "AND COALESCE(delivery_accepted_at, created_at) <= ? "
                    "AND status != ? "
                    "AND mail_class IN ('trade', 'shadow')",
                    (
                        scanner, since_dt.isoformat(), as_of_dt.isoformat(),
                        STATUS_PENDING_DELIVERY,
                    ),
                    ).fetchall()
                ]
        cells: Dict[Tuple[str, str, str, str], List[Dict[str, Any]]] = {}
        for row in rows:
            cells.setdefault(_calibration_cell_key(row), []).append(row)
        if all(value is not None for value in requested):
            requested_key = (
                scanner,
                "SHORT" if str(direction).upper() == "SHORT" else "LONG",
                str(horizon).strip().lower(),
                str(market_regime).strip().upper(),
            )
            selected_cell_rows = cells.get(requested_key, [])
            selected_key = requested_key
        elif len(cells) == 1:
            selected_key, selected_cell_rows = next(iter(cells.items()))
        elif len(cells) > 1:
            summary["error"] = "joint_cell_ambiguous"
            summary["joint_cell_candidates"] = ["|".join(key) for key in sorted(cells)]
            return summary
        else:
            summary["error"] = "no_fully_observed_joint_cell"
            return summary
        if not selected_cell_rows:
            summary["error"] = "joint_cell_not_found"
            return summary
        selected_rows = [
            row
            for row in selected_cell_rows
            if row.get("r_realized") is not None
            and _signal_has_full_observation_window(row, as_of_dt)
        ]
        summary["fully_observed_post_trip"] = len(selected_rows)
        summary["post_trip_cell_signals"] = len(selected_cell_rows)
        summary.update({
            "joint_cell_verified": True,
            "direction": selected_key[1],
            "horizon": selected_key[2],
            "market_regime": selected_key[3],
            "cell_id": "|".join(selected_key),
        })
        resolved_rows: List[Tuple[Dict[str, Any], float]] = []
        unresolved = 0
        for row in selected_rows:
            is_shadow = str(row.get("mail_class") or "").lower() == "shadow"
            if is_shadow:
                value, is_unresolved = (
                    _shadow_counterfactual_5050_be_resolution(row)
                )
            else:
                value, is_unresolved = _managed_5050_be_resolution(row)
            if is_unresolved:
                unresolved += 1
            elif value is not None:
                resolved_rows.append((row, value))
        realized = [value for _row, value in resolved_rows]
        decided = len(realized)
        wins = sum(1 for value in realized if value > 0.0)
        sufficient = decided >= 30
        summary.update({
                "available": sufficient and unresolved == 0,
                "decided": decided,
                "wins": wins,
                "win_pct": round((wins / decided) * 100.0, 2) if decided else None,
                "avg_r": round(sum(realized) / decided, 4) if decided else None,
                "sum_r": round(sum(realized), 4),
                "trade_decided": sum(
                    1
                    for row, _value in resolved_rows
                    if str(row["mail_class"] or "").lower() == "trade"
                ),
                "shadow_decided": sum(
                    1
                    for row, _value in resolved_rows
                    if str(row["mail_class"] or "").lower() == "shadow"
                ),
                "actual_delivery_decided": sum(
                    1
                    for row, _value in resolved_rows
                    if str(row["mail_class"] or "").lower() == "trade"
                ),
                "shadow_counterfactual_decided": sum(
                    1
                    for row, _value in resolved_rows
                    if str(row["mail_class"] or "").lower() == "shadow"
                ),
                "managed_be_unresolved": unresolved,
                "error": (
                    "managed_be_unresolved"
                    if unresolved
                    else ("insufficient_joint_cell_sample" if not sufficient else None)
                ),
            })
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
                row = conn.execute(
                    "SELECT COUNT(*) FROM signals WHERE status != ?",
                    (STATUS_PENDING_DELIVERY,),
                ).fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:
        logger.warning("get_signal_count fehlgeschlagen: %s", exc)
        return -1
