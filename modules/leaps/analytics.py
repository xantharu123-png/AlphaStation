"""Descriptive standard-call metrics; no provider access or return forecasts.

All monetary inputs are per share. Cash risk describes one fully paid long
call before unknown fees, financing and taxes, not risk after exercise.
Price comparability is the caller's responsibility before using these values.
"""
from datetime import date
from decimal import Decimal

from .contracts import decimal_context, parse_decimal
from .models import LeapsDataError, _freeze
from .pricing import evaluate_expiry


def _volatility(value):
    # HV is an internally derived Decimal50, not a 12-place wire price.
    if value is not None and (not isinstance(value, Decimal)
            or not value.is_finite() or value < 0 or value > Decimal('1e12')):
        raise LeapsDataError('invalid_volatility', 'contract')
    return value


def call_metrics(contract, ask, underlying_price, valuation_date, *, delta=None,
                 iv=None, hv=None):
    """One call's ACT/365F metrics on an already comparable price basis.

    Missing stock prices or expired terms cannot rank. Negative extrinsic is
    retained as an inconsistency diagnostic, never clamped into a bargain.
    Annual extrinsic is a linear premium allocation, not theta or an APR.
    Annual break-even is necessary stock-price change, not option ROI.
    """
    expiry = evaluate_expiry(contract, ask, Decimal(0), delta=delta)
    if type(valuation_date) is not date or type(contract.expiry) is not date:
        raise LeapsDataError('invalid_valuation_date', 'contract')
    if underlying_price is not None:
        if not isinstance(underlying_price, Decimal):
            raise LeapsDataError('invalid_decimal', 'underlying')
        underlying_price = parse_decimal(underlying_price)
        if underlying_price <= 0:
            raise LeapsDataError('nonpositive_price', 'underlying')
    iv, hv = _volatility(iv), _volatility(hv)
    days = (contract.expiry - valuation_date).days
    reasons = []
    if days <= 0:
        reasons.append('nonpositive_remaining_term')
    if underlying_price is None:
        reasons.append('missing_underlying_price')
    with decimal_context():
        term = Decimal(days) / 365 if days > 0 else None
        intrinsic = None if underlying_price is None else max(underlying_price - contract.strike, Decimal(0))
        extrinsic = None if intrinsic is None else ask - intrinsic
        if extrinsic is not None and extrinsic < 0:
            reasons.append('inconsistent_price_basis')
        rankable = not reasons
        return _freeze(dict(
            days_to_expiry=days, term_years=term,
            intrinsic_per_share=intrinsic, extrinsic_per_share=extrinsic,
            extrinsic_fraction_of_stock=extrinsic / underlying_price if rankable else None,
            extrinsic_per_month=extrinsic / (12 * term) if rankable else None,
            extrinsic_annual_fraction=extrinsic / (underlying_price * term) if rankable else None,
            break_even=expiry.break_even,
            break_even_annual_required_stock_return=(expiry.break_even / underlying_price) ** (Decimal(365) / days) - 1 if rankable else None,
            delta_leverage=delta * underlying_price / ask if rankable and delta is not None else None,
            premium_debit=expiry.debit, max_premium_loss=expiry.debit,
            iv=iv, hv=hv,
            iv_hv_difference_points=(iv - hv) * 100 if iv is not None and hv is not None else None,
            iv_hv_ratio=iv / hv if iv is not None and hv is not None and hv > 0 else None,
            known_dividend_total=None, rankable=rankable, reasons=tuple(reasons),
        ))


def annual_historical_volatility(closes):
    """Sample log-return HV from the last 253 supplied adjusted session closes.

    Caller validates session sequence, completion and adjustment provenance.
    Uses 252 returns, sample denominator 251 and annualization sqrt(252).
    There is no padding, calendar-day substitution or conversion to float.
    """
    if not isinstance(closes, (list, tuple)) or len(closes) < 253:
        raise LeapsDataError('insufficient_hv_history', 'underlying')
    values = []
    for value in closes[-253:]:
        if not isinstance(value, Decimal):
            raise LeapsDataError('invalid_hv_close', 'underlying')
        value = parse_decimal(value)
        if value <= 0:
            raise LeapsDataError('invalid_hv_close', 'underlying')
        values.append(value)
    with decimal_context():
        returns = [(right / left).ln() for left, right in zip(values, values[1:])]
        mean = sum(returns, Decimal(0)) / 252
        variance = sum(((value - mean) ** 2 for value in returns), Decimal(0)) / 251
        return variance.sqrt() * Decimal(252).sqrt()
