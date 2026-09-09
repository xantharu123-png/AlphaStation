"""Aggregate-only BI observation; never a signal or execution authorization.

The caller supplies the actual indicator registry. This module has no market,
network, filesystem, broker, persistence, or application imports. Counters belong
to one scanner run and must be serialized by that run's owner (not thread-safe).
Unknown result text, tickers, prices, and indicator descriptions are never copied.
"""
from __future__ import annotations

from datetime import datetime
import re


_FACTOR_COUNT = 20
_HARD_GATES = (
    "last_bar_pump", "range_breakdown", "recent_bearish_pressure",
    "recent_bullish_pressure", "unknown",
)
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,95}\Z")
_FACTOR_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_REVISION = re.compile(r"(?:[0-9a-f]{7,40}(?:-dirty|-tree-unknown)?|unknown)\Z")


def _token(value, label):
    if type(value) is not str or _TOKEN.fullmatch(value) is None:
        raise ValueError("Invalid BI diagnostic " + label)
    return value


def create_bi_diagnostics(*, direction, indicator_specs, required_green,
                          contract_version, run_id, code_revision, started_at):
    """Create one JSON-safe run aggregate from trusted scanner metadata.

    Registry entries may be original four-tuples or (id, key) pairs; names and
    weighted points are deliberately not retained. Required IDs are 1..20.
    started_at must explicitly identify a timezone. No clock is read here.
    """
    if type(direction) is not str or direction not in ("long", "short"):
        raise ValueError("Invalid BI diagnostic direction")
    if type(required_green) is not int or not 1 <= required_green <= _FACTOR_COUNT:
        raise ValueError("Invalid BI diagnostic required_green")
    if type(indicator_specs) not in (tuple, list) or len(indicator_specs) != _FACTOR_COUNT:
        raise ValueError("Invalid BI diagnostic registry")
    keys = []
    for expected_id, spec in enumerate(indicator_specs, 1):
        if type(spec) not in (tuple, list) or len(spec) not in (2, 4):
            raise ValueError("Invalid BI diagnostic registry entry")
        indicator_id, key = spec[:2]
        if (type(indicator_id) is not int or indicator_id != expected_id
                or type(key) is not str or _FACTOR_KEY.fullmatch(key) is None
                or key in keys):
            raise ValueError("Invalid BI diagnostic registry identity")
        keys.append(key)
    contract_version = _token(contract_version, "contract_version")
    run_id = _token(run_id, "run_id")
    if type(code_revision) is not str or _REVISION.fullmatch(code_revision) is None:
        raise ValueError("Invalid BI diagnostic code_revision")
    if type(started_at) is not str or len(started_at) > 64:
        raise ValueError("Invalid BI diagnostic started_at")
    try:
        timestamp = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        if timestamp.tzinfo is None or timestamp.utcoffset() is None:
            raise ValueError
    except (ValueError, OverflowError):
        raise ValueError("Invalid BI diagnostic started_at") from None
    return {
        "schema_version": 1,
        "scanner": "bi_" + direction,
        "direction": direction,
        "required_green": required_green,
        "run_id": run_id,
        "code_revision": code_revision,
        "contract_version": contract_version,
        "started_at": timestamp.isoformat(),
        "evaluated": 0,
        "schema_invalid": 0,
        "below_required": 0,
        "incomplete": 0,
        "pre_hard_gate_qualified": 0,
        "core_valid_count": 0,
        "payload_accepted_count": 0,
        "observation_errors": 0,
        "green_count_histogram": {str(i): 0 for i in range(_FACTOR_COUNT + 1)},
        "available_count_histogram": {str(i): 0 for i in range(_FACTOR_COUNT + 1)},
        "bar_count_histogram": {**{str(i): 0 for i in range(36, 51)}, "other": 0},
        "factor_counts": {
            key: {"evaluated": 0, "green": 0, "red": 0, "unavailable": 0}
            for key in keys
        },
        "first_hard_gate_counts": {key: 0 for key in _HARD_GATES},
        # Pairwise co-failures, not independent probabilities and not rows.
        # Only known red factors count; unavailable is never silently red.
        "failed_pair_counts": {f"{a:02}:{b:02}": 0 for a in range(1, 20) for b in range(a + 1, 21)},
        "consolidation_days_histogram": {**{str(i): 0 for i in range(51)}, "other": 0},
    }


def _validated_observation(diagnostics, result, payload_accepted):
    """Return only fixed primitive facts, or None for malformed result schemas."""
    if type(payload_accepted) is not bool:
        return None
    try:
        if not isinstance(result, tuple) or len(result) != 8 or type(result[0]) is not bool:
            return None
        checks = result.indicator_checks
        green = result.green_count
        available = result.available_count
        required = result.required_green
        contract = result.indicator_contract_ok
        version = result.contract_version
        hard_gates = result.hard_gate_failures
        if (type(checks) not in (tuple, list) or len(checks) != _FACTOR_COUNT
                or type(green) is not int or not 0 <= green <= _FACTOR_COUNT
                or type(available) is not int or not 0 <= available <= _FACTOR_COUNT
                or type(required) is not int or required != diagnostics["required_green"]
                or type(contract) is not bool or type(version) is not str
                or version != diagnostics["contract_version"]
                or type(hard_gates) not in (tuple, list) or len(hard_gates) > 64
                or any(type(item) is not str for item in hard_gates)):
            return None
        flags = []
        for expected_id, (key, check) in enumerate(zip(diagnostics["factor_counts"], checks), 1):
            if (type(check) is not dict or type(check.get("id")) is not int
                    or check["id"] != expected_id or type(check.get("key")) is not str
                    or check["key"] != key or type(check.get("available")) is not bool
                    or type(check.get("passed")) is not bool):
                return None
            if check["passed"] and not check["available"]:
                return None
            flags.append((check["available"], check["passed"]))
        if (green != sum(passed for _, passed in flags)
                or available != sum(known for known, _ in flags)
                or contract != (available == _FACTOR_COUNT)):
            return None
        prequalified = contract and green >= required
        core_valid = result[0]
        if core_valid and (not prequalified or hard_gates):
            return None
        if payload_accepted and not prequalified:
            return None
        # Hard-gate checks run only after confluence qualification. A stray
        # reason on a rejected low-confluence row is not causal gate evidence.
        first_gate = None
        if prequalified and hard_gates:
            first = hard_gates[0]
            first_gate = first if first in _HARD_GATES else "unknown"
        return (flags, green, available, prequalified, core_valid, first_gate)
    except Exception:
        # Broken/missing result attributes are schema failures, never copied
        # exception strings. Counter-storage errors remain the caller's concern.
        return None


def observe_bi_analysis(diagnostics, result, *, bar_count, payload_accepted):
    """Observe one attempt; mutate only diagnostics and return no authorization.

    top.evaluated includes malformed schemas; confluence/factor histograms do
    not. Per factor, evaluated=green+red and evaluated+unavailable equals the
    number of schema-valid observations. incomplete and below_required can
    overlap. bar_count_histogram includes every observation. A normal None
    payload is supplied as payload_accepted=False, not a schema error.
    payload_accepted means only that the confluence payload helper returned a
    value. It is independent of core_valid: that helper does not test hard
    gates. Neither counter is final scanner acceptance or trading permission.
    """
    if type(diagnostics) is not dict:
        raise TypeError("BI diagnostics must be a run dictionary")
    valid_bar_count = type(bar_count) is int and bar_count >= 0
    observation = _validated_observation(diagnostics, result, payload_accepted) if valid_bar_count else None
    bucket = str(bar_count) if valid_bar_count and 36 <= bar_count <= 50 else "other"
    diagnostics["evaluated"] += 1
    diagnostics["bar_count_histogram"][bucket] += 1
    if observation is None:
        diagnostics["schema_invalid"] += 1
        return None
    flags, green, available, prequalified, core_valid, first_gate = observation
    diagnostics["green_count_histogram"][str(green)] += 1
    diagnostics["available_count_histogram"][str(available)] += 1
    diagnostics["below_required"] += int(green < diagnostics["required_green"])
    diagnostics["incomplete"] += int(available < _FACTOR_COUNT)
    diagnostics["pre_hard_gate_qualified"] += int(prequalified)
    diagnostics["core_valid_count"] += int(core_valid)
    diagnostics["payload_accepted_count"] += int(payload_accepted)
    if first_gate is not None:
        diagnostics["first_hard_gate_counts"][first_gate] += 1
    for counts, (known, passed) in zip(diagnostics["factor_counts"].values(), flags):
        counts["evaluated"] += int(known)
        counts["green"] += int(passed)
        counts["red"] += int(known and not passed)
        counts["unavailable"] += int(not known)
    red_ids = [i for i, (known, passed) in enumerate(flags, 1) if known and not passed]
    for offset, a in enumerate(red_ids):
        for b in red_ids[offset + 1:]:
            diagnostics["failed_pair_counts"][f"{a:02}:{b:02}"] += 1
    days = getattr(result, "consolidation_days", None)
    day_bucket = str(days) if type(days) is int and 0 <= days <= 50 else "other"
    diagnostics["consolidation_days_histogram"][day_bucket] += 1
    return None
