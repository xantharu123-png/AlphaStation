"""Offline HTTP contract and private publication tests for LEAPS only."""
import asyncio
from dataclasses import replace
from decimal import Decimal as D
from datetime import date, timedelta
from pathlib import Path
import threading
import time

import json
from types import SimpleNamespace
import pytest
import api

from modules.atomic_cache import write_json_atomic
from modules.leaps.models import LeapsSnapshot, QuoteAssessment, UnderlyingObservation
from modules.leaps.provider import MassiveLeapsProvider, OptionQuote, StockQuote
from modules.leaps.runtime import LeapsStore, AuthorityProvider
from modules.leaps.service import ComparisonEvidence, compare_contract
from tests.fixtures.leaps.provider import NOW, Admission, Transport, Response, capability, run, contract, chain_raw
from tests.fixtures.leaps.discovery import Market, session
from modules.leaps.underlying import build_underlying_evidence


def contract_api():
    assert Path('modules/leaps/api_contract.py').is_file(), 'LEAPS API contract missing'
    from modules.leaps import api_contract
    return api_contract


@pytest.fixture(autouse=True)
def auth(monkeypatch):
    monkeypatch.setattr(api, 'HAS_AUTH', True)
    monkeypatch.setattr(api, 'COMMERCE_ENFORCE_AUTH', False)
    monkeypatch.setattr(api, 'ADMIN_EMAILS', {'admin@example.invalid'})
    monkeypatch.setattr(api, 'verify_token', lambda token: {'email': token + '@example.invalid'} if token in ('admin','customer') else None)
    monkeypatch.setattr(api.time, 'time_ns', lambda: NOW)
    monkeypatch.setattr(api, '_LEAPS_POLICY_FACTORY', None, raising=False)
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', None, raising=False)
    from modules.leaps import api_contract
    if hasattr(api_contract,'LeapsReadAdmission'):
        monkeypatch.setattr(api,'_LEAPS_READ_ADMISSION',api_contract.LeapsReadAdmission(),raising=False)


def request(path, *, method='GET', token='admin', body=None, headers=None, cookie=False, raw_body=None):
    async def call():
        hdr = dict(headers or {})
        if token and not cookie:
            hdr['Authorization'] = 'Bearer ' + token
        if cookie and token:
            hdr['Cookie'] = 'as_session=' + token
        hdr['Content-Type'] = 'application/json'
        pathname, _, query = path.partition('?')
        scope = {'type':'http','asgi':{'version':'3.0'},'http_version':'1.1','method':method,
                 'scheme':'http','path':pathname,'raw_path':pathname.encode(),'query_string':query.encode(),
                 'root_path':'','server':('testserver',80),'client':('127.0.0.1',1234),
                 'headers':[(key.lower().encode(),value.encode()) for key,value in hdr.items()]}
        finished = asyncio.Event()
        received = False
        messages = []
        async def receive():
            nonlocal received
            if not received:
                received = True
                return {'type':'http.request','body':raw_body if raw_body is not None else json.dumps(body).encode() if body is not None else b'', 'more_body':False}
            await finished.wait()
            return {'type':'http.disconnect'}
        async def send(message):
            messages.append(message)
            if message['type']=='http.response.body' and not message.get('more_body',False):
                finished.set()
        await api.app(scope,receive,send)
        content = b''.join(item.get('body',b'') for item in messages).decode()
        return SimpleNamespace(status_code=next(item['status'] for item in messages if item['type']=='http.response.start'),
            headers={key.decode():value.decode() for item in messages if item['type']=='http.response.start' for key,value in item['headers']},
            text=content,json=lambda:json.loads(content))
    return asyncio.run(call())


def test_capabilities_always_require_login():
    assert request('/api/leaps/capabilities', token=None).status_code == 401


def test_default_disabled_capabilities_does_no_backend_io(monkeypatch):
    def forbidden():
        pytest.fail('disabled backend factory accessed')
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', forbidden, raising=False)
    response = request('/api/leaps/capabilities')
    assert response.status_code == 200
    assert response.json()['enabled'] is False
    assert request('/api/leaps/results').status_code == 403


@pytest.mark.parametrize('path', ['/api/leaps/scan','/api/leaps/control'])
def test_customer_control_forbidden_before_body_and_backend(path):
    assert request(path, method='POST', token='customer', body={}).status_code == 403


def row():
    p = MassiveLeapsProvider(Transport([], Admission()), lambda: NOW, Admission(), capability())
    option = p.parse_chain_item(chain_raw(), contract())
    stock = StockQuote('SOFI', Market().underlying_quote('SOFI'))
    underlying = build_underlying_evidence('SOFI', completed_bars=(), facts=None, sources=(), as_of=run().market_date)
    return compare_contract(contract(), option, ComparisonEvidence(underlying, stock, session(contract(), stock, NOW)), run(), NOW)


def enabled(monkeypatch, tmp_path, *, rights=True, rows=None, runner=None):
    mod = contract_api()
    store = LeapsStore(tmp_path, write_json_atomic, clock_ns=lambda: NOW)
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': tuple(rows or [row()])},
                                {'eligible':1,'progress': {'completed':1,'total':1,'phase':'done'}}, (), 'PRIVATE-CURSOR', ()))
    clock = [NOW]
    service = mod.LeapsApiService(store, runner=runner, clock_ns=lambda: clock[0])
    proof = mod.DisplayRights(NOW-1, NOW+10**12, ('synthetic-license',), ('options','stocks','derived','underlyings','terms')) if rights else None
    policy = mod.LeapsAccessPolicy(enabled=True, admin=False, results_allowed=True,
        endpoint_capabilities={key:True for key in ('results','status','scenarios','scan','control')},
        capabilities=mod.CapabilityEvidence(
            *[{'accessible':True,'evidence_ids':('synthetic',)} for _ in range(6)], None, NOW, ('synthetic',)),
        display_rights=proof)
    monkeypatch.setattr(api, '_LEAPS_POLICY_FACTORY', lambda email, admin: replace(policy, admin=admin))
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', lambda: service)
    return store, service, clock


def test_missing_license_is_not_empty_success_and_never_reads_backend(monkeypatch, tmp_path):
    enabled(monkeypatch, tmp_path, rights=False)
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', lambda: pytest.fail('unlicensed private access'))
    response = request('/api/leaps/results')
    assert response.status_code == 403
    assert response.json()['detail']['reason'] == 'display_rights_unverified'


def test_saved_results_paginate_exact_money_without_cursor_and_expire_source(monkeypatch, tmp_path):
    store, service, clock = enabled(monkeypatch, tmp_path, rows=[row(),row()])
    response = request('/api/leaps/results?limit=1&offset=0')
    payload = response.json()
    assert response.status_code == 200
    assert len(payload['rows']) == 1 and payload['total'] == 2 and payload['next_offset'] == 1
    assert payload['rows'][0]['observation']['quote']['ask'] == '5.2'
    assert payload['rows'][0]['observation']['quote']['source_ns'] == str(NOW-1)
    assert 'PRIVATE-CURSOR' not in response.text and 'cursor' not in payload
    assert payload['rows'][0]['assessment']['comparison_eligible'] is True
    clock[0] += 61*10**9
    expired = request('/api/leaps/results?limit=1').json()['rows'][0]
    assert expired['assessment']['comparison_eligible'] is False
    assert expired['assessment']['algebra_eligible'] is True
    assert expired['observation']['quote']['source_ns'] == str(NOW-1)
    assert request('/api/leaps/results?limit=1').json()['coverage']['eligible']==0


def selected():
    payload = request('/api/leaps/results').json()
    return dict(run_id=payload['run_id'], snapshot_id=payload['snapshot_id'], contract_id=payload['rows'][0]['contract']['id'], quantity=2)


def test_selected_complete_identity_survives_other_run_publications_but_stales_ask(monkeypatch, tmp_path):
    store, _, _ = enabled(monkeypatch, tmp_path)
    body = selected()
    store.publish(LeapsSnapshot(replace(run(),run_id='run-B'), 'queued', {}, {}, (), None, ()))
    response = request('/api/leaps/scenarios', method='POST', body=dict(body,terminal_prices=['20']))
    assert response.status_code == 200 and response.json()['scenarios'][0]['result']['debit'] == '1040.0'
    updated = dict(row())
    updated['observation'] = replace(updated['observation'], quote=replace(updated['observation'].quote,ask=D('5.3')))
    store.publish(LeapsSnapshot(run(), 'complete', {'comparisons': (updated,)}, {}, (), None, ()))
    assert request('/api/leaps/scenarios', method='POST', body=dict(body,terminal_prices=['20'])).status_code == 409


@pytest.mark.parametrize('change', [{'quantity':True},{'quantity':0},{'quantity':'2'}, {'terminal_prices':[20]}, {'terminal_prices':['NaN']}, {'terminal_prices':['-1']}, {'terminal_prices':['0.1234567890123']}, {'terminal_prices':['1000000000001']}, {'ask':'0.01'}, {'contract':{}}])
def test_scenario_schema_strict_and_no_fabricated_terms(monkeypatch, tmp_path, change):
    enabled(monkeypatch, tmp_path)
    body = dict(selected(), terminal_prices=['20'])
    body.update(change)
    assert request('/api/leaps/scenarios', method='POST', body=body).status_code == 422


def test_scenario_server_defaults_quantity_depth_and_dated_stock_bid(monkeypatch, tmp_path):
    original = dict(row())
    original['stock_reference'] = UnderlyingObservation('SOFI',D('999999999999.123456789012'), 'bid', NOW-1, 'realtime')
    enabled(monkeypatch,tmp_path,rows=[original])
    body = dict(selected(),quantity=1000000)
    response = request('/api/leaps/scenarios', method='POST', body=body)
    assert response.status_code == 200
    payload = response.json()
    assert payload['scenarios'][-1]['terminal'] == '1999999999998.24691357802400'
    assert payload['comparison_scope_quantity'] == 1 and payload['requested_size_liquidity'] == 'insufficient'
    assert payload['stock_reference_kind'] == 'bid' and payload['hypothetical'] is True
    assert request('/api/leaps/scenarios',method='POST',body=dict(body,terminal_prices=[payload['scenarios'][-1]['terminal']])).status_code == 422


def test_stale_run_and_corrupt_cache_safe_envelopes(monkeypatch,tmp_path):
    store,_,_ = enabled(monkeypatch,tmp_path)
    assert request('/api/leaps/scenarios',method='POST',body=dict(selected(),run_id='old',terminal_prices=['20'])).status_code == 409
    store.path.write_text('{malformed',encoding='utf-8')
    response = request('/api/leaps/results')
    assert response.status_code == 503 and response.json()['detail']['reason'] == 'invalid_store'
    assert 'malformed' not in response.text


@pytest.mark.parametrize('origin', [None,'https://evil.invalid','null'])
def test_cookie_mutation_requires_trusted_origin_before_backend(monkeypatch,tmp_path,origin):
    enabled(monkeypatch,tmp_path)
    monkeypatch.setattr(api, '_LEAPS_BACKEND_FACTORY', lambda: pytest.fail('csrf accessed backend'))
    response = request('/api/leaps/scenarios',method='POST',body={},cookie=True,headers={} if origin is None else {'Origin':origin})
    assert response.status_code == 403


def test_cookie_valid_frontend_origin_and_origin_free_bearer(monkeypatch,tmp_path):
    enabled(monkeypatch,tmp_path)
    body = dict(selected(),terminal_prices=['20'])
    monkeypatch.setattr(api,'_cors_origins',{'https://frontend.example.invalid'})
    assert request('/api/leaps/scenarios',method='POST',body=body,cookie=True,headers={'Origin':'https://frontend.example.invalid'}).status_code == 200
    assert request('/api/leaps/scenarios',method='POST',body=body).status_code == 200


def test_heavy_saved_read_off_event_loop(monkeypatch,tmp_path):
    _,service,_ = enabled(monkeypatch,tmp_path)
    original = service.results
    threads = []
    def slow(*args,**kwargs):
        threads.append(threading.get_ident())
        time.sleep(.04)
        return original(*args,**kwargs)
    monkeypatch.setattr(service,'results',slow)
    assert request('/api/leaps/results').status_code == 200
    assert threads[0] != threading.get_ident()


def test_optional_stock403_retains_options_and_refuses_stock_class_once():
    admission = Admission()
    transport = Transport([Response(),Response({'status':'OK','results':[chain_raw()]}),Response(status=403),Response()],admission)
    provider = MassiveLeapsProvider(transport,lambda:NOW,admission,capability())
    assert provider.list_contract_page(run()).complete
    assert provider.chain_page('SOFI',run()).items
    for ticker in ('SOFI','AAPL'):
        with pytest.raises(ValueError,match='authorization_denied'):
            provider.underlying_quote(ticker)
    assert provider.chain_page('SOFI',run()).complete
    assert len(transport.calls) == 4


def test_probe_stock403_preserves_independent_option_access():
    from modules.leaps.provider import probe_capabilities
    admission = Admission()
    transport = Transport([Response(),Response({'status':'OK','results':[chain_raw()]}),Response(status=403)],admission)
    evidence = probe_capabilities(MassiveLeapsProvider(transport,lambda:NOW,admission,capability()))
    assert evidence.reference is True and evidence.option_quotes is True and evidence.stock_quotes is False
    assert evidence.display_rights is None


def real_runner(tmp_path, *, before=None, stock_status=200):
    from tests.fixtures.leaps.api import certified, RoutedTransport
    from tests.fixtures.leaps.discovery import term_producer
    from modules.leaps.service import DiscoveryProvider
    from modules.leaps.runtime import LeapsRunner
    gate = certified(tmp_path)
    transport = RoutedTransport(before=before,stock_status=stock_status)
    raw = MassiveLeapsProvider(transport,lambda:NOW,gate,capability())
    runner = LeapsRunner(DiscoveryProvider(raw,terms_producer=term_producer,session_producer=session),
                        LeapsStore(tmp_path,write_json_atomic,clock_ns=lambda:NOW),gate,lambda:NOW)
    return runner, transport


def test_actual_worker_http_ack_disconnect_pause_and_single_manual_throttle(monkeypatch,tmp_path):
    mod = contract_api()
    entered, release = threading.Event(), threading.Event()
    def before(url):
        if not entered.is_set():
            entered.set()
            assert release.wait(2)
    runner,transport = real_runner(tmp_path,before=before)
    # Policy helper's saved store must be the same store as the runner.
    (tmp_path/'access').mkdir()
    _,_,_ = enabled(monkeypatch,tmp_path/'access')
    service = mod.LeapsApiService(runner.store,runner=runner,clock_ns=lambda:NOW)
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:service)
    started = time.monotonic()
    response = request('/api/leaps/scan',method='POST',body={'run_id':'worker-run'})
    try:
        assert response.status_code==200 and response.json()['status']=='queued'
        assert time.monotonic()-started < 1 and entered.wait(1)
        status = request('/api/leaps/status')
        assert status.status_code==200 and status.json()['run_id']=='worker-run'
        assert 'cursor' not in status.text
        assert request('/api/leaps/scan',method='POST',body={'run_id':'another'}).status_code==429
        paused = request('/api/leaps/control',method='POST',body={'run_id':'worker-run','action':'pause'})
        assert paused.status_code==200 and paused.json()['status']=='paused'
    finally:
        release.set()
        service._worker.join(2)
    assert runner.snapshot().status=='paused' and len(transport.calls)==1
    assert request('/api/leaps/control',method='POST',body={'run_id':'worker-run','action':'resume'}).status_code==429


def test_stock_resource403_persists_without_poisoning_chain_on_recreation(tmp_path):
    runner,transport = real_runner(tmp_path,stock_status=403)
    runner.start(run())
    snapshot = runner.run_slice()
    assert snapshot.status=='partial' and len(snapshot.data['comparisons'])==1
    row = snapshot.data['comparisons'][0]
    assert row['assessment'].algebra_eligible and not row['assessment'].comparison_eligible
    assert row['stock_reference'].price is None
    records = runner.authority_states()
    assert records['market:stock_quotes']['reason']=='authorization_denied' and 'market' not in records
    from tests.fixtures.leaps.api import RoutedTransport
    raw = MassiveLeapsProvider(RoutedTransport(),lambda:NOW,runner.admission,capability())
    reopened = AuthorityProvider(raw,LeapsStore(tmp_path,write_json_atomic),runner.admission,lambda:NOW)
    runner.start(replace(run(),run_id='explicit-next-run'))
    for ticker in ('SOFI','AAPL'):
        with pytest.raises(ValueError,match='authorization_denied'):
            reopened.underlying_quote(ticker)
    assert reopened.chain_page('SOFI',run()).complete and len(raw.transport.calls)==1


def test_resource_reference403_probe_can_prove_independent_option_access():
    from modules.leaps.provider import probe_capabilities
    admission = Admission()
    transport = Transport([Response(status=403),Response({'status':'OK','results':[chain_raw()]}),Response({'status':'OK','results':{'T':'SOFI','p':20,'P':21,'s':1,'S':1,'y':NOW-1}})],admission)
    provider = MassiveLeapsProvider(transport,lambda:NOW,admission,capability())
    evidence = probe_capabilities(provider)
    assert evidence.reference is False and evidence.option_quotes is True and evidence.stock_quotes is True
    with pytest.raises(ValueError,match='authorization_denied'):
        provider.list_contract_page(run())
    assert len(transport.calls)==3


@pytest.mark.parametrize('field', ['break_even','be_rise','stock_reference'])
def test_malformed_cached_monetary_float_is_not_browser_money(monkeypatch,tmp_path,field):
    bad = dict(row())
    bad[field] = replace(bad[field],price=20.1) if field=='stock_reference' else 20.1
    enabled(monkeypatch,tmp_path,rows=[bad])
    response = request('/api/leaps/results')
    assert response.status_code==503 and response.json()['detail']['reason']=='invalid_store'


def test_missing_source_proof_denies_scan_without_backend(monkeypatch,tmp_path):
    enabled(monkeypatch,tmp_path)
    factory = api._LEAPS_POLICY_FACTORY
    monkeypatch.setattr(api,'_LEAPS_POLICY_FACTORY',lambda email,admin:replace(factory(email,admin),capabilities=None))
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:pytest.fail('missing source proof accessed backend'))
    assert request('/api/leaps/scan',method='POST',body={}).status_code==403


def test_read_does_not_disclose_cache_after_display_license_expiry(monkeypatch,tmp_path):
    enabled(monkeypatch,tmp_path)
    monkeypatch.setattr(api.time,'time_ns',lambda:NOW+10**12+1)
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:pytest.fail('expired display license accessed backend'))
    assert request('/api/leaps/results').status_code==403


def test_backend_unexpected_error_never_exposes_secret(monkeypatch,tmp_path):
    enabled(monkeypatch,tmp_path)
    def broken():
        raise RuntimeError('synthetic-private-key /private/path')
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',broken)
    response = request('/api/leaps/status')
    assert response.status_code==503 and response.json()['detail']['reason']=='leaps_unavailable'
    assert 'synthetic-private-key' not in response.text and 'private/path' not in response.text


def test_pause_still_available_if_source_proof_revoked(monkeypatch,tmp_path):
    runner,transport = real_runner(tmp_path)
    runner.start(run())
    (tmp_path/'access').mkdir()
    enabled(monkeypatch,tmp_path/'access')
    mod = contract_api()
    service = mod.LeapsApiService(runner.store,runner=runner,clock_ns=lambda:NOW)
    factory = api._LEAPS_POLICY_FACTORY
    monkeypatch.setattr(api,'_LEAPS_POLICY_FACTORY',lambda email,admin:replace(factory(email,admin),capabilities=None))
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:service)
    assert request('/api/leaps/control',method='POST',body={'run_id':run().run_id,'action':'pause'}).status_code==200
    assert transport.calls==[] and runner.snapshot().status=='paused'
    assert request('/api/leaps/control',method='POST',body={'run_id':run().run_id,'action':'resume'}).status_code==403


def test_real_comparison_native_finite_indicator_metrics_remain_readable(monkeypatch,tmp_path):
    bars = tuple({'date':(date(2026,1,1)+timedelta(days=i)).isoformat(),
                  'open':D(20+i),'high':D(21+i),'low':D(19+i),'close':D('20.5')+i,
                  'volume':10000,'completed':True} for i in range(250))
    underlying = build_underlying_evidence('SOFI',completed_bars=bars,facts=None,sources=(),as_of=run().market_date)
    stock = StockQuote('SOFI',Market().underlying_quote('SOFI'))
    observation = row()['observation']
    comparison = compare_contract(contract(),observation,ComparisonEvidence(underlying,stock,session(contract(),stock,NOW)),run(),NOW)
    assert any(type(v) is float for v in underlying.indicators.values())
    enabled(monkeypatch,tmp_path,rows=[comparison])
    response = request('/api/leaps/results')
    assert response.status_code==200 and response.json()['rows'][0]['observation']['quote']['ask']=='5.2'


@pytest.mark.parametrize('operation',['resume','scan'])
def test_accepted_operation_handoff_after_native_owner_release_without_extra_http(monkeypatch,tmp_path,operation):
    """An alive EXITING thread must not discard accepted persisted runnable work."""
    entered, release_request = threading.Event(), threading.Event()
    exiting, release_exit = threading.Event(), threading.Event()
    def before(url):
        if not entered.is_set():
            entered.set()
            assert release_request.wait(3)
    runner, transport = real_runner(tmp_path,before=before)
    original_slice = runner.run_slice
    first_slice = True
    def hold_after_native_release():
        nonlocal first_slice
        result = original_slice()  # Native owner released in its REAL finally.
        if first_slice:
            first_slice = False
            exiting.set()
            assert release_exit.wait(3)
        return result
    monkeypatch.setattr(runner,'run_slice',hold_after_native_release)
    (tmp_path/'access').mkdir()
    enabled(monkeypatch,tmp_path/'access')
    clock = [NOW]
    service = contract_api().LeapsApiService(runner.store,runner=runner,clock_ns=lambda:clock[0])
    monkeypatch.setattr(api,'_LEAPS_BACKEND_FACTORY',lambda:service)
    predecessor = None
    try:
        initial = request('/api/leaps/scan',method='POST',body={'run_id':'handoff-first'})
        assert initial.status_code==200 and entered.wait(1)
        predecessor = service._worker
        assert request('/api/leaps/control',method='POST',body={'run_id':'handoff-first','action':'pause'}).status_code==200
        release_request.set()
        assert exiting.wait(1) and predecessor.is_alive()
        assert runner.snapshot().status=='paused'
        from modules.leaps.admission import OSLease
        owner = OSLease(runner.store.root/'worker.lock')
        assert owner.acquire()  # The exact reviewer boundary, not an IO pause.
        owner.release()
        clock[0] += 60*10**9
        if operation=='resume':
            response = request('/api/leaps/control',method='POST',body={'run_id':'handoff-first','action':'resume'})
            expected_run, reference_calls = 'handoff-first', 1
        else:
            response = request('/api/leaps/scan',method='POST',body={'run_id':'handoff-next'})
            expected_run, reference_calls = 'handoff-next', 2
        assert response.status_code==200 and response.json()['status']=='queued'
        assert runner.snapshot().run.run_id==expected_run
        # Response has ENDED while predecessor still held alive. No further HTTP.
        release_exit.set()
        predecessor.join(3)
        assert not predecessor.is_alive()
        saved = runner.snapshot()
        assert saved.status=='complete' and saved.run.run_id==expected_run
        assert saved.coverage['parsed']==1
        assert sum('/reference/options/contracts' in url for url in transport.calls)==reference_calls
    finally:
        release_request.set()
        release_exit.set()
        if predecessor is not None:
            predecessor.join(3)


def sorting_row(ticker,strike,ask,delta=None,open_interest=None):
    """Real comparison producer; synthetic raw values, never display rounding."""
    from modules.leaps.contracts import parse_standard_contract
    from modules.leaps.models import GreeksObservation
    from tests.fixtures.leaps.provider import raw_contract,listing,terms
    baseline = row()
    selected_contract = parse_standard_contract(raw_contract(ticker,strike),listing(ticker),terms())
    observation = replace(baseline['observation'],contract=selected_contract,
        quote=replace(baseline['observation'].quote,ask=ask),
        greeks=GreeksObservation(delta=delta,units_evidence={'delta':'fraction'}),open_interest=open_interest)
    stock = StockQuote(ticker,Market().underlying_quote(ticker))
    underlying = replace(baseline['underlying'],ticker=ticker)
    return compare_contract(selected_contract,observation,ComparisonEvidence(underlying,stock,session(selected_contract,stock,NOW)),run(),NOW)


@pytest.mark.parametrize('field',['break_even','be_rise','delta','open_interest'])
@pytest.mark.parametrize('order,expected',[('asc',['AAA','BBB','CCC']),('desc',['BBB','AAA','CCC'])])
def test_server_global_sort_before_pagination_raw_decimal_zero_and_unknown_last(monkeypatch,tmp_path,field,order,expected):
    nine = sorting_row('AAA',18,D(6),D(0),0)
    ten = sorting_row('BBB',19,D(6),D(1),10)
    missing = sorting_row('CCC',20,None)
    assert nine['break_even']==D(24) and ten['break_even']==D(25)
    enabled(monkeypatch,tmp_path,rows=[ten,missing,nine])
    pages = [request(f'/api/leaps/results?limit=1&offset={offset}&sort_by={field}&order={order}') for offset in range(3)]
    assert all(response.status_code==200 for response in pages)
    assert [p.json()['rows'][0]['contract']['underlying'] for p in pages]==expected
    assert len({p.json()['snapshot_id'] for p in pages})==1


@pytest.mark.parametrize('field',['underlying','ticker','strike','expiry','cost_per_contract','spread'])
@pytest.mark.parametrize('order',['asc','desc'])
def test_sort_whitelist_and_stable_contract_id_ties(monkeypatch,tmp_path,field,order):
    first = sorting_row('AAA',18,D(9))
    second = sorting_row('BBB',18,D(10))
    enabled(monkeypatch,tmp_path,rows=[second,first])
    result = request(f'/api/leaps/results?sort_by={field}&order={order}').json()
    expected = ['AAA','BBB'] if order=='asc' or field in ('strike','expiry') else ['BBB','AAA']
    assert [r['contract']['underlying'] for r in result['rows']]==expected
    assert result['sort_by']==('underlying' if field=='ticker' else field) and result['order']==order


@pytest.mark.parametrize('query',['sort_by=unsupported','sort_by=cursor','order=up','order=ASC'])
def test_sort_schema_rejects_unsupported_field_or_direction(monkeypatch,tmp_path,query):
    enabled(monkeypatch,tmp_path)
    assert request('/api/leaps/results?'+query).status_code==422


def test_default_sort_is_annual_extrinsic_ascending(monkeypatch,tmp_path):
    nine = sorting_row('AAA',18,D(6))
    ten = sorting_row('BBB',19,D(6))
    enabled(monkeypatch,tmp_path,rows=[ten,nine])
    result = request('/api/leaps/results').json()
    assert result['sort_by']=='extrinsic_annual_fraction'
    assert [r['contract']['underlying'] for r in result['rows']]==['AAA','BBB']
