"""Immutable LEAPS transport objects. Importing this module starts no services."""
from collections.abc import Mapping
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import date, datetime
from decimal import Decimal
import re
from types import MappingProxyType
from typing import Any


class LeapsDataError(ValueError):
    """Stable safe reason, never provider bodies, credentials or transport errors."""

    def __init__(self, code: str, scope: str):
        if scope not in ("contract", "underlying", "provider") or not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
            raise ValueError("invalid LEAPS error descriptor")
        self.code, self.scope = code, scope
        super().__init__(f"{scope}:{code}")


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze(item) for item in value)
    return value


class _Immutable:
    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if item.name.endswith("_ns") and value is not None:
                from .contracts import parse_ns_epoch
                parse_ns_epoch(value)
                if type(value) is not int:
                    raise LeapsDataError("invalid_ns_epoch", "provider")
            object.__setattr__(self, item.name, _freeze(getattr(self, item.name)))

    def to_json_dict(self) -> dict:
        return to_json_value(self)


def to_json_value(value: Any, *, _field: str = "") -> Any:
    """Exact wire representation: decimal strings and integer-string ns epochs."""
    if value is None:
        return None
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise LeapsDataError("nonfinite_serialization", "provider")
        return str(value)
    if _field.endswith("_ns"):
        if type(value) is not int:
            raise LeapsDataError("invalid_ns_epoch", "provider")
        return str(value)
    if is_dataclass(value):
        return {item.name: to_json_value(getattr(value, item.name), _field=item.name) for item in fields(value)}
    if isinstance(value, Mapping):
        return {key: to_json_value(item, _field=str(key)) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [to_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return value


@dataclass(frozen=True)
class OptionContract(_Immutable):
    id: str
    provider_symbol: str
    underlying: str
    right: str
    strike: Decimal
    expiry: date
    currency: str
    shares: int
    premium_multiplier: Decimal
    exercise_style: str
    settlement: str
    terms_fingerprint: str
    evidence: Mapping[str, Any]


@dataclass(frozen=True)
class QuoteObservation(_Immutable):
    bid: Decimal | None = None
    ask: Decimal | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    source_ns: int | None = None
    received_ns: int | None = None
    size_unit: str | None = None
    feed_mode: str | None = None
    feed_lag_s: Decimal | None = None
    source: str | None = None

    def __post_init__(self):
        from .contracts import parse_decimal
        for name in ("bid", "ask"):
            value = getattr(self, name)
            if value is not None:
                if not isinstance(value, Decimal):
                    raise LeapsDataError("invalid_quote_decimal", "contract")
                parse_decimal(value)
                if (name == "ask" and value <= 0) or (name == "bid" and value < 0):
                    raise LeapsDataError("invalid_quote_price", "contract")
        super().__post_init__()


@dataclass(frozen=True)
class UnderlyingObservation(_Immutable):
    ticker: str
    price: Decimal | None
    price_kind: str | None
    source_ns: int | None
    feed_mode: str | None


@dataclass(frozen=True)
class QuoteAssessment(_Immutable):
    algebra_eligible: bool
    comparison_eligible: bool
    liquidity_status: str
    reasons: tuple[str, ...]
    expiry_ns: int | None


@dataclass(frozen=True)
class GreeksObservation(_Immutable):
    delta: Decimal | None = None
    iv: Decimal | None = None
    raw: Mapping[str, Any] = field(default_factory=dict)
    units_evidence: Mapping[str, Any] | None = None
    model_evidence: Mapping[str, Any] | None = None
    source_ns: int | None = None

    def __post_init__(self):
        from .contracts import parse_decimal
        for name in ("delta", "iv"):
            value = getattr(self, name)
            if not self.units_evidence or self.units_evidence.get(name) != "fraction":
                object.__setattr__(self, name, None)
            elif value is not None:
                if not isinstance(value, Decimal):
                    raise LeapsDataError("invalid_greek_decimal", "contract")
                parse_decimal(value)
                if (name == "delta" and not -1 <= value <= 1) or (name == "iv" and value < 0):
                    raise LeapsDataError("invalid_greek_fraction", "contract")
        super().__post_init__()


@dataclass(frozen=True)
class CapabilityEvidence(_Immutable):
    reference: Any
    option_quotes: Any
    stock_quotes: Any
    standard_terms: Any
    sessions: Any
    budget: Any
    display_rights: Any
    captured_ns: int | None
    evidence_ids: tuple[str, ...]


@dataclass(frozen=True)
class PageResult(_Immutable):
    items: tuple[Any, ...]
    next_cursor: str | None
    complete: bool
    reason: str | None
    request_id: str | None


@dataclass(frozen=True)
class UnderlyingEvidence(_Immutable):
    ticker: str
    indicators: Mapping[str, Any]
    facts: Mapping[str, Any]
    coverage: Mapping[str, Any]
    sources: tuple[Any, ...]
    as_of: date | datetime


@dataclass(frozen=True)
class ExpiryResult(_Immutable):
    debit: Decimal
    intrinsic: Decimal
    pnl: Decimal
    premium_roi: Decimal
    break_even: Decimal
    exercise_cash: Decimal
    stock_pnl: Decimal | None
    stock_price_roi: Decimal | None
    delta_shares: Decimal | None


@dataclass(frozen=True)
class AdmissionDecision(_Immutable):
    allowed: bool
    reason: str
    retry_after_s: Decimal | None


@dataclass(frozen=True)
class RunSpec(_Immutable):
    run_id: str
    market_date: date
    filters: Mapping[str, Any]
    universe_revision: str
    schema_version: str
    methodology_version: str


@dataclass(frozen=True)
class LeapsSnapshot(_Immutable):
    run: RunSpec
    status: str
    data: Mapping[str, Any]
    coverage: Mapping[str, Any]
    reasons: tuple[str, ...]
    cursor: str | None
    sources: tuple[Any, ...]
