"""Alpha Station — Telegram-Benachrichtigungen fuer Trade-Alerts.

Schlanker Versand-Kanal ueber die Telegram-Bot-API (sendMessage).
Konfiguration ueber die ENV-Variablen TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID
(oder explizite Parameter). Alle Funktionen sind bewusst fehlertolerant:
send_telegram_alert() wirft nie und liefert bei jedem Problem False —
der Alert-Versand der App darf am Telegram-Kanal niemals scheitern.

API-Kontrakt (von api.py / bg_service.py konsumiert):
  - is_telegram_configured() -> bool
  - send_telegram_alert(subject, body_text, token, chat_id) -> bool
  - format_alert_rows_for_telegram(rows, max_rows) -> str
"""

from __future__ import annotations

import html
import logging
import math
import os
import re
import time
from typing import Any, List, Optional

import requests

# Gleiche tolerante Feld-Alias-Logik wie der Signal-Tracker (eine Quelle der
# Wahrheit fuer Ticker/Direction/Entry/Stop/TP1/TP2).
try:
    from modules.signal_tracker import extract_signal_fields
except ImportError:  # pragma: no cover — Fallback bei direktem Import aus modules/
    from signal_tracker import extract_signal_fields

logger = logging.getLogger(__name__)

__all__ = [
    "is_telegram_configured",
    "send_telegram_alert",
    "format_alert_rows_for_telegram",
]

TELEGRAM_API_BASE = "https://api.telegram.org"
TELEGRAM_MAX_MESSAGE_LEN = 4096  # hartes Telegram-Limit pro Nachricht
_ELLIPSIS = "…"  # '…'
_WARN_INTERVAL_SECONDS = 60.0

#: Spam-Schutz fuers Log: Zeitpunkt der letzten WARNING (monotonic).
_last_warn_monotonic: Optional[float] = None

#: Am Ende abgeschnittenes HTML-Entity-Fragment (z.B. '&am' aus '&amp;').
#: Komplette Entities enden immer mit ';' und matchen daher nicht.
_DANGLING_ENTITY_RE = re.compile(r"&[#A-Za-z0-9]{0,9}$")


def is_telegram_configured() -> bool:
    """True, wenn TELEGRAM_BOT_TOKEN und TELEGRAM_CHAT_ID beide nicht-leer sind."""
    token = str(os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
    chat_id = str(os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
    return bool(token) and bool(chat_id)


def _log_send_failure(message: str) -> None:
    """Fehler-Logging mit Spam-Schutz: maximal eine WARNING pro Minute.

    Weitere Fehler innerhalb des Fensters werden nur auf DEBUG geloggt,
    damit ein haengender Telegram-Endpoint das Log nicht flutet.
    """
    global _last_warn_monotonic
    now = time.monotonic()
    if _last_warn_monotonic is None or (now - _last_warn_monotonic) >= _WARN_INTERVAL_SECONDS:
        _last_warn_monotonic = now
        logger.warning("Telegram-Versand fehlgeschlagen: %s", message)
    else:
        logger.debug("Telegram-Versand fehlgeschlagen (Warnung gedrosselt): %s", message)


def _strip_dangling_entity(text: str) -> str:
    """Entfernt ein am Schnittpunkt zerteiltes HTML-Entity am Textende."""
    return _DANGLING_ENTITY_RE.sub("", text)


def _build_message_text(subject: str, body_text: str) -> str:
    """Baut '<b>{subject}</b>\\n{body_text}' mit HTML-Escaping und 4096er-Limit.

    BEIDE Inputs werden VOR dem Einbetten mit html.escape() entschaerft —
    das einzige echte HTML-Tag der Nachricht ist unser eigenes <b>-Paar.
    Ueberlange Nachrichten werden hart gekuerzt ('…'), ohne das <b>-Tag-Paar
    oder ein HTML-Entity zu zerschneiden (sonst HTTP 400 vom HTML-Parser).
    """
    subject_esc = html.escape(str(subject or ""))
    body_esc = html.escape(str(body_text or ""))
    text = "<b>%s</b>\n%s" % (subject_esc, body_esc)
    text = text.rstrip()
    if len(text) <= TELEGRAM_MAX_MESSAGE_LEN:
        return text
    overhead = len("<b></b>")
    # Fall 1: Subject passt komplett — nur den Body kuerzen.
    max_body = TELEGRAM_MAX_MESSAGE_LEN - overhead - len(subject_esc) - 1 - len(_ELLIPSIS)
    if max_body >= 0:
        body_cut = _strip_dangling_entity(body_esc[:max_body])
        return "<b>%s</b>\n%s%s" % (subject_esc, body_cut, _ELLIPSIS)
    # Fall 2: Schon das Subject sprengt das Limit — Subject kuerzen, Body weglassen.
    max_subject = TELEGRAM_MAX_MESSAGE_LEN - overhead - len(_ELLIPSIS)
    subject_cut = _strip_dangling_entity(subject_esc[:max_subject])
    return "<b>%s%s</b>" % (subject_cut, _ELLIPSIS)


def send_telegram_alert(
    subject: str,
    body_text: str = "",
    token: Optional[str] = None,
    chat_id: Optional[str] = None,
) -> bool:
    """Sendet eine Nachricht ueber die Telegram-Bot-API. Wirft nie.

    Args:
        subject:   Betreff — wird fett (<b>) gesetzt; eigene HTML-Tags im
                   Input werden escaped und erscheinen als sichtbarer Text.
        body_text: Optionaler Nachrichtentext (ebenfalls escaped).
        token:     Bot-Token; Default aus ENV TELEGRAM_BOT_TOKEN.
        chat_id:   Ziel-Chat; Default aus ENV TELEGRAM_CHAT_ID.

    Returns:
        True bei HTTP 200, sonst False (auch wenn Token/Chat-ID fehlen —
        dann wird gar kein Request abgesetzt). Fehler werden geloggt,
        gedrosselt auf maximal eine WARNING pro Minute.
    """
    try:
        token_value = str(token).strip() if token else str(os.environ.get("TELEGRAM_BOT_TOKEN", "") or "").strip()
        chat_value = str(chat_id).strip() if chat_id else str(os.environ.get("TELEGRAM_CHAT_ID", "") or "").strip()
        if not token_value or not chat_value:
            logger.debug(
                "Telegram nicht konfiguriert (Token/Chat-ID fehlt) — Nachricht '%s' nicht gesendet",
                str(subject or "")[:80],
            )
            return False
        text = _build_message_text(subject, body_text)
        response = requests.post(
            "%s/bot%s/sendMessage" % (TELEGRAM_API_BASE, token_value),
            json={
                "chat_id": chat_value,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        status_code = getattr(response, "status_code", None)
        if status_code == 200:
            return True
        body_snippet = str(getattr(response, "text", ""))[:200]
        _log_send_failure("HTTP %s: %s" % (status_code, body_snippet))
        return False
    except Exception as exc:
        # Exception-Texte koennen die Request-URL (inkl. Token) enthalten —
        # vor dem Loggen maskieren.
        message = "%s: %s" % (type(exc).__name__, exc)
        try:
            if token_value:
                message = message.replace(token_value, "***")
        except Exception:  # pragma: no cover
            pass
        _log_send_failure(message)
        return False


def _format_price(value: Any) -> str:
    """Preisformat: >= 1 zwei Dezimalstellen, < 1 sechs signifikante Stellen
    (ohne Scientific-Notation). None/unparsbar -> '-'."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "-"
    if not math.isfinite(number):
        return "-"
    magnitude = abs(number)
    if magnitude >= 1:
        return "%.2f" % number
    if magnitude == 0:
        return "0.00"
    formatted = "%.6g" % number
    if "e" in formatted or "E" in formatted:
        # %g weicht bei sehr kleinen Werten auf Scientific aus -> fest formatieren.
        decimals = 6 - int(math.floor(math.log10(magnitude))) - 1
        decimals = min(max(decimals, 2), 20)
        formatted = "%.*f" % (decimals, number)
    return formatted


def format_alert_rows_for_telegram(rows: list, max_rows: int = 5) -> str:
    """Formatiert Alert-Rows als kompakte Telegram-Textzeilen. Wirft nie.

    Format je Row:
      '{TICKER} {RICHTUNG} | Entry {x} | Stop {y} | TP1 {z} | TP2 {w}'
    Feld-Aliase identisch zu modules/signal_tracker.extract_signal_fields;
    fehlende Level erscheinen als '-'. Rows ohne Ticker werden uebersprungen.
    Mehr als max_rows Zeilen werden abgeschnitten und mit einer Hinweiszeile
    ('… +N weitere') zusammengefasst.
    """
    try:
        if not rows or not isinstance(rows, (list, tuple)):
            return ""
        try:
            limit = max(0, int(max_rows))
        except (TypeError, ValueError):
            limit = 5
        formatted: List[str] = []
        for row in rows:
            fields = extract_signal_fields(row)
            ticker = fields.get("ticker")
            if not ticker:
                continue
            direction = "SHORT" if fields.get("direction") == "SHORT" else "LONG"
            formatted.append(
                "%s %s | Entry %s | Stop %s | TP1 %s | TP2 %s"
                % (
                    ticker,
                    direction,
                    _format_price(fields.get("entry")),
                    _format_price(fields.get("stop")),
                    _format_price(fields.get("tp1")),
                    _format_price(fields.get("tp2")),
                )
            )
        lines = formatted[:limit]
        hidden = len(formatted) - len(lines)
        if hidden > 0:
            lines.append("%s +%d weitere" % (_ELLIPSIS, hidden))
        return "\n".join(lines)
    except Exception as exc:
        logger.warning("format_alert_rows_for_telegram fehlgeschlagen: %s", exc)
        return ""
