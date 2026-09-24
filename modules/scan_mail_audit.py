"""Per-scan mail evidence without subjects, recipients, symbols or prices.

Suppression occurrences overlap. Transport events count messages, not signals
or recipients. SMTP acceptance does not prove inbox delivery. No I/O here.
"""
from contextlib import contextmanager
from threading import local

_state = local()
MAX_COUNT = 10**9
SEMANTICS = "overlapping_reason_occurrences_and_message_events_not_inbox_delivery"
EVENTS = frozenset(f"{kind}_{event}" for kind in ("trade", "other")
                  for event in ("sender_called", "accepted", "partial", "partial_unknown", "unknown", "failed", "queued"))


def _count(value):
    return type(value) is int and 0 <= value <= MAX_COUNT


def project(value, allowed_reasons):
    """Project stored evidence through fixed reviewed identifiers."""
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        return {}
    result = {"schema_version": 1, "semantics": SEMANTICS}
    if _count(value.get("candidate_rows")):
        result["candidate_rows"] = value["candidate_rows"]
    for name, allowed in (("reason_occurrences", allowed_reasons), ("transport_events", EVENTS)):
        raw = value.get(name)
        result[name] = {key: raw[key] for key in sorted(allowed)
                        if isinstance(raw, dict) and _count(raw.get(key))}
    return result


@contextmanager
def capture(candidate_rows):
    previous = getattr(_state, "audit", None)
    audit = {"schema_version": 1, "candidate_rows": candidate_rows if _count(candidate_rows) else 0,
             "semantics": SEMANTICS, "reason_occurrences": {}, "transport_events": {}}
    _state.audit = audit
    try:
        yield audit
    finally:
        _state.audit = previous


def _add(name, key, count):
    audit = getattr(_state, "audit", None)
    if audit is not None and _count(count):
        audit[name][key] = min(MAX_COUNT, audit[name].get(key, 0) + count)


def suppressions(counts, allowed_reasons):
    try:
        if isinstance(counts, dict):
            for key in allowed_reasons:
                if key in counts:
                    _add("reason_occurrences", key, counts[key])
    except Exception:
        pass  # Diagnostics must not affect a delivery decision.


def transport(mail_class, event):
    try:
        kind = "trade" if mail_class in ("trade", "swing_trade") else "other"
        key = f"{kind}_{event}"
        if key in EVENTS:
            _add("transport_events", key, 1)
    except Exception:
        pass
