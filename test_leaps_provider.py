"""Authentic-schema synthetic transport fixtures; no network or secret loading."""
from datetime import date, datetime, timezone
from dataclasses import replace
from decimal import Decimal as D
from importlib import import_module
from pathlib import Path
import json
import subprocess
import sys

import pytest

from modules.leaps.models import AdmissionDecision, CapabilityEvidence, LeapsDataError, RunSpec, QuoteObservation
from modules.leaps.contracts import parse_standard_contract

from tests.fixtures.leaps.provider import (
    NOW, Admission, Response, Transport, capability, run, contract, chain_raw,
)


def api():
    assert Path(__file__).parent.joinpath('modules/leaps/provider.py').is_file(), 'LEAPS provider feature is missing'
    return import_module('modules.leaps.provider')




def provider(*responses, allowed=True):
    a = Admission(allowed)
    t = Transport(responses, a)
    return api().MassiveLeapsProvider(t, lambda: NOW, a, capability()), t, a




def test_reference_uses_calendar_window_and_decimal_wire_json():
    p, t, a = provider(Response(text='{"status":"OK","results":[{"strike_price":15.123456789012}],"request_id":"synthetic-1"}'))
    page = p.list_contract_page(run())
    assert page.items[0]['strike_price'] == D('15.123456789012')
    assert page.complete and page.next_cursor is None
    url = t.calls[0][1]
    assert 'expiration_date.gte=2027-10-13' in url and 'expiration_date.lte=2028-10-13' in url
    assert 'apiKey' not in url and 'synthetic-private-key' not in url
    assert t.calls[0][3:] == ((3, 10), False)
    assert a.events == [('acquire', 'market'), ('release', 'market')]


@pytest.mark.parametrize('status', [401, 403])
def test_authorization_denial_is_global_and_not_retried(status):
    p, t, a = provider(Response(status=status), Response())
    for action in (lambda: p.list_contract_page(run()), lambda: p.underlying_quote('SOFI')):
        with pytest.raises(LeapsDataError, match='provider:authorization_denied'):
            action()
    assert len(t.calls) == 1 and not a.held


@pytest.mark.parametrize('first', [Response(status=500), ConnectionError('synthetic-private-key')])
def test_retry_has_fresh_admission_and_release(first):
    p, t, a = provider(first, Response())
    assert p.list_contract_page(run()).complete
    assert len(t.calls) == 2
    assert a.events == [('acquire', 'market'), ('release', 'market')] * 2


@pytest.mark.parametrize('status,reason', [(429, 'rate_limited'), (302, 'redirect_rejected'), (400, 'http_error')])
def test_status_failures_are_safe_and_not_retried(status, reason):
    p, t, a = provider(Response(status=status, text='synthetic-private-key'))
    with pytest.raises(LeapsDataError, match='provider:' + reason):
        p.list_contract_page(run())
    assert len(t.calls) == 1 and not a.held


def test_denied_admission_never_transports_or_releases_someone_else():
    p, t, a = provider(Response(), allowed=False)
    with pytest.raises(LeapsDataError, match='admission_denied'):
        p.list_contract_page(run())
    assert t.calls == [] and a.events == [('acquire', 'market')]


@pytest.mark.parametrize('url', ['http://api.massive.com/v3/reference/options/contracts?cursor=a',
    'https://evil.example/v3/reference/options/contracts?cursor=a',
    'https://api.massive.com:443/v3/reference/options/contracts?cursor=a',
    'https://x@api.massive.com/v3/reference/options/contracts?cursor=a',
    'https://api.massive.com/v3/reference/tickers?cursor=a',
    'https://api.massive.com/v3/reference/options/contracts?cursor=a#secret'])
def test_untrusted_next_url_is_never_returned_or_followed(url):
    p, t, _ = provider(Response({'status':'OK', 'results':[], 'next_url':url}))
    with pytest.raises(LeapsDataError, match='invalid_cursor'):
        p.list_contract_page(run())
    assert len(t.calls) == 1


def test_cursor_cycle_rejected_before_third_request():
    url = 'https://api.massive.com/v3/reference/options/contracts?cursor=synthetic-next'
    p, t, _ = provider(Response({'status':'OK','results':[], 'next_url':url}), Response({'status':'OK','results':[], 'next_url':url}))
    page = p.list_contract_page(run())
    assert page.next_cursor == 'synthetic-next' and not page.complete
    with pytest.raises(LeapsDataError, match='cursor_cycle'):
        p.list_contract_page(run(), page.next_cursor)
    assert len(t.calls) == 2


def test_sec_is_gated_without_market_credentials():
    p, t, a = provider(Response({'cik':1818874,'entityName':'Synthetic','facts':{}}))
    facts = p.company_facts('1818874', configured_sec_user_agent='Synthetic Research synthetic@example.invalid')
    assert facts['cik'] == 1818874
    assert t.calls[0][1] == 'https://data.sec.gov/api/xbrl/companyfacts/CIK0001818874.json'
    assert t.calls[0][2] == {'User-Agent':'Synthetic Research synthetic@example.invalid', 'Accept':'application/json'}
    assert a.events == [('acquire','sec'), ('release','sec')]


def test_daily_history_validates_ms_without_borrowing_ns_quote_clock():
    p, t, a = provider(Response({'status':'OK','ticker':'SOFI','results':[{'t':1791777600000,'o':20,'h':21,'l':19,'c':20.5,'v':2000}]}))
    data = p.daily_history('SOFI', completed_through=date(2026,10,12), as_of=date(2026,10,13),completion=daily_completion(historical=True))
    assert data['results'][0]['t'] == 1791777600000
    assert data['results'][0]['c'] == D('20.5')
    assert a.events == [('acquire','market'),('release','market')]


def test_stock_original_exchange_time_and_share_sizes():
    p, _, _ = provider(Response({'status':'OK', 'results':{'T':'SOFI','p':20,'P':20.1,'s':200,'S':300,'y':NOW-100,'t':NOW,'q':1,'x':11,'X':19,'z':3}}))
    quote = p.underlying_quote('SOFI')
    assert quote.source_ns == NOW-100 and quote.size_unit == 'shares'
    assert quote.bid == D(20) and quote.ask_size == 300


def test_probe_cli_defaults_to_safe_dry_run():
    api()
    script = Path(__file__).parent / 'scripts/probe_leaps_provider.py'
    result = subprocess.run([sys.executable, '-B', str(script)], text=True, capture_output=True)
    assert result.returncode == 0 and 'dry_run' in result.stdout
    assert 'synthetic-private-key' not in result.stdout and result.stderr == ''




def bound_quotes(*, age=1, lag=D(0)):
    mod = api()
    mode = 'realtime' if lag == 0 else 'delayed'
    p, _, _ = provider()
    option = p.parse_chain_item(chain_raw(), contract())
    option = replace(option, quote=replace(option.quote,source_ns=NOW-age,feed_mode=mode,feed_lag_s=lag))
    stock = mod.StockQuote('SOFI', QuoteObservation(D(20),D('20.1'),200,300,NOW-age,NOW,'shares',mode,lag,'synthetic_stock'))
    session = mod.VerifiedSession(option.contract.provider_symbol,'SOFI','SOFI',NOW-3600*10**9,NOW+3600*10**9,
                                  NOW-3600*10**9,NOW+3600*10**9,('synthetic-session',))
    return option, stock, session


def test_feed_proof_and_chain_quote_use_independent_source_clocks():
    p, _, _ = provider()
    result = p.parse_chain_item(chain_raw(), contract())
    assert result.quote.source_ns == NOW-1 and result.quote.size_unit == 'contracts'
    assert result.quote.feed_mode == 'realtime' and result.quote.feed_lag_s == 0
    assert result.open_interest == 1500 and result.open_interest_basis == 'previous_session'
    assert result.reference_source_ns is None and result.greeks.source_ns is None
    assert result.greeks.delta is None and result.greeks.iv is None
    assert result.greeks.raw['delta'] == D('0.6') and result.greeks.raw['implied_volatility'] == D('0.4')


def test_no_last_quote_never_substitutes_fmv_trade_mid_or_underlying():
    p, _, _ = provider()
    raw = chain_raw()
    raw.pop('last_quote')
    raw.pop('greeks')
    result = p.parse_chain_item(raw, contract())
    assert result.quote.ask is None and result.quote.source_ns is None
    assert result.greeks.delta is None and result.greeks.iv is None


@pytest.mark.parametrize('field,value', [('ask','5.2'),('bid',True),('last_updated',1791898200000),
    ('last_updated',NOW+1),('bid_size',True),('ask_size','10'),('bid_size',-1)])
def test_malformed_quote_wire_evidence_is_rejected(field,value):
    p, _, _ = provider()
    raw = chain_raw()
    raw['last_quote'][field] = value
    with pytest.raises(LeapsDataError):
        p.parse_chain_item(raw, contract())


@pytest.mark.parametrize('field,value', [('ticker','O:SOFI280121C00016000'),('strike_price',16),
    ('expiration_date','2028-01-22'),('shares_per_contract',150),('exercise_style','european'),('contract_type','put')])
def test_chain_details_must_match_proven_reference(field,value):
    p, _, _ = provider()
    raw = chain_raw()
    raw['details'][field] = value
    with pytest.raises(LeapsDataError, match='chain_contract_mismatch'):
        p.parse_chain_item(raw, contract())


@pytest.mark.parametrize('age,lag,want', [(60*10**9,D(0),True),(60*10**9+1,D(0),False),
    (960*10**9,D(900),True),(960*10**9+1,D(900),False)])
def test_source_age_boundary_is_exact_not_receipt_time(age,lag,want):
    option, stock, session = bound_quotes(age=age,lag=lag)
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert result.algebra_eligible
    assert result.comparison_eligible is want
    assert result.expiry_ns == NOW-age+int((lag+60)*10**9)


@pytest.mark.parametrize('change', ['unknown_lag','unknown_feed','future','missing_source','wrong_units','zero_size','bool_size','string_size','missing_bid','zero_bid','crossed','last_only'])
def test_inadequate_observations_never_become_comparable(change):
    option, stock, session = bound_quotes()
    modifications = {'unknown_lag':dict(feed_mode='delayed',feed_lag_s=None), 'unknown_feed':dict(feed_mode=None),
        'future':dict(source_ns=NOW+1),'missing_source':dict(source_ns=None),'wrong_units':dict(size_unit='shares'),
        'zero_size':dict(ask_size=0),'bool_size':dict(bid_size=True),'string_size':dict(ask_size='10'),
        'missing_bid':dict(bid=None),'zero_bid':dict(bid=D(0)),'crossed':dict(bid=D(6))}
    if change == 'last_only':
        stock = replace(stock,quote=replace(stock.quote,bid=None,ask=None))
    else:
        option = replace(option,quote=replace(option.quote,**modifications[change]))
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert not result.comparison_eligible and result.liquidity_status != 'adequate'
    assert result.algebra_eligible is (change != 'future')


@pytest.mark.parametrize('offset,want', [(30*10**9,True),(30*10**9+1,False)])
def test_option_stock_source_synchronization_boundary(offset,want):
    option, stock, session = bound_quotes()
    stock = replace(stock,quote=replace(stock.quote,source_ns=option.quote.source_ns-offset))
    assert api().assess_quote(option,stock,now_ns=NOW,verified_session=session).comparison_eligible is want


@pytest.mark.parametrize('modification', [dict(option_class='AAPL'),dict(option_symbol='O:AAPL280121C00015000'),
    dict(underlying_ticker='AAPL'),dict(option_start_ns=NOW),dict(option_end_ns=NOW-1),
    dict(stock_start_ns=NOW),dict(stock_end_ns=NOW-1),dict(evidence_ids=())])
def test_session_proof_must_bind_actual_class_times_and_identity(modification):
    option, stock, session = bound_quotes()
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=replace(session,**modification))
    assert not result.comparison_eligible and 'unverified_session' in result.reasons


def test_anomalous_synchronous_ask_plus_strike_is_flagged_not_promoted():
    option, stock, session = bound_quotes()
    option = replace(option,quote=replace(option.quote,bid=D(4),ask=D('4.5')))
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert result.algebra_eligible and not result.comparison_eligible
    assert 'suspicious_price_relation' in result.reasons


def test_cursor_credential_query_is_stripped_before_return_or_follow():
    p, t, _ = provider(Response({'status':'OK','results':[], 'next_url':'https://api.massive.com/v3/reference/options/contracts?cursor=abc&apiKey=synthetic-private-key'}),Response())
    page = p.list_contract_page(run())
    assert page.next_cursor == 'abc'
    p.list_contract_page(run(),page.next_cursor)
    assert 'synthetic-private-key' not in t.calls[1][1]


def test_probe_missing_last_quote_is_not_entitlement_success():
    raw = chain_raw()
    raw.pop('last_quote')
    p, _, _ = provider(Response({'status':'OK','results':[{'ticker':'O:SOFI280121C00015000'}]}),
        Response({'status':'OK','results':[raw]}),Response({'status':'OK','results':{'T':'SOFI','p':20,'P':20.1,'s':200,'S':300,'y':NOW,'t':NOW}}))
    evidence = api().probe_capabilities(p)
    assert evidence.reference is True and evidence.option_quotes is None and evidence.stock_quotes is True
    assert evidence.display_rights is None and evidence.sessions is None
    assert p.request_count == 3


def test_probe_total_request_ceiling_includes_retry_attempts():
    p, t, _ = provider(Response(status=500),Response(),Response(status=500),Response())
    evidence = api().probe_capabilities(p,max_requests=3)
    assert p.request_count == 3 and len(t.calls) == 3
    assert evidence.option_quotes is None and evidence.display_rights is None


@pytest.mark.parametrize('status',[401,403])
def test_probe_globally_denied_access_stops_after_one_request(status):
    p, t, _ = provider(Response(status=status))
    evidence = api().probe_capabilities(p)
    assert evidence.reference is False and evidence.option_quotes is False and evidence.stock_quotes is False
    assert len(t.calls) == 1


@pytest.mark.parametrize('body', ['{"status":"ERROR","results":[],"status":"OK"}',
    '{"status":"OK","results":[{"strike_price":"15"}]}',
    '{"status":"OK","results":[{"strike_price":NaN}]}',
    '{"status":"OK","results":[{"strike_price":1e9999}]}'])
def test_ambiguous_or_non_numeric_wire_reference_never_reaches_consumer(body):
    p, _, a = provider(Response(text=body))
    with pytest.raises(LeapsDataError):
        p.list_contract_page(run())
    assert not a.held


@pytest.mark.parametrize('failure', ['headers','status','body'])
def test_private_transport_failures_never_escape_as_raw_exceptions(failure):
    response = Response()
    if failure == 'status':
        response.status_code = 'synthetic-private-key'
    elif failure == 'body':
        del response.text
    p, t, a = provider(response)
    if failure == 'headers':
        def broken_headers():
            raise RuntimeError('synthetic-private-key')
        t.market_headers = broken_headers
    with pytest.raises(LeapsDataError) as caught:
        p.list_contract_page(run())
    assert 'synthetic-private-key' not in str(caught.value) and not a.held


def test_429_pauses_subsequent_requests_without_transport():
    p, t, _ = provider(Response(status=429),Response())
    for _ in range(2):
        with pytest.raises(LeapsDataError,match='rate_limited'):
            p.list_contract_page(run())
    assert len(t.calls) == 1


@pytest.mark.parametrize('first', [Response(status=503),TimeoutError('synthetic-private-key')])
def test_second_retry_failure_is_terminal_and_sanitized(first):
    p, t, a = provider(first,Response(status=503),Response())
    with pytest.raises(LeapsDataError,match='transport_unavailable'):
        p.list_contract_page(run())
    assert len(t.calls) == 2 and not a.held


@pytest.mark.parametrize('kind',['CS','ADRC','ADRP'])
def test_listing_page_gates_each_kind_with_no_momentum_filter(kind):
    p, t, _ = provider(Response({'status':'OK','results':[{'ticker':'SOFI','market':'stocks','locale':'us','type':kind,'active':True,'currency_name':'usd','primary_exchange':'XNAS'}]}))
    page = p.list_listing_page(kind,run())
    assert page.items[0]['type'] == kind and f'type={kind}' in t.calls[0][1]
    with pytest.raises(TypeError):
        page.items[0]['ticker'] = 'CHANGED'


def test_chain_page_keeps_real_fields_and_late_join_is_explicit():
    p, t, _ = provider(Response({'status':'OK','results':[chain_raw()]}))
    page = p.chain_page('SOFI',run())
    assert page.items[0]['details']['strike_price'] == 15
    assert 'currency' not in page.items[0]['details']
    assert p.parse_chain_item(page.items[0],contract()).quote.ask == D('5.2')
    assert '/v3/snapshot/options/SOFI?' in t.calls[0][1]


@pytest.mark.parametrize('value', [True,False,None,{'accessible':True},
    {'accessible':True,'feed_mode':'delayed','feed_lag_s':None,'evidence_ids':['synthetic']},
    {'accessible':True,'feed_mode':'realtime','feed_lag_s':D(0),'evidence_ids':[]}])
def test_access_is_not_feed_recency_proof(value):
    p, _, _ = provider()
    p.capability = replace(p.capability, option_quotes=value)
    quote = p.parse_chain_item(chain_raw(),contract()).quote
    assert quote.feed_mode is None and quote.feed_lag_s is None


def test_quote_timeframe_conflicting_with_feed_evidence_stays_unknown():
    p, _, _ = provider()
    raw = chain_raw()
    raw['last_quote']['timeframe'] = 'DELAYED'
    assert p.parse_chain_item(raw,contract()).quote.feed_mode is None


def test_missing_stock_exchange_clock_does_not_borrow_sip_clock():
    p, _, _ = provider(Response({'status':'OK','results':{'T':'SOFI','p':20,'P':20.1,'s':200,'S':300,'t':NOW}}))
    assert p.underlying_quote('SOFI').source_ns is None


@pytest.mark.parametrize('timestamp', [NOW,1791898200,'1791763200000',1791849600000])
def test_daily_history_rejects_wrong_unit_type_or_incomplete_date(timestamp):
    p, _, _ = provider(Response({'status':'OK','ticker':'SOFI','results':[{'t':timestamp,'o':20,'h':21,'l':19,'c':20,'v':100}]}))
    with pytest.raises(LeapsDataError):
        p.daily_history('SOFI',completed_through=date(2026,10,12),as_of=date(2026,10,13),completion=daily_completion(historical=True))


def test_history_never_persists_secret_bearing_pagination_url():
    p, _, _ = provider(Response({'status':'OK','ticker':'SOFI','results':[],
        'next_url':'https://api.massive.com/v2/aggs/ticker/SOFI?apiKey=synthetic-private-key'}))
    with pytest.raises(LeapsDataError, match='history_truncated'):
        p.daily_history('SOFI',completed_through=date(2026,10,12),as_of=date(2026,10,13),completion=daily_completion(historical=True))


def test_old_unchanged_nbbo_new_receipt_retains_only_dated_algebra():
    option, stock, session = bound_quotes(age=61*10**9)
    assert option.quote.received_ns == NOW
    assessment = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert assessment.algebra_eligible and not assessment.comparison_eligible
    assert 'option_stale' in assessment.reasons


def test_stock_last_only_cannot_trigger_price_relation_warning():
    option, stock, session = bound_quotes()
    option = replace(option,quote=replace(option.quote,bid=D(4),ask=D('4.5')))
    stock = replace(stock,quote=replace(stock.quote,bid=None,ask=None))
    assert 'suspicious_price_relation' not in api().assess_quote(option,stock,now_ns=NOW,verified_session=session).reasons


def test_probe_quote_clock_is_not_frozen_at_probe_start():
    p, _, _ = provider(Response(),Response({'status':'OK','results':[chain_raw()]}))
    clocks = iter([NOW-2,NOW,NOW])
    p.clock_ns = lambda: next(clocks)
    evidence = api().probe_capabilities(p,max_requests=2)
    assert evidence.option_quotes is True


@pytest.mark.parametrize('source', [str(NOW-1),True])
def test_provider_source_timestamp_requires_wire_integer(source):
    p, _, _ = provider()
    raw = chain_raw()
    raw['last_quote']['last_updated'] = source
    with pytest.raises(LeapsDataError):
        p.parse_chain_item(raw,contract())


def test_populated_quote_size_above_purchase_quantity_limit_is_not_rejected():
    p, _, _ = provider()
    raw = chain_raw()
    raw['last_quote']['bid_size'] = 2000000
    assert p.parse_chain_item(raw,contract()).quote.bid_size == 2000000


def test_bare_boolean_session_cannot_verify_current_quotes():
    option, stock, _ = bound_quotes()
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=True)
    assert not result.comparison_eligible and 'unverified_session' in result.reasons


def test_session_accepts_verified_non_rth_interval_not_a_hardcoded_clock():
    option, stock, session = bound_quotes()
    # An independently supplied eligible-class session is sufficient; this
    # test does not invent which live classes have extended-hours eligibility.
    session = replace(session,option_start_ns=NOW-10,option_end_ns=NOW+10,stock_start_ns=NOW-10,stock_end_ns=NOW+10)
    assert api().assess_quote(option,stock,now_ns=NOW,verified_session=session).comparison_eligible


def test_sec_retry_releases_sec_lease_each_time_and_never_market_headers():
    p, t, a = provider(Response(status=503),Response({'cik':1818874,'facts':{}}))
    def forbidden():
        raise AssertionError('Market headers must not be requested for SEC')
    t.market_headers = forbidden
    assert p.company_facts('1818874',configured_sec_user_agent='Synthetic Research synthetic@example.invalid')['cik'] == 1818874
    assert a.events == [('acquire','sec'),('release','sec')]*2


@pytest.mark.parametrize('action', ['ticker','listing','date','cik','user_agent'])
def test_invalid_request_inputs_never_acquire_a_lease(action):
    p, t, a = provider()
    with pytest.raises(LeapsDataError):
        if action == 'ticker':
            p.underlying_quote('SOFI?apiKey=secret')
        elif action == 'listing':
            p.list_listing_page('ETF',run())
        elif action == 'date':
            p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13))
        elif action == 'cik':
            p.company_facts('../secret',configured_sec_user_agent='Synthetic Research synthetic@example.invalid')
        else:
            p.company_facts('1818874',configured_sec_user_agent='bad\r\nAuthorization: secret')
    assert a.events == [] and t.calls == []


def test_nonretryable_transport_exception_releases_and_sanitizes():
    p, t, a = provider(RuntimeError('synthetic-private-key'),Response())
    with pytest.raises(LeapsDataError,match='transport_error'):
        p.list_contract_page(run())
    assert len(t.calls) == 1 and not a.held


def test_session_proof_string_is_not_a_collection_of_evidence_ids():
    _, _, session = bound_quotes()
    with pytest.raises(LeapsDataError):
        replace(session,evidence_ids='not-a-proof-list')


def test_synchronous_two_sided_price_anomaly_remains_visible_without_fill_size():
    option, stock, session = bound_quotes()
    option = replace(option,quote=replace(option.quote,bid=D(4),ask=D('4.5'),ask_size=0))
    result = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert 'suspicious_price_relation' in result.reasons
    assert not result.comparison_eligible and result.algebra_eligible


def test_probe_reference_request_is_bounded_to_sofi_not_the_entire_universe():
    p, t, _ = provider(Response())
    api().probe_capabilities(p,max_requests=1)
    assert 'underlying_ticker=SOFI' in t.calls[0][1] and 'limit=1' in t.calls[0][1]


@pytest.mark.parametrize('field', ['option_end_ns','stock_end_ns'])
@pytest.mark.parametrize('offset', [10,0])
def test_assessment_expiry_is_capped_by_each_session_end_inclusively(field,offset):
    option, stock, session = bound_quotes()
    session = replace(session,**{field:NOW+offset})
    assessment = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert assessment.comparison_eligible
    assert assessment.expiry_ns == NOW+offset
    assert api().assess_quote(option,stock,now_ns=NOW+offset,verified_session=session).comparison_eligible
    later = api().assess_quote(option,stock,now_ns=NOW+offset+1,verified_session=session)
    assert not later.comparison_eligible


def test_quote_freshness_can_expire_before_either_session_end():
    option, stock, session = bound_quotes(age=60*10**9)
    assessment = api().assess_quote(option,stock,now_ns=NOW,verified_session=session)
    assert assessment.comparison_eligible and assessment.expiry_ns == NOW


# Synthetic source-window proof; these are not live calendar/entitlement facts.
# Oct13 bucket start=00ET, witnessed eligible coverage end=20ET, 15-minute lag.
DAILY_START = 1791864000000000000
DAILY_END = 1791936000000000000
DAILY_AVAILABLE = 1791936900000000000
DAILY_AFTER = 1791939600000000000  # 21ET Oct13, UTC Oct14: as_of is still Oct13.


def daily_completion(*, historical=False):
    mod = api()
    assert hasattr(mod,'DailyCompletionProof'), 'Verified daily completion feature is missing'
    shift = 86400*10**9 if historical else 0
    return mod.DailyCompletionProof('SOFI',date(2026,10,12 if historical else 13),
        'massive_stock_daily_aggregates','day',1,DAILY_START-shift,DAILY_END-shift,
        DAILY_END-shift,DAILY_AVAILABLE-shift,D(900),('synthetic-full-source-coverage',))


def daily_response(timestamp=1791864000000):
    return Response({'status':'OK','ticker':'SOFI','adjusted':True,
                     'results':[{'t':timestamp,'o':20,'h':21,'l':19,'c':20.5,'v':2000}]})


def test_same_day_verified_completed_history_is_accepted_without_faking_tomorrow():
    p, t, _ = provider(daily_response())
    p.clock_ns = lambda: DAILY_AFTER
    data = p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())
    assert data['results'][0]['t'] == 1791864000000
    assert '/2026-10-13?' in t.calls[0][1]


@pytest.mark.parametrize('offset,allowed', [(-1,False),(0,True),(1,True)])
def test_history_completion_and_lag_availability_have_exact_ns_boundary(offset,allowed):
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: DAILY_AVAILABLE+offset
    if allowed:
        assert p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())['ticker'] == 'SOFI'
        assert len(t.calls) == 1
    else:
        with pytest.raises(LeapsDataError,match='unverified_daily_completion'):
            p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())
        assert t.calls == [] and a.events == []


@pytest.mark.parametrize('target',[date(2026,10,12),date(2026,10,13)])
def test_historical_or_same_day_history_without_proof_never_requests(target):
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: DAILY_AFTER
    with pytest.raises(LeapsDataError,match='unverified_daily_completion'):
        p.daily_history('SOFI',completed_through=target,as_of=date(2026,10,13))
    assert t.calls == [] and a.events == []


@pytest.mark.parametrize('mutation', [dict(ticker='AAPL'),dict(market_date=date(2026,10,12)),
    dict(source='some_other_feed'),dict(timespan='hour'),dict(multiplier=2),dict(multiplier=True),
    dict(window_start_ns=DAILY_START-86400*10**9),dict(window_start_ns=DAILY_END),
    dict(window_end_ns=DAILY_START),dict(completed_ns=DAILY_END-1),
    dict(completed_ns=DAILY_AFTER+1),dict(available_ns=DAILY_AFTER+1),
    dict(available_ns=DAILY_END-1),dict(available_ns=DAILY_AVAILABLE-1),
    dict(feed_lag_s=None),dict(feed_lag_s=D(-1)),dict(feed_lag_s=900),dict(feed_lag_s=D('NaN')),
    dict(evidence_ids=()),dict(evidence_ids=(' ',))])
def test_mismatched_future_or_malformed_completion_fails_before_admission(mutation):
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: DAILY_AFTER
    proof = replace(daily_completion(),**mutation)
    with pytest.raises(LeapsDataError,match='unverified_daily_completion'):
        p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=proof)
    assert t.calls == [] and a.events == []


@pytest.mark.parametrize('field',['window_start_ns','window_end_ns','completed_ns','available_ns'])
@pytest.mark.parametrize('bad',[True,'1791936000000000000',1791936000000])
def test_completion_clock_metadata_is_strict_integer_ns(field,bad):
    with pytest.raises(LeapsDataError):
        replace(daily_completion(),**{field:bad})


@pytest.mark.parametrize('day',[date(2026,10,12),date(2026,10,14)])
def test_as_of_must_be_truthful_in_injected_clock_eastern_date(day):
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: DAILY_AFTER
    with pytest.raises(LeapsDataError,match='invalid_as_of'):
        p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=day,completion=daily_completion())
    assert t.calls == [] and a.events == []


def test_as_of_et_midnight_is_not_rounded_forward_from_last_nanosecond():
    p, _, _ = provider(daily_response())
    p.clock_ns = lambda: 1791950400000000000-1  # Oct14 00ET minus one nanosecond.
    assert p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())['ticker'] == 'SOFI'


@pytest.mark.parametrize('timestamp',[1791864000001,1791900000000,1791950400000])
def test_returned_target_daily_bucket_must_match_verified_source_window(timestamp):
    p, _, _ = provider(daily_response(timestamp))
    p.clock_ns = lambda: DAILY_AFTER
    with pytest.raises(LeapsDataError,match='incomplete_daily_bar'):
        p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())


@pytest.mark.parametrize('status,reason',[(401,'authorization_denied'),(403,'authorization_denied'),(429,'rate_limited')])
def test_sec_authority_failure_does_not_poison_independent_market_requests(status,reason):
    p, t, a = provider(Response(status=status),Response())
    for _ in range(2):
        with pytest.raises(LeapsDataError,match=reason):
            p.company_facts('1818874',configured_sec_user_agent='Synthetic Research synthetic@example.invalid')
    assert len(t.calls) == 1
    assert p.list_contract_page(run()).complete
    assert len(t.calls) == 2
    assert 'Authorization' not in t.calls[0][2] and 'Authorization' in t.calls[1][2]
    assert a.events == [('acquire','sec'),('release','sec'),('acquire','market'),('release','market')]
    assert not a.held


@pytest.mark.parametrize('status,reason',[(401,'authorization_denied'),(403,'authorization_denied'),(429,'rate_limited')])
def test_market_authority_failure_is_global_to_market_but_not_sec(status,reason):
    p, t, a = provider(Response(status=status),Response({'cik':1818874,'facts':{}}))
    for request in (lambda:p.list_contract_page(run()),lambda:p.underlying_quote('SOFI')):
        with pytest.raises(LeapsDataError,match=reason):
            request()
    assert len(t.calls) == 1
    assert p.company_facts('1818874',configured_sec_user_agent='Synthetic Research synthetic@example.invalid')['cik'] == 1818874
    assert len(t.calls) == 2
    assert 'Authorization' in t.calls[0][2] and 'Authorization' not in t.calls[1][2]
    assert a.events == [('acquire','market'),('release','market'),('acquire','sec'),('release','sec')]
    assert not a.held


@pytest.mark.parametrize('instant',[DAILY_END-4*3600*10**9,DAILY_END-1])
def test_regular_close_or_before_full_source_completion_is_not_daily_completion(instant):
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: instant
    with pytest.raises(LeapsDataError,match='unverified_daily_completion'):
        p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=daily_completion())
    assert t.calls == [] and a.events == []


def test_resource_unbounded_lag_is_a_safe_completion_error():
    p, t, a = provider(daily_response())
    p.clock_ns = lambda: DAILY_AFTER
    proof = replace(daily_completion(),feed_lag_s=D('1e999999999'))
    with pytest.raises(LeapsDataError,match='unverified_daily_completion'):
        p.daily_history('SOFI',completed_through=date(2026,10,13),as_of=date(2026,10,13),completion=proof)
    assert t.calls == [] and a.events == []
