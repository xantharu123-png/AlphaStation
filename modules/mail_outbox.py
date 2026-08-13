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
    signal_update -> 15 min   (Stop/TP/BE-Folgeereignis, zeitkritisch)
    info         ->  48 h     (Wochenreport, Waechter)
  Abgelaufene Eintraege werden NICHT mehr versendet, sondern als "expired"
  markiert — sichtbar in stats(), kein stiller Verlust mehr.
- Enqueue-Dedupe: Solange ein inhaltlich identischer Eintrag pending oder
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
  Mail gleichzeitig uebernehmen. Vor SMTP wechselt der Datensatz von
  ``sending`` nach ``delivering``. Nur verwaiste pre-DATA-Leases werden
  wieder freigegeben; eine verwaiste Delivery wird als ``uncertain``
  quarantänisiert, weil ein automatischer Retry duplizieren könnte.

Env:
- MAIL_OUTBOX_ENABLED=0 schaltet Enqueue UND Worker ab (Default: an).
- MAIL_OUTBOX_DB_PATH ueberschreibt den DB-Pfad (Tests/Default siehe unten).
"""

from __future__ import annotations

from contextlib import contextmanager
import json
import hashlib
import math
import os
import sqlite3
import threading
import time
import uuid
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
    "quarantine",
    "register_uncertain_delivery_keys",
    "has_uncertain_delivery_key",
    "record_tracker_acceptance_pending",
    "load_tracker_acceptance_pending",
    "mark_tracker_acceptance_done",
    "due_items",
    "mark_delivering",
    "mark_sent",
    "mark_failed",
    "mark_uncertain",
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
    "signal_update": 15 * 60,
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

_UNCERTAIN_REGISTRY_LOCK = threading.RLock()

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
  ,dedupe_key TEXT
  ,delivery_dedupe_keys_json TEXT NOT NULL DEFAULT '[]'
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


def _uncertain_registry_path(db_path: Optional[str] = None) -> str:
    return f"{_db_path(db_path)}.uncertain.json"


def _tracker_acceptance_journal_path(db_path: Optional[str] = None) -> str:
    return f"{_db_path(db_path)}.tracker_acceptance.json"


def _acquire_registry_lock(lock_file: Any) -> None:
    lock_file.seek(0, os.SEEK_END)
    if lock_file.tell() == 0:
        lock_file.write(b"0")
        lock_file.flush()
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)


def _release_registry_lock(lock_file: Any) -> None:
    if os.name == "nt":
        import msvcrt

        lock_file.seek(0)
        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
    else:
        import fcntl

        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _locked_uncertain_registry(db_path: Optional[str] = None):
    registry_path = _uncertain_registry_path(db_path)
    lock_path = f"{registry_path}.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with _UNCERTAIN_REGISTRY_LOCK:
        with open(lock_path, "a+b") as lock_file:
            _acquire_registry_lock(lock_file)
            try:
                yield registry_path
            finally:
                _release_registry_lock(lock_file)


@contextmanager
def _locked_tracker_acceptance_journal(db_path: Optional[str] = None):
    journal_path = _tracker_acceptance_journal_path(db_path)
    lock_path = f"{journal_path}.lock"
    Path(lock_path).parent.mkdir(parents=True, exist_ok=True)
    with _UNCERTAIN_REGISTRY_LOCK:
        with open(lock_path, "a+b") as lock_file:
            _acquire_registry_lock(lock_file)
            try:
                yield journal_path
            finally:
                _release_registry_lock(lock_file)


def _load_uncertain_registry_unlocked(path: str) -> Optional[Dict[str, Any]]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict):
        return None
    entries = raw.get("entries", raw)
    return entries if isinstance(entries, dict) else None


def _write_uncertain_registry_unlocked(path: str, entries: Dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    tmp_path = (
        f"{path}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(
                {"version": 1, "entries": entries},
                handle,
                separators=(",", ":"),
                sort_keys=True,
            )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
        except OSError:
            pass


def register_uncertain_delivery_keys(
    delivery_dedupe_keys: Optional[List[str]],
    *,
    subject: str = "",
    body_html: str = "",
    recipients: Optional[List[str]] = None,
    mail_class: str = "info",
    error: str = "delivery outcome unknown",
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Durably quarantine delivery keys when the primary SQLite write fails.

    The fallback registry has deliberately no TTL. A DATA outcome does not
    become safe to resend merely because a lease or normal mail TTL elapsed.
    A negative integer is an explicit fallback receipt; positive IDs belong to
    SQLite rows returned by :func:`quarantine`. ``None`` means no durable write.
    """
    timestamp = float(now if now is not None else time.time())
    clean_keys = sorted({
        str(value).strip()[:240]
        for value in (delivery_dedupe_keys or [])
        if str(value).strip() and "@" not in str(value)
    })
    clean_recipients = sorted({
        str(value).strip().lower()
        for value in (recipients or [])
        if "@" in str(value)
    })
    content_key = _content_dedupe_key(
        str(subject or "").strip(),
        str(body_html or ""),
        clean_recipients,
        str(mail_class or "info").strip().lower() or "info",
        "",
        clean_keys,
    )
    if not clean_keys and not str(subject or "").strip():
        return None
    receipt_seed = json.dumps(
        [content_key, clean_keys, clean_recipients],
        separators=(",", ":"),
    )
    receipt_hex = hashlib.sha256(receipt_seed.encode("utf-8")).hexdigest()
    receipt_key = f"fallback:{receipt_hex}"
    receipt_id = -max(1, int(receipt_hex[:15], 16))
    try:
        with _locked_uncertain_registry(db_path) as registry_path:
            entries = _load_uncertain_registry_unlocked(registry_path)
            if entries is None:
                # Corruption is not overwritten silently; callers must remain
                # fail-closed and surface that no new receipt was persisted.
                return None
            entries[receipt_key] = {
                "quarantined_at": timestamp,
                "delivery_dedupe_keys": clean_keys,
                "content_key": content_key,
                "recipient_count": len(clean_recipients),
                "mail_class": str(mail_class or "info").strip().lower() or "info",
                "error": str(error or "")[:500],
            }
            _write_uncertain_registry_unlocked(registry_path, entries)
        return receipt_id
    except Exception:
        return None


def record_tracker_acceptance_pending(
    intent_key: str,
    accepted_recipient_keys: Optional[List[str]],
    *,
    accepted_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[str]:
    """Journal an accepted SMTP cohort before tracker finalization.

    Only the opaque intent and SHA-256 recipient keys are persisted; email
    addresses and message content never enter this cross-database journal.
    """
    intent = str(intent_key or "").strip()[:240]
    recipient_keys = sorted({
        str(value).strip().lower()
        for value in (accepted_recipient_keys or [])
        if len(str(value).strip()) == 64
        and all(char in "0123456789abcdefABCDEF" for char in str(value).strip())
    })
    if not intent or not recipient_keys:
        return None
    try:
        timestamp = float(accepted_at if accepted_at is not None else time.time())
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(timestamp) or timestamp <= 0:
        return None
    receipt = hashlib.sha256(intent.encode("utf-8")).hexdigest()
    try:
        with _locked_tracker_acceptance_journal(db_path) as journal_path:
            entries = _load_uncertain_registry_unlocked(journal_path)
            if entries is None:
                return None
            prior = entries.get(receipt) if isinstance(entries.get(receipt), dict) else {}
            merged_keys = sorted({
                *recipient_keys,
                *[
                    str(value).strip().lower()
                    for value in (prior.get("accepted_recipient_keys") or [])
                    if len(str(value).strip()) == 64
                ],
            })
            try:
                prior_accepted_at = float(prior.get("accepted_at"))
            except (TypeError, ValueError, OverflowError):
                prior_accepted_at = timestamp
            if not math.isfinite(prior_accepted_at) or prior_accepted_at <= 0:
                prior_accepted_at = timestamp
            entries[receipt] = {
                "intent_key": intent,
                "accepted_recipient_keys": merged_keys,
                # Partial recipient retries can append to one intent. Preserve
                # the first provider acceptance, independent of write order.
                "accepted_at": min(prior_accepted_at, timestamp),
                "status": "pending",
                "updated_at": time.time(),
            }
            _write_uncertain_registry_unlocked(journal_path, entries)
        return receipt
    except Exception:
        return None


def load_tracker_acceptance_pending(
    *, db_path: Optional[str] = None
) -> Optional[List[Dict[str, Any]]]:
    """Load acceptance receipts awaiting idempotent tracker activation."""
    try:
        with _locked_tracker_acceptance_journal(db_path) as journal_path:
            entries = _load_uncertain_registry_unlocked(journal_path)
        if entries is None:
            return None
        pending: List[Dict[str, Any]] = []
        for receipt, entry in entries.items():
            if not isinstance(entry, dict):
                return None
            if str(entry.get("status") or "pending") != "pending":
                continue
            pending.append({"receipt": receipt, **entry})
        return sorted(
            pending,
            key=lambda item: (float(item.get("accepted_at") or 0), str(item.get("receipt"))),
        )
    except Exception:
        return None


def mark_tracker_acceptance_done(
    intent_key: str,
    *,
    completed_at: Optional[float] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Acknowledge the journal only after tracker activation is confirmed."""
    intent = str(intent_key or "").strip()[:240]
    if not intent:
        return False
    receipt = hashlib.sha256(intent.encode("utf-8")).hexdigest()
    try:
        with _locked_tracker_acceptance_journal(db_path) as journal_path:
            entries = _load_uncertain_registry_unlocked(journal_path)
            if entries is None or not isinstance(entries.get(receipt), dict):
                return False
            entry = dict(entries[receipt])
            if str(entry.get("intent_key") or "") != intent:
                return False
            entry["status"] = "done"
            entry["completed_at"] = float(
                completed_at if completed_at is not None else time.time()
            )
            entries[receipt] = entry
            _write_uncertain_registry_unlocked(journal_path, entries)
        return True
    except Exception:
        return False


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
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mail_outbox)")}
            if "dedupe_key" not in columns:
                conn.execute("ALTER TABLE mail_outbox ADD COLUMN dedupe_key TEXT")
            if "delivery_dedupe_keys_json" not in columns:
                conn.execute(
                    "ALTER TABLE mail_outbox ADD COLUMN "
                    "delivery_dedupe_keys_json TEXT NOT NULL DEFAULT '[]'"
                )
        return True
    except Exception:
        return False


def _row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    try:
        item["recipients"] = json.loads(item.pop("recipients_json") or "[]")
    except Exception:
        item["recipients"] = []
    try:
        item["delivery_dedupe_keys"] = json.loads(
            item.pop("delivery_dedupe_keys_json", "[]") or "[]"
        )
    except Exception:
        item["delivery_dedupe_keys"] = []
    return item


def _content_dedupe_key(
    subject: str,
    body_html: str,
    recipients: List[str],
    mail_class: str,
    telegram_text: str,
    delivery_dedupe_keys: List[str],
) -> str:
    payload = {
        "subject": subject,
        "body_html": body_html,
        "recipients": recipients,
        "mail_class": mail_class,
        "telegram_text": telegram_text,
    }
    if delivery_dedupe_keys:
        payload["delivery_dedupe_keys"] = delivery_dedupe_keys
    return hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def enqueue(
    subject: str,
    body_html: str,
    recipients: List[str],
    *,
    mail_class: str = "info",
    telegram_text: str = "",
    delivery_dedupe_keys: Optional[List[str]] = None,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Mail in die Warteschlange legen. Rueckgabe: Outbox-ID oder None.

    Dedupe: identischer Inhalt + Empfaenger bereits pending/sending -> vorhandene ID, kein
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
        normalized_class = str(mail_class or "info").strip().lower() or "info"
        normalized_body = str(body_html or "")
        normalized_telegram = str(telegram_text or "")
        clean_delivery_keys = sorted({
            str(value).strip()[:240]
            for value in (delivery_dedupe_keys or [])
            if str(value).strip() and "@" not in str(value)
        })
        dedupe_key = _content_dedupe_key(
            subject,
            normalized_body,
            clean_recipients,
            normalized_class,
            normalized_telegram,
            clean_delivery_keys,
        )
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(mail_outbox)")}
            if "dedupe_key" not in columns:
                conn.execute("ALTER TABLE mail_outbox ADD COLUMN dedupe_key TEXT")
            if "delivery_dedupe_keys_json" not in columns:
                conn.execute(
                    "ALTER TABLE mail_outbox ADD COLUMN "
                    "delivery_dedupe_keys_json TEXT NOT NULL DEFAULT '[]'"
                )
            dup = conn.execute(
                "SELECT id FROM mail_outbox "
                "WHERE status IN "
                "('pending','sending','delivering','uncertain') "
                "AND dedupe_key=? "
                "ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if dup is not None:
                return int(dup["id"])
            cur = conn.execute(
                "INSERT INTO mail_outbox "
                "(created_at, subject, body_html, recipients_json, mail_class, "
                " telegram_text, status, attempts, next_attempt_at, expires_at, "
                " dedupe_key, delivery_dedupe_keys_json) "
                "VALUES (?,?,?,?,?,?, 'pending', 0, ?, ?, ?, ?)",
                (
                    now,
                    subject,
                    normalized_body,
                    json.dumps(clean_recipients),
                    normalized_class,
                    normalized_telegram,
                    now,  # erster Nachzustellversuch sofort beim naechsten Worker-Tick
                    now + ttl,
                    dedupe_key,
                    json.dumps(clean_delivery_keys, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid)
    except Exception:
        return None


def quarantine(
    subject: str,
    body_html: str,
    recipients: List[str],
    *,
    mail_class: str = "info",
    telegram_text: str = "",
    delivery_dedupe_keys: Optional[List[str]] = None,
    error: str = "delivery outcome unknown",
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> Optional[int]:
    """Persist an unknown DATA outcome without making it retryable.

    If identical content is already queued or leased, that row is atomically
    quarantined too: retrying it could duplicate the same external delivery.
    Positive IDs acknowledge a SQLite row; negative IDs acknowledge the
    durable fallback registry. Only ``None`` means persistence failed.
    """
    now = float(now if now is not None else time.time())
    normalized_subject = str(subject or "").strip()
    clean_recipients = sorted({
        str(value).strip().lower()
        for value in (recipients or [])
        if "@" in str(value)
    })
    if not normalized_subject or not clean_recipients:
        return None
    normalized_class = str(mail_class or "info").strip().lower() or "info"
    normalized_body = str(body_html or "")
    normalized_telegram = str(telegram_text or "")
    clean_delivery_keys = sorted({
        str(value).strip()[:240]
        for value in (delivery_dedupe_keys or [])
        if str(value).strip() and "@" not in str(value)
    })
    try:
        dedupe_key = _content_dedupe_key(
            normalized_subject,
            normalized_body,
            clean_recipients,
            normalized_class,
            normalized_telegram,
            clean_delivery_keys,
        )
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(mail_outbox)")
            }
            if "dedupe_key" not in columns:
                conn.execute("ALTER TABLE mail_outbox ADD COLUMN dedupe_key TEXT")
            if "delivery_dedupe_keys_json" not in columns:
                conn.execute(
                    "ALTER TABLE mail_outbox ADD COLUMN "
                    "delivery_dedupe_keys_json TEXT NOT NULL DEFAULT '[]'"
                )
            existing = conn.execute(
                "SELECT id FROM mail_outbox WHERE dedupe_key=? "
                "AND status IN ('pending','sending','delivering','uncertain') "
                "ORDER BY created_at DESC LIMIT 1",
                (dedupe_key,),
            ).fetchone()
            if existing is not None:
                item_id = int(existing["id"])
                conn.execute(
                    "UPDATE mail_outbox SET status='uncertain', last_error=?, "
                    "next_attempt_at=? WHERE id=?",
                    (str(error or "")[:500], now, item_id),
                )
                return item_id
            ttl = ttl_seconds_for(normalized_class)
            cur = conn.execute(
                "INSERT INTO mail_outbox "
                "(created_at, subject, body_html, recipients_json, mail_class, "
                "telegram_text, status, attempts, next_attempt_at, expires_at, "
                "last_error, dedupe_key, delivery_dedupe_keys_json) "
                "VALUES (?,?,?,?,?,?,'uncertain',0,?,?,?,?,?)",
                (
                    now,
                    normalized_subject,
                    normalized_body,
                    json.dumps(clean_recipients),
                    normalized_class,
                    normalized_telegram,
                    now,
                    now + ttl,
                    str(error or "")[:500],
                    dedupe_key,
                    json.dumps(clean_delivery_keys, separators=(",", ":")),
                ),
            )
            return int(cur.lastrowid)
    except Exception:
        return register_uncertain_delivery_keys(
            clean_delivery_keys,
            subject=normalized_subject,
            body_html=normalized_body,
            recipients=clean_recipients,
            mail_class=normalized_class,
            error=error,
            now=now,
            db_path=db_path,
        )


def has_uncertain_delivery_key(
    delivery_key: str,
    *,
    db_path: Optional[str] = None,
) -> bool:
    """Return whether a recipient/event delivery awaits manual resolution.

    This ledger intentionally has no time-based auto-expiry. An unknown SMTP
    DATA outcome never becomes safe to resend merely because time passed.
    """
    normalized = str(delivery_key or "").strip()
    if not normalized:
        return False
    try:
        with _locked_uncertain_registry(db_path) as registry_path:
            entries = _load_uncertain_registry_unlocked(registry_path)
        if entries is None:
            return True
        for entry in entries.values():
            if not isinstance(entry, dict):
                return True
            keys = entry.get("delivery_dedupe_keys") or []
            if isinstance(keys, list) and normalized in {
                str(value).strip() for value in keys
            }:
                return True
        if not init_db(db_path):
            return True
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            rows = conn.execute(
                "SELECT delivery_dedupe_keys_json FROM mail_outbox "
                "WHERE status='uncertain'"
            ).fetchall()
        for row in rows:
            try:
                keys = json.loads(row["delivery_dedupe_keys_json"] or "[]")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if isinstance(keys, list) and normalized in {
                str(value).strip() for value in keys
            }:
                return True
        return False
    except Exception:
        # A broken safety ledger cannot prove that retry is safe.
        return True


def _expire_overdue(conn: sqlite3.Connection, now: float) -> int:
    """Pendente, abgelaufene Eintraege auf 'expired' setzen. Anzahl zurueck."""
    cur = conn.execute(
        "UPDATE mail_outbox SET status='expired' "
        "WHERE status='pending' AND expires_at < ?",
        (now,),
    )
    return int(cur.rowcount or 0)


def _recover_abandoned_claims(conn: sqlite3.Connection, now: float) -> int:
    """Recover pre-DATA leases; quarantine post-DATA crash windows."""
    uncertain = conn.execute(
        "UPDATE mail_outbox SET status='uncertain', "
        "last_error='worker disappeared after delivery phase started' "
        "WHERE status='delivering' AND next_attempt_at <= ?",
        (now,),
    )
    pending = conn.execute(
        "UPDATE mail_outbox SET status='pending' "
        "WHERE status='sending' AND next_attempt_at <= ?",
        (now,),
    )
    return int(pending.rowcount or 0) + int(uncertain.rowcount or 0)


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


def mark_delivering(
    item_id: int,
    *,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Persist the post-claim, pre-DATA safety boundary.

    Once this phase is durable, a worker crash is quarantined rather than
    automatically resent because the external SMTP outcome cannot be known.
    """
    now = float(now if now is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute(
                "UPDATE mail_outbox SET status='delivering', "
                "next_attempt_at=? WHERE id=? AND status='sending'",
                (now + CLAIM_LEASE_SECONDS, int(item_id)),
            )
        return bool(cur.rowcount)
    except Exception:
        return False


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
                "WHERE id=? AND status='delivering'",
                (now, int(item_id)),
            )
        return bool(cur.rowcount)
    except Exception:
        return False


def mark_failed(
    item_id: int,
    error: str,
    *,
    pending_recipients: Optional[List[str]] = None,
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
                "SELECT * FROM mail_outbox WHERE id=?",
                (int(item_id),),
            ).fetchone()
            if row is None:
                return "pending"
            if str(row["status"]) not in {"sending", "delivering"}:
                return str(row["status"])
            clean_pending = sorted({
                str(value).strip().lower()
                for value in (pending_recipients or [])
                if "@" in str(value)
            })
            if clean_pending:
                try:
                    delivery_keys = json.loads(
                        row["delivery_dedupe_keys_json"] or "[]"
                    )
                except (IndexError, KeyError, TypeError, ValueError, json.JSONDecodeError):
                    delivery_keys = []
                reduced_dedupe_key = _content_dedupe_key(
                    str(row["subject"] or ""),
                    str(row["body_html"] or ""),
                    clean_pending,
                    str(row["mail_class"] or "info"),
                    str(row["telegram_text"] or ""),
                    delivery_keys if isinstance(delivery_keys, list) else [],
                )
                conn.execute(
                    "UPDATE mail_outbox SET recipients_json=?, dedupe_key=? "
                    "WHERE id=? AND status IN ('sending','delivering')",
                    (
                        json.dumps(clean_pending),
                        reduced_dedupe_key,
                        int(item_id),
                    ),
                )
            attempts = int(row["attempts"]) + 1
            if attempts >= MAX_ATTEMPTS:
                conn.execute(
                    "UPDATE mail_outbox SET status='dead', attempts=?, last_error=? "
                    "WHERE id=? AND status IN ('sending','delivering')",
                    (attempts, str(error or "")[:500], int(item_id)),
                )
                return "dead"
            delay = BACKOFF_SECONDS[min(attempts - 1, len(BACKOFF_SECONDS) - 1)]
            conn.execute(
                "UPDATE mail_outbox SET status='pending', attempts=?, "
                "last_error=?, next_attempt_at=? "
                "WHERE id=? AND status IN ('sending','delivering')",
                (attempts, str(error or "")[:500], now + delay, int(item_id)),
            )
            return "pending"
    except Exception:
        return "pending"


def mark_uncertain(
    item_id: int,
    error: str,
    *,
    now: Optional[float] = None,
    db_path: Optional[str] = None,
) -> bool:
    """Quarantine an unknown post-DATA outcome without automatic resend."""
    now = float(now if now is not None else time.time())
    try:
        with _connect(db_path) as conn:
            conn.executescript(_SCHEMA)
            cur = conn.execute(
                "UPDATE mail_outbox SET status='uncertain', last_error=?, "
                "next_attempt_at=? WHERE id=? "
                "AND status IN ('sending','delivering')",
                (str(error or "")[:500], now, int(item_id)),
            )
        return bool(cur.rowcount)
    except Exception:
        return False


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
        "sent": 0,
        "failed": 0,
        "expired": 0,
        "dead": 0,
        "uncertain": 0,
        "sent_rows": [],
    }
    if not outbox_enabled():
        return result
    now = float(now if now is not None else time.time())
    items, expired = _claim_due_items(now=now, limit=limit, db_path=db_path)
    result["expired"] = expired
    for item in items:
        if not mark_delivering(item["id"], now=now, db_path=db_path):
            # SMTP must not start before the durable delivery phase exists.
            # The still-``sending`` lease can be safely recovered later.
            result["failed"] += 1
            continue
        try:
            send_fn(item)
        except Exception as exc:
            if bool(getattr(exc, "suppress_retry", False)):
                if mark_uncertain(
                    item["id"], str(exc), now=now, db_path=db_path
                ):
                    result["uncertain"] += 1
                result["failed"] += 1
                continue
            pending_recipients = getattr(exc, "pending_recipients", None)
            new_status = mark_failed(
                item["id"],
                str(exc),
                pending_recipients=(
                    list(pending_recipients)
                    if isinstance(pending_recipients, (list, tuple, set))
                    else None
                ),
                now=now,
                db_path=db_path,
            )
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
                    "delivery_dedupe_keys": item.get(
                        "delivery_dedupe_keys", []
                    ),
                }
            )
        else:
            # SMTP returned success but its DB acknowledgement failed. Keep
            # this delivery out of automatic recovery; retrying may duplicate.
            if mark_uncertain(
                item["id"],
                "SMTP succeeded but sent acknowledgement failed",
                now=now,
                db_path=db_path,
            ):
                result["uncertain"] += 1
            result["failed"] += 1
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
        "pending": 0, "sending": 0, "delivering": 0, "queued": 0,
        "sent": 0, "expired": 0, "dead": 0, "uncertain": 0, "total": 0,
        "pending_with_errors": 0,
        "oldest_pending_age_seconds": None,
        "oldest_pending_created_at": None,
        "tracker_acceptance_pending_count": 0,
        "tracker_acceptance_oldest_at": None,
        "tracker_acceptance_available": True,
        "last_error": "",
        "error": "",
    }
    fallback_uncertain = 0
    try:
        with _locked_uncertain_registry(db_path) as registry_path:
            fallback_entries = _load_uncertain_registry_unlocked(registry_path)
        if fallback_entries is None:
            counts["error"] = "uncertain fallback registry unreadable"
        else:
            fallback_uncertain = len(fallback_entries)
            counts["uncertain"] = fallback_uncertain
            counts["total"] = fallback_uncertain
    except Exception as exc:
        counts["error"] = f"uncertain fallback registry unavailable: {exc}"
    # SMTP acceptance can be journaled here when the tracker DB is unavailable.
    # Keep this observable even if the normal retry outbox is disabled: the
    # journal itself deliberately does not depend on MAIL_OUTBOX_ENABLED.
    acceptance_pending = load_tracker_acceptance_pending(db_path=db_path)
    if acceptance_pending is None:
        counts["tracker_acceptance_available"] = False
    else:
        counts["tracker_acceptance_pending_count"] = len(acceptance_pending)
        accepted_times = []
        for entry in acceptance_pending:
            try:
                accepted_at = float(entry.get("accepted_at"))
            except (AttributeError, TypeError, ValueError, OverflowError):
                continue
            if math.isfinite(accepted_at) and accepted_at > 0:
                accepted_times.append(accepted_at)
        if accepted_times:
            counts["tracker_acceptance_oldest_at"] = min(accepted_times)
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
                status = str(row["status"])
                counts[status] = int(row["n"]) + (
                    fallback_uncertain if status == "uncertain" else 0
                )
                counts["total"] += int(row["n"])
            counts["queued"] = (
                int(counts.get("pending", 0))
                + int(counts.get("sending", 0))
                + int(counts.get("delivering", 0))
            )
            pending = conn.execute(
                "SELECT MIN(created_at) AS oldest, "
                "SUM(CASE WHEN last_error <> '' THEN 1 ELSE 0 END) AS with_errors "
                "FROM mail_outbox "
                "WHERE status IN ('pending','sending','delivering')"
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
        counts["available"] = not bool(counts["error"])
    except Exception as exc:
        counts["error"] = str(exc) or counts["error"]
    return counts
