"""Pure delivery gate for Cup rows produced by the current daily detector.

The version is detector provenance, not a substitute for morphology validation.
Only the detector may mint it after shape and raw-close confirmation succeed.
Delivery paths must pass their expected strategy when a row can lack labels.
No rounded display value, live quote, high wick, or old geometry annotation can
stand in for the recorded daily close.
"""

from __future__ import annotations

import math
from numbers import Real
import re
from typing import Any


CUP_PATTERN_CONTRACT_VERSION = "cup_bowl_close_v2"
CUP_PLAN_CONTRACT_VERSION = "cup_causal_structure_v1"

_CUP_PROVENANCE_FIELDS = frozenset({
    "cup_pattern_version", "cup_confirmation_close", "cup_confirmation_level",
    "cup_rim_level", "cup_pattern_evidence", "CupDepth%", "cup_depth_pct",
})
_IDENTITY_FIELDS = (
    "strategy", "Strategy", "Strategie", "strategy_name", "pattern", "Pattern",
    "pattern_type", "source",
)
_STRATEGY_IDENTITY_FIELDS = ("name", "strategy", "strategy_name")


def _cup_name(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    tokens = set(re.findall(r"[a-z]+", value.lower()))
    compact = re.sub(r"[^a-z]", "", value.lower())
    return ("cup" in tokens and "handle" in tokens) or any(
        alias in compact for alias in ("cupandhandle", "cuphandle")
    )


def _cup_strategy(strategy: Any) -> bool:
    return isinstance(strategy, dict) and (
        bool(strategy.get("needs_cup_handle"))
        or any(_cup_name(strategy.get(key)) for key in _STRATEGY_IDENTITY_FIELDS)
    )


def is_cup_signal(
    row: Any, *, strategy_name: Any = None, strategy: Any = None,
) -> bool:
    """Recognize Cup identity from external context, row labels, or provenance.

    A nameless legacy row is still a Cup row when read from the Cup strategy's
    cache. Removing one label cannot disable the gate while other Cup identity
    remains. Non-Cup rows without Cup context remain unaffected.
    """
    if _cup_name(strategy_name) or _cup_strategy(strategy):
        return True
    if not isinstance(row, dict):
        return False
    if _CUP_PROVENANCE_FIELDS.intersection(row):
        return True
    if bool(row.get("needs_cup_handle")):
        return True
    if any(_cup_name(row.get(key)) for key in _IDENTITY_FIELDS):
        return True
    setup = row.get("trade_setup")
    return isinstance(setup, dict) and any(
        _cup_name(setup.get(key)) for key in _IDENTITY_FIELDS
    )


def _positive_number(value: Any) -> float | None:
    # JSON numbers only: bool is a Real but must never represent price proof.
    if isinstance(value, bool) or not isinstance(value, Real):
        return None
    try:
        number = float(value)
    except (OverflowError, TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def _round_rim_for_display(value: float) -> float:
    """Match api._round_level_price; rounding binds display, never confirms."""
    if value >= 10:
        return round(value, 2)
    if value >= 1:
        return round(value, 3)
    if value >= 0.01:
        return round(value, 5)
    return round(value, 8)


def cup_signal_contract_reason(
    row: Any, *, strategy_name: Any = None, strategy: Any = None,
) -> str | None:
    """Return a stable rejection reason; None means valid or outside Cup scope.

    This gate does not assert session freshness, executable trade geometry,
    intraday trigger confirmation, or market availability. Existing gates own
    those checks. It prevents legacy shape and wick-only signals re-entering via
    persistence after the corrected detector has been deployed.
    """
    if not isinstance(row, dict):
        return "cup_contract_invalid_row"
    if not is_cup_signal(row, strategy_name=strategy_name, strategy=strategy):
        return None

    # An otherwise-valid Cup row must not leak from a different strategy cache.
    if isinstance(strategy_name, str) and strategy_name.strip() and not _cup_name(strategy_name):
        return "cup_contract_strategy_mismatch"
    if isinstance(strategy, dict) and not _cup_strategy(strategy):
        expected_names = [strategy.get(key) for key in _STRATEGY_IDENTITY_FIELDS]
        if any(isinstance(name, str) and name.strip() for name in expected_names):
            return "cup_contract_strategy_mismatch"

    if row.get("cup_pattern_version") != CUP_PATTERN_CONTRACT_VERSION:
        return "cup_contract_legacy_or_missing_version"
    if row.get("pattern_timeframe") != "1D":
        return "cup_contract_invalid_timeframe"

    setup = row.get("trade_setup")
    for source in (row, setup if isinstance(setup, dict) else {}):
        for key in ("direction", "Signal_Direction"):
            if key in source and (
                not isinstance(source[key], str) or source[key].strip().upper() != "LONG"
            ):
                return "cup_contract_invalid_direction"

    close = _positive_number(row.get("cup_confirmation_close"))
    level = _positive_number(row.get("cup_confirmation_level"))
    rim = _positive_number(row.get("cup_rim_level"))
    if close is None or level is None or rim is None:
        return "cup_contract_invalid_raw_prices"
    if level < rim:
        return "cup_contract_confirmation_below_rim"
    # Keep the exact raw-price >= comparison used by the detector. Rounding or
    # an isclose tolerance would admit a daily close below the fixed 0.2% gate.
    required_close = level * 1.002
    if not math.isfinite(required_close) or close < required_close:
        return "cup_contract_close_not_confirmed"
    for key in ("Breakout_Level", "breakout_level"):
        if key in row:
            display_level = _positive_number(row[key])
            if display_level is None or display_level != _round_rim_for_display(rim):
                return "cup_contract_display_rim_mismatch"
    return None


def cup_signal_contract_valid(
    row: Any, *, strategy_name: Any = None, strategy: Any = None,
) -> bool:
    """Boolean form of :func:`cup_signal_contract_reason`."""
    return cup_signal_contract_reason(
        row, strategy_name=strategy_name, strategy=strategy,
    ) is None


def cup_final_plan_contract_reason(row: Any) -> str | None:
    """Require the finalized Cup receipt after cache/watch round-trips.

    This checks coherence, not positive admission. A coherent REJECT or WAIT
    retains its negative verdict; all existing structure/mail gates still run.
    Legacy measured-only plans must be rescanned, not upgraded during delivery.
    """
    if not is_cup_signal(row):
        return None
    setup = row.get("trade_setup") if isinstance(row, dict) else None
    if (not isinstance(setup, dict)
            or row.get("cup_plan_version") != CUP_PLAN_CONTRACT_VERSION
            or setup.get("cup_plan_version") != CUP_PLAN_CONTRACT_VERSION):
        return "cup_final_plan_legacy_or_missing"
    decision = row.get("structure_decision")
    if not isinstance(decision, dict) or decision != setup.get("structure_decision"):
        return "cup_final_plan_decision_mismatch"
    for name in ("structure_status", "structure_reason", "target_quality",
                 "nearest_barrier", "barrier_gate", "tp1_is_projection", "tp2_is_projection"):
        if name not in row or name not in setup or row[name] != setup[name]:
            return "cup_final_plan_metadata_mismatch"
    if (not isinstance(row["structure_status"], str)
            or row["structure_status"] not in {"ACCEPT", "WAIT_BREAK_RECLAIM", "REJECT", "STRUCTURE_UNAVAILABLE"}
            or not isinstance(row["target_quality"], str) or not row["target_quality"]
            or type(row["tp1_is_projection"]) is not bool
            or type(row["tp2_is_projection"]) is not bool):
        return "cup_final_plan_metadata_invalid"
    prices = []
    for aliases, nested_name, decision_name in (
        (("Entry", "entry"), "entry", "entry"),
        (("StopLoss", "stop_loss"), "stop", "stop"),
        (("TP1", "tp1"), "tp1", "target1"),
        (("TP2", "tp2"), "tp2", "target2"),
    ):
        price = _positive_number(setup.get(nested_name))
        if price is None or _positive_number(decision.get(decision_name)) != price:
            return "cup_final_plan_geometry_mismatch"
        if not any(alias in row for alias in aliases):
            return "cup_final_plan_geometry_mismatch"
        if any(_positive_number(row[alias]) != price for alias in aliases if alias in row):
            return "cup_final_plan_geometry_mismatch"
        prices.append(price)
    entry, stop, tp1, tp2 = prices
    risk = _positive_number(decision.get("risk"))
    if (not stop < entry < tp1 < tp2 or risk is None
            or decision.get("direction") != "LONG" or setup.get("direction") != "LONG"
            or ("stop_loss" in setup and _positive_number(setup["stop_loss"]) != stop)
            or not math.isclose(risk, entry - stop, rel_tol=1e-10, abs_tol=1e-8)
            or decision.get("status") != row["structure_status"]
            or decision.get("reason") != row["structure_reason"]
            or decision.get("barrier_gate") != row["barrier_gate"]
            or decision.get("nearest_barrier") != row["nearest_barrier"]):
        return "cup_final_plan_geometry_mismatch"
    return None
