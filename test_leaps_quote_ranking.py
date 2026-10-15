"""Real producer/read-path regressions: stale prices must not win a ranking."""
from dataclasses import replace
from decimal import Decimal as D

import pytest

from modules.atomic_cache import write_json_atomic
from modules.leaps.api_contract import LeapsApiService, _public_row
from modules.leaps.models import LeapsSnapshot
from modules.leaps.provider import StockQuote
from modules.leaps.runtime import LeapsStore
from modules.leaps.service import ComparisonEvidence, compare_contract
from modules.leaps.underlying import build_underlying_evidence
from tests.fixtures.leaps.discovery import Market, session
from tests.fixtures.leaps.provider import NOW, chain_raw, contract, run


def comparison(*, age=0, skew=0, ask='5.2', delta='.6', filters=None, proof=True):
    market = Market()
    selected = contract()
    raw = chain_raw()
    raw['greeks']['delta'] = D(delta)
    raw['last_quote'].update(ask=D(ask), bid=D(ask) - D('.1'),
                             last_updated=NOW - age * 10**9 - 1)
    option = market.parse_chain_item(raw, selected)
    stock = StockQuote('SOFI', replace(market.underlying_quote('SOFI'),
                       source_ns=NOW - (age + skew) * 10**9 - 1))
    quality = build_underlying_evidence('SOFI', completed_bars=(), facts=None,
                                       sources=(), as_of=run().market_date)
    evidence = ComparisonEvidence(quality, stock, session(selected, stock, NOW) if proof else None)
    return compare_contract(selected, option, evidence,
                            replace(run(), filters=filters or {}), NOW)


@pytest.mark.parametrize('age,skew,proof', [(61, 0, True), (0, 31, True), (0, 0, False)])
def test_uncomparable_prices_keep_ask_payoff_but_cannot_pass_relative_filters(age, skew, proof):
    # Break: allowing algebra_eligible to imply comparable stock/option prices.
    row = comparison(age=age, skew=skew, proof=proof,
                     filters={'max_be_rise': '.3', 'max_spread': '.1', 'min_underlying_price': '10'})
    assert row['assessment'].algebra_eligible
    assert not row['assessment'].comparison_eligible
    assert row['break_even'] == D('20.2')
    assert row['be_rise'] is None
    assert row['filter_status'] == 'missing_evidence'
    assert set(row['filter_missing']) == {'max_be_rise', 'max_spread', 'min_underlying_price'}
    assert all(point['result'].stock_pnl is None for point in row['scenarios'])
    assert row['rank_eligible'] is False


def test_public_old_snapshot_loses_relative_metrics_and_filter_pass_on_read():
    # Break: trusting a previously fresh stored row after its source expires.
    row = comparison()
    assert row['assessment'].comparison_eligible
    public = _public_row(row, NOW + 61 * 10**9)
    assert public['assessment']['algebra_eligible'] is True
    assert public['assessment']['comparison_eligible'] is False
    assert public['break_even'] == '20.2'
    assert public['be_rise'] is None
    assert public['rank_eligible'] is False
    assert public['filter_status'] == 'missing_evidence'


def test_current_price_analytics_reach_consumer_without_recomputing_in_browser():
    row = comparison(ask='6')
    # S=20, K=15, A=6: intrinsic5, extrinsic1, delta(.6)*20/6=2.
    assert row['analytics']['intrinsic_per_share'] == D(5)
    assert row['analytics']['extrinsic_per_share'] == D(1)
    assert row['analytics']['delta_leverage'] == D(2)
    assert row['analytics']['premium_debit'] == D(600)
    assert row['analytics']['known_dividend_total'] is None
    assert row['rank_eligible'] is True


def test_real_greek_precision_survives_pricing_and_analytics_boundary():
    row = comparison(ask='5', delta='0.6594548069277387')
    assert row['greeks'].delta == D('0.6594548069277387')
    assert row['analytics']['delta_leverage'] == D('2.6378192277109548')
    assert row['scenarios'][0]['result'].delta_shares == D('65.94548069277387')


def test_delayed_quotes_are_dated_algebra_not_current_ranking():
    from modules.leaps.provider import assess_quote
    market = Market()
    selected = contract()
    option = market.parse_chain_item(chain_raw(), selected)
    delayed = dict(source_ns=NOW - 900 * 10**9, feed_mode='delayed', feed_lag_s=D(900))
    option = replace(option, quote=replace(option.quote, **delayed))
    stock = StockQuote('SOFI', replace(market.underlying_quote('SOFI'), **delayed))
    result = assess_quote(option, stock, now_ns=NOW, verified_session=session(selected, stock, NOW))
    assert result.algebra_eligible is True
    assert result.comparison_eligible is False


def test_reference_fields_reach_discovery_without_external_terms_certificate():
    from modules.leaps.service import DiscoveryProvider, discover
    market = Market()
    market.references[0][0]['cfi'] = 'OCASPS'
    provider = DiscoveryProvider(market)
    saved = None
    for _ in range(12):
        saved = discover(provider, run(), saved.cursor if saved else None)
        if saved.status in ('complete', 'failed'):
            break
    assert saved.status == 'complete'
    assert saved.coverage['parsed'] == 1
    assert saved.data['comparisons'][0]['contract'].premium_multiplier == D(100)


def test_default_filters_are_server_side_and_explicit_clear_is_respected():
    from modules.leaps.api_contract import parse_scan
    defaults=parse_scan({},NOW).filters
    assert defaults=={'min_delta':D('.7'),'max_delta':D('.85'),'max_spread':D('.1'),
                      'min_open_interest':D(100),'min_underlying_price':D(10)}
    assert parse_scan({'filters':{}},NOW).filters=={}


def test_status_distinguishes_automatic_wait_from_manual_pause(tmp_path):
    from test_leaps_runtime import make_runner
    runner,_,clock,_=make_runner(tmp_path)
    runner.start(run())
    current=runner.store.load()
    runner.store.publish(replace(current,status='paused',coverage=dict(current.coverage,
                         retry={'reason':'priority_active','retry_at_ns':NOW+60*10**9})))
    api=LeapsApiService(runner.store,runner=runner,clock_ns=clock)
    assert api.status()['automatic_wait'] is True
    runner.pause(run().run_id)
    assert api.status()['automatic_wait'] is False


def test_filtered_current_contract_cannot_outrank_passed_contract(tmp_path):
    cheap_excluded = comparison(ask='5.2', delta='.6', filters={'min_delta': '.7'})
    dear_passed = comparison(ask='6', delta='.8', filters={'min_delta': '.7'})
    assert cheap_excluded['rank_eligible'] is False
    assert dear_passed['rank_eligible'] is True
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': (cheap_excluded, dear_passed)},
                                {}, (), None, ()))
    api = LeapsApiService(store, clock_ns=lambda: NOW)
    result = api.results(limit=1, sort_by='extrinsic_annual_fraction')
    assert result['rows'][0]['observation']['quote']['ask'] == '6'


@pytest.mark.parametrize('order', ['asc', 'desc'])
def test_quote_expiry_changes_global_order_before_pagination(tmp_path, order):
    # Break: cached sort orders keeping a now-expired cheap row on page one.
    cheap = comparison(age=50)
    fresh = comparison(ask='6')
    # Same real contract is sufficient: distinguish source quotes by Ask.
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': (cheap, fresh)},
                                {}, (), None, ()))
    clock = [NOW]
    api = LeapsApiService(store, clock_ns=lambda: clock[0])
    api.results(limit=1, sort_by='be_rise', order=order)
    clock[0] += 20 * 10**9
    result = api.results(limit=1, sort_by='be_rise', order=order)
    assert result['rows'][0]['observation']['quote']['ask'] == '6'
    assert result['rows'][0]['assessment']['comparison_eligible'] is True
    assert result['coverage']['eligible'] == 1


def test_stale_scenarios_do_not_compare_old_ask_to_new_stock(tmp_path):
    row = comparison(age=61)
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': (row,)}, {}, (), None, ()))
    api = LeapsApiService(store, clock_ns=lambda: NOW)
    identity = api.results()['snapshot_id']
    result = api.scenarios({'run_id': run().run_id, 'snapshot_id': identity,
                            'contract_id': contract().id, 'quantity': 1,
                            'terminal_prices': (D(22),)})
    assert result['scenarios'][0]['result']['pnl'] == '180.0'
    assert result['scenarios'][0]['result']['stock_pnl'] is None
    assert result['scenarios'][0]['result']['stock_reference_capital'] is None
