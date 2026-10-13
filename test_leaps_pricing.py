"""Synthetic expiry arithmetic checked with literals and a rational oracle."""
from dataclasses import replace
from datetime import date
from decimal import Decimal, getcontext, localcontext, ROUND_DOWN
from fractions import Fraction
from importlib import import_module, util
from itertools import product

import pytest

from modules.leaps.models import LeapsDataError, OptionContract, UnderlyingObservation


D = Decimal


def pricing():
    # Turn an intentionally absent feature into a behavioral RED, not import error.
    assert util.find_spec("modules.leaps.pricing") is not None, "LEAPS expiry arithmetic feature is missing"
    return import_module("modules.leaps.pricing")


def contract(strike="15"):
    return OptionContract("synthetic-sofi-call", "O:SOFI280121C00015000", "SOFI", "call", D(strike), date(2028, 1, 21), "USD", 100, D(100), "american", "physical", "synthetic-terms", {"standard": True})


def reference(price="15.34"):
    return UnderlyingObservation("SOFI", None if price is None else D(price), "synthetic", None, None)


@pytest.mark.parametrize("terminal,pnl", [("0", "-430"), ("15", "-430"), ("15.34", "-396"), ("19.30", "0"), ("22", "270"), ("26", "670"), ("30", "1070")])
def test_sofi_expiry_cash_not_underlying_percentage(terminal, pnl):
    result = pricing().evaluate_expiry(contract(), D("4.30"), D(terminal), underlying=reference())
    assert result.pnl == D(pnl)
    assert result.debit == D("430")
    assert result.break_even == D("19.30")
    assert result.exercise_cash == D("1500")
    assert abs(Fraction(result.premium_roi) - Fraction(pnl) / 430) < Fraction(1, 10**48)


def test_sofi_25_percent_stock_gain_is_still_a_call_loss():
    result = pricing().evaluate_expiry(contract(), D("4.30"), D("19.175"), underlying=reference())
    assert result.pnl == D("-12.50")
    assert result.stock_pnl == D("383.50")
    assert result.stock_price_roi == D("0.25")
    assert result.delta_shares is None


@pytest.mark.parametrize("terminal,pnl", [("19.299999", "-0.0001"), ("19.30", "0"), ("19.300001", "0.0001")])
def test_break_even_does_not_round_tiny_cash_sign(terminal, pnl):
    assert pricing().evaluate_expiry(contract(), D("4.30"), D(terminal)).pnl == D(pnl)


def test_decimal_point_one_plus_point_two_has_exact_break_even():
    result = pricing().evaluate_expiry(contract(".10"), D(".20"), D(".30"))
    assert result.pnl == 0
    assert result.break_even == D(".30")


@pytest.mark.parametrize("price", [None, "0", "-1"])
def test_unavailable_nonpositive_reference_leaves_stock_results_unknown(price):
    result = pricing().evaluate_expiry(contract(), D("4.30"), D("22"), underlying=reference(price))
    assert result.pnl == D("270")
    assert result.stock_pnl is None
    assert result.stock_price_roi is None


def test_absent_reference_leaves_stock_results_unknown():
    result = pricing().evaluate_expiry(contract(), D("4.30"), D("22"))
    assert result.stock_pnl is None
    assert result.stock_price_roi is None
    assert result.to_json_dict()["stock_pnl"] is None
    assert result.to_json_dict()["stock_price_roi"] is None


def test_scenarios_keep_backend_derived_14_place_precision_and_order():
    prices = pricing().scenario_prices(D("123.123456789123"))
    assert prices == tuple(map(D, ("0", "98.49876543129840", "123.123456789123", "153.90432098640375", "184.68518518368450", "246.24691357824600")))
    for terminal in prices:
        result = pricing().evaluate_expiry(contract(), D("4.30"), terminal)
        assert Fraction(result.pnl) == (max(Fraction(terminal) - 15, 0) - Fraction("4.30")) * 100


@pytest.mark.parametrize("price", [None, D(0), D(-1)])
def test_scenarios_without_positive_reference_are_only_absolute_zero(price):
    assert pricing().scenario_prices(price) == (D(0),)


def test_scenarios_at_maximum_external_reference_price_remain_priceable():
    prices = pricing().scenario_prices(D("1e12"))
    assert prices == tuple(map(D, ("0", "8e11", "1e12", "1.25e12", "1.5e12", "2e12")))
    assert pricing().evaluate_expiry(contract(), D("4.30"), prices[-1]).pnl == D("199999999998070")


@pytest.mark.parametrize("terminal,want", [("1999999999999.99999999999999", "199999999998069.99999999999900"), (".00000000000080", "-430")])
def test_fourteen_place_terminal_boundaries_keep_cash_exact(terminal, want):
    assert pricing().evaluate_expiry(contract(), D("4.30"), D(terminal)).pnl == D(want)


@pytest.mark.parametrize("price", [D("NaN"), D("Infinity"), "15.34", 15.34, True, D(".0000000000001"), D("1000000000001")])
def test_relative_scenarios_require_valid_typed_external_reference(price):
    with pytest.raises(LeapsDataError):
        pricing().scenario_prices(price)


@pytest.mark.parametrize("price", [D("NaN"), D("Infinity"), "15.34", 15.34, True])
def test_invalid_underlying_observation_is_not_silently_used(price):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), D("4.30"), D(22), underlying=replace(reference(), price=price))


def test_other_ticker_cannot_supply_stock_comparison_reference():
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), D("4.30"), D(22), underlying=replace(reference(), ticker="OTHER"))


@pytest.mark.parametrize("delta,want", [(None, None), ("0", "0"), (".5", "150"), ("1", "300")])
def test_three_contracts_scale_cash_and_delta_share_exposure(delta, want):
    result = pricing().evaluate_expiry(contract(), D("4.30"), D("22"), quantity=3, underlying=reference(), delta=None if delta is None else D(delta))
    assert (result.debit, result.pnl, result.exercise_cash, result.stock_pnl) == (D(1290), D(810), D(4500), D(1998))
    assert result.delta_shares == (None if want is None else D(want))


@pytest.mark.parametrize("delta", [D("-.01"), D("1.01"), D("NaN"), D("Infinity"), 0.5, "0.5", True])
def test_bought_call_rejects_invalid_delta(delta):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), D("4.30"), D("22"), delta=delta)


@pytest.mark.parametrize("quantity", [0, -1, 1_000_001, True, "1", 1.0])
def test_purchased_quantity_is_strict_positive_bounded_integer(quantity):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), D("4.30"), D("22"), quantity=quantity)


@pytest.mark.parametrize("ask", [D(0), D(-1), D("NaN"), D("Infinity"), 4.3, "4.3", True, D(".0000000000001"), D("1000000000001")])
def test_ask_rejects_nonpositive_unbounded_or_untyped_inputs(ask):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), ask, D(22))


@pytest.mark.parametrize("terminal", [D(-1), D("NaN"), D("Infinity"), 22.0, "22", True, D("2000000000000.0001"), D("19.300000000000001"), D("0." + "1" * 65)])
def test_terminal_rejects_invalid_backend_domain(terminal):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(contract(), D("4.3"), terminal)


@pytest.mark.parametrize("field,value", [("right", "put"), ("shares", 150), ("shares", True), ("premium_multiplier", D(150)), ("premium_multiplier", D("sNaN")), ("premium_multiplier", 100), ("currency", "EUR"), ("settlement", "cash"), ("exercise_style", "european"), ("strike", D(0)), ("strike", 15.0)])
def test_arithmetic_does_not_silently_price_nonstandard_direct_dtos(field, value):
    with pytest.raises(LeapsDataError):
        pricing().evaluate_expiry(replace(contract(), **{field: value}), D("4.3"), D(22))


def test_fraction_oracle_exact_cash_quantity_and_above_strike_identity():
    p = pricing()
    for strike, ask, terminal, initial, quantity in product((".10", "15", "20.125"), (".20", "4.30"), ("0", "15", "19.30", "30"), (".01", "15.34"), (1, 3, 1_000_000)):
        k, a, t, s = map(Fraction, (strike, ask, terminal, initial))
        shares = 100 * quantity
        expected_intrinsic = max(t - k, 0) * shares
        expected_debit = a * shares
        expected_stock = (t - s) * shares
        result = p.evaluate_expiry(contract(strike), D(ask), D(terminal), quantity=quantity, underlying=reference(initial))
        assert Fraction(result.debit) == expected_debit
        assert Fraction(result.intrinsic) == expected_intrinsic
        assert Fraction(result.pnl) == expected_intrinsic - expected_debit
        assert Fraction(result.exercise_cash) == k * shares
        assert Fraction(result.stock_pnl) == expected_stock
        assert Fraction(result.break_even) == k + a
        assert abs(Fraction(result.premium_roi) - (expected_intrinsic - expected_debit) / expected_debit) < Fraction(1, 10**45)
        assert abs(Fraction(result.stock_price_roi) - (t - s) / s) < Fraction(1, 10**45)
        at_be = p.evaluate_expiry(contract(strike), D(ask), result.break_even, quantity=quantity)
        assert at_be.pnl == 0
        if t >= k:
            assert Fraction(result.pnl - result.stock_pnl) == shares * (s - k - a)


def test_payoff_is_convex_and_nondecreasing_across_strike():
    results = [pricing().evaluate_expiry(contract(), D("4.3"), D(t)) for t in ("0", "5", "10", "15", "20", "25", "30")]
    payoff = [Fraction(result.intrinsic) for result in results]
    changes = [right - left for left, right in zip(payoff, payoff[1:])]
    assert changes == [0, 0, 0, 500, 500, 500]
    assert all(change >= 0 for change in changes)
    assert all(right >= left for left, right in zip(changes, changes[1:]))


def test_spread_ratios_have_fraction_units_and_50_digit_precision():
    spread, ask_loss = pricing().spread_metrics(D(4), D("4.30"))
    assert abs(Fraction(spread) - Fraction(6, 83)) < Fraction(1, 10**49)
    assert abs(Fraction(ask_loss) - Fraction(3, 43)) < Fraction(1, 10**49)


@pytest.mark.parametrize("bid,want", [(None, (None, None)), (D("4.3"), (D(0), D(0))), (D(0), (D(2), D(1)))])
def test_missing_equal_and_zero_bids_preserve_spread_meaning(bid, want):
    assert pricing().spread_metrics(bid, D("4.3")) == want


@pytest.mark.parametrize("bid,ask", [(D("4.31"), D("4.30")), (D(-1), D(4)), (D(1), D(0)), (D("NaN"), D(4)), (None, D(0)), (4.0, D(4)), (D(4), "4"), (D("1000000000001"), D(4))])
def test_invalid_crossed_or_untyped_quotes_cannot_produce_spreads(bid, ask):
    with pytest.raises(LeapsDataError):
        pricing().spread_metrics(bid, ask)


def test_caller_decimal_context_does_not_round_cash_or_ratios():
    with localcontext() as caller:
        caller.prec = 6
        caller.rounding = ROUND_DOWN
        before = (getcontext().prec, getcontext().rounding, dict(getcontext().flags))
        result = pricing().evaluate_expiry(contract(), D("4.30"), D("19.300001"), underlying=reference())
        spread, _ = pricing().spread_metrics(D(4), D("4.30"))
        assert result.pnl == D(".0001")
        assert abs(Fraction(spread) - Fraction(6, 83)) < Fraction(1, 10**49)
        assert (getcontext().prec, getcontext().rounding, dict(getcontext().flags)) == before
