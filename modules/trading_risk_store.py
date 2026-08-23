"""Durable coordination/evidence store for :mod:`modules.trading_risk`.

The module intentionally owns no broker connection and no trading workflow.
It only keeps immutable broker evidence and serializes risk admission for a
paper account in a small, separately configured SQLite database.
"""

from __future__ import annotations

from datetime import datetime, timezone
import errno
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import time
from typing import Any, Iterable, Mapping, Optional
import uuid

from modules.trading_risk import (
    DEFAULT_RISK_POLICY,
    aggregate_stop_risk,
    derive_intent_outcome,
    evaluate_projected_risk,
)


_TERMINAL_RESERVATIONS = {
    "CANCELLED",
    "CANCELED",
    "REJECTED",
    "EXPIRED",
    "RELEASED",
    "COMPLETED",
    "DONE",
}
_UNSET = object()
_CANONICAL_SIDES = {
    "BOT": "BOT",
    "BUY": "BOT",
    "SLD": "SLD",
    "SELL": "SLD",
}
_ORDER_ROLES = {"PARENT", "STOP", "TARGET"}
_ACTIVE_ORDER_STATUSES = {
    "PENDINGSUBMIT",
    "PRESUBMITTED",
    "SUBMITTED",
    "PENDINGCANCEL",
}
_EXECUTION_WRITE_LOCK_PROTOCOL = 1
_EXECUTION_WRITE_ID = re.compile(r"^[0-9a-f]{32}$")


class _ExecutionWriteLock:
    """One-byte OS lock proving that a specific broker write is still live."""

    def __init__(self, path: Path):
        self.path = path
        self._handle: Any = None
        self.failure_reason: Optional[str] = None
        self.busy = False

    def acquire(self) -> bool:
        self.busy = False
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if self.path.parent.is_symlink():
                raise OSError("execution lock directory is a symlink")
            handle = self.path.open("a+b", buffering=0)
            os.set_inheritable(handle.fileno(), False)
            handle.seek(0, os.SEEK_END)
            if handle.tell() < 1:
                handle.write(b"\0")
            handle.seek(0)
        except (OSError, BlockingIOError) as exc:
            try:
                handle.close()
            except (NameError, OSError):
                pass
            self.failure_reason = str(exc)[:160]
            return False
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            elif os.name == "posix":
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise OSError(f"unsupported execution lock backend: {os.name}")
        except (OSError, BlockingIOError) as exc:
            handle.close()
            if os.name == "posix":
                self.busy = exc.errno in {errno.EACCES, errno.EAGAIN}
            elif os.name == "nt":
                self.busy = (
                    exc.errno in {errno.EACCES, errno.EAGAIN}
                    or getattr(exc, "winerror", None) in {32, 33}
                )
            else:
                self.busy = False
            self.failure_reason = str(exc)[:160]
            return False
        self._handle = handle
        self.failure_reason = None
        return True

    def release(self, *, remove: bool = False) -> None:
        handle = self._handle
        self._handle = None
        if handle is not None:
            try:
                handle.seek(0)
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                elif os.name == "posix":
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                handle.close()
        if remove:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass


def _text(value: Any) -> str:
    return str(value or "").strip()


def _token(value: Any) -> str:
    return _text(value).upper()


def _finite(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _positive(value: Any) -> Optional[float]:
    number = _finite(value)
    return number if number is not None and number > 0 else None


def _order_price(value: Any) -> Optional[float]:
    """Canonicalize IB unused-price sentinels without tolerating real drift."""
    number = _finite(value)
    if number is None or number <= 0 or number > 1e100:
        return None
    return number


def _utc_datetime(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _utc_iso(value: Any, *, allow_none: bool = False) -> Optional[str]:
    if value is None and allow_none:
        return None
    parsed = _utc_datetime(value)
    return parsed.isoformat() if parsed is not None else None


def _paper_account(value: Any) -> bool:
    return re.fullmatch(r"DU[0-9]+", _token(value)) is not None


def _json_value(value: Any) -> Optional[Any]:
    """Return a JSON round-trip only when every numeric value is finite."""
    try:
        encoded = json.dumps(
            value,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return json.loads(encoded)
    except (TypeError, ValueError):
        return None


def _canonical_json(value: Any) -> Optional[str]:
    normalized = _json_value(value)
    if normalized is None:
        return None
    return json.dumps(normalized, sort_keys=True, separators=(",", ":"))


def _digest(encoded: str) -> str:
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _scalar_key(value: Any) -> str:
    return _text(value)


class TradingRiskStore:
    """SQLite-backed evidence and fenced reservation store.

    Each public operation uses its own connection so instances remain safe to
    pickle/use from Windows ``spawn`` workers.  Risk admission uses a
    ``BEGIN IMMEDIATE`` transaction to serialize separate submit leases.
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        if self.db_path == ":memory:":
            self._execution_lock_dir = (
                Path(os.environ.get("TEMP") or ".")
                / f"alpha-station-memory-{uuid.uuid4().hex}.execution-writes"
            )
        else:
            database_path = Path(self.db_path).resolve()
            self._execution_lock_dir = database_path.with_name(
                database_path.name + ".execution-writes"
            )
        self._held_execution_write_locks: dict[str, _ExecutionWriteLock] = {}
        self.initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(
            self.db_path,
            timeout=20,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=15000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def initialize(self) -> None:
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            existing_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(trading_risk_intents)"
                ).fetchall()
            }
            if existing_columns and "order_ref" not in existing_columns:
                raise RuntimeError(
                    "Incompatible trading-risk database schema; manual migration required"
                )
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS trading_risk_intents (
                    setup_id TEXT PRIMARY KEY,
                    account TEXT NOT NULL,
                    con_id TEXT NOT NULL,
                    order_ref TEXT NOT NULL,
                    intent_json TEXT NOT NULL,
                    intent_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (account, con_id, order_ref)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_intent_orders (
                    account TEXT NOT NULL,
                    con_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    setup_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    branch INTEGER NOT NULL,
                    mapping_json TEXT NOT NULL,
                    mapping_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (account, con_id, order_id),
                    UNIQUE (setup_id, role, branch),
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_fill_events (
                    exec_id TEXT PRIMARY KEY,
                    ledger_sequence INTEGER NOT NULL UNIQUE,
                    setup_id TEXT,
                    account TEXT NOT NULL,
                    con_id TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_fill_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exec_id TEXT NOT NULL,
                    incoming_setup_id TEXT,
                    incoming_account TEXT,
                    incoming_con_id TEXT,
                    incoming_order_id TEXT,
                    incoming_perm_id INTEGER,
                    incoming_client_id INTEGER,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (exec_id, payload_hash),
                    FOREIGN KEY (exec_id) REFERENCES trading_risk_fill_events(exec_id),
                    FOREIGN KEY (incoming_setup_id) REFERENCES trading_risk_intents(setup_id)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_rejected_fill_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    account TEXT,
                    exec_id TEXT,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trading_risk_outcomes (
                    setup_id TEXT PRIMARY KEY,
                    state TEXT NOT NULL,
                    outcome_json TEXT NOT NULL,
                    outcome_hash TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_evidence_conflicts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    setup_id TEXT NOT NULL,
                    conflict_kind TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (setup_id, conflict_kind, payload_hash),
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id)
                );

                CREATE TABLE IF NOT EXISTS trading_risk_leases (
                    lease_key TEXT PRIMARY KEY,
                    owner_token TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trading_risk_execution_state (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    generation INTEGER NOT NULL CHECK (generation >= 0),
                    armed INTEGER NOT NULL CHECK (armed IN (0, 1)),
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS trading_risk_execution_writes (
                    write_id TEXT PRIMARY KEY,
                    generation INTEGER NOT NULL CHECK (generation >= 0),
                    started_at TEXT NOT NULL,
                    lock_protocol_version INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN',
                    orphaned_generation INTEGER,
                    orphaned_at TEXT,
                    reconciled_at TEXT,
                    operation_kind TEXT NOT NULL DEFAULT 'UNSCOPED',
                    account TEXT,
                    setup_id TEXT,
                    order_id INTEGER,
                    order_ref TEXT
                );

                CREATE TABLE IF NOT EXISTS trading_risk_reservations (
                    reservation_id TEXT PRIMARY KEY,
                    setup_id TEXT NOT NULL,
                    account TEXT NOT NULL,
                    order_ref TEXT NOT NULL,
                    status TEXT NOT NULL,
                    reservation_json TEXT NOT NULL,
                    reservation_hash TEXT NOT NULL,
                    lease_key TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id)
                );
                CREATE TABLE IF NOT EXISTS trading_risk_terminal_evidence (
                    setup_id TEXT PRIMARY KEY,
                    reservation_id TEXT NOT NULL,
                    evidence_json TEXT NOT NULL,
                    evidence_hash TEXT NOT NULL,
                    observed_at TEXT NOT NULL,
                    lease_key TEXT NOT NULL,
                    fence_token INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (setup_id) REFERENCES trading_risk_intents(setup_id),
                    FOREIGN KEY (reservation_id)
                        REFERENCES trading_risk_reservations(reservation_id)
                );
                CREATE UNIQUE INDEX IF NOT EXISTS trading_risk_one_active_setup
                    ON trading_risk_reservations(account, setup_id, order_ref)
                    WHERE status IN ('SUBMITTING', 'RECONCILE_REQUIRED');
                CREATE UNIQUE INDEX IF NOT EXISTS trading_risk_one_authorization_per_setup
                    ON trading_risk_reservations(account, setup_id, order_ref);
                """
            )
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT OR IGNORE INTO trading_risk_execution_state
                    (singleton, generation, armed, updated_at)
                VALUES (1, 0, 0, ?)
                """,
                (datetime.now(timezone.utc).isoformat(),),
            )
            fill_conflict_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(trading_risk_fill_conflicts)"
                ).fetchall()
            }
            conflict_identity_columns = {
                "incoming_account": "TEXT",
                "incoming_con_id": "TEXT",
                "incoming_order_id": "TEXT",
                "incoming_perm_id": "INTEGER",
                "incoming_client_id": "INTEGER",
            }
            for column, column_type in conflict_identity_columns.items():
                if column not in fill_conflict_columns:
                    connection.execute(
                        f"ALTER TABLE trading_risk_fill_conflicts "
                        f"ADD COLUMN {column} {column_type}"
                    )
            execution_write_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(trading_risk_execution_writes)"
                ).fetchall()
            }
            execution_write_migrations = {
                "lock_protocol_version": "INTEGER NOT NULL DEFAULT 0",
                "status": "TEXT NOT NULL DEFAULT 'LEGACY_UNKNOWN'",
                "orphaned_generation": "INTEGER",
                "orphaned_at": "TEXT",
                "reconciled_at": "TEXT",
                "operation_kind": "TEXT NOT NULL DEFAULT 'UNSCOPED'",
                "account": "TEXT",
                "setup_id": "TEXT",
                "order_id": "INTEGER",
                "order_ref": "TEXT",
            }
            for column, column_type in execution_write_migrations.items():
                if column not in execution_write_columns:
                    connection.execute(
                        f"ALTER TABLE trading_risk_execution_writes "
                        f"ADD COLUMN {column} {column_type}"
                    )
            self._reap_orphaned_execution_writes(connection)
            legacy_conflicts = connection.execute(
                """
                SELECT id, payload_json, payload_hash
                FROM trading_risk_fill_conflicts
                WHERE incoming_account IS NULL
                   OR incoming_con_id IS NULL
                   OR incoming_order_id IS NULL
                   OR incoming_perm_id IS NULL
                   OR incoming_client_id IS NULL
                """
            ).fetchall()
            for conflict in legacy_conflicts:
                try:
                    raw_conflict = json.loads(conflict["payload_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    raw_conflict = None
                conflict_payload = self._fill_payload(raw_conflict)
                conflict_encoded = (
                    _canonical_json(conflict_payload)
                    if conflict_payload is not None
                    else None
                )
                if (
                    conflict_payload is None
                    or conflict_encoded is None
                    or _digest(conflict_encoded) != conflict["payload_hash"]
                ):
                    continue
                connection.execute(
                    """
                    UPDATE trading_risk_fill_conflicts
                    SET incoming_account=?, incoming_con_id=?, incoming_order_id=?,
                        incoming_perm_id=?, incoming_client_id=?
                    WHERE id=?
                    """,
                    (
                        conflict_payload["account"],
                        _scalar_key(conflict_payload["con_id"]),
                        _scalar_key(conflict_payload["order_id"]),
                        conflict_payload["perm_id"],
                        conflict_payload["client_id"],
                        conflict["id"],
                    ),
                )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS
                    trading_risk_fill_conflict_incoming_identity
                ON trading_risk_fill_conflicts(
                    incoming_account, incoming_con_id, incoming_order_id,
                    incoming_perm_id, incoming_client_id
                )
                """
            )
            fill_columns = {
                row["name"]
                for row in connection.execute(
                    "PRAGMA table_info(trading_risk_fill_events)"
                ).fetchall()
            }
            if "ledger_sequence" not in fill_columns:
                connection.execute(
                    """
                    ALTER TABLE trading_risk_fill_events
                    ADD COLUMN ledger_sequence INTEGER
                    """
                )
                legacy_rows = connection.execute(
                    """
                    SELECT rowid FROM trading_risk_fill_events
                    ORDER BY rowid
                    """
                ).fetchall()
                for sequence, row in enumerate(legacy_rows, start=1):
                    connection.execute(
                        """
                        UPDATE trading_risk_fill_events SET ledger_sequence=?
                        WHERE rowid=?
                        """,
                        (sequence, row["rowid"]),
                    )
            invalid_sequence = connection.execute(
                """
                SELECT 1 FROM trading_risk_fill_events
                WHERE ledger_sequence IS NULL
                   OR typeof(ledger_sequence) != 'integer'
                   OR ledger_sequence <= 0
                LIMIT 1
                """
            ).fetchone()
            duplicate_sequence = connection.execute(
                """
                SELECT 1 FROM trading_risk_fill_events
                GROUP BY ledger_sequence HAVING COUNT(*) > 1 LIMIT 1
                """
            ).fetchone()
            if invalid_sequence is not None or duplicate_sequence is not None:
                raise RuntimeError(
                    "Incompatible trading-risk fill sequence; manual migration required"
                )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS
                    trading_risk_fill_ledger_sequence
                ON trading_risk_fill_events(ledger_sequence)
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS
                    trading_risk_fill_sequence_immutable
                BEFORE UPDATE OF ledger_sequence ON trading_risk_fill_events
                WHEN NEW.ledger_sequence IS NOT OLD.ledger_sequence
                BEGIN
                    SELECT RAISE(ABORT, 'ledger_sequence_immutable');
                END
                """
            )
            connection.execute(
                """
                CREATE TRIGGER IF NOT EXISTS
                    trading_risk_fill_sequence_required
                BEFORE INSERT ON trading_risk_fill_events
                WHEN NEW.ledger_sequence IS NULL
                  OR typeof(NEW.ledger_sequence) != 'integer'
                  OR NEW.ledger_sequence <= 0
                BEGIN
                    SELECT RAISE(ABORT, 'ledger_sequence_invalid');
                END
                """
            )
            self._audit_persisted_broker_visible_state(connection)
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def _audit_persisted_broker_visible_state(
        self, connection: sqlite3.Connection
    ) -> None:
        """Quarantine legacy visibility claims lacking immutable broker proof."""
        global_perm_owners: dict[int, list[tuple[str, str, str]]] = {}
        for persisted_mapping in connection.execute(
            """
            SELECT setup_id, account, order_id, mapping_json
            FROM trading_risk_intent_orders
            """
        ).fetchall():
            try:
                raw_global_mapping = json.loads(persisted_mapping["mapping_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            raw_perm = (
                raw_global_mapping.get("perm_id")
                if isinstance(raw_global_mapping, Mapping)
                else None
            )
            if type(raw_perm) is int and raw_perm > 0:
                global_perm_owners.setdefault(raw_perm, []).append(
                    (
                        persisted_mapping["setup_id"],
                        persisted_mapping["account"],
                        persisted_mapping["order_id"],
                    )
                )
        visible_rows = connection.execute(
            """
            SELECT r.reservation_id, r.setup_id, r.account, r.order_ref,
                   r.reservation_json, r.reservation_hash,
                   i.intent_json, i.intent_hash
            FROM trading_risk_reservations AS r
            JOIN trading_risk_intents AS i ON i.setup_id=r.setup_id
            WHERE r.status='BROKER_VISIBLE'
            """
        ).fetchall()
        for row in visible_rows:
            reasons: set[str] = set()
            try:
                reservation = json.loads(row["reservation_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                reservation = None
                reasons.add("reservation_json_invalid")
            try:
                intent_raw = json.loads(row["intent_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                intent_raw = None
                reasons.add("intent_json_invalid")

            intent = self._intent_payload(intent_raw)
            intent_encoded = _canonical_json(intent) if intent is not None else None
            if (
                intent is None
                or intent_encoded is None
                or _digest(intent_encoded) != row["intent_hash"]
                or _text(intent.get("setup_id")) != row["setup_id"]
                or _token(intent.get("account")) != _token(row["account"])
                or _text(intent.get("order_ref")) != _text(row["order_ref"])
            ):
                reasons.add("intent_invalid")

            reservation_encoded = (
                _canonical_json(reservation)
                if isinstance(reservation, Mapping)
                else None
            )
            reservation_base = None
            if isinstance(reservation, Mapping):
                required_reservation_fields = (
                    "reservation_id",
                    "setup_id",
                    "order_ref",
                    "account",
                    "con_id",
                    "direction",
                    "quantity",
                    "entry",
                    "stop",
                    "group_key",
                    "group_verified",
                )
                reservation_candidate = {
                    key: reservation.get(key) for key in required_reservation_fields
                }
                reservation_candidate["status"] = "SUBMITTING"
                reservation_base = self._reservation_payload(
                    reservation_candidate, intent or {}
                )
            if (
                reservation_encoded is None
                or _digest(reservation_encoded) != row["reservation_hash"]
                or reservation_base is None
                or _text(reservation.get("reservation_id")) != row["reservation_id"]
                or _text(reservation.get("setup_id")) != row["setup_id"]
                or _token(reservation.get("account")) != _token(row["account"])
                or _text(reservation.get("order_ref")) != _text(row["order_ref"])
                or _token(reservation.get("status")) != "BROKER_VISIBLE"
            ):
                reasons.add("reservation_invalid")

            mapping_rows = connection.execute(
                """
                SELECT account, con_id, order_id, role, branch,
                       mapping_json, mapping_hash
                FROM trading_risk_intent_orders
                WHERE setup_id=? ORDER BY branch, role
                """,
                (row["setup_id"],),
            ).fetchall()
            mappings: list[dict[str, Any]] = []
            for mapping_row in mapping_rows:
                try:
                    raw_mapping = json.loads(mapping_row["mapping_json"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    reasons.add("mapping_invalid")
                    continue
                mapping = self._order_payload(raw_mapping)
                mapping_encoded = (
                    _canonical_json(mapping) if mapping is not None else None
                )
                if (
                    mapping is None
                    or mapping_encoded is None
                    or _digest(mapping_encoded) != mapping_row["mapping_hash"]
                    or _token(mapping.get("account")) != _token(mapping_row["account"])
                    or _scalar_key(mapping.get("con_id"))
                    != _scalar_key(mapping_row["con_id"])
                    or _scalar_key(mapping.get("order_id"))
                    != _scalar_key(mapping_row["order_id"])
                    or _token(mapping.get("role")) != _token(mapping_row["role"])
                    or int(mapping.get("branch", 0)) != int(mapping_row["branch"])
                ):
                    reasons.add("mapping_invalid")
                    continue
                mappings.append(mapping)

            allocations = intent.get("allocations") if isinstance(intent, Mapping) else None
            branch_count = len(allocations) if isinstance(allocations, list) else 0
            required_geometry = {
                (branch, role)
                for branch in range(1, branch_count + 1)
                for role in ("PARENT", "STOP", "TARGET")
            }
            actual_geometry = {
                (int(mapping["branch"]), _token(mapping["role"]))
                for mapping in mappings
            }
            if not required_geometry or actual_geometry != required_geometry:
                reasons.add("mapping_geometry_incomplete")

            perm_ids = [int(_finite(mapping.get("perm_id")) or 0) for mapping in mappings]
            if (
                any(perm_id <= 0 for perm_id in perm_ids)
                or len(perm_ids) != len(set(perm_ids))
                or any(
                    len(global_perm_owners.get(perm_id, [])) != 1
                    for perm_id in perm_ids
                    if perm_id > 0
                )
            ):
                reasons.add("mapping_perm_identity_invalid")

            mapping_by_geometry = {
                (int(mapping["branch"]), _token(mapping["role"])): mapping
                for mapping in mappings
            }
            if isinstance(intent, Mapping) and isinstance(allocations, list):
                direction = _token(intent.get("direction"))
                entry_action = "BUY" if direction == "LONG" else "SELL"
                exit_action = "SELL" if direction == "LONG" else "BUY"
                for mapping in mappings:
                    role = _token(mapping["role"])
                    branch = int(mapping["branch"])
                    suffix = {"PARENT": "P", "STOP": "S", "TARGET": "T"}[role]
                    expected_ref = f"{_text(intent.get('order_ref'))}-{suffix}{branch}"
                    expected_action = entry_action if role == "PARENT" else exit_action
                    expected_quantity = (
                        _positive(allocations[branch - 1])
                        if 0 < branch <= len(allocations)
                        else None
                    )
                    expected_aux = (
                        _positive(intent.get("entry"))
                        if role == "PARENT"
                        else _positive(intent.get("stop"))
                        if role == "STOP"
                        else None
                    )
                    expected_limit = (
                        _positive(intent.get("stop_limit"))
                        if role == "PARENT"
                        else _positive(
                            intent.get("tp1") if branch == 1 else intent.get("tp2")
                        )
                        if role == "TARGET"
                        else None
                    )
                    parent = mapping_by_geometry.get((branch, "PARENT"))
                    identity_valid = (
                        _token(mapping.get("account")) == _token(intent.get("account"))
                        and _scalar_key(mapping.get("con_id"))
                        == _scalar_key(intent.get("con_id"))
                        and _text(mapping.get("order_ref")) == expected_ref
                        and _token(mapping.get("action")) == expected_action
                        and expected_quantity is not None
                        and math.isclose(
                            float(mapping["quantity"]),
                            expected_quantity,
                            rel_tol=0,
                            abs_tol=1e-9,
                        )
                        and (
                            (role == "PARENT" and int(mapping["parent_order_id"]) == 0)
                            or (
                                role != "PARENT"
                                and parent is not None
                                and _scalar_key(mapping.get("parent_order_id"))
                                == _scalar_key(parent.get("order_id"))
                            )
                        )
                    )
                    if expected_aux is not None:
                        identity_valid = identity_valid and math.isclose(
                            float(mapping["aux_price"]),
                            expected_aux,
                            rel_tol=0,
                            abs_tol=1e-9,
                        )
                    if expected_limit is not None:
                        identity_valid = identity_valid and math.isclose(
                            float(mapping["limit_price"]),
                            expected_limit,
                            rel_tol=0,
                            abs_tol=1e-9,
                        )
                    if not identity_valid:
                        reasons.add("mapping_intent_mismatch")

            broker_ids = (
                reservation.get("broker_order_ids")
                if isinstance(reservation, Mapping)
                else None
            )
            if not isinstance(broker_ids, list):
                reasons.add("broker_order_ids_invalid")
            else:
                normalized_broker_ids = []
                for value in broker_ids:
                    numeric = _finite(value)
                    if numeric is None or numeric <= 0 or int(numeric) != numeric:
                        reasons.add("broker_order_ids_invalid")
                        break
                    normalized_broker_ids.append(int(numeric))
                mapped_order_ids = {
                    int(_finite(mapping.get("order_id")) or 0) for mapping in mappings
                }
                if (
                    len(normalized_broker_ids) != len(set(normalized_broker_ids))
                    or set(normalized_broker_ids) != mapped_order_ids
                ):
                    reasons.add("broker_order_ids_invalid")

            ack = (
                reservation.get("broker_ack_evidence")
                if isinstance(reservation, Mapping)
                else None
            )
            ack_encoded = _canonical_json(ack) if isinstance(ack, Mapping) else None
            if (
                ack_encoded is None
                or _text(reservation.get("broker_ack_evidence_hash"))
                != _digest(ack_encoded)
                or _utc_datetime(ack.get("observed_at")) is None
                or not isinstance(ack.get("orders"), list)
            ):
                reasons.add("broker_ack_evidence_invalid")
                ack_orders: list[Any] = []
            else:
                ack_orders = ack["orders"]

            if any(not isinstance(order, Mapping) for order in ack_orders):
                reasons.add("broker_ack_evidence_invalid")
                ack_orders = []
            ack_ids = [_scalar_key(order.get("order_id")) for order in ack_orders]
            ack_perms = [int(_finite(order.get("perm_id")) or 0) for order in ack_orders]
            ack_identities = [
                (
                    _token(order.get("account")),
                    _scalar_key(order.get("client_id")),
                    _scalar_key(order.get("con_id")),
                    _scalar_key(order.get("order_id")),
                )
                for order in ack_orders
            ]
            ack_refs = [
                (_token(order.get("account")), _text(order.get("order_ref")))
                for order in ack_orders
            ]
            ack_by_id = {
                _scalar_key(order.get("order_id")): order for order in ack_orders
            }
            if (
                len(ack_orders) != len(mappings)
                or len(ack_ids) != len(set(ack_ids))
                or any(perm_id <= 0 for perm_id in ack_perms)
                or len(ack_perms) != len(set(ack_perms))
                or len(ack_identities) != len(set(ack_identities))
                or len(ack_refs) != len(set(ack_refs))
            ):
                reasons.add("broker_ack_evidence_invalid")
            acknowledged = {"PRESUBMITTED", "SUBMITTED"}
            for mapping in mappings:
                observed = ack_by_id.get(_scalar_key(mapping.get("order_id")))
                if (
                    observed is None
                    or _token(observed.get("status")) not in acknowledged
                    or _token(observed.get("role")) != _token(mapping.get("role"))
                    or _scalar_key(observed.get("branch"))
                    != _scalar_key(mapping.get("branch"))
                    or not self._order_geometry_matches(mapping, observed)
                ):
                    reasons.add("broker_ack_evidence_invalid")

            if not reasons:
                continue
            audit_payload = {
                "reservation_id": row["reservation_id"],
                "setup_id": row["setup_id"],
                "reasons": sorted(reasons),
            }
            audit_encoded = _canonical_json(audit_payload)
            assert audit_encoded is not None
            self._record_evidence_conflict(
                connection,
                row["setup_id"],
                "startup_broker_visible_evidence_invalid",
                audit_encoded,
                _digest(audit_encoded),
            )
            if isinstance(reservation, dict):
                quarantined = dict(reservation)
            else:
                quarantined = {
                    "reservation_id": row["reservation_id"],
                    "setup_id": row["setup_id"],
                    "account": _token(row["account"]),
                    "order_ref": row["order_ref"],
                }
                if isinstance(intent, Mapping):
                    quarantined.update(
                        {
                            "con_id": intent.get("con_id"),
                            "direction": intent.get("direction"),
                            "quantity": intent.get("quantity"),
                            "entry": intent.get("entry"),
                            "stop": intent.get("stop"),
                            "group_key": intent.get("group_key"),
                            "group_verified": intent.get("group_verified") is True,
                        }
                    )
            quarantined["status"] = "RECONCILE_REQUIRED"
            quarantined["transition_reason"] = (
                "startup_broker_visible_evidence_invalid"
            )
            quarantined_encoded = _canonical_json(quarantined)
            assert quarantined_encoded is not None
            connection.execute(
                """
                UPDATE trading_risk_reservations
                SET status='RECONCILE_REQUIRED', reservation_json=?,
                    reservation_hash=?, updated_at=?
                WHERE reservation_id=?
                """,
                (
                    quarantined_encoded,
                    _digest(quarantined_encoded),
                    datetime.now(timezone.utc).isoformat(),
                    row["reservation_id"],
                ),
            )

    @staticmethod
    def _intent_payload(intent: Any) -> Optional[dict[str, Any]]:
        if not isinstance(intent, Mapping):
            return None
        payload = _json_value(dict(intent))
        if not isinstance(payload, dict):
            return None
        direction = _token(payload.get("direction"))
        quantity = _positive(payload.get("quantity"))
        entry = _positive(payload.get("entry"))
        stop = _positive(payload.get("stop"))
        tp1 = _positive(payload.get("tp1"))
        tp2 = _positive(payload.get("tp2"))
        stop_limit = _positive(payload.get("stop_limit"))
        raw_allocations = payload.get("allocations")
        allocations: list[float] = []
        if isinstance(raw_allocations, list):
            for value in raw_allocations:
                allocation = _positive(value)
                if allocation is None:
                    allocations = []
                    break
                allocations.append(allocation)
        con_id = _finite(payload.get("con_id"))
        if (
            not _text(payload.get("setup_id"))
            or not _text(payload.get("order_ref"))
            or not _paper_account(payload.get("account"))
            or con_id is None
            or con_id <= 0
            or int(con_id) != con_id
            or direction not in {"LONG", "SHORT"}
            or quantity is None
            or entry is None
            or stop is None
            or tp1 is None
            or tp2 is None
            or stop_limit is None
            or not allocations
            or len(allocations) > 2
            or not math.isclose(sum(allocations), quantity, rel_tol=0, abs_tol=1e-9)
            or (direction == "LONG" and stop >= entry)
            or (direction == "SHORT" and stop <= entry)
            # A buy stop-limit must permit a fill at/above the stop trigger;
            # the mirrored sell stop-limit must permit a fill at/below it.
            or (direction == "LONG" and not (stop < entry <= stop_limit < tp1 <= tp2))
            or (direction == "SHORT" and not (stop > entry >= stop_limit > tp1 >= tp2))
        ):
            return None
        payload["account"] = _token(payload["account"])
        payload["con_id"] = int(con_id)
        payload["direction"] = direction
        payload["quantity"] = quantity
        payload["entry"] = entry
        payload["stop"] = stop
        payload["tp1"] = tp1
        payload["tp2"] = tp2
        payload["stop_limit"] = stop_limit
        payload["allocations"] = allocations
        payload["group_key"] = _token(payload.get("group_key"))
        payload["group_verified"] = payload.get("group_verified") is True
        return payload

    @staticmethod
    def _order_payload(mapping: Any) -> Optional[dict[str, Any]]:
        if not isinstance(mapping, Mapping):
            return None
        raw_perm_id = mapping.get("perm_id", 0)
        raw_client_id = mapping.get("client_id")
        payload = _json_value(dict(mapping))
        if not isinstance(payload, dict):
            return None
        role = _token(payload.get("role"))
        branch_value = _finite(payload.get("branch"))
        order_id = _finite(payload.get("order_id"))
        parent_order_id = _finite(payload.get("parent_order_id", 0))
        con_id = _finite(payload.get("con_id"))
        perm_id = _finite(payload.get("perm_id", 0))
        client_id = _finite(payload.get("client_id"))
        quantity = _positive(payload.get("quantity"))
        action = _token(payload.get("action"))
        order_type = " ".join(_token(payload.get("order_type")).split())
        aux_price = _order_price(payload.get("aux_price"))
        limit_price = _order_price(payload.get("limit_price"))
        oca_type = _finite(payload.get("oca_type"))
        tif = _token(payload.get("tif"))
        required_geometry = {
            "action", "order_type", "quantity", "aux_price", "limit_price",
            "oca_group", "oca_type", "tif", "transmit", "outside_rth",
            "client_id",
        }
        if (
            not _paper_account(payload.get("account"))
            or con_id is None
            or con_id <= 0
            or int(con_id) != con_id
            or order_id is None
            or order_id <= 0
            or int(order_id) != order_id
            or parent_order_id is None
            or parent_order_id < 0
            or int(parent_order_id) != parent_order_id
            or not _text(payload.get("order_ref"))
            or role not in _ORDER_ROLES
            or branch_value is None
            or branch_value <= 0
            or int(branch_value) != branch_value
            or not required_geometry.issubset(payload)
            or type(raw_perm_id) is not int
            or perm_id is None
            or perm_id < 0
            or int(perm_id) != perm_id
            or type(raw_client_id) is not int
            or client_id is None
            or client_id < 0
            or int(client_id) != client_id
            or quantity is None
            or action not in {"BUY", "SELL"}
            or order_type not in {"STP LMT", "STP", "LMT"}
            or aux_price is None and role in {"PARENT", "STOP"}
            or aux_price is not None and (aux_price <= 0 or role == "TARGET")
            or limit_price is None and role in {"PARENT", "TARGET"}
            or limit_price is not None and (limit_price <= 0 or role == "STOP")
            or oca_type is None
            or int(oca_type) != oca_type
            or not isinstance(payload.get("transmit"), bool)
            or payload.get("outside_rth") is not False
        ):
            return None
        payload["role"] = role
        payload["branch"] = int(branch_value)
        payload["account"] = _token(payload["account"])
        payload["con_id"] = int(con_id)
        payload["order_id"] = int(order_id)
        payload["parent_order_id"] = int(parent_order_id)
        payload["perm_id"] = int(perm_id)
        payload["client_id"] = int(client_id)
        payload["action"] = action
        payload["order_type"] = order_type
        payload["quantity"] = quantity
        payload["aux_price"] = aux_price
        payload["limit_price"] = limit_price
        payload["oca_group"] = _text(payload.get("oca_group"))
        payload["oca_type"] = int(oca_type)
        payload["tif"] = tif
        payload["transmit"] = payload.get("transmit") is True
        payload["outside_rth"] = False
        recipe = {
            "PARENT": ("STP LMT", "DAY", 0, "", False),
            "STOP": ("STP", "GTC", 1, None, False),
            "TARGET": ("LMT", "GTC", 1, None, True),
        }[role]
        if (
            order_type != recipe[0]
            or tif != recipe[1]
            or int(oca_type) != recipe[2]
            or (role == "PARENT" and payload["oca_group"] != recipe[3])
            or (role != "PARENT" and not payload["oca_group"])
            or payload["transmit"] is not recipe[4]
        ):
            return None
        return payload

    @staticmethod
    def _fill_payload(fill: Any) -> Optional[dict[str, Any]]:
        if not isinstance(fill, Mapping):
            return None
        raw_perm_id = fill.get("perm_id")
        raw_client_id = fill.get("client_id")
        side = _CANONICAL_SIDES.get(_token(fill.get("side")))
        shares = _positive(fill.get("shares"))
        price = _positive(fill.get("price"))
        time_value = _utc_iso(fill.get("time"))
        con_id = _finite(fill.get("con_id"))
        order_id = _finite(fill.get("order_id"))
        perm_id = _finite(fill.get("perm_id"))
        client_id = _finite(fill.get("client_id"))
        if (
            side is None
            or shares is None
            or price is None
            or time_value is None
            or not _text(fill.get("exec_id"))
            or not _paper_account(fill.get("account"))
            or con_id is None
            or con_id <= 0
            or int(con_id) != con_id
            or order_id is None
            or order_id <= 0
            or int(order_id) != order_id
            or type(raw_perm_id) is not int
            or perm_id is None
            or perm_id <= 0
            or int(perm_id) != perm_id
            or type(raw_client_id) is not int
            or client_id is None
            or client_id < 0
            or int(client_id) != client_id
        ):
            return None
        return {
            "exec_id": _text(fill.get("exec_id")),
            "account": _token(fill.get("account")),
            "con_id": int(con_id),
            "order_id": int(order_id),
            "perm_id": int(perm_id),
            "client_id": int(client_id),
            "side": side,
            "shares": shares,
            "price": price,
            "time": time_value,
        }

    @staticmethod
    def _outcome_payload(outcome: Any) -> Optional[dict[str, Any]]:
        if not isinstance(outcome, Mapping):
            return None
        payload = _json_value(dict(outcome))
        if not isinstance(payload, dict) or not _text(payload.get("setup_id")):
            return None
        complete = payload.get("complete")
        if complete is not True and complete is not False:
            return None
        realized_at = _utc_iso(payload.get("realized_at"), allow_none=True)
        if payload.get("realized_at") is not None and realized_at is None:
            return None
        unresolved = payload.get("unresolved_codes")
        if not isinstance(unresolved, list) or any(not _text(code) for code in unresolved):
            return None
        payload["realized_at"] = realized_at
        if complete:
            realized_r = _finite(payload.get("realized_r"))
            if (
                realized_r is None
                or realized_at is None
                or payload.get("outcome_evidence") != "broker_fills"
                or unresolved
            ):
                return None
            payload["realized_r"] = realized_r
        return payload

    @staticmethod
    def _reservation_payload(
        reservation: Any, intent: Mapping[str, Any]
    ) -> Optional[dict[str, Any]]:
        if not isinstance(reservation, Mapping):
            return None
        payload = _json_value(dict(reservation))
        if not isinstance(payload, dict):
            return None
        direction = _token(payload.get("direction"))
        quantity = _positive(payload.get("quantity"))
        entry = _positive(payload.get("entry"))
        stop = _positive(payload.get("stop"))
        group_key = _token(payload.get("group_key"))
        group_verified = payload.get("group_verified") is True
        status = _token(payload.get("status"))
        if (
            not _text(payload.get("reservation_id"))
            or _text(payload.get("setup_id")) != _text(intent.get("setup_id"))
            or _text(payload.get("order_ref")) != _text(intent.get("order_ref"))
            or _token(payload.get("account")) != _token(intent.get("account"))
            or _scalar_key(payload.get("con_id")) != _scalar_key(intent.get("con_id"))
            or not _paper_account(payload.get("account"))
            or direction != _token(intent.get("direction"))
            or quantity is None
            or entry is None
            or stop is None
            or not math.isclose(quantity, float(intent.get("quantity")), rel_tol=0, abs_tol=1e-12)
            or not math.isclose(entry, float(intent.get("entry")), rel_tol=0, abs_tol=1e-12)
            or not math.isclose(stop, float(intent.get("stop")), rel_tol=0, abs_tol=1e-12)
            or group_key != _token(intent.get("group_key"))
            or group_verified != (intent.get("group_verified") is True)
            or (direction == "LONG" and stop >= entry)
            or (direction == "SHORT" and stop <= entry)
            or status != "SUBMITTING"
        ):
            return None
        payload["account"] = _token(payload["account"])
        payload["direction"] = direction
        payload["quantity"] = quantity
        payload["entry"] = entry
        payload["stop"] = stop
        payload["group_key"] = group_key
        payload["group_verified"] = group_verified
        payload["status"] = status
        return payload

    @staticmethod
    def _now_seconds(now: Any) -> Optional[tuple[float, str]]:
        parsed = _utc_datetime(now)
        return (parsed.timestamp(), parsed.isoformat()) if parsed is not None else None

    def register_intent(self, intent: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._intent_payload(intent)
        encoded = _canonical_json(payload) if payload is not None else None
        if payload is None or encoded is None:
            return {"accepted": False, "idempotent": False, "conflict": "intent_invalid"}
        setup_id = _text(payload["setup_id"])
        now = datetime.now(timezone.utc).isoformat()
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT intent_hash FROM trading_risk_intents WHERE setup_id=?",
                (setup_id,),
            ).fetchone()
            if existing is not None:
                if existing["intent_hash"] == _digest(encoded):
                    connection.commit()
                    return {"accepted": True, "idempotent": True, "conflict": None}
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    "intent_immutable_conflict",
                    encoded,
                    _digest(encoded),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_immutable_conflict",
                }
            identity_collision = connection.execute(
                """
                SELECT setup_id FROM trading_risk_intents
                WHERE account=? AND order_ref=?
                """,
                (
                    _text(payload["account"]),
                    _text(payload["order_ref"]),
                ),
            ).fetchone()
            if identity_collision is not None:
                self._record_evidence_conflict(
                    connection,
                    identity_collision["setup_id"],
                    "intent_identity_conflict",
                    encoded,
                    _digest(encoded),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_identity_conflict",
                }
            connection.execute(
                """
                INSERT INTO trading_risk_intents
                    (setup_id, account, con_id, order_ref, intent_json, intent_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    setup_id,
                    _text(payload["account"]),
                    _scalar_key(payload["con_id"]),
                    _text(payload["order_ref"]),
                    encoded,
                    _digest(encoded),
                    now,
                ),
            )
            connection.commit()
            return {"accepted": True, "idempotent": False, "conflict": None}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_intent(self, setup_id: Any) -> Optional[dict[str, Any]]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT intent_json FROM trading_risk_intents WHERE setup_id=?",
                (_text(setup_id),),
            ).fetchone()
            return json.loads(row["intent_json"]) if row is not None else None
        finally:
            connection.close()

    def register_intent_order(
        self,
        setup_id: Any,
        mapping: Mapping[str, Any],
        *,
        execution_generation: Any = _UNSET,
    ) -> dict[str, Any]:
        setup_key = _text(setup_id)
        expected_execution_generation = (
            None
            if execution_generation is _UNSET
            else _finite(execution_generation)
        )
        payload = self._order_payload(mapping)
        encoded = _canonical_json(payload) if payload is not None else None
        if (
            payload is None
            or encoded is None
            or (
                execution_generation is not _UNSET
                and (
                    expected_execution_generation is None
                    or expected_execution_generation <= 0
                    or int(expected_execution_generation)
                    != expected_execution_generation
                )
            )
        ):
            return {"accepted": False, "idempotent": False, "conflict": "intent_order_mapping_invalid"}
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if execution_generation is not _UNSET:
                execution_row = connection.execute(
                    """
                    SELECT generation, armed FROM trading_risk_execution_state
                    WHERE singleton=1
                    """
                ).fetchone()
                if (
                    execution_row is None
                    or not bool(execution_row["armed"])
                    or int(execution_row["generation"])
                    != int(expected_execution_generation)
                ):
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "execution_generation_fenced",
                    }
            intent = connection.execute(
                "SELECT account, con_id, intent_json FROM trading_risk_intents WHERE setup_id=?",
                (setup_key,),
            ).fetchone()
            if (
                intent is None
                or _text(payload["account"]) != intent["account"]
                or _scalar_key(payload["con_id"]) != intent["con_id"]
            ):
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_invalid",
                }
            intent_payload = json.loads(intent["intent_json"])
            identity_collision = connection.execute(
                """
                SELECT setup_id FROM trading_risk_intent_orders
                WHERE account=? AND con_id=? AND order_id=?
                """,
                (
                    _text(payload["account"]),
                    _scalar_key(payload["con_id"]),
                    _scalar_key(payload["order_id"]),
                ),
            ).fetchone()
            if (
                identity_collision is not None
                and identity_collision["setup_id"] != setup_key
            ):
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "intent_order_mapping_conflict",
                    encoded,
                    _digest(encoded),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_conflict",
                }
            reference_collision = connection.execute(
                """
                SELECT setup_id, order_id FROM trading_risk_intent_orders
                WHERE account=? AND json_extract(mapping_json, '$.order_ref')=?
                LIMIT 1
                """,
                (_text(payload["account"]), _text(payload["order_ref"])),
            ).fetchone()
            if reference_collision is not None and (
                reference_collision["setup_id"] != setup_key
                or _scalar_key(reference_collision["order_id"])
                != _scalar_key(payload["order_id"])
            ):
                self._record_evidence_conflict(
                    connection, setup_key, "intent_order_mapping_conflict",
                    encoded, _digest(encoded),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_conflict",
                }
            suffix = {"PARENT": "P", "STOP": "S", "TARGET": "T"}[payload["role"]]
            expected_ref = (
                f"{_text(intent_payload.get('order_ref'))}-{suffix}{payload['branch']}"
            )
            mapping_valid = _text(payload.get("order_ref")) == expected_ref
            direction = _token(intent_payload.get("direction"))
            expected_action = (
                ("BUY" if direction == "LONG" else "SELL")
                if payload["role"] == "PARENT"
                else ("SELL" if direction == "LONG" else "BUY")
            )
            mapping_valid = mapping_valid and payload["action"] == expected_action
            expected_oca = f"{_text(intent_payload.get('order_ref'))}-O{payload['branch']}"
            if payload["role"] != "PARENT":
                mapping_valid = mapping_valid and payload["oca_group"] == expected_oca
            allocations = intent_payload.get("allocations")
            if not isinstance(allocations, list) or payload["branch"] > len(allocations):
                mapping_valid = False
            else:
                expected_quantity = _positive(allocations[payload["branch"] - 1])
                mapping_valid = (
                    mapping_valid
                    and expected_quantity is not None
                    and math.isclose(
                        payload["quantity"], expected_quantity, rel_tol=0, abs_tol=1e-9
                    )
                )
            expected_aux = None
            expected_limit = None
            if payload["role"] == "PARENT":
                expected_aux = _positive(intent_payload.get("entry"))
                expected_limit = _positive(intent_payload.get("stop_limit"))
            elif payload["role"] == "STOP":
                expected_aux = _positive(intent_payload.get("stop"))
            else:
                expected_limit = _positive(
                    intent_payload.get("tp1")
                    if payload["branch"] == 1
                    else intent_payload.get("tp2")
                )
            if expected_aux is not None:
                mapping_valid = mapping_valid and math.isclose(
                    payload["aux_price"], expected_aux, rel_tol=0, abs_tol=1e-9
                )
            if expected_limit is not None:
                mapping_valid = mapping_valid and math.isclose(
                    payload["limit_price"], expected_limit, rel_tol=0, abs_tol=1e-9
                )
            if payload["role"] == "PARENT":
                mapping_valid = mapping_valid and int(payload["parent_order_id"]) == 0
            else:
                parent_mapping = connection.execute(
                    """
                    SELECT order_id FROM trading_risk_intent_orders
                    WHERE setup_id=? AND role='PARENT' AND branch=?
                    """,
                    (setup_key, payload["branch"]),
                ).fetchone()
                mapping_valid = (
                    mapping_valid
                    and parent_mapping is not None
                    and _scalar_key(payload["parent_order_id"])
                    == _scalar_key(parent_mapping["order_id"])
                )
            if not mapping_valid:
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_invalid",
                }
            perm_collision = None
            if payload["perm_id"] > 0:
                perm_collision = connection.execute(
                    """
                    SELECT setup_id FROM trading_risk_intent_orders
                    WHERE CAST(json_extract(mapping_json, '$.perm_id') AS INTEGER)=?
                      AND NOT (account=? AND con_id=? AND order_id=?)
                    LIMIT 1
                    """,
                    (
                        int(payload["perm_id"]),
                        _text(payload["account"]),
                        _scalar_key(payload["con_id"]),
                        _scalar_key(payload["order_id"]),
                    ),
                ).fetchone()
            if perm_collision is not None:
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "intent_order_mapping_conflict",
                    encoded,
                    _digest(encoded),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_conflict",
                }
            existing = connection.execute(
                """
                SELECT setup_id, mapping_hash, mapping_json FROM trading_risk_intent_orders
                WHERE account=? AND con_id=? AND order_id=?
                """,
                (
                    _text(payload["account"]),
                    _scalar_key(payload["con_id"]),
                    _scalar_key(payload["order_id"]),
                ),
            ).fetchone()
            branch = connection.execute(
                """
                SELECT mapping_hash, mapping_json FROM trading_risk_intent_orders
                WHERE setup_id=? AND role=? AND branch=?
                """,
                (setup_key, payload["role"], payload["branch"]),
            ).fetchone()
            encoded_hash = _digest(encoded)
            if existing is not None or branch is not None:
                matched = existing if existing is not None else branch
                old_payload = json.loads(matched["mapping_json"]) if matched is not None else None
                perm_enrichment = False
                if isinstance(old_payload, dict):
                    old_without_perm = {key: value for key, value in old_payload.items() if key != "perm_id"}
                    new_without_perm = {key: value for key, value in payload.items() if key != "perm_id"}
                    old_perm = int(_finite(old_payload.get("perm_id")) or 0)
                    new_perm = int(_finite(payload.get("perm_id")) or 0)
                    perm_enrichment = (
                        old_without_perm == new_without_perm
                        and old_perm == 0
                        and new_perm > 0
                    )
                if (
                    existing is not None
                    and existing["setup_id"] == setup_key
                    and (existing["mapping_hash"] == encoded_hash or perm_enrichment)
                ):
                    if perm_enrichment:
                        connection.execute(
                            """
                            UPDATE trading_risk_intent_orders
                            SET mapping_json=?, mapping_hash=?
                            WHERE account=? AND con_id=? AND order_id=?
                            """,
                            (
                                encoded, encoded_hash, _text(payload["account"]),
                                _scalar_key(payload["con_id"]),
                                _scalar_key(payload["order_id"]),
                            ),
                        )
                    mapped_fills, mapped_conflicts, pending_issue = self._bind_pending_fill_identity(
                        connection, setup_key, payload
                    )
                    mapping_after_complete = bool(mapped_fills) and self._setup_has_complete_outcome(
                        connection, setup_key
                    )
                    mapping_after_release = bool(mapped_fills) and self._setup_has_released_reservation(
                        connection, setup_key
                    )
                    if mapping_after_complete:
                        self._record_evidence_conflict(
                            connection,
                            setup_key,
                            "fill_set_changed_after_complete",
                            encoded,
                            encoded_hash,
                        )
                    if mapping_after_release:
                        for attached_fill in mapped_fills:
                            self._record_evidence_conflict(
                                connection,
                                setup_key,
                                "fill_seen_after_release",
                                attached_fill["payload_json"],
                                attached_fill["payload_hash"],
                            )
                    connection.commit()
                    return {
                        "accepted": True,
                        "idempotent": True,
                        "conflict": (
                            pending_issue
                            if pending_issue is not None
                            else (
                                "exec_id_payload_conflict"
                                if mapped_conflicts
                                else (
                                    "fill_seen_after_release"
                                    if mapping_after_release
                                    else (
                                        "fill_set_changed_after_complete"
                                        if mapping_after_complete
                                        else None
                                    )
                                )
                            )
                        ),
                    }
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "intent_order_mapping_conflict",
                    encoded,
                    encoded_hash,
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "intent_order_mapping_conflict",
                }
            connection.execute(
                """
                INSERT INTO trading_risk_intent_orders
                    (account, con_id, order_id, setup_id, role, branch,
                     mapping_json, mapping_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(payload["account"]),
                    _scalar_key(payload["con_id"]),
                    _scalar_key(payload["order_id"]),
                    setup_key,
                    payload["role"],
                    payload["branch"],
                    encoded,
                    encoded_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            mapping_seen_after_release = self._setup_has_released_reservation(
                connection, setup_key
            )
            mapping_seen_after_complete = self._setup_has_complete_outcome(
                connection, setup_key
            )
            if mapping_seen_after_release:
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "order_mapping_seen_after_release",
                    encoded,
                    encoded_hash,
                )
            if mapping_seen_after_complete:
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "order_mapping_seen_after_complete",
                    encoded,
                    encoded_hash,
                )
            mapped_fills, mapped_conflicts, pending_issue = self._bind_pending_fill_identity(
                connection, setup_key, payload
            )
            if (
                mapped_fills
                and self._setup_has_complete_outcome(connection, setup_key)
            ):
                self._record_evidence_conflict(
                    connection,
                    setup_key,
                    "fill_set_changed_after_complete",
                    encoded,
                    encoded_hash,
                )
            if (
                mapped_fills
                and mapping_seen_after_release
            ):
                for attached_fill in mapped_fills:
                    self._record_evidence_conflict(
                        connection,
                        setup_key,
                        "fill_seen_after_release",
                        attached_fill["payload_json"],
                        attached_fill["payload_hash"],
                    )
            connection.commit()
            return {
                "accepted": True,
                "idempotent": False,
                "conflict": (
                    pending_issue
                    if pending_issue is not None
                    else (
                        "exec_id_payload_conflict"
                        if mapped_conflicts
                        else (
                            "fill_seen_after_release"
                            if mapped_fills and mapping_seen_after_release
                            else (
                                "order_mapping_seen_after_release"
                                if mapping_seen_after_release
                                else (
                                    "fill_set_changed_after_complete"
                                    if mapped_fills and mapping_seen_after_complete
                                    else (
                                        "order_mapping_seen_after_complete"
                                        if mapping_seen_after_complete
                                        else None
                                    )
                                )
                            )
                        )
                    )
                ),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def intent_order_ids(self, setup_id: Any) -> list[Any]:
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT mapping_json FROM trading_risk_intent_orders
                WHERE setup_id=? ORDER BY rowid
                """,
                (_text(setup_id),),
            ).fetchall()
            return [json.loads(row["mapping_json"])["order_id"] for row in rows]
        finally:
            connection.close()

    def _bind_pending_fill_identity(
        self,
        connection: sqlite3.Connection,
        setup_id: str,
        mapping: Mapping[str, Any],
    ) -> tuple[list[sqlite3.Row], int, Optional[str]]:
        """Attach only fills proven to be the same broker order identity."""
        perm_id = int(_finite(mapping.get("perm_id")) or 0)
        client_id = _finite(mapping.get("client_id"))
        if (
            perm_id <= 0
            or client_id is None
            or client_id < 0
            or int(client_id) != client_id
        ):
            return [], 0, None
        account = _text(mapping["account"])
        con_id = _scalar_key(mapping["con_id"])
        order_id = _scalar_key(mapping["order_id"])
        client_id_value = int(client_id)
        candidate_fills = connection.execute(
            """
            SELECT exec_id, payload_json, payload_hash
            FROM trading_risk_fill_events
            WHERE setup_id IS NULL AND account=? AND con_id=? AND order_id=?
            """,
            (account, con_id, order_id),
        ).fetchall()
        pending_fills: list[sqlite3.Row] = []
        pending_issue: Optional[str] = None
        for candidate in candidate_fills:
            try:
                raw_fill = json.loads(candidate["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_fill = None
            fill_payload = self._fill_payload(raw_fill)
            fill_encoded = (
                _canonical_json(fill_payload) if fill_payload is not None else None
            )
            if (
                fill_payload is None
                or fill_encoded is None
                or _digest(fill_encoded) != candidate["payload_hash"]
            ):
                pending_issue = pending_issue or "pending_fill_payload_invalid"
                audit = _canonical_json(
                    {
                        "exec_id": candidate["exec_id"],
                        "reason": "pending_fill_payload_invalid",
                        "stored_hash": candidate["payload_hash"],
                    }
                )
                assert audit is not None
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    "pending_fill_payload_invalid",
                    audit,
                    _digest(audit),
                )
                continue
            if (
                fill_payload["account"] == _token(account)
                and _scalar_key(fill_payload["con_id"]) == con_id
                and _scalar_key(fill_payload["order_id"]) == order_id
                and fill_payload["perm_id"] == perm_id
                and fill_payload["client_id"] == client_id_value
            ):
                pending_fills.append(candidate)
        for pending_fill in pending_fills:
            connection.execute(
                """
                UPDATE trading_risk_fill_events SET setup_id=?
                WHERE setup_id IS NULL AND exec_id=?
                """,
                (setup_id, pending_fill["exec_id"]),
            )
        pending_conflicts = connection.execute(
            """
            SELECT id, payload_json, payload_hash FROM trading_risk_fill_conflicts
            WHERE incoming_setup_id IS NULL
              AND incoming_account=? AND incoming_con_id=? AND incoming_order_id=?
              AND incoming_perm_id=? AND incoming_client_id=?
            """,
            (account, con_id, order_id, perm_id, client_id_value),
        ).fetchall()
        matched_conflicts = 0
        for conflict_row in pending_conflicts:
            try:
                raw_conflict = json.loads(conflict_row["payload_json"])
            except (TypeError, ValueError, json.JSONDecodeError):
                raw_conflict = None
            conflict_payload = self._fill_payload(raw_conflict)
            conflict_encoded = (
                _canonical_json(conflict_payload)
                if conflict_payload is not None
                else None
            )
            if (
                conflict_payload is None
                or conflict_encoded is None
                or _digest(conflict_encoded) != conflict_row["payload_hash"]
            ):
                pending_issue = "fill_conflict_payload_invalid"
                audit = _canonical_json(
                    {
                        "conflict_id": int(conflict_row["id"]),
                        "reason": "fill_conflict_payload_invalid",
                        "stored_hash": conflict_row["payload_hash"],
                    }
                )
                assert audit is not None
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    "fill_conflict_payload_invalid",
                    audit,
                    _digest(audit),
                )
                continue
            if (
                conflict_payload["account"] == _token(account)
                and _scalar_key(conflict_payload["con_id"]) == con_id
                and _scalar_key(conflict_payload["order_id"]) == order_id
                and conflict_payload["perm_id"] == perm_id
                and conflict_payload["client_id"] == client_id_value
            ):
                connection.execute(
                    """
                    UPDATE trading_risk_fill_conflicts
                    SET incoming_setup_id=? WHERE id=?
                    """,
                    (setup_id, conflict_row["id"]),
                )
                matched_conflicts += 1
        return pending_fills, matched_conflicts, pending_issue

    def _find_intent_for_fill(
        self, connection: sqlite3.Connection, fill: Mapping[str, Any]
    ) -> Optional[str]:
        row = connection.execute(
            """
            SELECT setup_id, account, con_id, order_id, mapping_json, mapping_hash
            FROM trading_risk_intent_orders
            WHERE account=? AND con_id=? AND order_id=?
            """,
            (
                _text(fill["account"]),
                _scalar_key(fill["con_id"]),
                _scalar_key(fill["order_id"]),
            ),
        ).fetchone()
        if row is None:
            return None
        try:
            raw_mapping = json.loads(row["mapping_json"])
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_mapping = None
        mapping = self._order_payload(raw_mapping)
        mapping_encoded = _canonical_json(mapping) if mapping is not None else None
        mapping_valid = (
            mapping is not None
            and mapping_encoded is not None
            and _digest(mapping_encoded) == row["mapping_hash"]
            and _token(mapping.get("account")) == _token(row["account"])
            and _scalar_key(mapping.get("con_id")) == _scalar_key(row["con_id"])
            and _scalar_key(mapping.get("order_id")) == _scalar_key(row["order_id"])
        )
        if not mapping_valid:
            audit = _canonical_json(
                {
                    "reason": "intent_order_mapping_conflict",
                    "setup_id": row["setup_id"],
                    "stored_hash": row["mapping_hash"],
                }
            )
            assert audit is not None
            self._record_evidence_conflict(
                connection,
                row["setup_id"],
                "intent_order_mapping_conflict",
                audit,
                _digest(audit),
            )
            return None
        if (
            mapping["perm_id"] != int(fill["perm_id"])
            or mapping["client_id"] != int(fill["client_id"])
        ):
            return None
        return row["setup_id"]

    def append_fill(self, fill: Mapping[str, Any]) -> dict[str, Any]:
        payload = self._fill_payload(fill)
        encoded = _canonical_json(payload) if payload is not None else None
        if payload is None or encoded is None:
            raw_payload = None
            if isinstance(fill, Mapping):
                raw_payload = _json_value(dict(fill))
            if raw_payload is None:
                raw_payload = {
                    "python_type": type(fill).__name__,
                    "repr": repr(fill)[:1000],
                }
            rejected_payload = {
                "account": _token(fill.get("account")) if isinstance(fill, Mapping) else "",
                "exec_id": _text(fill.get("exec_id")) if isinstance(fill, Mapping) else "",
                "payload": raw_payload,
                "reason": "fill_invalid",
            }
            rejected_encoded = _canonical_json(rejected_payload)
            assert rejected_encoded is not None
            rejected_hash = _digest(rejected_encoded)
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO trading_risk_rejected_fill_events
                        (account, exec_id, payload_json, payload_hash, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        rejected_payload["account"] or None,
                        rejected_payload["exec_id"] or None,
                        rejected_encoded,
                        rejected_hash,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                persisted = cursor.rowcount > 0
            except Exception:
                connection.rollback()
                raise
            finally:
                connection.close()
            return {
                "accepted": False,
                "idempotent": not persisted,
                "conflict": "fill_invalid",
                "persisted": persisted,
            }
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            setup_id = self._find_intent_for_fill(connection, payload)
            existing = connection.execute(
                """
                SELECT setup_id, payload_hash FROM trading_risk_fill_events
                WHERE exec_id=?
                """,
                (payload["exec_id"],),
            ).fetchone()
            payload_hash = _digest(encoded)
            if existing is not None and existing["payload_hash"] == payload_hash:
                if existing["setup_id"] is None and setup_id is not None:
                    connection.execute(
                        "UPDATE trading_risk_fill_events SET setup_id=? WHERE exec_id=?",
                        (setup_id, payload["exec_id"]),
                    )
                    if self._setup_has_complete_outcome(connection, setup_id):
                        self._record_evidence_conflict(
                            connection,
                            setup_id,
                            "fill_set_changed_after_complete",
                            encoded,
                            payload_hash,
                        )
                connection.commit()
                return {
                    "accepted": True,
                    "idempotent": True,
                    "conflict": None,
                    "persisted": False,
                    **(
                        {"mapping_pending": True}
                        if setup_id is None and existing["setup_id"] is None
                        else {}
                    ),
                }
            if existing is not None:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO trading_risk_fill_conflicts
                        (exec_id, incoming_setup_id, incoming_account,
                         incoming_con_id, incoming_order_id, incoming_perm_id,
                         incoming_client_id, payload_json, payload_hash, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        payload["exec_id"],
                        setup_id,
                        payload["account"],
                        _scalar_key(payload["con_id"]),
                        _scalar_key(payload["order_id"]),
                        payload["perm_id"],
                        payload["client_id"],
                        encoded,
                        payload_hash,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "exec_id_payload_conflict",
                    "persisted": cursor.rowcount > 0,
                    **({"mapping_pending": True} if setup_id is None else {}),
                }
            connection.execute(
                """
                INSERT INTO trading_risk_fill_events
                    (exec_id, ledger_sequence, setup_id, account, con_id, order_id,
                     payload_json, payload_hash, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["exec_id"],
                    int(
                        connection.execute(
                            """
                            SELECT COALESCE(MAX(ledger_sequence), 0) + 1
                            FROM trading_risk_fill_events
                            """
                        ).fetchone()[0]
                    ),
                    setup_id,
                    _text(payload["account"]),
                    _scalar_key(payload["con_id"]),
                    _scalar_key(payload["order_id"]),
                    encoded,
                    payload_hash,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            changed_after_complete = self._setup_has_complete_outcome(
                connection, setup_id
            )
            seen_after_release = self._setup_has_released_reservation(
                connection, setup_id
            )
            if changed_after_complete:
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    "fill_set_changed_after_complete",
                    encoded,
                    payload_hash,
                )
            if seen_after_release:
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    "fill_seen_after_release",
                    encoded,
                    payload_hash,
                )
            connection.commit()
            return {
                "accepted": True,
                "idempotent": False,
                "conflict": (
                    "fill_set_changed_after_complete"
                    if changed_after_complete
                    else "fill_seen_after_release" if seen_after_release else None
                ),
                "persisted": True,
                **({"mapping_pending": True} if setup_id is None else {}),
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def fill_evidence(self, setup_id: Any) -> dict[str, Any]:
        connection = self._connect()
        try:
            key = _text(setup_id)
            primary_rows = connection.execute(
                """
                SELECT ledger_sequence, payload_json
                FROM trading_risk_fill_events
                WHERE setup_id=? ORDER BY ledger_sequence
                """,
                (key,),
            ).fetchall()
            conflict_rows = connection.execute(
                """
                SELECT c.payload_json FROM trading_risk_fill_conflicts AS c
                JOIN trading_risk_fill_events AS f ON f.exec_id=c.exec_id
                WHERE f.setup_id=? OR c.incoming_setup_id=?
                ORDER BY c.id
                """,
                (key, key),
            ).fetchall()
            fills = []
            for row in primary_rows:
                payload = json.loads(row["payload_json"])
                payload["ledger_sequence"] = int(row["ledger_sequence"])
                fills.append(payload)
            conflicts = []
            for row in conflict_rows:
                payload = json.loads(row["payload_json"])
                conflicts.append(
                    {
                        "exec_id": payload["exec_id"],
                        "side": payload["side"],
                        "shares": payload["shares"],
                        "price": payload["price"],
                        "time": payload["time"],
                    }
                )
            return {
                "fills": fills,
                "reliable": not conflicts,
                "unresolved_codes": ["fill_exec_conflict"] if conflicts else [],
                "conflicting_events": conflicts,
                "fill_set_hash": self._fill_set_hash(connection, key),
            }
        finally:
            connection.close()

    @staticmethod
    def _fill_set_hash(
        connection: sqlite3.Connection, setup_id: str
    ) -> Optional[str]:
        rows = connection.execute(
            """
            SELECT ledger_sequence, exec_id, payload_hash
            FROM trading_risk_fill_events
            WHERE setup_id=? ORDER BY ledger_sequence
            """,
            (setup_id,),
        ).fetchall()
        if not rows:
            return None
        encoded = _canonical_json(
            [
                [int(row["ledger_sequence"]), row["exec_id"], row["payload_hash"]]
                for row in rows
            ]
        )
        return _digest(encoded) if encoded is not None else None

    @staticmethod
    def _setup_has_fill_conflict(
        connection: sqlite3.Connection, setup_id: str
    ) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM trading_risk_fill_conflicts AS c
            JOIN trading_risk_fill_events AS f ON f.exec_id=c.exec_id
            WHERE f.setup_id=? OR c.incoming_setup_id=? LIMIT 1
            """,
            (setup_id, setup_id),
        ).fetchone() is not None

    @staticmethod
    def _setup_has_evidence_conflict(
        connection: sqlite3.Connection, setup_id: str
    ) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM trading_risk_evidence_conflicts
            WHERE setup_id=? LIMIT 1
            """,
            (setup_id,),
        ).fetchone() is not None

    @staticmethod
    def _account_has_incomplete_fill_evidence(
        connection: sqlite3.Connection, account: Any
    ) -> bool:
        account_key = _token(account)
        rejected = connection.execute(
            """
            SELECT 1 FROM trading_risk_rejected_fill_events
            WHERE account=? OR account IS NULL OR TRIM(account)=''
            LIMIT 1
            """,
            (account_key,),
        ).fetchone()
        unmapped = connection.execute(
            """
            SELECT 1 FROM trading_risk_fill_events
            WHERE setup_id IS NULL AND account=? LIMIT 1
            """,
            (account_key,),
        ).fetchone()
        return rejected is not None or unmapped is not None

    @staticmethod
    def _record_evidence_conflict(
        connection: sqlite3.Connection,
        setup_id: str,
        conflict_kind: str,
        payload_json: str,
        payload_hash: str,
    ) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO trading_risk_evidence_conflicts
                (setup_id, conflict_kind, payload_json, payload_hash, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                setup_id,
                conflict_kind,
                payload_json,
                payload_hash,
                datetime.now(timezone.utc).isoformat(),
            ),
        )

    @staticmethod
    def _setup_has_complete_outcome(
        connection: sqlite3.Connection, setup_id: Optional[str]
    ) -> bool:
        if not setup_id:
            return False
        return connection.execute(
            "SELECT 1 FROM trading_risk_outcomes WHERE setup_id=? AND state='COMPLETE'",
            (setup_id,),
        ).fetchone() is not None

    @staticmethod
    def _setup_has_released_reservation(
        connection: sqlite3.Connection, setup_id: Optional[str]
    ) -> bool:
        if not setup_id:
            return False
        return connection.execute(
            """
            SELECT 1 FROM trading_risk_reservations
            WHERE setup_id=? AND status='RELEASED' LIMIT 1
            """,
            (setup_id,),
        ).fetchone() is not None

    @staticmethod
    def _observed_order_mapping(
        connection: sqlite3.Connection,
        order: Mapping[str, Any],
        account: str,
    ) -> tuple[Optional[sqlite3.Row], Optional[str]]:
        """Resolve by broker identity, then by one exact immutable order ref."""
        exact = connection.execute(
            """
            SELECT m.setup_id, m.role, m.mapping_json, i.intent_json
            FROM trading_risk_intent_orders AS m
            JOIN trading_risk_intents AS i ON i.setup_id=m.setup_id
            WHERE m.account=? AND m.con_id=? AND m.order_id=?
              AND m.role IN ('PARENT', 'STOP', 'TARGET')
            """,
            (
                _text(account),
                _scalar_key(order.get("con_id")),
                _scalar_key(order.get("order_id")),
            ),
        ).fetchone()
        if exact is not None:
            return exact, "broker_id"
        order_ref = _text(order.get("order_ref"))
        if not order_ref:
            return None, None
        candidates = connection.execute(
            """
            SELECT m.setup_id, m.role, m.mapping_json, i.intent_json
            FROM trading_risk_intent_orders AS m
            JOIN trading_risk_intents AS i ON i.setup_id=m.setup_id
            WHERE m.account=?
              AND m.role IN ('PARENT', 'STOP', 'TARGET')
            """,
            (_text(account),),
        ).fetchall()
        reference_matches = [
            candidate
            for candidate in candidates
            if _text(json.loads(candidate["mapping_json"]).get("order_ref"))
            == order_ref
        ]
        if len(reference_matches) == 1:
            return reference_matches[0], "order_ref"
        return None, None

    @staticmethod
    def _has_active_reservation(connection: sqlite3.Connection, setup_id: str) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM trading_risk_reservations
            WHERE setup_id=? AND status NOT IN
                ('CANCELLED','CANCELED','REJECTED','EXPIRED',
                 'RELEASED','COMPLETED','DONE')
            LIMIT 1
            """,
            (setup_id,),
        ).fetchone() is not None

    @staticmethod
    def _has_broker_visible_reservation(
        connection: sqlite3.Connection, setup_id: str
    ) -> bool:
        return connection.execute(
            """
            SELECT 1 FROM trading_risk_reservations
            WHERE setup_id=? AND status='BROKER_VISIBLE' LIMIT 1
            """,
            (setup_id,),
        ).fetchone() is not None

    @staticmethod
    def _order_geometry_matches(
        mapping_payload: Mapping[str, Any], order: Mapping[str, Any]
    ) -> bool:
        observed_aux = order.get("aux_price", order.get("stop_price"))
        observed_perm = _finite(order.get("perm_id"))
        mapped_perm = _finite(mapping_payload.get("perm_id"))
        if (
            observed_perm is None
            or mapped_perm is None
            or int(observed_perm) != observed_perm
            or int(mapped_perm) != mapped_perm
        ):
            return False
        perm_matches = int(mapped_perm) == int(observed_perm)
        numeric_pairs = (
            (_finite(mapping_payload.get("quantity")), _finite(order.get("quantity"))),
            (_order_price(mapping_payload.get("aux_price")), _order_price(observed_aux)),
            (_order_price(mapping_payload.get("limit_price")), _order_price(order.get("limit_price"))),
        )
        for expected, actual in numeric_pairs:
            expected_number = expected
            actual_number = actual
            if expected_number is None or actual_number is None:
                if expected_number is not None or actual_number is not None:
                    return False
            elif not math.isclose(expected_number, actual_number, rel_tol=0, abs_tol=1e-9):
                return False
        return (
            _token(order.get("account")) == _token(mapping_payload.get("account"))
            and _scalar_key(order.get("con_id")) == _scalar_key(mapping_payload.get("con_id"))
            and _scalar_key(order.get("order_id")) == _scalar_key(mapping_payload.get("order_id"))
            and _scalar_key(order.get("parent_id"))
            == _scalar_key(mapping_payload.get("parent_order_id"))
            and _text(order.get("order_ref")) == _text(mapping_payload.get("order_ref"))
            and _token(order.get("action")) == _token(mapping_payload.get("action"))
            and " ".join(_token(order.get("order_type")).split())
            == " ".join(_token(mapping_payload.get("order_type")).split())
            and _text(order.get("oca_group")) == _text(mapping_payload.get("oca_group"))
            and _scalar_key(order.get("oca_type")) == _scalar_key(mapping_payload.get("oca_type"))
            and _token(order.get("tif")) == _token(mapping_payload.get("tif"))
            and order.get("transmit") is mapping_payload.get("transmit")
            and order.get("outside_rth") is mapping_payload.get("outside_rth")
            and _scalar_key(order.get("client_id")) == _scalar_key(mapping_payload.get("client_id"))
            and perm_matches
        )

    @staticmethod
    def _persisted_account_conflicts(
        connection: sqlite3.Connection, account: str
    ) -> list[dict[str, str]]:
        rows = connection.execute(
            """
            SELECT DISTINCT c.setup_id, c.conflict_kind
            FROM trading_risk_evidence_conflicts AS c
            JOIN trading_risk_intents AS i ON i.setup_id=c.setup_id
            WHERE i.account=?
            ORDER BY c.setup_id, c.conflict_kind
            """,
            (_text(account),),
        ).fetchall()
        return [
            {"setup_id": row["setup_id"], "conflict": row["conflict_kind"]}
            for row in rows
        ]

    def _record_terminal_child_reappearances(
        self,
        connection: sqlite3.Connection,
        orders: Iterable[Mapping[str, Any]],
        account: str,
    ) -> list[dict[str, str]]:
        """Persist exact mapped order contradictions from a full snapshot."""
        conflicts: list[dict[str, str]] = []
        for order in orders:
            if _token(order.get("status")) not in _ACTIVE_ORDER_STATUSES:
                continue
            remaining = _finite(order.get("remaining"))
            if remaining is None or remaining <= 0:
                continue
            mapping, match_kind = self._observed_order_mapping(
                connection, order, account
            )
            if mapping is None:
                continue
            setup_id = mapping["setup_id"]
            mapping_payload = json.loads(mapping["mapping_json"])
            observed_perm = _finite(order.get("perm_id"))
            mapped_perm = _finite(mapping_payload.get("perm_id"))
            if (
                observed_perm is not None
                and int(observed_perm) == observed_perm
                and observed_perm > 0
                and mapped_perm == 0
                and match_kind == "broker_id"
                and _scalar_key(order.get("order_id"))
                == _scalar_key(mapping_payload.get("order_id"))
                and _scalar_key(order.get("con_id"))
                == _scalar_key(mapping_payload.get("con_id"))
                and _scalar_key(order.get("client_id"))
                == _scalar_key(mapping_payload.get("client_id"))
                and _text(order.get("order_ref"))
                == _text(mapping_payload.get("order_ref"))
            ):
                mapping_payload["perm_id"] = int(observed_perm)
                enriched_json = _canonical_json(mapping_payload)
                if enriched_json is not None:
                    connection.execute(
                        """
                        UPDATE trading_risk_intent_orders
                        SET mapping_json=?, mapping_hash=?
                        WHERE setup_id=? AND role=? AND branch=?
                        """,
                        (
                            enriched_json, _digest(enriched_json), setup_id,
                            mapping["role"], int(mapping_payload["branch"]),
                        ),
                    )
            exact_geometry = self._order_geometry_matches(mapping_payload, order)
            terminal_kind = None
            if self._setup_has_complete_outcome(connection, setup_id):
                terminal_kind = "complete"
            elif self._setup_has_released_reservation(connection, setup_id):
                terminal_kind = "release"
            if terminal_kind is None:
                conflict_kind = None
                if not self._has_active_reservation(connection, setup_id):
                    conflict_kind = "active_order_without_reservation"
                elif not exact_geometry:
                    conflict_kind = "active_order_geometry_mismatch"
                if conflict_kind is not None:
                    encoded = _canonical_json(dict(order))
                    if encoded is not None:
                        self._record_evidence_conflict(
                            connection, setup_id, conflict_kind, encoded, _digest(encoded)
                        )
                        conflicts.append({"setup_id": setup_id, "conflict": conflict_kind})
                continue
            intent_payload = json.loads(mapping["intent_json"])
            direction = _token(intent_payload.get("direction"))
            expected_action = (
                ("BUY" if direction == "LONG" else "SELL")
                if mapping["role"] == "PARENT"
                else ("SELL" if direction == "LONG" else "BUY")
            )
            order_type = _token(order.get("order_type"))
            role_type_valid = {
                "PARENT": "STP" in order_type,
                "STOP": "STP" in order_type,
                "TARGET": order_type == "LMT",
            }[mapping["role"]]
            exact_geometry = exact_geometry and (
                _token(order.get("action")) == expected_action and role_type_valid
            )
            subject = "parent_order" if mapping["role"] == "PARENT" else "child_order"
            mismatch_subject = (
                "terminal_parent_order_identity_mismatch"
                if mapping["role"] == "PARENT"
                else "terminal_child_identity_mismatch"
            )
            conflict_kind = (
                f"{subject}_reappeared_after_{terminal_kind}"
                if exact_geometry
                else f"{mismatch_subject}_after_{terminal_kind}"
            )
            encoded = _canonical_json(dict(order))
            if encoded is not None:
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    conflict_kind,
                    encoded,
                    _digest(encoded),
                )
                conflicts.append(
                    {"setup_id": setup_id, "conflict": conflict_kind}
                )
        return conflicts

    def _record_terminal_position_reappearances(
        self,
        connection: sqlite3.Connection,
        positions: Iterable[Mapping[str, Any]],
        account: str,
    ) -> tuple[list[dict[str, str]], list[str]]:
        """Attribute broker positions only from coherent mapped fill evidence.

        A contract may legitimately be traded again after an older setup became
        terminal.  Account/conId alone therefore never attributes the position
        to terminal history.  Exactly one active, nonterminal reservation must
        have conflict-free mapped fills whose role, side, quantity and direction
        reproduce the signed broker position.
        """
        conflicts: list[dict[str, str]] = []
        position_setup_ids: set[str] = set()
        account_fill_evidence_incomplete = self._account_has_incomplete_fill_evidence(
            connection, account
        )
        for position in positions:
            quantity = _finite(position.get("quantity"))
            if quantity is None or abs(quantity) <= 1e-9:
                continue
            con_id = _scalar_key(position.get("con_id"))
            intent_rows = connection.execute(
                """
                SELECT setup_id, intent_json FROM trading_risk_intents
                WHERE account=? AND con_id=? ORDER BY setup_id
                """,
                (_text(account), con_id),
            ).fetchall()
            terminal: list[tuple[str, str]] = []
            active: list[tuple[str, str]] = []
            for row in intent_rows:
                setup_id = row["setup_id"]
                if self._setup_has_complete_outcome(connection, setup_id):
                    terminal.append((setup_id, "complete"))
                    continue
                if self._setup_has_released_reservation(connection, setup_id):
                    terminal.append((setup_id, "release"))
                    continue
                has_active_reservation = connection.execute(
                    """
                    SELECT 1 FROM trading_risk_reservations
                    WHERE setup_id=? AND status NOT IN
                        ('CANCELLED','CANCELED','REJECTED','EXPIRED',
                         'RELEASED','COMPLETED','DONE')
                    LIMIT 1
                    """,
                    (setup_id,),
                ).fetchone() is not None
                if has_active_reservation:
                    direction = _token(json.loads(row["intent_json"]).get("direction"))
                    active.append((setup_id, direction))

            fill_backed: list[str] = []
            for setup_id, direction in active:
                if (
                    direction not in {"LONG", "SHORT"}
                    or account_fill_evidence_incomplete
                    or self._setup_has_fill_conflict(connection, setup_id)
                    or self._setup_has_evidence_conflict(connection, setup_id)
                ):
                    continue
                fill_rows = connection.execute(
                    """
                    SELECT f.payload_json, m.role
                    FROM trading_risk_fill_events AS f
                    JOIN trading_risk_intent_orders AS m
                      ON m.setup_id=f.setup_id
                     AND m.account=f.account
                     AND m.con_id=f.con_id
                     AND m.order_id=f.order_id
                    WHERE f.setup_id=?
                    """,
                    (setup_id,),
                ).fetchall()
                total_fill_count = connection.execute(
                    "SELECT COUNT(*) FROM trading_risk_fill_events WHERE setup_id=?",
                    (setup_id,),
                ).fetchone()[0]
                entry_quantity = 0.0
                exit_quantity = 0.0
                fills_valid = bool(fill_rows)
                for fill_row in fill_rows:
                    fill = json.loads(fill_row["payload_json"])
                    shares = _positive(fill.get("shares"))
                    role = _token(fill_row["role"])
                    side = _token(fill.get("side"))
                    expected_side = (
                        ("BOT" if direction == "LONG" else "SLD")
                        if role == "PARENT"
                        else ("SLD" if direction == "LONG" else "BOT")
                    )
                    if (
                        shares is None
                        or role not in _ORDER_ROLES
                        or side != expected_side
                    ):
                        fills_valid = False
                        break
                    if role == "PARENT":
                        entry_quantity += shares
                    else:
                        exit_quantity += shares
                open_quantity = entry_quantity - exit_quantity
                signed_open_quantity = (
                    open_quantity if direction == "LONG" else -open_quantity
                )
                if (
                    fills_valid
                    and len(fill_rows) == total_fill_count
                    and entry_quantity > 0
                    and open_quantity > 0
                    and math.isclose(
                        signed_open_quantity,
                        quantity,
                        rel_tol=1e-9,
                        abs_tol=1e-9,
                    )
                ):
                    fill_backed.append(setup_id)

            if len(fill_backed) == 1:
                position_setup_ids.add(fill_backed[0])
                continue
            encoded = _canonical_json(dict(position))
            if encoded is None:
                continue
            if not active and len(terminal) == 1:
                targets = [terminal[0]]
                conflict_kind = f"position_reappeared_after_{terminal[0][1]}"
            elif active or len(terminal) > 1:
                targets = terminal + [
                    (setup_id, "") for setup_id, _direction in active
                ]
                conflict_kind = "position_attribution_unresolved"
            else:
                continue
            for setup_id, _terminal_kind in targets:
                self._record_evidence_conflict(
                    connection,
                    setup_id,
                    conflict_kind,
                    encoded,
                    _digest(encoded),
                )
                conflicts.append(
                    {"setup_id": setup_id, "conflict": conflict_kind}
                )
        return conflicts, sorted(position_setup_ids)

    def observe_open_orders(
        self,
        orders: Optional[Iterable[Mapping[str, Any]]],
        *,
        account: Any,
        snapshot_complete: Any,
        positions: Optional[Iterable[Mapping[str, Any]]] = None,
        positions_snapshot_complete: Any = False,
        fills_snapshot_complete: Any = False,
        observed_at: Any,
    ) -> dict[str, Any]:
        """Persist terminal exposure contradictions from one full broker snapshot."""
        if snapshot_complete is not True:
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "orders_snapshot_incomplete",
            }
        if positions_snapshot_complete is not True:
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "positions_snapshot_incomplete",
            }
        if fills_snapshot_complete is not True:
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "fills_snapshot_incomplete",
            }
        account_key = _token(account)
        observed = _utc_iso(observed_at)
        try:
            materialized = list(orders) if orders is not None else None
            materialized_positions = (
                list(positions) if positions is not None else None
            )
        except TypeError:
            materialized = None
            materialized_positions = None
        order_shapes_valid = materialized is not None
        for order in materialized or []:
            remaining = _finite(order.get("remaining"))
            if (
                _token(order.get("status")) not in _ACTIVE_ORDER_STATUSES
                or remaining is None
                or remaining < 0
                or not _text(order.get("order_ref"))
                or _positive(order.get("order_id")) is None
                or _positive(order.get("con_id")) is None
            ):
                order_shapes_valid = False
                break
        position_shapes_valid = materialized_positions is not None
        for position in materialized_positions or []:
            quantity = _finite(position.get("quantity"))
            if (
                quantity is None
                or _positive(position.get("con_id")) is None
                or (
                    abs(quantity) > 1e-9
                    and _positive(position.get("avg_cost")) is None
                )
            ):
                position_shapes_valid = False
                break
        if not _paper_account(account_key) or observed is None:
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "snapshot_identity_invalid",
            }
        if (
            not order_shapes_valid
            or not self._live_snapshot_valid(materialized, account_key)
        ):
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "orders_snapshot_invalid",
            }
        if (
            not position_shapes_valid
            or not self._live_snapshot_valid(materialized_positions, account_key)
        ):
            return {
                "accepted": False,
                "conflicts": [],
                "reason": "positions_snapshot_invalid",
            }
        order_identity_keys: set[tuple[str, str, str, str]] = set()
        order_ref_keys: set[tuple[str, str]] = set()
        for order in materialized or []:
            identity = (
                _token(order.get("account")),
                _scalar_key(order.get("client_id")),
                _scalar_key(order.get("con_id")),
                _scalar_key(order.get("order_id")),
            )
            reference = (_token(order.get("account")), _text(order.get("order_ref")))
            if identity in order_identity_keys or reference in order_ref_keys:
                return {
                    "accepted": False,
                    "conflicts": [],
                    "reason": "orders_snapshot_duplicate",
                }
            order_identity_keys.add(identity)
            order_ref_keys.add(reference)
        position_keys: set[tuple[str, str]] = set()
        for position in materialized_positions or []:
            identity = (
                _token(position.get("account")),
                _scalar_key(position.get("con_id")),
            )
            if identity in position_keys:
                return {
                    "accepted": False,
                    "conflicts": [],
                    "reason": "positions_snapshot_duplicate",
                }
            position_keys.add(identity)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            terminal_setup_ids = [
                row["setup_id"]
                for row in connection.execute(
                    """
                    SELECT i.setup_id FROM trading_risk_intents AS i
                    WHERE i.account=? AND (
                        EXISTS (
                            SELECT 1 FROM trading_risk_outcomes AS o
                            WHERE o.setup_id=i.setup_id AND o.state='COMPLETE'
                        ) OR EXISTS (
                            SELECT 1 FROM trading_risk_reservations AS r
                            WHERE r.setup_id=i.setup_id AND r.status='RELEASED'
                        )
                    ) ORDER BY i.setup_id
                    """,
                    (account_key,),
                ).fetchall()
            ]
            conflicts = self._record_terminal_child_reappearances(
                connection, materialized or [], account_key
            )
            unknown_broker_order = False
            for order in materialized or []:
                remaining = _finite(order.get("remaining"))
                if remaining is None or remaining <= 1e-9:
                    continue
                mapping, match_kind = self._observed_order_mapping(
                    connection, order, account_key
                )
                if mapping is None:
                    unknown_broker_order = True
                    break
                if match_kind == "order_ref" and not (
                    self._setup_has_complete_outcome(
                        connection, mapping["setup_id"]
                    )
                    or self._setup_has_released_reservation(
                        connection, mapping["setup_id"]
                    )
                ):
                    unknown_broker_order = True
                    break
            position_conflicts, position_setup_ids = (
                self._record_terminal_position_reappearances(
                    connection, materialized_positions or [], account_key
                )
            )
            conflicts.extend(position_conflicts)
            persisted_conflicts = self._persisted_account_conflicts(
                connection, account_key
            )
            conflict_keys = {
                (_text(item.get("setup_id")), _text(item.get("conflict")))
                for item in conflicts
            }
            for item in persisted_conflicts:
                key = (_text(item.get("setup_id")), _text(item.get("conflict")))
                if key not in conflict_keys:
                    conflicts.append(item)
                    conflict_keys.add(key)
            explained_setup_ids = set(position_setup_ids) | {
                _text(conflict.get("setup_id"))
                for conflict in position_conflicts
                if _text(conflict.get("setup_id"))
            }
            unknown_broker_position = False
            for position in materialized_positions or []:
                if abs(_finite(position.get("quantity")) or 0.0) <= 1e-9:
                    continue
                contract_setup_ids = {
                    row["setup_id"]
                    for row in connection.execute(
                        """
                        SELECT setup_id FROM trading_risk_intents
                        WHERE account=? AND con_id=?
                        """,
                        (account_key, _scalar_key(position.get("con_id"))),
                    ).fetchall()
                }
                if not contract_setup_ids.intersection(explained_setup_ids):
                    unknown_broker_position = True
                    break
            connection.commit()
            active_order_conflict = any(
                _text(item.get("conflict"))
                in {"active_order_geometry_mismatch", "active_order_without_reservation"}
                for item in conflicts
            )
            if active_order_conflict:
                return {
                    "accepted": False,
                    "conflicts": conflicts,
                    "reason": "order_geometry_conflict",
                    "observed_at": observed,
                    "terminal_setup_ids": terminal_setup_ids,
                    "position_setup_ids": position_setup_ids,
                }
            if unknown_broker_order:
                return {
                    "accepted": False,
                    "conflicts": conflicts,
                    "reason": "unknown_broker_order",
                    "observed_at": observed,
                    "terminal_setup_ids": terminal_setup_ids,
                    "position_setup_ids": position_setup_ids,
                }
            if unknown_broker_position:
                return {
                    "accepted": False,
                    "conflicts": conflicts,
                    "reason": "unknown_broker_position",
                    "observed_at": observed,
                    "terminal_setup_ids": terminal_setup_ids,
                    "position_setup_ids": position_setup_ids,
                }
            return {
                "accepted": True,
                "conflicts": conflicts,
                "reason": None,
                "observed_at": observed,
                "terminal_setup_ids": terminal_setup_ids,
                "position_setup_ids": position_setup_ids,
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _derive_stored_outcome(
        self,
        connection: sqlite3.Connection,
        setup_id: str,
        *,
        broker_position_open: Any,
        parent_orders_terminal: Any,
    ) -> Optional[dict[str, Any]]:
        intent_row = connection.execute(
            "SELECT intent_json FROM trading_risk_intents WHERE setup_id=?",
            (setup_id,),
        ).fetchone()
        if intent_row is None:
            return None
        intent = json.loads(intent_row["intent_json"])
        mapping_rows = connection.execute(
            """
            SELECT role, mapping_json FROM trading_risk_intent_orders
            WHERE setup_id=? ORDER BY rowid
            """,
            (setup_id,),
        ).fetchall()
        mappings = [json.loads(row["mapping_json"]) for row in mapping_rows]
        intent["order_ids"] = [mapping["order_id"] for mapping in mappings]
        intent["parent_order_ids"] = [
            mapping["order_id"]
            for row, mapping in zip(mapping_rows, mappings)
            if _token(row["role"]) == "PARENT"
        ]
        fill_rows = connection.execute(
            """
            SELECT ledger_sequence, payload_json
            FROM trading_risk_fill_events
            WHERE setup_id=? ORDER BY ledger_sequence
            """,
            (setup_id,),
        ).fetchall()
        fills = []
        for row in fill_rows:
            fill = json.loads(row["payload_json"])
            fill["ledger_sequence"] = int(row["ledger_sequence"])
            fills.append(fill)
        derived = derive_intent_outcome(
            intent,
            fills,
            broker_position_open=broker_position_open,
            parent_orders_terminal=parent_orders_terminal,
        )
        derived["fill_set_hash"] = self._fill_set_hash(connection, setup_id)
        return derived

    def _validated_terminal_evidence(
        self,
        connection: sqlite3.Connection,
        setup_id: str,
        intent: Mapping[str, Any],
        *,
        reservation_id: Any,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        now: Any,
        terminal_evidence: Mapping[str, Any],
    ) -> Optional[dict[str, Any]]:
        timing = self._now_seconds(now)
        reservation_key = _text(reservation_id)
        key = _text(lease_key)
        owner = _text(owner_token)
        fence = _finite(fence_token)
        if (
            timing is None
            or not reservation_key
            or not key
            or not owner
            or fence is None
            or int(fence) != fence
        ):
            return None
        now_seconds, now_iso = timing
        lease = connection.execute(
            """
            SELECT owner_token, fence_token, expires_at
            FROM trading_risk_leases WHERE lease_key=?
            """,
            (key,),
        ).fetchone()
        reservation = connection.execute(
            """
            SELECT setup_id, account, order_ref, status, lease_key, updated_at
            FROM trading_risk_reservations WHERE reservation_id=?
            """,
            (reservation_key,),
        ).fetchone()
        if (
            lease is None
            or lease["owner_token"] != owner
            or int(lease["fence_token"]) != int(fence)
            or lease["expires_at"] <= now_seconds
            or reservation is None
            or reservation["setup_id"] != setup_id
            or _token(reservation["account"]) != _token(intent.get("account"))
            or _text(reservation["order_ref"]) != _text(intent.get("order_ref"))
            or reservation["lease_key"] != key
            or _token(reservation["status"])
            not in {"BROKER_VISIBLE", "RECONCILE_REQUIRED", "COMPLETED"}
        ):
            return None
        reservation_updated_at = _utc_datetime(reservation["updated_at"])
        transition_at = _utc_datetime(now_iso)
        evidence = _json_value(dict(terminal_evidence))
        observed_at = _utc_datetime(
            evidence.get("observed_at") if isinstance(evidence, dict) else None
        )
        observed_age = (
            now_seconds - observed_at.timestamp()
            if observed_at is not None
            else None
        )
        if (
            not isinstance(evidence, dict)
            or evidence.get("snapshot_complete") is not True
            or evidence.get("position_open") is not False
            or _token(evidence.get("account")) != _token(intent.get("account"))
            or _scalar_key(evidence.get("con_id"))
            != _scalar_key(intent.get("con_id"))
            or reservation_updated_at is None
            or transition_at is None
            or transition_at < reservation_updated_at
            or observed_at is None
            or observed_at < reservation_updated_at
            or observed_age is None
            or observed_age < 0
            or observed_age > 60
        ):
            return None
        raw_open_ids = evidence.get("open_order_ids")
        if not isinstance(raw_open_ids, Iterable) or isinstance(
            raw_open_ids, (str, bytes, Mapping)
        ):
            return None
        raw_open_id_values = list(raw_open_ids)
        open_ids: set[str] = set()
        for raw_id in raw_open_id_values:
            numeric = _finite(raw_id)
            if numeric is None or numeric <= 0 or int(numeric) != numeric:
                return None
            open_ids.add(_scalar_key(int(numeric)))
        if len(open_ids) != len(raw_open_id_values):
            return None
        raw_open_orders = evidence.get("open_orders", [])
        if not isinstance(raw_open_orders, Iterable) or isinstance(
            raw_open_orders, (str, bytes, Mapping)
        ):
            return None
        open_orders = list(raw_open_orders)
        if any(not isinstance(order, Mapping) for order in open_orders):
            return None
        if any(
            _token(order.get("status")) not in _ACTIVE_ORDER_STATUSES
            or (_finite(order.get("remaining")) or 0) <= 0
            for order in open_orders
        ):
            return None
        evidence_order_ids = [_scalar_key(order.get("order_id")) for order in open_orders]
        if (
            len(evidence_order_ids) != len(set(evidence_order_ids))
            or set(evidence_order_ids) != open_ids
        ):
            return None
        mapping_rows = connection.execute(
            """
            SELECT order_id, role FROM trading_risk_intent_orders
            WHERE setup_id=?
            """,
            (setup_id,),
        ).fetchall()
        mapped_ids = {_scalar_key(row["order_id"]) for row in mapping_rows}
        mapped_roles = {_token(row["role"]) for row in mapping_rows}
        if (
            not mapped_ids
            or "PARENT" not in mapped_roles
            or "STOP" not in mapped_roles
            or mapped_ids.intersection(open_ids)
        ):
            return None
        for open_order in open_orders:
            open_id = _scalar_key(open_order.get("order_id"))
            if open_id in mapped_ids:
                return None
            mapped_order, match_kind = self._observed_order_mapping(
                connection, open_order, _token(intent.get("account"))
            )
            mapped_payload = (
                json.loads(mapped_order["mapping_json"])
                if mapped_order is not None
                else None
            )
            mapped_perm = (
                _finite(mapped_payload.get("perm_id"))
                if isinstance(mapped_payload, Mapping)
                else None
            )
            observed_perm = _finite(open_order.get("perm_id"))
            if (
                mapped_order is None
                or match_kind != "broker_id"
                or mapped_order["setup_id"] == setup_id
                or not self._has_broker_visible_reservation(
                    connection, mapped_order["setup_id"]
                )
                or mapped_perm is None
                or observed_perm is None
                or mapped_perm <= 0
                or observed_perm <= 0
                or int(mapped_perm) != mapped_perm
                or int(observed_perm) != observed_perm
                or int(mapped_perm) != int(observed_perm)
                or not self._order_geometry_matches(
                    mapped_payload, open_order
                )
            ):
                return None
        geometry_keys = (
            "account", "con_id", "order_id", "perm_id", "client_id",
            "parent_id", "order_ref", "action", "order_type", "quantity",
            "aux_price", "limit_price", "oca_group", "oca_type", "tif",
            "transmit", "outside_rth", "status", "remaining",
        )
        normalized_open_orders = [
            {key: _json_value(order.get(key)) for key in geometry_keys}
            for order in open_orders
        ]
        for order in normalized_open_orders:
            order["aux_price"] = _order_price(order.get("aux_price"))
            order["limit_price"] = _order_price(order.get("limit_price"))
            order["status"] = _token(order.get("status"))
            order["remaining"] = _finite(order.get("remaining"))
        normalized_open_orders.sort(
            key=lambda order: (
                _token(order.get("account")),
                int(_finite(order.get("client_id")) or 0),
                int(_finite(order.get("order_id")) or 0),
                int(_finite(order.get("con_id")) or 0),
                int(_finite(order.get("perm_id")) or 0),
                _text(order.get("order_ref")),
            )
        )
        normalized_evidence = {
            "snapshot_complete": True,
            "observed_at": observed_at.isoformat(),
            "account": _token(evidence["account"]),
            "con_id": int(float(evidence["con_id"])),
            "position_open": False,
            "open_order_ids": sorted(int(value) for value in open_ids),
            "open_orders": normalized_open_orders,
        }
        encoded = _canonical_json(normalized_evidence)
        assert encoded is not None
        return {
            "reservation_id": reservation_key,
            "lease_key": key,
            "fence_token": int(fence),
            "observed_at": observed_at.isoformat(),
            "created_at": now_iso,
            "evidence": normalized_evidence,
            "evidence_json": encoded,
            "evidence_hash": _digest(encoded),
        }

    @staticmethod
    def _complete_setup_reservations(
        connection: sqlite3.Connection, setup_id: str, updated_at: str
    ) -> None:
        rows = connection.execute(
            """
            SELECT reservation_id, reservation_json
            FROM trading_risk_reservations
            WHERE setup_id=? AND status NOT IN (
                'CANCELLED', 'CANCELED', 'REJECTED', 'EXPIRED',
                'RELEASED', 'COMPLETED', 'DONE'
            )
            """,
            (setup_id,),
        ).fetchall()
        for row in rows:
            reservation = json.loads(row["reservation_json"])
            reservation["status"] = "COMPLETED"
            reservation["transition_reason"] = "broker_fill_outcome_complete"
            encoded = _canonical_json(reservation)
            assert encoded is not None
            connection.execute(
                """
                UPDATE trading_risk_reservations
                SET status='COMPLETED', reservation_json=?, reservation_hash=?,
                    updated_at=? WHERE reservation_id=?
                """,
                (encoded, _digest(encoded), updated_at, row["reservation_id"]),
            )

    def record_outcome(
        self,
        outcome: Mapping[str, Any],
        *,
        broker_position_open: Any = None,
        parent_orders_terminal: Any = None,
        reservation_id: Any = None,
        lease_key: Any = None,
        owner_token: Any = None,
        fence_token: Any = None,
        now: Any = None,
        terminal_evidence: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        payload = self._outcome_payload(outcome)
        encoded = _canonical_json(payload) if payload is not None else None
        if payload is None or encoded is None:
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_invalid",
                "transition": "rejected",
            }
        setup_id = _text(payload["setup_id"])
        state = "COMPLETE" if payload["complete"] is True else "UNRESOLVED"
        encoded_hash = _digest(encoded)
        requested_timing = self._now_seconds(now) if now is not None else None
        if now is not None and requested_timing is None:
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_invalid",
                "transition": "rejected",
            }
        requested_now_iso = (
            requested_timing[1]
            if requested_timing is not None
            else datetime.now(timezone.utc).isoformat()
        )
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            known_intent = connection.execute(
                "SELECT intent_json FROM trading_risk_intents WHERE setup_id=?",
                (setup_id,),
            ).fetchone()
            if known_intent is None:
                connection.commit()
                return {
                    "accepted": False,
                    "idempotent": False,
                    "conflict": "outcome_invalid",
                    "transition": "rejected",
                }
            if state == "COMPLETE":
                expected_fill_hash = self._fill_set_hash(connection, setup_id)
                known_intent_payload = json.loads(known_intent["intent_json"])
                if (
                    expected_fill_hash is None
                    or self._setup_has_fill_conflict(connection, setup_id)
                    or self._setup_has_evidence_conflict(connection, setup_id)
                    or self._account_has_incomplete_fill_evidence(
                        connection, known_intent_payload.get("account")
                    )
                    or _text(payload.get("fill_set_hash")) != expected_fill_hash
                ):
                    self._record_evidence_conflict(
                        connection,
                        setup_id,
                        "outcome_fill_evidence_invalid",
                        encoded,
                        encoded_hash,
                    )
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_fill_evidence_invalid",
                        "transition": "rejected",
                    }
                if (
                    not isinstance(terminal_evidence, Mapping)
                    or not _text(reservation_id)
                    or not _text(lease_key)
                    or not _text(owner_token)
                    or fence_token is None
                    or now is None
                ):
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_terminal_evidence_required",
                        "transition": "rejected",
                    }
                verified_terminal = self._validated_terminal_evidence(
                    connection,
                    setup_id,
                    known_intent_payload,
                    reservation_id=reservation_id,
                    lease_key=lease_key,
                    owner_token=owner_token,
                    fence_token=fence_token,
                    now=now,
                    terminal_evidence=terminal_evidence,
                )
                if verified_terminal is None:
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_terminal_evidence_invalid",
                        "transition": "rejected",
                    }
                derived = self._derive_stored_outcome(
                    connection,
                    setup_id,
                    broker_position_open=False,
                    parent_orders_terminal=True,
                )
                submitted_r = _finite(payload.get("realized_r"))
                derived_r = _finite(derived.get("realized_r")) if derived else None
                derived_matches = (
                    derived is not None
                    and derived.get("complete") is True
                    and submitted_r is not None
                    and derived_r is not None
                    and math.isclose(
                        submitted_r,
                        derived_r,
                        rel_tol=0,
                        abs_tol=1e-12,
                    )
                    and payload.get("realized_at") == derived.get("realized_at")
                    and payload.get("outcome_evidence")
                    == derived.get("outcome_evidence")
                    and payload.get("unresolved_codes")
                    == derived.get("unresolved_codes")
                )
                if not derived_matches:
                    self._record_evidence_conflict(
                        connection,
                        setup_id,
                        "outcome_derived_mismatch",
                        encoded,
                        encoded_hash,
                    )
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_derived_mismatch",
                        "transition": "rejected",
                    }
                payload = derived
                payload["terminal_evidence"] = "broker_snapshot"
                payload["terminal_evidence_hash"] = verified_terminal[
                    "evidence_hash"
                ]
                encoded = _canonical_json(payload)
                assert encoded is not None
                encoded_hash = _digest(encoded)
                existing = connection.execute(
                    """
                    SELECT state, outcome_hash, updated_at
                    FROM trading_risk_outcomes WHERE setup_id=?
                    """,
                    (setup_id,),
                ).fetchone()
                prior_outcome_at = (
                    _utc_datetime(existing["updated_at"])
                    if existing is not None
                    else None
                )
                completion_at = _utc_datetime(verified_terminal["created_at"])
                if existing is not None and (
                    prior_outcome_at is None
                    or completion_at is None
                    or completion_at < prior_outcome_at
                ):
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_time_regression",
                        "transition": "rejected",
                    }
                existing_terminal = connection.execute(
                    """
                    SELECT evidence_hash FROM trading_risk_terminal_evidence
                    WHERE setup_id=?
                    """,
                    (setup_id,),
                ).fetchone()
                if (
                    existing_terminal is not None
                    and existing_terminal["evidence_hash"]
                    != verified_terminal["evidence_hash"]
                ):
                    self._record_evidence_conflict(
                        connection,
                        setup_id,
                        "terminal_evidence_immutable_conflict",
                        verified_terminal["evidence_json"],
                        verified_terminal["evidence_hash"],
                    )
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "terminal_evidence_immutable_conflict",
                        "transition": "rejected",
                    }
                if existing_terminal is None:
                    connection.execute(
                        """
                        INSERT INTO trading_risk_terminal_evidence
                            (setup_id, reservation_id, evidence_json,
                             evidence_hash, observed_at, lease_key,
                             fence_token, created_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            setup_id,
                            verified_terminal["reservation_id"],
                            verified_terminal["evidence_json"],
                            verified_terminal["evidence_hash"],
                            verified_terminal["observed_at"],
                            verified_terminal["lease_key"],
                            verified_terminal["fence_token"],
                            verified_terminal["created_at"],
                        ),
                    )
            if state != "COMPLETE":
                existing = connection.execute(
                    """
                    SELECT state, outcome_hash, updated_at
                    FROM trading_risk_outcomes WHERE setup_id=?
                    """,
                    (setup_id,),
                ).fetchone()
            if existing is None:
                outcome_updated_at = (
                    verified_terminal["created_at"]
                    if state == "COMPLETE"
                    else requested_now_iso
                )
                connection.execute(
                    """
                    INSERT INTO trading_risk_outcomes
                        (setup_id, state, outcome_json, outcome_hash, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (setup_id, state, encoded, encoded_hash, outcome_updated_at),
                )
                if state == "COMPLETE":
                    self._complete_setup_reservations(
                        connection, setup_id, outcome_updated_at
                    )
                connection.commit()
                return {
                    "accepted": True,
                    "idempotent": False,
                    "conflict": None,
                    "transition": "completed" if state == "COMPLETE" else "stored_unresolved",
                }
            if existing["outcome_hash"] == encoded_hash:
                if state == "COMPLETE":
                    self._complete_setup_reservations(
                        connection,
                        setup_id,
                        verified_terminal["created_at"],
                    )
                connection.commit()
                return {
                    "accepted": True,
                    "idempotent": True,
                    "conflict": None,
                    "transition": "idempotent",
                }
            if existing["state"] == "UNRESOLVED" and state == "COMPLETE":
                outcome_updated_at = verified_terminal["created_at"]
                connection.execute(
                    """
                    UPDATE trading_risk_outcomes
                    SET state=?, outcome_json=?, outcome_hash=?, updated_at=?
                    WHERE setup_id=?
                    """,
                    (state, encoded, encoded_hash, outcome_updated_at, setup_id),
                )
                self._complete_setup_reservations(
                    connection, setup_id, outcome_updated_at
                )
                connection.commit()
                return {
                    "accepted": True,
                    "idempotent": False,
                    "conflict": None,
                    "transition": "completed",
                }
            if existing["state"] == "UNRESOLVED" and state == "UNRESOLVED":
                prior_outcome_at = _utc_datetime(existing["updated_at"])
                next_outcome_at = _utc_datetime(requested_now_iso)
                if (
                    prior_outcome_at is None
                    or next_outcome_at is None
                    or next_outcome_at < prior_outcome_at
                ):
                    connection.commit()
                    return {
                        "accepted": False,
                        "idempotent": False,
                        "conflict": "outcome_time_regression",
                        "transition": "rejected",
                    }
                connection.execute(
                    """
                    UPDATE trading_risk_outcomes
                    SET outcome_json=?, outcome_hash=?, updated_at=?
                    WHERE setup_id=?
                    """,
                    (
                        encoded,
                        encoded_hash,
                        requested_now_iso,
                        setup_id,
                    ),
                )
                connection.commit()
                return {
                    "accepted": True,
                    "idempotent": False,
                    "conflict": None,
                    "transition": "updated_unresolved",
                }
            self._record_evidence_conflict(
                connection,
                setup_id,
                "outcome_immutable_conflict",
                encoded,
                encoded_hash,
            )
            connection.commit()
            return {
                "accepted": False,
                "idempotent": False,
                "conflict": "outcome_immutable_conflict",
                "transition": "rejected",
            }
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_outcome(self, setup_id: Any) -> Optional[dict[str, Any]]:
        connection = self._connect()
        try:
            row = connection.execute(
                "SELECT outcome_json FROM trading_risk_outcomes WHERE setup_id=?",
                (_text(setup_id),),
            ).fetchone()
            return json.loads(row["outcome_json"]) if row is not None else None
        finally:
            connection.close()

    def _execution_write_lock(self, write_id: str) -> Optional[_ExecutionWriteLock]:
        if _EXECUTION_WRITE_ID.fullmatch(write_id) is None:
            return None
        return _ExecutionWriteLock(self._execution_lock_dir / f"{write_id}.lock")

    def _reap_orphaned_execution_writes(
        self, connection: sqlite3.Connection
    ) -> dict[str, int]:
        """Fence only claims whose exact per-write OS lock proves owner death.

        The caller must hold ``BEGIN IMMEDIATE``.  Age is deliberately ignored:
        a hung but live broker call remains ACTIVE forever until its lock is
        released, while an unverifiable legacy/broken claim remains blocking.
        """
        connection.execute(
            """
            UPDATE trading_risk_execution_writes
            SET status='LEGACY_UNKNOWN'
            WHERE status NOT IN (
                'ACTIVE', 'ORPHANED', 'RECONCILED', 'LEGACY_UNKNOWN'
            )
            """
        )
        active_rows = connection.execute(
            """
            SELECT write_id, lock_protocol_version
            FROM trading_risk_execution_writes
            WHERE status='ACTIVE'
            """
        ).fetchall()
        newly_orphaned: list[str] = []
        newly_unknown: list[str] = []
        for claim in active_rows:
            write_id = _text(claim["write_id"])
            if int(claim["lock_protocol_version"] or 0) != _EXECUTION_WRITE_LOCK_PROTOCOL:
                newly_unknown.append(write_id)
                continue
            claim_lock = self._execution_write_lock(write_id)
            if claim_lock is None:
                newly_unknown.append(write_id)
                continue
            if not claim_lock.acquire():
                # A busy lock is positive liveness evidence.  Any other lock
                # failure is indistinguishable from an unsafe lock backend.
                if not claim_lock.busy:
                    newly_unknown.append(write_id)
                continue
            try:
                newly_orphaned.append(write_id)
            finally:
                claim_lock.release(remove=True)

        now_iso = datetime.now(timezone.utc).isoformat()
        if newly_unknown:
            placeholders = ",".join("?" for _ in newly_unknown)
            connection.execute(
                f"""
                UPDATE trading_risk_execution_writes
                SET status='LEGACY_UNKNOWN'
                WHERE write_id IN ({placeholders}) AND status='ACTIVE'
                """,
                tuple(newly_unknown),
            )
        if newly_orphaned:
            placeholders = ",".join("?" for _ in newly_orphaned)
            connection.execute(
                f"""
                UPDATE trading_risk_execution_writes
                SET status='ORPHANED', orphaned_at=?
                WHERE write_id IN ({placeholders}) AND status='ACTIVE'
                """,
                (now_iso, *newly_orphaned),
            )

        unfenced_unknown = connection.execute(
            """
            SELECT COUNT(*) AS count FROM trading_risk_execution_writes
            WHERE status='LEGACY_UNKNOWN' AND orphaned_generation IS NULL
            """
        ).fetchone()
        blocking_rows = connection.execute(
            """
            SELECT COUNT(*) AS count FROM trading_risk_execution_writes
            WHERE status IN ('ORPHANED', 'LEGACY_UNKNOWN')
            """
        ).fetchone()
        state_before_fence = connection.execute(
            """
            SELECT generation, armed FROM trading_risk_execution_state
            WHERE singleton=1
            """
        ).fetchone()
        must_fence = bool(newly_orphaned) or bool(
            unfenced_unknown is not None and int(unfenced_unknown["count"]) > 0
        ) or bool(
            blocking_rows is not None
            and int(blocking_rows["count"]) > 0
            and state_before_fence is not None
            and bool(state_before_fence["armed"])
        )
        if must_fence:
            state = state_before_fence
            if state is None:
                raise RuntimeError("execution_state_missing")
            fenced_generation = int(state["generation"]) + 1
            connection.execute(
                """
                UPDATE trading_risk_execution_state
                SET generation=?, armed=0, updated_at=? WHERE singleton=1
                """,
                (fenced_generation, now_iso),
            )
            if newly_orphaned:
                placeholders = ",".join("?" for _ in newly_orphaned)
                connection.execute(
                    f"""
                    UPDATE trading_risk_execution_writes
                    SET orphaned_generation=?
                    WHERE write_id IN ({placeholders})
                      AND status='ORPHANED'
                    """,
                    (fenced_generation, *newly_orphaned),
                )
            connection.execute(
                """
                UPDATE trading_risk_execution_writes
                SET orphaned_generation=?, orphaned_at=COALESCE(orphaned_at, ?)
                WHERE status='LEGACY_UNKNOWN' AND orphaned_generation IS NULL
                """,
                (fenced_generation, now_iso),
            )

        counts = connection.execute(
            """
            SELECT
              SUM(CASE WHEN status='ACTIVE' THEN 1 ELSE 0 END) AS active_count,
              SUM(CASE WHEN status='ORPHANED' THEN 1 ELSE 0 END) AS orphaned_count,
              SUM(CASE WHEN status='LEGACY_UNKNOWN' THEN 1 ELSE 0 END) AS unknown_count
            FROM trading_risk_execution_writes
            """
        ).fetchone()
        return {
            "active_count": int(counts["active_count"] or 0),
            "orphaned_count": int(counts["orphaned_count"] or 0),
            "unknown_count": int(counts["unknown_count"] or 0),
            "newly_fenced": int(must_fence),
        }

    def reconcile_orphaned_execution_writes(
        self,
        expected_generation: Any,
        *,
        reconciliation_started_at: Any,
        observed_at: Any,
        orders_snapshot_complete: Any,
        positions_snapshot_complete: Any,
        fills_snapshot_complete: Any,
        risk_evidence_reliable: Any,
        reconciled_accounts: Optional[Iterable[Any]] = None,
    ) -> dict[str, Any]:
        expected = _finite(expected_generation)
        started = _utc_datetime(reconciliation_started_at)
        observed = _utc_datetime(observed_at)
        if (
            expected is None
            or expected < 0
            or int(expected) != expected
            or started is None
            or observed is None
            or observed < started
        ):
            current = self.execution_state()
            return {
                "accepted": False,
                "resolved_count": 0,
                "generation": current["generation"],
                "reason": "execution_recovery_invalid",
            }
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            counts = self._reap_orphaned_execution_writes(connection)
            state = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            current_generation = int(state["generation"]) if state is not None else 0
            orphaned_rows = connection.execute(
                """
                SELECT write_id, orphaned_at, operation_kind, account,
                       setup_id, order_id, order_ref
                FROM trading_risk_execution_writes
                WHERE status='ORPHANED'
                """
            ).fetchall()
            if not orphaned_rows:
                connection.commit()
                return {
                    "accepted": True,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": None,
                }
            if (
                state is None
                or bool(state["armed"])
                or current_generation != int(expected)
            ):
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_generation_fenced",
                }
            if counts["active_count"] or counts["unknown_count"]:
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_writes_active",
                }
            try:
                covered_accounts = {
                    _token(value) for value in (reconciled_accounts or [])
                }
            except TypeError:
                covered_accounts = set()
            claim_accounts = {_token(row["account"]) for row in orphaned_rows}
            if (
                not claim_accounts
                or any(not _paper_account(value) for value in claim_accounts)
                or not claim_accounts.issubset(covered_accounts)
            ):
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_recovery_account_coverage_incomplete",
                }
            broker_visibility_proven = True
            for claim in orphaned_rows:
                operation_kind = _token(claim["operation_kind"])
                account = _token(claim["account"])
                setup_id = _text(claim["setup_id"])
                order_ref = _text(claim["order_ref"])
                if (
                    operation_kind not in {"PLACE_ORDER", "LOCAL_STATE"}
                    or not setup_id
                    or not order_ref
                ):
                    broker_visibility_proven = False
                    break
                reservation_row = connection.execute(
                    """
                    SELECT status, reservation_json
                    FROM trading_risk_reservations
                    WHERE setup_id=? AND account=?
                    ORDER BY rowid DESC LIMIT 1
                    """,
                    (setup_id, account),
                ).fetchone()
                if (
                    reservation_row is None
                    or _token(reservation_row["status"]) != "BROKER_VISIBLE"
                ):
                    broker_visibility_proven = False
                    break
                try:
                    reservation_payload = json.loads(
                        reservation_row["reservation_json"]
                    )
                except (TypeError, ValueError):
                    broker_visibility_proven = False
                    break
                if operation_kind == "LOCAL_STATE":
                    continue
                order_id = _finite(claim["order_id"])
                if order_id is None or order_id <= 0 or int(order_id) != order_id:
                    broker_visibility_proven = False
                    break
                mapping_row = connection.execute(
                    """
                    SELECT mapping_json FROM trading_risk_intent_orders
                    WHERE setup_id=? AND account=?
                      AND CAST(order_id AS INTEGER)=?
                    """,
                    (setup_id, account, int(order_id)),
                ).fetchone()
                try:
                    mapping_payload = (
                        json.loads(mapping_row["mapping_json"])
                        if mapping_row is not None
                        else None
                    )
                    broker_ids = {
                        int(value)
                        for value in reservation_payload.get(
                            "broker_order_ids", []
                        )
                        if _finite(value) is not None
                        and _finite(value) > 0
                        and int(_finite(value)) == _finite(value)
                    }
                    ack_orders = reservation_payload.get(
                        "broker_ack_evidence", {}
                    ).get("orders", [])
                    matching_acks = [
                        item
                        for item in ack_orders
                        if isinstance(item, Mapping)
                        and int(_finite(item.get("order_id")) or 0)
                        == int(order_id)
                        and _text(item.get("order_ref")) == order_ref
                    ]
                except (AttributeError, TypeError, ValueError):
                    mapping_payload = None
                    broker_ids = set()
                    matching_acks = []
                if (
                    not isinstance(mapping_payload, Mapping)
                    or _text(mapping_payload.get("order_ref")) != order_ref
                    or int(order_id) not in broker_ids
                    or len(matching_acks) != 1
                ):
                    broker_visibility_proven = False
                    break
            if not broker_visibility_proven:
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_recovery_broker_visibility_unproven",
                }
            if not all(
                flag is True
                for flag in (
                    orders_snapshot_complete,
                    positions_snapshot_complete,
                    fills_snapshot_complete,
                    risk_evidence_reliable,
                )
            ):
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_recovery_evidence_incomplete",
                }
            orphaned_times = [
                _utc_datetime(row["orphaned_at"]) for row in orphaned_rows
            ]
            if any(value is None or started <= value for value in orphaned_times):
                connection.commit()
                return {
                    "accepted": False,
                    "resolved_count": 0,
                    "generation": current_generation,
                    "reason": "execution_recovery_snapshot_not_causal",
                }
            connection.execute(
                """
                UPDATE trading_risk_execution_writes
                SET status='RECONCILED', reconciled_at=?
                WHERE status='ORPHANED'
                """,
                (observed.isoformat(),),
            )
            connection.commit()
            return {
                "accepted": True,
                "resolved_count": len(orphaned_rows),
                "generation": current_generation,
                "reason": None,
            }
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def execution_state(self) -> dict[str, Any]:
        connection = self._connect()
        try:
            row = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            if row is None:
                return {
                    "armed": False,
                    "generation": 0,
                    "reason": "execution_state_missing",
                }
            return {
                "armed": bool(row["armed"]),
                "generation": int(row["generation"]),
                "reason": None,
            }
        finally:
            connection.close()

    def transition_execution_state(
        self,
        armed: Any,
        *,
        expected_generation: Any = _UNSET,
        require_drained: Any = False,
    ) -> dict[str, Any]:
        expected = (
            None
            if expected_generation is _UNSET
            else _finite(expected_generation)
        )
        if (
            (armed is not True and armed is not False)
            or (require_drained is not True and require_drained is not False)
            or (
                expected_generation is not _UNSET
                and (
                    expected is None
                    or expected < 0
                    or int(expected) != expected
                )
            )
        ):
            current = self.execution_state()
            return {
                "updated": False,
                "armed": current["armed"],
                "generation": current["generation"],
                "reason": "execution_state_invalid",
            }
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            claim_counts = self._reap_orphaned_execution_writes(connection)
            row = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            if row is None:
                connection.rollback()
                return {
                    "updated": False,
                    "armed": False,
                    "generation": 0,
                    "reason": "execution_state_missing",
                }
            current_generation = int(row["generation"])
            current_armed = bool(row["armed"])
            if (
                expected_generation is not _UNSET
                and current_generation != int(expected)
            ):
                connection.commit()
                return {
                    "updated": False,
                    "armed": current_armed,
                    "generation": current_generation,
                    "reason": "execution_generation_fenced",
                }
            if (
                not armed
                and expected_generation is _UNSET
                and claim_counts["newly_fenced"]
            ):
                connection.commit()
                return {
                    "updated": True,
                    "armed": False,
                    "generation": current_generation,
                    "reason": None,
                }
            # Arming is never permitted to bypass the drain invariant.  The
            # flag remains accepted for API compatibility, but safety does not
            # depend on a caller remembering to set it.
            if armed:
                if claim_counts["orphaned_count"] > 0:
                    connection.commit()
                    return {
                        "updated": False,
                        "armed": current_armed,
                        "generation": current_generation,
                        "reason": "execution_writes_orphaned",
                    }
                active_count = (
                    claim_counts["active_count"] + claim_counts["unknown_count"]
                )
                if active_count > 0:
                    connection.commit()
                    return {
                        "updated": False,
                        "armed": current_armed,
                        "generation": current_generation,
                        "reason": "execution_writes_active",
                    }
                if current_armed:
                    connection.commit()
                    return {
                        "updated": False,
                        "armed": True,
                        "generation": current_generation,
                        "reason": "execution_state_already_armed",
                    }
            generation = current_generation + 1
            connection.execute(
                """
                UPDATE trading_risk_execution_state
                SET generation=?, armed=?, updated_at=? WHERE singleton=1
                """,
                (
                    generation,
                    1 if armed else 0,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            connection.commit()
            return {
                "updated": True,
                "armed": bool(armed),
                "generation": generation,
                "reason": None,
            }
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()

    def run_if_execution_generation(
        self,
        expected_generation: Any,
        operation: Any,
        *,
        claim_context: Optional[Mapping[str, Any]] = None,
        retain_until_ack: bool = False,
        on_claim_registered: Optional[Any] = None,
    ) -> dict[str, Any]:
        generation = _finite(expected_generation)
        if (
            generation is None
            or generation < 0
            or int(generation) != generation
            or not callable(operation)
            or not isinstance(retain_until_ack, bool)
            or (retain_until_ack and not callable(on_claim_registered))
        ):
            current = self.execution_state()
            return {
                "executed": False,
                "result": None,
                "armed": current["armed"],
                "generation": current["generation"],
                "reason": "execution_generation_invalid",
            }
        write_id = uuid.uuid4().hex
        raw_context = claim_context if isinstance(claim_context, Mapping) else {}
        operation_kind = _token(raw_context.get("operation_kind")) or "UNSCOPED"
        claim_account = _token(raw_context.get("account"))
        claim_setup_id = _text(raw_context.get("setup_id")) or None
        claim_order_ref = _text(raw_context.get("order_ref")) or None
        raw_order_id = _finite(raw_context.get("order_id"))
        claim_order_id = (
            int(raw_order_id)
            if raw_order_id is not None and raw_order_id > 0 and int(raw_order_id) == raw_order_id
            else None
        )
        if claim_context is not None and (
            operation_kind not in {"PLACE_ORDER", "LOCAL_STATE"}
            or not _paper_account(claim_account)
            or not claim_setup_id
            or not claim_order_ref
            or (operation_kind == "PLACE_ORDER" and claim_order_id is None)
        ):
            current = self.execution_state()
            return {
                "executed": False,
                "result": None,
                "armed": current["armed"],
                "generation": current["generation"],
                "reason": "execution_claim_context_invalid",
            }
        claim_lock = self._execution_write_lock(write_id)
        if claim_lock is None or not claim_lock.acquire():
            current = self.execution_state()
            return {
                "executed": False,
                "result": None,
                "armed": current["armed"],
                "generation": current["generation"],
                "reason": "execution_claim_lock_unavailable",
            }
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            self._reap_orphaned_execution_writes(connection)
            row = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            current_generation = int(row["generation"]) if row is not None else 0
            current_armed = bool(row["armed"]) if row is not None else False
            if (
                row is None
                or not current_armed
                or current_generation != int(generation)
            ):
                connection.commit()
                claim_lock.release(remove=True)
                return {
                    "executed": False,
                    "result": None,
                    "armed": current_armed,
                    "generation": current_generation,
                    "reason": "execution_generation_fenced",
                }
            connection.execute(
                """
                INSERT INTO trading_risk_execution_writes
                    (write_id, generation, started_at,
                     lock_protocol_version, status, operation_kind,
                     account, setup_id, order_id, order_ref)
                VALUES (?, ?, ?, ?, 'ACTIVE', ?, ?, ?, ?, ?)
                """,
                (
                    write_id,
                    current_generation,
                    datetime.now(timezone.utc).isoformat(),
                    _EXECUTION_WRITE_LOCK_PROTOCOL,
                    operation_kind,
                    claim_account or None,
                    claim_setup_id,
                    claim_order_id,
                    claim_order_ref,
                ),
            )
            connection.commit()
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            claim_lock.release(remove=True)
            raise
        finally:
            connection.close()

        if retain_until_ack:
            self._held_execution_write_locks[write_id] = claim_lock
            try:
                on_claim_registered(write_id)
            except BaseException:
                cleanup = self._connect()
                cleanup_committed = False
                try:
                    cleanup.execute("BEGIN IMMEDIATE")
                    cleanup.execute(
                        "DELETE FROM trading_risk_execution_writes WHERE write_id=?",
                        (write_id,),
                    )
                    cleanup.commit()
                    cleanup_committed = True
                except Exception:
                    if cleanup.in_transaction:
                        cleanup.rollback()
                    raise
                finally:
                    cleanup.close()
                    self._held_execution_write_locks.pop(write_id, None)
                    claim_lock.release(remove=cleanup_committed)
                raise

        try:
            result = operation()
        except BaseException:
            if retain_until_ack:
                raise
            cleanup = self._connect()
            try:
                cleanup.execute("BEGIN IMMEDIATE")
                cleanup.execute(
                    "DELETE FROM trading_risk_execution_writes WHERE write_id=?",
                    (write_id,),
                )
                cleanup.commit()
            except Exception:
                if cleanup.in_transaction:
                    cleanup.rollback()
                raise
            finally:
                cleanup.close()
                claim_lock.release(remove=True)
            raise

        completion = self._connect()
        completion_committed = False
        try:
            completion.execute("BEGIN IMMEDIATE")
            row = completion.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            if not retain_until_ack:
                completion.execute(
                    "DELETE FROM trading_risk_execution_writes WHERE write_id=?",
                    (write_id,),
                )
            completion.commit()
            completion_committed = True
        except Exception:
            if completion.in_transaction:
                completion.rollback()
            raise
        finally:
            completion.close()
            if not retain_until_ack:
                claim_lock.release(remove=completion_committed)
        current_generation = int(row["generation"]) if row is not None else 0
        current_armed = bool(row["armed"]) if row is not None else False
        generation_valid = (
            row is not None
            and current_armed
            and current_generation == int(generation)
        )
        response = {
            "executed": True,
            "result": result,
            "armed": current_armed,
            "generation": current_generation,
            "reason": (
                None
                if generation_valid
                else "execution_generation_fenced_after_write"
            ),
        }
        if retain_until_ack:
            response["write_id"] = write_id
        return response

    def acknowledge_execution_write(
        self,
        write_id: Any,
        *,
        expected_generation: Any,
    ) -> dict[str, Any]:
        claim_id = _text(write_id)
        generation = _finite(expected_generation)
        claim_lock = self._held_execution_write_locks.get(claim_id)
        if (
            _EXECUTION_WRITE_ID.fullmatch(claim_id) is None
            or generation is None
            or generation < 0
            or int(generation) != generation
            or claim_lock is None
        ):
            return {"updated": False, "reason": "execution_claim_not_owned"}
        connection = self._connect()
        committed = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            state = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            claim = connection.execute(
                """
                SELECT generation, status, operation_kind, account,
                       setup_id, order_id, order_ref
                FROM trading_risk_execution_writes WHERE write_id=?
                """,
                (claim_id,),
            ).fetchone()
            if (
                state is None
                or claim is None
                or not bool(state["armed"])
                or int(state["generation"]) != int(generation)
                or int(claim["generation"]) != int(generation)
                or _token(claim["status"]) != "ACTIVE"
            ):
                connection.commit()
                return {
                    "updated": False,
                    "reason": "execution_generation_fenced",
                }
            setup_id = _text(claim["setup_id"])
            account = _token(claim["account"])
            reservation = connection.execute(
                """
                SELECT status, reservation_json
                FROM trading_risk_reservations
                WHERE setup_id=? AND account=?
                ORDER BY rowid DESC LIMIT 1
                """,
                (setup_id, account),
            ).fetchone()
            visible = (
                reservation is not None
                and _token(reservation["status"]) == "BROKER_VISIBLE"
            )
            if visible and _token(claim["operation_kind"]) == "PLACE_ORDER":
                order_id = int(_finite(claim["order_id"]) or 0)
                mapping = connection.execute(
                    """
                    SELECT mapping_json FROM trading_risk_intent_orders
                    WHERE setup_id=? AND account=?
                      AND CAST(order_id AS INTEGER)=?
                    """,
                    (setup_id, account, order_id),
                ).fetchone()
                try:
                    mapping_payload = json.loads(mapping["mapping_json"])
                    reservation_payload = json.loads(
                        reservation["reservation_json"]
                    )
                    broker_ids = {
                        int(value)
                        for value in reservation_payload.get(
                            "broker_order_ids", []
                        )
                    }
                    ack_orders = reservation_payload.get(
                        "broker_ack_evidence", {}
                    ).get("orders", [])
                    exact_acks = [
                        item
                        for item in ack_orders
                        if isinstance(item, Mapping)
                        and int(_finite(item.get("order_id")) or 0) == order_id
                        and _text(item.get("order_ref"))
                        == _text(claim["order_ref"])
                    ]
                    visible = (
                        _text(mapping_payload.get("order_ref"))
                        == _text(claim["order_ref"])
                        and order_id in broker_ids
                        and len(exact_acks) == 1
                    )
                except (AttributeError, TypeError, ValueError):
                    visible = False
            if not visible:
                connection.commit()
                return {
                    "updated": False,
                    "reason": "execution_claim_visibility_unproven",
                }
            connection.execute(
                "DELETE FROM trading_risk_execution_writes WHERE write_id=?",
                (claim_id,),
            )
            connection.commit()
            committed = True
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        if committed:
            self._held_execution_write_locks.pop(claim_id, None)
            claim_lock.release(remove=True)
        return {"updated": True, "reason": None}

    def quarantine_execution_writes(
        self,
        write_ids: Iterable[Any],
        *,
        expected_generation: Any,
    ) -> dict[str, Any]:
        try:
            claim_ids = list(dict.fromkeys(_text(value) for value in write_ids))
        except TypeError:
            claim_ids = []
        generation = _finite(expected_generation)
        if (
            not claim_ids
            or any(_EXECUTION_WRITE_ID.fullmatch(value) is None for value in claim_ids)
            or any(value not in self._held_execution_write_locks for value in claim_ids)
            or generation is None
            or generation < 0
            or int(generation) != generation
        ):
            current = self.execution_state()
            return {
                "updated": False,
                "status": None,
                "armed": current["armed"],
                "generation": current["generation"],
                "reason": "execution_claim_not_owned",
            }
        connection = self._connect()
        committed = False
        try:
            connection.execute("BEGIN IMMEDIATE")
            placeholders = ",".join("?" for _ in claim_ids)
            claims = connection.execute(
                f"""
                SELECT write_id, generation, status
                FROM trading_risk_execution_writes
                WHERE write_id IN ({placeholders})
                """,
                tuple(claim_ids),
            ).fetchall()
            state = connection.execute(
                """
                SELECT generation, armed FROM trading_risk_execution_state
                WHERE singleton=1
                """
            ).fetchone()
            if (
                state is None
                or len(claims) != len(claim_ids)
                or any(
                    int(row["generation"]) != int(generation)
                    or _token(row["status"]) != "ACTIVE"
                    for row in claims
                )
                or int(state["generation"]) < int(generation)
            ):
                connection.commit()
                current_generation = int(state["generation"]) if state else 0
                current_armed = bool(state["armed"]) if state else False
                return {
                    "updated": False,
                    "status": None,
                    "armed": current_armed,
                    "generation": current_generation,
                    "reason": "execution_generation_fenced",
                }
            current_generation = int(state["generation"])
            if bool(state["armed"]):
                current_generation += 1
                connection.execute(
                    """
                    UPDATE trading_risk_execution_state
                    SET generation=?, armed=0, updated_at=? WHERE singleton=1
                    """,
                    (current_generation, datetime.now(timezone.utc).isoformat()),
                )
            now_iso = datetime.now(timezone.utc).isoformat()
            connection.execute(
                f"""
                UPDATE trading_risk_execution_writes
                SET status='ORPHANED', orphaned_generation=?, orphaned_at=?
                WHERE write_id IN ({placeholders}) AND status='ACTIVE'
                """,
                (current_generation, now_iso, *claim_ids),
            )
            connection.commit()
            committed = True
        except Exception:
            if connection.in_transaction:
                connection.rollback()
            raise
        finally:
            connection.close()
        if committed:
            for claim_id in claim_ids:
                claim_lock = self._held_execution_write_locks.pop(claim_id, None)
                if claim_lock is not None:
                    claim_lock.release(remove=True)
        return {
            "updated": True,
            "status": "ORPHANED",
            "armed": False,
            "generation": current_generation,
            "reason": None,
        }

    def quarantine_execution_write(
        self,
        write_id: Any,
        *,
        expected_generation: Any,
    ) -> dict[str, Any]:
        return self.quarantine_execution_writes(
            [write_id], expected_generation=expected_generation
        )

    def wait_for_execution_writes(
        self,
        *,
        timeout_seconds: Any,
        poll_interval_seconds: Any = 0.01,
    ) -> dict[str, Any]:
        timeout = _finite(timeout_seconds)
        interval = _finite(poll_interval_seconds)
        if timeout is None or timeout < 0 or interval is None or interval <= 0:
            return {
                "drained": False,
                "active_count": None,
                "reason": "execution_drain_invalid",
            }
        deadline = time.monotonic() + timeout
        while True:
            connection = self._connect()
            try:
                connection.execute("BEGIN IMMEDIATE")
                counts = self._reap_orphaned_execution_writes(connection)
                connection.commit()
                active_count = counts["active_count"] + counts["unknown_count"]
                orphaned_count = counts["orphaned_count"]
            except Exception:
                if connection.in_transaction:
                    connection.rollback()
                raise
            finally:
                connection.close()
            if orphaned_count > 0:
                return {
                    "drained": False,
                    "active_count": active_count,
                    "orphaned_count": orphaned_count,
                    "reason": "execution_writes_orphaned",
                }
            if active_count == 0:
                return {"drained": True, "active_count": 0, "reason": None}
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return {
                    "drained": False,
                    "active_count": active_count,
                    "reason": "execution_writes_active",
                }
            time.sleep(min(interval, remaining))

    def acquire_lease(
        self,
        lease_key: Any,
        owner_token: Any,
        *,
        now: Any,
        ttl_seconds: Any,
    ) -> dict[str, Any]:
        timing = self._now_seconds(now)
        ttl = _positive(ttl_seconds)
        key = _text(lease_key)
        owner = _text(owner_token)
        if timing is None or ttl is None or not key or not owner:
            return {"acquired": False, "reason": "lease_invalid", "fence_token": 0}
        now_seconds, now_iso = timing
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT owner_token, fence_token, expires_at FROM trading_risk_leases WHERE lease_key=?",
                (key,),
            ).fetchone()
            if existing is not None and existing["expires_at"] > now_seconds:
                connection.commit()
                return {
                    "acquired": False,
                    "reason": "lease_held",
                    "fence_token": int(existing["fence_token"]),
                }
            if existing is None:
                fence = 1
                connection.execute(
                    """
                    INSERT INTO trading_risk_leases
                        (lease_key, owner_token, fence_token, expires_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (key, owner, fence, now_seconds + ttl, now_iso),
                )
            else:
                fence = int(existing["fence_token"]) + 1
                connection.execute(
                    """
                    UPDATE trading_risk_leases
                    SET owner_token=?, fence_token=?, expires_at=?, updated_at=?
                    WHERE lease_key=?
                    """,
                    (owner, fence, now_seconds + ttl, now_iso, key),
                )
            connection.commit()
            return {"acquired": True, "fence_token": fence}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def renew_lease(
        self,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        *,
        now: Any,
        ttl_seconds: Any,
    ) -> dict[str, Any]:
        timing = self._now_seconds(now)
        ttl = _positive(ttl_seconds)
        fence = _finite(fence_token)
        key = _text(lease_key)
        owner = _text(owner_token)
        if (
            timing is None
            or ttl is None
            or fence is None
            or int(fence) != fence
            or not key
            or not owner
        ):
            return {"renewed": False, "reason": "lease_fenced"}
        now_seconds, now_iso = timing
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT owner_token, fence_token, expires_at FROM trading_risk_leases WHERE lease_key=?",
                (key,),
            ).fetchone()
            if (
                row is None
                or row["owner_token"] != owner
                or int(row["fence_token"]) != int(fence)
                or row["expires_at"] <= now_seconds
            ):
                connection.commit()
                return {"renewed": False, "reason": "lease_fenced"}
            connection.execute(
                "UPDATE trading_risk_leases SET expires_at=?, updated_at=? WHERE lease_key=?",
                (now_seconds + ttl, now_iso, key),
            )
            connection.commit()
            return {"renewed": True, "fence_token": int(fence)}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _transition_reservation(
        self,
        reservation_id: Any,
        target_status: str,
        *,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        now: Any,
        reason: Any = "",
        broker_order_ids: Optional[Iterable[Any]] = None,
        broker_order_evidence: Optional[Iterable[Mapping[str, Any]]] = None,
        broker_absence_evidence: Optional[Mapping[str, Any]] = None,
        execution_generation: Any = _UNSET,
    ) -> dict[str, Any]:
        timing = self._now_seconds(now)
        key = _text(lease_key)
        owner = _text(owner_token)
        fence = _finite(fence_token)
        reservation_key = _text(reservation_id)
        expected_execution_generation = (
            None
            if execution_generation is _UNSET
            else _finite(execution_generation)
        )
        if (
            timing is None
            or not key
            or not owner
            or not reservation_key
            or fence is None
            or int(fence) != fence
            or (
                execution_generation is not _UNSET
                and (
                    expected_execution_generation is None
                    or expected_execution_generation <= 0
                    or int(expected_execution_generation)
                    != expected_execution_generation
                )
            )
        ):
            return {"updated": False, "reason": "lease_fenced"}
        now_seconds, now_iso = timing
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if execution_generation is not _UNSET:
                execution_row = connection.execute(
                    """
                    SELECT generation, armed FROM trading_risk_execution_state
                    WHERE singleton=1
                    """
                ).fetchone()
                if (
                    execution_row is None
                    or not bool(execution_row["armed"])
                    or int(execution_row["generation"])
                    != int(expected_execution_generation)
                ):
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "execution_generation_fenced",
                    }
            lease = connection.execute(
                """
                SELECT owner_token, fence_token, expires_at
                FROM trading_risk_leases WHERE lease_key=?
                """,
                (key,),
            ).fetchone()
            if (
                lease is None
                or lease["owner_token"] != owner
                or int(lease["fence_token"]) != int(fence)
                or lease["expires_at"] <= now_seconds
            ):
                connection.commit()
                return {"updated": False, "reason": "lease_fenced"}
            row = connection.execute(
                """
                SELECT status, reservation_json, lease_key, updated_at
                FROM trading_risk_reservations WHERE reservation_id=?
                """,
                (reservation_key,),
            ).fetchone()
            if row is None or row["lease_key"] != key:
                connection.commit()
                return {"updated": False, "reason": "reservation_not_found"}
            previous_updated_at = _utc_datetime(row["updated_at"])
            transition_at = _utc_datetime(now_iso)
            if (
                previous_updated_at is None
                or transition_at is None
                or transition_at < previous_updated_at
            ):
                connection.commit()
                return {
                    "updated": False,
                    "reason": "reservation_time_regression",
                }
            current_status = _token(row["status"])
            allowed_from = {
                "RECONCILE_REQUIRED": {"SUBMITTING", "RECONCILE_REQUIRED"},
                "BROKER_VISIBLE": {"SUBMITTING", "RECONCILE_REQUIRED"},
                "RELEASED": {"RECONCILE_REQUIRED"},
            }
            if current_status not in allowed_from.get(target_status, set()):
                connection.commit()
                return {"updated": False, "reason": "reservation_transition_invalid"}
            payload = json.loads(row["reservation_json"])
            payload["status"] = target_status
            if target_status == "RELEASED" and not _text(reason):
                connection.commit()
                return {"updated": False, "reason": "release_reason_required"}
            if _text(reason):
                payload["transition_reason"] = _text(reason)
            if target_status == "BROKER_VISIBLE":
                raw_ids = list(broker_order_ids or [])
                normalized_ids = []
                for value in raw_ids:
                    numeric = _finite(value)
                    if numeric is None or numeric <= 0 or int(numeric) != numeric:
                        connection.commit()
                        return {"updated": False, "reason": "broker_order_ids_invalid"}
                    normalized_ids.append(int(numeric))
                if not normalized_ids:
                    connection.commit()
                    return {"updated": False, "reason": "broker_order_ids_invalid"}
                if len(normalized_ids) != len(set(normalized_ids)):
                    connection.commit()
                    return {"updated": False, "reason": "broker_order_ids_invalid"}
                expected_rows = connection.execute(
                    """
                    SELECT order_id, role, branch, mapping_json FROM trading_risk_intent_orders
                    WHERE setup_id=(
                        SELECT setup_id FROM trading_risk_reservations
                        WHERE reservation_id=?
                    )
                    """,
                    (reservation_key,),
                ).fetchall()
                expected_ids = {_scalar_key(row["order_id"]) for row in expected_rows}
                supplied_ids = {_scalar_key(value) for value in normalized_ids}
                roles = {_token(row["role"]) for row in expected_rows}
                setup_row = connection.execute(
                    """
                    SELECT i.intent_json FROM trading_risk_intents AS i
                    JOIN trading_risk_reservations AS r ON r.setup_id=i.setup_id
                    WHERE r.reservation_id=?
                    """,
                    (reservation_key,),
                ).fetchone()
                setup_payload = json.loads(setup_row["intent_json"]) if setup_row else {}
                allocations = setup_payload.get("allocations")
                strict_branches = set(range(1, len(allocations) + 1))
                actual_geometry = {
                    (int(row["branch"]), _token(row["role"])) for row in expected_rows
                }
                required_geometry = {
                    (branch, role)
                    for branch in strict_branches
                    for role in ("PARENT", "STOP", "TARGET")
                }
                if actual_geometry != required_geometry:
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "broker_order_geometry_incomplete",
                    }
                if (
                    supplied_ids != expected_ids
                    or "PARENT" not in roles
                    or "STOP" not in roles
                ):
                    connection.commit()
                    return {"updated": False, "reason": "broker_order_ids_mismatch"}
                try:
                    evidence_orders = list(broker_order_evidence)
                except (TypeError, ValueError):
                    evidence_orders = []
                if any(not isinstance(item, Mapping) for item in evidence_orders):
                    evidence_orders = []
                evidence_ids = [_scalar_key(item.get("order_id")) for item in evidence_orders]
                evidence_identities = [
                    (
                        _token(item.get("account")),
                        _scalar_key(item.get("client_id")),
                        _scalar_key(item.get("con_id")),
                        _scalar_key(item.get("order_id")),
                    )
                    for item in evidence_orders
                ]
                evidence_refs = [
                    (_token(item.get("account")), _text(item.get("order_ref")))
                    for item in evidence_orders
                ]
                evidence_perms = [int(_finite(item.get("perm_id")) or 0) for item in evidence_orders]
                if (
                    len(evidence_ids) != len(set(evidence_ids))
                    or len(evidence_identities) != len(set(evidence_identities))
                    or len(evidence_refs) != len(set(evidence_refs))
                    or any(value <= 0 for value in evidence_perms)
                    or len(evidence_perms) != len(set(evidence_perms))
                ):
                    evidence_orders = []
                evidence_by_id = {
                    _scalar_key(item.get("order_id")): item
                    for item in evidence_orders
                }
                acknowledged = {"PRESUBMITTED", "SUBMITTED"}
                mapped_perms = [
                    int(_finite(json.loads(row["mapping_json"]).get("perm_id")) or 0)
                    for row in expected_rows
                ]
                if (
                    len(evidence_by_id) != len(expected_rows)
                    or set(evidence_by_id) != expected_ids
                    or any(value <= 0 for value in mapped_perms)
                    or len(mapped_perms) != len(set(mapped_perms))
                    or any(
                        (_finite(json.loads(row["mapping_json"]).get("perm_id")) or 0) <= 0
                        or _token(evidence_by_id[_scalar_key(row["order_id"])].get("status"))
                        not in acknowledged
                        or not self._order_geometry_matches(
                            json.loads(row["mapping_json"]),
                            evidence_by_id[_scalar_key(row["order_id"])],
                        )
                        for row in expected_rows
                    )
                ):
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "broker_ack_evidence_invalid",
                    }
                payload["broker_order_ids"] = sorted(set(normalized_ids))
                role_rank = {"PARENT": 0, "STOP": 1, "TARGET": 2}
                canonical_ack_orders = []
                for expected in sorted(
                    expected_rows,
                    key=lambda item: (
                        int(item["branch"]), role_rank[_token(item["role"])]
                    ),
                ):
                    observed_order = evidence_by_id[_scalar_key(expected["order_id"])]
                    canonical_ack_orders.append(
                        {
                            "account": _token(observed_order.get("account")),
                            "con_id": int(_finite(observed_order.get("con_id")) or 0),
                            "order_id": int(_finite(observed_order.get("order_id")) or 0),
                            "perm_id": int(_finite(observed_order.get("perm_id")) or 0),
                            "client_id": int(_finite(observed_order.get("client_id")) or 0),
                            "parent_id": int(_finite(observed_order.get("parent_id")) or 0),
                            "order_ref": _text(observed_order.get("order_ref")),
                            "role": _token(expected["role"]),
                            "branch": int(expected["branch"]),
                            "action": _token(observed_order.get("action")),
                            "order_type": " ".join(
                                _token(observed_order.get("order_type")).split()
                            ),
                            "quantity": _finite(observed_order.get("quantity")),
                            "aux_price": _order_price(observed_order.get("aux_price")),
                            "limit_price": _order_price(observed_order.get("limit_price")),
                            "oca_group": _text(observed_order.get("oca_group")),
                            "oca_type": int(_finite(observed_order.get("oca_type")) or 0),
                            "tif": _token(observed_order.get("tif")),
                            "transmit": observed_order.get("transmit") is True,
                            "outside_rth": observed_order.get("outside_rth") is True,
                            "status": _token(observed_order.get("status")),
                            "filled": _finite(observed_order.get("filled")),
                            "remaining": _finite(observed_order.get("remaining")),
                            "avg_fill_price": _finite(
                                observed_order.get("avg_fill_price")
                            ),
                        }
                    )
                ack_evidence = {
                    "observed_at": now_iso,
                    "orders": canonical_ack_orders,
                }
                ack_encoded = _canonical_json(ack_evidence)
                assert ack_encoded is not None
                payload["broker_ack_evidence"] = ack_evidence
                payload["broker_ack_evidence_hash"] = _digest(ack_encoded)
            if target_status == "RELEASED":
                if not isinstance(broker_absence_evidence, Mapping):
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "broker_absence_evidence_required",
                    }
                evidence = _json_value(dict(broker_absence_evidence))
                observed_at = _utc_datetime(
                    evidence.get("observed_at") if isinstance(evidence, dict) else None
                )
                reconcile_at = _utc_datetime(row["updated_at"])
                observed_age = (
                    now_seconds - observed_at.timestamp()
                    if observed_at is not None
                    else None
                )
                if (
                    not isinstance(evidence, dict)
                    or evidence.get("snapshot_complete") is not True
                    or evidence.get("position_open") is not False
                    or _token(evidence.get("account"))
                    != _token(payload.get("account"))
                    or _scalar_key(evidence.get("con_id"))
                    != _scalar_key(payload.get("con_id"))
                    or reconcile_at is None
                    or observed_at is None
                    or observed_at < reconcile_at
                    or observed_age is None
                    or observed_age < 0
                    or observed_age > 60
                ):
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "broker_absence_evidence_invalid",
                    }

                def normalized_evidence_ids(value: Any) -> Optional[set[str]]:
                    if not isinstance(value, Iterable) or isinstance(
                        value, (str, bytes, Mapping)
                    ):
                        return None
                    ids: set[str] = set()
                    for raw_id in value:
                        numeric = _finite(raw_id)
                        if numeric is None or numeric <= 0 or int(numeric) != numeric:
                            return None
                        ids.add(_scalar_key(int(numeric)))
                    return ids

                open_ids = normalized_evidence_ids(evidence.get("open_order_ids"))
                fill_ids = normalized_evidence_ids(evidence.get("fill_order_ids"))
                expected_rows = connection.execute(
                    """
                    SELECT order_id FROM trading_risk_intent_orders
                    WHERE setup_id=(
                        SELECT setup_id FROM trading_risk_reservations
                        WHERE reservation_id=?
                    )
                    """,
                    (reservation_key,),
                ).fetchall()
                expected_ids = {_scalar_key(item["order_id"]) for item in expected_rows}
                if open_ids is None or fill_ids is None or not expected_ids:
                    connection.commit()
                    return {
                        "updated": False,
                        "reason": "broker_absence_evidence_invalid",
                    }
                if expected_ids.intersection(open_ids | fill_ids):
                    connection.commit()
                    return {"updated": False, "reason": "broker_evidence_present"}
                payload["broker_absence_evidence"] = {
                    "snapshot_complete": True,
                    "observed_at": observed_at.isoformat(),
                    "account": _token(evidence["account"]),
                    "con_id": int(float(evidence["con_id"])),
                    "position_open": False,
                    "open_order_ids": sorted(int(value) for value in open_ids),
                    "fill_order_ids": sorted(int(value) for value in fill_ids),
                }
            encoded = _canonical_json(payload)
            assert encoded is not None
            connection.execute(
                """
                UPDATE trading_risk_reservations
                SET status=?, reservation_json=?, reservation_hash=?,
                    fence_token=?, updated_at=?
                WHERE reservation_id=?
                """,
                (
                    target_status,
                    encoded,
                    _digest(encoded),
                    int(fence),
                    now_iso,
                    reservation_key,
                ),
            )
            connection.commit()
            return {"updated": True, "status": target_status}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def mark_reservation_reconcile_required(
        self,
        reservation_id: Any,
        *,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        now: Any,
        reason: Any,
    ) -> dict[str, Any]:
        return self._transition_reservation(
            reservation_id,
            "RECONCILE_REQUIRED",
            lease_key=lease_key,
            owner_token=owner_token,
            fence_token=fence_token,
            now=now,
            reason=reason,
        )

    def mark_reservation_broker_visible(
        self,
        reservation_id: Any,
        broker_order_ids: Iterable[Any],
        *,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        now: Any,
        broker_order_evidence: Optional[Iterable[Mapping[str, Any]]] = None,
        execution_generation: Any = _UNSET,
    ) -> dict[str, Any]:
        return self._transition_reservation(
            reservation_id,
            "BROKER_VISIBLE",
            lease_key=lease_key,
            owner_token=owner_token,
            fence_token=fence_token,
            now=now,
            broker_order_ids=broker_order_ids,
            broker_order_evidence=broker_order_evidence,
            execution_generation=execution_generation,
        )

    def release_reservation(
        self,
        reservation_id: Any,
        *,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
        now: Any,
        reason: Any,
        broker_absence_evidence: Optional[Mapping[str, Any]] = None,
    ) -> dict[str, Any]:
        return self._transition_reservation(
            reservation_id,
            "RELEASED",
            lease_key=lease_key,
            owner_token=owner_token,
            fence_token=fence_token,
            now=now,
            reason=reason,
            broker_absence_evidence=broker_absence_evidence,
        )

    def _risk_intents(
        self, connection: sqlite3.Connection, account: Optional[str] = None
    ) -> list[dict[str, Any]]:
        intent_rows = connection.execute(
            """
            SELECT i.setup_id, i.intent_json
            FROM trading_risk_intents AS i
            LEFT JOIN trading_risk_outcomes AS o ON o.setup_id=i.setup_id
            WHERE (? IS NULL OR i.account=?)
              AND COALESCE(o.state, '') != 'COMPLETE'
              AND EXISTS (
                  SELECT 1 FROM trading_risk_reservations AS active_r
                  WHERE active_r.setup_id=i.setup_id
                    AND active_r.status NOT IN
                        ('CANCELLED','CANCELED','REJECTED','EXPIRED',
                         'RELEASED','COMPLETED','DONE')
              )
            ORDER BY i.setup_id
            """,
            (account, account),
        ).fetchall()
        mapping_rows = connection.execute(
            """
            SELECT setup_id, role, order_id, mapping_json
            FROM trading_risk_intent_orders ORDER BY rowid
            """
        ).fetchall()
        by_setup: dict[str, list[sqlite3.Row]] = {}
        for mapping in mapping_rows:
            by_setup.setdefault(mapping["setup_id"], []).append(mapping)
        intents = []
        for row in intent_rows:
            intent = json.loads(row["intent_json"])
            mappings = by_setup.get(row["setup_id"], [])
            intent["order_ids"] = [
                json.loads(mapping["mapping_json"])["order_id"] for mapping in mappings
            ]
            intent["parent_order_ids"] = [
                json.loads(mapping["mapping_json"])["order_id"]
                for mapping in mappings
                if mapping["role"] == "PARENT"
            ]
            intents.append(intent)
        return intents

    @staticmethod
    def _active_reservation_rows(
        connection: sqlite3.Connection,
        account: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        rows = connection.execute(
            """
            SELECT status, reservation_json FROM trading_risk_reservations
            WHERE (? IS NULL OR account=?) ORDER BY rowid
            """,
            (account, account),
        ).fetchall()
        return [
            json.loads(row["reservation_json"])
            for row in rows
            if _token(row["status"]) not in _TERMINAL_RESERVATIONS
        ]

    @staticmethod
    def _live_snapshot_valid(value: Any, account: Optional[str] = None) -> bool:
        if value is None:
            return False
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
            return False
        for record in value:
            if not isinstance(record, Mapping) or not _paper_account(record.get("account")):
                return False
            if account is not None and _token(record.get("account")) != _token(account):
                return False
            if _json_value(dict(record)) is None:
                return False
        return True

    @staticmethod
    def _invalid_risk_snapshot(net_liquidation: Any) -> dict[str, Any]:
        return {
            "reliable": False,
            "net_liquidation": _positive(net_liquidation),
            "total_risk_usd": 0.0,
            "total_risk_pct": None,
            "position_notional_usd": 0.0,
            "non_position_committed_notional_usd": 0.0,
            "committed_notional_usd": 0.0,
            "committed_setup_count": 0,
            "committed_contract_count": 0,
            "committed_contracts": [],
            "direction_risk_usd": {"LONG": 0.0, "SHORT": 0.0},
            "verified_group_risk_usd": {},
            "unresolved_codes": ["store_live_input_invalid"],
            "warnings": [],
        }

    def reserve_if_allowed(
        self,
        reservation: Mapping[str, Any],
        *,
        net_liquidation: Any,
        available_funds: Any,
        min_available_funds: Any,
        positions: Optional[Iterable[Mapping[str, Any]]],
        orders: Optional[Iterable[Mapping[str, Any]]],
        policy: Optional[Mapping[str, Any]] = None,
        gross_position_value: Any = _UNSET,
        max_total_exposure_pct: Any = 20.0,
        max_positions: Any = 3,
        orders_snapshot_complete: Any = False,
        execution_generation: Any = _UNSET,
        now: Any,
        lease_key: Any,
        owner_token: Any,
        fence_token: Any,
    ) -> dict[str, Any]:
        timing = self._now_seconds(now)
        try:
            materialized_positions = list(positions) if positions is not None else None
            materialized_orders = list(orders) if orders is not None else None
        except TypeError:
            materialized_positions = None
            materialized_orders = None
        key = _text(lease_key)
        owner = _text(owner_token)
        fence = _finite(fence_token)
        expected_execution_generation = (
            None
            if execution_generation is _UNSET
            else _finite(execution_generation)
        )
        available_funds_value = _finite(available_funds)
        min_available_funds_value = _finite(min_available_funds)
        cash_inputs_valid = (
            available_funds_value is not None
            and available_funds_value >= 0
            and min_available_funds_value is not None
            and min_available_funds_value >= 0
        )
        if (
            timing is None
            or not key
            or not owner
            or fence is None
            or int(fence) != fence
            or (
                execution_generation is not _UNSET
                and (
                    expected_execution_generation is None
                    or expected_execution_generation <= 0
                    or int(expected_execution_generation)
                    != expected_execution_generation
                )
            )
        ):
            return {"allowed": False, "decision": "lease_fenced"}
        now_seconds, now_iso = timing
        connection = self._connect()
        try:
            # This lock covers both reading existing reservations and inserting
            # the next one, even when competing callers use different leases.
            connection.execute("BEGIN IMMEDIATE")
            self._reap_orphaned_execution_writes(connection)
            lease = connection.execute(
                """
                SELECT owner_token, fence_token, expires_at
                FROM trading_risk_leases WHERE lease_key=?
                """,
                (key,),
            ).fetchone()
            if (
                lease is None
                or lease["owner_token"] != owner
                or int(lease["fence_token"]) != int(fence)
                or lease["expires_at"] <= now_seconds
            ):
                connection.commit()
                return {"allowed": False, "decision": "lease_fenced"}
            if execution_generation is not _UNSET:
                execution_row = connection.execute(
                    """
                    SELECT generation, armed FROM trading_risk_execution_state
                    WHERE singleton=1
                    """
                ).fetchone()
                if (
                    execution_row is None
                    or not bool(execution_row["armed"])
                    or int(execution_row["generation"])
                    != int(expected_execution_generation)
                ):
                    connection.commit()
                    return {
                        "allowed": False,
                        "decision": "execution_generation_fenced",
                    }
            setup_id = _text(reservation.get("setup_id")) if isinstance(reservation, Mapping) else ""
            intent_row = connection.execute(
                "SELECT intent_json FROM trading_risk_intents WHERE setup_id=?",
                (setup_id,),
            ).fetchone()
            intent = json.loads(intent_row["intent_json"]) if intent_row is not None else None
            canonical_reservation = self._reservation_payload(reservation, intent or {})
            if canonical_reservation is None:
                connection.commit()
                return {"allowed": False, "decision": "risk_blocked", "risk": self._invalid_risk_snapshot(net_liquidation)}
            if execution_generation is not _UNSET:
                canonical_reservation["execution_generation"] = int(
                    expected_execution_generation
                )
            reservation_quantity = _positive(canonical_reservation.get("quantity"))
            reservation_entry = _positive(canonical_reservation.get("entry"))
            reservation_stop = _positive(canonical_reservation.get("stop"))
            reservation_limit = _positive((intent or {}).get("stop_limit"))
            reservation_direction = _token(canonical_reservation.get("direction"))
            risk_basis_price = (
                max(reservation_entry, reservation_limit)
                if reservation_direction == "LONG"
                and reservation_entry is not None
                and reservation_limit is not None
                else min(reservation_entry, reservation_limit)
                if reservation_direction == "SHORT"
                and reservation_entry is not None
                and reservation_limit is not None
                else None
            )
            risk_per_share = (
                abs(risk_basis_price - reservation_stop)
                if risk_basis_price is not None and reservation_stop is not None
                else None
            )
            cash_basis_price = (
                max(reservation_entry, reservation_limit)
                if reservation_entry is not None and reservation_limit is not None
                else None
            )
            cash_required = (
                reservation_quantity * cash_basis_price
                if reservation_quantity is not None and cash_basis_price is not None
                else None
            )
            if cash_required is None or risk_per_share is None or risk_per_share <= 0:
                connection.commit()
                return {
                    "allowed": False,
                    "decision": "risk_blocked",
                    "risk": self._invalid_risk_snapshot(net_liquidation),
                }
            canonical_reservation["cash_basis_price_usd"] = cash_basis_price
            canonical_reservation["cash_required_usd"] = cash_required
            canonical_reservation["risk_basis_price_usd"] = risk_basis_price
            canonical_reservation["risk_per_share_usd"] = risk_per_share
            reservation_encoded = _canonical_json(canonical_reservation)
            if reservation_encoded is None:
                connection.commit()
                return {"allowed": False, "decision": "risk_blocked", "risk": self._invalid_risk_snapshot(net_liquidation)}
            if _token(key) != _token(f"submit:{setup_id}"):
                connection.commit()
                return {"allowed": False, "decision": "lease_fenced"}
            existing_reservation = connection.execute(
                """
                SELECT reservation_id FROM trading_risk_reservations
                WHERE reservation_id=? OR (
                    account=? AND setup_id=? AND order_ref=?
                )
                LIMIT 1
                """,
                (
                    _text(canonical_reservation["reservation_id"]),
                    _text(canonical_reservation["account"]),
                    setup_id,
                    _text(canonical_reservation["order_ref"]),
                ),
            ).fetchone()
            if existing_reservation is not None:
                connection.commit()
                return {"allowed": False, "decision": "already_reserved"}

            snapshot_valid = self._live_snapshot_valid(
                materialized_positions,
                canonical_reservation["account"],
            ) and self._live_snapshot_valid(
                materialized_orders,
                canonical_reservation["account"],
            ) and orders_snapshot_complete is True
            fill_evidence_conflict = connection.execute(
                """
                SELECT 1 FROM trading_risk_fill_conflicts AS c
                JOIN trading_risk_fill_events AS f ON f.exec_id=c.exec_id
                LEFT JOIN trading_risk_intents AS incoming
                  ON incoming.setup_id=c.incoming_setup_id
                WHERE f.account=?
                   OR incoming.account=?
                   OR c.incoming_account=?
                LIMIT 1
                """,
                (
                    _text(canonical_reservation["account"]),
                    _text(canonical_reservation["account"]),
                    _text(canonical_reservation["account"]),
                ),
            ).fetchone() is not None
            unknown_fill_conflict = connection.execute(
                """
                SELECT 1 FROM trading_risk_fill_conflicts
                WHERE incoming_setup_id IS NULL
                  AND (
                      incoming_account IS NULL OR TRIM(incoming_account)=''
                      OR incoming_con_id IS NULL OR TRIM(incoming_con_id)=''
                      OR incoming_order_id IS NULL OR TRIM(incoming_order_id)=''
                      OR incoming_perm_id IS NULL OR incoming_perm_id<=0
                      OR incoming_client_id IS NULL OR incoming_client_id<0
                  )
                LIMIT 1
                """
            ).fetchone() is not None
            immutable_evidence_conflict = connection.execute(
                """
                SELECT 1 FROM trading_risk_evidence_conflicts AS c
                JOIN trading_risk_intents AS i ON i.setup_id=c.setup_id
                WHERE i.account=? LIMIT 1
                """,
                (_text(canonical_reservation["account"]),),
            ).fetchone() is not None
            rejected_fill_evidence = connection.execute(
                """
                SELECT 1 FROM trading_risk_rejected_fill_events
                WHERE account=? OR account IS NULL OR TRIM(account)=''
                LIMIT 1
                """,
                (_text(canonical_reservation["account"]),),
            ).fetchone() is not None
            unmapped_fill = connection.execute(
                """
                SELECT 1 FROM trading_risk_fill_events
                WHERE setup_id IS NULL AND account=? LIMIT 1
                """,
                (_text(canonical_reservation["account"]),),
            ).fetchone() is not None
            # Observe only a caller-certified complete broker-order snapshot.
            # The conflict query above intentionally precedes this write so
            # the current call keeps the core's precise unresolved child code;
            # all subsequent admissions fail on the durable conflict as well.
            snapshot_conflicts: list[dict[str, str]] = []
            if snapshot_valid and materialized_orders is not None:
                snapshot_conflicts = self._record_terminal_child_reappearances(
                    connection,
                    materialized_orders,
                    _text(canonical_reservation["account"]),
                )
            active_reservations = self._active_reservation_rows(
                connection, _text(canonical_reservation["account"])
            )
            if (
                not snapshot_valid
                or bool(snapshot_conflicts)
                or fill_evidence_conflict
                or unknown_fill_conflict
                or immutable_evidence_conflict
                or rejected_fill_evidence
                or unmapped_fill
            ):
                current_risk = self._invalid_risk_snapshot(net_liquidation)
            else:
                current_risk = aggregate_stop_risk(
                    net_liquidation,
                    materialized_positions,
                    materialized_orders,
                    self._risk_intents(
                        connection, _text(canonical_reservation["account"])
                    ),
                    active_reservations,
                )
            outcome_rows = connection.execute(
                """
                SELECT o.outcome_json FROM trading_risk_outcomes AS o
                JOIN trading_risk_intents AS i ON i.setup_id=o.setup_id
                WHERE i.account=? ORDER BY o.setup_id
                """,
                (_text(canonical_reservation["account"]),),
            ).fetchall()
            outcomes = [json.loads(row["outcome_json"]) for row in outcome_rows]
            risk_quantity = _positive(canonical_reservation.get("quantity"))
            candidate = {
                "direction": canonical_reservation.get("direction"),
                "risk_usd": (
                    risk_quantity * risk_per_share
                    if risk_quantity is not None and risk_per_share is not None
                    else None
                ),
                "group_key": canonical_reservation.get("group_key"),
                "group_verified": canonical_reservation.get("group_verified"),
            }
            risk = evaluate_projected_risk(
                current_risk,
                candidate,
                policy or DEFAULT_RISK_POLICY,
                outcomes,
                now,
            )
            risk["current_unresolved_codes"] = list(
                current_risk.get("unresolved_codes") or []
            )

            position_notional = _finite(
                current_risk.get("position_notional_usd")
            )
            non_position_notional = _finite(
                current_risk.get("non_position_committed_notional_usd")
            )
            exposure_pct = _finite(max_total_exposure_pct)
            position_limit = _finite(max_positions)
            if gross_position_value is _UNSET:
                gross_notional = None
            else:
                gross_notional = _finite(gross_position_value)
            raw_contracts = current_risk.get("committed_contracts")
            committed_contracts: set[tuple[str, str]] = set()
            contracts_valid = isinstance(raw_contracts, list)
            if contracts_valid:
                for contract in raw_contracts:
                    if not isinstance(contract, Mapping):
                        contracts_valid = False
                        break
                    identity = (
                        _text(contract.get("account")),
                        _scalar_key(contract.get("con_id")),
                    )
                    if not identity[0] or not identity[1]:
                        contracts_valid = False
                        break
                    committed_contracts.add(identity)
            quantity = _positive(canonical_reservation.get("quantity"))
            candidate_notional = cash_required if quantity is not None else None
            active_reserved_cash = 0.0
            active_cash_valid = True
            for active_reservation in active_reservations:
                active_cash = _finite(active_reservation.get("cash_required_usd"))
                if active_cash is None or active_cash < 0:
                    active_cash_valid = False
                    break
                active_reserved_cash += active_cash
            cash_capacity = (
                available_funds_value - min_available_funds_value
                if cash_inputs_valid
                else None
            )
            projected_cash_use = (
                active_reserved_cash + cash_required
                if active_cash_valid and cash_required is not None
                else None
            )
            net_liquidation_value = _positive(net_liquidation)
            limits_valid = (
                current_risk.get("reliable") is True
                and position_notional is not None
                and position_notional >= 0
                and non_position_notional is not None
                and non_position_notional >= 0
                and gross_notional is not None
                and gross_notional >= 0
                and (
                    (gross_notional > 0)
                    == (position_notional > 0)
                )
                and exposure_pct is not None
                and exposure_pct > 0
                and position_limit is not None
                and position_limit > 0
                and int(position_limit) == position_limit
                and candidate_notional is not None
                and net_liquidation_value is not None
                and contracts_valid
            )
            projected_notional = None
            notional_limit = None
            projected_contract_count = None
            capacity_reasons: list[str] = []
            candidate_contract = (
                _text(canonical_reservation.get("account")),
                _scalar_key(canonical_reservation.get("con_id")),
            )
            if not limits_valid:
                capacity_reasons.append("risk_state_unresolved")
            else:
                assert (
                    gross_notional is not None
                    and position_notional is not None
                    and non_position_notional is not None
                    and candidate_notional is not None
                    and net_liquidation_value is not None
                    and exposure_pct is not None
                    and position_limit is not None
                )
                position_component = max(gross_notional, position_notional)
                projected_notional = (
                    position_component
                    + non_position_notional
                    + candidate_notional
                )
                notional_limit = net_liquidation_value * exposure_pct / 100.0
                duplicate_contract = candidate_contract in committed_contracts
                projected_contract_count = len(committed_contracts) + (
                    0 if duplicate_contract else 1
                )
                if duplicate_contract:
                    capacity_reasons.append("duplicate_contract_committed")
                if projected_notional > notional_limit and not math.isclose(
                    projected_notional,
                    notional_limit,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                ):
                    capacity_reasons.append("max_total_exposure_exceeded")
                if projected_contract_count > int(position_limit):
                    capacity_reasons.append("max_positions_reached")
                risk["position_notional_usd"] = position_notional
                risk["gross_position_value"] = gross_notional
                risk["position_exposure_component_usd"] = position_component
                risk["non_position_committed_notional_usd"] = non_position_notional
                risk["candidate_notional_usd"] = candidate_notional
            if not cash_inputs_valid or not active_cash_valid:
                capacity_reasons.append("cash_capacity_unresolved")
            elif (
                cash_capacity is None
                or projected_cash_use is None
                or cash_capacity < 0
                or (
                    projected_cash_use > cash_capacity
                    and not math.isclose(
                        projected_cash_use,
                        cash_capacity,
                        rel_tol=1e-12,
                        abs_tol=1e-12,
                    )
                )
            ):
                capacity_reasons.append("min_cash_reserve_exceeded")
            risk["projected_total_exposure_usd"] = projected_notional
            risk["max_total_exposure_usd"] = notional_limit
            risk["available_funds_usd"] = (
                available_funds_value
                if available_funds_value is not None and available_funds_value >= 0
                else None
            )
            risk["min_available_funds_usd"] = (
                min_available_funds_value
                if min_available_funds_value is not None
                and min_available_funds_value >= 0
                else None
            )
            risk["active_reserved_cash_usd"] = (
                active_reserved_cash if active_cash_valid else None
            )
            risk["candidate_cash_required_usd"] = cash_required
            risk["projected_cash_use_usd"] = projected_cash_use
            risk["cash_capacity_usd"] = cash_capacity
            risk["committed_contract_count"] = len(committed_contracts)
            risk["projected_contract_count"] = projected_contract_count
            risk["max_positions"] = (
                int(position_limit)
                if position_limit is not None and int(position_limit) == position_limit
                else None
            )
            for reason in capacity_reasons:
                if reason not in risk["reasons"]:
                    risk["reasons"].append(reason)
            risk["allowed"] = not risk["reasons"]
            if not risk["allowed"]:
                connection.commit()
                return {"allowed": False, "decision": "risk_blocked", "risk": risk}
            connection.execute(
                """
                INSERT INTO trading_risk_reservations
                    (reservation_id, setup_id, account, order_ref, status,
                     reservation_json, reservation_hash, lease_key,
                     fence_token, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    _text(canonical_reservation["reservation_id"]),
                    setup_id,
                    _text(canonical_reservation["account"]),
                    _text(canonical_reservation["order_ref"]),
                    _text(canonical_reservation["status"]),
                    reservation_encoded,
                    _digest(reservation_encoded),
                    key,
                    int(fence),
                    now_iso,
                    now_iso,
                ),
            )
            connection.commit()
            return {"allowed": True, "decision": "reserved", "risk": risk}
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def active_reservations(self, *, now: Any = None) -> list[dict[str, Any]]:
        # Deliberately independent of lease expiry: a durable submission marker
        # must survive process failure until a broker/reconciliation path moves
        # it to a terminal status.
        connection = self._connect()
        try:
            return self._active_reservation_rows(connection)
        finally:
            connection.close()


__all__ = ["TradingRiskStore"]
