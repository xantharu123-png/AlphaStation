"""Pure completed history and exact option comparison evidence."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from importlib import import_module
from pathlib import Path
import pytest
from modules.leaps.models import UnderlyingEvidence, QuoteObservation, LeapsDataError
from modules.leaps.provider import StockQuote
from tests.fixtures.leaps.provider import NOW, contract, chain_raw, run, Response, Admission, Transport, capability
from tests.fixtures.leaps.discovery import Market, session, completion


def api(name):
    assert Path('modules/leaps/'+name+'.py').is_file(), 'LEAPS '+name+' feature is missing'
    return import_module('modules.leaps.'+name)


def bars():
    return [dict(date=(date(2025,1,1)+timedelta(days=i)).isoformat(),open=D(20),high=D(21),low=D(19),close=D(20),volume=D(1000),completed=True) for i in range(220)]


def quality(**kw):
    return api('underlying').build_underlying_evidence('SOFI',completed_bars=kw.get('bars',bars()),facts=kw.get('facts'),sources=('synthetic-completed-history',),as_of=date(2026,10,13))


def test_full_validated_decimal_history_uses_pure_indicators_and_missing_facts_are_not_zero():
    result=quality()
    assert result.indicators['ema200']==20
    assert result.coverage['history']=='complete'
    assert result.coverage['quality']=='partial' and result.facts['score'] is None
    assert result.facts['backtest_eligible'] is False


@pytest.mark.parametrize('change', [{'high':D(18)},{'close':True},{'volume':D(-1)},{'open':'20'},{'completed':False},{'close':float('nan')}])
def test_invalid_earliest_causal_bar_rejects_entire_history(change):
    data=bars(); data[0].update(change)
    result=quality(bars=data)
    assert result.coverage['history']=='invalid' and not result.indicators


def test_future_and_duplicate_dates_rejected():
    for changed in ('2028-01-01',bars()[1]['date']):
        data=bars(); data[0]['date']=changed
        assert quality(bars=data).coverage['history']=='invalid'


def comparison(*,ask='5.2',bid='5.1',stock=True,proof=True,filters=None):
    s=api('service'); m=Market(); c=contract(); raw=chain_raw()
    raw['last_quote'].update(ask=D(ask),bid=D(bid))
    option=m.parse_chain_item(raw,c)
    sq=StockQuote('SOFI',m.underlying_quote('SOFI') if stock else QuoteObservation())
    evidence=s.ComparisonEvidence(quality(),sq,session(c,sq,NOW) if proof else None)
    return s.compare_contract(c,option,evidence,replace(run(),filters=filters or {}),NOW)


def test_comparison_preserves_exact_be_rise_ask_stock_basis_and_scenarios():
    result=comparison()
    assert result['break_even']==D('20.2') and result['be_rise']==D('0.01')
    assert result['stock_reference'].price==D(20) and result['stock_reference'].price_kind=='bid'
    assert result['stock_reference'].source_ns==NOW-1
    assert result['assessment'].comparison_eligible
    assert len(result['scenarios'])==6 and result['scenarios'][0]['result'].pnl==D('-520')
    assert result['greeks'].delta is None
    assert result['filter_status']=='passed' and result['terms_fingerprint']==contract().terms_fingerprint


def test_ask_only_algebra_and_missing_filter_evidence_stay_visible():
    r=comparison(stock=False,proof=False,filters={'max_be_rise':'0.2','min_delta':'0.5'})
    assert r['break_even']==D('20.2') and r['be_rise'] is None
    assert r['assessment'].algebra_eligible and not r['assessment'].comparison_eligible
    assert r['filter_status']=='missing_evidence' and r['filter_missing']==('max_be_rise','min_delta')


def test_strict_filter_bounds_raw_unrounded_and_fingerprint():
    a=comparison(filters={'max_be_rise':'0.009999999999'})
    b=comparison(filters={'max_be_rise':'0.01','min_open_interest':1500})
    assert a['filter_status']=='excluded' and b['filter_status']=='passed'
    assert a['filter_fingerprint']!=b['filter_fingerprint']


def test_no_completion_proof_means_no_history_io_and_partial_quality():
    from modules.leaps.provider import MassiveLeapsProvider
    a=Admission(); t=Transport([],a); p=MassiveLeapsProvider(t,lambda:NOW,a,capability())
    loader=api('underlying').CachedUnderlyingLoader(p)
    result=loader.load('SOFI',{'ticker':'SOFI'},now_ns=NOW)
    assert result.coverage['quality']=='partial' and t.calls==[]
    assert 'unverified_daily_completion' in result.coverage['reasons']


def test_listing_cik_missing_user_agent_and_facts_are_not_fabricated():
    class Provider:
        def company_facts(self,*a,**kw):
            pytest.fail('must not fetch without verified CIK and configured user agent')
    loader=api('underlying').CachedUnderlyingLoader(Provider())
    result=loader.load('SOFI',{'ticker':'SOFI','cik':'123'},now_ns=NOW)
    assert result.facts['score'] is None


def test_admitted_loader_caches_history_and_sec_per_underlying_not_contract():
    from modules.leaps.provider import MassiveLeapsProvider
    from tests.fixtures.leaps.provider import listing
    a=Admission(); t=Transport([
        Response({'status':'OK','ticker':'SOFI','results':[{'t':1791777600000,'o':20,'h':21,'l':19,'c':20,'v':2000}]}),
        Response({'cik':123,'facts':{},'entityName':'Synthetic'})],a)
    p=MassiveLeapsProvider(t,lambda:NOW,a,capability())
    loader=api('underlying').CachedUnderlyingLoader(p,completion_producer=completion,configured_sec_user_agent='Synthetic synthetic@example.invalid')
    first=loader.load('SOFI',listing(cik='123'),now_ns=NOW)
    second=loader.load('SOFI',listing(cik='123'),now_ns=NOW+1)
    assert first==second and len(t.calls)==2
    assert [x[1] for x in a.events if x[0]=='acquire']==['market','sec']
    assert first.indicators['indicators_meta']['completed_history_bars']==1
    assert first.facts['score'] is None and first.coverage['quality']=='partial'
    assert first.sources[0].evidence_ids==('synthetic-full-source-window',)


@pytest.mark.parametrize('kind',['Banks','Real Estate Investment Trusts'])
def test_specialist_industries_and_facts_remain_live_partial(kind):
    q=quality(facts={'companyfacts':{'facts':{'us-gaap':{}},'entityName':'Synthetic'},'industry':kind})
    assert q.coverage['facts']=='partial' and q.facts['backtest_eligible'] is False
    assert q.facts['freshness_basis']=='analysis_wall_clock_utc'


def test_invalid_completion_factory_proof_makes_no_history_request():
    class Never:
        def daily_history(self,*args,**kw): pytest.fail('invalid proof must not request history')
    loader=api('underlying').CachedUnderlyingLoader(Never(),completion_producer=lambda *args:completion('WRONG',None,None))
    result=loader.load('SOFI',{'ticker':'SOFI'},now_ns=NOW)
    assert 'unverified_daily_completion' in result.coverage['reasons']


def test_cached_source_history_envelope_truncation_and_identity_fail_closed():
    class BrokenCache:
        def daily_history(self,*args,**kw):
            return {'ticker':'WRONG','next_url':'https://example.invalid/private','results':[]}
    result=api('underlying').CachedUnderlyingLoader(BrokenCache(),completion_producer=completion).load('SOFI',{'ticker':'SOFI'},now_ns=NOW)
    assert 'invalid_history_envelope' in result.coverage['reasons']


def test_old_quotes_are_dated_not_current_but_stock_basis_not_replaced():
    s=api('service'); m=Market(); c=contract(); option=m.parse_chain_item(chain_raw(),c)
    sq=StockQuote('SOFI',replace(m.underlying_quote('SOFI'),source_ns=NOW-61*10**9))
    result=s.compare_contract(c,option,s.ComparisonEvidence(quality(),sq,session(c,sq,NOW)),run(),NOW)
    assert not result['assessment'].comparison_eligible
    assert result['stock_reference'].source_ns==NOW-61*10**9 and result['stock_reference'].price==20


def test_sec_failure_is_recorded_by_authority_and_does_not_hide_quality():
    from tests.fixtures.leaps.provider import listing
    class Failed:
        def company_facts(self,*a,**kw): raise LeapsDataError('authorization_denied','provider')
    result=api('underlying').CachedUnderlyingLoader(Failed(),configured_sec_user_agent='Synthetic user-agent').load('SOFI',listing(cik='123'),now_ns=NOW)
    assert result.coverage['provider_failures']==({'authority':'sec','reason':'authorization_denied'},)
    assert result.facts['score'] is None


def test_cached_facts_wrong_cik_does_not_become_quality_evidence():
    from tests.fixtures.leaps.provider import listing
    class Wrong:
        def company_facts(self,*a,**kw): return {'cik':456,'facts':{'us-gaap':{}}}
    result=api('underlying').CachedUnderlyingLoader(Wrong(),configured_sec_user_agent='Synthetic user-agent').load('SOFI',listing(cik='123'),now_ns=NOW)
    assert 'invalid_company_facts' in result.coverage['reasons'] and result.facts['score'] is None


def test_nonfinite_native_analytics_are_unknown_partial_not_published(monkeypatch):
    import modules.ticker_detail_indicators as helper
    original=helper.build_ticker_detail_indicators
    def overflow(bars):
        result=original(bars); result['ema200']=float('inf')
        return result
    monkeypatch.setattr(helper,'build_ticker_detail_indicators',overflow)
    q=quality()
    assert q.indicators['ema200'] is None
    assert q.indicators['indicators_meta']['available']['ema200'] is False
    assert q.coverage['history']=='partial' and 'invalid_native_analytics' in q.coverage['reasons']


def test_unvalidated_history_cannot_supply_facts_valuation_price(monkeypatch):
    from tests.fixtures.leaps.provider import listing
    import modules.business_quality as helper
    original=helper.analyze_company_facts
    seen=[]
    def analyze(raw,**kw):
        seen.append(kw['price']); return original(raw,**kw)
    monkeypatch.setattr(helper,'analyze_company_facts',analyze)
    class Broken:
        def daily_history(self,*a,**kw): return {'ticker':'SOFI','results':[{'t':1791777600000,'o':20,'h':1,'l':19,'c':20,'v':100}]}
        def company_facts(self,*a,**kw): return {'cik':123,'facts':{}}
    q=api('underlying').CachedUnderlyingLoader(Broken(),completion_producer=completion,configured_sec_user_agent='Synthetic agent').load('SOFI',listing(cik='123'),now_ns=NOW)
    assert seen==[None] and q.coverage['quality']=='partial'


def test_nonfinite_nested_facts_analytics_are_unknown_partial(monkeypatch):
    import modules.business_quality as helper
    original=helper.analyze_company_facts
    def overflow(raw,**kw):
        result=original(raw,**kw); result.update(status='ok',score=float('nan'),metrics={'bad':float('inf')})
        return result
    monkeypatch.setattr(helper,'analyze_company_facts',overflow)
    q=quality()
    assert q.facts['score'] is None and q.facts['metrics']['bad'] is None
    assert q.coverage['facts']=='partial'


def fact_debt(value):
    return {'companyfacts':{'facts':{'us-gaap':{'LongTermDebt':{'units':{'USD':[
        {'end':'2025-12-31','filed':'2026-02-01','form':'10-K','val':value}]}}}}}}


@pytest.mark.parametrize('value',[True,False,D('NaN'),D('Infinity'),'100'])
def test_malformed_sec_numeric_entries_cannot_become_quality_metrics(value):
    q=quality(facts=fact_debt(value))
    assert q.facts['metrics'].get('latest_debt') is None
    assert 'invalid_company_fact_values' in q.coverage['reasons'] and q.coverage['facts']=='partial'


def test_legitimate_company_total_above_option_price_limit_is_preserved():
    q=quality(facts=fact_debt(D('15000000000000')))
    assert q.facts['metrics']['latest_debt']==15000000000000
    assert 'invalid_company_fact_values' not in q.coverage['reasons']


@pytest.mark.parametrize('mutation',['ticker','listing','proof','bars','shape'])
def test_unfinished_loader_checkpoint_is_validated_before_any_io(mutation):
    from tests.fixtures.leaps.provider import listing
    mod=api('underlying')
    assert hasattr(mod,'UnderlyingLoadPaused'), 'resumable underlying progress missing'
    class Source:
        def daily_history(self,*args,**kw):
            raise LeapsDataError('admission_denied','provider')
    loader=mod.CachedUnderlyingLoader(Source(),completion_producer=completion)
    with pytest.raises(mod.UnderlyingLoadPaused) as caught:
        loader.load('SOFI',listing(cik='123'),now_ns=NOW)
    checkpoint=dict(caught.value.progress)
    with pytest.raises(TypeError): caught.value.progress['ticker']='WRONG'
    if mutation=='ticker': checkpoint['ticker']='WRONG'
    elif mutation=='listing': checkpoint['listing']=listing('WRONG')
    elif mutation=='proof': checkpoint['proof']=completion('WRONG',None,None)
    elif mutation=='bars': checkpoint['bars']=[None]
    else: checkpoint['history_done']='yes'
    class Never:
        def daily_history(self,*args,**kw): pytest.fail('invalid checkpoint called history')
        def company_facts(self,*args,**kw): pytest.fail('invalid checkpoint called SEC')
    restored=mod.CachedUnderlyingLoader(Never(),completion_producer=lambda *args:pytest.fail('invalid checkpoint called proof factory'))
    with pytest.raises(LeapsDataError,match='invalid_underlying_checkpoint'):
        restored.load('SOFI',listing(cik='123'),now_ns=NOW,checkpoint=checkpoint)
