"""Final-review regressions. All sources are synthetic; no live entitlement claim."""
from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal as D
from fractions import Fraction
import os

import pytest

from modules.atomic_cache import write_json_atomic
from modules.leaps.models import LeapsSnapshot, UnderlyingObservation
from modules.leaps.pricing import evaluate_expiry
from modules.leaps.provider import MassiveLeapsProvider, StockQuote
from modules.leaps.runtime import LeapsStore
from modules.leaps.service import ComparisonEvidence, compare_contract
from modules.leaps.underlying import build_underlying_evidence
from modules.leaps.api_contract import LeapsApiService, LeapsApiError
from tests.fixtures.leaps.provider import NOW, Admission, Transport, capability, contract, run, chain_raw
from tests.fixtures.leaps.discovery import Market, session


def comparison(change=None):
    raw = chain_raw()
    if change == 'undated':
        raw['last_quote'].pop('last_updated', None)
    if change == 'crossed':
        raw['last_quote']['bid'] = 6
    parser = MassiveLeapsProvider(Transport([], Admission()), lambda: NOW, Admission(), capability())
    option = parser.parse_chain_item(raw, contract())
    stock = StockQuote('SOFI', Market().underlying_quote('SOFI'))
    facts = build_underlying_evidence('SOFI', completed_bars=(), facts=None, sources=(), as_of=run().market_date)
    return compare_contract(contract(), option, ComparisonEvidence(facts, stock, session(contract(), stock, NOW)), run(), NOW)


def test_snapshot_directory_and_blob_apply_distinct_private_policy(tmp_path, monkeypatch):
    import modules.leaps.runtime as runtime
    calls = []
    actual = runtime._private_permissions
    def record(path, *, directory=False):
        calls.append((path, directory))
        actual(path, directory=directory)
    monkeypatch.setattr(runtime, '_private_permissions', record)
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    snapshot = LeapsSnapshot(run(), 'complete', {}, {}, (), None, ())
    store.publish(snapshot)
    assert store.load() == snapshot
    assert (store.blobs, True) in calls
    assert all(directory is path.is_dir() for path, directory in calls if path.exists())
    if os.name == 'posix':
        assert store.blobs.stat().st_mode & 0o777 == 0o700
        assert all(p.stat().st_mode & 0o777 == 0o600 for p in store.blobs.iterdir())


@pytest.mark.parametrize('change', ['undated', 'crossed'])
def test_invalid_premium_parser_comparison_and_saved_api(change, tmp_path):
    row = comparison(change)
    assert row['assessment'].algebra_eligible is False
    assert row['break_even'] is None and row['be_rise'] is None and row['scenarios'] == ()
    # Existing old cache can have the former incorrect flag/economics. Serving
    # it must re-enforce provenance, not accept this old flag as authorization.
    old = dict(row, assessment=replace(row['assessment'], algebra_eligible=True), break_even=D('20.2'), be_rise=D('.01'))
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': (old,)}, {}, (), None, ()))
    backend = LeapsApiService(store, clock_ns=lambda: NOW)
    result = backend.results()
    assert result['rows'][0]['break_even'] is None
    assert result['rows'][0]['assessment']['algebra_eligible'] is False
    with pytest.raises(LeapsApiError, match='no_valid_stored_ask'):
        backend.scenarios(dict(run_id=result['run_id'], snapshot_id=result['snapshot_id'], contract_id=contract().id, quantity=1))


@pytest.mark.parametrize('price', [D('15.34'), None, D(0)])
def test_exact_quantity_scaled_stock_capital_and_native_count(price):
    reference = UnderlyingObservation('SOFI', price, 'bid', NOW-1, 'realtime')
    result = evaluate_expiry(contract(), D('4.30'), D(30), quantity=3, underlying=reference)
    assert result.stock_share_count == 300
    expected = None if not price else Fraction(300)*Fraction('15.34')
    assert (None if result.stock_reference_capital is None else Fraction(result.stock_reference_capital)) == expected
    assert result.debit == D(1290)


def test_old_expiry_dto_decodes_new_optional_fields_as_unknown():
    from modules.leaps.runtime import _encode, _decode
    value=_encode(evaluate_expiry(contract(),D('4.30'),D(30)))
    del value['v']['stock_share_count']
    del value['v']['stock_reference_capital']
    old=_decode(value)
    assert old.stock_reference_capital is None and old.stock_share_count is None


@pytest.mark.parametrize('days',[0,1])
def test_persistent_runner_new_runs_refresh_facts_without_start_io(tmp_path, monkeypatch,days):
    from test_leaps_runtime import make_runner
    import modules.leaps.underlying as underlying
    from modules.leaps.models import UnderlyingEvidence
    from tests.fixtures.leaps.provider import Response
    runner, transport, clock, gate = make_runner(tmp_path, sec=True)
    acquired = []
    real_request = transport.request
    def request(method, url, **kwargs):
        if 'sec.gov' in url:
            acquired.append(len(acquired)+1)
            return Response({'cik':1, 'facts':{}, 'revision':acquired[-1]})
        return real_request(method, url, **kwargs)
    transport.request = request
    def build(ticker, *, facts, sources, as_of, **kwargs):
        return UnderlyingEvidence(ticker, {}, {'revision': facts['companyfacts']['revision']},
                                  {'quality':'partial', 'reasons':()}, sources, as_of)
    monkeypatch.setattr(underlying, 'build_underlying_evidence', build)
    runner.start(run())
    first = runner.run_slice()
    assert first.status == 'complete'
    assert first.data['underlyings']['SOFI'].facts['revision'] == 1
    # Another accepted run on the SAME day must not reuse stale source facts.
    clock.now+=days*86400*10**9
    gate.budget.proof=replace(gate.budget.proof,valid_until_ns=clock.now+10**12)
    gate.priority.proof=replace(gate.priority.proof,valid_until_ns=clock.now+10**12)
    gate.priority.publish_owners({'stock':False,'bi':False,'alert':False},clock.now)
    second_run = replace(run(), run_id='new-run',market_date=run().market_date+timedelta(days=days))
    before = list(transport.calls)
    runner.start(second_run)
    assert transport.calls == before and acquired == [1]
    second = runner.run_slice()
    assert second.data['underlyings']['SOFI'].facts['revision'] == 2
    assert acquired == [1, 2]


@pytest.mark.parametrize('status,phase,day,expected', [
    ('partial','done',date(2026,10,13),(True,False,True)),
    ('partial','chain',date(2026,10,13),(False,True,True)),
    ('paused','chain',date(2026,10,12),(False,True,True)),
])
def test_status_separates_terminal_resumable_restartable(tmp_path,status,phase,day,expected):
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(replace(run(), market_date=day), status, {},
                  {'progress':{'phase':phase,'completed':0,'total':None}}, (), None, ()))
    info = LeapsApiService(store, clock_ns=lambda: NOW).status()
    assert (info['terminal'],info['resumable'],info['restartable']) == expected


def test_filters_parse_percent_on_server_and_bind_immutable_run():
    from modules.leaps.api_contract import parse_scan
    parsed = parse_scan({'filters':{'max_be_rise_percent':'25', 'max_spread_percent':'7.25',
        'min_maturity_months':'15','max_maturity_months':'18','min_underlying_price':'10.125',
        'max_underlying_price':'25','close_above_ema200':True}}, NOW)
    assert parsed.filters == {'max_be_rise':D('.25'),'max_spread':D('.0725'),
        'min_maturity_months':D(15),'max_maturity_months':D(18),
        'min_underlying_price':D('10.125'),'max_underlying_price':D(25),'close_above_ema200':True}
    with pytest.raises(TypeError):
        parsed.filters['min_maturity_months'] = D(12)


@pytest.mark.parametrize('filters', [
    {'min_maturity_months':'11'}, {'max_maturity_months':'25'}, {'min_maturity_months':'12.5'},
    {'min_maturity_months':'20','max_maturity_months':'15'},
    {'min_underlying_price':'30','max_underlying_price':'20'}, {'min_underlying_price':'0'},
    {'close_above_ema200':'true'}, {'close_above_ema200':False}, {'min_open_interest':'1.5'},
    {'max_spread_percent':'5','max_spread':'.05'}, {'min_maturity_months':12}, {'momentum':'80'}])
def test_filter_schema_strict_and_unsupported_explicit(filters):
    from modules.leaps.api_contract import parse_scan
    with pytest.raises(LeapsApiError, match='invalid_filters'):
        parse_scan({'filters':filters},NOW)


@pytest.mark.parametrize('expiry,expected', [(date(2027,4,29),'excluded'),(date(2027,4,30),'passed'),(date(2027,5,1),'excluded')])
def test_calendar_maturity_filter_uses_stored_market_date_not_365_days(expiry,expected):
    original=comparison()
    dated=replace(run(), market_date=date(2026,1,31), filters={'min_maturity_months':D(15),'max_maturity_months':D(15)})
    c=replace(contract(),expiry=expiry)
    option=replace(original['observation'],contract=c)
    stock=StockQuote('SOFI',Market().underlying_quote('SOFI'))
    from tests.fixtures.leaps.discovery import session
    value=compare_contract(c,option,ComparisonEvidence(original['underlying'],stock,session(c,stock,NOW)),dated,NOW)
    assert value['filter_status']==expected
    assert value['remaining_term']=={'basis_date':date(2026,1,31),'days':(expiry-date(2026,1,31)).days}


def test_underlying_active_filters_use_dated_completed_evidence_not_quote_vs_ema():
    original=comparison()
    stock=StockQuote('SOFI',Market().underlying_quote('SOFI'))
    bars=[dict(date=(date(2026,1,1)+timedelta(days=i)).isoformat(),completed=True,
               open=D(100+i),high=D(102+i),low=D(99+i),close=D(101+i),volume=D(1000)) for i in range(210)]
    quality=build_underlying_evidence('SOFI',completed_bars=bars,facts=None,sources=(),as_of=run().market_date)
    filtered=replace(run(),filters={'min_underlying_price':D(20),'max_underlying_price':D(20),'close_above_ema200':True})
    from tests.fixtures.leaps.discovery import session
    evidence=ComparisonEvidence(quality,stock,session(contract(),stock,NOW))
    result=compare_contract(contract(),original['observation'],evidence,filtered,NOW)
    assert result['filter_status']=='passed'  # quote20 < EMA, but completed close310 > EMA
    assert quality.indicators['completed_close']==D(310)
    assert quality.indicators['completed_close_session']==bars[-1]['date']
    missing=replace(quality,indicators={})
    result=compare_contract(contract(),original['observation'],replace(evidence,underlying=missing),filtered,NOW)
    assert result['filter_status']=='missing_evidence' and result['filter_missing']==('close_above_ema200',)
    missing_stock=replace(stock,quote=replace(stock.quote,source_ns=None))
    result=compare_contract(contract(),original['observation'],replace(evidence,stock=missing_stock),filtered,NOW)
    assert set(result['filter_missing'])=={'min_underlying_price','max_underlying_price'}


def test_verified_listing_company_name_and_unknown_name(tmp_path):
    from test_leaps_runtime import make_runner
    from tests.fixtures.leaps.provider import Response,listing
    runner,transport,_,_=make_runner(tmp_path)
    real_request=transport.request
    def request(method,url,**kwargs):
        if '/reference/tickers' in url and 'type=CS' in url:
            return Response({'status':'OK','results':[listing(name='Synthetic Verified Company')]})
        return real_request(method,url,**kwargs)
    transport.request=request
    runner.start(run())
    result=runner.run_slice()
    assert result.data['comparisons'][0]['company_name']=='Synthetic Verified Company'
    assert comparison()['company_name'] is None


@pytest.mark.parametrize('case',['terminal_partial','soft_partial','stale_paused'])
def test_actual_backend_native_recovery_preserves_run_binding(tmp_path,monkeypatch,case):
    from test_leaps_runtime import make_runner
    from tests.fixtures.leaps.provider import Response
    runner,transport,clock,gate=make_runner(tmp_path)
    backend=LeapsApiService(runner.store,runner=runner,clock_ns=clock)
    monkeypatch.setattr(backend,'_dispatch',lambda:None)  # explicit worker boundary; native runner itself is real
    runner.start(run())
    if case=='terminal_partial':
        real=transport.request
        transport.request=lambda method,url,**kw: Response(status=403) if '/last/nbbo/' in url else real(method,url,**kw)
        snapshot=runner.run_slice()
        assert snapshot.status=='partial' and backend.status()['terminal']
        with pytest.raises(Exception,match='terminal_run'): runner.resume(run().run_id)
    else:
        transport.before=lambda _:runner.pause(run().run_id)
        snapshot=runner.run_slice()
        transport.before=None
        if case=='soft_partial':
            runner.store.publish(replace(snapshot,status='partial',reasons=('slice_deadline',)))
            assert backend.status()['resumable']
            ack=backend.control(run().run_id,'resume')
            assert ack['run_id']==run().run_id
            assert runner.run_slice().status=='complete'
            return
        clock.now+=86400*10**9
        assert backend.status()['resumable'] and backend.status()['restartable']
        resumed=runner.resume(run().run_id)
        assert resumed.run.market_date==run().market_date
        runner.pause(run().run_id)
    current=replace(run(),run_id='replacement',market_date=run().market_date+timedelta(days=case=='stale_paused'))
    before=list(transport.calls)
    ack=backend.scan(current)
    assert ack=={'run_id':'replacement','status':'queued'} and transport.calls==before
    assert runner.store.load().run==current
