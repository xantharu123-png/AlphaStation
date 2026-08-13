#!/usr/bin/env python3
"""Auditable, fail-closed repairs for signal_tracker.sqlite.

The default mode is a read-only dry-run.  Applying a repair requires all of:

* an explicit ``--apply`` flag and confirmation phrase,
* a manifest with a strict before-state fingerprint for every row,
* a successful SQLite integrity check,
* a consistent SQLite backup created before the first write,
* a second fingerprint check inside ``BEGIN IMMEDIATE``.

This tool deliberately does not infer historical fills from incomplete tracker
rows.  Market evidence is reviewed separately and encoded in the manifest.  A
repair therefore remains small, reproducible and attributable instead of
silently rewriting the full forward track record.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


SCHEMA_VERSION = 1
APPLY_CONFIRMATION = "APPLY_SIGNAL_REPAIR"
_UNTRACKED_AFTER_FILL_OUTCOME_DETAILS = frozenset(
    {
        "observation_window_ended_after_fill_without_complete_interval_path",
        "eval_failed_5x_after_confirmed_fill",
        "initial_interval_coverage_incomplete_after_confirmed_fill",
        "alert_day_initial_interval_unobservable_after_confirmed_fill",
        "causal_boundary_interval_level_touch_unresolved_after_confirmed_fill",
    }
)

# Signal identity and trade-plan fields are intentionally immutable here.
# Corrections may change only evaluation/output fields.
UPDATE_ALLOWLIST = frozenset(
    {
        "status",
        "outcome_detail",
        "tp1_hit_at",
        "tp2_hit_at",
        "stop_hit_at",
        "closed_at",
        "r_realized",
        "r_realized_upper",
        "r_realized_be",
        "max_favorable_r",
        "max_adverse_r",
        "last_eval_at",
        "eval_fail_count",
        "entry_filled_at",
        "entry_fill_price",
        "exit_fill_price",
        "stop_gap_slippage_r",
        "stop_gap_slippage_pct",
        "be_activated_at",
        "be_mail_sent_at",
        "be_trigger_at",
        "be_exit_fill_price",
        "be_exit_at",
        "be_exit_evidence_mode",
    }
)

EXPECTED_REQUIRED = frozenset(
    {
        "ticker",
        "scanner",
        "created_at",
        "status",
        "entry",
        "stop",
        "r_realized",
        "outcome_detail",
    }
)

INSPECT_FIELDS = (
    "id",
    "created_at",
    "delivery_accepted_at",
    "scanner",
    "ticker",
    "strategy",
    "direction",
    "status",
    "outcome_detail",
    "entry",
    "entry_filled_at",
    "entry_fill_price",
    "stop",
    "tp1",
    "tp2",
    "r_realized",
    "r_realized_upper",
    "r_realized_be",
    "tp1_hit_at",
    "tp2_hit_at",
    "stop_hit_at",
    "closed_at",
    "exit_fill_price",
    "stop_gap_slippage_r",
    "stop_gap_slippage_pct",
    "be_activated_at",
    "be_mail_sent_at",
    "be_trigger_at",
    "be_exit_fill_price",
    "be_exit_at",
    "be_exit_evidence_mode",
)


class RepairError(RuntimeError):
    """Manifest, database or safety-contract violation."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(path: Path) -> tuple[dict[str, Any], str]:
    try:
        raw = path.read_bytes()
        manifest = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepairError(f"Manifest nicht lesbar: {exc}") from exc
    if not isinstance(manifest, dict):
        raise RepairError("Manifest muss ein JSON-Objekt sein")
    return manifest, _sha256_bytes(raw)


def _table_columns(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("PRAGMA table_info(signals)").fetchall()
    if not rows:
        raise RepairError("Tabelle 'signals' fehlt")
    return {str(row[1]) for row in rows}


def _integrity_check(conn: sqlite3.Connection) -> None:
    result = conn.execute("PRAGMA integrity_check").fetchone()
    if not result or str(result[0]).lower() != "ok":
        raise RepairError(f"SQLite integrity_check fehlgeschlagen: {result!r}")
    foreign = conn.execute("PRAGMA foreign_key_check").fetchall()
    if foreign:
        raise RepairError(f"SQLite foreign_key_check meldet {len(foreign)} Fehler")


def _finite_json_value(value: Any, field: str) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise RepairError(f"{field}: NaN/Infinity ist nicht erlaubt")
    if isinstance(value, (list, dict)):
        raise RepairError(f"{field}: nur skalare Werte oder null sind erlaubt")


def validate_manifest(manifest: Mapping[str, Any], columns: set[str]) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise RepairError(
            f"schema_version muss {SCHEMA_VERSION} sein, erhalten: "
            f"{manifest.get('schema_version')!r}"
        )
    repair_id = str(manifest.get("repair_id") or "").strip()
    if not repair_id or len(repair_id) > 120:
        raise RepairError("repair_id fehlt oder ist zu lang")
    corrections = manifest.get("corrections")
    if not isinstance(corrections, list) or not corrections:
        raise RepairError("corrections muss eine nichtleere Liste sein")

    seen_ids: set[int] = set()
    normalized: list[dict[str, Any]] = []
    for index, correction in enumerate(corrections):
        label = f"corrections[{index}]"
        if not isinstance(correction, dict):
            raise RepairError(f"{label}: muss ein Objekt sein")
        row_id = correction.get("id")
        if isinstance(row_id, bool) or not isinstance(row_id, int) or row_id <= 0:
            raise RepairError(f"{label}.id: positive Ganzzahl erforderlich")
        if row_id in seen_ids:
            raise RepairError(f"{label}.id: doppelte Signal-ID {row_id}")
        seen_ids.add(row_id)

        reason = str(correction.get("reason") or "").strip()
        if len(reason) < 12 or len(reason) > 500:
            raise RepairError(f"{label}.reason: 12 bis 500 Zeichen erforderlich")
        evidence = correction.get("evidence")
        if not isinstance(evidence, dict):
            raise RepairError(f"{label}.evidence: Objekt erforderlich")
        for key in ("source", "observed_at", "summary"):
            if not str(evidence.get(key) or "").strip():
                raise RepairError(f"{label}.evidence.{key}: erforderlich")

        expected = correction.get("expected")
        updates = correction.get("updates")
        if not isinstance(expected, dict) or not isinstance(updates, dict) or not updates:
            raise RepairError(f"{label}: expected-Objekt und nichtleeres updates-Objekt erforderlich")
        missing_fingerprint = EXPECTED_REQUIRED.difference(expected)
        if missing_fingerprint:
            raise RepairError(
                f"{label}.expected: Fingerprint-Felder fehlen: "
                + ", ".join(sorted(missing_fingerprint))
            )
        forbidden = set(updates).difference(UPDATE_ALLOWLIST)
        if forbidden:
            raise RepairError(
                f"{label}.updates: nicht erlaubte Felder: " + ", ".join(sorted(forbidden))
            )
        missing_columns = set(updates).difference(columns)
        if missing_columns:
            raise RepairError(
                f"{label}.updates: Schema noch nicht ausgerollt; Spalten fehlen: "
                + ", ".join(sorted(missing_columns))
            )
        unknown_expected = set(expected).difference(columns)
        if unknown_expected:
            raise RepairError(
                f"{label}.expected: unbekannte DB-Felder: "
                + ", ".join(sorted(unknown_expected))
            )
        missing_update_fingerprint = set(updates).difference(expected)
        if missing_update_fingerprint:
            raise RepairError(
                f"{label}.expected: jedes Update-Feld braucht seinen exakten "
                "Vorzustand; es fehlen: "
                + ", ".join(sorted(missing_update_fingerprint))
            )
        if all(key in expected and _values_equal(expected[key], value) for key, value in updates.items()):
            raise RepairError(f"{label}.updates: keine effektive Aenderung")
        for key, value in expected.items():
            _finite_json_value(value, f"{label}.expected.{key}")
        for key, value in updates.items():
            _finite_json_value(value, f"{label}.updates.{key}")

        normalized.append(
            {
                "id": row_id,
                "reason": reason,
                "evidence": evidence,
                "expected": dict(expected),
                "updates": dict(updates),
            }
        )
    return normalized


def _values_equal(expected: Any, actual: Any) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if isinstance(expected, bool) or isinstance(actual, bool):
        return expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return math.isclose(float(expected), float(actual), rel_tol=1e-10, abs_tol=1e-10)
    return str(expected) == str(actual)


def _to_numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def _to_utc_timestamp(value: Any, *, row_id: int, field: str) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        raise RepairError(f"Signal-ID {row_id}: {field} ist leer")
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise RepairError(
            f"Signal-ID {row_id}: {field} ist kein gueltiger ISO-Zeitstempel"
        ) from exc
    if parsed.tzinfo is None:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            raise RepairError(f"Signal-ID {row_id}: {field} braucht eine Zeitzone")
    return parsed.astimezone(timezone.utc)


def _fetch_row(conn: sqlite3.Connection, row_id: int) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM signals WHERE id = ?", (row_id,)).fetchone()
    if row is None:
        raise RepairError(f"Signal-ID {row_id} existiert nicht")
    return dict(row)


def _validate_after_state(row_id: int, after: Mapping[str, Any]) -> None:
    """Reject internally contradictory repaired outcome state."""
    status = str(after.get("status") or "").upper()
    allowed_statuses = {
        "OPEN", "STOP_HIT", "TP2_HIT", "EXPIRED", "UNTRACKED", "NO_FILL"
    }
    if status not in allowed_statuses:
        raise RepairError(f"Signal-ID {row_id}: unbekannter Status {status!r}")
    numeric_fields = (
        "entry", "entry_fill_price", "stop", "tp1", "tp2",
        "r_realized", "r_realized_upper", "r_realized_be",
        "max_favorable_r", "max_adverse_r", "exit_fill_price",
        "stop_gap_slippage_r", "stop_gap_slippage_pct",
        "be_exit_fill_price",
    )
    for field in numeric_fields:
        if field in after and after.get(field) is not None and _to_numeric(after.get(field)) is None:
            raise RepairError(f"Signal-ID {row_id}: {field} muss eine endliche Zahl sein")
    direction = str(after.get("direction") or "").upper()
    if direction not in {"LONG", "SHORT"}:
        raise RepairError(f"Signal-ID {row_id}: ungueltige Richtung {direction!r}")
    entry = _to_numeric(after.get("entry"))
    stop = _to_numeric(after.get("stop"))
    tp1 = _to_numeric(after.get("tp1"))
    tp2 = _to_numeric(after.get("tp2"))
    if None not in (entry, stop, tp1, tp2):
        valid_geometry = (
            float(stop) < float(entry) < float(tp1) < float(tp2)
            if direction == "LONG"
            else float(tp2) < float(tp1) < float(entry) < float(stop)
        )
        if not valid_geometry:
            relation = "Stop < Entry < TP1 < TP2" if direction == "LONG" else "TP2 < TP1 < Entry < Stop"
            raise RepairError(
                f"Signal-ID {row_id}: Zielreihenfolge verletzt ({relation} erforderlich)"
            )
    timestamp_fields = (
        "created_at", "delivery_accepted_at", "entry_filled_at", "tp1_hit_at", "tp2_hit_at",
        "stop_hit_at", "closed_at", "be_activated_at", "be_mail_sent_at",
        "be_trigger_at", "be_exit_at", "last_eval_at",
    )
    timestamps = {
        field: _to_utc_timestamp(after.get(field), row_id=row_id, field=field)
        for field in timestamp_fields
        if field in after and after.get(field) is not None
    }
    created_at = timestamps.get("created_at")
    delivery_accepted_at = timestamps.get("delivery_accepted_at")
    causal_start = delivery_accepted_at or created_at
    filled_at = timestamps.get("entry_filled_at")
    closed_at = timestamps.get("closed_at")
    has_fill_time = after.get("entry_filled_at") is not None
    has_fill_price = after.get("entry_fill_price") is not None
    if has_fill_time != has_fill_price:
        raise RepairError(
            f"Signal-ID {row_id}: entry_filled_at und entry_fill_price muessen gemeinsam gesetzt sein"
        )
    if (
        created_at is not None
        and delivery_accepted_at is not None
        and delivery_accepted_at < created_at
    ):
        raise RepairError(f"Signal-ID {row_id}: Zustellannahme liegt vor Signalerzeugung")
    if causal_start is not None and filled_at is not None and filled_at < causal_start:
        raise RepairError(f"Signal-ID {row_id}: Fill liegt vor dem kausalen Signalstart")
    if filled_at is not None and closed_at is not None and closed_at < filled_at:
        raise RepairError(f"Signal-ID {row_id}: Abschluss liegt vor Fill")
    for field in ("tp1_hit_at", "tp2_hit_at", "stop_hit_at", "be_trigger_at", "be_exit_at"):
        event_at = timestamps.get(field)
        if event_at is None:
            continue
        if filled_at is not None and event_at < filled_at:
            raise RepairError(f"Signal-ID {row_id}: {field} liegt vor Fill")
        if closed_at is not None and event_at > closed_at:
            raise RepairError(f"Signal-ID {row_id}: {field} liegt nach Abschluss")
    for field in (
        "tp1_hit_at", "tp2_hit_at", "stop_hit_at", "closed_at",
        "be_activated_at", "be_mail_sent_at", "be_trigger_at", "be_exit_at",
        "last_eval_at",
    ):
        event_at = timestamps.get(field)
        if causal_start is not None and event_at is not None and event_at < causal_start:
            raise RepairError(
                f"Signal-ID {row_id}: {field} liegt vor dem kausalen Signalstart"
            )
    tp1_hit_at = timestamps.get("tp1_hit_at")
    tp2_hit_at = timestamps.get("tp2_hit_at")
    if tp1_hit_at is not None and tp2_hit_at is not None and tp1_hit_at > tp2_hit_at:
        raise RepairError(f"Signal-ID {row_id}: TP1 liegt nach TP2")
    be_activated_at = timestamps.get("be_activated_at")
    be_mail_sent_at = timestamps.get("be_mail_sent_at")
    be_trigger_at = timestamps.get("be_trigger_at")
    if be_activated_at is not None:
        if filled_at is None or be_activated_at < filled_at:
            raise RepairError(f"Signal-ID {row_id}: BE-Aktivierung braucht einen vorherigen Fill")
        if be_trigger_at is not None and be_activated_at < be_trigger_at:
            raise RepairError(f"Signal-ID {row_id}: BE-Aktivierung liegt vor dem Trigger")
    if be_mail_sent_at is not None:
        if be_activated_at is None:
            raise RepairError(f"Signal-ID {row_id}: BE-Mail braucht eine Aktivierung")
        if be_mail_sent_at < be_activated_at or (
            be_trigger_at is not None and be_mail_sent_at < be_trigger_at
        ):
            raise RepairError(f"Signal-ID {row_id}: BE-Mail liegt vor ihrer Wirksamkeit")
    be_exit_parts = (
        after.get("be_exit_fill_price"),
        after.get("be_exit_at"),
        after.get("be_exit_evidence_mode"),
    )
    if any(value is not None for value in be_exit_parts) and not all(
        value is not None and str(value).strip() for value in be_exit_parts
    ):
        raise RepairError(
            f"Signal-ID {row_id}: BE-Exit-Preis, Zeit und Evidenz muessen gemeinsam gesetzt sein"
        )
    if all(value is not None for value in be_exit_parts):
        if after.get("be_activated_at") is None or after.get("be_mail_sent_at") is None:
            raise RepairError(f"Signal-ID {row_id}: BE-Exit braucht Aktivierung und Mailzustellung")
        be_exit_at = timestamps.get("be_exit_at")
        be_effective_times = [
            timestamps.get("be_activated_at"), timestamps.get("be_mail_sent_at"),
            timestamps.get("be_trigger_at"),
        ]
        if be_exit_at is None or any(
            value is not None and be_exit_at < value for value in be_effective_times
        ):
            raise RepairError(f"Signal-ID {row_id}: BE-Exit liegt vor seiner Wirksamkeit")
    if status == "NO_FILL":
        if closed_at is None:
            raise RepairError(f"Signal-ID {row_id}: NO_FILL braucht closed_at")
        if causal_start is None:
            raise RepairError(
                f"Signal-ID {row_id}: NO_FILL braucht created_at oder delivery_accepted_at"
            )
        must_be_null = (
            "entry_filled_at",
            "entry_fill_price",
            "tp1_hit_at",
            "tp2_hit_at",
            "stop_hit_at",
            "r_realized",
            "r_realized_upper",
            "r_realized_be",
            "exit_fill_price",
            "stop_gap_slippage_r",
            "stop_gap_slippage_pct",
            "be_activated_at",
            "be_mail_sent_at",
            "be_trigger_at",
            "be_exit_fill_price",
            "be_exit_at",
            "be_exit_evidence_mode",
        )
        contradictory = [
            field for field in must_be_null
            if field in after and after.get(field) is not None
        ]
        if contradictory:
            raise RepairError(
                f"Signal-ID {row_id}: NO_FILL widerspricht gesetzten Feldern: "
                + ", ".join(contradictory)
            )
        trajectory_fields = ("max_favorable_r", "max_adverse_r")
        nonzero_trajectory = []
        for field in trajectory_fields:
            if field not in after:
                continue
            value = after.get(field)
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isclose(float(value), 0.0, rel_tol=0.0, abs_tol=1e-12)
            ):
                nonzero_trajectory.append(field)
        if nonzero_trajectory:
            raise RepairError(
                f"Signal-ID {row_id}: NO_FILL darf keine Trade-Trajektorie behalten: "
                + ", ".join(nonzero_trajectory)
            )
    if status == "OPEN":
        contradictory_open = [
            field for field in (
                "closed_at", "stop_hit_at", "tp2_hit_at", "r_realized",
                "r_realized_upper", "r_realized_be", "exit_fill_price",
            )
            if after.get(field) is not None
        ]
        if contradictory_open:
            raise RepairError(
                f"Signal-ID {row_id}: OPEN widerspricht terminalen Feldern: "
                + ", ".join(contradictory_open)
            )
    if status in {"STOP_HIT", "TP2_HIT", "EXPIRED"}:
        required_terminal = ["closed_at", "entry_filled_at", "entry_fill_price", "r_realized"]
        if status == "STOP_HIT":
            required_terminal.extend((
                "stop_hit_at", "exit_fill_price",
                "stop_gap_slippage_r", "stop_gap_slippage_pct",
            ))
        if status == "TP2_HIT":
            required_terminal.extend(("tp1_hit_at", "tp2_hit_at"))
        missing_terminal = [field for field in required_terminal if after.get(field) is None]
        if missing_terminal:
            raise RepairError(
                f"Signal-ID {row_id}: {status} fehlen Pflichtfelder: "
                + ", ".join(missing_terminal)
            )
        contradictory_events = {
            "STOP_HIT": ("tp2_hit_at",),
            "TP2_HIT": ("stop_hit_at",),
            "EXPIRED": ("stop_hit_at", "tp2_hit_at"),
        }[status]
        present_events = [field for field in contradictory_events if after.get(field) is not None]
        if present_events:
            raise RepairError(
                f"Signal-ID {row_id}: {status} widerspricht Ereignisfeldern: "
                + ", ".join(present_events)
            )
    if status == "UNTRACKED":
        if closed_at is None:
            raise RepairError(f"Signal-ID {row_id}: UNTRACKED braucht closed_at")
        must_be_null = (
            "tp1_hit_at",
            "tp2_hit_at",
            "stop_hit_at",
            "r_realized",
            "r_realized_upper",
            "r_realized_be",
            "exit_fill_price",
            "stop_gap_slippage_r",
            "stop_gap_slippage_pct",
            "be_exit_fill_price",
            "be_exit_at",
            "be_exit_evidence_mode",
        )
        contradictory = [
            field for field in must_be_null if after.get(field) is not None
        ]
        if contradictory:
            raise RepairError(
                f"Signal-ID {row_id}: UNTRACKED widerspricht terminalen Feldern: "
                + ", ".join(contradictory)
            )
        outcome_detail = str(after.get("outcome_detail") or "").strip()
        has_entry_fill = (
            after.get("entry_filled_at") is not None
            or after.get("entry_fill_price") is not None
        )
        if (
            has_entry_fill
            and outcome_detail not in _UNTRACKED_AFTER_FILL_OUTCOME_DETAILS
        ):
            raise RepairError(
                f"Signal-ID {row_id}: UNTRACKED darf Entry-Fill nur bei einem "
                "expliziten nach-Fill verlorenen Beobachtungspfad behalten"
            )
    if after.get("r_realized") is None:
        contradictory_r = [
            field for field in ("r_realized_upper", "r_realized_be")
            if field in after and after.get(field) is not None
        ]
        if contradictory_r:
            raise RepairError(
                f"Signal-ID {row_id}: ohne r_realized muessen ebenfalls leer sein: "
                + ", ".join(contradictory_r)
            )
    lower_r = _to_numeric(after.get("r_realized"))
    upper_r = _to_numeric(after.get("r_realized_upper"))
    if lower_r is not None and upper_r is not None and upper_r < lower_r:
        raise RepairError(f"Signal-ID {row_id}: r_realized_upper liegt unter r_realized")
    parsed_gap_r = None
    parsed_gap_pct = None
    gap_r = after.get("stop_gap_slippage_r")
    gap_pct = after.get("stop_gap_slippage_pct")
    if (gap_r is None) != (gap_pct is None):
        raise RepairError(
            f"Signal-ID {row_id}: Stop-Gap-R und Stop-Gap-Prozent muessen gemeinsam gesetzt sein"
        )
    if gap_r is not None or gap_pct is not None:
        parsed_gap_r = _to_numeric(gap_r)
        parsed_gap_pct = _to_numeric(gap_pct)
        if (gap_r is not None and parsed_gap_r is None) or (
            gap_pct is not None and parsed_gap_pct is None
        ):
            raise RepairError(f"Signal-ID {row_id}: Stop-Gap-Werte muessen numerisch sein")
        values = [
            value for value in (parsed_gap_r, parsed_gap_pct) if value is not None
        ]
        if status != "STOP_HIT" or any(value < 0 for value in values):
            raise RepairError(
                f"Signal-ID {row_id}: Stop-Gap-Werte sind nur als nichtnegative STOP_HIT-Metrik erlaubt"
            )
        if any(value > 0 for value in values) and str(after.get("outcome_detail") or "") != "stop_gap_slippage":
            raise RepairError(
                f"Signal-ID {row_id}: positive Stop-Gap-Kosten erfordern outcome_detail=stop_gap_slippage"
            )
        if after.get("exit_fill_price") is None:
            raise RepairError(
                f"Signal-ID {row_id}: Stop-Gap-Werte brauchen einen Exit-Fill"
            )
    if status == "STOP_HIT":
        fill = _to_numeric(after.get("entry_fill_price"))
        if fill is None:
            fill = _to_numeric(after.get("entry"))
        stop = _to_numeric(after.get("stop"))
        exit_fill = _to_numeric(after.get("exit_fill_price"))
        direction = str(after.get("direction") or "LONG").upper()
        if None in (fill, stop, exit_fill, lower_r, parsed_gap_r, parsed_gap_pct):
            raise RepairError(
                f"Signal-ID {row_id}: STOP_HIT braucht Exit-Fill sowie abgeleitete R-/Gap-Werte"
            )
        risk = abs(float(fill) - float(stop))
        if risk <= 0:
            raise RepairError(f"Signal-ID {row_id}: ungueltiges Stop-Risiko")
        expected_r = (
            (float(fill) - float(exit_fill)) / risk
            if direction == "SHORT"
            else (float(exit_fill) - float(fill)) / risk
        )
        if not math.isclose(float(lower_r), expected_r, rel_tol=1e-6, abs_tol=1e-4):
            raise RepairError(
                f"Signal-ID {row_id}: r_realized widerspricht dem Exit-Fill"
            )
        expected_gap_r = max(
            0.0,
            (float(exit_fill) - float(stop)) / risk
            if direction == "SHORT"
            else (float(stop) - float(exit_fill)) / risk,
        )
        if not math.isclose(
            parsed_gap_r, expected_gap_r, rel_tol=1e-6, abs_tol=1e-4
        ):
            raise RepairError(
                f"Signal-ID {row_id}: stop_gap_slippage_r widerspricht dem Exit-Fill"
            )
        expected_gap_pct = (
            100.0 * expected_gap_r * risk / abs(float(stop))
            if float(stop) != 0
            else 0.0
        )
        if not math.isclose(
            parsed_gap_pct, expected_gap_pct, rel_tol=1e-6, abs_tol=1e-4
        ):
            raise RepairError(
                f"Signal-ID {row_id}: stop_gap_slippage_pct widerspricht dem Exit-Fill"
            )
        has_gap_detail = str(after.get("outcome_detail") or "") == "stop_gap_slippage"
        if (expected_gap_r > 1e-12) != has_gap_detail:
            raise RepairError(
                f"Signal-ID {row_id}: outcome_detail widerspricht der Stop-Gap-Ausfuehrung"
            )
        if lower_r is not None and lower_r > 0:
            raise RepairError(f"Signal-ID {row_id}: STOP_HIT darf kein positives R haben")
    if status == "TP2_HIT":
        fill = _to_numeric(after.get("entry_fill_price"))
        stop = _to_numeric(after.get("stop"))
        tp2 = _to_numeric(after.get("tp2"))
        if None in (fill, stop, tp2, lower_r):
            raise RepairError(f"Signal-ID {row_id}: TP2_HIT braucht Fill, Stop, TP2 und R")
        risk = abs(float(fill) - float(stop))
        if risk <= 0:
            raise RepairError(f"Signal-ID {row_id}: ungueltiges TP2-Risiko")
        expected_r = (
            (float(fill) - float(tp2)) / risk
            if direction == "SHORT"
            else (float(tp2) - float(fill)) / risk
        )
        if expected_r <= 0 or not math.isclose(
            float(lower_r), expected_r, rel_tol=1e-6, abs_tol=1e-4
        ):
            raise RepairError(f"Signal-ID {row_id}: TP2-R widerspricht Fill und Ziel")
    if all(value is not None for value in be_exit_parts):
        fill = _to_numeric(after.get("entry_fill_price"))
        stop = _to_numeric(after.get("stop"))
        be_exit = _to_numeric(after.get("be_exit_fill_price"))
        be_r = _to_numeric(after.get("r_realized_be"))
        if None in (fill, stop, be_exit, be_r):
            raise RepairError(f"Signal-ID {row_id}: BE-Exit braucht Fill, Stop und BE-R")
        risk = abs(float(fill) - float(stop))
        if risk <= 0:
            raise RepairError(f"Signal-ID {row_id}: ungueltiges BE-Risiko")
        expected_be_r = (
            (float(fill) - float(be_exit)) / risk
            if direction == "SHORT"
            else (float(be_exit) - float(fill)) / risk
        )
        if not math.isclose(float(be_r), expected_be_r, rel_tol=1e-6, abs_tol=1e-4):
            raise RepairError(f"Signal-ID {row_id}: r_realized_be widerspricht dem BE-Exit")


def verify_before_state(
    conn: sqlite3.Connection,
    corrections: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    preview: list[dict[str, Any]] = []
    for correction in corrections:
        row = _fetch_row(conn, int(correction["id"]))
        mismatches = []
        for field, expected in correction["expected"].items():
            actual = row.get(field)
            if not _values_equal(expected, actual):
                mismatches.append({"field": field, "expected": expected, "actual": actual})
        if mismatches:
            raise RepairError(
                f"Signal-ID {correction['id']}: Before-State stimmt nicht: "
                + _canonical_json(mismatches)
            )
        after = dict(row)
        after.update(correction["updates"])
        _validate_after_state(int(correction["id"]), after)
        preview.append(
            {
                "id": int(correction["id"]),
                "ticker": row.get("ticker"),
                "scanner": row.get("scanner"),
                "created_at": row.get("created_at"),
                "reason": correction["reason"],
                "changes": {
                    key: {"before": row.get(key), "after": value}
                    for key, value in correction["updates"].items()
                },
            }
        )
    return preview


def _backup_database(db_path: Path, backup_dir: Path, repair_id: str) -> tuple[Path, str]:
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = _utc_now().strftime("%Y%m%dT%H%M%SZ")
    safe_id = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in repair_id)[:80]
    backup_path = backup_dir / f"signal_tracker_before_{safe_id}_{stamp}.sqlite"
    if backup_path.exists():
        raise RepairError(f"Backup-Ziel existiert bereits: {backup_path}")
    source = sqlite3.connect(str(db_path), timeout=30)
    target = sqlite3.connect(str(backup_path), timeout=30)
    try:
        source.backup(target)
        target.commit()
        _integrity_check(target)
    finally:
        target.close()
        source.close()
    return backup_path, _sha256_file(backup_path)


def _apply_updates(
    conn: sqlite3.Connection,
    corrections: Sequence[Mapping[str, Any]],
) -> None:
    for correction in corrections:
        updates = dict(correction["updates"])
        fields = sorted(updates)
        assignments = ", ".join(f'"{field}" = ?' for field in fields)
        values = [updates[field] for field in fields]
        cursor = conn.execute(
            f"UPDATE signals SET {assignments} WHERE id = ?",
            (*values, int(correction["id"])),
        )
        if cursor.rowcount != 1:
            raise RepairError(f"Signal-ID {correction['id']}: UPDATE traf {cursor.rowcount} Zeilen")


def _append_audit_record(path: Path, record: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _canonical_json(record) + "\n"
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())


def _same_file_or_path(left: Path, right: Path) -> bool:
    """Detect textual aliases and existing hardlinks/symlinks."""
    if left.resolve() == right.resolve():
        return True
    try:
        return os.path.samefile(left, right)
    except (FileNotFoundError, OSError):
        return False


def _guard_audit_path(audit_path: Path, protected: Sequence[Path]) -> None:
    if any(_same_file_or_path(audit_path, item) for item in protected):
        raise RepairError("Auditlog darf nicht mit DB, Manifest oder Backup kollidieren")


def inspect_tickers(db_path: Path, tickers: Iterable[str]) -> list[dict[str, Any]]:
    wanted = sorted({str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()})
    if not wanted:
        return []
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        _integrity_check(conn)
        columns = _table_columns(conn)
        fields = [field for field in INSPECT_FIELDS if field in columns]
        marks = ",".join("?" for _ in wanted)
        rows = conn.execute(
            f"SELECT {', '.join(fields)} FROM signals "
            f"WHERE UPPER(ticker) IN ({marks}) ORDER BY created_at, id",
            wanted,
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def run_repair(
    db_path: Path,
    manifest_path: Path,
    *,
    apply: bool = False,
    confirmation: str = "",
    backup_dir: Path | None = None,
    audit_log: Path | None = None,
) -> dict[str, Any]:
    db_path = db_path.resolve()
    manifest_path = manifest_path.resolve()
    if not db_path.is_file():
        raise RepairError(f"Tracker-DB fehlt: {db_path}")
    manifest, manifest_sha256 = _read_manifest(manifest_path)

    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA query_only = ON")
        _integrity_check(conn)
        columns = _table_columns(conn)
        corrections = validate_manifest(manifest, columns)
        preview = verify_before_state(conn, corrections)
    finally:
        conn.close()

    result: dict[str, Any] = {
        "status": "dry_run_ok",
        "repair_id": str(manifest["repair_id"]),
        "database": str(db_path),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "correction_count": len(corrections),
        "preview": preview,
        "applied": False,
    }
    if not apply:
        return result
    if confirmation != APPLY_CONFIRMATION:
        raise RepairError(
            f"Apply verweigert: --confirm {APPLY_CONFIRMATION} ist erforderlich"
        )
    if backup_dir is None or audit_log is None:
        raise RepairError("Apply erfordert --backup-dir und --audit-log")

    audit_path = audit_log.resolve()
    _guard_audit_path(audit_path, (db_path, manifest_path))
    if audit_path.parent == db_path.parent and audit_path.name in {
        db_path.name + "-wal", db_path.name + "-shm"
    }:
        raise RepairError("Auditlog darf keine SQLite-Sidecar-Datei sein")

    backup_path, backup_sha256 = _backup_database(
        db_path, backup_dir.resolve(), str(manifest["repair_id"])
    )

    _guard_audit_path(audit_path, (backup_path.resolve(),))
    apply_run_id = (
        f"{_utc_now().strftime('%Y%m%dT%H%M%S.%fZ')}-"
        f"{manifest_sha256[:12]}"
    )
    prepared_record = {
        "schema_version": SCHEMA_VERSION,
        "event": "signal_tracker_repair_prepared",
        "prepared_at": _utc_now().isoformat(),
        "apply_run_id": apply_run_id,
        "repair_id": str(manifest["repair_id"]),
        "database": str(db_path),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "backup": str(backup_path),
        "backup_sha256": backup_sha256,
        "correction_count": len(corrections),
        "preview": preview,
    }
    # A durable PREPARED record is written before touching the database.  If
    # the final APPLIED append later fails, operations can still identify the
    # exact backup/manifest and resolve the interrupted audit chain safely.
    _append_audit_record(audit_path, prepared_record)

    conn = sqlite3.connect(str(db_path), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("BEGIN IMMEDIATE")
        _integrity_check(conn)
        # Protect against a scheduler/evaluator write between dry-run and lock.
        verify_before_state(conn, corrections)
        _apply_updates(conn, corrections)
        _integrity_check(conn)
        conn.execute("COMMIT")
    except Exception:
        try:
            conn.execute("ROLLBACK")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()

    audit_record = {
        "schema_version": SCHEMA_VERSION,
        "event": "signal_tracker_repair_applied",
        "applied_at": _utc_now().isoformat(),
        "apply_run_id": apply_run_id,
        "repair_id": str(manifest["repair_id"]),
        "database": str(db_path),
        "manifest": str(manifest_path),
        "manifest_sha256": manifest_sha256,
        "backup": str(backup_path),
        "backup_sha256": backup_sha256,
        "correction_count": len(corrections),
        "preview": preview,
    }
    try:
        _append_audit_record(audit_path, audit_record)
    except Exception as exc:
        raise RepairError(
            "DB-Korrektur wurde committed, aber der finale APPLIED-Auditeintrag "
            f"schlug fehl. PREPARED apply_run_id={apply_run_id} pruefen: {exc}"
        ) from exc
    result.update(
        {
            "status": "applied",
            "applied": True,
            "backup": str(backup_path),
            "backup_sha256": backup_sha256,
            "audit_log": str(audit_path),
            "apply_run_id": apply_run_id,
        }
    )
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path, help="Pfad zu signal_tracker.sqlite")
    parser.add_argument("--manifest", type=Path, help="Geprueftes JSON-Korrekturmanifest")
    parser.add_argument(
        "--inspect-ticker",
        action="append",
        default=[],
        help="Read-only: Zeilen eines Tickers anzeigen (wiederholbar)",
    )
    parser.add_argument("--apply", action="store_true", help="Korrekturen wirklich anwenden")
    parser.add_argument("--confirm", default="", help="Zusaetzliche Apply-Bestaetigung")
    parser.add_argument("--backup-dir", type=Path, help="Pflichtziel fuer konsistentes Backup")
    parser.add_argument("--audit-log", type=Path, help="Pflichtpfad fuer append-only JSONL-Audit")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.inspect_ticker:
            if args.apply or args.manifest:
                raise RepairError("--inspect-ticker ist ein eigener read-only Modus")
            payload = {
                "status": "inspect_ok",
                "database": str(args.db.resolve()),
                "rows": inspect_tickers(args.db.resolve(), args.inspect_ticker),
            }
        else:
            if args.manifest is None:
                raise RepairError("--manifest ist erforderlich")
            payload = run_repair(
                args.db,
                args.manifest,
                apply=args.apply,
                confirmation=args.confirm,
                backup_dir=args.backup_dir,
                audit_log=args.audit_log,
            )
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except RepairError as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
