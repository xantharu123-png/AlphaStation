"""Bounded exact inputs and standard-contract admission, with no external I/O."""
from calendar import monthrange
from collections.abc import Mapping
from datetime import date, datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from hashlib import sha256
import json
import re

from .models import LeapsDataError, OptionContract, to_json_value


def decimal_context():
    """A local 50-digit context; never mutates the caller's global context."""
    return localcontext(Context(prec=50, rounding=ROUND_HALF_EVEN))


def parse_decimal(value) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, Decimal)):
        raise LeapsDataError("invalid_decimal", "contract")
    if isinstance(value, int) and not -10**12 <= value <= 10**12:
        raise LeapsDataError("decimal_bounds", "contract")
    lexical = str(value)
    if len(lexical) > 64 or not re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?", lexical):
        raise LeapsDataError("invalid_decimal", "contract")
    coefficient = lexical.lower().split("e", 1)[0]
    if "." in coefficient and len(coefficient.split(".", 1)[1]) > 12:
        raise LeapsDataError("decimal_bounds", "contract")
    try:
        with decimal_context():
            result = Decimal(lexical)
            if not result.is_finite() or result.as_tuple().exponent < -12 or result.copy_abs() > Decimal("1e12"):
                raise LeapsDataError("decimal_bounds", "contract")
            return result
    except InvalidOperation:
        raise LeapsDataError("invalid_decimal", "contract") from None


def parse_quantity(value) -> int:
    if type(value) is not int or not 1 <= value <= 10**6:
        raise LeapsDataError("invalid_quantity", "contract")
    return value


def parse_ns_epoch(value) -> int:
    # 19-digit contemporary ns epochs reject seconds/ms/us ambiguity. These
    # resource/unit bounds deliberately cover 2001-09 through 2286-11.
    if isinstance(value, str) and re.fullmatch(r"[0-9]{19}", value):
        value = int(value)
    if type(value) is not int or not 10**18 <= value < 10**19:
        raise LeapsDataError("invalid_ns_epoch", "provider")
    return value


def parse_utc_datetime(value) -> datetime:
    if isinstance(value, str) and len(value) <= 64:
        try:
            value = datetime.fromisoformat(value[:-1] + "+00:00" if value.endswith("Z") else value)
        except ValueError:
            raise LeapsDataError("invalid_utc_time", "provider") from None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise LeapsDataError("invalid_utc_time", "provider")
    return value.astimezone(timezone.utc)


def expiry_window(as_of: date) -> tuple[date, date]:
    if type(as_of) is not date:
        raise LeapsDataError("invalid_market_date", "underlying")
    try:
        return tuple(date(as_of.year + years, as_of.month, min(as_of.day, monthrange(as_of.year + years, as_of.month)[1])) for years in (1, 2))
    except ValueError:
        raise LeapsDataError("invalid_market_date", "underlying") from None


def parse_standard_contract(raw: Mapping, listing: Mapping, evidence: Mapping) -> OptionContract:
    """Parse Massive reference wire data + stock listing + trusted terms proof.

    No default proof is invented. Optional correction is provenance, not an
    adjustment flag. Missing additional_underlyings means no extras per schema.
    """
    if not all(isinstance(item, Mapping) for item in (raw, listing, evidence)):
        raise LeapsDataError("invalid_contract_mapping", "contract")
    symbol = raw.get("ticker")
    underlying = raw.get("underlying_ticker")
    match = re.fullmatch(r"(?:O:)?([A-Z][A-Z0-9.]{0,15})([0-9]{6})([CP])([0-9]{8})", symbol) if isinstance(symbol, str) else None
    if not match or not isinstance(underlying, str) or not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,15}", underlying):
        raise LeapsDataError("invalid_contract_identity", "contract")
    root, ymd, right, strike_digits = match.groups()
    aliases = evidence.get("root_aliases", {})
    if root != underlying and (not isinstance(aliases, Mapping) or aliases.get(root) != underlying):
        raise LeapsDataError("unproven_root_alias", "contract")
    try:
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
        metadata_expiry = date.fromisoformat(raw.get("expiration_date", ""))
    except (ValueError, TypeError):
        raise LeapsDataError("invalid_contract_expiry", "contract") from None
    strike = parse_decimal(raw.get("strike_price"))
    with decimal_context():
        occ_strike = Decimal(strike_digits) / Decimal(1000)
    if right != "C" or raw.get("contract_type") != "call" or strike <= 0 or strike != occ_strike or expiry != metadata_expiry:
        raise LeapsDataError("contract_metadata_mismatch", "contract")
    if listing.get("ticker") != underlying or listing.get("market") != "stocks" or listing.get("locale") != "us" or listing.get("active") is not True or listing.get("currency_name") != "usd":
        raise LeapsDataError("ineligible_underlying_listing", "contract")
    listing_type = listing.get("type")
    asset_class = evidence.get("asset_class")
    if not ((listing_type == "CS" and asset_class == "equity") or (listing_type in ("ADRC", "ADRP") and asset_class == "adr")):
        raise LeapsDataError("unproven_equity_terms", "contract")
    ids = evidence.get("evidence_ids")
    if evidence.get("standard") is not True or not isinstance(ids, (list, tuple)) or not ids or any(not isinstance(item, str) or not item.strip() for item in ids):
        raise LeapsDataError("unproven_standard_terms", "contract")
    shares = parse_quantity(raw.get("shares_per_contract"))
    multiplier = parse_decimal(evidence.get("premium_multiplier"))
    extras = raw.get("additional_underlyings", [])
    if shares != 100 or multiplier != 100 or not isinstance(extras, (list, tuple)) or extras or evidence.get("currency") != "USD" or evidence.get("settlement") != "physical":
        raise LeapsDataError("nonstandard_terms", "contract")
    exercise = raw.get("exercise_style")
    if exercise != "american" or not isinstance(raw.get("primary_exchange"), str) or not raw["primary_exchange"] or not isinstance(listing.get("primary_exchange"), str) or not listing["primary_exchange"]:
        raise LeapsDataError("unknown_contract_terms", "contract")
    correction = raw.get("correction")
    if correction is not None and (type(correction) is not int or not 0 <= correction <= 10**6):
        raise LeapsDataError("invalid_correction_provenance", "contract")
    with decimal_context():
        canonical_strike = format(strike.normalize(), "f")
        canonical_multiplier = format(multiplier.normalize(), "f")
    terms = dict(underlying=underlying, right="call", strike=canonical_strike, expiry=expiry, currency="USD", shares=100, premium_multiplier=canonical_multiplier, exercise_style=exercise, settlement="physical", correction=correction, option_exchange=raw["primary_exchange"], stock_exchange=listing["primary_exchange"])
    fingerprint = sha256(json.dumps(to_json_value(terms), sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    identity = f"{underlying}|{expiry.isoformat()}|call|{canonical_strike}|USD|100|100"
    return OptionContract(identity, symbol, underlying, "call", strike, expiry, "USD", 100, multiplier, exercise, "physical", fingerprint, evidence)
