"""Provider-independent call metrics, with literal and rational counterexamples."""
from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal as D, ROUND_DOWN, getcontext, localcontext
from fractions import Fraction
from importlib import import_module, util

import pytest

from modules.leaps.models import LeapsDataError, OptionContract
from modules.leaps.underlying import build_underlying_evidence


def analytics():
    assert util.find_spec('modules.leaps.analytics') is not None, 'descriptive LEAPS metrics are missing'
    return import_module('modules.leaps.analytics')


def contract():
    return OptionContract('fixture-call', 'O:FIX281013C00080000', 'FIX', 'call',
        D(80), date(2028, 10, 13), 'USD', 100, D(100), 'american', 'physical',
        'fixture-terms', {'standard': True})


def metrics(**changes):
    values = dict(contract=contract(), ask=D(41), underlying_price=D(100),
                  valuation_date=date(2026, 10, 14))
    values.update(changes)
    return analytics().call_metrics(**values)


def test_two_year_call_separates_linear_extrinsic_from_compound_break_even():
    result = metrics(delta=D('.8'), iv=D('.4'), hv=D('.25'))
    assert result['days_to_expiry'] == 730
    assert result['term_years'] == D(2)
    assert result['intrinsic_per_share'] == D(20)
    assert result['extrinsic_per_share'] == D(21)
    assert result['extrinsic_fraction_of_stock'] == D('.21')
    assert result['extrinsic_per_month'] == D('.875')
    assert result['extrinsic_annual_fraction'] == D('.105')
    assert result['break_even'] == D(121)
    assert result['break_even_annual_required_stock_return'] == D('.1')
    assert abs(Fraction(result['delta_leverage']) - Fraction(80, 41)) < Fraction(1, 10**48)
    assert result['premium_debit'] == result['max_premium_loss'] == D(4100)
    assert result['iv_hv_difference_points'] == D(15)
    assert result['iv_hv_ratio'] == D('1.6')
    assert result['rankable'] is True


def test_at_the_money_call_has_no_intrinsic_and_uses_act_365f():
    result = metrics(contract=replace(contract(), strike=D(100), expiry=date(2027, 10, 14)), ask=D(20))
    assert result['intrinsic_per_share'] == 0
    assert result['extrinsic_per_share'] == D(20)
    assert result['extrinsic_annual_fraction'] == D('.2')
    assert result['break_even_annual_required_stock_return'] == D('.2')


def test_missing_greeks_volatility_and_complete_dividend_schedule_stay_unknown():
    result = metrics()
    for key in ('delta_leverage', 'iv', 'hv', 'iv_hv_difference_points',
                'iv_hv_ratio', 'known_dividend_total'):
        assert result[key] is None
    assert result['rankable'] is True


def test_zero_delta_and_zero_hv_are_real_values_but_hv_ratio_is_undefined():
    result = metrics(delta=D(0), iv=D('.4'), hv=D(0))
    assert result['delta_leverage'] == 0
    assert result['hv'] == 0
    assert result['iv_hv_difference_points'] == D(40)
    assert result['iv_hv_ratio'] is None


def test_negative_extrinsic_marks_inconsistent_basis_unrankable_without_clamping():
    result = metrics(ask=D(19), delta=D('.8'))
    assert result['intrinsic_per_share'] == D(20)
    assert result['extrinsic_per_share'] == D(-1)
    assert result['rankable'] is False
    assert 'inconsistent_price_basis' in result['reasons']
    assert result['premium_debit'] == D(1900)
    assert result['break_even'] == D(99)
    for key in ('extrinsic_fraction_of_stock', 'extrinsic_per_month',
                'extrinsic_annual_fraction', 'break_even_annual_required_stock_return',
                'delta_leverage'):
        assert result[key] is None


@pytest.mark.parametrize('expiry', [date(2026, 10, 14), date(2026, 10, 13)])
def test_no_positive_remaining_term_preserves_cash_but_cannot_rank(expiry):
    result = metrics(contract=replace(contract(), expiry=expiry))
    assert result['rankable'] is False
    assert result['term_years'] is None
    assert result['extrinsic_annual_fraction'] is None
    assert result['break_even_annual_required_stock_return'] is None
    assert result['premium_debit'] == D(4100)


def test_missing_stock_reference_preserves_absolute_payoff_inputs_only():
    result = metrics(underlying_price=None)
    assert result['rankable'] is False
    assert result['intrinsic_per_share'] is None
    assert result['extrinsic_per_share'] is None
    assert result['extrinsic_annual_fraction'] is None
    assert result['break_even'] == D(121)
    assert result['premium_debit'] == D(4100)


@pytest.mark.parametrize('changes', [
    {'ask': D(0)}, {'ask': 41.0}, {'underlying_price': D(0)},
    {'underlying_price': D('NaN')}, {'underlying_price': '100'},
    {'delta': D('-.1')}, {'delta': D('1.1')}, {'delta': True},
    {'iv': D(-1)}, {'iv': D('Infinity')}, {'iv': .4},
    {'hv': D(-1)}, {'hv': D('NaN')}, {'hv': .2},
    {'contract': replace(contract(), right='put')},
    {'contract': replace(contract(), strike=D(0))},
    {'contract': replace(contract(), expiry='2028-10-13')},
    {'valuation_date': datetime(2026, 10, 14)},
])
def test_invalid_or_untyped_inputs_never_produce_relative_metrics(changes):
    with pytest.raises(LeapsDataError):
        metrics(**changes)


def test_compound_return_has_50_digit_precision_without_mutating_caller_context():
    with localcontext() as caller:
        caller.prec = 6
        caller.rounding = ROUND_DOWN
        before = (getcontext().prec, getcontext().rounding, dict(getcontext().flags))
        result = metrics(contract=replace(contract(), strike=D(100)), ask=D(20))
        actual = Fraction(result['break_even_annual_required_stock_return'])
        expected = Fraction('0.0954451150103322269139395656016042679054893899959665')
        assert abs(actual - expected) < Fraction(1, 10**48)
        assert (getcontext().prec, getcontext().rounding, dict(getcontext().flags)) == before


def adjusted_bars():
    # Actual 2025 NYSE sessions plus Jan 2/5/6 2026: 253 closes, including
    # the exceptional Jan 9 mourning closure. Literal fixture, no code oracle.
    closures = {date(2025, 1, 9), date(2025, 1, 20), date(2025, 2, 17),
                date(2025, 4, 18), date(2025, 5, 26), date(2025, 6, 19),
                date(2025, 7, 4), date(2025, 9, 1), date(2025, 11, 27),
                date(2025, 12, 25), date(2026, 1, 1)}
    day, result = date(2025, 1, 2), []
    while day <= date(2026, 1, 6):
        if day.weekday() < 5 and day not in closures:
            close = D(100 if len(result) % 2 == 0 else 110)
            result.append(dict(date=day.isoformat(), completed=True, open=close,
                               high=close, low=close, close=close, volume=D(1000)))
        day += timedelta(days=1)
    assert len(result) == 253
    return result


def evidence(bars, *, adjusted=True):
    sources = ({'source': 'synthetic', 'adjusted': True,
                'adjustment_basis': 'split_adjusted'},) if adjusted else ()
    return build_underlying_evidence('FIX', completed_bars=bars, facts=None,
        sources=sources, as_of=date(2026, 1, 7))


def test_hv_uses_252_log_returns_and_sample_denominator_251():
    # Independent oracle: alternating +/-log(1.1), mean zero. The resulting
    # sample HV is log(1.1) * 252 / sqrt(251), not a population deviation.
    result = evidence(adjusted_bars())
    hv = result.indicators['hv_252_decimal']
    expected = Fraction('1.516013151678525131335329307942919568154701835738537')
    assert abs(Fraction(hv) - expected) < Fraction(1, 10**47)
    assert result.indicators['hv_meta']['return_count'] == 252
    assert result.indicators['hv_meta']['adjustment_basis'] == 'split_adjusted'


def test_hv_flat_adjusted_history_is_zero_not_unknown():
    bars = adjusted_bars()
    for bar in bars:
        bar.update(open=D(100), high=D(100), low=D(100), close=D(100))
    assert evidence(bars).indicators['hv_252_decimal'] == D(0)


def test_hv_uses_only_last_253_closes_and_does_not_float_round_them():
    closes = [D(100), D(110)] * 126 + [D(100)]
    expected = analytics().annual_historical_volatility(closes)
    assert analytics().annual_historical_volatility([D(1000000), *closes]) == expected
    tiny_moves = [D('100000000000.000000000001'), D('100000000000.000000000002')] * 126 + [D('100000000000.000000000001')]
    assert analytics().annual_historical_volatility(tiny_moves) > 0


@pytest.mark.parametrize('change,reason', [
    ('short', 'insufficient_hv_history'),
    ('missing_session', 'missing_hv_sessions'),
    ('calendar_days', 'non_session_hv_history'),
    ('adjustment_unknown', 'unverified_adjustment_basis'),
])
def test_unproven_hv_remains_unknown_without_removing_other_indicators(change, reason):
    bars = adjusted_bars()
    adjusted = True
    if change == 'short':
        bars = bars[1:]
    elif change == 'missing_session':
        bars.pop(100)
        bars.insert(0, dict(bars[0], date='2024-12-31'))
    elif change == 'calendar_days':
        for i, bar in enumerate(bars):
            bar['date'] = (date(2025, 1, 1) + timedelta(days=i)).isoformat()
    else:
        adjusted = False
    result = evidence(bars, adjusted=adjusted)
    assert result.indicators['hv_252_decimal'] is None
    assert result.indicators['hv_meta']['reason'] == reason
    assert result.indicators['ema200_decimal'] is not None


def test_invalid_causal_bar_is_rejected_before_any_hv_enrichment():
    bars = adjusted_bars()
    bars[0]['high'] = D(1)
    result = evidence(bars)
    assert result.coverage['history'] == 'invalid'
    assert not result.indicators


@pytest.mark.parametrize('closes', [[D(100)] * 252, [D(0)] * 253,
    [D('NaN')] * 253, [100.0] * 253, [True] * 253])
def test_hv_helper_rejects_incomplete_or_invalid_prices(closes):
    with pytest.raises(LeapsDataError):
        analytics().annual_historical_volatility(closes)
