"""Offline discovery behavior: no hidden universe gate or live requests."""
from dataclasses import replace
from importlib import import_module
from pathlib import Path
from decimal import Decimal as D
import json
import pytest

from modules.leaps.models import LeapsDataError, PageResult
from tests.fixtures.leaps.provider import NOW, run, raw_contract, listing
from tests.fixtures.leaps.discovery import Market, term_producer, session


def service():
    assert Path('modules/leaps/service.py').is_file(), 'LEAPS discovery feature is missing'
    return import_module('modules.leaps.service')


def wrapper(market, **kw):
    return service().DiscoveryProvider(market, terms_producer=term_producer, session_producer=session, **kw)


def finish(provider, spec=None, cursor=None):
    for _ in range(500):
        snap = service().discover(provider,spec or run(),cursor)
        if snap.status in ('complete','partial','failed'):
            return snap
        cursor = snap.cursor
        assert isinstance(json.loads(cursor),dict)
    pytest.fail('discovery did not terminate')


def test_more_than_eight_underlyings_twenty_pages_and_all_listing_variants():
    tickers = tuple('T'+chr(65+i) for i in range(12))
    market = Market(tickers, pages=23)
    p = wrapper(market)
    first = service().discover(p,run(),None)
    assert first.coverage['reference_total'] is None
    snap = finish(p,cursor=first.cursor)
    assert snap.status == 'complete'
    assert snap.coverage['reference_total'] == 12
    assert snap.coverage['underlyings_total'] == 12
    assert snap.coverage['chain_observed'] == snap.coverage['parsed'] == snap.coverage['eligible'] == 12
    assert snap.coverage['chain_complete'] == 12
    assert len([x for x in market.calls if x[0]=='reference']) == 23
    assert len([x for x in market.calls if x[0]=='stock']) == 12
    assert len(snap.data['comparisons']) == 12


def test_actual_empty_window_is_complete_not_provider_failure():
    snap=finish(wrapper(Market((),pages=1)))
    assert snap.status=='complete' and snap.coverage['reference_total']==0
    assert not snap.data['comparisons'] and not snap.reasons


@pytest.mark.parametrize('change', [{'type':'ETF'},{'type':'UNKNOWN'},{'locale':'gb'},{'active':False}])
def test_only_verified_us_equity_adr_listing_types_are_admitted(change):
    m=Market(); m.listings['CS'][0].update(change)
    snap=finish(wrapper(m))
    assert snap.coverage['eligible']==0 and snap.coverage['excluded']==1
    assert not any(x[0].startswith('chain:') for x in m.calls)


def test_duplicate_reference_deduplicates_and_conflict_excludes_only_its_contract():
    m=Market(('SOFI','GOOD'))
    m.references[0].append(dict(m.references[0][0]))
    conflict=dict(m.references[0][0],strike_price=16)
    m.references.append([conflict])
    snap=finish(wrapper(m))
    assert snap.coverage['reference_total']==2 and snap.coverage['reference_duplicates']==1
    assert snap.coverage['eligible']==1 and snap.coverage['excluded']==1
    assert snap.data['comparisons'][0]['contract'].underlying=='GOOD'


def test_missing_reference_in_completed_chain_is_unobserved_not_evaluated():
    m=Market(); m.references[0].append(raw_contract('SOFI',16))
    snap=finish(wrapper(m))
    assert snap.coverage['reference_total']==2 and snap.coverage['chain_observed']==1
    assert snap.coverage['unobserved']==1 and snap.coverage['eligible']==1
    assert any(x['reason']=='not_observed_in_completed_chain' for x in snap.data['exclusions'])


def test_failed_second_chain_page_preserves_first_and_other_underlying():
    m=Market(('SOFI','GOOD'))
    m.references[0].append(raw_contract('SOFI',16))
    m.chains['SOFI'].append(LeapsDataError('transport_unavailable','provider'))
    snap=finish(wrapper(m))
    assert snap.status=='partial' and 'transport_unavailable' in snap.reasons
    assert snap.coverage['chain_complete']==1 and snap.coverage['eligible']==2
    assert snap.coverage['unobserved']==1
    assert any(x['reason']=='chain_incomplete' for x in snap.data['exclusions'])


def test_persisted_cycle_detection_survives_new_provider_instance():
    m=Market()
    m.list_contract_page=lambda run,cursor=None: PageResult((), 'cycle',False,None,None)
    first=service().discover(wrapper(m),run(),None)
    snap=service().discover(wrapper(m),run(),first.cursor)
    assert snap.status=='failed' and 'cursor_cycle' in snap.reasons
    assert snap.coverage['reference_total'] is None


def test_checkpoint_binds_every_run_field_and_rejects_unknown_filters():
    p=wrapper(Market())
    cursor=service().discover(p,run(),None).cursor
    for field,value in [('run_id','other'),('universe_revision','v2'),('schema_version','2'),('methodology_version','2'),('filters',{'min_delta':'0.2'})]:
        with pytest.raises(LeapsDataError,match='checkpoint_mismatch'):
            service().discover(p,replace(run(),**{field:value}),cursor)
    with pytest.raises(LeapsDataError,match='unsupported_filter'):
        finish(p,replace(run(),filters={'momentum':20}))


def test_missing_terms_is_excluded_without_fabricating_proof():
    p=service().DiscoveryProvider(Market())
    snap=finish(p)
    assert snap.coverage['eligible']==0 and snap.coverage['excluded']==1
    assert snap.data['exclusions'][0]['reason']=='unverified_terms'


def test_chain_contract_local_invalid_record_keeps_good_contract_and_raw_observation_counts():
    m=Market(); m.references[0].append(raw_contract('SOFI',16))
    bad=dict(m.chains['SOFI'][0][0]); bad['details']=dict(bad['details'],ticker=raw_contract('SOFI',16)['ticker'],strike_price=16)
    bad['last_quote']={'ask':False}
    m.chains['SOFI'][0].extend([{'details':None},bad])
    snap=finish(wrapper(m))
    assert snap.coverage['parsed']==1 and snap.coverage['chain_observed']==2
    assert snap.coverage['excluded']==1 and snap.coverage['chain_complete']==1


def test_stock_failure_leaves_ask_algebra_visible_and_other_ticker_runs():
    m=Market(('SOFI','GOOD')); original=m.underlying_quote
    def quote(ticker):
        if ticker=='SOFI':
            raise LeapsDataError('transport_unavailable','provider')
        return original(ticker)
    m.underlying_quote=quote
    snap=finish(wrapper(m))
    assert snap.status=='partial' and snap.coverage['parsed']==2 and snap.coverage['eligible']==1
    row=next(x for x in snap.data['comparisons'] if x['contract'].underlying=='SOFI')
    assert row['assessment'].algebra_eligible and str(row['break_even'])=='20.2'
    assert row['be_rise'] is None


def test_rank_is_raw_be_rise_with_stable_contract_id_and_null_last():
    m=Market(('ZZZ','AAA','NULL'))
    m.chains['ZZZ'][0][0]['last_quote']['ask']=6
    m.chains['ZZZ'][0][0]['last_quote']['bid']=5
    m.chains['NULL'][0][0].pop('last_quote')
    snap=finish(wrapper(m))
    assert [r['contract'].underlying for r in snap.data['comparisons']]==['AAA','ZZZ','NULL']
    m=Market(('ZZZ','AAA'))
    assert [r['contract'].underlying for r in finish(wrapper(m)).data['comparisons']]==['AAA','ZZZ']


def test_filter_unknown_stays_visible_and_counter_separate_from_eligible():
    snap=finish(wrapper(Market()),replace(run(),filters={'min_delta':'0.2'}))
    assert snap.coverage['filter_unknown']==1 and snap.coverage['eligible']==0
    assert len(snap.data['comparisons'])==1 and snap.data['comparisons'][0]['filter_status']=='missing_evidence'


def test_reference_failure_does_not_claim_known_denominator():
    m=Market(); m.references.append(LeapsDataError('transport_unavailable','provider'))
    snap=finish(wrapper(m))
    assert snap.status=='failed' and snap.coverage['reference_seen']==1 and snap.coverage['reference_total'] is None


def test_resume_rejects_modified_payload_and_market_date():
    p=wrapper(Market()); cursor=service().discover(p,run()).cursor
    value=json.loads(cursor); value['state']['v']['phase']='done'
    with pytest.raises(LeapsDataError,match='invalid_checkpoint'):
        service().discover(p,run(),json.dumps(value))
    with pytest.raises(LeapsDataError,match='checkpoint_mismatch'):
        service().discover(p,replace(run(),market_date=run().market_date.replace(day=14)),cursor)


def test_paused_chain_page_retries_same_cursor_without_recounting():
    m=Market(); m.chains['SOFI'].append(LeapsDataError('admission_denied','provider'))
    p=wrapper(m); cursor=None
    for _ in range(20):
        snap=service().discover(p,run(),cursor); cursor=snap.cursor
        if snap.status=='paused': break
    assert snap.status=='paused' and snap.coverage['parsed']==1
    m.chains['SOFI'][1]=[]
    resumed=finish(p,cursor=cursor)
    assert resumed.status=='complete' and resumed.coverage['parsed']==1 and resumed.coverage['chain_complete']==1


def test_real_json_resume_retains_exact_prices_and_recomputes_unbounded_derived_ratios():
    m=Market(); quote=m.chains['SOFI'][0][0]['last_quote']
    quote.update(ask=D('5.123456789012'),bid=D('5.012345678901'))
    original=m.underlying_quote
    m.underlying_quote=lambda ticker:replace(original(ticker),bid=D('19.123456789012'),ask=D('19.223456789012'))
    first=finish(wrapper(m))
    resumed=service().discover(wrapper(m),run(),json.loads(json.dumps(first.cursor)))
    a,b=first.data['comparisons'][0],resumed.data['comparisons'][0]
    assert a==b and b['observation'].quote.ask==D('5.123456789012')
    assert len(str(b['be_rise']).split('.')[1])>12
    assert b['scenarios'][1]['terminal']==D('15.29876543120960')
    assert b['stock_reference'].source_ns==NOW-1


@pytest.mark.parametrize('field,value',[('phase','bogus'),('index',True),('references',[]),('listing_kind',4),('contracts',[]),('cursor',{'url':'private'})])
def test_semantically_invalid_checkpoints_fail_safely_even_with_recomputed_digest(field,value):
    from hashlib import sha256
    p=wrapper(Market()); raw=json.loads(service().discover(p,run()).cursor)
    raw['state']['v'][field]=value
    raw['digest']=sha256(json.dumps(raw['state'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    with pytest.raises(LeapsDataError,match='invalid_checkpoint'):
        service().discover(p,run(),json.dumps(raw))


def test_unmatched_and_malformed_chain_rows_have_visible_distinct_counts():
    m=Market(); m.chains['SOFI'][0].extend([{'details':None},{'details':{'ticker':'O:OTHER280121C00015000'}}])
    snap=finish(wrapper(m))
    assert snap.coverage['chain_unmatched']==1 and snap.coverage['chain_malformed']==1
    assert snap.coverage['parsed']==snap.coverage['reference_total']==1


def test_unhashable_reference_underlying_is_contract_local_not_run_failure():
    m=Market(('SOFI','GOOD')); m.references[0][0]['underlying_ticker']=[]
    snap=finish(wrapper(m))
    assert snap.coverage['eligible']==1 and snap.coverage['excluded']==1


def test_rejected_out_of_wire_bound_raw_decimal_can_resume_without_poisoning_others():
    m=Market(('SOFI','GOOD')); m.chains['GOOD'][0][0]['last_quote']['ask']=D('1e13')
    snap=finish(wrapper(m))
    assert snap.coverage['parsed']==1 and snap.coverage['excluded']==1
    again=service().discover(wrapper(m),run(),snap.cursor)
    assert again.coverage['parsed']==1 and again.data['comparisons'][0]['contract'].underlying=='SOFI'
