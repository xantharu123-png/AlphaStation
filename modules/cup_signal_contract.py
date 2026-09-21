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
