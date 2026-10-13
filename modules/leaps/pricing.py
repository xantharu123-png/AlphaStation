"""Pure expiry economics, not pre-expiry valuation or a profitability forecast.

Cash results are before unknown fees. Stock comparison is price return before
dividends, costs and financing. Exercise cash is additional capital required
for physical exercise, never a guaranteed cash payout. Delta is exposure,
not a probability. Scenarios are assumptions, not predictions.
"""
from decimal import Decimal

from .contracts import decimal_context, parse_decimal, parse_quantity
from .models import ExpiryResult, LeapsDataError, OptionContract, UnderlyingObservation


def _wire_decimal(value: Decimal) -> Decimal:
    if not isinstance(value, Decimal):
        raise LeapsDataError("invalid_decimal", "contract")
    return parse_decimal(value)


def _positive(value: Decimal) -> Decimal:
    value = _wire_decimal(value)
    if value <= 0:
        raise LeapsDataError("nonpositive_price", "contract")
    return value


def _terminal(value: Decimal) -> Decimal:
    # Internal generated prices may exceed the stricter wire/user domain.
    if not isinstance(value, Decimal) or not value.is_finite():
        raise LeapsDataError("invalid_terminal", "contract")
    if value.as_tuple().exponent < -14 or value < 0 or value > Decimal("2e12"):
        raise LeapsDataError("terminal_bounds", "contract")
    return value


def evaluate_expiry(
    contract: OptionContract,
    ask: Decimal,
    terminal: Decimal,
    *,
    quantity: int = 1,
    underlying: UnderlyingObservation | None = None,
    delta: Decimal | None = None,
) -> ExpiryResult:
    """Economics at expiry for purchased standard physically settled calls."""
    if (not isinstance(contract, OptionContract)
            or contract.right != "call" or contract.currency != "USD"
            or type(contract.shares) is not int or contract.shares != 100
            or not isinstance(contract.premium_multiplier, Decimal)
            or not contract.premium_multiplier.is_finite()
            or contract.premium_multiplier != 100
            or contract.exercise_style != "american" or contract.settlement != "physical"):
        raise LeapsDataError("nonstandard_terms", "contract")
    strike = _positive(contract.strike)
    ask = _positive(ask)
    terminal = _terminal(terminal)
    quantity = parse_quantity(quantity)
    if delta is not None:
        delta = _wire_decimal(delta)
        if not 0 <= delta <= 1:
            raise LeapsDataError("invalid_call_delta", "contract")
    initial = None
    if underlying is not None:
        if not isinstance(underlying, UnderlyingObservation) or underlying.ticker != contract.underlying:
            raise LeapsDataError("mismatched_underlying", "underlying")
        if underlying.price is not None:
            observed = _wire_decimal(underlying.price)
            if observed > 0:
                initial = observed
    with decimal_context():
        shares = Decimal(100 * quantity)
        debit = shares * ask
        intrinsic = shares * max(terminal - strike, Decimal(0))
        pnl = intrinsic - debit
        stock_pnl = None if initial is None else shares * (terminal - initial)
        stock_roi = None if initial is None else (terminal - initial) / initial
        return ExpiryResult(
            debit=debit, intrinsic=intrinsic, pnl=pnl,
            premium_roi=pnl / debit, break_even=strike + ask,
            exercise_cash=shares * strike, stock_pnl=stock_pnl,
            stock_price_roi=stock_roi,
            delta_shares=None if delta is None else shares * delta,
        )


def spread_metrics(bid: Decimal | None, ask: Decimal) -> tuple[Decimal | None, Decimal | None]:
    """Midpoint-relative spread and immediate Ask-to-Bid loss, as fractions."""
    ask = _positive(ask)
    if bid is None:
        return None, None
    bid = _wire_decimal(bid)
    if bid < 0 or bid > ask:
        raise LeapsDataError("invalid_bid_ask", "contract")
    with decimal_context():
        difference = ask - bid
        return difference / ((ask + bid) / 2), difference / ask


def scenario_prices(reference: Decimal | None) -> tuple[Decimal, ...]:
    """Absolute zero plus approved relative assumptions, without rounding."""
    if reference is None:
        return (Decimal(0),)
    reference = _wire_decimal(reference)
    if reference <= 0:
        return (Decimal(0),)
    with decimal_context():
        return (Decimal(0), *(reference * factor for factor in
                (Decimal(".80"), Decimal(1), Decimal("1.25"), Decimal("1.50"), Decimal("2.00"))))
