"""Persistente Mail-Outbox (AUDIT 2026-08-01, Befund F-10).

Anlass (bewiesener Vorfall KW31/2026): Der Provider blockierte SMTP-Port 465
mehrere Stunden. bg_service lief pro Mail 3x in den Timeout und verwarf die
Mail danach endgueltig — 3 Exit-Update-Mails und fast der Wochenreport gingen
unwiederbringlich verloren. Es gab keinerlei Warteschlange.

Dieses Modul ist die Warteschlange:

- Jede Mail, deren Sofort-Versand (inkl. Retry-Loop) endgueltig scheitert,
  wird in einer SQLite-DB unter data_cache/ abgelegt (ueberlebt Neustarts —
  bewusst NICHT /tmp).
- Ein eigener Worker im bg_service liefert faellige Eintraege mit
  Backoff nach: 5 min, 15 min, 1 h, dann 3 h-Rhythmus; nach MAX_ATTEMPTS
  Fehlversuchen gilt ein Eintrag als "dead".
- STALE-SCHUTZ (zentrale Produktentscheidung): Jede Mail-Klasse hat eine
  Verfallszeit (TTL). Eine "🚨 JETZT TRADEN"-Mail, die 5 Stunden spaeter
  zugestellt wird, ist kein Service, sondern eine Gefahr (Entry laengst
  weg, evtl. bereits im Stop). Deshalb:
    trade        ->  90 min   (Intraday-Trigger verfaellt schnell)
    swing_trade  ->   8 h     (Struktur-Setup, gleicher Handelstag)
    watch        ->  12 h     (Beobachtung, unkritisch)
    info         ->  48 h     (Wochenreport, Exit-Updates, Waechter)
  Abgelaufene Eintraege werden NICHT mehr versendet, sondern als "expired"
  markiert — sichtbar in stats(), kein stiller Verlust mehr.
- Enqueue-Dedupe: Solange ein Eintrag mit identischem Betreff pending oder
  bereits von einem Worker geleast ist, wird kein zweiter angelegt
  (Schutz vor Queue-Flut bei Dauer-Stoerung,
  z. B. Waechter-Mail alle 10 min).

Kontrakte:
- Oeffentliche Funktionen werfen NIE (die Outbox darf den Mail-Pfad, den sie
  retten soll, selbst nie brechen). Fehler landen im Log/Return None.
- DB-Zugriff: WAL + busy_timeout, weil api (enqueue) und bg (enqueue+worker)
  parallel auf dieselbe Datei zugreifen.
- Ein atomarer SQLite-Lease schuetzt die Versand-Statusuebergaenge. Dadurch
  koennen auch bei versehentlich zwei laufenden Workern nicht beide dieselbe
  Mail gleichzeitig uebernehmen. Verwaiste Leases werden nach Ablauf wieder
  freigegeben.

Env:
- MAIL_OUTBOX_ENABLED=0 schaltet Enqueue UND Worker ab (Default: an).
- MAIL_OUTBOX_DB_PATH ueberschreibt den DB-Pfad (Tests/Default siehe unten).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

__all__ = [
    "MAIL_OUTBOX_DB_PATH",
    "TTL_SECONDS_BY_CLASS",
    "BACKOFF_SECONDS",
    "MAX_ATTEMPTS",
    "CLAIM_LEASE_SECONDS",
    "outbox_enabled",
    "ttl_seconds_for",
    "init_db",
    "enqueue",
    "due_items",
    "mark_sent",
    "mark_failed",
    "process_outbox",
    "stats",
]

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DATA_DIR = Path(os.environ.get("ALPHA_DATA_DIR", _REPO_ROOT / "data_cache"))

#: DB-Pfad; via Env/Monkeypatch ueberschreibbar (Muster wie signal_tracker).
MAIL_OUTBOX_DB_PATH: str = os.environ.get(
    "MAIL_OUTBOX_DB_PATH", str(_DATA_DIR / "mail_outbox.sqlite")
)

#: Verfallszeiten je Mail-Klasse (Stale-Schutz, siehe Modul-Docstring).
TTL_SECONDS_BY_CLASS: Dict[str, int] = {
    "trade": 90 * 60,
    "swing_trade": 8 * 3600,
    "watch": 12 * 3600,
    "info": 48 * 3600,
}
TTL_SECONDS_DEFAULT: int = 12 * 3600

#: Wartezeit vor dem naechsten Zustellversuch, indexiert nach Attempt-Zaehler
#: (1. Fehlversuch -> 5 min, 2. -> 15 min, 3. -> 1 h, danach 3 h-Rhythmus).
BACKOFF_SECONDS: List[int] = [5 * 60, 15 * 60, 3600, 3 * 3600]

#: Nach so vielen Fehlversuchen wird ein Eintrag endgueltig aufgegeben.
MAX_ATTEMPTS: int = 10

#: Maximaldauer einer atomaren Worker-Uebernahme. Nach einem Prozessabbruch
#: darf ein anderer Worker den Eintrag wieder aufnehmen.
CLAIM_LEASE_SECONDS: int = 10 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS mail_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL NOT NULL,
  subject TEXT NOT NULL,
  body_html TEXT NOT NULL,
  recipients_json TEXT NOT NULL,
  mail_class TEXT NOT NULL DEFAULT 'info',
  telegram_text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  next_attempt_at REAL NOT NULL,
  expires_at REAL NOT NULL,
  last_error TEXT NOT NULL DEFAULT '',
  sent_at REAL
);
CREATE INDEX IF NOT EXISTS idx_mail_outbox_due
  ON mail_outbox(status, next_attempt_at);
"""


def outbox_enabled() -> bool:
    """MAIL_OUTBOX_ENABLED=0 deaktiviert Enqueue + Worker (Default: an)."""
    return str(os.environ.get("MAIL_OUTBOX_ENABLED", "1")).strip().lower() not in {
        "0", "false", "no", "off",
    }


def ttl_seconds_for(mail_class: Optional[str]) -> int:
    """Verfallszeit fuer eine Mail-Klasse (unbekannt -> Default)."""
    return TTL_SECONDS_BY_CLASS.get(
        str(mail_class or "").strip().lower(), TTL_SECONDS_DEFAULT
    )


def _db_path(db_path: Optional[str] = None) -> str:
    return str(db_path or MAIL_OUTBOX_DB_PATH)


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    path = Path(_db_path(db_path))
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def init_db(db_path: Optional[str] = None) -> bool:
    """Schema idempotent anlegen. Rueckgabe False statt Exception bei Fehler."""
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
        return True
    except Exception:
        return False


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
    except Exception:
        item["recipients"] = []
    return item


def enqueue(
    subject: str,
    body_html: str,
    recipients: List[str],
    *,
    mail_class: str = "info",
    telegram_text: str = "",
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Mail in die Warteschlange legen. Rueckgabe: Outbox-ID oder None.

    Dedupe: identischer Betreff bereits pending/sending -> vorhandene ID, kein
    Duplikat. Wirft nie.
    """
    if not outbox_enabled():
        return None
    now = float(now if now is not None else time.time())
    try:
        subject = str(subject or "").strip()
        if not subject:
            return None
        clean_recipients = sorted({str(a).strip() for a in (recipients or []) if "@" in str(a)})
        if not clean_recipients:
            return None
        ttl = ttl_seconds_for(mail_class)
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            dup = conn.execute(
                "SELECT id FROM mail_outbox "
                "WHERE status IN ('pending','sending') AND subject=? "
                "ORDER BY created_at DESC LIMIT 1",
                (subject,),
            ).fetchone()
            if dup is not None:
                return int(dup["id"])
            cur = conn.execute(
                "INSERT INTO mail_outbox "
                "(created_at, subject, body_html, recipients_json, mail_class, "
                " telegram_text, status, attempts, next_attempt_at, expires_at) "
                "VALUES (?,?,?,?,?,?, 'pending', 0, ?, ?)",
                (
                    now,
                    subject,
                    str(body_html or ""),
                    json.dumps(clean_recipients),
                    str(mail_class or "info").strip().lower() or "info",
                    str(telegram_text or ""),
                    now,  # erster Nachzustellversuch sofort beim naechsten Worker-Tick
                    now + ttl,
                ),
            )
            return int(cur.lastrowid)
    except Exception:
        return None


def _expire_overdue(conn: sqlite3.Connection, now: float) -> int:
    """Pendente, abgelaufene Eintraege auf 'expired' setzen. Anzahl zurueck."""
    cur = conn.execute(
        "UPDATE mail_outbox SET status='expired' "
        "WHERE status='pending' AND expires_at < ?",
        (now,),
    )
    return int(cur.rowcount or 0)


def _recover_abandoned_claims(conn: sqlite3.Connection, now: float) -> int:
    """Abgelaufene Worker-Leases wieder als pending freigeben."""
    cur = conn.execute(
        "UPDATE mail_outbox SET status='pending' "
        "WHERE status='sending' AND next_attempt_at <= ?",
        (now,),
    )
    return int(cur.rowcount or 0)


def _claim_due_items(
    *,
    now: Optional[float] = None,
    limit: int = 10,
    db_path: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], int]:
    """Faellige Eintraege in einer exklusiven DB-Transaktion uebernehmen.

    next_attempt_at dient waehrend status='sending' als Lease-Ablauf. Die
    Transaktion verhindert, dass zwei Prozesse dieselben IDs erhalten.
    Rueckgabe: (geleaste Eintraege, neu abgelaufene Eintraege).
    """
    now = float(now if now is not None else time.time())
    conn: Optional[sqlite3.Connection] = None
    try:
        conn = _connect(db_path)
        conn.executescript(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        _recover_abandoned_claims(conn, now)
        expired = _expire_overdue(conn, now)
        rows = conn.execute(
            "SELECT id FROM mail_outbox "
            "WHERE status='pending' AND next_attempt_at <= ? "
            "ORDER BY created_at ASC LIMIT ?",
            (now, max(1, int(limit))),
        ).fetchall()
        ids = [int(row["id"]) for row in rows]
        claimed: List[sqlite3.Row] = []
        if ids:
            placeholders = ",".join("?" for _ in ids)
            conn.execute(
                f"UPDATE mail_outbox SET status='sending', next_attempt_at=? "
                f"WHERE status='pending' AND id IN ({placeholders})",
                (now + CLAIM_LEASE_SECONDS, *ids),
            )
            claimed = conn.execute(
                f"SELECT * FROM mail_outbox "
                f"WHERE status='sending' AND id IN ({placeholders}) "
                "ORDER BY created_at ASC",
                ids,
            ).fetchall()
        conn.commit()
        return [_row_to_dict(row) for row in claimed], expired
    except Exception:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass
        return [], 0
    finally:
        if conn is not None:
            conn.close()


def due_items(
    *,
    now: Optional[float] = None,
    limit: int = 10,
    db_path: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Faellige pendente Eintraege (aeltste zuerst). Expired vorher markiert.

    Rueckgabe: Liste von Dicts inkl. 'recipients' (bereits dekodiert).
    Wirft nie (Fehler -> leere Liste).
    """
    now = float(now if now is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            _recover_abandoned_claims(conn, now)
            _expire_overdue(conn, now)
            rows = conn.execute(
                "SELECT * FROM mail_outbox "
                "WHERE status='pending' AND next_attempt_at <= ? "
                "ORDER BY created_at ASC LIMIT ?",
                (now, max(1, int(limit))),
            ).fetchall()
            return [_row_to_dict(r) for r in rows]
    except Exception:
        return []


def mark_sent(
    item_id: int,
    *,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Eintrag als zugestellt markieren. Wirft nie."""
    now = float(now if now is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute(
                "UPDATE mail_outbox SET status='sent', sent_at=?, last_error='' "
                "WHERE id=? AND status='sending'",
                (now, int(item_id)),
            )
        return bool(cur.rowcount)
    except Exception:
        return False


def mark_failed(
    item_id: int,
    error: str,
    *,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> str:
    """Fehlversuch verbuchen: Attempts+1, naechster Versuch nach Backoff.

    Rueckgabe: neuer Status ("pending" oder "dead"). Wirft nie
    (Fehler -> "pending", damit nichts verloren geht).
    """
    now = float(now if now is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            row = conn.execute(
                "SELECT attempts, status FROM mail_outbox WHERE id=?",
                (int(item_id),),
            ).fetchone()
            if row is None:
                return "pending"
            if str(row["status"]) != "sending":
                return str(row["status"])
            attempts = int(row["attempts"]) + 1
            if attempts >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE mail_outbox SET status='dead', attempts=?, last_error=? "
                    "WHERE id=? AND status='sending'",
                    (attempts, str(error or "")[:500], int(item_id)),
                )
                return "dead"
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            conn.execute(
                "UPDATE mail_outbox SET status='pending', attempts=?, "
                "last_error=?, next_attempt_at=? "
                "WHERE id=? AND status='sending'",
                (attempts, str(error or "")[:500], now + delay, int(item_id)),
            )
            return "pending"
    except Exception:
        return "pending"


def process_outbox(
    send_fn: Callable[[Dict[str, Any]], None],
    *,
    now: Optional[float] = None,
    limit: int = 10,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Worker-Kern: liefert faellige Eintraege ueber send_fn aus.

    send_fn(row) muss bei Erfolg still zurueckkehren und bei Misserfolg eine
    Exception werfen. Pro Eintrag: Erfolg -> mark_sent; Fehler -> mark_failed
    (Backoff bzw. dead). Ablauf wird vorher als expired markiert und NICHT
    mehr versendet (Stale-Schutz).

    Rueckgabe: {"sent": n, "failed": n, "expired": n, "dead": n,
                "sent_rows": [..]} — wirft nie.
    """
    result: Dict[str, Any] = {
        "sent": 0, "failed": 0, "expired": 0, "dead": 0, "sent_rows": [],
    }
    if not outbox_enabled():
        return result
    now = float(now if now is not None else time.time())
    items, expired = _claim_due_items(now=now, limit=limit, db_path=db_path)
    result["expired"] = expired
    for item in items:
        try:
            send_fn(item)
        except Exception as exc:
            new_status = mark_failed(item["id"], str(exc), now=now, db_path=db_path)
            result["failed"] += 1
            if new_status == "dead":
                result["dead"] += 1
            continue
        if mark_sent(item["id"], now=now, db_path=db_path):
            result["sent"] += 1
            result["sent_rows"].append(
                {
                    "id": item["id"],
                    "subject": item["subject"],
                    "mail_class": item.get("mail_class", "info"),
                    "telegram_text": item.get("telegram_text", ""),
                }
            )
    return result


def stats(
    *,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Transparenter Outbox-Zustand fuer Health/API. Wirft nie."""
    now = float(now if now is not None else time.time())
    counts: Dict[str, Any] = {
        "enabled": outbox_enabled(),
        "available": False,
        "pending": 0, "sending": 0, "queued": 0,
        "sent": 0, "expired": 0, "dead": 0, "total": 0,
        "pending_with_errors": 0,
        "oldest_pending_age_seconds": None,
        "oldest_pending_created_at": None,
        "last_error": "",
        "error": "",
    }
    if not counts["enabled"]:
        return counts
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            _recover_abandoned_claims(conn, now)
            _expire_overdue(conn, now)
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM mail_outbox GROUP BY status"
            ).fetchall():
                counts[str(row["status"])] = int(row["n"])
                counts["total"] += int(row["n"])
            counts["queued"] = int(counts.get("pending", 0)) + int(
                counts.get("sending", 0)
            )
            pending = conn.execute(
                "SELECT MIN(created_at) AS oldest, "
                "SUM(CASE WHEN last_error <> '' THEN 1 ELSE 0 END) AS with_errors "
                "FROM mail_outbox WHERE status IN ('pending','sending')"
            ).fetchone()
            if pending is not None:
                oldest = pending["oldest"]
                counts["pending_with_errors"] = int(pending["with_errors"] or 0)
                if oldest is not None:
                    counts["oldest_pending_created_at"] = float(oldest)
                    counts["oldest_pending_age_seconds"] = max(
                        0, int(now - float(oldest))
                    )
            latest_error = conn.execute(
                "SELECT last_error FROM mail_outbox "
                "WHERE last_error <> '' ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if latest_error is not None:
                counts["last_error"] = str(latest_error["last_error"] or "")
        counts["available"] = True
    except Exception as exc:
        counts["error"] = str(exc)
    return counts
