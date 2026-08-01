"""watchdog_log.py — JSONL-Ereignis-Log des Scan-Waechters (2026-07-30).

Der Waechter (api.py/bg_service.py) loggt hier jede Haenge-Episode:
  warn       — Wächter-Warnung (mailed=True/False, throttled=True bei gedrosselt)
  reset      — Haenge-Episode per Auto-Reset beendet (nur Aktien-Scheduler)
  recovery   — Entwarnung: Scan lief wieder durch
  bg_warn / bg_recovery — dasselbe fuer den Background-Scheduler (Crypto)

Der Freitags-Wochenreport fasst diese Events zusammen (Sektion "Scan-Waechter
diese Woche"). Das Log ist bewusst schlicht: eine JSON-Zeile je Event, Cap bei
MAX_LINES (FIFO-Rotation), korrupte Zeilen werden beim Lesen uebersprungen.
Datei liegt in data_cache/ (persistenter Neustart-sicherer Ort; /tmp waere wegen
PrivateTmp pro-Service isoliert).
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

_DEFAULT_PATH = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "data_cache", "watchdog_events.jsonl")
)
WATCHDOG_EVENTS_PATH = os.environ.get("WATCHDOG_EVENTS_PATH", _DEFAULT_PATH)

MAX_LINES = 2000
TRIM_TO = 1500

VALID_KINDS = (
    "warn",
    "hard_timeout",
    "reset",
    "recovery",
    "bg_warn",
    "bg_recovery",
)


def _now() -> float:
    return time.time()


def log_watchdog_event(
    kind: str,
    scanner: str,
    stuck_min: float | None = None,
    mailed: bool = False,
    throttled: bool = False,
) -> None:
    """Haengt ein Waechter-Event an. Wirft NIE — Log-Ausfall darf den
    Waechter/Mailversand nicht beeinflussen."""
    try:
        if kind not in VALID_KINDS:
            return
        rec = {
            "ts": _now(),
            "kind": kind,
            "scanner": str(scanner or "")[:80],
            "stuck_min": round(float(stuck_min), 1) if stuck_min is not None else None,
            "mailed": bool(mailed),
            "throttled": bool(throttled),
        }
        os.makedirs(os.path.dirname(WATCHDOG_EVENTS_PATH), exist_ok=True)
        _trim_if_needed()
        with open(WATCHDOG_EVENTS_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _trim_if_needed() -> None:
    try:
        if not os.path.exists(WATCHDOG_EVENTS_PATH):
            return
        if os.path.getsize(WATCHDOG_EVENTS_PATH) < 512 * 1024:
            # Groessen-Schnellcheck: kleine Datei sicher unter dem Zeilen-Cap
            return
        with open(WATCHDOG_EVENTS_PATH, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        if len(lines) <= MAX_LINES:
            return
        with open(WATCHDOG_EVENTS_PATH, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-TRIM_TO:])
    except Exception:
        pass


def load_watchdog_events(days: float = 7.0, now: float | None = None) -> list[dict[str, Any]]:
    """Liest Events der letzten `days` Tage. Korrupte Zeilen werden uebersprungen."""
    try:
        if not os.path.exists(WATCHDOG_EVENTS_PATH):
            return []
        cutoff = (_now() if now is None else now) - days * 86400.0
        out: list[dict[str, Any]] = []
        with open(WATCHDOG_EVENTS_PATH, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not isinstance(rec, dict):
                    continue
                try:
                    ts = float(rec.get("ts") or 0)
                except Exception:
                    continue
                if ts >= cutoff:
                    rec["ts"] = ts
                    out.append(rec)
        return out
    except Exception:
        return []


def summarize_watchdog_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregiert Events je Scanner + GESAMT fuer die Report-Tabelle."""
    per: dict[str, dict[str, Any]] = {}

    def _bucket(name: str) -> dict[str, Any]:
        b = per.get(name)
        if b is None:
            b = {
                "episodes": 0, "mailed": 0, "throttled": 0,
                "hard_timeouts": 0, "resets": 0, "recoveries": 0, "_durs": [],
            }
            per[name] = b
        return b

    for ev in events:
        name = str(ev.get("scanner") or "?")
        kind = str(ev.get("kind") or "")
        b = _bucket(name)
        if kind in ("warn", "bg_warn"):
            b["episodes"] += 1
            if ev.get("mailed"):
                b["mailed"] += 1
            if ev.get("throttled"):
                b["throttled"] += 1
            d = ev.get("stuck_min")
            if isinstance(d, (int, float)):
                b["_durs"].append(float(d))
        elif kind == "hard_timeout":
            b["hard_timeouts"] += 1
            if ev.get("mailed"):
                b["mailed"] += 1
            d = ev.get("stuck_min")
            if isinstance(d, (int, float)):
                b["_durs"].append(float(d))
        elif kind == "reset":
            b["episodes"] += 1
            b["resets"] += 1
            if ev.get("mailed"):
                b["mailed"] += 1
            if ev.get("throttled"):
                b["throttled"] += 1
            d = ev.get("stuck_min")
            if isinstance(d, (int, float)):
                b["_durs"].append(float(d))
        elif kind in ("recovery", "bg_recovery"):
            b["recoveries"] += 1

    def _finish(b: dict[str, Any]) -> dict[str, Any]:
        durs = b.pop("_durs")
        b["avg_stuck_min"] = round(sum(durs) / len(durs), 1) if durs else None
        return b

    rows = {name: _finish(b) for name, b in sorted(per.items())}
    total = {
        "episodes": sum(b["episodes"] for b in rows.values()),
        "mailed": sum(b["mailed"] for b in rows.values()),
        "throttled": sum(b["throttled"] for b in rows.values()),
        "hard_timeouts": sum(b["hard_timeouts"] for b in rows.values()),
        "resets": sum(b["resets"] for b in rows.values()),
        "recoveries": sum(b["recoveries"] for b in rows.values()),
    }
    return {"per_scanner": rows, "total": total, "events": len(events)}
