"""Execute real LEAPS formatting/projection helpers; rendered QA stays separate."""
import json

import pytest

from test_frontend_leaps_contract import evaluate


def test_approved_initial_filter_inputs_submit_the_bounded_default_preset():
    """Catches omitting the approved conservative preset from a fresh UI run."""
    assert evaluate('leapsFilterBody(leapsInitialFilters())') == {'filters': {
        'min_delta_percent': '70', 'max_delta_percent': '85', 'max_spread_percent': '10',
        'min_open_interest': '100', 'min_underlying_price': '10',
    }}


def test_explicitly_cleared_initial_filters_still_submit_no_filter_mode():
    """Catches silently restoring defaults after the user deliberately clears them."""
    assert evaluate('leapsFilterBody(Object.fromEntries(Object.keys(leapsInitialFilters()).map(key => [key, ""])))') == {'filters': {}}


def test_premium_risk_box_uses_exact_server_debit_not_ask_or_quantity_math():
    """Catches float loss or recomputing server cash from displayed price inputs."""
    payload = {'quantity': 3, 'ask': '1', 'scenarios': [
        {'terminal': '0', 'result': {'debit': '9007199254740993.12', 'pnl': '-7'}},
    ]}
    assert evaluate(f'leapsPremiumSummary({json.dumps(payload)})') == (
        'Prämie 9007199254740993.12 USD · maximaler Prämienverlust 9007199254740993.12 USD'
    )


@pytest.mark.parametrize('payload', [None, {}, {'scenarios': []}, {'scenarios': [{'result': {'debit': None}}]}])
def test_unknown_server_debit_stays_unknown_in_premium_risk_box(payload):
    """Catches making missing cash look like a free zero-risk call."""
    assert evaluate(f'leapsPremiumSummary({json.dumps(payload)})') == (
        'Prämie – USD · maximaler Prämienverlust – USD'
    )


def test_compact_metrics_format_only_server_annual_and_leverage_fields():
    """Catches annualizing browser values or confusing a fraction with percent."""
    analytics = {'extrinsic_annual_fraction': '0.012345',
                 'break_even_annual_required_stock_return': '-0.025', 'delta_leverage': '2.346',
                 'extrinsic_per_share': '999', 'term_years': '999'}
    assert evaluate(f'leapsAnalyticsSummary({json.dumps(analytics)})') == {
        'extrinsicAnnual': '1.23%', 'requiredAnnual': '-2.50%', 'deltaLeverage': '2.35×',
    }


@pytest.mark.parametrize('analytics', [None, {}, {'delta_leverage': None, 'extrinsic_annual_fraction': None}])
def test_missing_analytics_never_become_zero_percent_or_zero_leverage(analytics):
    """Catches silently presenting unavailable ranking evidence as cheap leverage."""
    assert evaluate(f'leapsAnalyticsSummary({json.dumps(analytics)})') == {
        'extrinsicAnnual': '–', 'requiredAnnual': '–', 'deltaLeverage': '–',
    }


def test_payoff_projection_keeps_exact_server_points_without_inventing_financial_results():
    """Catches deriving payoffs from strike/ask or replacing exact labels with floats."""
    points = [{'terminal': '25', 'result': {'pnl': '480'}},
              {'terminal': '0', 'result': {'pnl': '-520'}},
              {'terminal': '20', 'result': {'pnl': '-20'}}]
    result = evaluate(f'leapsPayoffPresentation({json.dumps(points)})')
    assert 'polyline' not in result  # No invented interpolation across an omitted strike.
    assert result['zeroY'] == 62
    assert result['points'] == [
        {'x': 24, 'y': 114, 'terminal': '0', 'pnl': '-520'},
        {'x': 273.6, 'y': 64, 'terminal': '20', 'pnl': '-20'},
        {'x': 336, 'y': 14, 'terminal': '25', 'pnl': '480'},
    ]


@pytest.mark.parametrize('points', [None, [], [{'terminal': '20', 'result': {'pnl': '9'}}],
    [{'terminal': '20', 'result': {'pnl': '1'}}, {'terminal': '20', 'result': {'pnl': '2'}}],
    [{'terminal': '0', 'result': {'pnl': None}}, {'terminal': '20', 'result': {'pnl': '9'}}],
    [{'terminal': '0', 'result': {'pnl': 'NaN'}}, {'terminal': '20', 'result': {'pnl': '9'}}],
    [{'terminal': '0', 'result': {'pnl': '-2'}}, {'terminal': 'Infinity', 'result': {'pnl': '9'}}]])
def test_payoff_never_draws_an_incomplete_or_invented_curve(points):
    """Catches null-to-zero conversion, fake custom-price curves or nonfinite SVG."""
    assert evaluate(f'leapsPayoffPresentation({json.dumps(points)})') is None


@pytest.mark.parametrize('state,want', [('running', 'Läuft'), ('complete', 'Abgeschlossen'),
    ('partial', 'Teilstand'), ('paused', 'Pausiert'), ('queued', 'Wartet'),
    ('unexpected_private_code', 'Unbekannt')])
def test_customer_run_status_does_not_echo_raw_codes(state, want):
    """Catches leaking raw backend states into the compact customer status."""
    assert evaluate(f'leapsRunState({json.dumps(state)})') == want


def test_compact_quote_date_does_not_borrow_or_refresh_the_source_time():
    """Catches replacing an old original quote date with receipt time or today."""
    assert evaluate('leapsShortDate("1791898200000000000")') == '2026-10-13'
    assert evaluate('leapsShortDate(null)') == 'unbekannt'


def test_pause_is_available_during_automatic_wait_but_not_manual_pause():
    policy={'admin':True,'enabled':True,'endpoint_capabilities':{'control':True}}
    status={'run_id':'run','status':'paused','terminal':False,'automatic_wait':True}
    assert evaluate(f'leapsControlReason({json.dumps(policy)},"pause",{json.dumps(status)})')==''
    status['automatic_wait']=False
    assert evaluate(f'leapsControlReason({json.dumps(policy)},"pause",{json.dumps(status)})')=='Lauf nicht aktiv'
